"""内置 ffi 模块：显式 (import "ffi") 后才可用，不会自动加载。

两种导入模式
============

1. 直接导入（使用随包发布的默认兼容配置 ``ffi_default.toml``）

       (def math (ffi.python "math"))     ; Python 模块：自动发现公开成员
       (def libm (ffi.c "libm.so"))       ; C 动态库：使用默认签名表（libm/libc）

2. 配置文件导入（用 TOML 指定如何转译）

       (def z (ffi.import "zlib.ffi.toml"))

   规格文件示例（Python）::

       kind = "python"
       module = "math"
       exports = ["sqrt", "pi"]      # 只导出这些名字；不写则全部公开成员

       [rename]
       "根号" = "sqrt"

   规格文件示例（C）::

       kind = "c"
       library = "libm.so"
       uses-default = true           # 合并默认配置里 libm 的签名

       [structs.Point]
       fields = [["x", "double"], ["y", "double"]]

       [functions.sqrt]
       restype = "double"
       args = ["double"]

常用接口
========

    (ffi.python 模块名 [配置文件])     导入 Python 模块
    (ffi.c 库名/路径 [配置文件])       导入 C 动态库
    (ffi.import 规格文件)              按配置文件导入（kind = "python" / "c"）
    (ffi.close 句柄)                   关闭句柄（C 库只是解除绑定）
    (ffi.members 句柄)                 列出可用成员
    (ffi.bind 句柄 "符号" "返回" [参数...] ["成员名"])  给 C 句柄补充绑定
    (ffi.call 句柄 "名字" 参数...)     按字符串名字调用
    (ffi.null)                         表示 Python None / C NULL
    (ffi.address 指针)                 取指针地址（整数）
    (ffi.at 指针 下标 ["类型"])        读指针/数组元素
    (ffi.set-field 对象 "字段" 值)     写 C 结构体字段
    (ffi.version)                      返回 slang 版本

值转换
======

* Python -> slang：None→[]、bool/int/float/str 原样、bytes→str、
  list/tuple/set→list、dict→dict、其他对象→不透明句柄（可继续访问属性）。
* slang -> Python：[]→[]、数字/字符串原样、list/dict 递归转换、
  ForeignObject→底层对象、Prim/UserFn→Python 回调。
* C 类型：见 ``ffi_default.toml`` 的 ``[c]`` 与 ``[libs.*]``。

安全提示：导入外部库等于获得该库的全部能力。slang 不做沙箱；只导入你信任的库。
"""
import ctypes
import ctypes.util
import importlib
import importlib.util
import os

from ..env import Env
from ..errors import EvalError
from .. import tomlcompat

DEFAULT_CONFIG_FILE = "ffi_default.toml"


# --------------------------------------------------------------------------
# 默认兼容配置
# --------------------------------------------------------------------------
def default_config_path():
    return os.path.join(
        os.path.dirname(os.path.abspath(__file__)), DEFAULT_CONFIG_FILE
    )


def _read_toml(path):
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            text = fh.read()
    except OSError as exc:
        raise EvalError("无法读取 ffi 配置 %s: %s" % (path, exc))
    try:
        data = tomlcompat.loads(text)
    except Exception as exc:
        raise EvalError("解析 ffi 配置失败 %s: %s" % (path, exc))
    if not isinstance(data, dict):
        raise EvalError("ffi 配置顶层必须是表: %s" % path)
    return data


def _as_dict(value, what):
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise EvalError("%s 必须是表（table）" % what)
    return value


def _as_list(value, what):
    if value is None:
        return []
    if not isinstance(value, list):
        raise EvalError("%s 必须是数组" % what)
    return value


def _as_bool(value, what, default=False):
    if value is None:
        return default
    if not isinstance(value, bool):
        raise EvalError("%s 必须是布尔值" % what)
    return value


def _as_int(value, what, default):
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        raise EvalError("%s 必须是整数" % what)
    return value


# --------------------------------------------------------------------------
# 值转换
# --------------------------------------------------------------------------
class _Null:
    """Python None / C NULL 的显式表示。"""

    slang_type = "nil"

    def __repr__(self):
        return "<null>"


_NULL = _Null()


class ForeignObject:
    """不透明外部对象（Python 对象、C 结构体、指针等）。

    成员访问通过 resolver 回调完成；没有 resolver 时不可访问成员。
    """

    def __init__(self, obj, label, slang_type="foreign", resolver=None):
        self.obj = obj
        self.label = label
        self.slang_type = slang_type
        self.resolver = resolver

    def __repr__(self):
        return "<%s>" % self.label

    def get_member(self, name):
        if self.resolver is None:
            return False, None
        return self.resolver(self, name)


class _Converter:
    """Python 值与 slang 值之间的转换（带深度/条目上限）。"""

    def __init__(self, interp, encoding="utf-8", max_items=1000, max_depth=4):
        self.interp = interp
        self.encoding = encoding
        self.max_items = max_items
        self.max_depth = max_depth

    # ---- Python -> slang ----
    def to_slang(self, value, depth=0, node=None, label=None):
        if value is None:
            return []
        if isinstance(value, bool):
            return value
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return value
        if isinstance(value, str):
            return value
        if isinstance(value, bytes):
            return value.decode(self.encoding, "replace")
        if isinstance(value, bytearray):
            return bytes(value).decode(self.encoding, "replace")
        if depth >= self.max_depth:
            return ForeignObject(value, label or "python 容器（嵌套过深）")
        if isinstance(value, (list, tuple)):
            items = list(value[: self.max_items])
            out = [self.to_slang(v, depth + 1, node) for v in items]
            if len(value) > self.max_items:
                out.append("…（共 %d 项）" % len(value))
            return out
        if isinstance(value, (set, frozenset)):
            items = list(value)[: self.max_items]
            out = [self.to_slang(v, depth + 1, node) for v in items]
            if len(value) > self.max_items:
                out.append("…（共 %d 项）" % len(value))
            return out
        if isinstance(value, dict):
            out = {}
            for index, (key, item) in enumerate(value.items()):
                if index >= self.max_items:
                    out["…"] = "（共 %d 项）" % len(value)
                    break
                out[self.to_slang(key, depth + 1, node)] = self.to_slang(
                    item, depth + 1, node
                )
            return out
        if isinstance(value, complex):
            return _py_foreign(self.interp, self, value, "python complex")
        return _py_foreign(self.interp, self, value,
                           label or ("python %s" % type(value).__name__))

    # ---- slang -> Python ----
    def to_py(self, value, node=None):
        return self._to_py(value, node, 0)

    def _to_py(self, value, node, depth):
        if isinstance(value, _Null):
            return None
        if isinstance(value, ForeignObject):
            return value.obj
        if value is None:
            return None
        if isinstance(value, (bool, int, float, str, bytes)):
            return value
        if depth >= self.max_depth:
            self.interp.error("传给外部库的值嵌套太深（超过 %d 层）"
                              % self.max_depth, node)
        if isinstance(value, list):
            return [self._to_py(v, node, depth + 1)
                    for v in value[: self.max_items]]
        if isinstance(value, dict):
            out = {}
            for key, item in list(value.items())[: self.max_items]:
                out[self._to_py(key, node, depth + 1)] = self._to_py(
                    item, node, depth + 1
                )
            return out
        if isinstance(value, Env):
            self.interp.error("不能把模块 %r 直接传给外部库" % value.name, node)
        from ..interp import Prim, UserFn
        if isinstance(value, (Prim, UserFn)):
            return self._callback(value, node)
        self.interp.error("不能把 %r 传给外部库" % (value,), node)

    def _callback(self, fn, node):
        """把 slang 函数包成 Python 回调（例如 functools.reduce 的 f）。"""
        from ..interp import Prim
        interp = self.interp

        def py_fn(*py_args):
            slang_args = [self.to_slang(a) for a in py_args]
            if isinstance(fn, Prim):
                if fn.lazy:
                    interp.error("宏式函数不能作为 Python 回调", node)
                result = fn.fn(interp, slang_args, None, node)
            else:
                result = interp.call_eager(fn, slang_args, None, node)
            return self.to_py(result, node)

        py_fn.__name__ = getattr(fn, "name", "slang_callback") or "slang_callback"
        return py_fn


# --------------------------------------------------------------------------
# C 类型
# --------------------------------------------------------------------------
class CType:
    __slots__ = ("ctype", "kind", "name", "elem")

    def __init__(self, ctype, kind, name="", elem=None):
        self.ctype = ctype
        self.kind = kind
        self.name = name
        self.elem = elem

    def __repr__(self):
        return "<CType %s %s>" % (self.name or self.kind, self.ctype)


_SCALARS = {
    "bool": (ctypes.c_bool, "bool"),
    "char": (ctypes.c_char, "char"),
    "schar": (ctypes.c_byte, "int"),
    "uchar": (ctypes.c_ubyte, "int"),
    "byte": (ctypes.c_byte, "int"),
    "ubyte": (ctypes.c_ubyte, "int"),
    "int8": (ctypes.c_int8, "int"),
    "uint8": (ctypes.c_uint8, "int"),
    "i8": (ctypes.c_int8, "int"),
    "u8": (ctypes.c_uint8, "int"),
    "int16": (ctypes.c_int16, "int"),
    "uint16": (ctypes.c_uint16, "int"),
    "i16": (ctypes.c_int16, "int"),
    "u16": (ctypes.c_uint16, "int"),
    "short": (ctypes.c_short, "int"),
    "ushort": (ctypes.c_ushort, "int"),
    "int32": (ctypes.c_int32, "int"),
    "uint32": (ctypes.c_uint32, "int"),
    "i32": (ctypes.c_int32, "int"),
    "u32": (ctypes.c_uint32, "int"),
    "int": (ctypes.c_int, "int"),
    "uint": (ctypes.c_uint, "uint"),
    "long": (ctypes.c_long, "int"),
    "ulong": (ctypes.c_ulong, "uint"),
    "longlong": (ctypes.c_longlong, "int"),
    "ulonglong": (ctypes.c_ulonglong, "uint"),
    "int64": (ctypes.c_int64, "int"),
    "uint64": (ctypes.c_uint64, "int"),
    "i64": (ctypes.c_int64, "int"),
    "u64": (ctypes.c_uint64, "int"),
    "size_t": (ctypes.c_size_t, "uint"),
    "ssize_t": (ctypes.c_ssize_t, "int"),
    "float": (ctypes.c_float, "float"),
    "double": (ctypes.c_double, "float"),
    "cstring": (ctypes.c_char_p, "string"),
    "string": (ctypes.c_char_p, "string"),
    "str": (ctypes.c_char_p, "string"),
    "char*": (ctypes.c_char_p, "string"),
    "void*": (ctypes.c_void_p, "pointer"),
    "ptr": (ctypes.c_void_p, "pointer"),
    "pointer": (ctypes.c_void_p, "pointer"),
}

_VOID = CType(None, "void", "void")


class CContext:
    """一个 C 句柄的类型/结构体上下文。"""

    def __init__(self, structs=None, types=None):
        # structs: name -> ctypes.Structure 子类
        self.structs = dict(structs or {})
        # types: name -> C 类型描述字符串（用户别名）
        self.types = dict(types or {})

    def struct(self, name):
        cls = self.structs.get(name)
        if cls is None:
            raise EvalError("未定义的结构体 %r（先在 [structs.%s] 里声明）"
                            % (name, name))
        return cls


def parse_ctype(spec, ctx):
    """把配置里的类型字符串解析成 CType。"""
    if not isinstance(spec, str) or not spec.strip():
        raise EvalError("C 类型必须是非空字符串，得到 %r" % (spec,))
    spec = spec.strip()
    if spec == "void":
        return _VOID
    if spec.startswith("struct:"):
        name = spec[len("struct:"):]
        return CType(ctx.struct(name), "struct", name)
    if spec in ctx.structs:
        return CType(ctx.struct(spec), "struct", spec)
    if spec in ctx.types:
        return parse_ctype(ctx.types[spec], ctx)
    if spec.endswith("*"):
        base_spec = spec[:-1].strip()
        if base_spec in ("char", "cchar"):
            return CType(ctypes.c_char_p, "string", spec)
        if base_spec == "void":
            return CType(ctypes.c_void_p, "pointer", spec)
        base = parse_ctype(base_spec, ctx)
        pointer = ctypes.POINTER(base.ctype)
        return CType(pointer, "array" if base.kind != "struct" else "pointer",
                     spec, elem=base)
    if spec in _SCALARS:
        ctype, kind = _SCALARS[spec]
        return CType(ctype, kind, spec)
    raise EvalError("未知的 C 类型 %r（可用：%s，或自定义 [types]）"
                    % (spec, ", ".join(sorted(_SCALARS))))


def desc_from_ctype(ctype, name=""):
    """从 ctypes 类型反推 CType（用于结构体字段/指针元素）。"""
    if ctype is None:
        return _VOID
    if not isinstance(ctype, type):
        # c_int._type_ 之类是字符串，不是类型
        return CType(ctypes.c_void_p, "pointer", name or "pointer")
    if ctype is ctypes.c_char_p:
        return CType(ctype, "string", name or "char*")
    if issubclass(ctype, ctypes.Structure):
        return CType(ctype, "struct", name or ctype.__name__)
    pointer_cls = getattr(ctypes, "_Pointer", None)
    if pointer_cls is not None and issubclass(ctype, pointer_cls):
        elem = desc_from_ctype(ctype._type_)
        kind = "pointer" if elem.kind == "struct" else "array"
        return CType(ctype, kind, name or "pointer", elem=elem)
    if ctype is ctypes.c_void_p:
        return CType(ctype, "pointer", name or "void*")
    if ctype is ctypes.c_bool:
        return CType(ctype, "bool", name or "bool")
    if ctype is ctypes.c_char:
        return CType(ctype, "char", name or "char")
    if ctype in (ctypes.c_float, ctypes.c_double):
        return CType(ctype, "float", name or "float")
    return CType(ctype, "int", name or "int")


def _build_structs(raw_structs, ctx):
    pending = _as_dict(raw_structs, "[structs]")
    # 按声明顺序构建；字段可以引用前面已声明的结构体或 [types] 别名。
    for name, body in pending.items():
        if not isinstance(name, str) or not name:
            raise EvalError("结构体名必须是非空字符串")
        body = _as_dict(body, "[structs.%s]" % name)
        fields = _as_list(body.get("fields"), "[structs.%s].fields" % name)
        if not fields:
            raise EvalError("结构体 %s 至少需要一个字段" % name)
        converted = []
        for field in fields:
            if (not isinstance(field, list) or len(field) != 2
                    or not isinstance(field[0], str) or not field[0]):
                raise EvalError("结构体 %s 的字段必须是 [\"名字\", \"类型\"]" % name)
            converted.append((field[0], parse_ctype(field[1], ctx).ctype))
        cls = type(name, (ctypes.Structure,), {"_fields_": converted})
        ctx.structs[name] = cls


# --------------------------------------------------------------------------
# C 参数 / 返回值转换
# --------------------------------------------------------------------------
def _c_arg(value, desc, conv, interp, node, keep):
    kind = desc.kind
    if kind == "void":
        interp.error("void 不能作为参数类型", node)
    if kind == "string":
        if isinstance(value, _Null) or value is None:
            return None
        if isinstance(value, ForeignObject):
            value = value.obj
        if isinstance(value, str):
            return value.encode(conv.encoding)
        if isinstance(value, (bytes, bytearray)):
            return bytes(value)
        interp.error("参数需要字符串，得到 %s" % _describe(value), node)
    if kind == "char":
        if isinstance(value, str) and len(value) == 1:
            return value.encode(conv.encoding)
        if isinstance(value, (bytes, bytearray)) and len(value) == 1:
            return bytes(value)
        if isinstance(value, int) and not isinstance(value, bool):
            return bytes([value & 0xFF])
        interp.error("参数需要单个字符，得到 %s" % _describe(value), node)
    if kind == "bool":
        if isinstance(value, bool):
            return value
        if isinstance(value, int):
            return bool(value)
        interp.error("参数需要布尔值，得到 %s" % _describe(value), node)
    if kind in ("int", "uint"):
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, int):
            return value
        if isinstance(value, float) and value.is_integer():
            return int(value)
        interp.error("参数需要整数，得到 %s" % _describe(value), node)
    if kind == "float":
        if isinstance(value, bool):
            return float(value)
        if isinstance(value, (int, float)):
            return float(value)
        interp.error("参数需要数字，得到 %s" % _describe(value), node)
    if kind == "struct":
        if isinstance(value, ForeignObject):
            obj = value.obj
            if isinstance(obj, desc.ctype):
                return obj
            if isinstance(obj, ctypes.Structure):
                return desc.ctype.from_buffer_copy(obj)
            interp.error("结构体 %s 参数类型不匹配：得到 %s"
                         % (desc.name, _describe(value)), node)
        if isinstance(value, dict):
            obj = desc.ctype()
            for field_name, field_ctype in desc.ctype._fields_:
                if field_name not in value:
                    interp.error("结构体 %s 缺少字段 %r" % (desc.name, field_name), node)
                setattr(obj, field_name,
                        _c_arg(value[field_name], desc_from_ctype(field_ctype),
                               conv, interp, node, keep))
            return obj
        interp.error("结构体 %s 参数需要对象或字典，得到 %s"
                     % (desc.name, _describe(value)), node)
    if kind == "array":
        if isinstance(value, ForeignObject):
            return _cast_pointer(value.obj, desc, interp, node)
        if isinstance(value, _Null) or value is None:
            return None
        if isinstance(value, (bytes, bytearray)):
            return _cast_pointer(value, desc, interp, node)
        if isinstance(value, str):
            return ctypes.c_char_p(value.encode(conv.encoding))
        if isinstance(value, list):
            elem = desc.elem
            converted = []
            for item in value:
                converted.append(_c_arg(item, elem, conv, interp, node, keep))
            array = (elem.ctype * len(converted))(*converted)
            keep.append(array)
            return array
        interp.error("参数 %s 需要列表（数组）或指针，得到 %s"
                     % (desc.name, _describe(value)), node)
    if kind == "pointer":
        if isinstance(value, _Null) or value is None:
            return None
        if isinstance(value, ForeignObject):
            return _cast_pointer(value.obj, desc, interp, node)
        if isinstance(value, (bytes, bytearray)):
            return _cast_pointer(value, desc, interp, node)
        if isinstance(value, str):
            return ctypes.c_char_p(value.encode(conv.encoding))
        if isinstance(value, bool):
            interp.error("指针参数不接受布尔值", node)
        if isinstance(value, int):
            return _cast_pointer(value, desc, interp, node)
        interp.error("指针参数需要整数地址/指针对象，得到 %s"
                     % _describe(value), node)
    interp.error("不支持的 C 参数类型 %r" % (desc,), node)


def _cast_pointer(value, desc, interp, node):
    """把整数地址或 ctypes 指针统一成参数需要的指针类型。"""
    if isinstance(value, int) and not isinstance(value, bool):
        if desc.ctype is ctypes.c_void_p:
            return ctypes.c_void_p(value)
        return ctypes.cast(ctypes.c_void_p(value), desc.ctype)
    if isinstance(value, (ctypes.Structure, ctypes.Array)):
        pointer = ctypes.pointer(value)
        if desc.ctype is ctypes.c_void_p:
            return ctypes.cast(pointer, ctypes.c_void_p)
        return ctypes.cast(pointer, desc.ctype)
    if isinstance(value, (bytes, bytearray)):
        return ctypes.c_char_p(bytes(value))
    if desc.ctype is ctypes.c_void_p:
        try:
            return ctypes.cast(value, ctypes.c_void_p)
        except (TypeError, ValueError):
            return value
    return value


def _vararg_arg(value, conv, interp, node):
    if isinstance(value, _Null) or value is None:
        return None
    if isinstance(value, ForeignObject):
        return value.obj
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        # printf("%s") 这类可变参数通常需要 bytes
        return value.encode(conv.encoding)
    if isinstance(value, (bytes, bytearray)):
        return bytes(value)
    interp.error("可变参数只支持数字/字符串/指针，得到 %s" % _describe(value), node)


def _c_ret(value, desc, conv, interp, node):
    kind = desc.kind
    if kind == "void":
        return []
    if value is None:
        return []
    if kind == "string":
        if isinstance(value, (bytes, bytearray)):
            return bytes(value).decode(conv.encoding, "replace")
        return value
    if kind == "char":
        if isinstance(value, (bytes, bytearray)):
            return bytes(value).decode(conv.encoding, "replace")
        return value
    if kind == "bool":
        return bool(value)
    if kind in ("int", "uint"):
        return int(value)
    if kind == "float":
        return float(value)
    if kind == "struct":
        return _struct_foreign(value, "c %s" % (desc.name or "struct"),
                               conv, interp)
    if kind == "pointer":
        if not value:
            return []
        if isinstance(value, int):
            return value  # ctypes 的 c_void_p restype 本身就是整数地址
        return ForeignObject(value, "c %s" % (desc.name or "pointer"),
                             slang_type="pointer")
    if kind == "array":
        if not value:
            return []
        if isinstance(value, int):
            return value
        return ForeignObject(value, "c %s" % (desc.name or "pointer"),
                             slang_type="pointer")
    return value


def _describe(value):
    if isinstance(value, ForeignObject):
        return value.label
    try:
        from ..interp import display
        return display(value)
    except Exception:
        return repr(value)


def _call_py(interp, conv, display_name, fn, args, node):
    py_args = [conv.to_py(a, node) for a in args]
    interp.trace("ffi.call", lib=display_name, args=args)
    try:
        result = fn(*py_args)
    except EvalError:
        raise
    except Exception as exc:
        interp.error("Python 调用 %s 失败: %s: %s"
                     % (display_name, type(exc).__name__, exc), node)
    return conv.to_slang(result)


def _py_callable(interp, conv, obj, display_name):
    """把 Python 可调用对象包成既能调用、又能访问属性的值。"""
    foreign = _py_foreign(interp, conv, obj, display_name)
    foreign.slang_type = "fn"

    def slang_call(i, args, env, node):
        return _call_py(i, conv, display_name, obj, args, node)

    foreign.slang_call = slang_call
    return foreign


def _py_foreign(interp, conv, obj, label):
    """包装一个 Python 对象，并允许用 m.attr 继续访问属性。"""
    def resolver(foreign, name):
        if not isinstance(name, str) or not name or name.startswith("_"):
            return False, None
        try:
            attr = getattr(foreign.obj, name)
        except AttributeError:
            return False, None
        except Exception:
            return False, None
        child = "%s.%s" % (label, name)
        if callable(attr):
            return True, _py_callable(interp, conv, attr, child)
        return True, conv.to_slang(attr)

    return ForeignObject(obj, label, resolver=resolver)


def _struct_target(obj):
    """结构体指针取 contents；普通结构体原样返回。"""
    pointer_cls = getattr(ctypes, "_Pointer", None)
    if pointer_cls is not None and isinstance(obj, pointer_cls):
        try:
            return obj.contents
        except ValueError:
            return None
    return obj


def _struct_foreign(value, label, conv, interp):
    """包装 C 结构体（或结构体指针），支持 field 成员访问。"""
    def resolver(foreign, name):
        target = _struct_target(foreign.obj)
        if isinstance(target, ctypes.Structure):
            for field_name, field_ctype in target._fields_:
                if field_name == name:
                    return True, _c_ret(getattr(target, field_name),
                                        desc_from_ctype(field_ctype),
                                        conv, interp, None)
        return False, None

    return ForeignObject(value, label, slang_type="struct", resolver=resolver)


# --------------------------------------------------------------------------
# 句柄
# --------------------------------------------------------------------------
class _Handle:
    kind = "?"
    mode = "direct"

    def __init__(self, name, env):
        self.name = name
        self.env = env
        self.closed = False

    def ensure_open(self, interp, node):
        if self.closed:
            interp.error("ffi 句柄 %r 已关闭" % self.name, node)

    def close(self):
        self.closed = True

    def describe(self):
        return "%s:%s" % (self.kind, self.name)


class _PyHandle(_Handle):
    kind = "python"

    def __init__(self, name, env, module, conv, exports=None, renames=None,
                 include_private=False, deny=None):
        super().__init__(name, env)
        self.module = module
        self.conv = conv
        # exports 里的名字是 Python 名；[rename] 的键是额外暴露的 slang 别名。
        self.exports = set(exports) if exports is not None else None
        self.renames = dict(renames or {})
        self.include_private = bool(include_private)
        self.deny = set(deny or ())
        self.allowed = None
        if self.exports is not None:
            self.allowed = set(self.exports) | set(self.renames)
        self._cache = {}

    def resolve(self, interp, name):
        self.ensure_open(interp, None)
        if name in self._cache:
            return True, self._cache[name]
        if name in self.deny or name.startswith("__"):
            return False, None
        if self.allowed is not None and name not in self.allowed:
            return False, None
        if (name.startswith("_") and self.allowed is None
                and not self.include_private):
            return False, None
        py_name = self.renames.get(name, name)
        try:
            attr = getattr(self.module, py_name)
        except AttributeError:
            return False, None
        except Exception as exc:  # 模块 __getattr__ 可能抛任意异常
            raise EvalError("读取 Python 成员 %s.%s 失败: %s: %s"
                            % (self.name, py_name, type(exc).__name__, exc))
        value = self._wrap(interp, attr, name)
        if self.allowed is not None:
            self._cache[name] = value
        return True, value

    def _wrap(self, interp, attr, name):
        if callable(attr):
            return _py_callable(interp, self.conv, attr,
                                "%s.%s" % (self.describe(), name))
        return self.conv.to_slang(attr)

    def member_names(self):
        if self.allowed is not None:
            return sorted(self.allowed)
        names = []
        for name in dir(self.module):
            if name in self.deny or name.startswith("__"):
                continue
            if name.startswith("_") and not self.include_private:
                continue
            names.append(name)
        return sorted(names)


class _CHandle(_Handle):
    kind = "c"

    def __init__(self, name, env, lib, conv, functions, structs):
        super().__init__(name, env)
        self.lib = lib
        self.conv = conv
        self.functions = functions  # slang 名 -> spec dict
        self.structs = structs      # name -> ctypes.Structure
        self.ctx = None             # import_c 里补上 CContext
        self._cache = {}

    def resolve(self, interp, name):
        self.ensure_open(interp, None)
        if name in self._cache:
            return True, self._cache[name]
        if name in self.structs:
            value = self._make_struct_ctor(interp, name, self.structs[name])
        elif name in self.functions:
            value = self._make_prim(interp, name)
        else:
            return False, None
        self._cache[name] = value
        return True, value

    def _make_struct_ctor(self, interp, name, cls):
        from ..interp import Prim
        field_names = [f[0] for f in cls._fields_]

        def ctor(i, args, env, node):
            if len(args) == 1 and isinstance(args[0], dict):
                obj = cls()
                for field_name, field_ctype in cls._fields_:
                    if field_name not in args[0]:
                        i.error("结构体 %s 缺少字段 %r" % (name, field_name), node)
                    setattr(obj, field_name,
                            _c_arg(args[0][field_name],
                                   desc_from_ctype(field_ctype),
                                   self.conv, i, node, []))
                return _struct_foreign(obj, "c %s" % name, self.conv, i)
            if len(args) != len(field_names):
                i.error("结构体 %s 需要 %d 个字段值或一个字典，实际 %d 个"
                        % (name, len(field_names), len(args)), node)
            obj = cls()
            for (field_name, field_ctype), value in zip(cls._fields_, args):
                setattr(obj, field_name,
                        _c_arg(value, desc_from_ctype(field_ctype),
                               self.conv, i, node, []))
            return _struct_foreign(obj, "c %s" % name, self.conv, i)

        return Prim("ffi.%s.%s" % (self.name, name), ctor)

    def _make_prim(self, interp, name):
        from ..interp import Prim
        spec = self.functions[name]
        fn = spec["fn"]
        decl = spec["args"]
        restype = spec["restype"]
        varargs = spec["varargs"]

        def call(i, args, env, node):
            self.ensure_open(i, node)
            if len(args) < len(decl):
                i.error("%s 需要至少 %d 个参数，实际 %d 个"
                        % (name, len(decl), len(args)), node)
            if len(args) > len(decl) and not varargs:
                i.error("%s 需要 %d 个参数，实际 %d 个"
                        % (name, len(decl), len(args)), node)
            keep = []
            c_args = [_c_arg(args[idx], decl[idx], self.conv, i, node, keep)
                      for idx in range(len(decl))]
            for extra in args[len(decl):]:
                c_args.append(_vararg_arg(extra, self.conv, i, node))
            i.trace("ffi.call", lib=self.describe(), name=name, args=args)
            try:
                result = fn(*c_args)
            except EvalError:
                raise
            except (ctypes.ArgumentError, TypeError, ValueError, OSError) as exc:
                i.error("C 调用 %s.%s 失败: %s" % (self.name, name, exc), node)
            except Exception as exc:  # 防御：坏指针等异常
                i.error("C 调用 %s.%s 崩溃保护: %s: %s"
                        % (self.name, name, type(exc).__name__, exc), node)
            return _c_ret(result, restype, self.conv, i, node)

        return Prim("ffi.%s.%s" % (self.name, name), call)

    def member_names(self):
        return sorted(set(self.functions) | set(self.structs))


# --------------------------------------------------------------------------
# 规格解析
# --------------------------------------------------------------------------
def _lib_config(defaults, library):
    """在默认配置里按库名找 [libs.*]。"""
    libs = _as_dict(defaults.get("libs"), "[libs]")
    if not libs:
        return None
    base = os.path.basename(str(library))
    stems = {base}
    for suffix in (".so.6", ".so.1", ".so.0", ".so", ".dylib", ".dll", ".a"):
        if base.endswith(suffix):
            stems.add(base[: -len(suffix)])
            break
    for stem in list(stems):
        if stem.startswith("lib"):
            stems.add(stem[3:])
        else:
            stems.add("lib" + stem)
    for stem in sorted(stems, key=len):
        if stem and stem in libs:
            return _as_dict(libs[stem], "[libs.%s]" % stem)
    return None


class _Ffi:
    def __init__(self, interp):
        self.interp = interp
        self.default_path = default_config_path()
        self._defaults = None
        self._conv = None

    @property
    def defaults(self):
        # 延迟到真正使用时才读，避免 (import "ffi") 因默认配置损坏而失败。
        if self._defaults is None:
            self._defaults = _read_toml(self.default_path)
        return self._defaults

    def default_section(self, name):
        return _as_dict(self.defaults.get(name), "[%s]" % name)

    @property
    def conv(self):
        # 供 ffi.at / ffi.set-field 这类无句柄接口使用的默认转换器
        if self._conv is None:
            self._conv = self._make_converter(self.default_section("python"))
        return self._conv

    # ---- 公共辅助 ----
    def _make_converter(self, options):
        options = _as_dict(options, "[python]")
        encoding = options.get("bytes-encoding", "utf-8")
        if not isinstance(encoding, str) or not encoding:
            raise EvalError("[python] bytes-encoding 必须是非空字符串")
        return _Converter(
            self.interp,
            encoding=encoding,
            max_items=_as_int(options.get("max-items"), "max-items", 1000),
            max_depth=_as_int(options.get("max-depth"), "max-depth", 4),
        )

    def _resolve_path(self, path, node, base_dir=None):
        full = self.interp._resolve_io(path, node)
        if base_dir and not os.path.isabs(full):
            full = os.path.join(base_dir, full)
        return os.path.realpath(full)

    def _merge_python_options(self, data):
        options = dict(self.default_section("python"))
        options.update(_as_dict(data.get("options"), "[options]"))
        return options

    # ---- Python ----
    def import_python(self, target, node, spec_path=None, data=None, mode="direct"):
        data = data or {}
        options = self._merge_python_options(data)
        conv = self._make_converter(options)

        if spec_path is not None:
            base_dir = os.path.dirname(spec_path)
        else:
            base_dir = os.path.dirname(getattr(node, "file", None) or "") or None

        module_name, module = self._load_python_module(target, base_dir, node)

        exports = data.get("exports")
        if exports is not None:
            exports = _as_list(exports, "exports")
            for item in exports:
                if not isinstance(item, str) or not item:
                    raise EvalError("exports 里只能放非空字符串")
                try:
                    exists = hasattr(module, item)
                except Exception:
                    exists = False
                if not exists:
                    raise EvalError("配置导出的成员 %r 在 Python 模块 %r 里不存在"
                                    % (item, module_name))
        renames = _as_dict(data.get("rename"), "[rename]")
        for key, value in renames.items():
            if not isinstance(key, str) or not key:
                raise EvalError("[rename] 的键必须是非空字符串")
            if not isinstance(value, str) or not value:
                raise EvalError("[rename] 的值必须是非空字符串")
            try:
                exists = hasattr(module, value)
            except Exception:
                exists = False
            if not exists:
                raise EvalError("[rename] 指向的成员 %r 在 Python 模块 %r 里不存在"
                                % (value, module_name))

        deny = options.get("deny")
        if deny is None:
            deny = []
        if not isinstance(deny, list) or not all(isinstance(x, str) for x in deny):
            raise EvalError("[python] deny 必须是字符串数组")
        include_private = _as_bool(
            options.get("include-private"), "[python] include-private", False
        )

        env = Env("python:%s" % module_name, parent=None, kind="module",
                  is_module=True)
        handle = _PyHandle(module_name, env, module, conv,
                           exports=exports, renames=renames,
                           include_private=include_private, deny=deny)
        handle.mode = mode
        env.member_getter = lambda member: handle.resolve(self.interp, member)
        env.external = handle
        self.interp.trace("ffi.import", kind="python", name=module_name,
                          mode=mode, config=spec_path)
        return env

    def _load_python_module(self, target, base_dir, node):
        if not isinstance(target, str) or not target:
            raise EvalError("Python 模块名必须是非空字符串")
        looks_like_path = ("/" in target or target.endswith(".py")
                           or (os.sep in target))
        if looks_like_path:
            path = target
            if not os.path.isabs(path):
                if base_dir:
                    path = os.path.join(base_dir, path)
            path = os.path.realpath(path)
            if not os.path.isfile(path):
                self.interp.error("找不到 Python 文件 %s" % path, node)
            name = os.path.splitext(os.path.basename(path))[0]
            try:
                spec = importlib.util.spec_from_file_location(
                    "slang_ffi_" + name, path
                )
                if spec is None or spec.loader is None:
                    self.interp.error("无法为 %s 创建 Python 模块" % path, node)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
            except EvalError:
                raise
            except Exception as exc:
                self.interp.error("加载 Python 文件 %s 失败: %s: %s"
                                  % (path, type(exc).__name__, exc), node)
            return name, module
        try:
            module = importlib.import_module(target)
        except EvalError:
            raise
        except Exception as exc:
            self.interp.error("导入 Python 模块 %r 失败: %s: %s"
                              % (target, type(exc).__name__, exc), node)
        return target, module

    # ---- C ----
    def import_c(self, target, node, spec_path=None, data=None, mode="direct"):
        data = data or {}
        base_dir = os.path.dirname(spec_path) if spec_path else None
        if not isinstance(target, str) or not target:
            raise EvalError("C 库名必须是非空字符串")

        lib_defaults = _lib_config(self.defaults, target) or {}
        if mode == "direct":
            use_defaults = True
        else:
            use_defaults = _as_bool(data.get("uses-default"), "uses-default", False)
        merged_functions = {}
        merged_structs = {}
        if use_defaults and lib_defaults:
            merged_functions.update(
                _as_dict(lib_defaults.get("functions"), "[libs.*.functions]")
            )
            merged_structs.update(
                _as_dict(lib_defaults.get("structs"), "[libs.*.structs]")
            )

        merged_functions.update(
            _as_dict(data.get("functions"), "[functions]")
        )
        merged_structs.update(_as_dict(data.get("structs"), "[structs]"))

        c_options = dict(self.default_section("c"))
        c_options.update(_as_dict(data.get("c"), "[c]"))
        conv = self._make_converter(self._merge_python_options(data))

        ctx = CContext(types=_as_dict(data.get("types"), "[types]"))
        _build_structs(merged_structs, ctx)

        calling = c_options.get("calling-convention", "cdecl")
        if not isinstance(calling, str) or not calling:
            raise EvalError("[c] calling-convention 必须是非空字符串")
        lib, used = self._open_library(target, base_dir, lib_defaults, node,
                                       calling)
        lib_name = used

        functions = {}
        for name, raw in merged_functions.items():
            functions[name] = self._bind_c_function(
                lib, name, raw, ctx, c_options, node, lib_name
            )

        env = Env("c:%s" % lib_name, parent=None, kind="module", is_module=True)
        handle = _CHandle(lib_name, env, lib, conv, functions, ctx.structs)
        handle.mode = mode
        env.member_getter = lambda member: handle.resolve(self.interp, member)
        env.external = handle
        handle.ctx = ctx
        self.interp.trace("ffi.import", kind="c", name=lib_name, mode=mode,
                          config=spec_path, symbols=len(functions))
        return env

    def _open_library(self, target, base_dir, lib_defaults, node,
                      calling="cdecl"):
        if calling == "cdecl":
            loader = ctypes.CDLL
        elif calling in ("stdcall", "win64"):
            loader = getattr(ctypes, "WinDLL", None)
            if loader is None:
                self.interp.error(
                    "当前平台不支持调用约定 %r（只有 cdecl）" % calling, node
                )
        else:
            self.interp.error(
                "未知的调用约定 %r（支持 cdecl / stdcall / win64）" % calling,
                node,
            )
        candidates = []
        path = target
        if not os.path.isabs(path) and base_dir:
            path = os.path.join(base_dir, path)
        if os.path.isfile(path):
            candidates.append(os.path.realpath(path))
        else:
            if path != target:
                candidates.append(path)
            candidates.append(target)
            names = _as_list(lib_defaults.get("names"), "[libs.*].names")
            for name in names:
                if isinstance(name, str) and name:
                    candidates.append(name)
            found = ctypes.util.find_library(target)
            if found:
                candidates.append(found)
            stem = os.path.basename(target)
            for suffix in (".so.6", ".so.1", ".so", ".dylib", ".dll"):
                if stem.endswith(suffix):
                    stem = stem[: -len(suffix)]
                    break
            if stem.startswith("lib"):
                stem = stem[3:]
            found = ctypes.util.find_library(stem)
            if found:
                candidates.append(found)

        errors = []
        seen = set()
        for candidate in candidates:
            if not candidate or candidate in seen:
                continue
            seen.add(candidate)
            try:
                lib = loader(candidate)
                return lib, candidate
            except OSError as exc:
                errors.append("%s: %s" % (candidate, exc))
        self.interp.error(
            "无法加载 C 库 %r（尝试过 %s）" % (target, "; ".join(errors) or "无"),
            node,
        )

    def _bind_c_function(self, lib, name, raw, ctx, c_options, node, lib_name):
        raw = _as_dict(raw, "[functions.%s]" % name)
        symbol = raw.get("symbol", name)
        if not isinstance(symbol, str) or not symbol:
            raise EvalError("[functions.%s].symbol 必须是非空字符串" % name)
        try:
            fn = getattr(lib, symbol)
        except AttributeError:
            self.interp.error("C 库 %s 里找不到符号 %r" % (lib_name, symbol), node)
        restype_spec = raw.get("restype", c_options.get("default-restype", "int"))
        arg_specs = raw.get("args")
        if arg_specs is None:
            arg_specs = []
        arg_specs = _as_list(arg_specs, "[functions.%s].args" % name)
        varargs = _as_bool(raw.get("varargs"), "[functions.%s].varargs" % name)
        restype = parse_ctype(restype_spec, ctx)
        args = [parse_ctype(spec, ctx) for spec in arg_specs]

        try:
            fn.restype = restype.ctype
            if not varargs:
                fn.argtypes = [a.ctype for a in args]
            else:
                # 可变参数函数不能设 argtypes（ctypes 会拒绝多余实参），
                # 固定部分由 _c_arg 手动转换。
                fn.argtypes = None
        except (TypeError, AttributeError) as exc:
            self.interp.error("配置 C 函数 %s 的签名失败: %s" % (name, exc), node)
        return {
            "fn": fn,
            "args": args,
            "restype": restype,
            "varargs": varargs,
        }

    # ---- 配置文件入口 ----
    def import_spec(self, path, node):
        full = self._resolve_path(path, node)
        if not os.path.isfile(full):
            self.interp.error("找不到 ffi 配置文件 %s" % full, node)
        data = _read_toml(full)
        kind = data.get("kind")
        if kind not in ("python", "c"):
            raise EvalError("ffi 配置 %s 的 kind 必须是 \"python\" 或 \"c\"" % full)
        if kind == "python":
            module = data.get("module")
            if not isinstance(module, str) or not module:
                raise EvalError("ffi 配置 %s 缺少 module" % full)
            return self.import_python(module, node, spec_path=full, data=data,
                                      mode="config")
        library = data.get("library")
        if not isinstance(library, str) or not library:
            raise EvalError("ffi 配置 %s 缺少 library" % full)
        return self.import_c(library, node, spec_path=full, data=data,
                             mode="config")


# --------------------------------------------------------------------------
# 原语
# --------------------------------------------------------------------------
def build_module(interp):
    from ..interp import Prim

    env = Env("ffi", parent=None, kind="module", is_module=True)
    state = _Ffi(interp)

    def _python(i, args, env_, node):
        if len(args) not in (1, 2) or not isinstance(args[0], str):
            i.error("ffi.python 需要 (模块名) 或 (模块名 配置文件)", node)
        spec_path = None
        data = None
        if len(args) == 2:
            if not isinstance(args[1], str):
                i.error("ffi.python 的第二个参数必须是配置文件路径", node)
            spec_path = state._resolve_path(args[1], node)
            data = _read_toml(spec_path)
            if data.get("kind") not in (None, "python"):
                i.error("ffi.python 的配置文件 kind 必须是 \"python\"", node)
        return state.import_python(args[0], node, spec_path=spec_path, data=data,
                                   mode="config" if spec_path else "direct")

    def _c(i, args, env_, node):
        if len(args) not in (1, 2) or not isinstance(args[0], str):
            i.error("ffi.c 需要 (库名/路径) 或 (库名/路径 配置文件)", node)
        spec_path = None
        data = None
        if len(args) == 2:
            if not isinstance(args[1], str):
                i.error("ffi.c 的第二个参数必须是配置文件路径", node)
            spec_path = state._resolve_path(args[1], node)
            data = _read_toml(spec_path)
            if data.get("kind") not in (None, "c"):
                i.error("ffi.c 的配置文件 kind 必须是 \"c\"", node)
        return state.import_c(args[0], node, spec_path=spec_path, data=data,
                              mode="config" if spec_path else "direct")

    def _import(i, args, env_, node):
        if len(args) != 1 or not isinstance(args[0], str):
            i.error("ffi.import 需要 1 个配置文件路径", node)
        return state.import_spec(args[0], node)

    def _close(i, args, env_, node):
        if len(args) != 1:
            i.error("ffi.close 需要 1 个句柄", node)
        handle = _handle_of(args[0])
        if handle is None:
            i.error("ffi.close 需要 ffi 导入得到的句柄", node)
        handle.close()
        i.trace("ffi.close", lib=handle.describe())
        return []

    def _members(i, args, env_, node):
        if len(args) != 1:
            i.error("ffi.members 需要 1 个句柄", node)
        handle = _handle_of(args[0])
        if handle is None:
            i.error("ffi.members 需要 ffi 导入得到的句柄", node)
        return handle.member_names()

    def _bind(i, args, env_, node):
        """在已导入的 C 句柄上补充绑定一个符号。

        (ffi.bind 句柄 "符号名" "返回类型" ["参数类型"...] ["成员名"])
        """
        if len(args) not in (4, 5):
            i.error("ffi.bind 需要 (句柄 \"符号\" \"返回类型\" [\"参数类型\"...] [\"成员名\"])",
                    node)
        handle = _handle_of(args[0])
        if not isinstance(handle, _CHandle):
            i.error("ffi.bind 只支持 C 句柄", node)
        symbol, restype_spec, arg_specs = args[1], args[2], args[3]
        member = args[4] if len(args) == 5 else symbol
        if not isinstance(symbol, str) or not symbol:
            i.error("ffi.bind 的符号名必须是非空字符串", node)
        if not isinstance(restype_spec, str) or not restype_spec:
            i.error("ffi.bind 的返回类型必须是非空字符串", node)
        if not isinstance(member, str) or not member:
            i.error("ffi.bind 的成员名必须是非空字符串", node)
        if not isinstance(arg_specs, list) or not all(
                isinstance(item, str) for item in arg_specs):
            i.error("ffi.bind 的参数类型必须是字符串数组", node)
        raw = {
            "symbol": symbol,
            "restype": restype_spec,
            "args": list(arg_specs),
        }
        spec = state._bind_c_function(handle.lib, member, raw, handle.ctx,
                                      {}, node, handle.name)
        handle.functions[member] = spec
        handle._cache.pop(member, None)
        found, value = handle.resolve(i, member)
        return value if found else []

    def _call(i, args, env_, node):
        if len(args) < 2 or not isinstance(args[1], str):
            i.error("ffi.call 需要 (句柄 \"名字\" 参数...)", node)
        handle = _handle_of(args[0])
        if handle is None:
            i.error("ffi.call 的第一个参数必须是 ffi 句柄", node)
        name = args[1]
        found, fn = handle.resolve(i, name)
        if not found:
            i.error("%s 里没有成员 %r" % (handle.describe(), name), node)
        rest = args[2:]
        if isinstance(fn, Prim):
            if fn.lazy:
                i.error("ffi.call 不支持宏式成员", node)
            return fn.fn(i, rest, env_, node)
        slang_call = getattr(fn, "slang_call", None)
        if slang_call is not None:
            return slang_call(i, rest, env_, node)
        if rest:
            i.error("成员 %r 不是函数，不能带参数调用" % name, node)
        return fn

    def _null(i, args, env_, node):
        if args:
            i.error("ffi.null 不接受参数", node)
        return _NULL

    def _address(i, args, env_, node):
        if len(args) != 1:
            i.error("ffi.address 需要 1 个指针/对象", node)
        value = args[0]
        if isinstance(value, int) and not isinstance(value, bool):
            return value
        if isinstance(value, ForeignObject):
            obj = value.obj
            if isinstance(obj, int) and not isinstance(obj, bool):
                return obj
            try:
                if isinstance(obj, (ctypes.Structure, ctypes.Array)):
                    return ctypes.addressof(obj)
                if isinstance(obj, ctypes.c_void_p):
                    return obj.value or 0
                return ctypes.cast(obj, ctypes.c_void_p).value or 0
            except (TypeError, ValueError):
                i.error("%s 不能转成地址" % value.label, node)
        i.error("ffi.address 需要指针对象或整数地址", node)

    def _at(i, args, env_, node):
        if len(args) not in (2, 3):
            i.error("ffi.at 需要 (指针 下标) 或 (指针 下标 \"类型\")", node)
        ptr = args[0]
        index = args[1]
        if isinstance(index, bool) or not isinstance(index, int):
            i.error("ffi.at 的下标必须是整数", node)
        obj = None
        if isinstance(ptr, ForeignObject):
            obj = ptr.obj
        elif isinstance(ptr, int) and not isinstance(ptr, bool):
            obj = ptr
        else:
            i.error("ffi.at 需要指针对象或整数地址", node)

        if isinstance(obj, int) and not isinstance(obj, bool):
            if len(args) != 3 or not isinstance(args[2], str):
                i.error("用整数地址调用 ffi.at 时必须给类型字符串", node)
            desc = parse_ctype(args[2], CContext())
            try:
                value = ctypes.cast(ctypes.c_void_p(obj),
                                    ctypes.POINTER(desc.ctype))[index]
            except (TypeError, ValueError) as exc:
                i.error("按地址读取失败: %s" % exc, node)
            return _c_ret(value, desc, state.conv, i, node)

        try:
            item = obj[index]
        except (TypeError, IndexError, ValueError, KeyError) as exc:
            i.error("读取指针/数组下标 %d 失败: %s" % (index, exc), node)
        return _generic_from_c(item, state.conv, i)

    def _set_field(i, args, env_, node):
        if len(args) != 3 or not isinstance(args[1], str):
            i.error("ffi.set-field 需要 (对象 \"字段\" 值)", node)
        target = args[0]
        if not isinstance(target, ForeignObject):
            i.error("ffi.set-field 的第一个参数必须是结构体对象", node)
        obj = target.obj
        obj = _struct_target(obj)
        if not isinstance(obj, ctypes.Structure):
            i.error("%s 不是 C 结构体" % target.label, node)
        field_ctype = None
        for field_name, ctype in obj._fields_:
            if field_name == args[1]:
                field_ctype = ctype
                break
        if field_ctype is None:
            i.error("结构体 %s 没有字段 %r" % (target.label, args[1]), node)
        setattr(obj, args[1],
                _c_arg(args[2], desc_from_ctype(field_ctype), state.conv,
                       i, node, []))
        return []

    def _version(i, args, env_, node):
        if args:
            i.error("ffi.version 不接受参数", node)
        from .. import __version__
        return __version__

    def _default_config(i, args, env_, node):
        if args:
            i.error("ffi.default-config 不接受参数", node)
        return state.default_path

    prims = {
        "python": _python,
        "c": _c,
        "import": _import,
        "close": _close,
        "members": _members,
        "bind": _bind,
        "call": _call,
        "null": _null,
        "address": _address,
        "at": _at,
        "set-field": _set_field,
        "version": _version,
        "default-config": _default_config,
    }

    def _positioned(fn):
        """把没有位置的 EvalError 补上调用点位置。"""
        def call(i, args, env_, node):
            try:
                return fn(i, args, env_, node)
            except EvalError as exc:
                if exc.filename:
                    raise
                i.error(exc, node)
        return call

    for name, fn in prims.items():
        wrapped = _positioned(fn)
        env.bind(name, Prim("ffi." + name,
                            lambda i, a, e, n, _fn=wrapped: _fn(i, a, e, n)))
    return env


def _handle_of(value):
    """从 Env 里取回 _Handle（导入时挂在 Env.external 上）。"""
    handle = getattr(value, "external", None)
    if isinstance(handle, _Handle):
        return handle
    return None


def _generic_from_c(value, conv, interp=None):
    """把 ctypes 取出的任意值转成 slang 值。"""
    if value is None:
        return []
    if isinstance(value, ctypes.Structure):
        if interp is not None:
            return _struct_foreign(value, "c %s" % type(value).__name__,
                                   conv, interp)
        return ForeignObject(value, "c %s" % type(value).__name__,
                             slang_type="struct")
    if isinstance(value, ctypes.Array):
        return ForeignObject(value, "c %s[%d]" % (type(value).__name__, len(value)),
                             slang_type="pointer")
    if isinstance(value, bytes):
        return value.decode(conv.encoding, "replace")
    pointer_cls = getattr(ctypes, "_Pointer", None)
    if pointer_cls is not None and isinstance(value, pointer_cls):
        if not value:
            return []
        return ForeignObject(value, "c pointer", slang_type="pointer")
    return value
