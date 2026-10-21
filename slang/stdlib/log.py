"""内置 log 模块：显式 (import "log") 后才存在，不自动配置/加载。

启用后解释器会把顶层表达式、函数/原语调用、def/set、load/import、print、
错误等写成 JSON Lines 文件，逐行 flush，便于事后审计或回放。

用法：
    (def log (import "log"))
    (log.start "run.jsonl")        ; 默认截断；limit=500 字符
    (log.start "run.jsonl" 0 "a")  ; 0=不截断，"a"=追加
    (log.info "checkpoint" "data") ; 手动事件
    (log.stop)
"""
from ..env import Env
from ..errors import EvalError


def _arg(args, idx, kind, interp, node, what):
    if idx >= len(args) or not isinstance(args[idx], kind):
        interp.error("%s 参数类型错误" % what, node)
    return args[idx]


def _start(interp, args, env, node):
    if len(args) not in (1, 2, 3) or not isinstance(args[0], str):
        interp.error("log.start 需要 (路径) 或 (路径 字符上限) 或 (路径 字符上限 模式)", node)
    path = interp._resolve_io(args[0], node)
    limit = 500
    mode = "w"
    if len(args) >= 2:
        limit = args[1]
        if isinstance(limit, bool) or not isinstance(limit, int):
            interp.error("log.start 的字符上限必须是整数（0 表示不截断）", node)
        if limit < 0:
            interp.error("log.start 的字符上限不能为负数", node)
    if len(args) == 3:
        mode = args[2]
        if not isinstance(mode, str):
            interp.error("log.start 的模式必须是 \"w\" 或 \"a\"", node)
    try:
        return interp.open_log(path, mode=mode, limit=limit)
    except (OSError, ValueError, EvalError) as exc:
        interp.error("启动日志失败 %s: %s" % (path, exc), node)


def _stop(interp, args, env, node):
    if args:
        interp.error("log.stop 不接受参数", node)
    return interp.close_log()


def _enabled(interp, args, env, node):
    if args:
        interp.error("log.enabled 不接受参数", node)
    return interp.log_file is not None


def _log_path(interp, args, env, node):
    if args:
        interp.error("log.path 不接受参数", node)
    return interp.log_path or ""


def _event(interp, args, env, node):
    if len(args) not in (1, 2) or not isinstance(args[0], str):
        interp.error("log.event 需要 (事件名) 或 (事件名 数据)", node)
    data = args[1] if len(args) == 2 else None
    return interp.log_manual("event", args[0], data, node)


def _make_level(level):
    def fn(interp, args, env, node):
        if len(args) not in (1, 2) or not isinstance(args[0], str):
            interp.error("log.%s 需要 (名称/消息) 或 (名称/消息 数据)" % level, node)
        data = args[1] if len(args) == 2 else None
        return interp.log_manual(level, args[0], data, node)
    return fn


def build_module(interp):
    from ..interp import Prim

    env = Env("log", parent=None, kind="module", is_module=True)
    prims = {
        "start": _start,
        "stop": _stop,
        "enabled": _enabled,
        "path": _log_path,
        "event": _event,
        "record": _event,
        "info": _make_level("info"),
        "warn": _make_level("warn"),
        "error": _make_level("error"),
        "debug": _make_level("debug"),
    }
    for name, fn in prims.items():
        env.bind(name, Prim("log." + name,
                            lambda i, a, e, n, _fn=fn: _fn(i, a, e, n)))
    return env
