"""slang 错误类型。"""


class SlangError(Exception):
    """所有 slang 错误的基类。"""

    def __init__(self, message, filename=None, line=None, col=None):
        self.message = message
        self.filename = filename
        self.line = line
        self.col = col
        super().__init__(str(self))

    def __str__(self):
        loc = []
        if self.filename:
            loc.append(str(self.filename))
        if self.line is not None:
            loc.append("第 %s 行" % self.line)
        if self.col is not None:
            loc.append("第 %s 列" % self.col)
        prefix = " ".join(loc)
        return (prefix + ": " + self.message) if prefix else self.message


class LexError(SlangError):
    pass


class ParseError(SlangError):
    pass


class EvalError(SlangError):
    pass


class LoadError(SlangError):
    pass
