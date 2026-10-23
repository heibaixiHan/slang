"""slang 语言：极简、可自举、定义可被任意覆盖的编程语言。"""

__version__ = "0.6.0"

from .errors import EvalError, LexError, LoadError, ParseError, SlangError
from .interp import Interp

__all__ = [
    "__version__",
    "Interp",
    "SlangError",
    "LexError",
    "ParseError",
    "EvalError",
    "LoadError",
]
