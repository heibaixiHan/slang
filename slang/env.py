"""名称作用域（环境）。

所有定义都放在 Env.bindings 里，没有“受保护”的条目：
后来加载的 .lang.build 直接覆盖同名绑定即可。

环境类型（kind）：
  root      全局根环境（也是一个模块）
  module    import 得到的独立模块
  snapshot  模块可见的内置定义快照（只读用途，不标记为模块）
  function  普通函数调用帧
  lazy      宏式（lazy）调用帧；caller/macro_target 指向调用点
  capture   宏闭包冻结帧；保留宏参数，去掉动态 caller
  block     普通词法块
"""


class Env:
    __slots__ = (
        "name",
        "parent",
        "bindings",
        "caller",
        "dcaller",
        "kind",
        "is_module",
        "is_macro_binding",
        "macro_target",
        "member_getter",
        "external",
        "macro_scope",
    )

    def __init__(self, name="", parent=None, kind=None, is_module=False):
        self.name = name
        self.parent = parent
        self.bindings = {}
        # 宏式（lazy）调用帧的调用者环境；普通环境为 None。
        # 放在属性上而不是 bindings 里，避免用户用同名变量污染宏展开。
        self.caller = None
        # 普通函数调用帧的动态调用者（仅用于日志/诊断）。
        self.dcaller = None
        self.kind = kind or ("module" if is_module else "block")
        self.is_module = bool(is_module)
        # 宏参数帧标记：expand_macro_arg 只在这些帧里做参数替换。
        self.is_macro_binding = False
        # 宏参数原始代码的求值环境；闭包捕获后仍然保留。
        self.macro_target = None
        # 动态成员解析回调（FFI 模块用）：name -> (found, value)。
        # 普通模块为 None，成员访问只查 bindings。
        self.member_getter = None
        # 外部句柄（FFI 模块用来挂 _Handle），普通环境为 None。
        self.external = None
        # 当前环境链上是否有宏参数帧（lazy/capture）。求值器用它决定
        # 能否走“没有宏参数替换”的快速路径；创建时一次算好，避免
        # 每次条件分支都沿父链检查。
        self.macro_scope = (self.kind in ("lazy", "capture")
                            or (parent is not None and parent.macro_scope))

    def get_member(self, name):
        """解析模块成员，返回 (found, value)。

        默认只查自身 bindings；动态模块可以设置 member_getter 按需解析
        （例如 FFI 导入的 Python / C 库）。注意 lookup() 不使用这个回调：
        成员访问与普通名字查找是两套语义。
        """
        if name in self.bindings:
            return True, self.bindings[name]
        getter = self.member_getter
        if getter is not None:
            return getter(name)
        return False, None

    def lookup(self, name):
        """沿父链查找，返回 (found, value)。"""
        env = self
        while env is not None:
            if name in env.bindings:
                return True, env.bindings[name]
            env = env.parent
        return False, None

    def bind(self, name, value):
        """在当前环境绑定（覆盖同名定义）。"""
        self.bindings[name] = value

    def __repr__(self):
        return "<模块 %s>" % (self.name or "匿名")
