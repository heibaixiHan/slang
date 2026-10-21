"""TOML 兼容层：优先 tomllib / tomli，最后回退到内置的极简解析器。

极简解析器覆盖项目配置需要的子集：表、点号键、字符串、数字、布尔、
数组、行内注释。遇到多行字符串、行内表、数组表等高级语法会明确报错，
而不是静默解析成错误的值。Python 3.11+ 使用 tomllib；3.9/3.10 建议安装
tomli（pyproject 已声明条件依赖）。
"""
import re

try:
    import tomllib as _toml

    def loads(text):
        return _toml.loads(text)

    def load(path):
        with open(path, "rb") as fh:
            return _toml.load(fh)

    BACKEND = "tomllib"

except ImportError:  # pragma: no cover
    try:
        import tomli as _toml

        def loads(text):
            return _toml.loads(text)

        def load(path):
            with open(path, "rb") as fh:
                return _toml.load(fh)

        BACKEND = "tomli"

    except ImportError:
        BACKEND = "mini"

        def loads(text):
            return _mini_loads(text)

        def load(path):
            with open(path, "r", encoding="utf-8") as fh:
                return _mini_loads(fh.read())

_DATE_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}(?:[Tt ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:[Zz]|[+-]\d{2}:\d{2})?)?$"
)
_TIME_RE = re.compile(r"^\d{2}:\d{2}:\d{2}(?:\.\d+)?$")


def _split_top(value):
    """按顶层逗号切分数组内容（忽略字符串/嵌套数组里的逗号）。"""
    parts = []
    buf = []
    depth = 0
    in_str = None
    escape = False
    for ch in value:
        if in_str:
            buf.append(ch)
            if escape:
                escape = False
            elif ch == "\\" and in_str == '"':
                escape = True
            elif ch == in_str:
                in_str = None
            continue
        if ch in "\"'":
            in_str = ch
            buf.append(ch)
        elif ch in "[{":
            depth += 1
            buf.append(ch)
        elif ch in "]}":
            depth = max(0, depth - 1)
            buf.append(ch)
        elif ch == "," and depth == 0:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    if buf:
        parts.append("".join(buf))
    return [p.strip() for p in parts if p.strip()]


def _strip_comment(line):
    """去掉字符串之外的行内注释（# 之后）。"""
    quote = None
    escape = False
    for i, ch in enumerate(line):
        if quote:
            if escape:
                escape = False
            elif ch == "\\" and quote == '"':
                escape = True
            elif ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
        elif ch == "#":
            return line[:i]
    return line


def _key_parts(key):
    """把 TOML 键拆成路径；整体带引号的键不按点号拆分。"""
    key = key.strip()
    if len(key) >= 2 and key[0] == key[-1] and key[0] in "\"'":
        return [key[1:-1]]
    return [p.strip().strip('"').strip("'") for p in key.split(".") if p.strip()]


def _parse_quoted(text):
    quote = text[0]
    if len(text) < 2 or text[-1] != quote:
        raise ValueError("字符串未闭合: %s" % text)
    body = text[1:-1]
    if quote == "'":
        return body
    out = []
    i = 0
    escapes = {
        "n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\",
        "b": "\b", "f": "\f", "/": "/",
    }
    while i < len(body):
        ch = body[i]
        if ch != "\\":
            out.append(ch)
            i += 1
            continue
        if i + 1 >= len(body):
            raise ValueError("字符串末尾的反斜杠非法")
        nxt = body[i + 1]
        if nxt in escapes:
            out.append(escapes[nxt])
            i += 2
        elif nxt in ("u", "U"):
            width = 4 if nxt == "u" else 8
            digits = body[i + 2:i + 2 + width]
            if len(digits) != width or not re.match(r"^[0-9a-fA-F]+$", digits):
                raise ValueError("非法的 Unicode 转义")
            out.append(chr(int(digits, 16)))
            i += 2 + width
        else:
            raise ValueError("非法的字符串转义 \\%s" % nxt)
    return "".join(out)


def _parse_value(text):
    text = text.strip()
    if not text:
        raise ValueError("缺少值")
    if text.startswith('"""') or text.startswith("'''"):
        raise ValueError("不支持多行字符串")
    if text.startswith("{"):
        raise ValueError("不支持行内表（inline table）")
    if text[0] in "\"'":
        return _parse_quoted(text)
    if text.startswith("[") and text.endswith("]"):
        return [_parse_value(p) for p in _split_top(text[1:-1])]
    if text in ("true", "false"):
        return text == "true"
    if _DATE_RE.match(text) or _TIME_RE.match(text):
        # 日期时间目前按原文字符串返回；项目配置不需要 datetime 对象。
        return text
    cleaned = text.replace("_", "")
    lower = cleaned.lower().lstrip("+-")
    if lower in ("inf", "nan"):
        return float(cleaned)
    if re.match(r"^[+-]?0[0-9]", cleaned):
        raise ValueError("数字不能有前导零: %s" % text)
    if lower.startswith(("0x", "0o", "0b")):
        try:
            return int(cleaned, 0)
        except ValueError:
            raise ValueError("非法数字: %s" % text)
    if re.match(r"^[+-]?(?:0|[1-9][0-9]*)$", cleaned):
        return int(cleaned)
    if re.match(r"^[+-]?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?$",
                cleaned):
        return float(cleaned)
    raise ValueError("无法识别的 TOML 值（字符串必须加引号）: %s" % text)


def _bracket_balance(text):
    depth = 0
    quote = None
    escape = False
    for ch in text:
        if quote:
            if escape:
                escape = False
            elif ch == "\\" and quote == '"':
                escape = True
            elif ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
        elif ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
    return depth


def _mini_loads(text):
    root = {}
    current = root
    lines = text.splitlines()
    declared_tables = set()
    i = 0
    while i < len(lines):
        line = _strip_comment(lines[i]).strip()
        i += 1
        if not line:
            continue
        if line.startswith("[[") or (line.startswith("[") and line.endswith("]]")):
            raise ValueError("不支持数组表 [[...]]: %s" % line)
        if line.startswith("["):
            if not line.endswith("]"):
                raise ValueError("不支持的 TOML 表头: %s" % line)
            header = line[1:-1].strip()
            if header in declared_tables:
                raise ValueError("重复表声明: [%s]" % header)
            declared_tables.add(header)
            current = root
            for part in _key_parts(header):
                if part in current:
                    if not isinstance(current[part], dict):
                        raise ValueError("键 %r 已经是非表值" % part)
                    current = current[part]
                else:
                    current = current.setdefault(part, {})
            continue
        if "=" not in line:
            raise ValueError("无法解析的 TOML 行: %s" % line)
        key, _, value = line.partition("=")
        parts = _key_parts(key)
        if not parts:
            raise ValueError("空键: %s" % line)
        value = value.strip()
        while value.startswith("[") and _bracket_balance(value) > 0:
            if i >= len(lines):
                raise ValueError("数组未闭合")
            value += " " + _strip_comment(lines[i]).strip()
            i += 1
        target = current
        for part in parts[:-1]:
            if part in target and not isinstance(target[part], dict):
                raise ValueError("键 %r 已经是非表值" % part)
            target = target.setdefault(part, {})
        if parts[-1] in target:
            raise ValueError("重复键: %s" % ".".join(parts))
        target[parts[-1]] = _parse_value(value)
    return root
