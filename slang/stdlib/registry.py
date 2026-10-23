"""slang 标准库模块注册表。

标准库统一放在 ``slang/stdlib/`` 下：

    core/     语言自举层（base / std）
    python/   Python 标准库的常用封装
    c/        C 标准库 / libm 的常用封装
    web/      Flask 风格 Web 框架

逻辑导入名到文件的映射是约定式的：

    std.python.os  -> python/os.lang
    python.os      -> python/os.lang
    std.c.math     -> c/math.lang
    c.math         -> c/math.lang
    std.web.flask  -> web/flask.lang
    web.flask      -> web/flask.lang
    flask          -> web/flask.lang

``load_file`` 会先检查调用方目录/项目里是否存在同名文件；本地文件优先，
找不到时才回退到标准库。这样项目可以覆盖标准库模块，而不会被注册表抢走。
"""
import os

STDLIB_ROOT = os.path.dirname(os.path.abspath(__file__))


def resolve(name):
    """把逻辑标准库名解析成绝对文件路径；不是标准库名时返回 None。"""
    if not isinstance(name, str) or not name:
        return None
    clean = name[4:] if name.startswith("std.") else name
    if clean == "flask":
        clean = "web.flask"
    if not clean.startswith(("python.", "c.", "web.")):
        return None
    rel = clean.replace(".", os.sep) + ".lang"
    path = os.path.join(STDLIB_ROOT, rel)
    if os.path.isfile(path):
        return os.path.abspath(path)
    return None


def list_modules():
    """列出当前可导入的标准库逻辑名（用于文档/检查）。"""
    names = []
    for group in ("python", "c", "web"):
        folder = os.path.join(STDLIB_ROOT, group)
        if not os.path.isdir(folder):
            continue
        for entry in sorted(os.listdir(folder)):
            if entry.endswith(".lang"):
                logic = group + "." + entry[:-5]
                names.append(logic)
                names.append("std." + logic)
    flask = os.path.join(STDLIB_ROOT, "web", "flask.lang")
    if os.path.isfile(flask):
        names.append("flask")
    return names
