"""内置 logger 模块：分级日志，默认 ``warning``。

与 ``log`` 的区别：
  * ``log`` 是解释器审计日志（记录调用/定义/加载，写 JSON Lines）；
  * ``logger`` 是普通程序日志，按级别过滤，默认输出到 stderr。

级别从低到高：DEBUG < INFO < WARNING < ERROR < CRITICAL。
默认阈值是 WARNING；把阈值设为最低的 DEBUG 会输出所有级别。

用法：
    (def logger (import "logger"))
    (logger.debug "detail")       ; 默认被过滤
    (logger.warning "careful")    ; 默认输出
    (logger.set_level "debug")    ; 之后所有级别都输出
    (logger.info "all visible")
"""
import json
import sys
import threading

from ..env import Env

_LEVELS = {
    "debug": 10,
    "info": 20,
    "warning": 30,
    "warn": 30,
    "error": 40,
    "critical": 50,
    "fatal": 50,
}
_NAMES = {
    10: "DEBUG",
    20: "INFO",
    30: "WARNING",
    40: "ERROR",
    50: "CRITICAL",
}


def _level_name(value):
    if isinstance(value, str):
        key = value.strip().lower()
        if key in _LEVELS:
            return _LEVELS[key]
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if value in _NAMES else None


def _format_data(value):
    if isinstance(value, (dict, list, tuple)):
        try:
            return json.dumps(value, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            return str(value)
    return str(value)


class LoggerState:
    def __init__(self):
        self.level = _LEVELS["warning"]
        self.stream_name = "stderr"
        self.format = "[{level}] {message}"
        self.lock = threading.Lock()

    def stream(self):
        return sys.stdout if self.stream_name == "stdout" else sys.stderr

    def emit(self, level, message, data=None):
        if level < self.level:
            return False
        text = message if isinstance(message, str) else str(message)
        if data is not None:
            text = text + " " + _format_data(data)
        line = self.format.format(
            level=_NAMES.get(level, str(level)),
            message=text,
            name="logger",
        )
        if not line.endswith("\n"):
            line += "\n"
        stream = self.stream()
        with self.lock:
            try:
                stream.write(line)
                stream.flush()
            except (OSError, ValueError):
                # 日志失败不应反过来破坏业务逻辑；退回 stderr 再试一次。
                try:
                    sys.stderr.write(line)
                    sys.stderr.flush()
                except (OSError, ValueError):
                    pass
        return True


def build_module(interp):
    from ..interp import Prim

    env = Env("logger", parent=None, kind="module", is_module=True)
    state = LoggerState()

    def fail(message, node):
        interp.error(message, node)

    def start(level, message, data, node):
        if not isinstance(message, str):
            fail("logger 消息必须是字符串", node)
        if data is not None and len(data) > 1:
            fail("logger 最多接受 (消息 数据) 两个参数", node)
        value = data[0] if data else None
        state.emit(level, message, value)
        return []

    def make_level(level):
        def fn(i, args, env_, node):
            if len(args) not in (1, 2):
                fail("logger.%s 需要 (消息) 或 (消息 数据)"
                     % _NAMES.get(level, level).lower(), node)
            return start(level, args[0], args[1:], node)
        return fn

    def _set_level(i, args, env_, node):
        if len(args) != 1:
            fail("logger.set_level 需要 1 个级别", node)
        level = _level_name(args[0])
        if level is None:
            fail("未知日志级别 %r（可用 debug/info/warning/error/critical）"
                 % (args[0],), node)
        previous = _NAMES.get(state.level, str(state.level))
        state.level = level
        return previous

    def _get_level(i, args, env_, node):
        if args:
            fail("logger.get_level 不接受参数", node)
        return _NAMES.get(state.level, str(state.level))

    def _set_stream(i, args, env_, node):
        if len(args) != 1 or not isinstance(args[0], str):
            fail("logger.set_stream 需要 \"stdout\" 或 \"stderr\"", node)
        name = args[0].strip().lower()
        if name not in ("stdout", "stderr"):
            fail("logger.set_stream 只支持 \"stdout\" 或 \"stderr\"", node)
        previous = state.stream_name
        state.stream_name = name
        return previous

    def _set_format(i, args, env_, node):
        if len(args) != 1 or not isinstance(args[0], str):
            fail("logger.set_format 需要格式字符串", node)
        fmt = args[0]
        try:
            fmt.format(level="LEVEL", message="message", name="logger")
        except (KeyError, IndexError, ValueError) as exc:
            fail("日志格式非法: %s" % exc, node)
        previous = state.format
        state.format = fmt
        return previous

    def _enabled(i, args, env_, node):
        if len(args) != 1:
            fail("logger.enabled 需要 1 个级别", node)
        level = _level_name(args[0])
        if level is None:
            fail("未知日志级别 %r" % (args[0],), node)
        return level >= state.level

    def _levels(i, args, env_, node):
        if args:
            fail("logger.levels 不接受参数", node)
        return ["debug", "info", "warning", "error", "critical"]

    def _reset(i, args, env_, node):
        if args:
            fail("logger.reset 不接受参数", node)
        state.level = _LEVELS["warning"]
        state.stream_name = "stderr"
        state.format = "[{level}] {message}"
        return []

    def _log(i, args, env_, node):
        if len(args) not in (2, 3) or not isinstance(args[0], str):
            fail("logger.log 需要 (级别 消息) 或 (级别 消息 数据)", node)
        level = _level_name(args[0])
        if level is None:
            fail("未知日志级别 %r" % (args[0],), node)
        return start(level, args[1], args[2:], node)

    prims = {
        "set_level": _set_level,
        "get_level": _get_level,
        "set_stream": _set_stream,
        "set_format": _set_format,
        "enabled": _enabled,
        "levels": _levels,
        "reset": _reset,
        "log": _log,
        "debug": make_level(10),
        "info": make_level(20),
        "warning": make_level(30),
        "warn": make_level(30),
        "error": make_level(40),
        "critical": make_level(50),
        "fatal": make_level(50),
    }
    for name, fn in prims.items():
        env.bind(name, Prim("logger." + name,
                            lambda i, a, e, n, _fn=fn: _fn(i, a, e, n)))
    return env
