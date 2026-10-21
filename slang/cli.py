"""命令行入口：python -m slang <run|check|lex|decode|version>。"""
import argparse
import json
import math
import os
import sys

from . import __version__, formats, loader, native
from .errors import SlangError
from .interp import Interp


def _json_token(tok):
    """token 是 (kind, value, line, col, end)；非有限浮点转成字符串，
    保证 lex 输出始终是严格 JSON（不再出现裸 Infinity/NaN）。"""
    values = list(tok)
    if len(values) > 1 and isinstance(values[1], float) and not math.isfinite(values[1]):
        if math.isnan(values[1]):
            values[1] = "NaN"
        elif values[1] > 0:
            values[1] = "Infinity"
        else:
            values[1] = "-Infinity"
    return values


def build_parser():
    parser = argparse.ArgumentParser(prog="slang", description="slang 语言解释器")
    parser.add_argument("--no-native", action="store_true",
                        help="禁用 C++ 核心，只用纯 Python 词法器")
    sub = parser.add_subparsers(dest="command")

    def add_no_native(p):
        # 允许 --no-native 出现在子命令后面；default=SUPPRESS 避免覆盖
        # 顶层已经解析出的 True。
        p.add_argument("--no-native", action="store_true", default=argparse.SUPPRESS,
                       help="禁用 C++ 核心，只用纯 Python 词法器")

    run = sub.add_parser("run", help="运行程序")
    run.add_argument("files", nargs="*", help="程序文件（省略时使用配置里的 project.main）")
    run.add_argument("--project", help="slang.toml 路径（默认从程序文件/当前目录向上查找）")
    add_no_native(run)

    check = sub.add_parser("check", help="只解析不执行")
    check.add_argument("files", nargs="*", help="要检查的源文件")
    check.add_argument("--project", help="校验指定的 slang.toml 与构建文件（只解析，不执行）")
    add_no_native(check)

    lex = sub.add_parser("lex", help="输出词法 token（JSON），用于调试")
    lex.add_argument("file", help="源文件")
    lex.add_argument("--mode", choices=("sexpr", "auto", "slang"),
                     help="覆盖文件首行的格式声明")
    add_no_native(lex)

    decode = sub.add_parser("decode", help="把 .slang 解码为等价 .lang 源码")
    decode.add_argument("file", help=".slang 源文件")
    decode.add_argument("-o", "--output", help="输出文件（省略时写到 stdout）")
    decode.add_argument("--width", type=int, default=80,
                        help="换行宽度，默认 80（最小 24）")
    add_no_native(decode)

    version = sub.add_parser("version", help="显示版本信息")
    add_no_native(version)
    return parser


def make_interp(project_root=None, warn=None):
    interp = Interp(project_root=project_root or os.getcwd(), warn=warn)
    return interp


def load_project(interp, project_arg, files=None):
    """定位并加载项目的 slang.toml。

    优先使用显式 --project；否则先按第一个程序文件向上查找，
    再退回当前目录向上查找（保持临时脚本可以直接运行时加载当前项目）。
    加载成功后把 project_root 固定为配置文件所在目录。
    """
    toml_path = project_arg
    if not toml_path and files:
        toml_path = loader.find_project_file(files[0])
    if not toml_path:
        toml_path = loader.find_project_file()
    if not toml_path:
        return None, None
    toml_path = os.path.abspath(toml_path)
    data = loader.load_config(interp, toml_path)
    interp.project_root = os.path.dirname(toml_path)
    return toml_path, data


def cmd_run(args):
    if args.no_native:
        native.disable()
    files = [os.path.abspath(f) for f in args.files]
    interp = make_interp()
    try:
        toml_path, data = load_project(interp, args.project, files)
        if data is None:
            # 没有项目配置时使用随包发布的内置 base/std，使安装后的
            # `slang run file.lang` 在任意目录可用。log 仍不会自动加载。
            loader.load_builtin_runtime(interp)
        interp.begin_program()

        if not files and data is not None:
            main = loader.project_main(data)
            if main:
                base = os.path.dirname(toml_path)
                files = [main if os.path.isabs(main) else os.path.join(base, main)]

        if not files:
            print("错误: 没有指定程序文件，也没有在 slang.toml 里找到 project.main",
                  file=sys.stderr)
            return 2

        interp.trace("run.start", files=files, project=interp.project_root)
        for path in files:
            loader.load_file(interp, path, mode="override")
        interp.trace("run.end", files=files)
        return 0
    finally:
        interp.close_log()


def cmd_check(args):
    if args.no_native:
        native.disable()
    failures = 0
    if not args.files and not args.project:
        print("错误: 请至少给一个源文件，或用 --project 指定 slang.toml",
              file=sys.stderr)
        return 2
    interp = make_interp()
    if args.project:
        try:
            loader.check_config(interp, os.path.abspath(args.project))
            print("OK 配置 %s（只解析，未执行构建）" % args.project)
        except SlangError as exc:
            print("失败 %s: %s" % (args.project, exc), file=sys.stderr)
            return 1
    for path in args.files:
        try:
            nodes, mode, fmt_name, backend = loader.read_nodes(interp, path)
            fmt_label = "@" + fmt_name if fmt_name else "未声明(按 auto)"
            print("OK %s (格式 %s, 模式 %s, 词法 %s, %d 个顶层表达式)"
                  % (path, fmt_label, mode, backend, len(nodes)))
        except SlangError as exc:
            failures += 1
            print("失败 %s: %s" % (path, exc), file=sys.stderr)
        except (BrokenPipeError, RecursionError, KeyboardInterrupt):
            raise
        except Exception as exc:  # 单个文件的意外错误不应中止整批检查
            failures += 1
            print("失败 %s: 内部错误 %s: %s" % (path, type(exc).__name__, exc),
                  file=sys.stderr)
    return 1 if failures else 0


def cmd_lex(args):
    if args.no_native:
        native.disable()
    text = loader.read_source(args.file)
    fmt_name, _version, body, _header_line = formats.split_header(text, args.file)
    if fmt_name is None and args.file.lower().endswith(".slang"):
        fmt_name = "slang"
    mode = args.mode or formats.resolve(
        fmt_name, args.file, has_body=formats.has_code(body))[0]
    if mode == "slang":
        from . import slangfmt
        tokens = [t.tuple() for t in slangfmt.lex(body, args.file)]
        backend = "slang"
    else:
        tokens, backend = native.lex_with_fallback(body, mode, args.file)
    payload = {
        "format": fmt_name,
        "mode": mode,
        "backend": backend,
        "tokens": [_json_token(t) for t in tokens],
    }
    print(json.dumps(payload, ensure_ascii=False, allow_nan=False))
    return 0


def cmd_decode(args):
    if args.no_native:
        native.disable()
    text = loader.read_source(args.file)
    fmt_name, _version, body, _header_line = formats.split_header(text, args.file)
    if fmt_name is not None and fmt_name.lower() != "slang":
        print("错误: decode 只处理 .slang 或 @slang，%s 声明的是 @%s"
              % (args.file, fmt_name), file=sys.stderr)
        return 1
    from . import slangfmt
    nodes = slangfmt.parse(body, args.file)
    output = slangfmt.decode_to_lang(nodes, width=max(24, args.width))
    if args.output:
        try:
            with open(args.output, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(output)
        except OSError as exc:
            print("错误: 写入 %s 失败: %s" % (args.output, exc), file=sys.stderr)
            return 1
    else:
        sys.stdout.write(output)
    return 0


def cmd_version(args):
    print("slang 语言 %s (Python %s)" % (__version__, sys.version.split()[0]))
    if args.no_native:
        print("C++ 核心: 已禁用 (--no-native)")
        return 0
    core_version = native.version()
    if core_version:
        state = native.load()
        print("C++ 核心: %s (%s)" % (core_version, state[1]))
    else:
        why = native.reason()
        suffix = "，%s" % why if why else ""
        print("C++ 核心: 未启用（使用纯 Python 词法器%s）" % suffix)
    return 0


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 2
    sys.setrecursionlimit(max(sys.getrecursionlimit(), 20000))
    try:
        if args.command == "run":
            return cmd_run(args)
        if args.command == "check":
            return cmd_check(args)
        if args.command == "lex":
            return cmd_lex(args)
        if args.command == "decode":
            return cmd_decode(args)
        if args.command == "version":
            return cmd_version(args)
    except BrokenPipeError:
        # 例如 slang lex ... | head：安静退出，避免解释器收尾时再次报错
        try:
            devnull = os.open(os.devnull, os.O_WRONLY)
            os.dup2(devnull, sys.stdout.fileno())
        except OSError:
            pass
        return 141
    except SlangError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except RecursionError:
        print("错误: 递归太深（超过 Python 栈限制）", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    except Exception as exc:  # 兜底：不把 Python traceback 直接暴露给用户
        print("错误: 内部错误: %s: %s" % (type(exc).__name__, exc), file=sys.stderr)
        return 70
    parser.print_help()
    return 2
