"""C++ 核心（可选）的 ctypes 桥。

core/ 目录下编译出 libslangcore.so 后自动启用，用于加速词法分析；
找不到、ABI 不匹配、冒烟测试失败或加载失败时，回退到纯 Python 词法器。

注意：Android 上 /sdcard 不允许 dlopen，因此加载失败时会把 .so 复制到
Termux 内部目录（$TMPDIR）再加载。缓存文件名包含源路径与文件内容哈希，
写入使用同目录临时文件 + 原子 rename；并发加载用进程内锁串行化。
"""
import ctypes
import hashlib
import json
import os
import shutil
import tempfile
import threading

# 必须与 core/slang_core.cpp 的 slang_abi_version() 一致。
ABI_VERSION = 2

_STATE = None  # None=未尝试, False=不可用, (lib, path)=可用
_REASON = None  # 不可用原因（给 version 命令展示）
_LOCK = threading.RLock()


def _candidate_paths():
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(here)
    paths = []
    env = os.environ.get("SLANG_CORE")
    if env:
        # 显式指定时只尝试该路径：避免用户以为在用测试库，实际却
        # 静默回退到项目里的另一个核心。
        return [env]
    for d in (os.path.join(root, "core", "build"), os.path.join(root, "core"), root):
        for name in ("libslangcore.so", "libslangcore.dylib", "slangcore.dll"):
            paths.append(os.path.join(d, name))
    return paths


def _runtime_dir():
    for d in (
        os.environ.get("TMPDIR"),
        "/data/data/com.termux/files/usr/tmp",
        tempfile.gettempdir(),
    ):
        if d and os.path.isdir(d) and os.access(d, os.W_OK):
            return d
    return None


def _file_digest(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()[:20]


def _cleanup_cache(target_dir, keep=16):
    """清理旧缓存，避免长期使用后 $TMPDIR 堆积大量 .so。"""
    try:
        names = []
        for name in os.listdir(target_dir):
            if name.startswith("libslangcore-") and name.endswith(".so"):
                path = os.path.join(target_dir, name)
                try:
                    names.append((os.stat(path).st_mtime_ns, path))
                except OSError:
                    pass
        names.sort(reverse=True)
        for _mtime, path in names[keep:]:
            try:
                os.remove(path)
            except OSError:
                pass
    except OSError:
        pass


def _copy_to_runtime_dir(path):
    """把 .so 复制到可 dlopen 的目录；内容哈希命名 + 原子写入。"""
    target_dir = _runtime_dir()
    if not target_dir:
        return None
    try:
        digest = _file_digest(path)
        path_tag = hashlib.sha256(os.path.realpath(path).encode("utf-8")).hexdigest()[:10]
        dest = os.path.join(target_dir, "libslangcore-%s-%s.so" % (path_tag, digest))
        if not os.path.exists(dest):
            fd, tmp = tempfile.mkstemp(prefix=".slangcore-", suffix=".tmp",
                                       dir=target_dir)
            try:
                with os.fdopen(fd, "wb") as out, open(path, "rb") as src:
                    shutil.copyfileobj(src, out, 1024 * 1024)
                    out.flush()
                    os.fsync(out.fileno())
                os.chmod(tmp, 0o755)
                os.replace(tmp, dest)
            finally:
                if os.path.exists(tmp):
                    try:
                        os.remove(tmp)
                    except OSError:
                        pass
        _cleanup_cache(target_dir)
        return dest
    except OSError:
        return None


def _open_library(path):
    try:
        return ctypes.CDLL(path), path
    except OSError:
        pass
    cached = _copy_to_runtime_dir(path)
    if not cached:
        return None
    for attempt in range(2):
        try:
            return ctypes.CDLL(cached), cached
        except OSError:
            if attempt == 0:
                # 缓存可能损坏，删除后重新复制一次
                try:
                    os.remove(cached)
                except OSError:
                    pass
                cached = _copy_to_runtime_dir(path)
                if not cached:
                    return None
            else:
                return None


def _configure(lib):
    """绑定符号并校验 ABI；成功返回 True。"""
    try:
        lib.slang_abi_version.argtypes = []
        lib.slang_abi_version.restype = ctypes.c_int
        abi = lib.slang_abi_version()
    except AttributeError:
        global _REASON
        _REASON = "库缺少 slang_abi_version（需要重新编译 core）"
        return False
    if abi != ABI_VERSION:
        _REASON = "ABI 不匹配：Python 需要 %d，库为 %d（请重新编译 core）" % (
            ABI_VERSION,
            abi,
        )
        return False
    try:
        lib.slang_lex.argtypes = [
            ctypes.c_char_p,
            ctypes.c_size_t,
            ctypes.c_int,
            ctypes.POINTER(ctypes.c_size_t),
        ]
        lib.slang_lex.restype = ctypes.c_void_p
        lib.slang_free.argtypes = [ctypes.c_void_p]
        lib.slang_free.restype = None
        lib.slang_version.restype = ctypes.c_char_p
        lib.slang_last_error.restype = ctypes.c_char_p
    except AttributeError as exc:
        _REASON = "库缺少符号 %s" % exc
        return False
    return True


def _raw_lex(lib, source, mode):
    try:
        data = source.encode("utf-8")
    except UnicodeEncodeError:
        return None
    out_len = ctypes.c_size_t(0)
    try:
        ptr = lib.slang_lex(data, len(data), 0 if mode == "sexpr" else 1,
                            ctypes.byref(out_len))
        if not ptr:
            return None
        try:
            payload = ctypes.string_at(ptr, out_len.value)
        finally:
            lib.slang_free(ptr)
        return json.loads(payload.decode("utf-8"))
    except (ValueError, UnicodeDecodeError, TypeError, OSError, ctypes.ArgumentError):
        return None


def _smoke(lib):
    """拒绝“符号齐全但行为为空/崩溃”的假核心。"""
    toks = _raw_lex(lib, "(x)", "sexpr")
    if toks is None:
        _REASON = "C++ 核心冒烟测试失败（slang_lex 返回空或非法 JSON）"
        return False
    try:
        kinds = [t[0] for t in toks]
    except (TypeError, IndexError):
        _REASON = "C++ 核心冒烟测试失败（token 结构异常）"
        return False
    if kinds != ["LP", "SYM", "RP"]:
        _REASON = "C++ 核心冒烟测试失败（token 序列异常）"
        return False
    return True


def load():
    global _STATE, _REASON
    with _LOCK:
        if _STATE is not None:
            return _STATE
        found = False
        for path in _candidate_paths():
            if not path or not os.path.isfile(path):
                continue
            found = True
            opened = _open_library(path)
            if not opened:
                _REASON = _REASON or "无法加载 %s" % path
                continue
            lib, loaded_path = opened
            if not _configure(lib):
                _REASON = _REASON or "无法配置 %s" % loaded_path
                continue
            if not _smoke(lib):
                _REASON = _REASON or "冒烟测试失败 %s" % loaded_path
                continue
            _STATE = (lib, loaded_path)
            return _STATE
        if not found:
            _REASON = "未找到 libslangcore.so（可在 core/ 执行 make）"
        _STATE = False
        return False


def reason():
    """返回 C++ 核心不可用的原因（可用时返回 None）。"""
    load()
    return _REASON if _STATE is False else None


def available():
    return load() is not False


def disable():
    """禁用 C++ 核心（测试/排错用）。"""
    global _STATE
    with _LOCK:
        _STATE = False


def version():
    state = load()
    if not state:
        return None
    lib = state[0]
    v = lib.slang_version()
    return v.decode("utf-8") if v else None


def lex(source, mode="sexpr"):
    """用 C++ 核心分词；不可用或结果异常时返回 None。"""
    state = load()
    if not state:
        return None
    toks = _raw_lex(state[0], source, mode)
    if toks is None:
        return None
    try:
        return [tuple(t) for t in toks]
    except TypeError:
        return None


def _too_dense_for_native(source):
    """大源码下 native 的 JSON + ctypes 峰值内存远高于 Python。

    纯 Python 逐字符扫描虽然不调用 C++，但内存占用低。这里用分隔符密度
    估算 token 数：token 密集的大文件直接走 Python；长字符串/长注释仍用
    native（那正是 native 最擅长、也最省内存的场景）。
    """
    n = len(source)
    if n <= 4 * 1024 * 1024:
        return False
    if n > 32 * 1024 * 1024:
        return True
    separators = " \t\n()[]{};,\""
    count = sum(source.count(ch) for ch in separators)
    return count > n // 16


def lex_with_fallback(source, mode="sexpr", filename=None):
    """优先 C++，失败/大 token 密集文件则纯 Python。"""
    from . import lexer as pylexer

    if not _too_dense_for_native(source):
        toks = lex(source, mode)
        if toks is not None:
            return toks, "cpp"
    return pylexer.lex(source, mode, filename), "python"
