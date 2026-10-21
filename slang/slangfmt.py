"""`.slang` 人类友好格式。

`.lang` 保持严格 S 表达式；`.slang` 用缩进、中缀运算符和 Python 风格
的 `def` / `if`，更适合手写。本模块负责：

    词法  lex()            -> Token 列表
    语法  parse()          -> 与 `.lang` 完全相同的 AST 节点
    解码  decode_to_lang() -> 可运行的 `@sexpr` 文本

运行时直接使用 `parse()` 产出的 AST（不做“生成文本再重新解析”），
`slang decode` 命令则用 `decode_to_lang()` 输出等价的 `.lang` 源码，
便于阅读、审查或交给只认 `.lang` 的工具。

语法速查::

    # 注释
    import "math.lang" as math
    from "math.lang" import square, cube

    def answer = 42
    def add(a, b) = a + b
    def fib(n):
        if n < 2:
            n
        else:
            fib(n - 1) + fib(n - 2)

    def main():
        xs = [1, 2, 3]
        println(f"sum = {reduce(add, 0, xs)}")

    main()

设计约束：
  * 函数体是表达式序列，最后一个表达式的值就是返回值（没有 return）。
  * 块（if/函数体）里可以有多条语句，按顺序求值。
  * 只有 `()` / `[]` 会跨行；其余语句以换行或 `;` 分隔。
"""
import os

from .ast_nodes import Call, Nil, Num, Str, Sym, to_source
from .errors import LexError, ParseError

# 行尾仍是这些 token 时，下一行视为续行（不需要反斜杠）。
_CONTINUE_KINDS = {"OP", "COMMA"}
_CONTINUE_NAMES = {"and", "or", "not"}
# 这些关键字后面期待一个表达式，因此 % 开头的名字应识别为名字而不是取模。
_PREFIX_KEYWORDS = {
    "if", "elif", "else", "when", "and", "or", "not", "return",
    "def", "let", "set", "defmacro", "import", "from", "load",
    "load-config", "as", "in", "fn", "do",
}

_ESCAPES = {
    "n": "\n",
    "t": "\t",
    "r": "\r",
    "\\": "\\",
    '"': '"',
    "'": "'",
    "0": "\0",
}

_MULTI_OPS = ("==", "!=", "<=", ">=")
_ONE_OPS = set("+-*/%<>=!")


class Token:
    __slots__ = ("kind", "value", "line", "col", "end")

    def __init__(self, kind, value, line, col, end=None):
        self.kind = kind
        self.value = value
        self.line = line
        self.col = col
        self.end = col if end is None else end

    def __repr__(self):
        return "Token(%r, %r, %s:%s)" % (self.kind, self.value, self.line, self.col)

    def tuple(self):
        return (self.kind, self.value, self.line, self.col, self.end)


def _is_name_start(ch):
    return ch == "_" or ch.isalpha()


def _is_name_char(ch):
    return ch == "_" or ch == "?" or ch.isalnum()


def _short(text, limit=60):
    if len(text) <= limit:
        return text
    return text[:limit] + "…（共 %d 字符）" % len(text)


class _Lexer:
    def __init__(self, source, filename=None, line=1, col_offset=0):
        if "\r" in source:
            source = source.replace("\r\n", "\n").replace("\r", "\n")
        self.src = source
        self.n = len(source)
        self.filename = filename
        self.i = 0
        self.line = line
        self.line_start = 0
        self.col_offset = col_offset
        self.tokens = []
        self.indents = [0]
        self.brackets = []  # (open_char, line, col)
        self.at_line_start = True
        self.continuation = False

    # ---------- 基础 ----------
    def error(self, message, line=None, col=None):
        raise LexError(message, self.filename,
                       self.line if line is None else line,
                       (self.i - self.line_start + 1) if col is None else col)

    def emit(self, kind, value, line, col, end=None):
        self.tokens.append(Token(kind, value, line, col, end))

    def prev(self):
        return self.tokens[-1] if self.tokens else None

    def _prev_allows_postfix(self):
        t = self.prev()
        if t is None:
            return False
        if t.kind in ("NUM", "STR", "FSTR", "RP", "RB"):
            return True
        if t.kind == "NAME":
            return t.value not in _PREFIX_KEYWORDS
        return False

    # ---------- 主循环 ----------
    def run(self):
        src = self.src
        n = self.n
        while self.i < n:
            if self.at_line_start and not self.brackets:
                if self._handle_line_start():
                    continue
            # 括号内的行首没有缩进语义；但这一行已经有内容了，不能再把
            # 随后的换行当成“空行”吞掉（例如单独一行的 `)`）。
            self.at_line_start = False

            ch = src[self.i]
            if ch in " \t":
                self.i += 1
                continue
            if ch == "\n":
                self._newline()
                continue
            if ch == "#":
                while self.i < n and src[self.i] != "\n":
                    self.i += 1
                continue
            if ch == "\\" and self.i + 1 < n and src[self.i + 1] == "\n":
                # 显式续行
                self.i += 2
                self.line += 1
                self.line_start = self.i
                self.at_line_start = True
                self.continuation = True
                continue
            if ch == ";":
                if self.brackets:
                    self.error("括号内不能用 ';' 分隔语句")
                if self.tokens and self.tokens[-1].kind != "NEWLINE":
                    self.emit("NEWLINE", None, self.line,
                              self.i - self.line_start + 1)
                self.i += 1
                continue
            if ch == "(":
                self._open("LP", "(")
                continue
            if ch == "[":
                self._open("LB", "[")
                continue
            if ch in ")]":
                self._close(ch)
                continue
            if ch in "{}":
                self.error("`.slang` 不支持花括号（没有字典字面量）")
            if ch == ",":
                self.emit("COMMA", ",", self.line, self.i - self.line_start + 1)
                self.i += 1
                continue
            if ch == ":":
                self.emit("COLON", ":", self.line, self.i - self.line_start + 1)
                self.i += 1
                continue
            if src.startswith("...", self.i):
                self.emit("ELLIPSIS", "...", self.line,
                          self.i - self.line_start + 1)
                self.i += 3
                continue

            # f/r 字符串前缀（必须紧贴引号）
            if ch in "fFrR" and self.i + 1 < n and src[self.i + 1] in "\"'":
                is_raw = ch in "rR"
                is_f = ch in "fF"
                if is_f and is_raw:
                    self.error("暂不支持 fr\"...\" 组合前缀")
                self.i += 1
                self._string(src[self.i], raw=is_raw, interpolate=is_f)
                continue

            if ch in "\"'":
                self._string(ch, raw=False, interpolate=False)
                continue

            if ch.isdigit() or (ch == "." and self.i + 1 < n
                                and src[self.i + 1].isdigit()
                                and not self._prev_allows_postfix()):
                self._number()
                continue

            if ch == ".":
                self.error("无法识别的字符 '.'（成员访问请写 a.b，数字请写 0.5）")

            if ch == "%" and not self._prev_allows_postfix() \
                    and self.i + 1 < n and _is_name_start(src[self.i + 1]):
                self._name()
                continue

            if ch in _ONE_OPS:
                self._operator()
                continue

            if _is_name_start(ch):
                self._name()
                continue

            self.error("无法识别的字符 %r" % ch)

        self._finish()
        return self.tokens

    def _handle_line_start(self):
        """处理行首缩进；返回 True 表示已经跳到下一个位置。"""
        src = self.src
        n = self.n
        j = self.i
        width = 0
        seen_space = False
        seen_tab = False
        while j < n and src[j] in " \t":
            if src[j] == "\t":
                seen_tab = True
                width += 4
            else:
                seen_space = True
                width += 1
            j += 1
        if seen_tab and seen_space:
            self.error("缩进里混用了空格和制表符", self.line, 1)
        # 空行 / 注释行：跳过，不改变缩进栈
        if j >= n:
            self.i = j
            return True
        if src[j] == "\n":
            self.i = j + 1
            self.line += 1
            self.line_start = self.i
            return True
        if src[j] == "#":
            while j < n and src[j] != "\n":
                j += 1
            if j < n:
                j += 1
                self.line += 1
                self.line_start = j
            self.i = j
            return True

        self.i = j
        self.at_line_start = False
        if self.continuation:
            self.continuation = False
            return False

        top = self.indents[-1]
        if width > top:
            self.indents.append(width)
            self.emit("INDENT", width, self.line, 1)
        elif width < top:
            while width < self.indents[-1]:
                self.indents.pop()
                self.emit("DEDENT", None, self.line, 1)
            if width != self.indents[-1]:
                self.error("缩进不匹配：这一行缩进 %d 列，无法与外层对齐"
                           % width, self.line, 1)
        return False

    def _open(self, kind, ch):
        self.emit(kind, ch, self.line, self.i - self.line_start + 1)
        self.brackets.append((ch, self.line, self.i - self.line_start + 1))
        self.i += 1

    def _close(self, ch):
        if not self.brackets:
            self.error("多余的右括号 %r" % ch)
        opener, line, col = self.brackets.pop()
        want = {"(": ")", "[": "]"}[opener]
        if ch != want:
            self.error("括号不匹配：%r（第 %d 行）需要 %r，实际遇到 %r"
                       % (opener, line, want, ch))
        self.emit("RP" if ch == ")" else "RB", ch,
                  self.line, self.i - self.line_start + 1)
        self.i += 1

    def _newline(self):
        if self.brackets:
            # 括号内换行只算空白
            self.i += 1
            self.line += 1
            self.line_start = self.i
            self.at_line_start = True
            return
        last = self.prev()
        cont = False
        if last is not None:
            if last.kind in _CONTINUE_KINDS:
                cont = True
            elif last.kind == "NAME" and last.value in _CONTINUE_NAMES:
                cont = True
        if cont:
            self.continuation = True
        elif last is not None and last.kind != "NEWLINE":
            self.emit("NEWLINE", None, self.line, self.i - self.line_start + 1)
        self.i += 1
        self.line += 1
        self.line_start = self.i
        self.at_line_start = True

    def _finish(self):
        if self.brackets:
            opener, line, col = self.brackets[-1]
            self.error("括号未闭合：%r" % opener, line, col)
        last = self.prev()
        if last is not None and last.kind not in ("NEWLINE", "INDENT", "DEDENT"):
            self.emit("NEWLINE", None, self.line, self.i - self.line_start + 1)
        while len(self.indents) > 1:
            self.indents.pop()
            self.emit("DEDENT", None, self.line, self.i - self.line_start + 1)
        self.emit("EOF", None, self.line, self.i - self.line_start + 1)

    # ---------- 名字 / 数字 / 运算符 ----------
    def _name(self):
        src = self.src
        n = self.n
        start = self.i
        line = self.line
        col = start - self.line_start + 1
        i = start
        if src[i] == "%":
            i += 1
        while i < n and _is_name_char(src[i]):
            i += 1
        # 点号成员：a.b.c（点后必须紧跟名字）
        while i + 1 < n and src[i] == "." and _is_name_start(src[i + 1]):
            i += 1
            while i < n and _is_name_char(src[i]):
                i += 1
        if i == start or (src[start] == "%" and i == start + 1):
            self.error("这里需要一个名字")
        self.emit("NAME", src[start:i], line, col)
        self.i = i

    def _number(self):
        src = self.src
        n = self.n
        start = self.i
        line = self.line
        col = start - self.line_start + 1
        i = start
        base = 10
        if src[i] == "0" and i + 1 < n and src[i + 1] in "xXbBoO":
            kind = src[i + 1].lower()
            base = {"x": 16, "b": 2, "o": 8}[kind]
            i += 2
            digit_start = i
            allowed = {
                16: "0123456789abcdefABCDEF_",
                2: "01_",
                8: "01234567_",
            }[base]
            while i < n and src[i] in allowed:
                i += 1
            text = src[digit_start:i].replace("_", "")
            if not text:
                self.error("数字字面量缺少数字", line, col)
            try:
                value = int(text, base)
            except ValueError as exc:
                self.error("非法数字字面量 %r: %s" % (src[start:i], exc), line, col)
        else:
            saw_dot = False
            saw_exp = False
            while i < n and (src[i].isdigit() or src[i] == "_"):
                i += 1
            if i < n and src[i] == "." and i + 1 < n and src[i + 1].isdigit():
                saw_dot = True
                i += 1
                while i < n and (src[i].isdigit() or src[i] == "_"):
                    i += 1
            elif i < n and src[i] == "." and (i + 1 >= n or not _is_name_start(src[i + 1])):
                # 允许 1. 这种写法
                saw_dot = True
                i += 1
            if i < n and src[i] in "eE":
                j = i + 1
                if j < n and src[j] in "+-":
                    j += 1
                if j < n and src[j].isdigit():
                    saw_exp = True
                    i = j
                    while i < n and (src[i].isdigit() or src[i] == "_"):
                        i += 1
                else:
                    self.error("指数部分缺少数字", line, col)
            text = src[start:i].replace("_", "")
            try:
                value = float(text) if (saw_dot or saw_exp) else int(text)
            except ValueError as exc:
                self.error("非法数字字面量 %r: %s" % (text, exc), line, col)
        if i < n and (_is_name_char(src[i]) or src[i] == "."):
            self.error("数字 %r 后面不能紧跟 %r"
                       % (src[start:i], src[i]), line, col)
        self.emit("NUM", value, line, col)
        self.i = i

    def _operator(self):
        src = self.src
        n = self.n
        start = self.i
        line = self.line
        col = start - self.line_start + 1
        two = src[start:start + 2]
        if two in _MULTI_OPS:
            self.emit("OP", two, line, col)
            self.i += 2
            return
        ch = src[start]
        if ch == "!":
            self.error("'!' 不是运算符（不等比较请用 !=，逻辑非请用 not）",
                       line, col)
        self.emit("OP", ch, line, col)
        self.i += 1

    # ---------- 字符串 ----------
    def _string(self, quote, raw=False, interpolate=False):
        src = self.src
        n = self.n
        line = self.line
        col = self.i - self.line_start + 1
        triple = src.startswith(quote * 3, self.i)
        start = self.i + (3 if triple else 1)
        self.i = start
        if interpolate:
            value = self._fstring_body(quote, triple, line, col)
            self.emit("FSTR", value, line, col)
            return
        buf = []
        i = start
        while i < n:
            ch = src[i]
            if triple:
                if src.startswith(quote * 3, i):
                    i += 3
                    break
            else:
                if ch == quote:
                    i += 1
                    break
                if ch == "\n":
                    self.error("字符串不能跨行（请用 \\n 或三引号）", line, col)
            if ch == "\\" and not raw:
                if i + 1 >= n:
                    self.error("字符串末尾的反斜杠没有转义对象", line, col)
                e = src[i + 1]
                if e == "\n":
                    if not triple:
                        self.error("字符串不能跨行（不支持反斜杠续行）", line, col)
                    i += 2
                    self.line += 1
                    self.line_start = i
                    continue
                if e in _ESCAPES:
                    buf.append(_ESCAPES[e])
                elif e == "x":
                    h = src[i + 2:i + 4]
                    if len(h) != 2 or any(c not in "0123456789abcdefABCDEF" for c in h):
                        self.error("\\x 转义需要两位十六进制数", line, col)
                    buf.append(chr(int(h, 16)))
                    i += 4
                    continue
                elif e == "u":
                    h = src[i + 2:i + 6]
                    if len(h) != 4 or any(c not in "0123456789abcdefABCDEF" for c in h):
                        self.error("\\u 转义需要四位十六进制数", line, col)
                    buf.append(chr(int(h, 16)))
                    i += 6
                    continue
                else:
                    # 未知转义保留反斜杠，避免静默吞掉字符
                    buf.append("\\")
                    buf.append(e)
                i += 2
                continue
            if raw and ch == "\\":
                # 原始字符串里的反斜杠原样保留；但引号前如果有奇数个
                # 连续反斜杠，这个引号属于字符串内容（Python 同规则）。
                j = i
                while j < n and src[j] == "\\":
                    j += 1
                run = j - i
                if j < n and src[j] == quote and run % 2 == 1:
                    buf.append(src[i:j + 1])
                    i = j + 1
                else:
                    buf.append(src[i:j])
                    i = j
                continue
            if ch == "\n":
                self.line += 1
                self.line_start = i + 1
            buf.append(ch)
            i += 1
        else:
            self.error("字符串未闭合", line, col)
        self.i = i
        self.emit("STR", "".join(buf), line, col)

    def _fstring_body(self, quote, triple, start_line, start_col):
        """返回 [('text', s) | ('expr', source, line, col), ...]。"""
        src = self.src
        n = self.n
        i = self.i
        segments = []
        text = []

        def flush():
            if text:
                segments.append(["text", "".join(text)])
                text.clear()

        while i < n:
            ch = src[i]
            if triple:
                if src.startswith(quote * 3, i):
                    i += 3
                    break
            else:
                if ch == quote:
                    i += 1
                    break
                if ch == "\n":
                    self.error("f-string 不能跨行（请用三引号）", start_line, start_col)
            if ch == "\\":
                if i + 1 >= n:
                    self.error("f-string 末尾的反斜杠没有转义对象", start_line, start_col)
                e = src[i + 1]
                if e == "\n":
                    if not triple:
                        self.error("f-string 不能跨行（不支持反斜杠续行）",
                                   start_line, start_col)
                    i += 2
                    self.line += 1
                    self.line_start = i
                    continue
                if e in _ESCAPES:
                    text.append(_ESCAPES[e])
                elif e == "x":
                    h = src[i + 2:i + 4]
                    if len(h) != 2 or any(c not in "0123456789abcdefABCDEF"
                                          for c in h):
                        self.error("\\x 转义需要两位十六进制数",
                                   start_line, start_col)
                    text.append(chr(int(h, 16)))
                    i += 4
                    continue
                elif e == "u":
                    h = src[i + 2:i + 6]
                    if len(h) != 4 or any(c not in "0123456789abcdefABCDEF"
                                          for c in h):
                        self.error("\\u 转义需要四位十六进制数",
                                   start_line, start_col)
                    text.append(chr(int(h, 16)))
                    i += 6
                    continue
                else:
                    text.append("\\")
                    text.append(e)
                i += 2
                continue
            if ch == "{":
                if i + 1 < n and src[i + 1] == "{":
                    text.append("{")
                    i += 2
                    continue
                flush()
                expr_line = self.line
                expr_col = i - self.line_start + 2
                j = i + 1
                depth = 1
                q = None
                tq = False
                while j < n:
                    c = src[j]
                    if q is not None:
                        if c == "\\":
                            j += 2
                            continue
                        if tq:
                            if src.startswith(q * 3, j):
                                q = None
                                tq = False
                                j += 3
                                continue
                        elif c == q:
                            q = None
                            j += 1
                            continue
                        if c == "\n":
                            self.error("f-string 插值表达式不能跨行",
                                       expr_line, expr_col)
                        j += 1
                        continue
                    if c in "\"'":
                        if src.startswith(c * 3, j):
                            q = c
                            tq = True
                            j += 3
                        else:
                            q = c
                            j += 1
                        continue
                    if c in "([{":
                        depth += 1
                    elif c in ")]":
                        if depth > 1:
                            depth -= 1
                    elif c == "}":
                        depth -= 1
                        if depth == 0:
                            break
                    elif c == "\n":
                        self.error("f-string 插值表达式不能跨行",
                                   expr_line, expr_col)
                    j += 1
                if j >= n or depth != 0:
                    self.error("f-string 的插值缺少 '}'", expr_line, expr_col)
                expr_src = src[i + 1:j]
                if not expr_src.strip():
                    self.error("f-string 插值不能为空", expr_line, expr_col)
                segments.append(["expr", expr_src, expr_line, expr_col])
                i = j + 1
                continue
            if ch == "}":
                if i + 1 < n and src[i + 1] == "}":
                    text.append("}")
                    i += 2
                    continue
                self.error("f-string 里单个 '}' 需要写成 '}}'",
                           start_line, start_col)
            if ch == "\n":
                self.line += 1
                self.line_start = i + 1
            text.append(ch)
            i += 1
        else:
            self.error("f-string 未闭合", start_line, start_col)
        flush()
        self.i = i
        return segments


def lex(source, filename=None):
    """把 `.slang` 源码切成 Token 列表（含 NEWLINE/INDENT/DEDENT/EOF）。"""
    return _Lexer(source, filename).run()


# ---------------------------------------------------------------------------
# 语法分析 + 降级到核心 AST
# ---------------------------------------------------------------------------

_BINOPS = {
    "+": "+", "-": "-", "*": "*", "/": "/", "%": "%",
    "==": "==", "!=": "!=", "<": "<", "<=": "<=", ">": ">", ">=": ">=",
}
_CMP_OPS = {"==", "!=", "<", "<=", ">", ">="}


class _Parser:
    def __init__(self, tokens, filename=None):
        self.tokens = tokens
        self.i = 0
        self.filename = filename
        self._alias_counter = 0

    # ---------- 工具 ----------
    def peek(self, offset=0):
        idx = self.i + offset
        if idx >= len(self.tokens):
            return self.tokens[-1]
        return self.tokens[idx]

    def take(self):
        t = self.tokens[self.i]
        self.i += 1
        return t

    def check(self, kind, value=None):
        t = self.peek()
        if t.kind != kind:
            return False
        return value is None or t.value == value

    def accept(self, kind, value=None):
        if self.check(kind, value):
            return self.take()
        return None

    def expect(self, kind, value=None, message=None):
        t = self.peek()
        if t.kind == kind and (value is None or t.value == value):
            return self.take()
        if message is None:
            want = value if value is not None else kind
            message = "这里需要 %r，实际得到 %r" % (want, t.value)
        self.error(message, t)

    def error(self, message, tok=None):
        tok = tok or self.peek()
        raise ParseError(message, self.filename, tok.line, tok.col)

    def skip_newlines(self):
        while self.check("NEWLINE"):
            self.i += 1

    def sym(self, name, tok):
        return Sym(name, tok.line, tok.col, self.filename)

    def call(self, name, args, tok):
        return Call(self.sym(name, tok), args, tok.line, tok.col, self.filename)

    def end_statement(self):
        if self.accept("NEWLINE"):
            return
        if self.check("EOF") or self.check("DEDENT"):
            return
        # fn(...): 的缩进块会以 DEDENT 结束，而块的最后一个语句已经
        # 吃掉了 NEWLINE；此时下一条语句直接开始是合法的。
        if self.i > 0 and self.tokens[self.i - 1].kind == "DEDENT":
            return
        t = self.peek()
        self.error("一行只能写一个语句；请用换行或 ';' 分隔（这里遇到 %r）"
                   % (t.value,), t)

    # ---------- 顶层 ----------
    def parse_module(self):
        forms = []
        self.skip_newlines()
        while not self.check("EOF"):
            if self.check("INDENT"):
                self.error("意外的缩进（这一行前面没有以 ':' 结尾的块）")
            if self.check("DEDENT"):
                self.error("意外的 dedent")
            forms.append(self.parse_statement())
            self.skip_newlines()
        return forms

    def parse_statement(self):
        t = self.peek()
        if t.kind == "NAME":
            kw = t.value
            if kw in ("def", "let"):
                return self.parse_def()
            if kw == "set":
                return self.parse_set()
            if kw == "defmacro":
                return self.parse_defmacro()
            if kw == "if":
                return self.parse_if()
            if kw == "when":
                return self.parse_when()
            if kw == "do":
                return self.parse_do()
            if kw == "import":
                return self.parse_import()
            if kw == "from":
                return self.parse_from()
            if kw == "load":
                return self.parse_load("load")
            if kw == "load-config":
                return self.parse_load("load-config")
            if kw in ("elif", "else"):
                self.error("%r 必须紧跟在 if 块之后，并且缩进与 if 一致" % kw, t)
            if kw == "return":
                self.error("slang 函数没有 return：函数体最后一个表达式的值"
                           "就是返回值（把 return 去掉即可）", t)
            if kw in ("while", "for"):
                self.error("slang 没有内置循环：请用 range/map/filter/reduce "
                           "或递归实现", t)
            if kw in ("break", "continue"):
                self.error("slang 没有 break/continue", t)
        # 裸赋值 name = expr 是 set 的糖；定义新名字也可以用 def。
        if t.kind == "NAME" and self.peek(1).kind == "OP" \
                and self.peek(1).value == "=":
            return self.parse_assignment()
        if t.kind == "NAME" and self.peek(1).kind == "COMMA":
            self.error("slang 不支持解构赋值或逗号表达式；请分开赋值",
                       self.peek(1))
        expr = self.parse_expr()
        self.end_statement()
        return expr

    def parse_assignment(self):
        name_tok = self.take()
        self.expect("OP", "=")
        if "." in name_tok.value:
            self.error("不能给模块成员赋值（点号名只读）", name_tok)
        value = self.parse_expr()
        self.end_statement()
        return self.call("set", [self.sym(name_tok.value, name_tok), value],
                         name_tok)

    # ---------- 语句 ----------
    def _parse_body(self):
        """解析 ':' 后面的块：缩进多行，或同一行的单个语句。"""
        if self.accept("NEWLINE"):
            self.expect("INDENT", message="':' 后面需要缩进块")
            body = []
            self.skip_newlines()
            while not self.check("DEDENT") and not self.check("EOF"):
                body.append(self.parse_statement())
                self.skip_newlines()
            self.expect("DEDENT")
            if not body:
                self.error("空的缩进块")
            return body
        stmt = self.parse_statement()
        return [stmt]

    def _body_expr(self, body, tok):
        if len(body) == 1:
            return body[0]
        return self.call("do", body, tok)

    def _parse_params(self):
        # 左括号已经吃掉
        if self.check("RP"):
            self.take()
            return Nil(self.peek().line, self.peek().col, self.filename)
        first_tok = self.peek()
        items = []
        while True:
            name_tok = self.expect("NAME", message="参数名必须是标识符")
            name = name_tok.value
            if "." in name:
                self.error("参数名不能包含 '.'", name_tok)
            if self.accept("ELLIPSIS"):
                name += "..."
            if self.check("OP", "="):
                self.error("slang 不支持参数默认值；请在函数体里用 if 处理",
                           self.peek())
            items.append(self.sym(name, name_tok))
            if self.accept("COMMA"):
                if self.check("RP"):
                    break
                continue
            break
        self.expect("RP", message="参数表缺少 ')'")
        if not items:
            return Nil(first_tok.line, first_tok.col, self.filename)
        return Call(items[0], items[1:], first_tok.line, first_tok.col,
                    self.filename)

    def parse_def(self):
        kw = self.take()
        name_tok = self.expect("NAME", message="def 后面需要名字")
        name = name_tok.value
        if "." in name:
            self.error("定义名不能包含 '.'（点号保留给模块成员访问）", name_tok)
        if self.accept("LP"):
            params = self._parse_params()
            if self.accept("OP", "="):
                body = [self.parse_expr()]
                self.end_statement()
            elif self.accept("COLON"):
                body = self._parse_body()
            else:
                self.error("函数定义需要 '=' 或 ':'", self.peek())
            node = self.call("defn", [self.sym(name, name_tok), params] + body,
                             kw)
        elif self.accept("OP", "="):
            value = self.parse_expr()
            self.end_statement()
            node = self.call("def", [self.sym(name, name_tok), value], kw)
        else:
            self.error("定义需要 '=' 或参数表（例如 def x = 1 / def f(a): ...）",
                       self.peek())
        return node

    def parse_set(self):
        kw = self.take()
        name_tok = self.expect("NAME", message="set 后面需要名字")
        if "." in name_tok.value:
            self.error("赋值名不能包含 '.'", name_tok)
        self.expect("OP", "=", message="set 需要 '='")
        value = self.parse_expr()
        self.end_statement()
        return self.call("set", [self.sym(name_tok.value, name_tok), value], kw)

    def parse_defmacro(self):
        kw = self.take()
        name_tok = self.expect("NAME", message="defmacro 后面需要名字")
        if "." in name_tok.value:
            self.error("宏名不能包含 '.'", name_tok)
        self.expect("LP", message="defmacro 后面需要参数表")
        params = self._parse_params()
        if self.accept("OP", "="):
            body = [self.parse_expr()]
            self.end_statement()
        elif self.accept("COLON"):
            body = self._parse_body()
        else:
            self.error("defmacro 需要 '=' 或 ':'", self.peek())
        return self.call("defmacro", [self.sym(name_tok.value, name_tok), params] + body,
                         kw)

    def parse_if(self):
        first = self.take()  # if
        branches = []
        while True:
            cond = self.parse_expr()
            self.expect("COLON", message="if 条件后面需要 ':'")
            body = self._parse_body()
            branches.append((cond, body))
            if self.check("NAME", "elif"):
                self.take()
                continue
            break
        else_body = None
        if self.check("NAME", "else"):
            self.take()
            self.expect("COLON", message="else 后面需要 ':'")
            else_body = self._parse_body()
        node = (Nil(first.line, first.col, self.filename) if else_body is None
                else self._body_expr(else_body, first))
        for cond, body in reversed(branches):
            node = self.call("if", [cond, self._body_expr(body, first), node], first)
        return node

    def parse_when(self):
        kw = self.take()
        cond = self.parse_expr()
        self.expect("COLON", message="when 条件后面需要 ':'")
        body = self._parse_body()
        return self.call("when", [cond, self._body_expr(body, kw)], kw)

    def parse_do(self):
        kw = self.take()
        self.expect("COLON", message="do 后面需要 ':'")
        body = self._parse_body()
        return self.call("do", body, kw)

    def parse_import(self):
        kw = self.take()
        path_tok = self.expect("STR", message="import 后面需要字符串路径")
        path = Str(path_tok.value, path_tok.line, path_tok.col, self.filename)
        if self.check("NAME", "as"):
            self.take()
            name_tok = self.expect("NAME", message="as 后面需要名字")
            if "." in name_tok.value:
                self.error("模块别名不能包含 '.'", name_tok)
            self.end_statement()
            return self.call("def",
                             [self.sym(name_tok.value, name_tok),
                              self.call("import", [path], kw)], kw)
        self.end_statement()
        return self.call("import", [path], kw)

    def parse_from(self):
        kw = self.take()
        path_tok = self.expect("STR", message="from 后面需要字符串路径")
        self.expect("NAME", "import", message="from \"路径\" import 名字...")
        names = []
        while True:
            name_tok = self.expect("NAME", message="import 后面需要名字")
            if "." in name_tok.value:
                self.error("from import 的名字不能包含 '.'", name_tok)
            names.append(name_tok)
            if not self.accept("COMMA"):
                break
        self.end_statement()
        self._alias_counter += 1
        alias = "__slang_import_%d" % self._alias_counter
        alias_sym = Sym(alias, kw.line, kw.col, self.filename)
        path = Str(path_tok.value, path_tok.line, path_tok.col, self.filename)
        forms = [self.call("def", [alias_sym, self.call("import", [path], kw)], kw)]
        for name_tok in names:
            forms.append(self.call(
                "def",
                [self.sym(name_tok.value, name_tok),
                 Sym(alias + "." + name_tok.value, name_tok.line, name_tok.col,
                     self.filename)],
                name_tok,
            ))
        return self.call("do", forms, kw)

    def parse_load(self, which):
        kw = self.take()
        path_tok = self.expect("STR", message="%s 后面需要字符串路径" % which)
        self.end_statement()
        path = Str(path_tok.value, path_tok.line, path_tok.col, self.filename)
        return self.call(which, [path], kw)

    # ---------- 表达式 ----------
    def parse_expr(self):
        return self.parse_ternary()

    def parse_ternary(self):
        node = self.parse_or()
        if self.check("NAME", "if"):
            tok = self.take()
            cond = self.parse_or()
            self.expect("NAME", "else", message="条件表达式需要 else")
            other = self.parse_expr()
            return self.call("if", [cond, node, other], tok)
        return node

    def parse_or(self):
        node = self.parse_and()
        while self.check("NAME", "or"):
            tok = self.take()
            node = self.call("or", [node, self.parse_and()], tok)
        return node

    def parse_and(self):
        node = self.parse_not()
        while self.check("NAME", "and"):
            tok = self.take()
            node = self.call("and", [node, self.parse_not()], tok)
        return node

    def parse_not(self):
        if self.check("NAME", "not") and self._starts_expr(self.peek(1)):
            tok = self.take()
            return self.call("not", [self.parse_not()], tok)
        return self.parse_comparison()

    @staticmethod
    def _starts_expr(tok):
        if tok.kind in ("NUM", "STR", "FSTR", "NAME", "LP", "LB"):
            return True
        return tok.kind == "OP" and tok.value in ("+", "-")

    def parse_comparison(self):
        node = self.parse_additive()
        t = self.peek()
        if t.kind == "OP" and t.value in _CMP_OPS:
            self.take()
            right = self.parse_additive()
            if self.check("OP") and self.peek().value in _CMP_OPS:
                self.error("不支持链式比较；请写成 (a < b) and (b < c) 的形式",
                           self.peek())
            return self.call(_BINOPS[t.value], [node, right], t)
        if t.kind == "OP" and t.value == "=":
            self.error("相等比较请用 '=='；'=' 只用于 def/set 赋值", t)
        return node

    def parse_additive(self):
        node = self.parse_multiplicative()
        while self.check("OP") and self.peek().value in ("+", "-"):
            tok = self.take()
            node = self.call(tok.value, [node, self.parse_multiplicative()], tok)
        return node

    def parse_multiplicative(self):
        node = self.parse_unary()
        while self.check("OP") and self.peek().value in ("*", "/", "%"):
            tok = self.take()
            node = self.call(tok.value, [node, self.parse_unary()], tok)
        return node

    def parse_unary(self):
        t = self.peek()
        if t.kind == "OP" and t.value in ("+", "-"):
            self.take()
            operand = self.parse_unary()
            if t.value == "+":
                return operand
            return self.call("-", [operand], t)
        return self.parse_postfix()

    def parse_postfix(self):
        node = self.parse_primary()
        while True:
            if self.check("LP"):
                tok = self.take()
                args = self._parse_args()
                node = Call(node, args, getattr(node, "line", tok.line),
                            getattr(node, "col", tok.col), self.filename)
                continue
            if self.check("LB"):
                tok = self.take()
                if self.check("COLON"):
                    self.error("slang 不支持切片；请用 take/drop 或循环构造新列表",
                               self.peek())
                index = self.parse_expr()
                if self.check("COLON"):
                    self.error("slang 不支持切片；请用 take/drop 或循环构造新列表",
                               self.peek())
                self.expect("RB", message="下标缺少 ']'")
                node = self.call("nth", [node, index], tok)
                continue
            break
        return node

    def _parse_args(self):
        if self.check("RP"):
            self.take()
            return []
        args = []
        while True:
            if self.check("NAME") and self.peek(1).kind == "OP" \
                    and self.peek(1).value == "=":
                self.error("slang 不支持关键字参数；请按位置传参", self.peek())
            args.append(self.parse_expr())
            if self.accept("COMMA"):
                if self.check("RP"):
                    break
                continue
            break
        self.expect("RP", message="参数表缺少 ')'")
        return args

    def parse_primary(self):
        t = self.peek()
        if t.kind == "NUM":
            self.take()
            return Num(t.value, t.line, t.col, self.filename)
        if t.kind == "STR":
            self.take()
            return Str(t.value, t.line, t.col, self.filename)
        if t.kind == "FSTR":
            self.take()
            return self._parse_fstring(t)
        if t.kind == "NAME":
            self.take()
            if t.value == "fn":
                return self.parse_lambda(t)
            if t.value == "if":
                self.error("if 语句必须独立成行；条件表达式请写 a if cond else b",
                           t)
            if t.value in ("else", "elif", "and", "or"):
                self.error("%r 不能出现在这个位置" % t.value, t)
            return self.sym(t.value, t)
        if t.kind == "LP":
            self.take()
            if self.check("RP"):
                self.take()
                return Nil(t.line, t.col, self.filename)
            node = self.parse_expr()
            self.expect("RP", message="分组缺少 ')'")
            return node
        if t.kind == "LB":
            self.take()
            items = []
            if not self.check("RB"):
                while True:
                    items.append(self.parse_expr())
                    if self.accept("COMMA"):
                        if self.check("RB"):
                            break
                        continue
                    break
            self.expect("RB", message="列表缺少 ']'")
            return self.call("list", items, t)
        if t.kind == "OP":
            self.error("表达式不能以运算符 %r 开头" % t.value, t)
        self.error("这里需要表达式，得到 %r" % (t.value,), t)

    def parse_lambda(self, tok):
        self.expect("LP", message="fn 后面需要参数表")
        params = self._parse_params()
        if self.accept("OP", "="):
            body = [self.parse_expr()]
        elif self.accept("COLON"):
            body = self._parse_body()
        else:
            self.error("fn 需要 '=' 或 ':'", self.peek())
        return self.call("fn", [params] + body, tok)

    def _parse_fstring(self, tok):
        parts = []
        for seg in tok.value:
            if seg[0] == "text":
                if seg[1]:
                    parts.append(Str(seg[1], tok.line, tok.col, self.filename))
            else:
                _, text, line, col = seg
                parts.append(parse_expression_text(
                    text, self.filename, line, col))
        if not parts:
            return Str("", tok.line, tok.col, self.filename)
        if len(parts) == 1 and isinstance(parts[0], Str):
            return parts[0]
        return self.call("concat", parts, tok)


def parse_expression_text(text, filename=None, line=1, col=1):
    """解析 f-string 插值里的单个表达式。"""
    # f"{ 1 + 2 }" 里的前导空格如果直接交给词法器，会被当成缩进；
    # 先去掉两端空白，并把列号补偿回来。
    stripped = text.strip()
    if stripped != text:
        col += len(text) - len(text.lstrip())
        text = stripped
    toks = lex(text, filename)
    adjusted = []
    for t in toks:
        if t.kind == "EOF":
            continue
        if t.kind == "NEWLINE":
            continue
        t.line = line + (t.line - 1)
        if t.line == line:
            t.col += col - 1
        adjusted.append(t)
    eof = Token("EOF", None, line, col)
    parser = _Parser(adjusted + [eof], filename)
    node = parser.parse_expr()
    parser.skip_newlines()
    if not parser.check("EOF"):
        parser.error("f-string 插值里有多余的内容", parser.peek())
    return node


def parse(source, filename=None):
    """把 `.slang` 源码解析成核心 AST（顶层表达式列表）。"""
    return _Parser(lex(source, filename), filename).parse_module()


# ---------------------------------------------------------------------------
# 解码为 `.lang`
# ---------------------------------------------------------------------------

def _render_lines(node, indent=0, width=80):
    """把节点渲染成带缩进的多行；每行都包含 indent 个前导空格。"""
    pad = " " * indent
    flat = to_source(node)
    if not isinstance(node, Call) or len(flat) + indent <= width:
        return [pad + flat]

    def render_args(args, extra=2):
        lines = []
        for arg in args:
            lines.extend(_render_lines(arg, indent + extra, width))
        return lines

    head = node.head.name if isinstance(node.head, Sym) else None
    args = node.args

    if head in ("defn", "defmacro") and len(args) >= 2:
        lines = [pad + "(" + head + " " + to_source(args[0])
                 + " " + to_source(args[1])]
        for body in args[2:]:
            lines.extend(_render_lines(body, indent + 2, width))
        lines[-1] += ")"
        return lines

    if head == "fn" and args:
        lines = [pad + "(fn " + to_source(args[0])]
        for body in args[1:]:
            lines.extend(_render_lines(body, indent + 2, width))
        lines[-1] += ")"
        return lines

    if head == "if" and len(args) == 3:
        lines = [pad + "(if " + to_source(args[0])]
        lines.extend(_render_lines(args[1], indent + 2, width))
        lines.extend(_render_lines(args[2], indent + 2, width))
        lines[-1] += ")"
        return lines

    if head == "when" and len(args) == 2:
        lines = [pad + "(when " + to_source(args[0])]
        lines.extend(_render_lines(args[1], indent + 2, width))
        lines[-1] += ")"
        return lines

    if head in ("do", "def", "set") and args:
        # (do ...) 把每个子表达式放在新行；def/set 的值放新行。
        lines = [pad + "(" + head]
        if head in ("def", "set"):
            lines[0] += " " + to_source(args[0])
            rest = args[1:]
        else:
            rest = args
        lines.extend(render_args(rest))
        lines[-1] += ")"
        return lines

    lines = [pad + "(" + to_source(node.head)]
    lines.extend(render_args(args))
    lines[-1] += ")"
    return lines


def decode_to_lang(nodes, width=80):
    """把 AST 列表解码成可运行的 `@sexpr` 文本。"""
    out = ["@sexpr"]
    for node in nodes:
        out.extend(_render_lines(node, 0, width))
    return "\n".join(out) + "\n"


def decode_source(source, filename=None, width=80):
    return decode_to_lang(parse(source, filename), width=width)


def is_slang_path(path):
    return path.lower().endswith(".slang")
