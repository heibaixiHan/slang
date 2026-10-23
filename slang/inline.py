"""`.slang.module.inline` 模块缓存与轻量内联优化。

目标与 Python 的 ``.pyc`` 类似，但只做两件事：

1. 缓存已经解析好的 AST，避免每次 import 都重新词法/语法分析；
2. 对一个模块内**简单、固定参数、纯表达式**的顶层函数做安全内联。

缓存文件与源文件放在同一目录，名字是 ``<源文件>.slang.module.inline``。
第一行保存源文件 SHA-256；导入时先比较哈希，不一致就重新解析并覆盖缓存。
缓存是纯 JSON AST，不执行任何代码；损坏、权限不足或哈希不匹配时自动回退
到正常解析。

安全边界（刻意保守）：

* 只内联**顶层作用域**的调用（不进入函数体），避免把函数体的自由变量
  解析到错误的词法作用域；
* 只内联固定参数、单表达式、且只使用内置纯函数/参数的函数；
* 实参只能是字面量，避免变量未绑定/重复求值/改变副作用顺序；
* 同一模块内出现 ``load``/``eval`` 或对函数名的 ``set``/重复定义时，
  对应函数不内联（或整个模块不做内联）。
* 不做常量折叠，因为 slang 允许覆盖 ``+``/``*`` 等基础定义；内联只替换
  函数调用，不改变这些名字在运行时的解析结果。
"""
import hashlib
import json
import math
import os
import tempfile

from .ast_nodes import Call, Nil, Num, Str, Sym

CACHE_SUFFIX = ".slang.module.inline"
CACHE_MAGIC = "SLANG-INLINE"
CACHE_VERSION = 1
OPTIMIZER_VERSION = 3
MAX_CACHE_DEPTH = 10000


# ---------------------------------------------------------------------------
# 节点序列化
# ---------------------------------------------------------------------------
def _loc(node):
    return {
        "l": getattr(node, "line", None),
        "c": getattr(node, "col", None),
        "f": getattr(node, "file", None),
    }


def node_to_dict(node):
    out = {"t": type(node).__name__}
    out.update(_loc(node))
    if isinstance(node, Num):
        value = node.value
        if isinstance(value, float) and not math.isfinite(value):
            out["v"] = "nan" if math.isnan(value) else ("inf" if value > 0 else "-inf")
            out["x"] = "float"
        else:
            out["v"] = value
    elif isinstance(node, Str):
        out["v"] = node.value
    elif isinstance(node, Sym):
        out["v"] = node.name
    elif isinstance(node, Nil):
        pass
    elif isinstance(node, Call):
        out["h"] = node_to_dict(node.head)
        out["a"] = [node_to_dict(arg) for arg in node.args]
    else:
        raise TypeError("无法序列化的 AST 节点: %r" % (type(node),))
    return out


def node_from_dict(data, depth=0):
    if depth > MAX_CACHE_DEPTH:
        raise ValueError("inline 缓存 AST 过深")
    if not isinstance(data, dict):
        raise ValueError("inline 缓存节点不是对象")
    kind = data.get("t")
    line = data.get("l")
    col = data.get("c")
    file = data.get("f")
    if line is not None and (isinstance(line, bool) or not isinstance(line, int)):
        raise ValueError("inline 缓存行号非法")
    if col is not None and (isinstance(col, bool) or not isinstance(col, int)):
        raise ValueError("inline 缓存列号非法")
    if file is not None and not isinstance(file, str):
        raise ValueError("inline 缓存文件名字非法")
    if kind == "Num":
        value = data.get("v")
        if data.get("x") == "float":
            if value == "nan":
                value = float("nan")
            elif value == "inf":
                value = float("inf")
            elif value == "-inf":
                value = float("-inf")
            else:
                raise ValueError("inline 缓存特殊浮点数非法")
        elif isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("inline 缓存数字非法")
        return Num(value, line, col, file)
    if kind == "Str":
        value = data.get("v")
        if not isinstance(value, str):
            raise ValueError("inline 缓存字符串非法")
        return Str(value, line, col, file)
    if kind == "Sym":
        value = data.get("v")
        if not isinstance(value, str) or not value:
            raise ValueError("inline 缓存符号非法")
        return Sym(value, line, col, file)
    if kind == "Nil":
        return Nil(line, col, file)
    if kind == "Call":
        head = node_from_dict(data.get("h"), depth + 1)
        if not isinstance(head, Sym):
            raise ValueError("inline 缓存调用头不是符号")
        args = data.get("a")
        if not isinstance(args, list):
            raise ValueError("inline 缓存调用参数非法")
        return Call(head, [node_from_dict(arg, depth + 1) for arg in args],
                    line, col, file)
    raise ValueError("inline 缓存节点类型未知: %r" % (kind,))


def nodes_to_dict(nodes):
    return [node_to_dict(node) for node in nodes]


def nodes_from_dict(data):
    if not isinstance(data, list):
        raise ValueError("inline 缓存根节点不是数组")
    return [node_from_dict(node) for node in data]


def _retarget_files(node, path):
    # 缓存可能被移动/复制到同内容的其他目录；错误定位始终指回当前源文件。
    node.file = path
    if isinstance(node, Call):
        _retarget_files(node.head, path)
        for arg in node.args:
            _retarget_files(arg, path)


# ---------------------------------------------------------------------------
# 缓存文件
# ---------------------------------------------------------------------------
def cache_path(source_path):
    return source_path + CACHE_SUFFIX


def source_digest(text):
    # read_source 已经统一成 UTF-8 文本；对文本做哈希即可。
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _cache_enabled():
    return os.environ.get("SLANG_NO_INLINE_CACHE", "").lower() not in (
        "1", "true", "yes", "on"
    )


def read_cache(source_path, digest, mode, fmt_name):
    """返回 (nodes, mode, fmt_name) 或 None。任何异常都视为缓存未命中。"""
    if not _cache_enabled():
        return None
    path = cache_path(source_path)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            header = fh.readline().rstrip("\n")
            parts = header.split()
            if len(parts) != 3 or parts[0] != CACHE_MAGIC:
                return None
            if parts[1] != str(CACHE_VERSION) or parts[2] != digest:
                return None
            payload = json.load(fh)
        if not isinstance(payload, dict):
            return None
        if payload.get("source_sha256") != digest:
            return None
        if payload.get("cache_version") != CACHE_VERSION:
            return None
        if payload.get("optimizer_version") != OPTIMIZER_VERSION:
            return None
        if payload.get("mode") != mode or payload.get("format") != fmt_name:
            return None
        nodes = nodes_from_dict(payload.get("nodes"))
        for node in nodes:
            _retarget_files(node, source_path)
        return nodes, mode, fmt_name
    except Exception:
        # 缓存是可丢弃的优化产物；损坏/过深/权限问题一律回退正常解析。
        return None


def write_cache(source_path, digest, mode, fmt_name, nodes):
    """原子写入缓存；失败时静默回退（只读目录/权限不足/磁盘满）。"""
    if not _cache_enabled():
        return False
    path = cache_path(source_path)
    try:
        payload = {
            "cache_version": CACHE_VERSION,
            "optimizer_version": OPTIMIZER_VERSION,
            "source_sha256": digest,
            "mode": mode,
            "format": fmt_name,
            "nodes": nodes_to_dict(nodes),
        }
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        directory = os.path.dirname(os.path.abspath(path)) or "."
        fd, tmp = tempfile.mkstemp(prefix=".slang-inline-", suffix=".tmp",
                                   dir=directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write("%s %d %s\n" % (CACHE_MAGIC, CACHE_VERSION, digest))
                fh.write(body)
            os.replace(tmp, path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        return True
    except Exception:
        # 缓存写入失败绝不能影响源码执行。
        return False


# ---------------------------------------------------------------------------
# 轻量内联
# ---------------------------------------------------------------------------
# 只允许这些内置名字出现在“可内联函数体”里。它们要么是纯表达式，
# 要么是宏式控制流；内联只替换函数调用，不改变这些名字的绑定方式。
SIMPLE_SYMBOLS = {
    "+", "-", "*", "/", "%", "=", "==", "!=", "<", "<=", ">", ">=",
    "not", "len", "first", "rest", "nth", "cons", "list", "str", "num",
    "type", "concat", "substr", "append", "sort", "split", "join",
    "take", "drop", "range", "map", "filter", "reduce", "sum", "last",
    "square", "not=", "apply", "if", "and", "or", "when", "do",
    "true", "false", "nil",
}
SIMPLE_CALLS = SIMPLE_SYMBOLS - {"true", "false", "nil"}
SPECIAL_FORMS = {"fn", "lambda", "defn", "def", "defmacro", "quote", "%quote", "let", "let*"}
# 宏参数是代码，宏可以检查/延迟求值；不把内联塞进这些宏的参数。
LAZY_FORMS = {"if", "when", "and", "or", "do"}
# 出现这些顶层操作时，模块可能在运行时改变定义/求值代码，放弃整模块内联。
DANGEROUS_HEADS = {"load", "load-config", "load_config", "loadconfig",
                   "eval", "%eval", "%bind", "defmacro"}


def _params(node):
    if isinstance(node, Nil):
        return []
    if not isinstance(node, Call) or not isinstance(node.head, Sym):
        return None
    names = [node.head] + list(node.args)
    out = []
    for name in names:
        if not isinstance(name, Sym) or name.name.endswith("..."):
            return None
        out.append(name.name)
    return out


def _definition(node):
    """返回 (name, params, body_forms) 或 None。

    params 为 None 表示这是同名但不是可分析函数的定义（例如 def 一个普通值）。
    """
    if not isinstance(node, Call) or not isinstance(node.head, Sym):
        return None
    head = node.head.name
    if head == "defn" and len(node.args) >= 3 and isinstance(node.args[0], Sym):
        return node.args[0].name, _params(node.args[1]), node.args[2:]
    if head == "def" and len(node.args) == 2 and isinstance(node.args[0], Sym):
        name = node.args[0].name
        value = node.args[1]
        if (isinstance(value, Call) and isinstance(value.head, Sym)
                and value.head.name in ("fn", "lambda") and value.args):
            return name, _params(value.args[0]), value.args[1:]
        return name, None, None
    return None


def _simple_expr(node, params):
    if isinstance(node, (Num, Str, Nil)):
        return True
    if isinstance(node, Sym):
        return node.name in params or node.name in SIMPLE_SYMBOLS
    if not isinstance(node, Call) or not isinstance(node.head, Sym):
        return False
    name = node.head.name
    # 参数可以遮蔽同名内置函数；这种调用不能按内置函数内联。
    if name not in SIMPLE_CALLS or name in params:
        return False
    return all(_simple_expr(arg, params) for arg in node.args)


def _blocked_and_danger(nodes):
    blocked = set()
    counts = {}
    dangerous = False

    def visit(node):
        nonlocal dangerous
        if isinstance(node, Call) and isinstance(node.head, Sym):
            head = node.head.name
            if head in DANGEROUS_HEADS:
                dangerous = True
            if head in ("set", "%set"):
                if node.args and isinstance(node.args[0], Sym):
                    blocked.add(node.args[0].name)
                else:
                    # 动态目标无法静态确认，保守放弃整模块内联。
                    dangerous = True
            if head == "defmacro" and node.args and isinstance(node.args[0], Sym):
                counts[node.args[0].name] = counts.get(node.args[0].name, 0) + 1
                blocked.add(node.args[0].name)
            definition = _definition(node)
            if definition is not None:
                name = definition[0]
                counts[name] = counts.get(name, 0) + 1
        if isinstance(node, Call):
            visit(node.head)
            for arg in node.args:
                visit(arg)

    for node in nodes:
        visit(node)
    for name, count in counts.items():
        if count > 1:
            blocked.add(name)
    return blocked, dangerous


def _clone(node, loc=None):
    line = loc[0] if loc is not None else getattr(node, "line", None)
    col = loc[1] if loc is not None else getattr(node, "col", None)
    file = loc[2] if loc is not None else getattr(node, "file", None)
    if isinstance(node, Num):
        return Num(node.value, line, col, file)
    if isinstance(node, Str):
        return Str(node.value, line, col, file)
    if isinstance(node, Sym):
        return Sym(node.name, line, col, file)
    if isinstance(node, Nil):
        return Nil(line, col, file)
    if isinstance(node, Call):
        return Call(_clone(node.head, loc),
                    [_clone(arg, loc) for arg in node.args],
                    line, col, file)
    raise TypeError("未知 AST 节点: %r" % (node,))


def _substitute(node, mapping, loc):
    if isinstance(node, Sym) and node.name in mapping:
        return _clone(mapping[node.name], loc)
    if isinstance(node, Call):
        return Call(_substitute(node.head, mapping, loc),
                    [_substitute(arg, mapping, loc) for arg in node.args],
                    loc[0], loc[1], loc[2])
    return _clone(node, loc)


def _inline_expr(node, definitions, blocked):
    if not isinstance(node, Call) or not isinstance(node.head, Sym):
        return node
    name = node.head.name
    if name in SPECIAL_FORMS or name in LAZY_FORMS:
        return node
    if name in definitions and name not in blocked:
        params, body = definitions[name]
        # 只允许字面量实参：变量在未绑定/求值顺序上可能改变错误行为。
        if len(params) == len(node.args) and all(
                isinstance(arg, (Num, Str, Nil)) for arg in node.args):
            mapping = dict(zip(params, node.args))
            return _substitute(body, mapping,
                               (node.line, node.col, node.file))
    return Call(node.head,
                [_inline_expr(arg, definitions, blocked) for arg in node.args],
                node.line, node.col, node.file)


def optimize(nodes):
    """返回内联后的新节点列表；不修改输入。"""
    blocked, dangerous = _blocked_and_danger(nodes)
    if dangerous:
        return list(nodes)
    definitions = {}
    out = []
    for node in nodes:
        definition = _definition(node)
        if definition is not None:
            name, params, body = definition
            if name in blocked or params is None or body is None:
                definitions.pop(name, None)
            elif len(body) == 1 and _simple_expr(body[0], set(params)):
                definitions[name] = (params, body[0])
            else:
                definitions.pop(name, None)
            out.append(node)
            continue
        out.append(_inline_expr(node, definitions, blocked))
    return out


def load_or_generate(source_path, text, mode, fmt_name, parse_fn):
    """先查缓存，未命中则调用 parse_fn()，优化并写回。

    parse_fn 返回节点列表。返回 (nodes, from_cache)。
    """
    digest = source_digest(text)
    cached = read_cache(source_path, digest, mode, fmt_name)
    if cached is not None:
        return cached[0], True
    nodes = parse_fn()
    if mode in ("sexpr", "slang"):
        nodes = optimize(nodes)
    write_cache(source_path, digest, mode, fmt_name, nodes)
    return nodes, False
