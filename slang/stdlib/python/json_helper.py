"""Python json 标准库的辅助函数。

jd 的默认参数让 slang 侧不必处理 Python 的关键字参数。
"""
import json


def dumps_pretty(obj, indent=2):
    return json.dumps(obj, ensure_ascii=False, indent=indent, default=str)


def read(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def write(path, obj, indent=2):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=indent, default=str)
    return path
