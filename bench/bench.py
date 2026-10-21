#!/usr/bin/env python3
"""slang 性能基准（不依赖第三方库）。

用法：
    python3 bench/bench.py
    python3 bench/bench.py --repeat 5
    python3 bench/bench.py --case fib

每个用例都在独立的子进程里运行，取多次测量的最好值与中位数。
数字随机器/Android 负载变化，只用于同一台机器上的前后对比。
"""
import argparse
import os
import statistics
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PROGRAMS = {
    "hello": ("hello.lang", "@sexpr\n(println \"hello\")\n"),
    "fib": ("fib.lang", """@sexpr
(defn fib (n)
  (if (< n 2)
      n
      (+ (fib (- n 1)) (fib (- n 2)))))
(println (fib 24))
"""),
    "reduce": ("reduce.lang", """@sexpr
(println (reduce (fn (a b) (+ a b)) 0 (range 0 200000)))
"""),
    "map": ("map.lang", """@sexpr
(defn sq (x) (* x x))
(println (reduce (fn (a b) (+ a b)) 0 (map sq (range 0 100000))))
"""),
    "join": ("join.lang", """@sexpr
(println (len (join (map str (range 0 100000)) ",")))
"""),
    "slang-fib": ("fib.slang", """def fib(n):
    if n < 2:
        n
    else:
        fib(n - 1) + fib(n - 2)
println(str(fib(24)))
"""),
}


def run_once(path, cwd):
    env = dict(os.environ)
    env["PYTHONPATH"] = ROOT + os.pathsep + env.get("PYTHONPATH", "")
    start = time.perf_counter()
    proc = subprocess.run(
        [sys.executable, "-m", "slang", "run", path],
        cwd=cwd,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    elapsed = time.perf_counter() - start
    if proc.returncode != 0:
        raise SystemExit("用例失败 %s:\n%s" % (path, proc.stderr))
    return elapsed


def main():
    ap = argparse.ArgumentParser(description="slang 性能基准")
    ap.add_argument("--repeat", type=int, default=3,
                    help="每个用例测量次数（默认 3）")
    ap.add_argument("--case", action="append", choices=sorted(PROGRAMS),
                    help="只跑指定用例，可重复")
    args = ap.parse_args()
    if args.repeat < 1:
        print("--repeat 必须 >= 1", file=sys.stderr)
        return 2

    cases = args.case or list(PROGRAMS)
    tmpdir = tempfile.mkdtemp(prefix="slang-bench-",
                              dir=os.environ.get("TMPDIR") or None)
    try:
        paths = {}
        for name, (filename, source) in PROGRAMS.items():
            path = os.path.join(tmpdir, filename)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(source)
            paths[name] = path

        print("slang 性能基准（Python %s，重复 %d 次，取最好/中位数）"
              % (sys.version.split()[0], args.repeat))
        print("%-12s %10s %10s" % ("用例", "最好", "中位数"))
        print("-" * 34)
        for name in cases:
            times = [run_once(paths[name], tmpdir) for _ in range(args.repeat)]
            print("%-12s %9.3fs %9.3fs"
                  % (name, min(times), statistics.median(times)))
    finally:
        for _name, (filename, _source) in PROGRAMS.items():
            path = os.path.join(tmpdir, filename)
            try:
                os.remove(path)
            except OSError:
                pass
        try:
            os.rmdir(tmpdir)
        except OSError:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
