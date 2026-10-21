"""slang 的语法树节点。

节点类型：
  Num   数字字面量
  Str   字符串字面量
  Sym   符号（名称）
  Nil   ()  —— 空列表字面量
  Call  调用/分组：(head arg1 arg2 ...)
"""

import math


class Node:
    __slots__ = ("line", "col", "file")

    def __init__(self, line=None, col=None, file=None):
        self.line = line
        self.col = col
        # 节点所在源文件；错误定位与相对路径解析都依赖它。
        self.file = file


class Num(Node):
    __slots__ = ("value",)

    def __init__(self, value, line=None, col=None, file=None):
        super().__init__(line, col, file)
        self.value = value


class Str(Node):
    __slots__ = ("value",)

    def __init__(self, value, line=None, col=None, file=None):
        super().__init__(line, col, file)
        self.value = value


class Sym(Node):
    __slots__ = ("name",)

    def __init__(self, name, line=None, col=None, file=None):
        super().__init__(line, col, file)
        self.name = name


class Nil(Node):
    """() 空列表字面量。"""

    __slots__ = ()


class Call(Node):
    __slots__ = ("head", "args")

    def __init__(self, head, args, line=None, col=None, file=None):
        super().__init__(line, col, file)
        self.head = head
        self.args = args


def quote_string(s):
    # 常见情况（普通文本）直接返回，避免大字符串逐字符追加造成高内存峰值。
    if not any(ch in s for ch in '\\"\n\t\r\0'):
        return '"' + s + '"'
    out = ['"']
    for ch in s:
        if ch == "\\":
            out.append("\\\\")
        elif ch == '"':
            out.append('\\"')
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\t":
            out.append("\\t")
        elif ch == "\r":
            out.append("\\r")
        elif ch == "\0":
            out.append("\\0")
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def to_source(node):
    """把节点还原为规范化的 sexpr 文本（调试用）。"""
    if isinstance(node, Num):
        value = node.value
        if isinstance(value, float) and not math.isfinite(value):
            # 解码/回显必须仍可运行：inf/nan 没有直接字面量，用 (num "...")。
            if math.isnan(value):
                return '(num "nan")'
            return '(num "%s")' % ("inf" if value > 0 else "-inf")
        return repr(value)
    if isinstance(node, Str):
        return quote_string(node.value)
    if isinstance(node, Sym):
        return node.name
    if isinstance(node, Nil):
        return "()"
    if isinstance(node, Call):
        parts = [to_source(node.head)]
        parts.extend(to_source(a) for a in node.args)
        return "(" + " ".join(parts) + ")"
    return repr(node)
