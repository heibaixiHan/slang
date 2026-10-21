"""配置 (.toml) 与源文件 (.lang.build / .lang) 的加载器。

加载顺序（覆盖语义）：
    slang.toml 里的 builds.files 按数组顺序依次求值进同一个环境，
    后加载的定义直接覆盖先加载的同名定义；程序文件最后求值，
    同样可以覆盖任意已有定义。

模块语义（0.3.0 起）：
    import 得到一个独立模块。模块始终只看到 base/std/启动配置构建出的
    固定内置快照（interp.module_base），看不到主程序在导入前后定义的全局
    变量。因此 import 顺序不会改变模块里的自由变量；模块之间需要协作时
    显式 import。内置模块（目前是 log）也必须显式 import，不会自动加载。

相对路径解析基准是“写这条 load/import 语句的源文件目录”；
不再回退到项目根或 cwd，避免误加载项目外的同名文件。
"""
import os

from . import formats, native, tomlcompat
from .ast_nodes import to_source
from .env import Env
from .errors import LoadError
from .parser import parse

CONFIG_TOP_KEYS = {"project", "builds", "build"}
PROJECT_KEYS = {"name", "version", "main"}
BUILDS_KEYS = {"files"}
BUILTIN_MODULES = {"log", "ffi"}


def read_source(path):
    try:
        # utf-8-sig 同时兼容有/无 BOM 的文件
        with open(path, "r", encoding="utf-8-sig") as fh:
            return fh.read()
    except UnicodeDecodeError as exc:
        raise LoadError("文件不是合法 UTF-8: %s（%s）" % (path, exc))
    except OSError as exc:
        raise LoadError("无法读取 %s: %s" % (path, exc))


def resolve_path(interp, path, base_dir=None):
    """解析相对路径。

    有 base_dir（来自 AST 节点/配置文件）时严格只按该目录解析；这是
    “相对语句所在文件”的契约，不能悄悄回退到项目根。没有 base_dir 时
    （CLI 入口）才依次尝试 current_dir、project_root、cwd。
    """
    if os.path.isabs(path):
        return path
    if base_dir is not None:
        return os.path.abspath(os.path.join(base_dir, path))
    candidates = []
    cur_dir = getattr(interp, "current_dir", None)
    if cur_dir:
        candidates.append(os.path.join(cur_dir, path))
    if interp.project_root:
        candidates.append(os.path.join(interp.project_root, path))
    candidates.append(os.path.join(os.getcwd(), path))
    for cand in candidates:
        if os.path.exists(cand):
            return os.path.abspath(cand)
    return os.path.abspath(candidates[0])


def read_nodes(interp, path, warn=True):
    """读取文件 -> (表达式列表, 解析模式, 格式名, 词法后端)。"""
    text = read_source(path)
    fmt_name, _version, body, _header_line = formats.split_header(text, path)
    # .slang 扩展名本身就是格式声明；显式的 @xxx 仍然优先。
    if fmt_name is None and path.lower().endswith(".slang"):
        fmt_name = "slang"
    # 纯注释 / 只有 shebang 的文件不应触发“未声明格式”警告。
    mode, warning = formats.resolve(fmt_name, path, has_body=formats.has_code(body))
    if warning and warn:
        interp.warn(warning)

    if mode == "slang":
        from . import slangfmt
        nodes = slangfmt.parse(body, path)
        return nodes, mode, fmt_name, "slang"

    tokens, backend = native.lex_with_fallback(body, mode, path)
    nodes = parse(tokens, mode, path)
    return nodes, mode, fmt_name, backend


def _load_stack(interp):
    stack = getattr(interp, "load_stack", None)
    if stack is None:
        stack = interp.load_stack = []
    return stack


def _trace(interp, event, **fields):
    fn = getattr(interp, "trace", None)
    if fn is not None:
        fn(event, **fields)


def _cache(interp):
    return getattr(interp, "module_cache", None)


def _is_builtin_name(path):
    return path in BUILTIN_MODULES


def load_file(interp, path, mode="override", target_env=None, base_dir=None, origin_node=None):
    """加载一个源文件。

    mode="override" 直接把定义写进 target_env（覆盖同名定义）；
    mode="import"   在隔离的模块环境里求值，并返回该模块环境
                    （同一真实路径只执行一次）。
    base_dir 是相对路径的解析基准（通常是发起 load 的语句所在目录）。
    """
    lock = getattr(interp, "load_lock", None)
    if lock is not None:
        with lock:
            return _load_file_locked(interp, path, mode, target_env, base_dir, origin_node)
    return _load_file_locked(interp, path, mode, target_env, base_dir, origin_node)


def _load_file_locked(interp, path, mode, target_env, base_dir, origin_node):
    if mode == "import" and _is_builtin_name(path):
        loader = getattr(interp, "load_builtin_module", None)
        if loader is None:
            raise LoadError("当前解释器不支持内置模块 %r" % path)
        _trace(interp, "import.builtin", name=path)
        return loader(path)

    full = resolve_path(interp, path, base_dir=base_dir)
    if not os.path.exists(full):
        origin = ""
        if origin_node is not None:
            ofile = getattr(origin_node, "file", None) or interp.current_file
            if ofile:
                origin = "（来自 %s 第 %s 行）" % (ofile, getattr(origin_node, "line", "?"))
        raise LoadError("找不到文件 %s（从 %r 解析）%s" % (full, path, origin))
    if not os.path.isfile(full):
        raise LoadError("不是普通文件: %s" % full)
    # 统一使用真实路径：符号链接文件里的相对 load 以真实目录为基准，
    # 错误信息/缓存键也不会因不同链接路径重复执行。
    real = os.path.realpath(full)
    env = target_env if target_env is not None else interp.root

    stack = _load_stack(interp)
    cache = _cache(interp)
    if mode == "import" and cache is not None and real in cache:
        return cache[real]
    if real in stack:
        chain = " -> ".join(os.path.basename(p) for p in stack + [real])
        raise LoadError("检测到循环加载: %s" % chain)

    old_file = interp.current_file
    old_dir = getattr(interp, "current_dir", None)
    stack.append(real)
    interp.current_file = real
    interp.current_dir = os.path.dirname(real)
    mod_name = os.path.splitext(os.path.basename(real))[0]
    _trace(interp, "load.start" if mode == "override" else "import.start",
           path=real, mode=mode, module=mod_name)
    old_bindings = dict(env.bindings) if mode == "override" else None
    try:
        nodes, _mode, _fmt, _backend = read_nodes(interp, real)
        if mode == "import":
            # 固定内置快照：每个模块一份 copy，避免模块用 set 改写共享快照。
            snapshot = Env("builtins:快照", kind="snapshot")
            snapshot.bindings.update(getattr(interp, "module_base", {}) or {})
            module = Env(mod_name, parent=snapshot, kind="module", is_module=True)
            for node in nodes:
                _trace(interp, "form", file=real,
                       line=getattr(node, "line", None), source=node)
                interp.evaluate(node, module)
            if cache is not None:
                cache[real] = module
            _trace(interp, "import.end", path=real, module=mod_name)
            return module
        for node in nodes:
            _trace(interp, "form", file=real,
                   line=getattr(node, "line", None), source=node)
            interp.evaluate(node, env)
        _trace(interp, "load.end", path=real, mode=mode)
        return None
    except Exception:
        if old_bindings is not None:
            # 覆盖模式失败时回滚目标环境，避免“文件报错但前半段定义已生效”。
            env.bindings.clear()
            env.bindings.update(old_bindings)
        raise
    finally:
        stack.pop()
        interp.current_file = old_file
        interp.current_dir = old_dir


def find_project_file(start=None):
    """从 start 开始向上查找 slang.toml（符号链接按真实路径处理）。"""
    current = os.path.abspath(start or os.getcwd())
    if os.path.isfile(current):
        current = os.path.dirname(os.path.realpath(current))
    elif not os.path.isdir(current):
        current = os.path.dirname(current)
    while True:
        candidate = os.path.join(current, "slang.toml")
        if os.path.exists(candidate):
            return candidate
        parent = os.path.dirname(current)
        if parent == current:
            return None
        current = parent


def _config_error(full, message):
    raise LoadError("%s: %s" % (full, message))


def _warn_unknown(interp, full, table, data, allowed, label=""):
    for key in data:
        if key not in allowed:
            hint = ""
            if label == "[builds]" and key == "file":
                hint = "（是否想写 files？）"
            interp.warn("警告: %s: %s 里未知键 %r 已忽略%s"
                        % (full, label or "配置", key, hint))


def parse_config(interp, full):
    """读取并严格校验 TOML 配置；返回 dict（不执行构建）。"""
    text = read_source(full)
    try:
        data = tomlcompat.loads(text)
    except Exception as exc:
        raise LoadError("解析配置失败 %s: %s" % (full, exc))
    if not isinstance(data, dict):
        raise LoadError("%s: 配置顶层必须是表（table）" % full)

    for key in data:
        if key not in CONFIG_TOP_KEYS:
            interp.warn("警告: %s: 未知顶层键 %r 已忽略" % (full, key))

    if "builds" in data and "build" in data:
        _config_error(full, "同时出现 [builds] 和旧版 [build]；请只保留 [builds]")
    builds = data["builds"] if "builds" in data else data.get("build", {})
    if not isinstance(builds, dict):
        _config_error(full, "[builds] 必须是表（table），得到 %s"
                      % type(builds).__name__)
    _warn_unknown(interp, full, "builds", builds, BUILDS_KEYS, "[builds]")
    files = builds.get("files", [])
    if not isinstance(files, list):
        _config_error(full, "builds.files 必须是字符串数组")
    for item in files:
        if not isinstance(item, str) or not item:
            _config_error(full, "builds.files 里只能放非空路径字符串")

    project = data.get("project", {})
    if not isinstance(project, dict):
        _config_error(full, "[project] 必须是表（table），得到 %s"
                      % type(project).__name__)
    _warn_unknown(interp, full, "project", project, PROJECT_KEYS, "[project]")
    for key in ("name", "version"):
        if key in project and not isinstance(project[key], str):
            _config_error(full, "project.%s 必须是字符串" % key)
    main = project.get("main")
    if main is not None and (not isinstance(main, str) or not main):
        _config_error(full, "project.main 必须是非空字符串")
    return data


def check_config(interp, path, base_dir=None):
    """check --project 使用：只解析配置与构建文件，不执行任何代码。"""
    full = resolve_path(interp, path, base_dir=base_dir)
    if not os.path.exists(full):
        raise LoadError("找不到配置文件 %s" % full)
    if not os.path.isfile(full):
        raise LoadError("不是普通文件: %s" % full)
    full = os.path.realpath(full)
    data = parse_config(interp, full)
    base = os.path.dirname(full)
    builds = data.get("builds", data.get("build", {})) or {}
    for rel in builds.get("files", []):
        build_path = resolve_path(interp, rel, base_dir=base)
        if not os.path.exists(build_path):
            raise LoadError("找不到构建文件 %s（配置 %s）" % (build_path, full))
        read_nodes(interp, os.path.realpath(build_path))
    main = project_main(data)
    if main:
        main_path = resolve_path(interp, main, base_dir=base)
        if not os.path.exists(main_path):
            raise LoadError("找不到 project.main 文件 %s（配置 %s）" % (main_path, full))
    return data


def load_config(interp, path, target_env=None, base_dir=None):
    """读取 TOML 配置，按顺序加载 builds.files。

    配置里的路径以配置文件所在目录为基准；函数结束时恢复
    interp.project_root（CLI 的 load_project 会显式设置项目根）。
    """
    full = resolve_path(interp, path, base_dir=base_dir)
    if not os.path.exists(full):
        raise LoadError("找不到配置文件 %s" % full)
    if not os.path.isfile(full):
        raise LoadError("不是普通文件: %s" % full)
    full = os.path.realpath(full)
    data = parse_config(interp, full)
    env = target_env if target_env is not None else interp.root
    files = data.get("builds", data.get("build", {})).get("files", []) \
        if isinstance(data.get("builds", data.get("build", {})), dict) else []

    base = os.path.dirname(full)
    old_root = interp.project_root
    old_dir = getattr(interp, "current_dir", None)
    interp.project_root = base
    interp.current_dir = base
    try:
        for rel in files:
            load_file(interp, rel, mode="override", target_env=env, base_dir=base)
            if env is interp.root:
                # 让“启动配置阶段”后续 import 的模块看到已加载的构建定义。
                interp.freeze_module_base()
        if env is interp.root:
            interp.freeze_module_base()
    finally:
        interp.current_dir = old_dir
        interp.project_root = old_root
    _trace(interp, "config.load", path=full, files=files)
    return data


def load_builtin_runtime(interp):
    """没有项目配置时加载随包发布的内置 base/std。

    这不等于自动加载 log 模块：内置模块仍只由显式 (import "log") 触发。
    """
    base = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stdlib", "builds")
    files = [os.path.join(base, "base.lang.build"), os.path.join(base, "std.lang.build")]
    missing = [p for p in files if not os.path.exists(p)]
    if missing:
        raise LoadError("内置标准库缺失: %s" % ", ".join(missing))
    for path in files:
        load_file(interp, path, mode="override", target_env=interp.root,
                  base_dir=base)
    interp.freeze_module_base()
    _trace(interp, "runtime.builtin", files=files)
    return files


def project_main(data):
    project = data.get("project", {}) or {}
    return project.get("main")
