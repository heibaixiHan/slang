# slang 语言

一个为「适配不同编程项目」而设计的极简编程语言（v0.5.0）。

- **双前端**：`.lang` 是严格 S 表达式；`.slang` 是缩进 + 中缀 + f-string 的人类友好格式。两者生成同一棵 AST，`slang decode` 可把 `.slang` 导出为等价的 `.lang`（规范见 `docs/slang-format.md`）。
- **底层**：Python 负责解析/求值/加载等复杂且易变的部分；C++ 负责稳定、性能敏感的词法分析（编译一次，长期使用）。
- **自举**：核心只提供 `%` 开头的原子操作。`def`、`if`、`add`、`println` 这些“语言本身”全部由 `builds/base.lang.build` 定义。
- **权限极高**：环境里没有任何受保护的条目。后加载的 `.lang.build` 直接覆盖先加载的同名定义；程序自己也能覆盖任何东西。标准库内部改用 `%` 原语实现关键操作，因此覆盖 `=`、`if`、`+` 不会意外弄坏 `map`/`reduce` 等（见「覆盖与绑定时机」；`.slang` 的语法关键字在语法层保留，详见格式规范）。
- **多格式**：文件首行用 `@格式名` 声明语法；`.slang` 扩展名自动识别，未适配的格式由通用转换器兜底，并在运行前警告。
- **两种加载模式**：覆盖（override）与模块化导入（import，模块只看到固定的 base/std 快照，实现真隔离）。
- **可定位的错误**：AST 节点携带源文件，相对加载按“语句所在文件”解析；报错带文件/行/列与调用链。
- **可审计**：内置 `log` 模块必须显式 `(import "log")` 才会启用，能把定义/调用/加载/打印/错误写成 JSON Lines 日志。

---

## 快速开始

```sh
# 运行示例（自动发现项目：优先按程序文件目录，其次当前目录向上查找 slang.toml）
python3 -m slang run examples/hello.lang

# 人类友好格式：缩进 + 中缀 + f-string
python3 -m slang run examples/slang_demo.slang

# 把 .slang 解码成等价的 .lang（输出带 @sexpr 头，可直接运行）
# 注意：demo 里有相对 import，解码文件要放在源文件同目录，否则相对路径会变
python3 -m slang decode examples/slang_demo.slang -o examples/slang_demo.decoded.lang
python3 -m slang run examples/slang_demo.decoded.lang
rm examples/slang_demo.decoded.lang

# 或者用包装脚本（自动设置 PYTHONPATH，可在任何目录下运行；/sdcard 是 noexec，需用 sh 调用）
sh bin/slang run examples/hello.lang

# 只检查语法（--project 可同时校验项目配置与构建文件，只解析不执行）
python3 -m slang check examples/lists.lang
python3 -m slang check examples/slang_demo.slang
python3 -m slang check --project slang.toml examples/lists.lang
python3 -m slang check --project slang.toml

# 版本 / 是否启用 C++ 核心（--no-native 放在顶层或子命令后都可以）
python3 -m slang version
python3 -m slang version --no-native

# 编译 C++ 核心（可选）
make -C core

# 运行全部测试（make test 会先编译 C++ 核心）
make test

# 只跑纯 Python 词法器的测试
make test-python

# 也可安装为命令（pyproject.toml；安装后可直接用 slang）
pip install -e .
slang run examples/hello.lang
```

运行项目外的单文件时：若文件所在目录（或它的父目录）没有 `slang.toml`，运行器会退回当前目录的项目；两者都没有时，会加载**随包发布的内置 base/std**（`slang/stdlib/builds/`），因此 `println`、`def` 等依然可用：

```sh
python3 -m slang run "$TMPDIR/plain.lang"      # Termux 无 /tmp；项目外建议用绝对路径
python3 -m slang --project /path/to/slang.toml run /path/to/program.lang
```

### 项目配置 `slang.toml`

```toml
[project]
name = "slang语言"
version = "0.5.0"
main = "examples/hello.lang"

[builds]
# 按数组顺序依次加载；后加载者直接覆盖先加载的同名定义
files = [
  "builds/base.lang.build",
  "builds/std.lang.build",
]
```

配置里的路径以 `slang.toml` 所在目录为基准；配置结构会做类型校验（`[builds]`、`builds.files`、`[project]`、`project.main`），未知键会给出警告。

注意：`log` 模块**不在** `builds.files` 里，也永远不会自动加载——它只能通过 `(import "log")` 显式引入（见「运行日志」）。

---

## 执行流程

```
1. 定位 slang.toml：--project 显式指定 > 程序文件目录向上查找 > 当前目录向上查找
2. 按 builds.files 顺序加载 .lang.build（直接覆盖模式，同一个全局环境）
       └─ base.lang.build：用 % 原语自举出 def / if / fn / add / println ...
       └─ std.lang.build ：标准库（内部关键操作直接用 % 原语）
   （找不到 slang.toml 时改为加载随包发布的内置 base/std，见 slang/stdlib/builds/）
3. 运行程序文件（同一环境，因此程序可以覆盖任意已有定义）
       └─ 程序里还可以再用 load / import / load-config 动态加载
4. 程序内的相对路径（load / import / load-config / readfile / writefile / log.start）
   以“写这条语句的源文件目录”为基准，文件位置固定，不随调用点漂移；
   不会回退到项目根或当前工作目录
```

### 覆盖语义

`examples/override_demo.lang` 演示了程序内的覆盖模式：

```lisp
(load "examples/override_packs/pack_a.lang.build")   ; add = ["A", 和]
(println (add 1 2))                                  ; => ["A" 3]
(load "examples/override_packs/pack_b.lang.build")   ; 后加载，直接覆盖
(println (add 1 2))                                  ; => ["B" 3]
```

没有“内置保护”“只读定义”这种概念：连 `println`、`if` 都可以被重新定义。

### 覆盖与绑定时机

- **别名是定义时快照**：`(def add %add)` 记录的是当时那个原语对象；之后再覆盖 `%add` 不会改变 `add`。
- **函数体里的名字在调用时查找**：覆盖 `println` 后，之前定义的函数也会调用新版本。
- **标准库有依赖隔离**：`std.lang.build` 内部直接使用 `%ifc`/`%eq`/`%add` 等原语，所以 `(defn = (a b) 1)`、`(def if (fn (c a b) a))`、覆盖 `+` 都不会弄坏 `map`/`filter`/`reduce`/`sum` 等；直接覆盖 `map`/`reduce` 本身依然立即生效。

### 模块化导入

```lisp
(def math (import "examples/modules/math.lang.build"))
(println (math.square 6))   ; 36
(println math.pi)           ; 3.14159
```

- 模块只看到一份**固定的内置快照**（base/std + 启动配置里加载的构建定义），这份快照在配置加载完成后冻结；主程序定义的全局变量（无论在 import 之前还是之后定义）**模块都看不到**。
- 因此 import 顺序不会改变模块里的自由变量，语义稳定；模块之间要协作必须显式 `import`。
- 模块里的定义不会污染当前环境；访问方式为 `模块名.名字`，支持多级 `a.b.c`。
- 成员访问只查模块**自身**的导出：`m.println`、`m.secret` 会报「模块里没有成员」，不会意外泄漏内置定义。
- 定义名/赋值名不能包含 `.`（点号保留给模块成员访问）：`(def m.value 7)` 会直接报错。
- 重复 `import` 会返回同一个模块（按真实路径缓存），模块顶层只执行一次；没有显式 reload 语法。
- 循环 `load`/`import` 会被检测并报「循环加载」。

### 程序内加载配置

```lisp
(load-config "other-project/slang.toml")  ; 按该配置的 builds.files 顺序加载（覆盖模式）
```

路径以 `slang.toml` 为基准；加载完成后 `project_root` 会恢复，不会永久改写当前项目根。在函数里调用时，加载进来的定义会落在当前模块/全局环境，而不是函数临时帧。

---

## 运行日志（`log` 模块）

`log` 是**内置模块**，但和其他标准库不同：它**不会**出现在 `slang.toml` 的 `builds.files` 里，也**不会**被自动加载。只有显式 `import` 之后它才存在（类似 Python 3 里 `import json` 才拿到 `json` 模块）。

```lisp
(def lg (import "log"))     ; 必须显式 import
(lg.start "run.jsonl")      ; 打开日志文件（默认截断；相对路径按源文件目录解析）
(lg.info "阶段完成" "data") ; 手动事件
(lg.event "自定义事件" 42)
(lg.stop)                   ; 关闭（进程退出时也会自动关闭）
```

| 接口 | 说明 |
| --- | --- |
| `(log.start 路径 [字符上限] [模式])` | 打开日志；上限默认 500 字符，`0` 表示不截断；模式 `"w"`（默认）或 `"a"`（追加） |
| `(log.stop)` | 关闭日志 |
| `(log.enabled)` | 当前是否正在记录 |
| `(log.path)` | 当前日志文件路径（未启动时是空串） |
| `(log.event 名称 [数据])` / `(log.record ...)` | 记录一条自定义事件 |
| `(log.info 消息 [数据])` | 记录 info 级别事件 |
| `(log.warn 消息 [数据])` | 记录 warn 级别事件 |
| `(log.error 消息 [数据])` | 记录 error 级别事件 |
| `(log.debug 消息 [数据])` | 记录 debug 级别事件 |

日志是 **JSON Lines**（一行一个 JSON 对象，逐行 flush），覆盖的行为包括：

| event | 记录内容 |
| --- | --- |
| `log.open` / `log.close` | 日志文件的打开与关闭 |
| `run.start` / `run.end` | 一次 `run` 的开始与结束 |
| `form` | 每个被求值的顶层表达式（源码 + 行号） |
| `call` | 函数/原语调用（名字、参数、结果、位置） |
| `print` | 打印内容 |
| `load.start` / `load.end` / `import.start` / `import.end` / `import.builtin` | 加载与导入 |
| `config.load` / `runtime.builtin` | 配置与内置运行时的加载 |
| `log.info` / `log.warn` / `log.error` / `log.debug` / `log.event` | 手动事件 |
| `error` | 运行期错误（消息 + 位置） |

示例输出：

```json
{"ts": "2026-10-20T22:19:27.878+08:00", "pid": 1234, "event": "log.open", "path": "/tmp/run.jsonl", "mode": "w", "limit": "500"}
{"ts": "2026-10-20T22:19:27.879+08:00", "pid": 1234, "event": "call", "name": "println", "args": ["hi"], "result": [], "file": "/tmp/a.lang", "line": "3"}
```

未启动日志就调用 `log.info` 等接口会得到带文件/行/列的普通错误（而不是 traceback）。

完整示例见 `examples/log_demo.lang`：

```sh
python3 -m slang run examples/log_demo.lang   # 生成 examples/log_demo.jsonl
```

---

## 外部库（`ffi` 模块）

`ffi` 是**内置模块**，同样不会自动加载，也不在 `slang.toml` 的 `builds.files` 里；必须显式 `(import "ffi")`。它把 Python 模块和 C 动态库包装成 slang 里的“模块”，用同样的 `名字.成员` 语法访问。

支持两种导入模式：

| 模式 | 写法 | 转译规则来自 |
| --- | --- | --- |
| 直接导入 | `(ffi.python "math")` / `(ffi.c "m")` | 随包默认兼容配置 `slang/stdlib/builds/ffi_default.toml`；Python 自动发现公开成员，常见 C 库（libm/libc）使用内置签名表 |
| 配置文件导入 | `(ffi.import "spec.toml")` | 由 TOML 指定 `kind`、模块/库名、导出、重命名、C 函数签名、结构体等 |

### Python

```lisp
(def ffi (import "ffi"))

; 直接导入：自动发现公开成员
(def math (ffi.python "math"))
(math.sqrt 2)          ; 1.4142135623730951
math.pi                ; 3.141592653589793

; 对象属性与方法链
(def dt (ffi.python "datetime"))
(def now (dt.datetime.now))
now.year               ; 2026

; 配置文件导入
(def j (ffi.import "examples/ffi/json.ffi.toml"))
(j.编码 (list 1 2 3))  ; "[1, 2, 3]"
```

Python 规格文件：

```toml
kind = "python"
module = "json"                  # 也可以是相对规格文件的 .py 路径
exports = ["dumps", "loads"]     # 只导出这些 Python 名；省略则全部公开成员

[options]
include-private = false          # 是否允许 _ 开头的成员（__ 始终不允许）
max-items = 1000                 # 容器最多转换多少项
max-depth = 4                    # 容器最大递归深度
bytes-encoding = "utf-8"         # bytes <-> str
deny = ["__loader__"]            # 始终隐藏的名字

[rename]
"编码" = "dumps"                 # 额外暴露的 slang 别名
```

值转换：`None→[]`、`bool/int/float/str` 原样、`bytes→str`、`list/tuple/set→list`、`dict→dict`、类/对象/函数→可继续访问成员或调用的不透明句柄。slang 的 `fn` 可以作为 Python 回调（例如 `functools.reduce`）。`(ffi.null)` 表示 `None`。

### C

```lisp
(def ffi (import "ffi"))

; 直接导入：使用默认配置里的 libm/libc 签名
(def m (ffi.c "m"))
(m.sqrt 25)            ; 5
(m.pow 2 10)           ; 1024

; 默认签名表之外的库：运行时补充绑定
(def z (ffi.c "z"))
(ffi.bind z "zlibVersion" "char*" (list))
(z.zlibVersion)        ; "1.3.2"
(ffi.bind z "compressBound" "ulong" (list "ulong") "边界")
(z.边界 1000)          ; 1013

(def c (ffi.c "c"))
(c.strlen "hello")     ; 5
(c.getpid)             ; 进程号

; 配置文件导入：自定义签名、结构体、重命名
(def libc (ffi.import "examples/ffi/libc.ffi.toml"))
(def r (libc.div 17 5))
r.quot                 ; 3
r.rem                  ; 2
```

C 规格文件：

```toml
kind = "c"
library = "c"            # 库名、路径或绝对路径；相对路径按规格文件目录解析
uses-default = false     # true 时合并默认配置里同名库（libm/libc）的签名

[structs.div_t]          # 字段可以引用前面已声明的结构体或 [types] 别名
fields = [["quot", "int"], ["rem", "int"]]

[types]
real = "double"          # 自定义类型别名

[functions.div]
restype = "struct:div_t"
args = ["int", "int"]

[functions."根号"]       # 非 ASCII 的 TOML 键要加引号
symbol = "sqrt"          # slang 名 -> C 符号名
restype = "double"
args = ["double"]

[functions.printf]
restype = "int"
args = ["char*"]
varargs = true           # 可变参数：固定部分按 args 转换，多余参数按数字/字符串/指针处理
```

支持的类型：`void`、`bool`、`char`、`int8/16/32/64`、`uint8/16/32/64`、`short/ushort`、`int/uint`、`long/ulong`、`size_t/ssize_t`、`float/double`、`char*`/`string`、`void*`/`ptr`、`T*`（列表按输入数组转换；返回值是带类型的指针）、`struct:名字`。结构体按值返回时可用 `r.字段` 读取，用 `(ffi.set-field r "字段" 值)` 修改；`(句柄.div_t 3 2)` 按字段顺序构造结构体。

### 接口一览

| 接口 | 说明 |
| --- | --- |
| `(ffi.python 模块名 [配置文件])` | 导入 Python 模块（第二个参数可覆盖默认配置） |
| `(ffi.c 库名/路径 [配置文件])` | 导入 C 动态库（第二个参数可覆盖默认配置） |
| `(ffi.import 规格文件)` | 按 TOML 规格导入，`kind = "python"` 或 `"c"` |
| `(ffi.close 句柄)` | 关闭句柄；关闭后成员访问/调用报错（C 库只解除绑定，进程内不强制卸载） |
| `(ffi.members 句柄)` | 列出可访问的成员名 |
| `(ffi.bind 句柄 "符号" "返回类型" ["参数类型"...] ["成员名"])` | 给已导入的 C 句柄补充绑定一个符号（不写成员名时与符号同名） |
| `(ffi.call 句柄 "名字" 参数...)` | 按字符串名字调用（适合符号名不能写成 slang 标识符的情况） |
| `(ffi.null)` | Python `None` / C `NULL` |
| `(ffi.address 指针)` | 取指针/结构体的整数地址 |
| `(ffi.at 指针 下标 ["类型"])` | 读取指针/数组元素；整数地址必须给类型 |
| `(ffi.set-field 对象 "字段" 值)` | 写 C 结构体字段 |
| `(ffi.default-config)` | 返回随包默认兼容配置的路径 |
| `(ffi.version)` | 返回 slang 版本 |

错误模型：Python 异常、找不到符号、类型不匹配、签名配置错误都会转成带文件/行/列的普通 slang 错误（不会打印 traceback）；每个句柄用 `ffi.close` 标记关闭。

**安全边界**：导入外部库等于获得该库的全部能力（Python 可以执行任意代码，C 可以访问内存）。slang 不提供沙箱；只导入你信任的库和配置。

完整示例见 `examples/ffi_demo.lang`：

```sh
python3 -m slang run examples/ffi_demo.lang
```

---

## 两种源码格式

| | `.lang` | `.slang` |
| --- | --- | --- |
| 定位 | 严格 S 表达式，适合机器生成/精确控制 | 缩进、中缀、f-string，适合手写 |
| 识别 | 首行 `@sexpr`（推荐）或自动模式 | 扩展名 `.slang`，或 `.lang` 首行 `@slang` |
| 块 | 括号嵌套 | 缩进 |
| 运算 | `(+ a b)` | `a + b` |
| 解码 | — | `slang decode x.slang -o x.lang` |

同一段逻辑：

```lisp
@sexpr
(defn fib (n)
  (if (< n 2)
      n
      (+ (fib (- n 1)) (fib (- n 2)))))
(println (fib 20))
```

```slang
def fib(n):
    if n < 2:
        n
    else:
        fib(n - 1) + fib(n - 2)

println(f"fib(20) = {fib(20)}")
```

`.slang` 不是第二套运行时：它直接被解码成与 `.lang` 相同的 AST。已有 `.lang` 的项目、构建文件和模块无需改动，可以和 `.slang` 混用（`import "m.slang"`、`load "x.slang"` 都可以）。完整语法、降级映射和限制见 [`docs/slang-format.md`](docs/slang-format.md)。

---

## 文件格式与自动转换

格式声明必须**单独占一行**（可以在前面写 shebang 或留空行）；声明行后同一行再写代码会直接报错，不会被静默丢弃。UTF-8 BOM 会被自动跳过。

```lisp
@sexpr
(println "hello")
```

| 声明 | 处理方式 |
| --- | --- |
| `@sexpr`（也接受 `@lisp`、`@scheme`） | 严格 S 表达式（不接受 `[]` `{}` 与逗号） |
| `@slang` | 缩进式人类友好格式（`.slang` 扩展名自动使用，无需声明） |
| `@auto` / `@generic` | 通用宽松转换器（显式选择，不警告） |
| 未声明 / 未适配（如 `@lua`） | 通用宽松转换器 + **运行前 stderr 警告**（空文件/纯注释不警告） |

通用转换器支持：

- `f(a, b)` 形式的紧邻调用
- `[]`、`{}` 与 `()` 一样作为分组；单元素括号是纯分组（`(1) + 2` → 3），不是零参调用
- 逗号分隔、`;` / `#` 注释；顶层表达式之间也可以用逗号分隔
- `+ - * / % < <= > >= == !=` 中缀运算；**顶层表达式也支持中缀**（`1 + 2`）
- 优先级与 C/Python 一致：`== !=` < `< <= > >=` < `+ -` < `* / %`，同级左结合
- 运算符两侧需要空格（`1 + 2` 可以，`1+2` 会整体当成符号名）
- `+` `-` `*` `/` `%` 的连续链会被折叠成一个 n 元调用，不会因嵌套变深而栈溢出

示例（`examples/auto_format_demo.lang`）：

```
@some-future-format
print("来自自动转换格式的问候\n")
def(total, 1 + 2 * 3)
print("total = ")
print(total)
```

运行时会先看到：

```
警告: .../auto_format_demo.lang: 格式 '@some-future-format' 未适配，已使用通用自动转换器（可能导致语义偏差）
```

**新增格式适配器**：注册表在 `slang/formats.py`（`register_format(name, mode)`），`mode` 目前支持 `sexpr` / `slang` / `auto`；`.slang` 前端的词法、语法与解码在 `slang/slangfmt.py`。所有前端共享同一棵 AST，所以语义层不需要改。

---

## 语言速览

```lisp
; 变量 / 函数（函数体可以写多条表达式，返回最后一条的值）
(def x 10)
(defn add3 (a b c) (+ a b c))
(defn log-and-add (a b)
  (println "adding")
  (+ a b))
(def inc (fn (n) (+ n 1)))
(def "字符串名字" 7)          ; 名称也可以是字符串字面量

; 赋值：set 沿环境链更新已有绑定（闭包状态可变），找不到才新建
(def counter 0)
(defn bump () (do (set counter (+ counter 1)) counter))

; 条件（只求值选中的分支；and/or 短路并返回实际值）
(if (< x 0) "负数" "非负")
(when (> x 0) (println "正数"))     ; 条件为假时返回 []（假值）
(and 0 1)                          ; => 0
(or 5 6)                           ; => 5

; 列表
(def xs (list 1 2 3))
(println (first xs))          ; 1
(println (rest xs))           ; [2 3]
(println (cons 0 xs))         ; [0 1 2 3]
(println (len xs))            ; 3
(println (nth xs 1))          ; 2

; 标准库（builds/std.lang.build；由批量原语实现，可处理十万级列表）
(range 0 5)                   ; [0 1 2 3 4]
(map square (range 1 4))      ; [1 4 9]
(filter (fn (x) (> x 2)) xs)  ; 谓词自定
(reduce + 0 (range 1 101))    ; 5050
(take 2 xs) (drop 2 xs) (last xs)

; 字符串 / 列表日常操作（原语实现）
(sort (list 3 1 2))                     ; [1 2 3]
(sort (list 1 2 3) (fn (a b) (- b a)))  ; [3 2 1]（比较函数返回数字）
(split "a,b,c" ",")                     ; ["a" "b" "c"]
(join (list "a" "b") "-")               ; "a-b"
(substr "hello" 1 3)                    ; "el"
(substr "hello" -3)                     ; "llo"

; 字符串 / 输出
(println (concat "x = " (str x)))
(print "不换行")
```

**真值规则**：`0`、`""`、`[]`、`nil` 为假，其余为真。`nil` 就是空列表 `()`；布尔字面量 `true`/`false` 是真正的布尔类型，`(= true (= 1 1))` 为 `true`。

**变长参数**：参数名以 `...` 结尾（如 `(%lambda (xs...) ...)`），函数体内 `xs` 和 `xs...` 都能访问到参数列表；变长参数必须是最后一个且不能为空名。

**整数位数上限**：默认允许 10 万位整数（`SLANG_INT_MAX_DIGITS` 可调整）。超过上限时给出可读错误「整数太大（超过 N 位上限）」，不会退化成 `inf` 或 traceback。

---

## 核心原语（`%` 开头）

核心只提供这些原子；它们本身也在同一个环境里，可以被覆盖（不推荐，但允许）。

| 类别 | 原语 |
| --- | --- |
| 宏式（收原始 AST） | `%eval` `%bind` `%set` `%lookup` `%cond` `%ifc` `%do` `%lambda` `%lazy` `%quote` `%deref` |
| 数字 | `%add` `%sub` `%mul` `%div` `%mod` `%neg` |
| 比较/逻辑 | `%eq` `%ne` `%lt` `%le` `%gt` `%ge` `%not` `%and` `%or` |
| 字符串/列表 | `%str` `%num` `%concat` `%len` `%type` `%list` `%first` `%rest` `%cons` `%nth` `%append` `%sort` `%split` `%join` `%substr` |
| 批量列表（迭代实现） | `%range` `%map` `%filter` `%reduce` `%take` `%drop` `%apply` |
| IO/杂项 | `%print` `%readfile` `%writefile` `%error` |
| 加载 | `%load` `%loadconfig` `%import` |

`%eval` 是“宏展开”工具：先在当前环境求值得到节点，再在调用者环境求值该节点（因此 `def`/`if` 可以用普通函数 + 原语实现）。

`base.lang.build` 由此给出：`def` `set` `fn` `defn` `defmacro` `if` `when` `and` `or` `nil` `true` `false`、算术/比较运算符、`list/first/rest/...`、`sort/split/join/substr`、`println`、`load/import/load-config`、`do` 等。

`std.lang.build` 再给出：`range` `map` `filter` `reduce` `sum` `square` `not=` `last` `take` `drop`。

---

## 性能

0.5.0 对解释器热路径做了优化，语义不变。参考数据来自 `bench/bench.py`（同一台 Termux/Android 设备、Python 3.13、重复 3 次取最好）：

| 用例 | 0.4.0 基线 | 0.5.0 | 提升 |
| --- | --- | --- | --- |
| `fib(24)` | 3.79s | 1.05s | 3.6× |
| `reduce` 20 万 | 2.39s | 0.81s | 3.0× |
| `map` 10 万 | 2.28s | 0.81s | 2.8× |
| `join` 10 万 | 0.41s | 0.22s | 1.9× |
| 启动 hello | ~0.20s | ~0.18s | — |

Android 的 CPU 调度/降频会让绝对值相差数倍；比较优化效果时，请在同一轮、同一温度状态下同时跑基线版本和当前版本，不要跨轮直接比。上表是正常温度下的参考值。

```sh
python3 bench/bench.py --repeat 3
python3 bench/bench.py --case fib --repeat 5
```

主要优化：

- AST 求值按精确类型分派；非点号名字查找内联，用哨兵去掉重复 dict 查找；
- `if` 在无宏参数帧、无日志时走快速路径，跳过 `expand_macro_arg`/`_p_ifc` 包装；
- 函数调用内联参数绑定与调用栈维护，调用栈只保存 `(名字, 节点)`；
- 数字算术、比较、`eq/ne` 增加 int/float/str 快速路径；
- `apply/map/filter/reduce` 去掉逐元素可调用类型分派与多余参数复制；
- `print` 不再每次 flush；显示逻辑改精确类型分派；
- `.slang` 直接生成 AST，运行时不做“生成 `.lang` 文本再解析”。

仍是树遍历解释器，没有 JIT / 尾调用优化；启动时间主要受 Python 进程与导入影响。需要更高吞吐时，优先把真正的热点下移到 C++ / `ffi`，或把递归改成 `range`/`map`/`reduce`。

---

## 目录结构

```
slang语言/
├── slang.toml              # 项目配置（TOML）
├── slang/                  # Python 主实现
│   ├── lexer.py            # 纯 Python 词法器（行为基准）
│   ├── parser.py           # sexpr 严格解析 + auto 宽松转换
│   ├── slangfmt.py         # .slang 词法/语法/降级到 AST + 解码为 .lang
│   ├── interp.py           # 求值器 + % 原语 + 运行日志
│   ├── env.py              # 环境模型（root / module / snapshot / function / capture）
│   ├── formats.py          # @格式 声明与适配器注册表（sexpr / slang / auto）
│   ├── loader.py           # TOML / .lang.build / 程序加载（模块隔离、严格路径、循环检测）
│   ├── native.py           # C++ 核心 ctypes 桥（ABI 校验、回退、缓存管理）
│   ├── tomlcompat.py       # tomllib/tomli/内置极简解析
│   ├── cli.py              # run / check / lex / decode / version
│   └── stdlib/
│       ├── log.py          # 内置 log 模块（必须显式 import）
│       ├── ffi.py          # 内置 ffi 模块：导入 Python / C 库（必须显式 import）
│       └── builds/         # 随包发布的内置 base/std + ffi 默认兼容配置
├── builds/
│   ├── base.lang.build     # 引导层：语言从这里诞生
│   └── std.lang.build      # 标准库
├── core/
│   ├── slang_core.cpp      # C++ 词法器（C ABI v2：显式长度 + 数值原文）
│   ├── Makefile            # make -C core
│   └── CMakeLists.txt
├── docs/
│   └── slang-format.md     # .slang 格式规范（词法/语法/映射/限制）
├── bench/bench.py          # 可复现的性能基准
├── examples/               # hello / fact / lists / 覆盖 / 导入 / 自动转换 / log / ffi / slang 演示
│   ├── slang_demo.slang    # .slang 综合示例
│   ├── slang_math.slang    # 供示例导入的 .slang 模块
│   └── ffi/                # ffi 配置文件示例（json.ffi.toml / libc.ffi.toml）
├── tests/test_slang.py     # 端到端测试（121 个）
└── bin/slang               # 命令行包装器（自动解析符号链接）
```

---

## C++ 核心（ABI v2）

```sh
make -C core      # 产出 core/build/libslangcore.so
python3 -m slang version
```

- Python 自动通过 `ctypes` 加载；环境变量 `SLANG_CORE=/path/to/lib.so` 可指定其他位置（指定后只尝试这一个路径，不再自动编译/复制）。
- **ABI 校验**：库必须导出 `slang_abi_version() == 2`；版本不匹配或缺少符号时自动回退纯 Python 词法器，并在 `version` 里给出原因。
- **正确性契约**：`slang_lex(src, len, mode, out_len)` 显式传长度（NUL 是普通字符）；数字以原文交给 Python 解析（大整数保持精确、`1e400` 变 `inf`、`1.0` 不被降成整数）。
- **Android 注意**：`/sdcard` 不允许 `dlopen`，加载失败时会自动把 `.so` 复制到 `$TMPDIR` 再加载（原子写入 + 损坏重试）。缓存文件名带内容哈希（`$TMPDIR/libslangcore-<路径哈希>-<内容哈希>.so`），源码改动会自动重新编译。
- 不可用时自动回退纯 Python 词法器；测试对示例、NUL、大整数、溢出浮点等做逐 token 差分比对。
- 设计原则：**性能稳定部分进 C++，复杂/易调整部分留在 Python**。目前 C++ 只做词法，性能收益视输入而定（见 `AUDIT.md` P1-10）。

---

## 测试

```sh
python3 -m unittest discover -s tests -v

# 或者用 Makefile：test 会先重建 C++ 核心；test-python 显式禁用 C++ 核心
make test
make test-python
```

121 个端到端测试覆盖：示例输出、条件/布尔语义（裸变量、`true/false/nil`、遮蔽）、嵌套宏与宏参数名冲突、`and/or/when` 短路返回值、`set` 与闭包状态、宏环境、多表达式函数体、字符串名称、转义与续行、`sort/split/join/substr`、覆盖隔离、十万级列表、auto 模式（`1 +2`/`1 -2`/`1 %2`、空参数、顶层逗号）、重复参数名与变长参数位置、空函数体、定义名含 `.`、大整数与位数上限、NUL 路径、模块相对加载、函数内 load/load-config、import 缓存、错误定位与调用链、循环加载、模块隔离（主程序全局不可见、`a.b.c` 嵌套、成员不泄漏）、BOM/shebang、裸 CR 归一化、同行声明报错、项目发现、相对路径严格按源文件目录解析、`check --project` 只解析不执行、警告去重、readfile 相对路径、`log` 模块（显式 import / 不自动加载 / 事件内容 / 追加模式）、`ffi` 模块（显式 import / 不自动加载 / Python 直接导入与配置导入 / 重命名与导出 / 本地 .py / Python 异常定位 / Python 回调 / C 直接导入 / C 签名与结构体 / 变参 printf / malloc+指针读取 / 运行时 ffi.bind / 两参数覆盖配置 / 句柄关闭 / 默认配置合法性 / 错误配置）、`.slang` 格式（综合语法、`@slang` 头、显式 `@sexpr` 覆盖扩展名、`.lang`/`.slang` 混合加载与导入、`.slang` 构建文件、模块 import/from、宏、控制流、运算符优先级、字符串/插值、f-string 空白与花括号、`\x`/`\u` 转义、原始字符串反斜杠、数字与续行、赋值/变长参数、嵌套函数/行内块、块 lambda、相对 load、decode 往返、非有限浮点 decode、check/lex、Unicode/CRLF、空文件、缩进与语法错误）、TOML 兜底解析、内置构建与项目构建一致性，以及 C++/Python 两套词法器的逐 token 一致性（含 600 条固定种子的随机差分语料）。

---

## 已知限制 / Roadmap

- [ ] 宏系统仍是「惰性函数」：没有 quasiquote / 卫生性 / 模板参数自动替换；`%quote` 返回的 AST 可用 `(str ...)` 查看，但生成代码要靠 `%eval` 手工拼接。文档按此定位，不宣称完整宏系统。
- [ ] 深递归仍受 Python 栈限制（CLI 已把上限调到 20000，解释器内部另有 2000 层调用深度保护并给出可读错误）；`range/map/filter/reduce/take/drop` 已是迭代实现、可处理十万级列表，但用户手写的 `cons` 递归仍是 O(n²) 且受栈深限制。
- [ ] 无导出列表/选择性导入；模块缓存没有显式 reload 语法（需新进程）。模块看不到主程序全局变量，需要共享时必须显式 `import`。
- [ ] TOML 兜底解析已支持行内注释、点号键、数字下划线/十六进制与重复键检测，但仍不支持多行字符串、行内表等高级语法；优先用 `tomllib`/`tomli`。
- [ ] 字符串：`.lang` 支持 `\n \t \r \\ \" \0`；未知转义（`\q`）会**保留反斜杠**而不是静默丢弃；`\u`/`\x` 只在 `.slang` 前端支持，`.lang` 尚未实现，`\`+换行会报错。
- [ ] `.slang` 是语法糖，不引入新的运行时能力：没有 `return` / `while` / `for` / `break` / `continue`、字典字面量、切片、索引赋值、关键字参数、参数默认值、解构赋值或链式比较；函数体最后一个表达式的值就是返回值。详见 `docs/slang-format.md`。
- [ ] `check` 只解析不执行；`--project` 会校验配置与构建文件（同样不执行）；`lex` 仅用于调试，输出严格 JSON（非有限浮点写成 `"Infinity"`/`"NaN"` 字符串）。
- [ ] 求值器未迁入 C++；没有尾调用优化，也没有 `while`。
- [ ] 无 REPL；没有字典/结构体**字面量**、标准输入、异常捕获；`sort` 只支持自然序与双参数比较函数。`ffi` 可以返回 dict / C 结构体对象，但没有对应的源码字面量语法。
- [ ] `ffi` 的 C 侧不会解析头文件：直接导入只覆盖默认配置里的 libm/libc 常见符号，其他库需要写 TOML 签名或用 `ffi.bind` 逐个绑定；`T*` 返回值是带类型的指针，长度需要调用方自己知道，数组越界/悬垂指针不做检查；可变参数只支持数字/字符串/指针，结构体嵌套数组与 C++ 符号（name mangling）暂不支持。
- [ ] Python 调用只支持位置参数（没有关键字参数语法，可用 `functools.partial` 或配置文件重命名包装）；Python 的迭代器/生成器会作为不透明对象返回，不会自动展开。
- [ ] `log` 只记录解释器层面的行为（定义、调用、加载、打印、错误），不做用户级审计或回放；日志里的值会被截断到字符上限（默认 500），列表/字典最多渲染 50 项。

> 设计上的“没有任何保护”是刻意为之：slang 的定位是**项目专属方言**——你的项目需要什么样的 `add`、什么样的 `if`，就由你的 `.lang.build` 来决定。
