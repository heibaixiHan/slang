"""slang 解释器核心：求值器 + %-原语。

核心只提供以 % 开头的原子操作；`def`、`if`、`add` 这些用户可见的东西
全部由 slang/stdlib/core/base.lang.build 定义，因此可以被后加载的文件随意覆盖。

调用规则：
- 普通函数（%lambda 创建）的实参先求值；
- 宏式函数（%lazy 创建）收到原始 AST，可以自己决定何时求值；
- %-原语分 eager（先求值实参）和 lazy（收原始 AST）两类。
"""
import atexit
import datetime
import functools
import json
import operator
import os
import sys
import threading

from .ast_nodes import Call, Nil, Node, Num, Str, Sym, quote_string, to_source
from .env import Env
from .errors import EvalError, LoadError, SlangError

# 默认允许 10 万位整数：足够覆盖实际脚本，同时避免极端输入把 str()/int()
# 拖进分钟级甚至内存耗尽。Python 3.11+ 默认只有 4300 位。
INT_MAX_DIGITS = int(os.environ.get("SLANG_INT_MAX_DIGITS", "100000") or "100000")

# 名字查找的“未找到”哨兵：避免 `if name in bindings: return bindings[name]`
# 对同一个 dict 做两次哈希查找。
_MISSING = object()

_CMP_FUNCS = {
    "lt": operator.lt,
    "le": operator.le,
    "gt": operator.gt,
    "ge": operator.ge,
}


def _configure_int_limit():
    if not hasattr(sys, "set_int_max_str_digits"):
        return
    try:
        if INT_MAX_DIGITS <= 0:
            sys.set_int_max_str_digits(0)
        else:
            sys.set_int_max_str_digits(max(640, INT_MAX_DIGITS))
    except (ValueError, AttributeError):
        pass


_configure_int_limit()


def _format_int(value):
    try:
        return str(value)
    except ValueError:
        # 超过 int_max_str_digits 时不再冒险格式化，给出稳定的占位。
        return "<整数过大:%d位>" % max(1, int(value.bit_length() * 0.30103) + 1)

def truthy(value):
    t = type(value)
    if t is bool:
        return value
    if value is None:
        return False
    if t is int:
        return value != 0
    if t is str or t is list or t is dict:
        return len(value) > 0
    if t is float:
        return value != 0
    return True


def display(value, nest=False):
    """用户可见的显示形式。"""
    if value is None:
        return "nil"
    if value is True:
        return "true"
    if value is False:
        return "false"
    t = type(value)
    if t is int:
        return _format_int(value)
    if t is str:
        return quote_string(value) if nest else value
    if t is float:
        if value.is_integer() and abs(value) < 1e16:
            return _format_int(int(value))
        return repr(value)
    if t is list:
        return "[" + " ".join(display(v, True) for v in value) + "]"
    if t is dict:
        items = " ".join("%s: %s" % (k, display(v, True)) for k, v in value.items())
        return "{" + items + "}"
    if isinstance(value, Node):
        # 未求值的 AST（例如 (quote x) 的结果）显示为源码而不是内存地址
        return to_source(value)
    return repr(value)


class Prim:
    __slots__ = ("name", "fn", "lazy")

    def __init__(self, name, fn, lazy=False):
        self.name = name
        self.fn = fn
        self.lazy = lazy

    def __repr__(self):
        return "<原语 %s>" % self.name


class UserFn:
    __slots__ = ("name", "params", "rest", "body", "env", "lazy")

    def __init__(self, name, params, rest, body, env, lazy):
        self.name = name
        self.params = params
        self.rest = rest
        self.body = body
        self.env = env
        self.lazy = lazy

    def __repr__(self):
        kind = "宏" if self.lazy else "函数"
        return "<%s %s/%d>" % (kind, self.name, len(self.params))


class Interp:
    def __init__(self, warn=None, project_root=None):
        self._warn_target = warn or (lambda msg: print(msg, file=sys.stderr))
        self._warned = set()
        self.warn = self._warn_once
        self.project_root = project_root or os.getcwd()
        self.current_file = None
        self.current_dir = None
        self.load_stack = []
        self.call_stack = []
        self.module_cache = {}
        self.builtin_module_cache = {}
        self.module_base = {}
        # 启动阶段（加载 base/std/config 构建）允许更新模块内置快照；
        # 程序开始执行后冻结，避免运行时 load-config 把主程序全局变量
        # 混进模块可见的内置环境。
        self.bootstrapping = True
        self.load_lock = threading.RLock()
        # 常见调用栈在 Python 默认限制下只有几十层；解释器统一抬高到与 CLI 一致，
        # 再用 call_depth 给出可读的“递归太深”错误，而不是 Python RecursionError。
        if sys.getrecursionlimit() < 20000:
            sys.setrecursionlimit(20000)
        self.max_call_depth = 2000
        self.call_depth = 0
        self.log_file = None
        self.log_path = None
        self.log_limit = 500
        self._log_lock = threading.RLock()
        self._log_closed = True
        self._log_atexit_registered = False
        self.root = Env("root", kind="root", is_module=True)
        self._install_primitives()
        # 默认的 if 实现；call() 用它识别可以跳过宏参数展开的快速路径。
        self._ifc_prim = self.root.bindings.get("%ifc")
        self.freeze_module_base()


    def _warn_once(self, message):
        """同一条警告只输出一次（例如同一文件被加载两次）。"""
        if message in self._warned:
            return
        self._warned.add(message)
        self._warn_target(message)

    def freeze_module_base(self):
        """冻结模块可见的内置环境（base/std/配置构建）。

        模块不再对“导入那一刻的主程序环境”做快照，而是始终只看这份固定
        的内置定义。这样 import 顺序不会影响模块里的自由变量，行为更接近
        Python 模块：模块看不到主程序的全局变量，需要时显式 import。

        只在启动阶段更新；begin_program() 之后运行时 load-config 不会把
        主程序定义混入快照。
        """
        if getattr(self, "bootstrapping", True):
            self.module_base = dict(self.root.bindings)

    def begin_program(self):
        """启动配置已加载完毕，冻结模块内置快照。"""
        self.freeze_module_base()
        self.bootstrapping = False

    def _builtin_module_names(self):
        return {"log", "ffi", "logger"}

    def load_builtin_module(self, name):
        """加载显式 import 的内置模块；不会自动注入根环境。"""
        if name in self.builtin_module_cache:
            return self.builtin_module_cache[name]
        if name == "log":
            from .stdlib.log import build_module
        elif name == "ffi":
            from .stdlib.ffi import build_module
        elif name == "logger":
            from .stdlib.logger import build_module
        else:
            raise LoadError("未知的内置模块 %r" % name)

        env = build_module(self)
        self.builtin_module_cache[name] = env
        return env

    # ---------- 运行日志（显式 import "log" 后才启用） ----------
    #: 容器型值的最大渲染元素数，避免超长列表/字典把日志撑爆。
    LOG_MAX_ITEMS = 50
    #: 容器型值的最大嵌套深度。
    LOG_MAX_DEPTH = 3

    def _log_truncate_container(self, value, depth=0):
        """先截断容器再渲染，避免超大 list/dict 造成内存峰值。"""
        if depth >= self.LOG_MAX_DEPTH:
            return "<嵌套过深>"
        if isinstance(value, list):
            out = []
            for item in value[: self.LOG_MAX_ITEMS]:
                out.append(self._log_truncate_container(item, depth + 1))
            if len(value) > self.LOG_MAX_ITEMS:
                out.append("…（共 %d 项）" % len(value))
            return out
        if isinstance(value, tuple):
            out = []
            for item in value[: self.LOG_MAX_ITEMS]:
                out.append(self._log_truncate_container(item, depth + 1))
            if len(value) > self.LOG_MAX_ITEMS:
                out.append("…（共 %d 项）" % len(value))
            return out
        if isinstance(value, dict):
            out = {}
            for i, (key, item) in enumerate(value.items()):
                if i >= self.LOG_MAX_ITEMS:
                    out["…"] = "（共 %d 项）" % len(value)
                    break
                out[key] = self._log_truncate_container(item, depth + 1)
            return out
        return value

    def _log_value(self, value):
        try:
            if isinstance(value, Node):
                text = to_source(value)
            elif isinstance(value, Env):
                text = "<模块 %s>" % (value.name or "匿名")
            elif isinstance(value, (Prim, UserFn)):
                text = repr(value)
            else:
                value = self._log_truncate_container(value)
                text = display(value)
        except Exception:
            try:
                text = repr(value)
            except Exception:
                text = "<不可显示>"
        if self.log_limit and len(text) > self.log_limit:
            return text[: self.log_limit] + "…（共 %d 字符）" % len(text)
        return text

    def _log_field(self, value):
        """把一个日志字段渲染成 JSON 友好的值。

        列表/元组只保留前 LOG_MAX_ITEMS 项（其余用一行计数代替），
        避免 `call` 事件把十万级列表整个写进日志。
        """
        if isinstance(value, (list, tuple)):
            out = [self._log_value(v) for v in value[: self.LOG_MAX_ITEMS]]
            if len(value) > self.LOG_MAX_ITEMS:
                out.append("…（共 %d 项）" % len(value))
            return out
        return self._log_value(value)

    def trace(self, event, **fields):
        """写一条 JSONL 行为日志。未启用日志时是空操作。"""
        if self.log_file is None:
            return
        record = {
            "ts": datetime.datetime.now().astimezone().isoformat(timespec="milliseconds"),
            "pid": os.getpid(),
            "event": event,
        }
        for key, value in fields.items():
            if value is None:
                continue
            record[key] = self._log_field(value)
        try:
            line = json.dumps(record, ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError):
            line = json.dumps(
                {"ts": record["ts"], "pid": record["pid"], "event": event,
                 "unserializable": True},
                ensure_ascii=False,
            )
        with self._log_lock:
            if self.log_file is None:
                return
            try:
                self.log_file.write(line + "\n")
                self.log_file.flush()
            except (OSError, ValueError):
                # 日志失败不能反过来把用户程序弄崩。
                pass

    def open_log(self, path, mode="w", limit=500):
        if "\0" in path:
            raise EvalError("日志路径包含 NUL 字符")
        if mode not in ("w", "a"):
            raise EvalError("日志模式只能是 \"w\" 或 \"a\"")
        if self.log_file is not None:
            self.close_log()
        fh = open(path, mode, encoding="utf-8", newline="\n")
        self.log_file = fh
        self.log_path = path
        self.log_limit = max(0, int(limit))
        self._log_closed = False
        self.trace("log.open", path=path, mode=mode, limit=self.log_limit)
        if not self._log_atexit_registered:
            # 只注册一次：重复 start 会在 atexit 里堆积同一批回调。
            atexit.register(self.close_log)
            self._log_atexit_registered = True
        return True

    def close_log(self):
        with self._log_lock:
            fh = self.log_file
            if fh is None:
                return False
            self.trace("log.close", path=self.log_path)
            self.log_file = None
            try:
                fh.close()
            except OSError:
                pass
            self._log_closed = True
        return True

    def log_manual(self, level, name, data=None, node=None):
        if self.log_file is None:
            self.error("日志未启动（先调用 (log.start \"路径\")）", node)
        self.trace("log." + level, name=name, data=data)
        return []

    # ---------- 错误 ----------
    def error(self, message, node=None, cls=EvalError):
        if isinstance(message, SlangError):
            # 包装已有错误：保留最内层的位置信息，只补一次调用链。
            filename = message.filename or getattr(node, "file", None) or self.current_file
            line = message.line if message.line is not None else getattr(node, "line", None)
            col = message.col if message.col is not None else getattr(node, "col", None)
            text = message.message
        else:
            filename = getattr(node, "file", None) or self.current_file
            line = getattr(node, "line", None)
            col = getattr(node, "col", None)
            text = message
        if self.call_stack and "调用链(由内向外)" not in text:
            chain = []
            for name, nd in reversed(self.call_stack[-8:]):
                f = getattr(nd, "file", None) or self.current_file
                ln = getattr(nd, "line", None)
                if f and ln is not None:
                    loc = "%s:%s" % (os.path.basename(f), ln)
                else:
                    loc = "?"
                chain.append("%s (%s)" % (name, loc))
            text += "\n调用链(由内向外): " + " <- ".join(chain)
        self.trace("error", message=text, file=filename, line=line, col=col)
        raise cls(text, filename, line, col)

    # ---------- 求值 ----------
    def evaluate(self, node, env):
        t = type(node)
        if t is Sym:
            name = node.name
            if "." in name:
                return self._lookup_dotted(node, env)
            e = env
            while e is not None:
                value = e.bindings.get(name, _MISSING)
                if value is not _MISSING:
                    return value
                e = e.parent
            self.error("未定义的名称 %r（需要先由 .lang.build 定义）" % name, node)
        if t is Call:
            return self.call(node, env)
        if t is Num:
            return node.value
        if t is Str:
            return node.value
        if t is Nil:
            return []
        self.error("无法求值的节点: %r" % (node,), node)

    def lookup_symbol(self, node, env):
        name = node.name
        if "." in name:
            return self._lookup_dotted(node, env)
        e = env
        while e is not None:
            value = e.bindings.get(name, _MISSING)
            if value is not _MISSING:
                return value
            e = e.parent
        self.error("未定义的名称 %r（需要先由 .lang.build 定义）" % name, node)

    def _lookup_dotted(self, node, env):
        name = node.name
        parts = name.split(".")
        found, value = env.lookup(parts[0])
        if not found:
            self.error("未定义的名称 %r（模块或命名空间 %r 不存在）"
                       % (name, parts[0]), node)
        for part in parts[1:]:
            if isinstance(value, dict):
                if part not in value:
                    self.error("未定义的名称 %r（字典里没有键 %r）"
                               % (name, part), node)
                value = value[part]
                continue
            container = value
            getter = getattr(container, "get_member", None)
            if getter is None:
                self.error("未定义的名称 %r（%r 不是模块或字典）"
                           % (name, part), node)
            # Env 与 FFI 句柄都走 get_member：普通模块只看自身 bindings，
            # FFI 句柄按需解析 Python/C 成员，不会向父环境回退。
            found_member, value = getter(part)
            if not found_member:
                label = (getattr(container, "name", None)
                         or getattr(container, "label", None) or "?")
                kind = "对象" if getattr(container, "slang_type", None) else "模块"
                self.error("未定义的名称 %r（%s %r 里没有成员 %r）"
                           % (name, kind, label, part), node)
        return value

    def call(self, node, env):
        head = node.head
        if type(head) is Sym:
            name = head.name
            if "." in name:
                fn = self._lookup_dotted(head, env)
            else:
                e = env
                while e is not None:
                    fn = e.bindings.get(name, _MISSING)
                    if fn is not _MISSING:
                        break
                    e = e.parent
                else:
                    self.error("未定义的名称 %r（需要先由 .lang.build 定义）"
                               % name, head)
        else:
            fn = self.evaluate(head, env)
        # 默认 if 的快速路径：没有宏参数帧、也没有开启日志时，直接
        # 求值条件与选中的分支，跳过 _p_ifc/expand_macro_arg 的包装。
        if fn is self._ifc_prim and self.log_file is None \
                and not env.macro_scope and len(node.args) == 3:
            args = node.args
            cond = self.evaluate(args[0], env)
            branch = args[1] if truthy(cond) else args[2]
            if branch is args[0]:
                return cond
            return self.evaluate(branch, env)
        tf = type(fn)
        if tf is Prim:
            if fn.lazy:
                result = fn.fn(self, node.args, env, node)
                if self.log_file is not None:
                    self._trace_call(fn.name, node.args, result, node)
                return result
            args = [self.evaluate(a, env) for a in node.args]
            result = fn.fn(self, args, env, node)
            if self.log_file is not None:
                self._trace_call(fn.name, args, result, node)
            return result
        if tf is UserFn:
            if fn.lazy:
                result = self.call_lazy(fn, node.args, env, node)
                if self.log_file is not None:
                    self._trace_call(fn.name, node.args, result, node)
                return result
            args = [self.evaluate(a, env) for a in node.args]
            result = self.call_eager(fn, args, env, node)
            if self.log_file is not None:
                self._trace_call(fn.name, args, result, node)
            return result
        # 外部可调用对象（如 ffi 包装的 Python 函数/类）：鸭子类型协议。
        slang_call = getattr(fn, "slang_call", None)
        if slang_call is not None:
            args = [self.evaluate(a, env) for a in node.args]
            result = slang_call(self, args, env, node)
            if self.log_file is not None:
                self._trace_call(getattr(fn, "label", "foreign"), args, result, node)
            return result
        self.error("%s 不可调用（求值结果是 %s）"
                   % (to_source(node.head), display(fn)), node)

    def _trace_call(self, name, args, result, node):
        if self.log_file is None:
            return
        self.trace(
            "call",
            name=name,
            args=list(args),
            result=result,
            file=getattr(node, "file", None) or self.current_file,
            line=getattr(node, "line", None),
        )

    def _enter_call(self, name, node):
        self.call_depth += 1
        if self.call_depth > self.max_call_depth:
            self.call_depth -= 1
            self.error("递归太深（超过 %d 层调用）" % self.max_call_depth, node)
        # 只存 (名字, 调用节点)：文件/行号在真正报错时再取，省掉每次
        # 函数调用的两次 getattr。
        self.call_stack.append((name, node))

    def _leave_call(self):
        if self.call_stack:
            self.call_stack.pop()
        if self.call_depth > 0:
            self.call_depth -= 1

    def call_eager(self, fn, args, caller, node):
        env = Env(fn.name, fn.env, kind="function")
        env.dcaller = caller
        # 内联 bind_params：普通函数调用是最热的路径，避免额外的
        # 方法调用与 Env.bind 调用。
        params = fn.params
        fixed = len(params)
        rest = fn.rest
        if rest is not None:
            if len(args) < fixed:
                self.error("%s 需要至少 %d 个参数，实际 %d 个"
                           % (fn.name, fixed, len(args)), node)
        elif len(args) != fixed:
            self.error("%s 需要 %d 个参数，实际 %d 个"
                       % (fn.name, fixed, len(args)), node)
        bindings = env.bindings
        for name, value in zip(params, args):
            bindings[name] = value
        if rest is not None:
            tail = list(args[fixed:])
            bindings[rest] = tail
            bindings[rest + "..."] = tail
        self._enter_call(fn.name, node)
        try:
            body = fn.body
            if type(body) is list:
                result = []
                for nd in body:
                    result = self.evaluate(nd, env)
                return result
            return self.evaluate(body, env)
        finally:
            # 与 _enter_call 配对；这里直接弹栈，省掉一次方法调用。
            self.call_stack.pop()
            self.call_depth -= 1

    def call_lazy(self, fn, rawargs, caller, node):
        env = Env(fn.name, fn.env, kind="lazy")
        self.bind_params(fn, rawargs, env, node)
        env.caller = caller
        env.macro_target = caller
        env.is_macro_binding = True
        self._enter_call(fn.name, node)
        try:
            return self.eval_body(fn, env)
        finally:
            self._leave_call()

    def bind_params(self, fn, args, env, node):
        fixed = len(fn.params)
        if fn.rest is not None:
            if len(args) < fixed:
                self.error("%s 需要至少 %d 个参数，实际 %d 个"
                           % (fn.name, fixed, len(args)), node)
        elif len(args) != fixed:
            self.error("%s 需要 %d 个参数，实际 %d 个"
                       % (fn.name, fixed, len(args)), node)
        for name, value in zip(fn.params, args):
            env.bind(name, value)
        if fn.rest is not None:
            rest = list(args[fixed:])
            env.bind(fn.rest, rest)
            env.bind(fn.rest + "...", rest)

    def eval_body(self, fn, env):
        body = fn.body
        if isinstance(body, list):
            result = []
            for nd in body:
                result = self.evaluate(nd, env)
            return result
        return self.evaluate(body, env)

    def resolve_caller(self, env):
        """返回宏式帧的直接调用者（只为兼容旧调用点）。"""
        if env is not None and env.kind in ("lazy", "capture"):
            return env.macro_target or env.caller
        return None

    def target_env(self, env):
        """宏式帧里写的表达式应在调用点求值；普通帧就是自身。"""
        if env is not None and env.kind in ("lazy", "capture"):
            return env.macro_target or env.caller or env
        return env

    def capture_env(self, env):
        """冻结宏参数帧供闭包捕获。

        宏帧的 bindings（原始参数 AST）被复制，动态 caller 被去掉；
        同时保留 macro_target，让闭包里的 (%eval 参数) 仍在宏调用点求值。
        普通函数/模块环境保持原对象，闭包才能共享可变状态。
        """
        if env is None:
            return None
        if env.kind not in ("lazy", "capture") and not env.is_macro_binding:
            return env
        cap = Env((env.name or "宏") + ":捕获", parent=env.parent, kind="capture")
        cap.bindings.update(env.bindings)
        cap.is_macro_binding = True
        cap.macro_target = env.macro_target or env.caller
        return cap

    def module_env(self, env):
        """load/load-config 的落点。

        模块/根环境直接返回自身；普通函数沿“定义环境”找模块；
        宏帧沿调用点找模块（宏里 load 的副作用属于使用宏的模块）。
        """
        e = env
        seen = set()
        while e is not None and id(e) not in seen and len(seen) < 128:
            seen.add(id(e))
            if e.is_module:
                return e
            if e.kind == "lazy":
                e = e.macro_target or e.caller or e.parent
            elif e.kind == "capture":
                e = e.macro_target or e.parent
            else:
                # function / block / snapshot：走词法定义链
                e = e.parent
        return self.root

    def caller_root(self, env):
        """沿宏调用链找到最外层的实际调用者环境。

        宏体里写 (def x ...) 时，定义应落在宏被调用的地方，
        而不是宏自己的参数帧里。
        """
        e = self.target_env(env)
        seen = 0
        while e is not None and e.kind in ("lazy", "capture") and seen < 64:
            nxt = e.macro_target or e.caller
            if nxt is None:
                break
            e = nxt
            seen += 1
        return e if e is not None else self.root

    def expand_macro_arg(self, value, env, default_target):
        """把宏参数展开成调用者写的原始 AST。

        只替换标记为宏参数帧（lazy/capture）里的绑定，不把普通函数局部变量
        或全局值错当成宏参数。返回 (展开后的值, 求值它的环境)：
        一旦发生替换，求值环境就切换为“写那段原始代码的调用点”。

        每次替换后从被替换帧的调用点继续查找，避免用户写的名字恰好等于
        宏的另一个参数名时被二次替换。
        """
        target = default_target
        frame = env
        seen = 0
        visited = set()
        while isinstance(value, Sym) and frame is not None and seen < 64:
            marker = id(frame)
            if marker in visited:
                break
            visited.add(marker)
            if frame.is_macro_binding and value.name in frame.bindings:
                bound = frame.bindings[value.name]
                if bound is value:
                    break
                value = bound
                target = frame.macro_target or frame.caller or target
                frame = target
                seen += 1
                continue
            if frame.kind == "lazy":
                frame = frame.caller
            elif frame.kind == "capture":
                frame = frame.macro_target or frame.parent
            else:
                frame = frame.parent
        return value, target

    def find_mutation_scope(self, name, env):
        """set 的查找路径：从宏调用点/当前帧开始，兼顾词法闭包与动态宏链。"""
        scope = self.target_env(env)
        seen = set()
        while scope is not None and id(scope) not in seen and len(seen) < 128:
            seen.add(id(scope))
            if name in scope.bindings:
                return scope
            if scope.kind in ("lazy", "capture"):
                scope = scope.macro_target or scope.caller or scope.parent
            else:
                scope = scope.parent
        return None

    # ---------- 原语安装 ----------
    def _prim(self, name, fn, lazy=False):
        if getattr(fn, "__self__", None) is not None:
            bound = fn

            def fn(i, a, e, n, _f=bound):
                return _f(a, e, n)

        self.root.bind(name, Prim(name, fn, lazy))

    def _install_primitives(self):
        p = self._prim
        # 宏式原语
        p("%eval", self._p_eval, lazy=True)
        p("%bind", self._p_bind, lazy=True)
        p("%set", self._p_set, lazy=True)
        p("%lookup", self._p_lookup, lazy=True)
        p("%cond", self._p_cond, lazy=True)
        p("%ifc", self._p_ifc, lazy=True)
        p("%do", self._p_do, lazy=True)
        p("%lambda", self._p_lambda, lazy=True)
        p("%lazy", self._p_lazy, lazy=True)
        p("%quote", self._p_quote, lazy=True)
        p("%deref", self._p_deref, lazy=True)
        p("%import", self._p_import)

        # 普通原语
        p("%list", lambda i, a, e, n: list(a))
        p("%add", self._p_add)
        p("%sub", self._p_sub)
        p("%mul", self._p_mul)
        p("%div", self._p_div)
        p("%mod", self._p_mod)
        p("%neg", self._p_neg)
        p("%eq", self._p_eq)
        p("%ne", self._p_ne)
        p("%lt", self._p_cmp("lt"))
        p("%le", self._p_cmp("le"))
        p("%gt", self._p_cmp("gt"))
        p("%ge", self._p_cmp("ge"))
        p("%not", self._p_not)
        p("%and", lambda i, a, e, n: all(truthy(v) for v in a))
        p("%or", lambda i, a, e, n: any(truthy(v) for v in a))
        p("%str", self._p_str)
        p("%num", self._p_num)
        p("%concat", self._p_concat)
        p("%len", self._p_len)
        p("%type", self._p_type)
        p("%first", self._p_first)
        p("%rest", self._p_rest)
        p("%cons", self._p_cons)
        p("%nth", self._p_nth)
        p("%append", self._p_append)
        p("%sort", self._p_sort)
        p("%split", self._p_split)
        p("%join", self._p_join)
        p("%substr", self._p_substr)
        p("%print", self._p_print)
        p("%readfile", self._p_readfile)
        p("%writefile", self._p_writefile)
        p("%error", self._p_error)
        p("%apply", self._p_apply)
        p("%range", self._p_range)
        p("%map", self._p_map)
        p("%filter", self._p_filter)
        p("%reduce", self._p_reduce)
        p("%take", self._p_take)
        p("%drop", self._p_drop)
        p("%load", self._p_load)
        p("%loadconfig", self._p_loadconfig)

    # ---------- 宏式原语 ----------
    def _p_eval(self, rawargs, env, node):
        if len(rawargs) != 1:
            self.error("%eval 需要 1 个参数", node)
        target = self.target_env(env)
        inner, eval_target = self.expand_macro_arg(rawargs[0], env, target)
        if not isinstance(inner, Node):
            return inner
        result = self.evaluate(inner, eval_target)
        if isinstance(result, Node):
            # (%eval (%quote ...))：把生成出来的代码再执行一次
            return self.evaluate(result, eval_target)
        return result

    def _name_of(self, rawarg, env, node, what="名称"):
        value, target = self.expand_macro_arg(rawarg, env, env)
        if isinstance(value, Node) and not isinstance(value, (Sym, Str)):
            value = self.evaluate(value, target)
        if isinstance(value, Sym):
            return value.name
        if isinstance(value, Str):
            return value.value
        if isinstance(value, str):
            return value
        self.error("%s 必须是符号或字符串，得到 %s" % (what, display(value)), node)

    def _eval_macro_arg(self, rawarg, env, default_target=None):
        target = env if default_target is None else default_target
        value, eval_target = self.expand_macro_arg(rawarg, env, target)
        if isinstance(value, Node):
            value = self.evaluate(value, eval_target)
        return value

    def _p_bind(self, rawargs, env, node):
        if len(rawargs) != 2:
            self.error("%bind 需要 2 个参数", node)
        name = self._name_of(rawargs[0], env, node)
        if "." in name:
            self.error("定义名不能包含 '.'（点号保留给模块成员访问）", node)
        value = self._eval_macro_arg(rawargs[1], env, env)
        if isinstance(value, UserFn) and value.name == "<匿名>":
            value.name = name
        self.caller_root(env).bind(name, value)
        self.trace("def", name=name, value=value,
                   file=getattr(node, "file", None) or self.current_file,
                   line=getattr(node, "line", None))
        return value

    def _p_set(self, rawargs, env, node):
        """更新已有绑定（沿词法闭包/宏调用链查找）；找不到才在调用点新建。"""
        if len(rawargs) != 2:
            self.error("%set 需要 2 个参数", node)
        name = self._name_of(rawargs[0], env, node)
        if "." in name:
            self.error("赋值名不能包含 '.'（点号保留给模块成员访问）", node)
        value = self._eval_macro_arg(rawargs[1], env, env)
        scope = self.find_mutation_scope(name, env)
        if scope is not None:
            scope.bindings[name] = value
        else:
            self.target_env(env).bind(name, value)
        self.trace("set", name=name, value=value,
                   file=getattr(node, "file", None) or self.current_file,
                   line=getattr(node, "line", None))
        return value

    def _p_lookup(self, rawargs, env, node):
        if len(rawargs) != 1:
            self.error("%lookup 需要 1 个参数", node)
        name = self._name_of(rawargs[0], env, node)
        scope = self.find_mutation_scope(name, env)
        if scope is None:
            self.error("未定义的名称 %r" % name, node)
        return scope.bindings[name]

    def _p_cond(self, rawargs, env, node):
        if len(rawargs) != 3:
            self.error("%cond 需要 3 个参数 (条件 真分支 假分支)", node)
        return self._ifc_value(rawargs, env, node)

    def _ifc_value(self, rawargs, env, node):
        # 普通函数/模块环境没有宏参数帧：条件与分支可以直接按普通表达式
        # 求值，跳过 expand_macro_arg 的父链扫描。
        if not env.macro_scope:
            cond_raw = rawargs[0]
            cond = (self.evaluate(cond_raw, env)
                    if isinstance(cond_raw, Node) else cond_raw)
            branch = rawargs[1] if truthy(cond) else rawargs[2]
            if branch is cond_raw:
                return cond
            return (self.evaluate(branch, env)
                    if isinstance(branch, Node) else branch)
        target = self.target_env(env)
        cond_raw, cond_target = self.expand_macro_arg(rawargs[0], env, target)
        if isinstance(cond_raw, Node):
            cond = self.evaluate(cond_raw, cond_target)
        else:
            cond = cond_raw
        branch_raw = rawargs[1] if truthy(cond) else rawargs[2]
        chosen, chosen_target = self.expand_macro_arg(branch_raw, env, target)
        if chosen is cond_raw:
            # 条件与分支引用同一个表达式（and/or 的短路返回），复用已求值结果
            return cond
        if isinstance(chosen, Node):
            return self.evaluate(chosen, chosen_target)
        return chosen

    def _p_ifc(self, rawargs, env, node):
        """条件分支宏：在调用者环境中求值条件，并只在其中求值选中的分支。"""
        if len(rawargs) != 3:
            self.error("%ifc 需要 3 个参数 (条件 真分支 假分支)", node)
        return self._ifc_value(rawargs, env, node)

    def _p_deref(self, rawargs, env, node):
        if len(rawargs) != 1:
            self.error("%deref 需要 1 个参数", node)
        value, target = self.expand_macro_arg(rawargs[0], env, self.target_env(env))
        if isinstance(value, Sym):
            return self.evaluate(value, target)
        return value

    def _p_do(self, rawargs, env, node):
        if not rawargs:
            return []
        target = self.target_env(env)
        if len(rawargs) == 1:
            first, first_target = self.expand_macro_arg(rawargs[0], env, target)
            if isinstance(first, Node):
                first = self.evaluate(first, first_target)
            if isinstance(first, list) and all(isinstance(x, Node) for x in first):
                nodes, run_env = first, first_target
            else:
                return first
        else:
            # 直接 (%do e1 e2 ...)：表达式写在宏体里，要在宏环境中求值
            nodes, run_env = rawargs, env
        result = []
        for nd in nodes:
            result = self.evaluate(nd, run_env)
        return result

    def _p_quote(self, rawargs, env, node):
        if len(rawargs) != 1:
            self.error("%quote 需要 1 个参数", node)
        return rawargs[0]

    def _p_lambda(self, rawargs, env, node):
        return self._make_fn(rawargs, env, node, lazy=False)

    def _p_lazy(self, rawargs, env, node):
        return self._make_fn(rawargs, env, node, lazy=True)

    def _make_fn(self, rawargs, env, node, lazy):
        if not rawargs:
            self.error("%s 需要 (参数表) [函数体]..."
                       % ("%lazy" if lazy else "%lambda"), node)
        pnode = self.deref(rawargs[0], env)
        bodies = [self.deref(b, env) for b in rawargs[1:]]
        body = bodies[0] if len(bodies) == 1 else (bodies if bodies else [])
        params, rest = self._param_names(pnode, node)
        captured = self.capture_env(self.target_env(env))
        return UserFn("<匿名>", params, rest, body, captured, lazy)

    def deref(self, raw, env):
        """%lambda/%lazy 的参数若是宏参数符号，取出它绑定的原始节点。

        这样 (fn (params body) (%lambda params body)) 这类包装才能正常工作；
        直接写字面量参数表 (a b) 时则原样使用。
        """
        value, target = self.expand_macro_arg(raw, env, env)
        if isinstance(value, Sym):
            return self.evaluate(value, target)
        return value

    def _param_names(self, node, errnode):
        if isinstance(node, Nil):
            return [], None
        if isinstance(node, Sym):
            items = [node]
        elif isinstance(node, Call):
            items = [node.head] + node.args
        else:
            self.error("参数表必须是 (a b c) 或单个符号", errnode)
        names = []
        rest = None
        seen = set()
        for idx, item in enumerate(items):
            if not isinstance(item, Sym):
                self.error("参数名必须是符号，得到 %s" % display(item), errnode)
            name = item.name
            if name.endswith("..."):
                name = name[:-3]
                if not name:
                    self.error("变长参数名不能为空", errnode)
                if idx != len(items) - 1:
                    self.error("变长参数 ... 只能放在参数表最后", errnode)
                if name in seen:
                    self.error("参数名重复: %s" % name, errnode)
                rest = name
                seen.add(name)
            else:
                if name in seen:
                    self.error("参数名重复: %s" % name, errnode)
                names.append(name)
                seen.add(name)
        return names, rest

    # ---------- 数值原语 ----------
    def _num(self, value, node, op):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            self.error("%s 需要数字，得到 %s" % (op, display(value)), node)
        return value

    def _p_add(self, args, env, node):
        if args and isinstance(args[0], str):
            if all(isinstance(a, str) for a in args):
                return "".join(args)
        total = 0
        try:
            for a in args:
                ta = type(a)
                if ta is int or ta is float:
                    total += a
                else:
                    total += self._num(a, node, "add")
        except (OverflowError, ValueError) as exc:
            self.error("add 数字超出可表示范围: %s" % exc, node)
        return total

    def _p_sub(self, args, env, node):
        if not args:
            self.error("sub 需要参数", node)
        first = args[0]
        if len(args) == 1:
            a = (first if type(first) is int or type(first) is float
                 else self._num(first, node, "sub"))
            try:
                return -a
            except (OverflowError, ValueError) as exc:
                self.error("sub 数字超出可表示范围: %s" % exc, node)
        total = (first if type(first) is int or type(first) is float
                 else self._num(first, node, "sub"))
        try:
            for a in args[1:]:
                ta = type(a)
                if ta is int or ta is float:
                    total -= a
                else:
                    total -= self._num(a, node, "sub")
        except (OverflowError, ValueError) as exc:
            self.error("sub 数字超出可表示范围: %s" % exc, node)
        return total

    def _p_mul(self, args, env, node):
        total = 1
        try:
            for a in args:
                ta = type(a)
                if ta is int or ta is float:
                    total *= a
                else:
                    total *= self._num(a, node, "mul")
        except (OverflowError, ValueError) as exc:
            self.error("mul 数字超出可表示范围: %s" % exc, node)
        return total

    def _p_div(self, args, env, node):
        if len(args) < 2:
            self.error("div 需要至少 2 个参数", node)
        first = args[0]
        total = (first if type(first) is int or type(first) is float
                 else self._num(first, node, "div"))
        try:
            for a in args[1:]:
                ta = type(a)
                d = a if ta is int or ta is float else self._num(a, node, "div")
                if d == 0:
                    self.error("除以零", node)
                total /= d
        except (OverflowError, ValueError) as exc:
            self.error("div 数字超出可表示范围: %s" % exc, node)
        return total

    def _p_mod(self, args, env, node):
        if len(args) != 2:
            self.error("mod 需要 2 个参数", node)
        a = args[0]
        b = args[1]
        if type(a) is not int and type(a) is not float:
            a = self._num(a, node, "mod")
        if type(b) is not int and type(b) is not float:
            b = self._num(b, node, "mod")
        if b == 0:
            self.error("取模时除数为零", node)
        try:
            return a % b
        except (OverflowError, ValueError) as exc:
            self.error("mod 数字超出可表示范围: %s" % exc, node)

    def _p_neg(self, args, env, node):
        if len(args) != 1:
            self.error("neg 需要 1 个参数", node)
        a = args[0]
        if type(a) is not int and type(a) is not float:
            a = self._num(a, node, "neg")
        try:
            return -a
        except (OverflowError, ValueError) as exc:
            self.error("neg 数字超出可表示范围: %s" % exc, node)

    @staticmethod
    def _eq_values(a, b):
        ta = type(a)
        tb = type(b)
        if ta is tb and (ta is int or ta is str or ta is bool or ta is float):
            return a == b
        if isinstance(a, Node) or isinstance(b, Node):
            # 引用的代码按源码结构比较：(= (quote a) (quote a)) => true
            return isinstance(a, Node) and isinstance(b, Node) and to_source(a) == to_source(b)
        if isinstance(a, bool) != isinstance(b, bool):
            return False
        if isinstance(a, list) or isinstance(b, list):
            if not (isinstance(a, list) and isinstance(b, list)):
                return False
            if len(a) != len(b):
                return False
            return all(Interp._eq_values(x, y) for x, y in zip(a, b))
        if isinstance(a, dict) or isinstance(b, dict):
            if not (isinstance(a, dict) and isinstance(b, dict)):
                return False
            if set(a.keys()) != set(b.keys()):
                return False
            return all(Interp._eq_values(a[k], b[k]) for k in a)
        # 数值允许 1 == 1.0（与现有语义一致），但布尔已经单独排除。
        return a == b

    def _p_eq(self, args, env, node):
        if len(args) != 2:
            self.error("eq 需要 2 个参数", node)
        return self._eq_values(args[0], args[1])

    def _p_ne(self, args, env, node):
        if len(args) != 2:
            self.error("ne 需要 2 个参数", node)
        return not self._eq_values(args[0], args[1])

    def _p_cmp(self, kind):
        op = _CMP_FUNCS[kind]

        def fn(i, args, env, node):
            if len(args) != 2:
                i.error("比较需要 2 个参数", node)
            a, b = args
            ta = type(a)
            tb = type(b)
            if ta is tb and (ta is int or ta is float or ta is str):
                return op(a, b)
            if ta is bool or tb is bool:
                i.error("比较只支持数字或字符串，布尔值请先转成数字", node)
            if not (isinstance(a, (int, float, str))
                    and isinstance(b, (int, float, str))):
                i.error("比较只支持数字或字符串", node)
            if isinstance(a, str) != isinstance(b, str):
                i.error("不能比较不同类型的值", node)
            return op(a, b)
        return fn

    # ---------- 字符串 / 列表 ----------
    def _p_num(self, args, env, node):
        if len(args) != 1:
            self.error("num 需要 1 个参数", node)
        v = args[0]
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return v
        if not isinstance(v, str):
            self.error("num 只支持字符串或数字", node)
        text = v.strip()
        if text and not any(c in text for c in ".eE") \
                and text.lower() not in ("inf", "+inf", "-inf", "nan", "+nan", "-nan"):
            try:
                return int(text)
            except ValueError as exc:
                if "limit" in str(exc) or "digits" in str(exc):
                    self.error("整数太大（超过 %d 位上限）" % INT_MAX_DIGITS, node)
                self.error("无法把 %r 转成数字" % v, node)
        try:
            return float(text)
        except (ValueError, OverflowError):
            self.error("无法把 %r 转成数字" % v, node)

    def _p_concat(self, args, env, node):
        return "".join(a if type(a) is str else display(a) for a in args)

    def _p_len(self, args, env, node):
        if len(args) != 1:
            self.error("len 需要 1 个参数", node)
        v = args[0]
        if isinstance(v, (str, list, dict)):
            return len(v)
        self.error("len 只支持字符串、列表或字典，得到 %s" % display(v), node)

    def _p_type(self, args, env, node):
        if len(args) > 1:
            self.error("type 最多接受 1 个参数，得到 %d 个" % len(args), node)
        v = args[0] if args else None
        if v is None:
            return "nil"
        if isinstance(v, bool):
            return "bool"
        if isinstance(v, int):
            return "num"
        if isinstance(v, float):
            return "num"
        if isinstance(v, str):
            return "str"
        if isinstance(v, list):
            return "list"
        if isinstance(v, dict):
            return "dict"
        if isinstance(v, Env):
            return "module"
        if isinstance(v, (Prim, UserFn)):
            return "fn"
        slang_type = getattr(v, "slang_type", None)
        if isinstance(slang_type, str) and slang_type:
            return slang_type
        return "unknown"

    def _p_str(self, args, env, node):
        if len(args) > 1:
            self.error("str 最多接受 1 个参数，得到 %d 个" % len(args), node)
        return display(args[0]) if args else ""

    def _p_not(self, args, env, node):
        if len(args) != 1:
            self.error("not 需要 1 个参数", node)
        return not truthy(args[0])

    def _p_first(self, args, env, node):
        if len(args) != 1:
            self.error("first 需要 1 个参数", node)
        v = args[0]
        if isinstance(v, str):
            if not v:
                self.error("first 不能用于空字符串", node)
            return v[0]
        if not isinstance(v, list):
            self.error("first 只支持列表或字符串", node)
        if not v:
            self.error("first 不能用于空列表", node)
        return v[0]

    def _p_rest(self, args, env, node):
        if len(args) != 1:
            self.error("rest 需要 1 个参数", node)
        v = args[0]
        if isinstance(v, (str, list)):
            return v[1:]
        self.error("rest 只支持列表或字符串", node)

    def _p_cons(self, args, env, node):
        if len(args) != 2:
            self.error("cons 需要 2 个参数 (元素 列表)", node)
        if not isinstance(args[1], list):
            self.error("cons 的第二个参数必须是列表", node)
        return [args[0]] + args[1]

    def _p_nth(self, args, env, node):
        if len(args) != 2:
            self.error("nth 需要 2 个参数 (列表 下标)", node)
        seq, idx = args
        if not isinstance(seq, (list, str)):
            self.error("nth 的第一个参数必须是列表或字符串", node)
        if isinstance(idx, bool) or not isinstance(idx, int):
            self.error("nth 的下标必须是整数", node)
        if idx < 0 or idx >= len(seq):
            self.error("下标越界: %d (长度 %d)" % (idx, len(seq)), node)
        return seq[idx]

    def _p_append(self, args, env, node):
        if len(args) != 2 or not isinstance(args[0], list) or not isinstance(args[1], list):
            self.error("append 需要 2 个列表", node)
        return args[0] + args[1]

    def _p_sort(self, args, env, node):
        if len(args) not in (1, 2) or not isinstance(args[0], list):
            self.error("sort 需要 (列表) 或 (列表 比较函数)", node)
        values = list(args[0])
        if len(args) == 2:
            fn = args[1]

            def compare(a, b):
                result = self.apply_fn(fn, [a, b], env, node)
                if isinstance(result, bool) or not isinstance(result, (int, float)):
                    self.error("sort 比较函数必须返回数字（负数/0/正数）", node)
                return -1 if result < 0 else (1 if result > 0 else 0)

            try:
                return sorted(values, key=functools.cmp_to_key(compare))
            except TypeError:
                self.error("sort 的元素无法比较", node)
        try:
            return sorted(values)
        except TypeError:
            self.error("sort 的元素无法比较（混合类型请提供比较函数）", node)

    def _p_split(self, args, env, node):
        if len(args) != 2 or not isinstance(args[0], str) or not isinstance(args[1], str):
            self.error("split 需要 (字符串 分隔符)", node)
        text, sep = args
        if sep == "":
            self.error("split 的分隔符不能为空", node)
        return text.split(sep)

    def _p_join(self, args, env, node):
        if len(args) != 2 or not isinstance(args[0], list) or not isinstance(args[1], str):
            self.error("join 需要 (字符串列表 分隔符)", node)
        for item in args[0]:
            if not isinstance(item, str):
                self.error("join 只支持字符串列表，遇到 %s" % display(item), node)
        return args[1].join(args[0])

    def _p_substr(self, args, env, node):
        if len(args) not in (2, 3) or not isinstance(args[0], str):
            self.error("substr 需要 (字符串 起点) 或 (字符串 起点 终点)", node)
        for v in args[1:]:
            if isinstance(v, bool) or not isinstance(v, int):
                self.error("substr 的下标必须是整数", node)
        text = args[0]
        if len(args) == 3:
            return text[args[1]:args[2]]
        return text[args[1]:]

    # ---------- IO / 杂项 ----------
    def _p_print(self, args, env, node):
        text = "".join(display(a) for a in args)
        sys.stdout.write(text)
        # 不再每次 print 都 flush：stdout 是行缓冲（终端）或块缓冲（管道），
        # 频繁 flush 会让输出密集的程序慢一个数量级。
        self.trace("print", text=text,
                   file=getattr(node, "file", None) or self.current_file,
                   line=getattr(node, "line", None))
        return []

    def _resolve_io(self, path, node):
        """相对路径按“写这条语句的源文件目录”解析，与 load 保持一致。"""
        if "\0" in path:
            self.error("路径包含 NUL 字符", node)
        if os.path.isabs(path):
            return path
        source = getattr(node, "file", None)
        if source:
            return os.path.join(os.path.dirname(source), path)
        return path

    def _p_readfile(self, args, env, node):
        if len(args) != 1 or not isinstance(args[0], str):
            self.error("readfile 需要 1 个路径字符串", node)
        path = args[0]
        full = self._resolve_io(path, node)
        try:
            # newline="" 保留原始换行（不做 universal newlines 转换）
            with open(full, "r", encoding="utf-8", newline="") as fh:
                return fh.read()
        except UnicodeDecodeError as exc:
            self.error("文件不是合法 UTF-8 %s: %s" % (full, exc), node)
        except OSError as exc:
            self.error("读取文件失败 %s: %s" % (full, exc), node)

    def _p_writefile(self, args, env, node):
        if len(args) != 2 or not isinstance(args[0], str):
            self.error("writefile 需要 (路径 内容)", node)
        path = args[0]
        full = self._resolve_io(path, node)
        content = args[1] if isinstance(args[1], str) else display(args[1])
        tmp = None
        try:
            directory = os.path.dirname(full) or "."
            import tempfile

            fd, tmp = tempfile.mkstemp(prefix=".slang-write-", dir=directory)
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
                fh.write(content)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, full)
            tmp = None
        except OSError as exc:
            self.error("写入文件失败 %s: %s" % (full, exc), node)
        finally:
            if tmp is not None:
                try:
                    os.remove(tmp)
                except OSError:
                    pass
        return []

    def _p_error(self, args, env, node):
        msg = args[0] if args else "程序触发了错误"
        self.error(display(msg), node)

    def _require_eager(self, fn, node):
        """校验批量/高阶调用只接受普通函数，返回 fn 以便调用方复用。"""
        if isinstance(fn, (Prim, UserFn)):
            if fn.lazy:
                self.error("这里只能调用普通（非宏式）函数", node)
            return fn
        self.error("不是可调用的函数: %s" % display(fn), node)

    def apply_fn(self, fn, args, env, node):
        """调用一个普通（非宏式）函数，供 apply 与批量原语复用。"""
        fn = self._require_eager(fn, node)
        if isinstance(fn, Prim):
            return fn.fn(self, args, env, node)
        return self.call_eager(fn, args, env, node)

    def _p_apply(self, args, env, node):
        if len(args) != 2 or not isinstance(args[1], list):
            self.error("apply 需要 (函数 参数列表)", node)
        return self.apply_fn(args[0], list(args[1]), env, node)

    def _p_range(self, args, env, node):
        if len(args) not in (1, 2, 3):
            self.error("%range 需要 1~3 个整数参数", node)
        for a in args:
            if isinstance(a, bool) or not isinstance(a, int):
                self.error("%range 的参数必须是整数", node)
        try:
            if len(args) == 1:
                values = range(args[0])
            elif len(args) == 2:
                values = range(args[0], args[1])
            else:
                values = range(args[0], args[1], args[2])
        except ValueError:
            self.error("%range 的步长不能为 0", node)
        return list(values)

    def _p_map(self, args, env, node):
        if len(args) != 2 or not isinstance(args[1], list):
            self.error("%map 需要 (函数 列表)", node)
        fn, xs = args
        fn = self._require_eager(fn, node)
        if isinstance(fn, UserFn):
            return [self.call_eager(fn, [x], env, node) for x in xs]
        return [fn.fn(self, [x], env, node) for x in xs]

    def _p_filter(self, args, env, node):
        if len(args) != 2 or not isinstance(args[1], list):
            self.error("%filter 需要 (判定函数 列表)", node)
        fn, xs = args
        fn = self._require_eager(fn, node)
        if isinstance(fn, UserFn):
            return [x for x in xs
                    if truthy(self.call_eager(fn, [x], env, node))]
        return [x for x in xs if truthy(fn.fn(self, [x], env, node))]

    def _p_reduce(self, args, env, node):
        if len(args) != 3 or not isinstance(args[2], list):
            self.error("%reduce 需要 (函数 初始值 列表)", node)
        fn, acc, xs = args
        fn = self._require_eager(fn, node)
        if isinstance(fn, UserFn):
            for x in xs:
                acc = self.call_eager(fn, [acc, x], env, node)
            return acc
        for x in xs:
            acc = fn.fn(self, [acc, x], env, node)
        return acc

    def _p_take(self, args, env, node):
        if len(args) != 2 or isinstance(args[0], bool) or not isinstance(args[0], int) \
                or not isinstance(args[1], list):
            self.error("%take 需要 (整数 列表)", node)
        n = args[0]
        return list(args[1][:n]) if n > 0 else []

    def _p_drop(self, args, env, node):
        if len(args) != 2 or isinstance(args[0], bool) or not isinstance(args[0], int) \
                or not isinstance(args[1], list):
            self.error("%drop 需要 (整数 列表)", node)
        n = args[0]
        return list(args[1][n:]) if n > 0 else list(args[1])

    def _p_load(self, args, env, node):
        if not args or len(args) > 2:
            self.error("load 需要 (路径) 或 (路径 模式)，模式为 \"override\" 或 \"import\"", node)
        path = args[0]
        mode = args[1] if len(args) > 1 else "override"
        if not isinstance(path, str) or not isinstance(mode, str):
            self.error("load 的参数必须是字符串", node)
        if "\0" in path:
            self.error("load 路径包含 NUL 字符", node)
        if mode not in ("override", "import"):
            self.error("load 的模式只能是 \"override\" 或 \"import\"，得到 %r" % mode, node)
        from . import loader
        base_dir = os.path.dirname(node.file) if getattr(node, "file", None) else None
        try:
            result = loader.load_file(self, path, mode=mode, target_env=self.module_env(env),
                                      base_dir=base_dir, origin_node=node)
        except LoadError as exc:
            self.error(exc, node, cls=LoadError)
        return [] if result is None else result

    def _p_import(self, args, env, node):
        if len(args) != 1 or not isinstance(args[0], str):
            self.error("import 需要 1 个路径字符串", node)
        if "\0" in args[0]:
            self.error("import 路径包含 NUL 字符", node)
        from . import loader
        base_dir = os.path.dirname(node.file) if getattr(node, "file", None) else None
        try:
            return loader.load_file(self, args[0], mode="import",
                                    target_env=self.module_env(env),
                                    base_dir=base_dir, origin_node=node)
        except LoadError as exc:
            self.error(exc, node, cls=LoadError)

    def _p_loadconfig(self, args, env, node):
        if len(args) != 1 or not isinstance(args[0], str):
            self.error("load-config 需要 1 个 TOML 路径", node)
        if "\0" in args[0]:
            self.error("load-config 路径包含 NUL 字符", node)
        from . import loader
        base_dir = os.path.dirname(node.file) if getattr(node, "file", None) else None
        try:
            return loader.load_config(self, args[0], target_env=self.module_env(env),
                                      base_dir=base_dir)
        except LoadError as exc:
            self.error(exc, node, cls=LoadError)
