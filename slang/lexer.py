"""纯 Python 词法分析器。

C++ 核心不可用时的后备实现，同时作为 C++ 版本的“行为基准”。
token = (kind, value, line, col, end)

kind: LP RP LB RB LC RC STR NUM SYM OP
"""
import re

from .errors import LexError

OP_CHARS = "+-*/<>=!"
ATOM_END = set(" \t\r\n()[]{};,#\"")
WHITESPACE = " \t\r"
ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "\\": "\\", '"': '"', "0": "\0"}

NUM_RE = re.compile(r"[+-]?(?:[0-9]+\.[0-9]*|\.[0-9]+|[0-9]+)(?:[eE][+-]?[0-9]+)?")
# auto 模式把前导 +/- 留给 parser：这样 `1 +2`、`1 -2` 与 `1 *2`
# 都按同一个二元运算规则解释，不再出现“加号被吞进数字”的不一致。
AUTO_NUM_RE = re.compile(r"(?:[0-9]+\.[0-9]*|\.[0-9]+|[0-9]+)(?:[eE][+-]?[0-9]+)?")

_OPEN = {"(": "LP", "[": "LB", "{": "LC"}
_CLOSE = {")": "RP", "]": "RB", "}": "RC"}


def _short(text, limit=80):
    if len(text) <= limit:
        return text
    return text[:limit] + "…（共 %d 字符）" % len(text)


def lex(source, mode="sexpr", filename=None):
    # CLI 的 read_source 使用 universal newlines；直接调用词法器时也做同样
    # 归一化，保证裸 CR 与文件词法结果一致，并避免 CRLF 被算成两行。
    if "\r" in source:
        source = source.replace("\r\n", "\n").replace("\r", "\n")
    toks = []
    i = 0
    n = len(source)
    line = 1
    line_start = 0
    num_re = NUM_RE if mode == "sexpr" else AUTO_NUM_RE
    op_chars = OP_CHARS if mode == "sexpr" else OP_CHARS + "%"

    while i < n:
        ch = source[i]
        if ch == "\n":
            line += 1
            line_start = i + 1
            i += 1
            continue
        if ch in WHITESPACE:
            i += 1
            continue
        if ch in ";#":
            while i < n and source[i] != "\n":
                i += 1
            continue

        if ch == ",":
            col = i - line_start + 1
            toks.append(("COMMA", ",", line, col, col + 1))
            i += 1
            continue

        col = i - line_start + 1

        if ch in _OPEN:
            toks.append((_OPEN[ch], ch, line, col, col + 1))
            i += 1
            continue
        if ch in _CLOSE:
            toks.append((_CLOSE[ch], ch, line, col, col + 1))
            i += 1
            continue

        if ch == '"':
            j = i + 1
            buf = []
            while j < n and source[j] != '"':
                if source[j] == "\\" and j + 1 < n:
                    e = source[j + 1]
                    if e == "\n":
                        raise LexError("字符串不能跨行（不支持反斜杠续行）", filename, line, col)
                    if e in ("x", "u"):
                        width = 2 if e == "x" else 4
                        digits = source[j + 2:j + 2 + width]
                        if len(digits) != width or any(
                                c not in "0123456789abcdefABCDEF" for c in digits):
                            if e == "x":
                                raise LexError("\\x 转义需要两位十六进制数",
                                               filename, line, j - line_start + 1)
                            raise LexError("\\u 转义需要四位十六进制数",
                                           filename, line, j - line_start + 1)
                        buf.append(chr(int(digits, 16)))
                        j += 2 + width
                    elif e in ESCAPES:
                        buf.append(ESCAPES[e])
                        j += 2
                    else:
                        # 未知转义保留反斜杠，避免静默吞掉字符
                        buf.append("\\")
                        buf.append(e)
                        j += 2
                elif source[j] == "\n":
                    raise LexError("字符串不能跨行（请用 \\n 转义）", filename, line, col)
                else:
                    buf.append(source[j])
                    j += 1
            if j >= n:
                raise LexError("字符串未闭合", filename, line, col)
            toks.append(("STR", "".join(buf), line, col, (j + 1) - line_start + 1))
            i = j + 1
            continue

        num = num_re.match(source, i)
        if num and (num.end() >= n or source[num.end()] in ATOM_END):
            text = num.group(0)
            try:
                value = float(text) if any(c in text for c in ".eE") else int(text)
            except ValueError as exc:
                if "limit" in str(exc) or "digits" in str(exc):
                    raise LexError("整数太大（超过 Python 字符串转换上限）: %s"
                                   % _short(text), filename, line, col)
                raise LexError("非法数字字面量 %r: %s" % (_short(text), exc),
                               filename, line, col)
            toks.append(("NUM", value, line, col, num.end() - line_start + 1))
            i = num.end()
            continue

        if ch in op_chars:
            j = i
            while j < n and source[j] in op_chars:
                j += 1
            toks.append(("OP", source[i:j], line, col, j - line_start + 1))
            i = j
            continue

        j = i
        while j < n and source[j] not in ATOM_END:
            j += 1
        if j == i:
            raise LexError("无法识别的字符 %r" % ch, filename, line, col)
        toks.append(("SYM", source[i:j], line, col, j - line_start + 1))
        i = j

    return toks
