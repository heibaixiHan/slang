"""slang 语言的端到端测试。

运行：python3 -m unittest discover -s tests -v
"""
import json
import math
import os
import random
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP_ROOT = os.environ.get("TMPDIR", "/data/data/com.termux/files/usr/tmp")


def c_libs_available():
    try:
        import ctypes.util
        return bool(ctypes.util.find_library("m")) and bool(
            ctypes.util.find_library("c")
        )
    except Exception:
        return False


_HAS_C_LIBS = c_libs_available()


def zlib_available():
    try:
        import ctypes.util
        return bool(ctypes.util.find_library("z"))
    except Exception:
        return False


_HAS_ZLIB = zlib_available()


def run_slang(*args, cwd=ROOT):
    env = dict(os.environ)
    env["PYTHONPATH"] = ROOT + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.run(
        [sys.executable, "-m", "slang"] + list(args),
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    return proc


def normalize(text):
    return text.replace("\r\n", "\n")


def cpp_available():
    proc = run_slang("version")
    return (
        proc.returncode == 0
        and "C++ 核心:" in proc.stdout
        and "未启用" not in proc.stdout
        and "已禁用" not in proc.stdout
    )


class _TmpDirMixin:
    def setUp(self):
        self.tmpdir = os.path.join(TMP_ROOT, "%s-%d" % (type(self).__name__, os.getpid()))
        os.makedirs(self.tmpdir, exist_ok=True)

    def write_program(self, name, text):
        path = os.path.join(self.tmpdir, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return path

    def write_bytes(self, name, data):
        path = os.path.join(self.tmpdir, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(data)
        return path


class TestExamples(unittest.TestCase):
    def test_hello(self):
        proc = run_slang("run", "examples/hello.lang")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(
            normalize(proc.stdout),
            "hello from slang语言!\n1 + 2 * 3 = 7\n",
        )

    def test_fact(self):
        proc = run_slang("run", "examples/fact.lang")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("fact(10) = 3628800", proc.stdout)

    def test_stdlib_lists(self):
        proc = run_slang("run", "examples/lists.lang")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("[0 1 2 3 4 5 6 7 8 9]", proc.stdout)
        self.assertIn("[1 4 9 16 25]", proc.stdout)
        self.assertIn("5050", proc.stdout)

    def test_override_mode(self):
        proc = run_slang("run", "examples/override_demo.lang")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = normalize(proc.stdout)
        self.assertIn("加载 pack_a 之后:\n[\"A\" 3]\n", out)
        self.assertIn("加载 pack_b 之后（B 覆盖了 A）:\n[\"B\" 3]\n", out)

    def test_import_mode(self):
        proc = run_slang("run", "examples/import_demo.lang")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("math.square(6) = 36", proc.stdout)
        self.assertIn("math.cube(3)   = 27", proc.stdout)
        self.assertIn("std.square(7)  = 49", proc.stdout)

    def test_auto_format_warning(self):
        proc = run_slang("run", "examples/auto_format_demo.lang")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("未适配", proc.stderr)
        self.assertIn("通用自动转换器", proc.stderr)
        self.assertIn("来自自动转换格式的问候", proc.stdout)
        self.assertIn("total = 7", proc.stdout)

    def test_check(self):
        proc = run_slang("check", "slang/stdlib/core/base.lang.build")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("OK", proc.stdout)

    def test_log_demo(self):
        proc = run_slang("run", "examples/log_demo.lang")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = normalize(proc.stdout)
        self.assertIn("日志已启动? false", out)
        self.assertIn("日志已关闭? false", out)
        log_path = os.path.join(ROOT, "examples", "log_demo.jsonl")
        try:
            self.assertTrue(os.path.exists(log_path))
            events = set()
            with open(log_path, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if line:
                        events.add(json.loads(line)["event"])
            for name in ("log.open", "log.close", "log.info", "log.event",
                         "log.warn", "call", "def", "set", "print"):
                self.assertIn(name, events)
        finally:
            if os.path.exists(log_path):
                os.remove(log_path)

    @unittest.skipUnless(_HAS_C_LIBS, "需要 libm / libc")
    def test_ffi_demo(self):
        proc = run_slang("run", "examples/ffi_demo.lang")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = normalize(proc.stdout)
        self.assertIn("sqrt(81) = 9", out)
        self.assertIn("pow(2,8) = 256", out)
        self.assertIn("div(17,5)= 3 余 2", out)
        self.assertIn("编码     = [1, 2, 3]", out)

    def test_slang_demo(self):
        proc = run_slang("run", "examples/slang_demo.slang")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(
            normalize(proc.stdout),
            "hello, slang!\n"
            "fib(15) = 610\n"
            "square(9) = 81, math.cube(3) = 27\n"
            "classify(-2/0/5) = negative, zero, positive\n"
            "xs = [1 2 3 4 5]\n"
            "sum = 15\n"
            "evens = [2 4]\n"
            "math.pi = 3.14159\n",
        )


class TestSemantics(_TmpDirMixin, unittest.TestCase):
    def test_undefined_name(self):
        path = self.write_program("bad.lang", "@sexpr\n(println (no-such-name 1))\n")
        proc = run_slang("run", path)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("未定义的名称", proc.stderr)

    def test_override_any_definition(self):
        path = self.write_program(
            "override.lang",
            "@sexpr\n"
            "(defn println (x) (print (concat \"<\" x \">\\n\")))\n"
            "(println \"hi\")\n",
        )
        proc = run_slang("run", path)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, "<hi>\n")

    def test_if_and_arity(self):
        path = self.write_program(
            "if.lang",
            "@sexpr\n"
            "(println (str (if 0 \"yes\" \"no\")))\n"
            "(println (str (if 1 \"yes\" \"no\")))\n",
        )
        proc = run_slang("run", path)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(normalize(proc.stdout), "no\nyes\n")

    def test_variadic_concat(self):
        path = self.write_program(
            "concat.lang",
            "@sexpr\n(println (concat \"a\" 1 \"b\" 2))\n",
        )
        proc = run_slang("run", path)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, "a1b2\n")

    def test_condition_values_and_shadowing(self):
        path = self.write_program(
            "cond.lang",
            "@sexpr\n"
            "(def flag 1)\n"
            "(def x 99)\n"
            "(defn f (x) (if x \"yes\" \"no\"))\n"
            "(println (if flag \"t\" \"f\"))\n"
            "(println (if true \"t\" \"f\"))\n"
            "(println (if false \"t\" \"f\"))\n"
            "(println (if nil \"t\" \"f\"))\n"
            "(println (if 0 \"t\" \"f\"))\n"
            "(println (f 0))\n"
            "(println (f 3))\n",
        )
        proc = run_slang("run", path)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(normalize(proc.stdout), "t\nt\nf\nf\nf\nno\nyes\n")

    def test_and_or_when_values(self):
        path = self.write_program(
            "logic.lang",
            "@sexpr\n"
            "(println (str (and 0 1)))\n"
            "(println (str (and 1 2)))\n"
            "(println (str (or 5 6)))\n"
            "(println (str (or 0 6)))\n"
            "(println (str (when 0 1)))\n"
            "(println (if (when 0 1) \"t\" \"f\"))\n"
            "(println (if (and 1 0) \"t\" \"f\"))\n",
        )
        proc = run_slang("run", path)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(normalize(proc.stdout), "0\n2\n5\n6\n[]\nf\nf\n")

    def test_set_updates_outer_and_closure(self):
        path = self.write_program(
            "set.lang",
            "@sexpr\n"
            "(def counter 0)\n"
            "(defn bump () (do (set counter (+ counter 1)) counter))\n"
            "(bump)\n"
            "(println (str counter))\n"
            "(defn make-counter ()\n"
            "  (do (def n 0)\n"
            "      (fn () (do (set n (+ n 1)) n))))\n"
            "(def c (make-counter))\n"
            "(println (str (c)))\n"
            "(println (str (c)))\n",
        )
        proc = run_slang("run", path)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(normalize(proc.stdout), "1\n1\n2\n")

    def test_macro_environments(self):
        path = self.write_program(
            "macro.lang",
            "@sexpr\n"
            "(defmacro define-it (n v) (def n v))\n"
            "(define-it z 5)\n"
            "(println (str z))\n"
            "(defmacro pick (c) (if c 11 22))\n"
            "(println (str (pick 0)))\n"
            "(println (str (pick 1)))\n",
        )
        proc = run_slang("run", path)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(normalize(proc.stdout), "5\n22\n11\n")

    def test_nested_macros(self):
        path = self.write_program(
            "nested.lang",
            "@sexpr\n"
            "(defmacro inc (x) (+ (%eval x) 1))\n"
            "(defmacro twice (y) (inc (inc y)))\n"
            "(println (str (twice 5)))\n"
            "(def b 99)\n"
            "(println (str (and 1 b)))\n"
            "(println (str (or b 7)))\n",
        )
        proc = run_slang("run", path)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(normalize(proc.stdout), "7\n99\n99\n")

    def test_macro_parameter_name_collisions(self):
        path = self.write_program(
            "collide.lang",
            "@sexpr\n"
            "(def value 1)\n"
            "(def name 2)\n"
            "(println (str value))\n"
            "(println (str name))\n"
            "(defmacro wrap-value (x) (def value x))\n"
            "(wrap-value 9)\n"
            "(println (str value))\n"
            "(defn value-fn (value) value)\n"
            "(println (str (value-fn 7)))\n",
        )
        proc = run_slang("run", path)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(normalize(proc.stdout), "1\n2\n9\n7\n")

    def test_multibody_and_string_name(self):
        path = self.write_program(
            "multi.lang",
            "@sexpr\n"
            "(defn multi (a) (println \"first\") (+ a 1))\n"
            "(println (multi 41))\n"
            "(def \"named\" 7)\n"
            "(println named)\n"
            "(println (str (do)))\n"
            "(println (str nil))\n"
            "(println (str (%quote (+ 1 2))))\n"
            "(println (str (= (%quote a) (%quote a))))\n"
            "(println (str (= (%quote a) (%quote b))))\n",
        )
        proc = run_slang("run", path)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(
            normalize(proc.stdout),
            "first\n42\n7\n[]\n[]\n(+ 1 2)\ntrue\nfalse\n",
        )

    def test_arity_error_names_function(self):
        path = self.write_program(
            "arity.lang", "@sexpr\n(defn one (x) x)\n(one 1 2)\n"
        )
        proc = run_slang("run", path)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("one 需要 1 个参数", proc.stderr)

    def test_string_escapes(self):
        program = r'''@sexpr
(println "a\qb")
(println "a\u0041b")
(println "tab:\there")
(println "quote:\"ok\"")
(println "slash:\\ok")
(println "nul:\0end")
'''
        path = self.write_program("escapes.lang", program)
        proc = run_slang("run", path)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        expected = (
            "a\\qb\n"
            "a\\u0041b\n"
            "tab:\there\n"
            "quote:\"ok\"\n"
            "slash:\\ok\n"
            "nul:\x00end\n"
        )
        self.assertEqual(proc.stdout, expected)

    def test_backslash_newline_rejected(self):
        path = self.write_program("cont.lang", '@sexpr\n(println "a\\\nb")\n')
        proc = run_slang("run", path)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("不能跨行", proc.stderr)

    def test_sort_split_join_substr(self):
        program = (
            "@sexpr\n"
            "(println (str (sort (list 3 1 2))))\n"
            "(println (str (sort (list 1 2 3) (fn (a b) (- b a)))))\n"
            "(println (str (sort (list \"b\" \"a\"))))\n"
            "(println (str (split \"a,b,c\" \",\")))\n"
            "(println (join (list \"a\" \"b\" \"c\") \"-\"))\n"
            "(println (substr \"hello\" 1 3))\n"
            "(println (substr \"hello\" -3))\n"
        )
        path = self.write_program("strlist.lang", program)
        proc = run_slang("run", path)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(
            normalize(proc.stdout),
            "[1 2 3]\n[3 2 1]\n[\"a\" \"b\"]\n[\"a\" \"b\" \"c\"]\na-b-c\nel\nllo\n",
        )

    def test_string_helper_validation(self):
        path = self.write_program("joinbad.lang", "@sexpr\n(join (list 1) \",\")\n")
        proc = run_slang("run", path)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("join 只支持字符串列表", proc.stderr)

        path = self.write_program("splitbad.lang", "@sexpr\n(split \"a\" \"\")\n")
        proc = run_slang("run", path)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("分隔符不能为空", proc.stderr)

    def test_std_survives_language_overrides(self):
        path = self.write_program(
            "override_std.lang",
            "@sexpr\n"
            "(defn = (a b) 1)\n"
            "(def if (fn (c a b) a))\n"
            "(println (str (map square (range 1 4))))\n"
            "(println (str (filter (fn (x) (> x 2)) (list 1 2 3))))\n"
            "(println (str (reduce (fn (a b) (+ a b)) 0 (range 1 11))))\n",
        )
        proc = run_slang("run", path)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(normalize(proc.stdout), "[1 4 9]\n[3]\n55\n")

    def test_large_lists_via_batch_primitives(self):
        path = self.write_program(
            "biglist.lang",
            "@sexpr\n"
            "(println (len (range 0 100000)))\n"
            "(println (len (map square (range 0 50000))))\n"
            "(println (sum (range 0 101)))\n",
        )
        proc = run_slang("run", path)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(normalize(proc.stdout), "100000\n50000\n5050\n")

    def test_auto_signed_adjacency(self):
        # auto 词法不再把 +/- 吞进数字：1 +2 / 1 -2 与 1 *2 规则一致。
        path = self.write_program(
            "auto_signed.lang",
            "@auto\n"
            "println(1 +2)\n"
            "println(1 -2)\n"
            "println(1 *2)\n"
            "println(7 % 3)\n",
        )
        proc = run_slang("run", path)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(normalize(proc.stdout), "3\n-1\n2\n1\n")

    def test_auto_empty_arguments_rejected(self):
        path = self.write_program("auto_empty.lang", "@auto\nprintln()\n")
        proc = run_slang("run", path)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("实际 0 个", proc.stderr)

    def test_auto_top_level_comma_is_separator(self):
        path = self.write_program(
            "auto_comma.lang", "@auto\nprintln(1), println(2)\n"
        )
        proc = run_slang("run", path)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(normalize(proc.stdout), "1\n2\n")

    def test_definition_name_cannot_contain_dot(self):
        path = self.write_program("dotdef.lang", "@sexpr\n(def m.value 7)\n")
        proc = run_slang("run", path)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("定义名不能包含 '.'", proc.stderr)

    def test_duplicate_parameter_name_rejected(self):
        path = self.write_program("dupparam.lang", "@sexpr\n(defn f (x x) x)\n")
        proc = run_slang("run", path)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("参数名重复", proc.stderr)

    def test_rest_parameter_must_be_last_and_named(self):
        for name, text in (
            ("rest_first.lang", "@sexpr\n(defn f (... x) x)\n"),
            ("rest_empty.lang", "@sexpr\n(defn f (x ...) x)\n"),
        ):
            with self.subTest(file=name):
                proc = run_slang("run", self.write_program(name, text))
                self.assertEqual(proc.returncode, 1)
                self.assertIn("变长参数名不能为空", proc.stderr)

    def test_empty_function_body_is_allowed(self):
        path = self.write_program(
            "emptybody.lang", "@sexpr\n(defn f ())\n(println (str (f)))\n"
        )
        proc = run_slang("run", path)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(normalize(proc.stdout), "[]\n")

    def test_nul_in_path_is_rejected(self):
        # 源码里放一个真正的 NUL 字节：路径里不能有 NUL。
        path = self.write_bytes(
            "nulpath.lang", b'@sexpr\n(println (readfile "a\x00b"))\n'
        )
        proc = run_slang("run", path)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("NUL", proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)

    def test_big_integer_and_num_string(self):
        digits = "7" * 5000
        path = self.write_program(
            "bignum.lang", '@sexpr\n(println (num "%s"))\n' % digits
        )
        proc = run_slang("run", path)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(normalize(proc.stdout).strip(), digits)

    def test_integer_digit_limit_is_readable(self):
        digits = "9" * 200000
        path = self.write_program(
            "toobig.lang", '@sexpr\n(println (num "%s"))\n' % digits
        )
        proc = run_slang("run", path)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("整数太大", proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)
        self.assertNotIn("inf", proc.stderr.lower().split("整数太大")[0])


class TestLogModule(_TmpDirMixin, unittest.TestCase):
    """log 模块：必须显式 (import "log") 才存在，且不自动加载。"""

    def test_log_is_not_loaded_automatically(self):
        path = self.write_program(
            "nolog.lang", "@sexpr\n(println (str (log.enabled)))\n"
        )
        proc = run_slang("run", path)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("未定义的名称", proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)

    def test_log_explicit_import_writes_events(self):
        out = os.path.join(self.tmpdir, "run.jsonl")
        path = self.write_program(
            "log.lang",
            "@sexpr\n"
            '(def lg (import "log"))\n'
            '(println (str (lg.enabled)))\n'
            '(lg.start "%s")\n'
            '(lg.info "checkpoint" "data")\n'
            '(lg.event "manual" 42)\n'
            "(lg.stop)\n"
            '(println (str (lg.enabled)))\n' % out,
        )
        proc = run_slang("run", path)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(normalize(proc.stdout), "false\nfalse\n")
        self.assertTrue(os.path.exists(out))
        records = []
        with open(out, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    records.append(json.loads(line))
        events = [r["event"] for r in records]
        self.assertIn("log.open", events)
        self.assertIn("log.info", events)
        self.assertIn("log.event", events)
        self.assertIn("log.close", events)
        self.assertIn("call", events)
        info = [r for r in records if r["event"] == "log.info"][0]
        self.assertEqual(info["name"], "checkpoint")
        self.assertEqual(info["data"], "data")
        manual = [r for r in records if r["event"] == "log.event"][0]
        self.assertEqual(manual["name"], "manual")
        self.assertEqual(manual["data"], "42")
        for record in records:
            self.assertIn("ts", record)
            self.assertIn("pid", record)

    def test_log_event_without_start_reports_position(self):
        path = self.write_program(
            "lognostart.lang",
            '@sexpr\n(def lg (import "log"))\n(lg.info "boom")\n',
        )
        proc = run_slang("run", path)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("日志未启动", proc.stderr)
        self.assertIn("lognostart.lang 第 3 行", proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)

    def test_log_start_rejects_bad_arguments(self):
        for text in (
            "@sexpr\n(def lg (import \"log\"))\n(lg.start)\n",
            "@sexpr\n(def lg (import \"log\"))\n(lg.start 1)\n",
            "@sexpr\n(def lg (import \"log\"))\n(lg.start \"x.jsonl\" -1)\n",
            "@sexpr\n(def lg (import \"log\"))\n(lg.start \"x.jsonl\" 10 \"z\")\n",
        ):
            with self.subTest(text=text):
                proc = run_slang("run", self.write_program("logargs.lang", text))
                self.assertEqual(proc.returncode, 1)
                self.assertNotIn("Traceback", proc.stderr)

    def test_log_append_mode_keeps_old_content(self):
        out = os.path.join(self.tmpdir, "append.jsonl")
        path = self.write_program(
            "logappend.lang",
            "@sexpr\n"
            '(def lg (import "log"))\n'
            '(lg.start "%s")\n(lg.info "first")\n(lg.stop)\n'
            '(lg.start "%s" 0 "a")\n(lg.info "second")\n(lg.stop)\n' % (out, out),
        )
        proc = run_slang("run", path)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        with open(out, "r", encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("first", text)
        self.assertIn("second", text)

    def test_log_not_in_project_builds(self):
        with open(os.path.join(ROOT, "slang.toml"), "r", encoding="utf-8") as fh:
            text = fh.read()
        self.assertNotIn('"log"', text)
        self.assertNotIn("log", text.replace("slang语言", ""))

    def test_log_truncates_large_values(self):
        out = os.path.join(self.tmpdir, "big.jsonl")
        path = self.write_program(
            "logbig.lang",
            "@sexpr\n"
            '(def lg (import "log"))\n'
            '(lg.start "%s")\n'
            "(def xs (range 0 100000))\n"
            "(println (len xs))\n"
            "(lg.stop)\n" % out,
        )
        proc = run_slang("run", path)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(normalize(proc.stdout), "100000\n")
        size = os.path.getsize(out)
        # 日志不能把十万项列表整个写进去。
        self.assertLess(size, 200 * 1024, "日志过大: %d 字节" % size)
        with open(out, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                record = json.loads(line)
                for value in record.values():
                    if isinstance(value, list):
                        self.assertLessEqual(len(value), 60)

    def test_log_start_rejects_nul_path(self):
        path = self.write_bytes(
            "lognul.lang",
            b'@sexpr\n(def lg (import "log"))\n(lg.start "a\x00b")\n',
        )
        proc = run_slang("run", path)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("NUL", proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)


class TestLoading(_TmpDirMixin, unittest.TestCase):
    def test_module_relative_load_and_function_load(self):
        self.write_program(
            "lib/util.lang",
            "@sexpr\n"
            "(def inner-value 99)\n"
            "(defn read-data ()\n"
            "  (load \"data.lang\")\n"
            "  inner-loaded)\n",
        )
        self.write_program("lib/data.lang", "@sexpr\n(def inner-loaded 123)\n")
        main = self.write_program(
            "main.lang",
            "@sexpr\n"
            "(def util (import \"lib/util.lang\"))\n"
            "(println (str (util.read-data)))\n"
            "(println (str util.inner-loaded))\n"
            "(defn setup () (load \"conf.lang\"))\n"
            "(setup)\n"
            "(println loaded-value)\n",
        )
        self.write_program("conf.lang", "@sexpr\n(def loaded-value 55)\n")
        proc = run_slang("run", main)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(normalize(proc.stdout), "123\n123\n55\n")

    def test_load_config_does_not_leak_project_root(self):
        self.write_program("cfg/slang.toml", "[builds]\nfiles = [\"defs.lang\"]\n")
        self.write_program("cfg/defs.lang", "@sexpr\n(def conf-value 42)\n")
        main = self.write_program(
            "main.lang",
            "@sexpr\n"
            "(defn setup-cfg () (load-config \"cfg/slang.toml\"))\n"
            "(setup-cfg)\n"
            "(println (str conf-value))\n"
            "(load \"sibling.lang\")\n"
            "(println (str sibling-value))\n",
        )
        self.write_program("sibling.lang", "@sexpr\n(def sibling-value 7)\n")
        proc = run_slang("run", main)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(normalize(proc.stdout), "42\n7\n")

    def test_error_location_and_call_chain(self):
        self.write_program(
            "lib/err.lang",
            "@sexpr\n(defn boom ()\n  (no-such))\n",
        )
        main = self.write_program(
            "errmain.lang",
            "@sexpr\n"
            "(def err (import \"lib/err.lang\"))\n"
            "(println \"before\")\n"
            "(err.boom)\n",
        )
        proc = run_slang("run", main)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("before", proc.stdout)
        self.assertIn("err.lang 第 3 行", proc.stderr)
        self.assertIn("boom (errmain.lang:4)", proc.stderr)
        self.assertNotIn("errmain.lang 第 3 行", proc.stderr)

    def test_module_isolation_snapshot(self):
        self.write_program("lib/reader.lang", "@sexpr\n(defn read-main () from-main)\n")
        # 模块只看到固定内置环境（base/std），主程序全局变量无论定义在
        # import 之前还是之后都不可见；这样 import 顺序不会改变语义。
        cases = {
            "before": (
                "@sexpr\n"
                "(def from-main 5)\n"
                "(def m (import \"lib/reader.lang\"))\n"
                "(println (str (m.read-main)))\n"
            ),
            "after": (
                "@sexpr\n"
                "(def m (import \"lib/reader.lang\"))\n"
                "(def from-main 5)\n"
                "(println (str (m.read-main)))\n"
            ),
        }
        for name, text in cases.items():
            with self.subTest(order=name):
                proc = run_slang("run", self.write_program("iso_%s.lang" % name, text))
                self.assertEqual(proc.returncode, 1)
                self.assertIn("from-main", proc.stderr)

    def test_import_cache(self):
        self.write_program(
            "lib/side.lang", "@sexpr\n(println \"loaded\")\n(def value 1)\n"
        )
        main = self.write_program(
            "cachemain.lang",
            "@sexpr\n"
            "(def a (import \"lib/side.lang\"))\n"
            "(def b (import \"lib/side.lang\"))\n"
            "(println (str (= a.value b.value)))\n",
        )
        proc = run_slang("run", main)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(normalize(proc.stdout), "loaded\ntrue\n")

    def test_circular_load_detected(self):
        self.write_program("circ/a.lang", "@sexpr\n(load \"b.lang\")\n")
        self.write_program("circ/b.lang", "@sexpr\n(load \"a.lang\")\n")
        main = self.write_program("circmain.lang", "@sexpr\n(load \"circ/a.lang\")\n")
        proc = run_slang("run", main)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("循环加载", proc.stderr)

    def test_bom_and_shebang(self):
        bom = self.write_bytes("bom.lang", b"\xef\xbb\xbf@sexpr\n(println (no-such))\n")
        proc = run_slang("run", bom)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("第 2 行", proc.stderr)
        self.assertNotIn("第 3 行", proc.stderr)

        shebang = self.write_program(
            "shebang.lang",
            "#!/usr/bin/env slang\n@sexpr\n(println (str (if 1 \"yes\" \"no\")))\n",
        )
        proc = run_slang("run", shebang)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, "yes\n")

    def test_header_same_line_code_rejected(self):
        path = self.write_program("sameline.lang", "@sexpr (println \"hi\")\n")
        proc = run_slang("run", path)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("格式声明必须单独占一行", proc.stderr)

    def test_empty_file_is_silent(self):
        path = self.write_program("empty.lang", "")
        proc = run_slang("run", path)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, "")
        self.assertNotIn("未声明", proc.stderr)

    def test_duplicate_format_warning_once(self):
        self.write_program("auto2.lang", "@custom\nprintln(\"hi\")\n")
        main = self.write_program(
            "warnmain.lang",
            "@sexpr\n(load \"auto2.lang\")\n(load \"auto2.lang\")\n",
        )
        proc = run_slang("run", main)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(normalize(proc.stdout), "hi\nhi\n")
        self.assertEqual(proc.stderr.count("未适配"), 1)

    def test_check_with_project(self):
        base = os.path.join(ROOT, "slang", "stdlib", "core", "base.lang.build")
        std = os.path.join(ROOT, "slang", "stdlib", "core", "std.lang.build")
        toml = self.write_program(
            "proj/slang.toml",
            "[builds]\nfiles = [%r, %r]\n" % (base, std),
        )
        good = self.write_program("proj/good.lang", "@sexpr\n(println 1)\n")
        proc = run_slang("check", "--project", toml, good)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("OK", proc.stdout)

    def test_check_continues_after_failure(self):
        good = self.write_program("good.lang", "@sexpr\n(println 1)\n")
        bad = self.write_bytes("bad_utf8_2.lang", b"@sexpr\n\xff\xfe\n")
        proc = run_slang("check", good, bad)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("OK", proc.stdout)
        self.assertIn("失败", proc.stderr)

    def test_readfile_relative_to_script(self):
        self.write_program("io/data.txt", "hello-relative\n")
        main = self.write_program("io/script.lang", "@sexpr\n(println (readfile \"data.txt\"))\n")
        proc = run_slang("run", main)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, "hello-relative\n\n")

    def test_project_discovered_from_program_path(self):
        base = os.path.join(ROOT, "slang", "stdlib", "core", "base.lang.build")
        std = os.path.join(ROOT, "slang", "stdlib", "core", "std.lang.build")
        self.write_program(
            "proj/slang.toml",
            "[builds]\nfiles = [%r, %r, \"extra.lang.build\"]\n"
            "[project]\nmain = \"main.lang\"\n" % (base, std),
        )
        self.write_program("proj/extra.lang.build", "@sexpr\n(def from-project 777)\n")
        main = self.write_program("proj/main.lang", "@sexpr\n(println (str from-project))\n")

        proc = run_slang("run", main)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, "777\n")

        # 不带文件参数时按配置的 project.main 运行
        proc = run_slang("run", cwd=os.path.dirname(main))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, "777\n")

    def test_relative_load_is_strict_to_source_directory(self):
        """相对路径只按“语句所在文件目录”解析，不再回退项目根。"""
        base = os.path.join(ROOT, "slang", "stdlib", "core", "base.lang.build")
        std = os.path.join(ROOT, "slang", "stdlib", "core", "std.lang.build")
        self.write_program(
            "strict/proj/slang.toml",
            "[project]\nmain = \"app/main.lang\"\n\n[builds]\nfiles = [%r, %r]\n"
            % (base, std),
        )
        self.write_program(
            "strict/proj/app/main.lang",
            "@sexpr\n(load \"helper.lang\")\n(println (str helper-val))\n",
        )
        # helper.lang 只在项目根存在：按新契约不会被加载到。
        self.write_program(
            "strict/proj/helper.lang", "@sexpr\n(def helper-val 999)\n"
        )
        main = os.path.join(self.tmpdir, "strict", "proj", "app", "main.lang")
        proc = run_slang("run", main)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("找不到文件", proc.stderr)
        self.assertIn("helper.lang", proc.stderr)
        self.assertNotIn("999", proc.stdout)

        # 放到同目录后即可加载。
        self.write_program(
            "strict/proj/app/helper.lang", "@sexpr\n(def helper-val 999)\n"
        )
        proc = run_slang("run", main)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, "999\n")

    def test_check_project_does_not_execute_builds(self):
        base = os.path.join(ROOT, "slang", "stdlib", "core", "base.lang.build")
        std = os.path.join(ROOT, "slang", "stdlib", "core", "std.lang.build")
        self.write_program(
            "checkexec/slang.toml",
            "[builds]\nfiles = [%r, %r, \"side.lang.build\"]\n" % (base, std),
        )
        self.write_program(
            "checkexec/side.lang.build",
            '@sexpr\n(writefile "side-effect.txt" "boom")\n(def ok 1)\n',
        )
        toml = os.path.join(self.tmpdir, "checkexec", "slang.toml")
        proc = run_slang("check", "--project", toml)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("OK", proc.stdout)
        self.assertFalse(
            os.path.exists(os.path.join(self.tmpdir, "checkexec", "side-effect.txt"))
        )

    def test_check_without_files_or_project_is_an_error(self):
        proc = run_slang("check")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("--project", proc.stderr)

    def test_check_project_only_is_allowed(self):
        base = os.path.join(ROOT, "slang", "stdlib", "core", "base.lang.build")
        std = os.path.join(ROOT, "slang", "stdlib", "core", "std.lang.build")
        toml = self.write_program(
            "onlyproj/slang.toml",
            "[builds]\nfiles = [%r, %r]\n" % (base, std),
        )
        proc = run_slang("check", "--project", toml)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("OK", proc.stdout)


class TestModuleSemantics(_TmpDirMixin, unittest.TestCase):
    def test_nested_module_member_access(self):
        self.write_program("lib/b.lang", "@sexpr\n(def x 42)\n")
        self.write_program("lib/a.lang", "@sexpr\n(def b (import \"b.lang\"))\n")
        main = self.write_program(
            "nested.lang",
            "@sexpr\n(def a (import \"lib/a.lang\"))\n(println (str a.b.x))\n",
        )
        proc = run_slang("run", main)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, "42\n")

    def test_module_member_leak_is_rejected(self):
        self.write_program("lib/m.lang", "@sexpr\n(def value 1)\n")
        main = self.write_program(
            "leak.lang",
            "@sexpr\n(def m (import \"lib/m.lang\"))\n(println (str m.println))\n",
        )
        proc = run_slang("run", main)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("没有成员 'println'", proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)

    def test_module_member_must_exist(self):
        self.write_program("lib/m.lang", "@sexpr\n(def value 1)\n")
        main = self.write_program(
            "missing.lang",
            "@sexpr\n(def m (import \"lib/m.lang\"))\n(println (str m.secret))\n",
        )
        proc = run_slang("run", main)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("没有成员 'secret'", proc.stderr)

    def test_module_cannot_reach_program_globals(self):
        self.write_program("lib/r.lang", "@sexpr\n(defn get () from-main)\n")
        main = self.write_program(
            "iso.lang",
            "@sexpr\n"
            "(def from-main 5)\n"
            "(def m (import \"lib/r.lang\"))\n"
            "(println (str (m.get)))\n",
        )
        proc = run_slang("run", main)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("from-main", proc.stderr)

    def test_module_import_order_does_not_matter(self):
        self.write_program("lib/r.lang", "@sexpr\n(defn get () from-main)\n")
        for order, text in (
            ("before", "(def from-main 5)\n(def m (import \"lib/r.lang\"))\n"),
            ("after", "(def m (import \"lib/r.lang\"))\n(def from-main 5)\n"),
        ):
            with self.subTest(order=order):
                path = self.write_program(
                    "iso_%s.lang" % order,
                    "@sexpr\n%s(println (str (m.get)))\n" % text,
                )
                proc = run_slang("run", path)
                self.assertEqual(proc.returncode, 1)
                self.assertIn("from-main", proc.stderr)


class TestLexerBackends(_TmpDirMixin, unittest.TestCase):
    def _lex(self, path, no_native=False):
        args = (["--no-native"] if no_native else []) + ["lex", path]
        proc = run_slang(*args)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout)

    def _tokens(self, path, no_native=False):
        return self._lex(path, no_native)["tokens"]

    def _assert_backends_match(self, path):
        cpp = self._lex(path)
        py = self._lex(path, no_native=True)
        self.assertEqual(cpp["tokens"], py["tokens"], path)
        return cpp

    def test_cpp_matches_python(self):
        proc = run_slang("version")
        if not cpp_available():
            self.skipTest("C++ 核心未启用（未编译或无法加载）")
        for rel in (
            "examples/hello.lang",
            "examples/auto_format_demo.lang",
            "slang/stdlib/core/base.lang.build",
            "slang/stdlib/core/std.lang.build",
        ):
            with self.subTest(file=rel):
                self._assert_backends_match(os.path.join(ROOT, rel))

    def test_cpp_matches_python_edge_cases(self):
        proc = run_slang("version")
        if not cpp_available():
            self.skipTest("C++ 核心未启用（未编译或无法加载）")
        cases = {
            "edge_bigint.lang": "@sexpr\n(println 12345678901234567890)\n",
            "edge_floats.lang": (
                "@sexpr\n(println 1.)\n(println .5)\n(println +1)\n"
                "(println 1.0)\n(println 1e400)\n(println -0.0)\n"
            ),
            "edge_comma.lang": "@sexpr\n(println 1 , 2)\n",
            "edge_zeros.lang": "@sexpr\n(println 00)\n(println 007)\n(println -00.5)\n(println 00e2)\n",
            "edge_unicode.lang": "@sexpr\n(println ١٢٣)\n",
            "edge_escapes.lang": '@sexpr\n(println "a\\qb")\n(println "a\\u0041b")\n(println "nul:\\0end")\n',
        }
        for name, text in cases.items():
            with self.subTest(file=name):
                self._assert_backends_match(self.write_program(name, text))
        for name, data in {
            "edge_nul_str.lang": b"@sexpr\n(println \"a\x00b\")\n",
            "edge_nul_sym.lang": b"@sexpr\n(println a\x00b)\n",
        }.items():
            with self.subTest(file=name):
                self._assert_backends_match(self.write_bytes(name, data))

    def test_numeric_fidelity(self):
        proc = run_slang("version")
        if not cpp_available():
            self.skipTest("C++ 核心未启用（未编译或无法加载）")
        tokens = self._tokens(
            self.write_program(
                "nums.lang",
                "@sexpr\n(println 12345678901234567890)\n"
                "(println 1.0)\n(println 1e400)\n",
            )
        )
        kinds = [(t[0], t[1]) for t in tokens if t[0] == "NUM"]
        self.assertEqual(kinds[0], ("NUM", 12345678901234567890))
        self.assertIsInstance(kinds[1][1], float)
        self.assertEqual(kinds[1], ("NUM", 1.0))
        # lex 输出是严格 JSON：非有限值用 "Infinity"/"NaN" 字符串表示。
        self.assertEqual(kinds[2][1], "Infinity")

    def test_lex_line_numbers_follow_file(self):
        path = self.write_program("hdr.lang", "@sexpr\n(println 1)\n")
        tokens = self._tokens(path)
        self.assertEqual(tokens[0][2], 2)

        path = self.write_program(
            "hdr2.lang", "#!/usr/bin/env slang\n@sexpr\n(println 1)\n"
        )
        tokens = self._tokens(path)
        self.assertEqual(tokens[0][2], 3)

    def test_random_differential(self):
        proc = run_slang("version")
        if not cpp_available():
            self.skipTest("C++ 核心未启用（未编译或无法加载）")
        from slang import lexer, native
        from slang.errors import LexError

        rng = random.Random(20261020)
        alphabet = "()[]{};,#\"'\\ \r\n\t+-*/<>=!_.%0123456789abcxyzABC\x00"
        mismatches = []
        for mode in ("auto", "sexpr"):
            for _ in range(300):
                src = "".join(
                    rng.choice(alphabet) for _ in range(rng.randrange(1, 60))
                )
                try:
                    py_tokens = lexer.lex(src, mode, "<fuzz>")
                    py_error = None
                except LexError as exc:
                    py_tokens, py_error = None, str(exc)
                cpp_tokens = native.lex(src, mode)
                if py_error is None:
                    if cpp_tokens != py_tokens:
                        mismatches.append((mode, src, py_tokens, cpp_tokens))
                elif cpp_tokens is not None:
                    mismatches.append((mode, src, py_error, cpp_tokens))
                if len(mismatches) >= 3:
                    break
            if len(mismatches) >= 3:
                break
        self.assertEqual(mismatches, [])

    def test_lex_output_is_strict_json(self):
        path = self.write_program(
            "inf.lang", "@sexpr\n(println 1e400)\n(println 1.5)\n"
        )
        proc = run_slang("lex", path)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        text = proc.stdout.strip()
        # 严格 JSON：不允许裸 Infinity / NaN
        self.assertNotIn("Infinity,", text.replace('"Infinity"', ""))
        data = json.loads(text)
        nums = [t[1] for t in data["tokens"] if t[0] == "NUM"]
        self.assertEqual(nums[0], "Infinity")
        self.assertEqual(nums[1], 1.5)

    def test_bare_cr_is_normalized(self):
        path = self.write_bytes("cr.lang", b"@sexpr\r(println 1)\r")
        proc = run_slang("run", path)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, "1\n")

    def test_header_column_numbers(self):
        path = self.write_program(
            "hdrcol.lang", "@sexpr\n(println (no-such-name))\n"
        )
        proc = run_slang("run", path)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("第 2 行", proc.stderr)
        self.assertIn("第 11 列", proc.stderr)


class TestPackaging(unittest.TestCase):
    def test_builtin_builds_match_project_builds(self):
        """标准库只有一份：slang/stdlib/core，slang.toml 指向它。"""
        with open(os.path.join(ROOT, "slang.toml"), "r", encoding="utf-8") as fh:
            config = fh.read()
        for name in ("base.lang.build", "std.lang.build"):
            with self.subTest(file=name):
                path = os.path.join(ROOT, "slang", "stdlib", "core", name)
                self.assertTrue(os.path.exists(path), path)
                self.assertIn("slang/stdlib/core/" + name, config)
        self.assertFalse(os.path.exists(os.path.join(ROOT, "builds")))

    def test_builtin_runtime_provides_primitives_without_project(self):
        """没有 slang.toml 时，内置 base/std 仍让 println 可用。"""
        work = os.path.join(TMP_ROOT, "slang-packaging-%d" % os.getpid())
        os.makedirs(work, exist_ok=True)
        path = os.path.join(work, "standalone.lang")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("@sexpr\n(println (str (+ 1 2)))\n")
        env = dict(os.environ)
        env["PYTHONPATH"] = ROOT + os.pathsep + env.get("PYTHONPATH", "")
        proc = subprocess.run(
            [sys.executable, "-m", "slang", "run", path],
            cwd=work, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8",
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, "3\n")

    def test_version_reports_both_backends(self):
        proc = run_slang("version")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("0.5.0", proc.stdout)
        if cpp_available():
            self.assertIn("0.5.0-cpp", proc.stdout)
        proc = run_slang("version", "--no-native")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("已禁用", proc.stdout)


class TestFfiModule(_TmpDirMixin, unittest.TestCase):
    """ffi 模块：显式 (import "ffi")，支持直接导入与配置文件导入。"""

    def test_ffi_not_loaded_automatically(self):
        path = self.write_program(
            "noffi.lang", "@sexpr\n(println (ffi.version))\n"
        )
        proc = run_slang("run", path)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("未定义的名称", proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)

    def test_ffi_not_in_project_builds(self):
        with open(os.path.join(ROOT, "slang.toml"), "r", encoding="utf-8") as fh:
            text = fh.read()
        self.assertNotIn('"ffi"', text)

    def test_python_direct_import(self):
        path = self.write_program(
            "ffipy.lang",
            "@sexpr\n"
            '(def ffi (import "ffi"))\n'
            '(def m (ffi.python "math"))\n'
            "(println (str (m.sqrt 16)))\n"
            "(println (str m.pi))\n"
            '(def j (ffi.python "json"))\n'
            "(println (j.dumps (list 1 2 3)))\n"
            '(println (type (j.loads "{\\"a\\": 1}")))\n'
            '(println (str (len (j.loads "{\\"a\\": 1, \\"b\\": 2}"))))\n',
        )
        proc = run_slang("run", path)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(
            normalize(proc.stdout),
            "4\n3.141592653589793\n[1, 2, 3]\ndict\n2\n",
        )

    def test_python_object_attributes_and_callback(self):
        path = self.write_program(
            "ffiobj.lang",
            "@sexpr\n"
            '(def ffi (import "ffi"))\n'
            '(def dt (ffi.python "datetime"))\n'
            "(def now (dt.datetime.now))\n"
            "(println (str (> now.year 2000)))\n"
            '(def ft (ffi.python "functools"))\n'
            "(println (str (ft.reduce (fn (a b) (+ a b)) (list 1 2 3 4))))\n",
        )
        proc = run_slang("run", path)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(normalize(proc.stdout), "true\n10\n")

    def test_python_config_import_rename_and_exports(self):
        self.write_program(
            "math.ffi.toml",
            'kind = "python"\n'
            'module = "math"\n'
            'exports = ["sqrt", "pi"]\n'
            "\n"
            "[rename]\n"
            '"根号" = "sqrt"\n',
        )
        path = self.write_program(
            "fficonfig.lang",
            "@sexpr\n"
            '(def ffi (import "ffi"))\n'
            '(def m (ffi.import "math.ffi.toml"))\n'
            "(println (str (m.根号 81)))\n"
            "(println (str m.pi))\n"
            "(println (str (m.cos 0)))\n",
        )
        proc = run_slang("run", path)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("9\n3.141592653589793\n", normalize(proc.stdout))
        self.assertIn("没有成员", proc.stderr)
        self.assertIn("fficonfig.lang 第 6 行", proc.stderr)

    def test_python_two_arg_config_override(self):
        self.write_program(
            "math2.ffi.toml",
            'kind = "python"\n'
            'module = "math"\n'
            'exports = ["sqrt"]\n'
            '\n'
            '[rename]\n'
            '"根号" = "sqrt"\n',
        )
        path = self.write_program(
            "ffitwo.lang",
            "@sexpr\n"
            '(def ffi (import "ffi"))\n'
            '(def m (ffi.python "math" "math2.ffi.toml"))\n'
            "(println (str (m.根号 144)))\n",
        )
        proc = run_slang("run", path)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(normalize(proc.stdout), "12\n")

    def test_python_file_import(self):
        self.write_program(
            "mymod.py",
            "def double(x):\n"
            "    return x * 2\n"
            "\n"
            "VALUE = 7\n",
        )
        self.write_program(
            "mymod.ffi.toml", 'kind = "python"\nmodule = "mymod.py"\n'
        )
        path = self.write_program(
            "ffifile.lang",
            "@sexpr\n"
            '(def ffi (import "ffi"))\n'
            '(def m (ffi.import "mymod.ffi.toml"))\n'
            "(println (str (m.double 21)))\n"
            "(println (str m.VALUE))\n",
        )
        proc = run_slang("run", path)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(normalize(proc.stdout), "42\n7\n")

    def test_python_exception_has_position(self):
        path = self.write_program(
            "ffierr.lang",
            "@sexpr\n"
            '(def ffi (import "ffi"))\n'
            '(def m (ffi.python "math"))\n'
            '(println (str (m.sqrt "x")))\n',
        )
        proc = run_slang("run", path)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("Python 调用", proc.stderr)
        self.assertIn("TypeError", proc.stderr)
        self.assertIn("ffierr.lang 第 4 行", proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)

    @unittest.skipUnless(_HAS_C_LIBS, "需要 libm / libc")
    def test_c_direct_import(self):
        path = self.write_program(
            "ffic.lang",
            "@sexpr\n"
            '(def ffi (import "ffi"))\n'
            '(def m (ffi.c "m"))\n'
            "(println (str (m.sqrt 25)))\n"
            "(println (str (m.pow 2 10)))\n"
            "(println (str (m.floor 3.9)))\n"
            '(def c (ffi.c "c"))\n'
            '(println (str (c.strlen "hello")))\n'
            '(println (str (c.atoi "42")))\n'
            "(println (str (> (c.getpid) 0)))\n",
        )
        proc = run_slang("run", path)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(
            normalize(proc.stdout), "5\n1024\n3\n5\n42\ntrue\n"
        )

    @unittest.skipUnless(_HAS_C_LIBS, "需要 libm / libc")
    def test_c_config_struct_and_varargs(self):
        self.write_program(
            "libc.ffi.toml",
            'kind = "c"\n'
            'library = "c"\n'
            "uses-default = false\n"
            "\n"
            "[structs.div_t]\n"
            'fields = [["quot", "int"], ["rem", "int"]]\n'
            "\n"
            "[functions.div]\n"
            'restype = "struct:div_t"\n'
            'args = ["int", "int"]\n'
            "\n"
            "[functions.strlen]\n"
            'restype = "size_t"\n'
            'args = ["char*"]\n'
            "\n"
            "[functions.printf]\n"
            'restype = "int"\n'
            'args = ["char*"]\n'
            "varargs = true\n",
        )
        path = self.write_program(
            "fficonfigc.lang",
            "@sexpr\n"
            '(def ffi (import "ffi"))\n'
            '(def c (ffi.import "libc.ffi.toml"))\n'
            "(def r (c.div 17 5))\n"
            '(println (concat (str r.quot) "/" (str r.rem)))\n'
            "(def r2 (c.div_t 9 4))\n"
            '(ffi.set-field r2 "quot" 99)\n'
            "(println (str r2.quot))\n"
            '(println (str (c.strlen "hello")))\n'
            '(c.printf "ffi-printf:%d\\n" 7)\n',
        )
        proc = run_slang("run", path)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = normalize(proc.stdout)
        self.assertIn("3/2\n99\n5\n", out)
        self.assertIn("ffi-printf:7", out)

    @unittest.skipUnless(_HAS_C_LIBS, "需要 libm / libc")
    def test_c_two_arg_config_override(self):
        self.write_program(
            "libc2.ffi.toml",
            'kind = "c"\n'
            'library = "c"\n'
            'uses-default = false\n'
            '\n'
            '[functions.strlen]\n'
            'restype = "size_t"\n'
            'args = ["char*"]\n',
        )
        path = self.write_program(
            "ffitwoc.lang",
            "@sexpr\n"
            '(def ffi (import "ffi"))\n'
            '(def c (ffi.c "c" "libc2.ffi.toml"))\n'
            '(println (str (c.strlen "abcdef")))\n',
        )
        proc = run_slang("run", path)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(normalize(proc.stdout), "6\n")

    @unittest.skipUnless(_HAS_C_LIBS, "需要 libm / libc")
    def test_c_malloc_pointer_at(self):
        path = self.write_program(
            "ffiptr.lang",
            "@sexpr\n"
            '(def ffi (import "ffi"))\n'
            '(def c (ffi.c "c"))\n'
            "(println (str (> (c.time (ffi.null)) 0)))\n"
            "(def p (c.malloc 8))\n"
            '(c.memcpy p "abc" 4)\n'
            '(println (ffi.at p 0 "char"))\n'
            '(println (ffi.at p 2 "char"))\n'
            "(println (str (> (ffi.address p) 0)))\n"
            "(c.free p)\n",
        )
        proc = run_slang("run", path)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(normalize(proc.stdout), "true\na\nc\ntrue\n")

    @unittest.skipUnless(_HAS_ZLIB, "需要 zlib")
    def test_c_bind_symbol_at_runtime(self):
        path = self.write_program(
            "ffibind.lang",
            "@sexpr\n"
            '(def ffi (import "ffi"))\n'
            '(def z (ffi.c "z"))\n'
            '(ffi.bind z "zlibVersion" "char*" (list))\n'
            "(println (str (> (len (z.zlibVersion)) 0)))\n"
            '(ffi.bind z "compressBound" "ulong" (list "ulong") "边界")\n'
            "(println (str (> (z.边界 1000) 0)))\n",
        )
        proc = run_slang("run", path)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(normalize(proc.stdout), "true\ntrue\n")

    @unittest.skipUnless(_HAS_C_LIBS, "需要 libm / libc")
    def test_c_members_and_close(self):
        path = self.write_program(
            "fficlose.lang",
            "@sexpr\n"
            '(def ffi (import "ffi"))\n'
            '(def m (ffi.c "m"))\n'
            "(println (str (> (len (ffi.members m)) 10)))\n"
            "(ffi.close m)\n"
            "(println (str (m.sqrt 4)))\n",
        )
        proc = run_slang("run", path)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("true\n", normalize(proc.stdout))
        self.assertIn("已关闭", proc.stderr)

    def test_default_config_is_valid_toml(self):
        path = self.write_program(
            "ffidefault.lang",
            '@sexpr\n(def ffi (import "ffi"))\n(println (ffi.default-config))\n',
        )
        proc = run_slang("run", path)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        config_path = proc.stdout.strip()
        self.assertTrue(os.path.isfile(config_path), config_path)
        try:
            import tomllib
        except ImportError:  # Python < 3.11
            tomllib = None
        if tomllib is not None:
            with open(config_path, "rb") as fh:
                data = tomllib.load(fh)
            self.assertIn("python", data)
            self.assertIn("libm", data["libs"])
            self.assertIn("libc", data["libs"])

    def test_bad_config_reports_error(self):
        cases = {
            "missing_module.toml": 'kind = "python"\n',
            "bad_type.toml": (
                'kind = "c"\nlibrary = "c"\n\n'
                "[functions.abs]\n"
                'restype = "int"\n'
                'args = ["nope"]\n'
            ),
        }
        for name, text in cases.items():
            with self.subTest(name=name):
                self.write_program(name, text)
                path = self.write_program(
                    "ffibad.lang",
                    "@sexpr\n"
                    '(def ffi (import "ffi"))\n'
                    '(ffi.import "%s")\n' % name,
                )
                proc = run_slang("run", path)
                self.assertEqual(proc.returncode, 1)
                self.assertNotIn("Traceback", proc.stderr)
        # 缺 module
        proc = run_slang(
            "run", self.write_program(
                "ffibad2.lang",
                '@sexpr\n(def ffi (import "ffi"))\n(ffi.import "missing_module.toml")\n',
            )
        )
        self.assertIn("缺少 module", proc.stderr)
        # 未知 C 类型
        proc = run_slang(
            "run", self.write_program(
                "ffibad3.lang",
                '@sexpr\n(def ffi (import "ffi"))\n(ffi.import "bad_type.toml")\n',
            )
        )
        self.assertIn("未知的 C 类型", proc.stderr)


class TestStdlibModules(_TmpDirMixin, unittest.TestCase):
    def test_python_wrappers(self):
        path = self.write_program(
            "stdlib_python.lang",
            """@sexpr
(def os (import "python.os"))
(def sys (import "python.sys"))
(def json (import "python.json"))
(def re (import "python.re"))
(def time (import "python.time"))
(def random (import "python.random"))
(def pathlib (import "python.pathlib"))
(def math (import "python.math"))
(println (concat "os=" (str (len (os.getenv "HOME")))))
(println (concat "sys=" (str (> (len sys.platform) 0))))
(println (concat "json=" (json.dumps (json.loads "{\\\"a\\\": 1}"))))
(println (concat "re=" (str (re.findall "\\\\d+" "a12b34"))))
(time.sleep 0.001)
(println (concat "time=" (str (> (time.time) 0))))
(println (concat "random=" (str (>= (random.randint 1 6) 1))))
(println (concat "pathlib=" (str (pathlib.exists "README.md"))))
(println (concat "math=" (str (math.sqrt 9))))
""",
        )
        proc = run_slang("run", path)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = normalize(proc.stdout)
        self.assertIn("sys=true", out)
        self.assertIn('json={"a": 1}', out)
        self.assertIn('re=["12" "34"]', out)
        self.assertIn("time=true", out)
        self.assertIn("random=true", out)
        self.assertIn("pathlib=true", out)
        self.assertIn("math=3", out)

    def test_c_wrappers(self):
        if not _HAS_C_LIBS:
            self.skipTest("缺少 libc/libm")
        path = self.write_program(
            "stdlib_c.lang",
            """@sexpr
(def math (import "c.math"))
(def stdlib (import "c.stdlib"))
(def string (import "c.string"))
(def stdio (import "c.stdio"))
(println (concat "sqrt=" (str (math.sqrt 16))))
(println (concat "strlen=" (str (string.strlen "hello"))))
(println (concat "atoi=" (str (stdlib.atoi "42"))))
(stdio.printf "printf=%d\\n" 7)
""",
        )
        proc = run_slang("run", path)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = normalize(proc.stdout)
        self.assertIn("sqrt=4", out)
        self.assertIn("strlen=5", out)
        self.assertIn("atoi=42", out)
        self.assertIn("printf=7", out)

    def test_stdlib_alias_and_local_precedence(self):
        local = self.write_program(
            "python.math",
            "@sexpr\n(def value 123)\n",
        )
        path = self.write_program(
            "stdlib_alias.lang",
            """@sexpr
(def a (import "std.python.os"))
(def b (import "python.math"))
(println (str (len (a.getenv "HOME"))))
(println (str b.value))
""",
        )
        proc = run_slang("run", path)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = normalize(proc.stdout).splitlines()
        self.assertTrue(out[0].isdigit())
        self.assertEqual(out[1], "123")

    def test_web_handle_routing(self):
        path = self.write_program(
            "web_handle.lang",
            """@sexpr
(def web (import "flask"))
(def app (web.app "demo"))
(web.get app "/" (fn (req) (web.text "hello")))
(web.get app "/user/<name>" (fn (req) (web.text (concat "user=" (web.param req "name")))))
(web.get app "/n/<int:n>" (fn (req) (web.json (list (list "n" (+ (web.param req "n") 1))))))
(web.get app "/q" (fn (req) (web.text (concat "q=" (web.query req "q" "none")))))
(web.post app "/echo" (fn (req) (web.json (web.json_body req))))
(def r1 (web.handle app "GET" "/"))
(println (concat "r1=" (web.response_text r1)))
(def r2 (web.handle app "GET" "/user/alice"))
(println (concat "r2=" (web.response_text r2)))
(def r3 (web.handle app "GET" "/n/41"))
(println (concat "r3=" (web.response_text r3)))
(def r4 (web.handle app "GET" "/q?q=abc"))
(println (concat "r4=" (web.response_text r4)))
(def r5 (web.handle app "POST" "/echo" "{\\\"x\\\": 2}"))
(println (concat "r5=" (web.response_text r5)))
(def r6 (web.handle app "GET" "/missing"))
(println (concat "r6=" (str (web.response_status r6))))
""",
        )
        proc = run_slang("run", path)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = normalize(proc.stdout)
        self.assertIn("r1=hello", out)
        self.assertIn("r2=user=alice", out)
        self.assertIn('r3=[["n", 42]]', out)
        self.assertIn("r4=q=abc", out)
        self.assertIn('r5={"x": 2}', out)
        self.assertIn("r6=404", out)

    def test_web_templates_static_hooks_and_session(self):
        template = self.write_program(
            "templates/index.html",
            "<h1>{{ title }}</h1>"
            "<ul>{% for item in items %}<li>{{ item }}</li>{% endfor %}</ul>"
            "{% if show %}<p>shown</p>{% else %}<p>hidden</p>{% endif %}"
            "<p>{{ raw|safe }}</p><p>{{ title|upper }}</p>"
            "<p>{{ missing|default:\"fallback\" }}</p>",
        )
        static = self.write_program("static/a.txt", "static-content\n")
        template_dir = os.path.dirname(template)
        static_dir = os.path.dirname(static)
        path = self.write_program(
            "web_full.lang",
            """@sexpr
(def web (import "flask"))
(def ffi (import "ffi"))
(def builtins (ffi.python "builtins"))
(def app (web.app "demo" %s %s))
(web.cors app "*")
(web.get app "/" (fn (req) (web.render_kv app "index.html" "title" "Hello" "items" (list "a" "b") "show" true "raw" "<b>x</b>")))
(web.static app "/static" %s)
(def api (web.group app "/api"))
(web.get api "/ping" (fn (req) (web.text "pong")))
(web.before app (fn (req) ()))
(web.after app (fn (req resp) (web.header resp "X-Test" "yes")))
(web.error app 404 (fn (req) (web.text "custom-404" 404)))
(web.get app "/set" (fn (req)
  (def r (web.text "set"))
  (web.session_set req r "who" "alice")))
(web.get app "/get" (fn (req) (web.text (concat "who=" (web.session_get req "who" "none")))))
(def r1 (web.handle app "GET" "/"))
(println (concat "r1=" (web.response_text r1)))
(def r2 (web.handle app "GET" "/static/a.txt"))
(println (concat "r2=" (web.response_text r2)))
(def r3 (web.handle app "GET" "/nope"))
(println (concat "r3=" (str (web.response_status r3)) " " (web.response_text r3)))
(def r4 (web.handle app "GET" "/api/ping"))
(println (concat "r4=" (web.response_text r4)))
(def r5 (web.handle app "GET" "/set"))
(def cookie (first (split (first (web.response_cookies r5)) ";")))
(def headers (builtins.dict (list (list "Cookie" cookie))))
(def r6 (web.handle_headers app "GET" "/get" "" headers))
(println (concat "r6=" (web.response_text r6)))
""" % (json.dumps(template_dir), json.dumps(static_dir), json.dumps(static_dir)),
        )
        proc = run_slang("run", path)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = normalize(proc.stdout)
        self.assertIn("<h1>Hello</h1>", out)
        self.assertIn("<li>a</li><li>b</li>", out)
        self.assertIn("<p>shown</p>", out)
        self.assertIn("<p><b>x</b></p>", out)
        self.assertIn("<p>HELLO</p>", out)
        self.assertIn("<p>fallback</p>", out)
        self.assertIn("r2=static-content", out)
        self.assertIn("r3=404 custom-404", out)
        self.assertIn("r4=pong", out)
        self.assertIn("r6=who=alice", out)

    def test_web_real_http_server(self):
        path = self.write_program(
            "web_server.lang",
            """@sexpr
(def ffi (import "ffi"))
(def web (import "flask"))
(def app (web.app "demo"))
(web.get app "/" (fn (req) (web.text "hello-http")))
(def server (web.start app "127.0.0.1" 0))
(def urllib (ffi.python "urllib.request"))
(def resp (urllib.urlopen (web.url server)))
(println (str (resp.read)))
(web.stop server)
""",
        )
        proc = run_slang("run", path)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("hello-http", proc.stdout)


class TestSlangFormat(_TmpDirMixin, unittest.TestCase):
    def _program(self, text, name="main.slang"):
        return self.write_program(name, text)

    def test_slang_demo_runs(self):
        path = self._program(
            "# `.slang` 综合演示\n"
            "def answer = 42\n"
            "def add(a, b) = a + b\n"
            "def fib(n):\n"
            "    if n < 2:\n"
            "        n\n"
            "    else:\n"
            "        fib(n - 1) + fib(n - 2)\n"
            "def classify(n):\n"
            "    if n < 0:\n"
            "        \"negative\"\n"
            "    elif n == 0:\n"
            "        \"zero\"\n"
            "    else:\n"
            "        \"positive\"\n"
            "println(f\"fib={fib(10)} answer={answer}\")\n"
            "println(classify(-1) + \",\" + classify(0) + \",\" + classify(2))\n"
            "xs = [1, 2, 3, 4]\n"
            "println(str(map(square, xs)))\n"
            "println(str(reduce(add, 0, xs)))\n"
            "println(f\"xs[1]={xs[1]} len={len(xs)}\")\n"
            "def pick(a, b, flag) = a if flag else b\n"
            "println(pick(\"A\", \"B\", 1 < 2))\n"
        )
        proc = run_slang("run", path)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertNotIn("未声明格式", proc.stderr)
        self.assertEqual(
            normalize(proc.stdout),
            "fib=55 answer=42\n"
            "negative,zero,positive\n"
            "[1 4 9 16]\n"
            "10\n"
            "xs[1]=2 len=4\n"
            "A\n",
        )

    def test_slang_header_in_lang_file(self):
        path = self.write_program(
            "header.lang",
            "@slang\n"
            "def x = 2\n"
            "println(str(x * 3))\n",
        )
        proc = run_slang("run", path)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(normalize(proc.stdout), "6\n")

    def test_sexpr_header_overrides_slang_extension(self):
        path = self.write_program(
            "override.slang",
            "@sexpr\n"
            '(println "sexpr wins")\n',
        )
        proc = run_slang("run", path)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(normalize(proc.stdout), "sexpr wins\n")
        self.assertNotIn("未声明格式", proc.stderr)
        proc = run_slang("check", path)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("模式 sexpr", proc.stdout)

    def test_slang_import_and_module_isolation(self):
        self._program(
            "def hidden = \"lib\"\n"
            "def name() = hidden\n"
            "def square(x) = x * x\n",
            name="lib.slang",
        )
        main = self._program(
            "import \"lib.slang\" as lib\n"
            "from \"lib.slang\" import square\n"
            "def hidden = \"main\"\n"
            "println(f\"{lib.name()} {lib.hidden} {hidden} {square(4)}\")\n",
            name="main.slang",
        )
        proc = run_slang("run", main)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(normalize(proc.stdout), "lib lib main 16\n")

    def test_slang_and_lang_can_mix(self):
        self._program("def triple(x) = x * 3\n", name="triple.slang")
        self._program("@sexpr\n(defn quad (x) (* x 4))\n", name="quad.lang")
        lang_main = self._program(
            "@sexpr\n"
            '(load "triple.slang")\n'
            '(println (triple 5))\n',
            name="main.lang",
        )
        proc = run_slang("run", lang_main)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(normalize(proc.stdout), "15\n")

        slang_main = self._program(
            'import "quad.lang" as q\n'
            'import "triple.slang" as t\n'
            'println(str(q.quad(5) + t.triple(2)))\n',
            name="main2.slang",
        )
        proc = run_slang("run", slang_main)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(normalize(proc.stdout), "26\n")

    def test_slang_macros(self):
        path = self._program(
            "defmacro unless(cond, body):\n"
            "    if cond:\n"
            "        nil\n"
            "    else:\n"
            "        body\n"
            "unless(false, println(\"yes\"))\n"
            "unless(true, println(\"no\"))\n"
        )
        proc = run_slang("run", path)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(normalize(proc.stdout), "yes\n")

    def test_slang_control_flow_and_blocks(self):
        path = self._program(
            "x = 3\n"
            "if x > 5:\n"
            "    println(\"big\")\n"
            "elif x == 3:\n"
            "    println(\"three\")\n"
            "else:\n"
            "    println(\"other\")\n"
            "when x == 3:\n"
            "    println(\"when\")\n"
            "do:\n"
            "    println(\"a\")\n"
            "    println(\"b\")\n"
            "println(str(1 if x == 3 else 0))\n"
            "println(str(not false and true or false))\n"
            "if true:\n"
            "    def branch_value = 9\n"
            "println(str(branch_value))\n"
        )
        proc = run_slang("run", path)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(
            normalize(proc.stdout),
            "three\nwhen\na\nb\n1\ntrue\n9\n",
        )

    def test_slang_operator_precedence(self):
        path = self._program(
            "println(str(1 + 2 * 3))\n"
            "println(str((1 + 2) * 3))\n"
            "println(str(7 % 4))\n"
            "println(str(2 * 3 + 4 * 5))\n"
            "println(str(10 - 3 - 2))\n"
            "println(str(1 < 2 and 2 < 3))\n"
            "println(str(1 == 1))\n"
            "println(str(-2 * -3))\n"
            "println(str(map(not, [true, false])))\n"
        )
        proc = run_slang("run", path)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(
            normalize(proc.stdout), "7\n9\n3\n26\n5\ntrue\ntrue\n6\n[false true]\n"
        )

    def test_slang_strings_and_interpolation(self):
        path = self._program(
            "println(\"double\")\n"
            "println('single')\n"
            "println(\"\"\"triple\nline\"\"\")\n"
            "println(r\"raw\\n\")\n"
            "println(\"escaped:\\t|\")\n"
            "println(f\"{{x}} {1 + 2}\")\n"
            "println(f\"{ 1 + 2 }\")\n"
            "println(f\"{'}'}\")\n"
            "println(f\"{1 if true else 0}\")\n"
            "println(f\"\\x41\\u4e2d\")\n"
            "println(f'{join([\"a\", \"b\"], \"-\")}')\n"
        )
        proc = run_slang("run", path)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(
            normalize(proc.stdout),
            "double\nsingle\ntriple\nline\nraw\\n\nescaped:\t|\n{x} 3\n3\n}\n1\nA中\na-b\n",
        )

    def test_slang_raw_string_backslashes(self):
        path = self._program(
            'println(r"\\\\")\n'
            'println(r"\\"")\n'
            'println(r"\\n")\n'
        )
        proc = run_slang("run", path)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(
            normalize(proc.stdout),
            "\\\\\n" "\\\"\n" "\\n\n",
        )

    def test_slang_numbers_and_continuation(self):
        path = self._program(
            "println(str(0x10 + 0b101 + 0o7))\n"
            "println(str(1_000_000))\n"
            "println(str(1.5e2))\n"
            "println(\n"
            "    \"a\" +\n"
            "    \"b\"\n"
            ")\n"
            "def sum3(a, b, c) = a +\n"
            "    b +\n"
            "    c\n"
            "println(str(sum3(1, 2, 3)))\n"
        )
        proc = run_slang("run", path)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(
            normalize(proc.stdout), "28\n1000000\n150\nab\n6\n"
        )

    def test_slang_assignment_and_variadic(self):
        path = self._program(
            "x = 1\n"
            "x = x + 1\n"
            "def y = 10\n"
            "set y = y + 5\n"
            "def show(a, rest...):\n"
            "    println(str(a))\n"
            "    println(str(rest))\n"
            "show(1, 2, 3)\n"
            "println(f\"{x} {y}\")\n"
        )
        proc = run_slang("run", path)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(normalize(proc.stdout), "1\n[2 3]\n2 15\n")

    def test_slang_nested_function_and_inline_body(self):
        path = self._program(
            "def outer(x):\n"
            "    def inner(y) = x + y\n"
            "    inner(10)\n"
            "def double(x): x * 2\n"
            "if true: println(\"inline\")\n"
            "println(str(outer(5)))\n"
            "println(str(double(4)))\n"
        )
        proc = run_slang("run", path)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(normalize(proc.stdout), "inline\n15\n8\n")

    def test_slang_build_file(self):
        base = os.path.join(ROOT, "slang", "stdlib", "core", "base.lang.build")
        std = os.path.join(ROOT, "slang", "stdlib", "core", "std.lang.build")
        cfg = self.write_program(
            "slangproj/slang.toml",
            "[builds]\nfiles = [%r, %r, \"extra.slang\"]\n"
            "[project]\nmain = \"main.slang\"\n" % (base, std),
        )
        self.write_program(
            "slangproj/extra.slang",
            "def from_slang_build = 21\n",
        )
        main = self.write_program(
            "slangproj/main.slang",
            "println(str(from_slang_build * 2))\n",
        )
        proc = run_slang("run", main)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(normalize(proc.stdout), "42\n")

        proc = run_slang("run", cwd=os.path.dirname(main))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(normalize(proc.stdout), "42\n")

        proc = run_slang("check", "--project", cfg, main)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("OK", proc.stdout)

    def test_slang_relative_load(self):
        self._program("def shared = 5\n", name="lib.slang")
        main = self._program(
            "load \"lib.slang\"\n"
            "println(str(shared))\n",
            name="main.slang",
        )
        proc = run_slang("run", main)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(normalize(proc.stdout), "5\n")

    def test_slang_decode_roundtrip(self):
        source = (
            "def fib(n):\n"
            "    if n < 2:\n"
            "        n\n"
            "    else:\n"
            "        fib(n - 1) + fib(n - 2)\n"
            "xs = [1, 2, 3]\n"
            "println(f\"fib={fib(6)} sum={reduce((fn (a, b) = a + b), 0, xs)}\")\n"
        )
        path = self._program(source)
        decoded = os.path.join(self.tmpdir, "decoded.lang")
        proc = run_slang("decode", path, "-o", decoded, "--width", "40")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        with open(decoded, "r", encoding="utf-8") as fh:
            text = fh.read()
        self.assertTrue(text.startswith("@sexpr\n"))
        self.assertIn("(defn fib (n)", text)
        proc = run_slang("run", decoded)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(normalize(proc.stdout), "fib=8 sum=6\n")

    def test_slang_decode_special_floats(self):
        path = self._program(
            "a = 1e400\n"
            "b = -1e400\n"
            "c = 1e400 - 1e400\n"
            "println(str(a))\n"
            "println(str(b))\n"
            "println(str(c))\n"
        )
        decoded = os.path.join(self.tmpdir, "special.lang")
        proc = run_slang("decode", path, "-o", decoded)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        with open(decoded, "r", encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn('(num "inf")', text)
        self.assertNotRegex(text, r"\(set [abc] (?:-?inf|nan)\)")
        proc = run_slang("run", decoded)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(normalize(proc.stdout), "inf\n-inf\nnan\n")

    def test_slang_check_and_lex(self):
        path = self._program(
            "def x = 1\n"
            "if x:\n"
            "    println(str(x))\n"
        )
        proc = run_slang("check", path)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("格式 @slang", proc.stdout)
        self.assertIn("模式 slang", proc.stdout)
        self.assertIn("词法 slang", proc.stdout)
        proc = run_slang("lex", path)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["mode"], "slang")
        self.assertEqual(payload["backend"], "slang")
        kinds = [t[0] for t in payload["tokens"]]
        self.assertIn("NEWLINE", kinds)
        self.assertIn("INDENT", kinds)
        self.assertIn("DEDENT", kinds)

    def test_slang_block_lambda(self):
        path = self._program(
            "f = fn(x):\n"
            "    if x > 0:\n"
            "        x\n"
            "    else:\n"
            "        -x\n"
            "println(str(f(-3)))\n"
            "println(str(f(2)))\n"
            "def g(x) = fn(y):\n"
            "    x + y\n"
            "println(str(g(10)(5)))\n"
            "def outer(x):\n"
            "    addx = fn(y):\n"
            "        x + y\n"
            "    addx(5)\n"
            "println(str(outer(7)))\n"
        )
        proc = run_slang("run", path)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(normalize(proc.stdout), "3\n2\n15\n12\n")

    def test_slang_unicode_names_and_crlf(self):
        path = os.path.join(self.tmpdir, "unicode.slang")
        with open(path, "wb") as fh:
            fh.write(
                "def 平方(x) = x * x\r\n"
                "println(str(平方(6)))\r\n".encode("utf-8")
            )
        proc = run_slang("run", path)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(normalize(proc.stdout), "36\n")

    def test_slang_empty_and_comments_only(self):
        for name, text in (("empty.slang", ""), ("comment.slang", "# only\n")):
            path = self._program(text, name=name)
            proc = run_slang("run", path)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(proc.stdout, "")
            self.assertNotIn("未声明格式", proc.stderr)

    def test_slang_indent_errors(self):
        cases = [
            ("bad_dedent.slang",
             "def f():\n"
             "    if true:\n"
             "        println(\"a\")\n"
             "      println(\"b\")\n",
             "缩进不匹配"),
            ("mixed.slang",
             "if true:\n"
             "\t println(\"x\")\n",
             "混用"),
            ("top_indent.slang",
             "    println(\"x\")\n",
             "意外的缩进"),
            ("empty_block.slang",
             "def f():\n",
             "缩进块"),
        ]
        for name, text, needle in cases:
            path = self._program(text, name=name)
            proc = run_slang("run", path)
            self.assertEqual(proc.returncode, 1, (name, proc.stdout))
            self.assertIn(needle, proc.stderr, name)

    def test_slang_syntax_errors(self):
        cases = [
            ("no_colon.slang", "if true\n    println(\"x\")\n", "需要 ':'"),
            ("else.slang", "else:\n    println(\"x\")\n", "必须紧跟在 if"),
            ("return.slang", "def f(x):\n    return x\n", "没有 return"),
            ("eq.slang", "if 1 = 2:\n    println(\"x\")\n", "相等比较请用 '=='"),
            ("for.slang", "for x in [1]:\n    println(str(x))\n", "没有内置循环"),
            ("unclosed.slang", "println([1, 2\n", "括号未闭合"),
            ("chain.slang", "println(str(1 < 2 < 3))\n", "链式比较"),
            ("slice.slang", "xs = [1, 2]\nprintln(str(xs[0:1]))\n", "不支持切片"),
            ("kwargs.slang", "println(str(1), end=\"\")\n", "不支持关键字参数"),
            ("default.slang", "def f(x = 1) = x\n", "不支持参数默认值"),
            ("destructure.slang", "a, b = [1, 2]\n", "不支持解构赋值"),
            ("brace.slang", "x = {1: 2}\n", "花括号"),
        ]
        for name, text, needle in cases:
            path = self._program(text, name=name)
            proc = run_slang("run", path)
            self.assertEqual(proc.returncode, 1, (name, proc.stdout))
            self.assertIn(needle, proc.stderr, name)
            self.assertNotIn("Traceback", proc.stderr)


class TestTomlFallback(unittest.TestCase):
    def test_mini_parser(self):
        from slang.tomlcompat import _mini_loads

        data = _mini_loads(
            "# 注释\n"
            "[project]\n"
            'name = "slang # not comment" # 行内注释\n'
            'version = "0.2.0"\n'
            "count = 1_000\n"
            "hex = 0x10\n"
            '"a.b" = 4\n'
            "\n"
            "[builds]\n"
            "files = [\n"
            '  "a",  # 第一项\n'
            '  "b",\n'
            "]\n"
            "meta.info = 3\n"
        )
        self.assertEqual(data["project"]["name"], "slang # not comment")
        self.assertEqual(data["project"]["version"], "0.2.0")
        self.assertEqual(data["project"]["count"], 1000)
        self.assertEqual(data["project"]["hex"], 16)
        self.assertEqual(data["project"]["a.b"], 4)
        self.assertEqual(data["builds"]["files"], ["a", "b"])
        self.assertEqual(data["builds"]["meta"]["info"], 3)

    def test_mini_parser_rejects_duplicate_keys(self):
        from slang.tomlcompat import _mini_loads

        with self.assertRaises(ValueError):
            _mini_loads("a = 1\na = 2\n")


if __name__ == "__main__":
    unittest.main()
