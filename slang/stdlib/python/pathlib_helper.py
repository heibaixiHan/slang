"""Python pathlib 的少量辅助函数。

slang 侧无法直接写 ``Path(p).read_text()``（调用结果再取成员），
所以这里提供扁平函数；路径按进程当前工作目录解析，与 Python 一致。
"""
from pathlib import Path


def read_text(path, encoding="utf-8"):
    return Path(path).read_text(encoding=encoding)


def write_text(path, text, encoding="utf-8"):
    Path(path).write_text(text, encoding=encoding)
    return len(text)
