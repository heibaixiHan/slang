# slang 语言审计报告（v0.1.0）

生成方式：5 个子代理独立只读审计，覆盖「求值器语义 / 词法·解析·格式 / 加载·模块·配置 / 健壮性·性能 / 设计·可用性·文档」。
主会话对全部 Critical 和绝大多数 High 做了二次复现。

图例：

- ✅ **已复核**：主会话亲自跑通复现
- 🔁 **代理实测**：子代理跑过、主会话未重跑
- ❓ **推测**：未验证

审计基线：`python3 -m unittest discover -s tests` 12/12 通过；C++ 核心（0.1.0-cpp）已加载；全部实验文件写在 `$TMPDIR`，未修改项目源码。

---

## 修复状态（v0.3.0，Batch 1–5，主会话实施）

在 0.2.0 的修复之上，继续按 Batch 1–5 推进全量审计项（不只零风险项）。版本升到 0.3.0（C++ `0.3.0-cpp`，ABI 仍为 2），`python3 -m unittest discover -s tests` **80/80 通过**（含 600 例固定种子的 C++/Python 词法差分）。

> 后续：0.4.0 新增内置 `ffi` 模块（Python/C 互操作，显式 import），测试增至 **97/97 通过**；Batch 1–5 的结论与遗留项不变，详见 `CHANGELOG.md` 与 README 的「外部库（ffi 模块）」章节。

> 后续：0.5.0 新增 `.slang` 人类友好前端与 `slang decode`，并优化解释器热路径（测试 **121/121 通过**）；`log` / `ffi` 的显式导入约束与 Batch 1–5 结论不变，详见 `CHANGELOG.md` 与 `docs/slang-format.md`。

### Batch 1：环境 / 模块模型重构 ✅

| 条目 | 修复内容 | 验证 |
| --- | --- | --- |
| P1-7 | `slang/env.py` 重写为 root / module / snapshot / function / lazy / capture / block 七类环境；模块基线 `module_base` 在配置加载完成后冻结，每个模块复制一份快照 | 测试：模块看不到主程序全局（import 前后都不行） |
| P1-7 | 模块成员访问只查模块自身 `bindings`：`m.println`/`m.secret` 报「模块里没有成员」，不再泄漏内置定义 | 测试：成员泄漏、成员缺失 |
| P1-3 | `def` 落 `caller_root`；`load`/`load-config` 落 `module_env`；宏闭包捕获时冻结参数帧并保留宏调用目标（`is_macro_binding`/`macro_target`） | 宏/闭包测试 |
| 新增 | 定义名/赋值名禁止包含 `.`（点号保留给模块成员访问） | 测试：`(def m.value 7)` 报错 |

### Batch 2：健壮性与错误信息 ✅

| 条目 | 修复内容 | 验证 |
| --- | --- | --- |
| 新增 | 整数位数上限 `SLANG_INT_MAX_DIGITS`（默认 100000），超出报「整数太大（超过 N 位上限）」而不是 `inf`/traceback；`sys.set_int_max_str_digits` 同步提升 | 测试：5000 位 `(num "…")`、20 万位超限 |
| 新增 | `_p_num` 健壮化；`%add/%sub/%mul/%div/%mod/%neg` 捕获 `OverflowError`/`ValueError`；路径含 NUL 直接报错 | 测试：NUL 路径 |
| 新增 | 重复参数名、`...` 位置与空名、空函数体、`%lambda` 仅参数表，均给出明确错误 | 测试：重复参数、变长参数位置、空 body |
| P1-3 | `LoadError` 带位置与调用链；包装已有错误时**只保留一次调用链**（此前会打印两条） | 测试：错误定位与调用链 |

### Batch 3：配置与工具链 ✅

| 条目 | 修复内容 | 验证 |
| --- | --- | --- |
| M-12 | `check --project` 改为**只解析不执行**（`loader.check_config`），不再触发 `writefile` 等副作用 | 测试：构建文件里的 `writefile` 不产生文件 |
| M-12 | `check` 的 `files` 改为 `nargs="*"`，允许只给 `--project`；两者都没有时报错退出 2 | 测试：`check` 空参数、`check --project` 单独使用 |
| M-6 | 配置 falsey/类型校验与未知键警告（`loader.parse_config`） | 代码审阅 + 加载测试 |
| M-13 | TOML 极简解析器加固：拒绝行内表/多行字符串/数组表/前导零/裸字符串，重复键与重复表报错 | 直接测试 `_mini_loads` |
| M-14 | 纯注释/shebang 不再警告（`formats.has_code`） | 测试 |
| M-5 | `--no-native` 顶层与子命令后均可写 | 手工复验 |
| 新增 | `lex` 输出严格 JSON：非有限浮点写成 `"Infinity"`/`"NaN"` 字符串 | 测试：`lex` 解析 `1e400` |

### Batch 4：词法与解析一致性 ✅

| 条目 | 修复内容 | 验证 |
| --- | --- | --- |
| M-16 | 裸 CR / CRLF 归一化（Python + C++ 两侧一致） | 测试：裸 CR 文件正常运行 |
| M-16 | header 同行代码报错并给出列号 | 测试：错误列号 |
| P1-8 | auto 顶层逗号作分隔符；空参数报错 | 测试：顶层逗号、空参数 |
| P1-8 | auto 词法器不再吞掉前导 `+/-`（`%` 作为 auto 运算符），`1 +2`/`1 -2`/`1 *2`/`1 %2` 规则统一 | 测试 + C++/Python 逐 token 差分 |
| P1-6 | `+ - * /` 链折叠为 n 元调用，防止深递归 | 差分测试（600 例） |

### Batch 5：原生核心与工程 ✅

| 条目 | 修复内容 | 验证 |
| --- | --- | --- |
| M-5 | `native.py` 重写：按内容哈希命名缓存、原子写入、缓存清理、并发锁；ABI 校验后做冒烟测试；拒绝伪 `.so`；大文件按 token 密度回退纯 Python | 手工复验 + 版本输出 |
| M-5 | 指定 `SLANG_CORE` 时只用该路径，不再自动编译/复制 | 手工复验 |
| M-7 | 加载器加 `load_lock` 串行化 + 循环加载检测 + 覆盖模式失败回滚（不留下半截定义） | 测试：循环加载 |
| M-9 | `writefile` 改原子写（临时文件 + `os.replace`） | 代码审阅 |
| 新增 | 调用深度上限 2000 层，超出给可读错误而不是 Python `RecursionError` | 代码审阅 |
| M-8 | 相对路径**严格**按“语句所在文件目录”解析，不再回退项目根/cwd；模块与文件统一用 `realpath` | 测试：项目根同名文件不再被误加载 |
| 新增 | 随包发布内置 base/std（`slang/stdlib/builds/`），无项目配置时也能运行；与项目 `builds/` 保持同步 | 测试：一致性 + 无项目运行 |
| P1-4 | `bin/slang` 逐级解析符号链接；`make test` 依赖 `core`；新增 `make test-python` | 手工复验 |

### 新增设施：内置 `log` 模块 ✅

- 只能显式 `(import "log")` 获得；**不在** `slang.toml` 的 `builds.files` 里，不会自动配置/加载（类似 Python 3 标准库）。
- 接口：`(log.start 路径 [上限] [模式])`、`(log.stop)`、`(log.enabled)`、`(log.path)`、`(log.event/record 名称 [数据])`、`(log.info/warn/error/debug 消息 [数据])`。
- 输出 JSON Lines（逐行 flush）：`log.open/close`、`run.start/end`、`form`、`call`、`print`、`load.*`/`import.*`、`config.load`、`runtime.builtin`、手动事件、`error` 全部记录；CLI 退出时自动关闭文件。
- 日志里的值按字符上限截断；列表/字典**先截断再渲染**（避免超大容器的内存峰值）；`atexit` 回调只注册一次。
- 未启动日志就调用接口 → 带文件/行/列的普通错误（不是 traceback）。

测试：模块显式 import、不自动加载、事件内容与 JSON 结构、追加模式、参数校验。

### 遗留清单（README「已知限制」已如实标注）

- **P1-1 剩余**：无 quasiquote / 卫生性 / 模板参数替换；`%quote` 结果可显示源码、可结构比较，生成代码仍靠 `%eval` 手工拼接。
- **P1-6 剩余**：用户手写 `cons` 递归仍是 O(n²) 且受 Python 栈深限制（无 TCO）。
- **P1-7 剩余**：无导出列表 / 选择性导入；模块缓存无显式 reload。
- **P1-9 部分**：已有 `sort`/`split`/`join`/`substr`；仍缺字典/结构体、标准输入、异常捕获、`while`/TCO、REPL。
- **M-13 剩余**：TOML 兜底仍不支持多行字符串、行内表等高级语法（优先 `tomllib`/`tomli`）。
- **P1-10**：C++ 仍只做词法，性能收益视输入而定（未变）。
- **L 系列**：`FALSY_STRINGS` / `CALLER_KEY` 死代码已移除；其余零散项未逐条清理。

---

## 修复状态（v0.2.0，主会话实施）

原审计内容保持不变；本节记录修复进度。版本升到 0.2.0（C++ ABI v2），`python3 -m unittest discover -s tests` **46/46 通过**（含 600 例固定种子的 C++/Python 词法差分）。

### 已修复（有测试或手工复验）

| 条目 | 修复内容 | 验证 |
| --- | --- | --- |
| P0-1 | 条件统一走惰性 `%ifc`（条件在调用者环境求值、只求值选中分支）；宏参数展开只沿 lazy 调用帧的 caller 链，不再污染词法作用域；局部参数遮蔽全局；修复用户定义名与宏参数名冲突（如 `(def value 1)`）时的二次展开 | 裸变量 / `true` / `false` / `nil` / `0` 条件、`(f 0)` 遮蔽、`value/name` 冲突，测试覆盖 |
| P0-2 | C ABI v2：`slang_lex(src,len,mode,out_len)` 显式长度（NUL 安全）、`slang_abi_version()` 校验、数字按原文交给 Python 解析（大整数精确、`1e400`→inf、`1.0` 保持浮点、前导零归一化）、两种模式都产出 COMMA、`strchr` 把 NUL 当分隔符的 bug | NUL 字符串/符号、大整数、溢出浮点、`1.`/`.5`/`+1`/`-0.0`/`00`、Unicode 数字逐 token 差分；600 例随机差分 |
| P1-2 | `set` 沿环境父链更新最近绑定、找不到才新建；新增 `%set`；闭包可变状态可用 | 全局计数与闭包计数，测试覆盖 |
| P1-3 | AST 节点带源文件；load/import/load-config/readfile/writefile 按“语句所在文件”解析；错误带 文件:行:列 + 调用链；`load-config` 后恢复 `project_root`；函数内 load/load-config 的定义落在模块/全局而非临时帧 | 模块内相对加载、模块内错误定位、readfile 相对脚本、load-config 不污染，测试覆盖 |
| P1-4 | 项目发现：`--project` > 程序文件目录向上 > cwd 向上；包装脚本任意目录可用 | 从程序路径发现项目（含 `project.main`），测试覆盖 |
| P1-5 | std 内部改用 `%` 原语，覆盖 `=`/`if`/`+` 不再弄坏 std；绑定时机（别名快照 / 调用时查找 / std 隔离）写入 README | 覆盖 `=` 与 `if` 后 map/filter/reduce 仍正确，测试覆盖 |
| P1-6 | 新增迭代批量原语 `%range/%map/%filter/%reduce/%take/%drop`；auto 运算符归约改为栈式线性折叠 | 10 万元素列表；20k 个 `+` 从 109s 降到 0.45s，测试覆盖 |
| P1-7 | import 时对当前环境做快照（真隔离，行为不随加载顺序漂移）；同一文件按真实路径缓存（顶层只执行一次）；循环 load/import 检测；多级 `a.b.c` | 快照前后顺序、import 缓存、循环加载，测试覆盖 |
| P1-8 | `@sexpr` 拒绝 `[]`/`{}`/逗号；声明必须单独占行；UTF-8 BOM 与 shebang；空文件不警告；行号与原文一致；auto 单原子括号是分组、顶层中缀、比较优先级修正 | 加载/词法/行号，测试覆盖 |
| M-1 | `true`/`false` 为真正布尔（`(= true (= 1 1))`→true、`(type true)`→bool）；nil 统一 `[]`（`(str nil)`→`[]`） | 语义测试 |
| M-2 | 原语 arity 校验统一（`%neg`/`%not`/`%str`/`%type`/`%cmp` 等），不再 traceback | 手工复验 |
| M-3 | 非法 UTF-8 → LoadError（run/check/lex/配置/readfile 全覆盖） | 手工复验 |
| M-4 | 管道关闭静默退出 141，无 traceback | 大文件 `| head` 复验 |
| M-5 | native 缓存原子写入 + 损坏重试 + ABI 校验 + 原因展示；假 .so 自动回退纯 Python | `SLANG_CORE` 指向非库文件复验 |
| M-6 | 配置 schema 类型校验（`[builds]`/`files`/`[project]`/`main`） | 代码审阅 + 加载测试 |
| M-7 | 循环加载检测（修复前为泛化「递归太深」） | 测试 |
| M-8 | `resolve_path` 去掉裸 cwd 兜底（无项目时 `project_root` 默认 cwd 仍可用） | 手工复验 |
| M-9 | `%readfile` 保留原换行（CRLF 不被改写）；IO 相对脚本目录 | readfile 测试 |
| M-10 | 函数名进入报错信息；错误 文件:行:列 + 调用链（如 `boom (errmain.lang:4)`） | 错误定位测试 |
| M-11 | auto 运算符栈式折叠（同 P1-6） | 0.45s 复验 |
| M-12 | `check` 支持 `--project`；单文件失败后继续检查其余文件；整批共用同一个解释器 | 测试覆盖 |
| M-13 | 内置 TOML 兜底解析补齐行内注释、点号键、数字下划线/十六进制、重复键检测 | 直接测试 `_mini_loads` |
| M-14 | 空文件不警告；未声明格式标签修正；同一格式警告只输出一次（去重） | 测试覆盖 |
| M-15 | 未知转义保留反斜杠（不再静默吞字符）；`\`+换行报错；`\u`/`\x` 仍未实现（README 标注） | 转义测试 + 模糊差分 |
| M-16 | header 行号不再 -1；`@sexpr` 同行代码直接报错而非吞掉 | BOM/行号/同行，测试覆盖 |

### 部分修复 / 已知遗留（README「已知限制」已如实标注）

- **P1-1 剩余**：无 quasiquote / 卫生性 / 模板参数替换；`%quote` 结果可显示源码、可结构比较（`(= (%quote a) (%quote a))`→true），生成代码仍靠 `%eval` 手工拼接。
- **P1-6 剩余**：用户手写 `cons` 递归仍是 O(n²) 且受 Python 栈深限制（无 TCO）。
- **P1-7 剩余**：无导出列表 / 选择性导入；模块缓存无显式 reload。
- **P1-9 部分**：新增 `sort`（可带比较函数）、`split`、`join`、`substr`；仍缺字典/结构体、标准输入、异常捕获、`while`/TCO、REPL。
- **M-13 剩余**：TOML 兜底仍不支持多行字符串、行内表等高级语法（优先 `tomllib`/`tomli`）。
- **L 系列**：`FALSY_STRINGS` / `CALLER_KEY` 死代码已移除，`slang_last_error` 已在 ctypes 侧绑定；其余零散项未逐条清理。
- **P1-10**：C++ 仍只做词法，性能收益视输入而定（未变）。

---

## 0. 总体结论

当前版本适合作为「可覆盖项目方言 / 教学原型」的 0.1.0，**不能**支撑真实项目脚本。原因不是零散 bug，而是四组系统性缺口：

1. **代码与值不分**：`if/and/or/when/%cond/%quote/%eval` 无法稳定区分「宏参数原始 AST」和「运行时值」。
2. **没有源位置模型**：`current_file`/`current_dir`/`project_root` 是全局动态状态，AST 节点不带文件信息 → 相对加载、错误定位、项目发现全部随调用点漂移。
3. **两套后端没有契约**：C++/Python 词法器无长度参数、无数值保真、无 ABI 校验、无边界 fuzz 测试。
4. **「一切可覆盖」没有依赖隔离**：名字晚绑定、别名快照并存；覆盖 `=`/`if`/`+`/`%bind` 会静默破坏 std/base。

再加上 stdlib 的非尾递归 + 列表复制，语言在约 1400 个元素规模就崩溃。

---

## 1. P0（Critical）

### P0-1 条件与布尔体系不可用 ✅

**位置**：`slang/interp.py:292-315`（`deref_node`/`_p_ifc`）、`builds/base.lang.build:24-34`

**复现**：

```lisp
@sexpr
(def flag 1)
(println (if flag "yes" "no"))       ; → 无法求值的节点: 1
(println (if true 1 2))              ; → 无法求值的节点: 1
(println (if false 1 2))             ; → 无法求值的节点: 0
(println (if nil 1 2))               ; → 无法求值的节点: []

(def x 99)
(defn f (x) (if x "yes" "no"))
(println (f 1))                      ; → 无法求值的节点: 99（局部参数被全局覆盖）
```

```lisp
(println (if (and 0 1) "yes" "no"))  ; → yes（假值被当真的）
(println (str (and 0 1)))            ; → <slang.ast_nodes.Sym object at 0x...>
(println (str (or 5 6)))             ; → <slang.ast_nodes.Sym object at 0x...>
(println (if (when 0 1) "yes" "no")) ; → yes
```

**根因**：`deref_node` 沿环境父链把符号解析成运行时值（1、0、[]、字符串），`_p_ifc` 随后又无条件把它交给 `evaluate()`，而 `evaluate` 只接受 AST 节点。`and/or/when` 的假分支用 `(%quote false/nil)`，求值后仍是 AST 对象，`truthy` 对任意对象返回真。

**为什么现有示例没暴露**：`hello`/`fact`/`lists` 的条件都写成 `(<= n 1)`、`(< x 0)` 这类复合表达式，`test_if_and_arity` 只用 `(if 0 ...)`/`(if 1 ...)` 字面量。裸变量、`true/false/nil`、变量分支全部漏测。

**修复建议**：

- `deref_node` 只展开当前宏调用帧内绑定到 `Node` 的别名，不沿父链查值；
- `_p_ifc` 拿到非 `Node` 时直接返回该值，只有 `Node` 才求值；
- 把 `when/and/or` 的假分支从 `%quote` 符号改为真正的值（`0` 或 `()`）；
- 增加回归测试：裸全局变量、裸局部变量、同名全局遮蔽、`true/false/nil`、短路分支返回值。

### P0-2 C++ 词法器破坏程序正确性 ✅

**位置**：`core/slang_core.cpp:239-258,295-299`、`slang/native.py:122-139`

| 问题 | 复现 | native 结果 | 纯 Python 结果 | 状态 |
| --- | --- | --- | --- | --- |
| NUL 截断 | 文件含 `\x00`，其后还有代码 | 只执行前半段，退出码 0；`check` 报 OK | 正常执行/正确报错 | ✅ |
| 大整数降级 | `(println 12345678901234567890)` | `1.2345678901234567e+19` | 精确整数 | ✅ |
| 非有限值 | `(println 1e400)` | `JSONDecodeError` traceback（run/check/lex） | `inf` | ✅ |
| 浮点变整数 | `(list "a" "b")` + 下标 `1.0` | `1.0` 被 JSON 化成 `1`，行为改变 | 报「下标必须是整数」 | 🔁 |
| Unicode 数字 | `(println ١٢٣)` | 未定义名称 | 输出 `123` | 🔁 |
| 假核心 | `SLANG_CORE=假.so` | 静默空程序 / 段错误 | — | 🔁 |

**根因**：C ABI 只传 `const char*`（`c_char_p` 在 NUL 截断）；整数用 `strtoll`，溢出转 `double` + `%.17g`；`inf` 不是合法 JSON；`native.lex` 不捕获 `JSONDecodeError`，失败也不回退；加载只查三个符号名，无 ABI 版本校验。

**修复建议**：C ABI 改为显式长度 + 数值原文返回（Python 侧解析）；`native.lex` 对异常回退纯 Python；增加 ABI 版本号并在加载时校验；补 `1e999`、`2^64-1`、NUL、Unicode 数字的差分测试。

---

## 2. P1（High）

### P1-1 宏系统半成品：环境错位、不能生成代码、`quote` 不可用 ✅/🔁

**位置**：`slang/interp.py:252-258,285-290,339-342,350-367`、`builds/base.lang.build:14,17`

- ✅ 宏内 `def` 不定义到调用者：`(defmacro m (n v) (def n v))` 调用后 `z` 未定义；
- ✅ 宏内 `if` 把原始 AST 当条件：`(pick 0)` 和 `(pick 5)` 都返回 `11`；
- 🔁 模板无参数替换、无卫生性：`(defmacro add1 (x) (%eval (%quote (+ x 1))))` 用全局 `x` 得 `101` 而不是 `6`；
- ✅ `%cond` 返回未求值节点：`(%cond 0 1 2)` 打印 `<slang.ast_nodes.Num object ...>`；
- 🔁 `quote` 输出内存地址、不可比较：`(str (quote a))` → `<slang.ast_nodes.Sym object at 0x...>`；`(= (quote a) (quote a))` → `false`；
- 🔁 没有 quasiquote / `%gensym` / AST 构造原语，`%eval` 无法执行 `%list` 构造的代码。

**根因**：宏展开目标靠「最近一层 `%__caller__`」猜测；`%quote` 返回裸 AST 而非带标记的代码数据；值/代码边界不清。

**修复建议**：引入 `Raw/Code` 包装和显式展开栈；提供 `%gensym`、quasiquote 或 `%ast/%call` 构造器；否则在文档中把 `defmacro` 降级说明为「惰性函数」，并把它从宣传特性中移除。

### P1-2 `set` 不是赋值，闭包状态无法更新 ✅

**位置**：`builds/base.lang.build:11`、`slang/env.py:25-27`

```lisp
(def x 1)
(defn f () (set x 2))
(f)
(println (str x))          ; → 1

(defn make-counter ()
  (do (def n 0) (fn () (do (set n (+ n 1)) n))))
(def c (make-counter))
(println (str (c)))        ; → 1
(println (str (c)))        ; → 1（期望 2）
```

**修复建议**：实现沿父链查找并原地更新的 `%set`；或把 `set` 改名 `def-local` 并明确文档化「语言无赋值」。

### P1-3 没有源位置模型：相对加载 / 错误定位 / 项目发现全部漂移 ✅

**位置**：`slang/loader.py:24-38,57-131`、`slang/interp.py:94-97,614-630`、`slang/ast_nodes.py`

- ✅ 模块函数内 `(load "dep.lang.build")` 按**调用点**目录解析，不是模块文件目录；模块函数内 `(import ...)` 直接找不到文件；
- ✅ 函数内 `(load ...)`/`(load-config ...)` 写进临时调用帧，函数返回后定义全部丢失（副作用却发生了）；
- ✅ `load-config` 永久改写 `interp.project_root`：加载 `sub/slang.toml` 后再加载裸 `slang.toml` 仍解析到 `sub`；
- ✅ 跨文件错误「文件名来自调用者、行号来自被调模块」：4 行的 `main.lang` 报 `main.lang 第 8 行`，实际错误在模块第 8 行；
- 🔁 相对 `import` 在模块函数内按调用者目录解析（同根因）。

**根因**：`current_file`/`current_dir`/`project_root` 是全局可变状态；AST/`UserFn` 不带源文件信息；无加载栈。

**修复建议**：节点携带 `file/source_dir`；`load/import` 以语句所在文件为基准；`error` 使用节点源文件并输出调用链；`load-config` 用 `try/finally` 恢复 `project_root`；加载栈用 realpath 做循环检测。

### P1-4 项目发现只按 cwd，包装脚本不兑现承诺 ✅

**位置**：`slang/cli.py:38-43`、`slang/loader.py:88-100`、`bin/slang`、`README.md:16-20,32-35`

- ✅ 从 slang 项目目录运行别的项目 `main.lang`，静默使用 slang 自己的 base（输出 `hello` 而不是 `[P4] hello`）；
- ✅ 从任意目录 `sh bin/slang run .../examples/hello.lang` → `println 未定义`；
- ✅ README 的 `cd /tmp && python3 -m slang ...` 在 Termux 不成立（`/tmp` 不存在，项目外无 `PYTHONPATH` 也找不到包）。

**修复建议**：`cmd_run` 先解析文件参数，再从程序文件目录向上发现 `slang.toml`；`--project` 显式覆盖；包装脚本自动带 `--project "$DIR/slang.toml"`；README 修正。

### P1-5 「一切可覆盖」无依赖隔离，静默弄坏 std/base ✅

**位置**：`builds/std.lang.build:7-44`、`builds/base.lang.build:37-88`

```lisp
(defn = (a b) 1)
(println (str (map square (range 1 4))))   ; → []（应为 [1 4 9]）
(println (str (reduce + 0 (range 1 11))))  ; → 0

(def if (fn (c a b) a))
(map square (range 1 4))                    ; → 递归太深
```

- 🔁 覆盖 `%bind` 后所有 `def/defn` 静默失效；
- 🔁 覆盖 `%add` 不影响 `add`/`+`（别名快照），覆盖 `%eval` 又会追溯影响已定义函数（晚绑定）——“快照 vs 晚绑定」规则不统一。

**修复建议**：std/base 内部改用 `%` 原语或私有命名空间；覆盖 bootstrap 关键名时警告；文档明确快照/晚绑定规则；补覆盖测试。

### P1-6 stdlib 在千级元素崩溃 + O(n²) + 慢 ✅/🔁

**位置**：`builds/std.lang.build:7-28`、`slang/interp.py:546-551`

- ✅ `range 0 1400` 正常；`range 0 1440` → `递归太深`；
- 🔁 二分实测上限：`range/map/reduce` ≈1426，`filter` ≈950；`cons` 为 `[x]+xs`，从尾部构造是 O(n²)；
- 🔁 `fib(24)` slang 约为 CPython 递归的 200 倍耗时；`map/filter/range` 全部急切。

**修复建议**：加迭代/批量原语（`%range`、C++ 批量 map/filter/reduce，或 `%loop`/TCO）；`cons` 自底向上构造；文档给出实测规模上限。

### P1-7 `import` 半隔离、无缓存、嵌套访问受限 🔁

**位置**：`slang/loader.py:57-85`、`slang/interp.py:113-128`

- 模块能看到主程序**导入之后**定义的全局，行为随加载顺序变化（README 称「隔离的子环境」）；
- 模块宏可读写调用者环境（`%bind` 泄漏、`%lookup` 读局部变量）；
- 重复 import 重复执行顶层副作用；无选择性导入/导出；`a.b.c` 不可用、`(def m.value 999)` 会制造扁平绑定遮蔽真实成员。

### P1-8 格式/解析层契约不可靠 ✅/🔁

**位置**：`slang/parser.py:13-25,170-216`、`slang/lexer.py:45-50`、`slang/formats.py:39-50`

- ✅ auto `print((1) + 2)` → `1 不可调用`（单原子括号被当零参调用）；
- ✅ `@sexpr` 不严格：`(print "a" , "b")` 输出 `ab`；`[print "hi"]` 输出 `hi`；`check` 报 OK；
- ✅ BOM 使 `@sexpr` 失效，程序报 `未定义的名称 '\ufeff@sexpr'`；
- 🔁 `==`/`!=` 与 `<` 同级，`2 == 1 < 3` 得到 `true`；顶层 `1 + 2` 报错；`@auto print("ok")` 同行代码被静默丢弃；`;` 吞掉本行后续语句（`print("a"); print("b")` → `ac`）；畸形数字 `1.2.3`、`1e` 退化为符号。

### P1-9 缺少日常设施（设计取舍）🔁

无字典/结构体、`split/join/substr/replace`、`sort`、`argv`/stdin、`try/catch`、循环/`while`、真正的 `let` 多绑定、多表达式函数体（必须手写 `do`）。若定位只是窄 DSL 可接受，但与 README 的「编程语言」定位有落差。

### P1-10 C++ 分工的投入产出存疑 🔁

- 1.49MB token 密集：native lex 约 1.8s vs Python 1.9s（约 5%）；2MB 时 native 反而慢 36%；`check` 无收益；
- 大文件内存：8MB 源码 native 峰值约 700MB；
- 收益集中在 10MB 单 atom 这类不常见输入（约 3.9×）。

**建议**：要么把 C++ 扩展到 parser/求值热路径并设基准门槛，要么暂时退回纯 Python；至少在性能类文档里修正表述。

---

## 3. P2（Medium / Low 摘要）

| 编号 | 问题 | 位置 | 状态 |
| --- | --- | --- | --- |
| M-1 | bool/nil 双表示：`(= true (= 1 1))`→false、`(type true)`→num、`(str nil)`→`[]` 而 `(str (do))`→`nil` | `base.lang.build:20-22`、`interp.py:446-452` | ✅ |
| M-2 | `%neg`/`%not` 缺参抛 `IndexError` traceback；`%str`/`%type` 静默忽略多余参数 | `interp.py:230,443-444` | 🔁 |
| M-3 | 非法 UTF-8 在 run/check/lex/配置/readfile 全面 traceback | `loader.py:16-21`、`interp.py:577-585` | 🔁 |
| M-4 | 管道关闭 `BrokenPipeError` traceback，退出码 120 | `interp.py:571-575`、`cli.py` | 🔁 |
| M-5 | native 缓存非原子复制、损坏不重试、永不清理；假 .so 可静默空程序/段错误 | `native.py:43-100` | ✅（Batch 5 重写） |
| M-6 | 配置 schema 未校验：`builds="x"` → AttributeError；拼错 `file=` 静默无 build | `loader.py:103-137` | ✅（Batch 3） |
| M-7 | 循环 load/import 不检测：先输出数千行副作用，再报泛化「递归太深」 | `loader.py:57-85` | ✅（Batch 5 强化） |
| M-8 | `resolve_path` 的 cwd 兜底会静默加载项目外同名文件 | `loader.py:34` | ✅（Batch 5 严格化） |
| M-9 | `%readfile` 改写 CRLF；相对路径按 cwd 而非脚本目录 | `interp.py:577-597` | ✅（Batch 5） |
| M-10 | 错误信息：函数名全是 `<匿名>`；`last` 空列表在 2 行程序报「第 35 行」 | `interp.py:356`、`std.lang.build:34-35` | ✅ |
| M-11 | auto `fold` 每轮归约一个运算符 + 切片：20k 个 `+` 需 109 秒 | `parser.py:204-230` | 🔁 |
| M-12 | `check` 无 `--project`、每文件新建解释器、非 SlangError 整批中止 | `cli.py:22-23,68-80` | ✅（Batch 3） |
| M-13 | TOML mini fallback 与标准差异大（点号键、行内注释、重复键、十六进制…） | `tomlcompat.py:105-133` | ✅/🔁（Batch 3 加固；高级语法仍缺） |
| M-14 | 空文件也警告「未声明格式」；`@未声明` 标签多一个 `@`；未知格式警告重复输出 | `formats.py:53-58`、`loader.py:41-47` | ✅（Batch 3） |
| M-15 | 未知转义 `\q`/`\u0041`/`\x41` 静默丢反斜杠；字符串内 `\`+换行导致行号偏移 | `lexer.py:63-79`、C++ 同 | ✅/🔁 |
| M-16 | 词法错误在带 header 时行号 -1；`@sexpr` 同行代码被吞 | `loader.py:49-51`、`formats.py:45-50` | ✅（Batch 4） |
| L 系列 | `FALSY_STRINGS` 死代码、`p,q = self._prim, self._prim` 未用、`slang_last_error` 从未绑定、原语 arity 校验不统一等 | 多处 | 🔁 |

---

## 4. 交叉印证的根因（五份报告的共同指向）

1. **代码/值不分**（P0-1、P1-1、P1-2、M-1）：`deref_node` 双重语义、`%quote` 返回裸 AST、`%eval` 双重求值、`truthy` 接受任意对象。
2. **动态全局位置状态**（P1-3、P1-4、M-8、M-9）：`current_file`/`current_dir`/`project_root`，AST 无源文件。
3. **宏展开环境靠单层 `%__caller__` 猜测**（P1-1、P1-3 的一部分）：嵌套宏/辅助函数/宏内特殊形式全部错位。
4. **快照 vs 晚绑定混合**（P1-5）：别名捕获旧 Prim，特殊形式按名查找。
5. **C++/Python 两套实现无契约**（P0-2、P1-10）：无长度参数、无数值保真、无 ABI、无边界差分测试。
6. **stdlib 用非尾递归 + 复制列表**（P1-6、M-11）：语言级性能天花板不在 C++ 而在解释器与算法。

---

## 5. 与 README 的差距（文档需修正）

| README 声明 | 实测 |
| --- | --- |
| 「C++/Python 行为完全一致」（README:235） | NUL、大整数、`1e400`、Unicode 数字不一致 |
| 「包装脚本可在任何目录下运行」（README:16-20） | 仅 cwd 在项目内时成立 |
| 「`cd /tmp && python3 -m slang run ...`」（README:32-35） | Termux 无 `/tmp`；项目外找不到包 |
| 「`@sexpr` 严格 S 表达式」（README:109） | 逗号、`[]`、`{}` 都接受 |
| 「`register_format` 可扩展适配器」（README:136） | 只是名字→模式的别名表，无前端接口 |
| 「测试覆盖 if 真值、变长参数、token 一致性」（README:247） | 漏测裸变量条件/true/false/nil；变长参数测的是 `%concat`；对比仅 4 个文件且核心缺失时 skip |
| 「深递归受限（上限 20000）」（README:253） | stdlib 约 1400 元素即栈溢出 |
| 「`(if (< x 0) ...)` 等速览可运行」（README:142-169） | `even?` 未定义；`if true/false` 失败；`set` 不是赋值 |

> 0.2.0 / 0.3.0 已按本节逐条修正 README（模块语义改为“固定内置快照”、`--no-native` 位置、`check --project` 只解析、相对路径严格基准、log 模块用法、已知限制清单）。

---

## 6. 建议修复顺序

1. **P0-1 条件/布尔语义** + 回归测试（裸变量、true/false/nil、短路返回值）。
2. **P0-2 native 正确性**：长度参数 + 数值原文 + 异常回退 + ABI 校验 + 差分测试。
3. **源位置模型**：Node 带 file/source_dir；load/import/readfile 按语句所在文件解析；错误输出调用链；load-config 恢复 project_root；项目发现按程序路径；加载栈循环检测。
4. **宏系统**：Raw/Code 包装 + 展开栈 + `%gensym`/quasiquote；或明确降级并修正文档。
5. **真正的 `set`** 或改名 `def-local`；统一 bool/nil 表示。
6. **覆盖隔离**：std/base 私有原语 + 关键名覆盖警告；统一快照/晚绑定规则。
7. **stdlib/解析性能**：批量/迭代原语、`fold` 改 O(n)、`cons` 不再复制、流式 token。
8. **工程化**：`pyproject.toml`、LICENSE、CI（含 C++ parity）、README 按第 5 节修正。

---

## 7. 审计中确认「正常/已达标」的部分

- 12 个端到端测试全部通过；6 个示例输出正确；
- ✅ `println`/`if` 等名字可被覆盖（简单场景）；
- ✅ 模块顶层定义不泄漏到主程序；
- ✅ `check` 确实只解析不执行（`%writefile` 未被触发）；
- ✅ 注释、基本转义、CRLF、空文件、emoji/组合字符行列号正常；C++/Python 在常规输入上 2 万例随机对比一致（除 P0-2 清单外）；
- ✅ ctypes token 内存无泄漏（400 次成功 + 400 次失败 + 100 次 JSON 错误迭代后 RSS 稳定）；
- ✅ SIGINT 退出码 130 且无 traceback；
- ✅ 缺失文件/目录/权限/除零/`%error`/调用非函数/用户函数参数个数错误均给出带位置的干净 `EvalError`；
- ✅ stdlib 空列表边界与参数顺序基本符合直觉；`qsort` 可用语言本身写出。

---

## 附：报告来源

| 角度 | 子代理会话 |
| --- | --- |
| 求值器语义 | `ses_eaacfc9f2ffeC70TYGlTPk8DWb` |
| 词法/解析/格式 | `ses_eaacfc9eeffepaiCBc0LhG0HLl` |
| 加载/模块/配置 | `ses_eaacfc9eaffeUkSWKxxT82ycms` |
| 健壮性/性能 | `ses_eaacfc9b7ffe8au0ZPhiVVLvIL` |
| 设计/可用性/文档 | `ses_eaacfc9b3ffe39Ai7iHolgnmqd` |
