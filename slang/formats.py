"""源码格式（方言）识别与适配器注册表。

每个文件可以在正文前用 @xxx 声明语法格式（允许前置 shebang 和空行）：

    @sexpr
    (println "hi")

支持的内置格式：
    sexpr / s-expr / lisp / scheme   -> 严格 S 表达式（.lang）
    slang                            -> 缩进式人类友好格式（.slang）
    auto / generic                   -> 通用宽松转换器

`.slang` 文件即使不写 @slang 也会按扩展名识别；`.lang` 文件可以显式
写 @slang 切换到缩进格式。声明了未适配的格式（例如 @lua）或者完全
没声明时，会退回 auto 模式，并在运行前向 stderr 打印警告。格式声明
行必须单独占一行，后面只能跟版本号或注释，否则报错（避免把代码静默
丢掉）。
"""
import re

from .errors import ParseError

HEADER_RE = re.compile(r"^\s*@([A-Za-z0-9_.-]+)(.*)$")
VERSION_RE = re.compile(r"^v?[0-9]+(?:\.[0-9]+)*(?:[-+][A-Za-z0-9_.-]+)?$")

# 格式名 -> 解析模式
KNOWN_FORMATS = {
    "sexpr": "sexpr",
    "s-expr": "sexpr",
    "s_expr": "sexpr",
    "lisp": "sexpr",
    "scheme": "sexpr",
    "sexpr1": "sexpr",
    "slang": "slang",
    "auto": "auto",
    "generic": "auto",
}


def register_format(name, mode):
    """注册一个新的格式适配器（mode 支持 'sexpr' / 'slang' / 'auto'）。"""
    if mode not in ("sexpr", "slang", "auto"):
        raise ValueError("mode 只能是 sexpr、slang 或 auto")
    KNOWN_FORMATS[name.lower()] = mode


def split_header(text, filename=None):
    """分离格式声明。

    允许声明前有 shebang 和空行。声明行在返回的正文里会被置空而不是删除，
    因此正文的行号与原文完全一致，调用方不需要再补偏移。

    返回 (format_name 或 None, 附加说明, 正文, 声明所在行号 或 None)
    """
    if not text:
        return None, "", text, None
    lines = text.split("\n")
    idx = 0
    while idx < len(lines):
        stripped = lines[idx].strip()
        if stripped == "" or stripped.startswith("#!"):
            idx += 1
            continue
        break
    if idx >= len(lines):
        return None, "", text, None
    m = HEADER_RE.match(lines[idx].rstrip("\r"))
    if not m:
        return None, "", text, None
    trailing = m.group(2).strip()
    if trailing and not trailing.startswith((";", "#")) and not VERSION_RE.match(trailing):
        tail = m.group(2)
        offset = m.start(2) + (len(tail) - len(tail.lstrip()))
        raise ParseError(
            "格式声明必须单独占一行：@%s 后面的 %r 会被忽略，"
            "需要执行的代码请另起一行" % (m.group(1), trailing),
            filename,
            idx + 1,
            offset + 1,
        )
    body_lines = list(lines)
    body_lines[idx] = ""
    return m.group(1), trailing, "\n".join(body_lines), idx + 1


def has_code(text):
    """文件正文里是否有真正的代码（排除空行、shebang、; 与 # 注释）。"""
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#!"):
            continue
        if stripped.startswith(";") or stripped.startswith("#"):
            continue
        return True
    return False


def resolve(fmt_name, filename, has_body=True):
    """返回 (解析模式, 警告文本 或 None)。

    has_body 为 False（空文件 / 纯注释）时不警告“未声明格式”，
    对未知格式也一样，避免注释文件产生噪声。
    """
    if fmt_name is None:
        if not has_body:
            return "auto", None
        warn = ("警告: %s: 未声明格式（首行缺少 @xxx），"
                "已使用通用自动转换器（可能导致语义偏差）" % filename)
        return "auto", warn

    mode = KNOWN_FORMATS.get(fmt_name.lower())
    if mode is not None:
        return mode, None

    if not has_body:
        return "auto", None
    warn = ("警告: %s: 格式 '@%s' 未适配，"
            "已使用通用自动转换器（可能导致语义偏差）" % (filename, fmt_name))
    return "auto", warn
