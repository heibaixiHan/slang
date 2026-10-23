# 更新记录

> 注：0.6.0 起旧版的顶层 `builds/` 路径迁移到 `slang/stdlib/core/`；下方历史版本条目保留当时的路径与测试数。

## 0.6.0 — 2026-10-23

标准库统一与模块优化版：把标准库收敛到 `slang/stdlib/`，新增 Python/C 常用包装、Flask 风格 Web、`.slang.module.inline` 解析/内联缓存和分级 `logger`；测试 121 → **139**，全部通过。

### 标准库与三类源码

- 标准库统一到 `slang/stdlib/`：`core/`（base/std 构建片段）、`python/`、`c/`、`web/`，以及 `ffi.py`、`log.py`、`logger.py`、`ffi_default.toml`、`registry.py`。
- 按扩展名正式区分：`.lang.build`（构建/覆盖片段）、`.lang`（S 表达式模块库）、`.slang`（人类友好前端库/程序）；`.slang.module.inline` 是生成缓存，不是源码。
- 逻辑导入名：`python.*` / `std.python.*`、`c.*` / `std.c.*`、`flask` / `web.flask` / `std.web.flask`、`logger.build`；本地同名文件优先于标准库。
- 删除旧的顶层 `builds/` 和 `slang/stdlib/builds/`，不保留兼容路径；`slang.toml`、loader、测试和打包元数据切换到新目录。

### Python / C 包装

- 新增 `python.os`、`python.sys`、`python.json`、`python.re`、`python.time`、`python.random`、`python.subprocess`、`python.pathlib`、`python.math`。
- 新增 `c.stdio`、`c.stdlib`、`c.string`、`c.math`、`c.time`；运行时仍走 `ffi.bind`。
- JSON/pathlib 使用小型 Python helper，保证相对路径按 cwd 解析，而不是按标准库模块目录解析。

### Flask 风格 Web

- 新增 `web/server.py`、`server.ffi.toml`、`flask.lang`；支持路由和 `<name>` / `<int:>` / `<float:>` / `<path:>` 转换器。
- 请求/响应：query、path、header、cookie、JSON/form/body，text/html/JSON/redirect/file，状态码与响应头。
- 静态文件、before/after 钩子、错误处理、路由 group、CORS、服务端 session。
- 简单模板：变量、过滤器、`for`、`if/else`；`handle` 可直接测试，`start`/`stop`/`url` 管理后台 HTTP 服务，`run` 阻塞运行。

### `.slang.module.inline` 缓存

- 每个 `.lang` / `.slang` 模块在源文件旁生成 `<源文件>.slang.module.inline`；第一行保存源文件 SHA-256，源文件/解析模式/优化器版本变化时自动重建。
- 缓存是纯 JSON AST，不执行代码；损坏、只读目录、权限不足或磁盘错误自动回退正常解析。
- 保守内联：同一模块顶层、固定参数、单表达式、纯内置函数、字面量实参；`set`/重复定义/`load`/`eval`/`defmacro` 时禁用。不做常量折叠，避免破坏可覆盖的基础定义。
- `SLANG_NO_INLINE_CACHE=1` 可关闭；缓存文件已加入 `.gitignore`。

### `logger` 模块

- 新增内置 `logger`（显式 import），级别 `debug < info < warning < error < critical`，默认 `warning`；阈值设为 `debug` 输出所有信息。
- 支持 `set_level/get_level/enabled/log/set_stream/set_format/reset`，默认写 stderr。
- 新增 `logger.lang.build`：把 `log-set-level`、`log-debug` 等便捷函数放进根环境，并展示如何覆盖基础 `println`。

### 测试、版本与打包

- 测试 121 → 139：新增标准库别名/本地优先、Python/C 包装、Web handle/真实 HTTP/模板/静态/session/hooks、缓存命中/失效/损坏回退/关闭、内联重定义安全、logger 级别与 build 版本。
- 版本 0.5.0 → 0.6.0；C++ 核心源码未变，仍为 `0.5.0-cpp` / ABI v2。
- wheel 包数据加入 `*.lang.build`、`web/*.ffi.toml`，并验证 `core/`、`python/`、`c/`、`web/`、`ffi_default.toml` 均随包发布。

## 0.5.0 — 2026-10-21

人类友好格式与性能版：新增 `.slang` 缩进式前端和 `slang decode`，两者生成同一棵 AST；解释器热路径优化，`fib(24)` / `reduce 200k` 约提升 2.8–3.6 倍；测试 97 → 121，全部通过。

### 新增 `.slang` 格式

- 缩进块，`def` / `let` / `set`、`if` / `elif` / `else`、`when`、`do`、`fn`、`defmacro`、`import` / `from`、`load` / `load-config`。
- 中缀运算与标准优先级、短路 `and` / `or` / `not`、条件表达式 `a if cond else b`、列表字面量、下标、点号成员、变长参数。
- 字符串：单/双引号、三引号、原始字符串、f-string 插值（`{expr}`、`{{` / `}}`）。
- 数字：十六进制/二进制/八进制、下划线分隔、指数。
- 行内单语句块、括号内自由折行、行尾运算符/逗号自动续行、`\` 显式续行、`;` 分行。
- `.slang` 扩展名自动识别；`.lang` 可用 `@slang` 显式切换；显式的 `@sexpr` 优先。
- `slang decode FILE [-o OUT] [--width N]` 输出等价的 `@sexpr` `.lang`；运行时直接解析为 AST，不经过“生成文本再重新解析”。
- `slang check` 自动识别 `.slang` 与 `@slang`；`slang lex` 支持 `--mode slang`（也可直接读取 `.slang` 文件）。
- 新增规范 `docs/slang-format.md`、示例 `examples/slang_demo.slang` / `examples/slang_math.slang`。
- 错误均带文件/行/列：缩进不匹配、混用 tab/空格、链式比较、`return`、`for` / `while`、表达式里写 `=`、未闭合括号/f-string 等。
- 原始字符串按连续反斜杠的奇偶规则闭合；f-string 文本支持 `\xHH` / `\uHHHH`。
- 支持语句层块 lambda（`f = fn(x): ...`）；括号内仍需单行 `fn(x) = ...`。
- `decode` 对 `inf` / `nan` 输出可运行的 `(num "inf")` 等形式；切片、关键字参数、参数默认值、解构赋值会给出明确的不支持错误。

### 性能

- AST 求值改为精确类型分派；非点号名字查找内联，并用哨兵去掉重复 dict 查找。
- `if` 在无宏参数帧、无日志时走快速路径，跳过 `expand_macro_arg` / `_p_ifc` 包装。
- 函数调用内联参数绑定与调用栈维护；调用栈只保存 `(名字, 节点)`。
- 数字算术、比较、`eq` / `ne` 增加 int/float/str 快速路径。
- `apply` / `map` / `filter` / `reduce` 去掉逐元素可调用类型分派与多余参数复制。
- `print` 不再每次 flush；列表/字典/字符串显示改精确类型分派。
- 新增 `bench/bench.py`；同一台 Termux/Android、Python 3.13 参考：`fib(24)` 3.79s → 1.05s，`reduce 200k` 2.39s → 0.81s，`map 100k` 2.28s → 0.81s，`join 100k` 0.41s → 0.22s；启动约 0.18s（主要受 Python 进程启动影响）。
- 语义不变：覆盖、模块隔离、`%` 原语、宏展开、日志行为均通过原有测试。

### 兼容性与打包

- 版本号 0.4.0 → 0.5.0（C++ 核心 `0.5.0-cpp`，ABI 仍为 2；改过 C++ 源码需 `make -C core`）。
- 测试 97 → 121。
- 没有新增自动加载的内置模块；`log` / `ffi` 仍需显式 import。
- `.lang` 语义与文件保持兼容；现有程序无需修改。

## 0.4.0 — 2026-10-21

外部库互操作版：新增内置 `ffi` 模块（必须显式 `(import "ffi")`），支持把 Python 模块和 C 动态库包装成 slang 模块；端到端测试 80 → 97，全部通过。

### 新增 `ffi` 模块

- 两种导入模式：
  - **直接导入**：`(ffi.python "math")`、`(ffi.c "m")`，使用随包默认兼容配置 `slang/stdlib/builds/ffi_default.toml`；Python 自动发现公开成员，libm/libc 使用内置签名表。
  - **配置文件导入**：`(ffi.import "spec.toml")`，用 TOML 指定 `kind`（`python` / `c`）、模块名/库名、导出列表、重命名、C 函数签名、结构体、类型别名与转换选项。
- Python：模块/类/函数/.py 文件导入；对象属性链（`now.year`）、类方法（`dt.datetime.now`）、`fn` 作为 Python 回调（`functools.reduce`）；异常转成带位置的 slang 错误。
- C：`ctypes` 后端；标量/字符串/`void*`/`T*`/结构体按值；`varargs`（含 `printf`）；`symbol` 重命名；`malloc`/`memset`/`ffi.at` 指针读写；结构体字段读取与 `ffi.set-field`。
- 接口：`ffi.python`、`ffi.c`、`ffi.import`、`ffi.close`、`ffi.members`、`ffi.bind`、`ffi.call`、`ffi.null`、`ffi.address`、`ffi.at`、`ffi.set-field`、`ffi.default-config`、`ffi.version`。
- 生命周期与错误：句柄关闭后再访问报「已关闭」；找不到符号、未知类型、Python 异常都带文件/行/列，不打印 traceback。
- 不自动加载：`ffi` 不在 `slang.toml` 的 `builds.files` 里，只有显式 import 才存在。

### 运行时与打包

- `Env` 新增动态成员解析钩子 `member_getter` 与外部句柄槽 `external`；普通模块仍只查自身 `bindings`。
- `interp.call` 新增 `slang_call` 鸭子类型协议，用于调用外部可调用对象。
- `type`/`len` 支持 `dict`（`ffi` 返回的 Python 字典）。
- 默认兼容配置随 wheel 发布（`slang.stdlib` 的 `builds/*.toml`）；删除未使用的 `ffi` 死代码路径。
- 版本号 0.3.0 → 0.4.0（C++ 核心 `0.4.0-cpp`，ABI 仍为 2；改过 C++ 源码仍需 `make -C core`）。
- 新增 `examples/ffi_demo.lang`、`examples/ffi/json.ffi.toml`、`examples/ffi/libc.ffi.toml`。

### 已知限制（新增）

- C 头文件不自动解析；默认直接导入只覆盖 libm/libc 常见签名，其他库需要 TOML。
- `T*` 返回值是带类型指针，长度由调用方负责；越界/悬垂指针不检查。
- 可变参数只支持数字/字符串/指针；结构体嵌套数组、C++ name mangling 暂不支持。

## 0.3.0 — 2026-10-20

Batch 1–5 审计修复版：46 → 80 个端到端测试，全部通过。新增内置 `log` 模块（必须显式 `import`）。

### Batch 1：环境 / 模块模型重构

- `slang/env.py` 重写：区分 root / module / snapshot / function / lazy / capture / block 七类环境，各自记录 `kind`、`is_module`、`is_macro_binding`、`macro_target`。
- 模块基线固定：配置加载完成后冻结 `module_base`，每个模块复制一份快照；模块**看不到主程序的全局变量**（无论 import 前后），需要时显式 `import`；import 顺序不再影响语义。
- 模块成员访问只查模块自身导出，`m.println` / `m.secret` 报「模块里没有成员」，不再泄漏内置定义。
- 宏闭包捕获时冻结参数帧并保留宏调用目标；`def` 落 `caller_root`，`load`/`load-config` 落 `module_env`；`set` 沿动态调用链与词法父链更新最近绑定。
- 定义名/赋值名禁止包含 `.`（点号保留给模块成员访问）。

### Batch 2：健壮性与错误信息

- 整数位数上限 `SLANG_INT_MAX_DIGITS`（默认 100000），超出报「整数太大（超过 N 位上限）」而不是 `inf`/traceback；`sys.set_int_max_str_digits` 同步提升。
- `_p_num` 健壮化；`%add/%sub/%mul/%div/%mod/%neg` 捕获 `OverflowError`/`ValueError`；路径含 NUL 直接报错。
- `LoadError` 带位置与调用链；包装已有错误时只保留一次调用链（不再出现两条调用链）。
- 重复参数名、`...` 位置与空名、空函数体、`%lambda` 仅参数表，均给出明确错误。

### Batch 3：配置与工具链

- `check --project` 改为**只解析不执行**（不再触发 `writefile` 等副作用）。
- 配置 falsey/类型校验与未知键警告；TOML 极简解析器加固（拒绝行内表/多行字符串/数组表/前导零/裸字符串，重复键与重复表报错）。
- 纯注释/shebang 文件不再误报“未声明格式”；`--no-native` 顶层与子命令后均可写；`lex` 输出严格 JSON（非有限值写成 `"Infinity"`/`"NaN"` 字符串）。
- `check` 的 `files` 改为 `nargs="*"`，允许只给 `--project`；两者都没有时报错。

### Batch 4：词法与解析一致性

- 裸 CR / CRLF 归一化（Python 与 C++ 两侧）；header 同行代码报错并给出列号。
- auto 模式：词法器不再吞掉前导 `+/-`（`%` 作为 auto 运算符），`1 +2`/`1 -2`/`1 *2`/`1 %2` 规则统一；空参数报错；顶层逗号作分隔符。
- `+ - * /` 链折叠为 n 元调用，避免深递归。

### Batch 5：原生核心与工程

- `native.py` 重写：按内容哈希命名缓存、原子写入、缓存清理、并发锁；ABI 校验后做冒烟测试；拒绝伪 `.so`；大文件按 token 密度回退纯 Python。
- 指定 `SLANG_CORE` 时只用该路径；加载器加 `load_lock` 串行化 + 循环加载检测 + 失败回滚；`writefile` 改原子写（临时文件 + `os.replace`）；调用深度上限 2000 层并给出可读错误。
- 相对路径严格按“语句所在文件目录”解析，不再回退项目根/cwd；模块与文件统一用 `realpath`。
- 随包发布内置 base/std（`slang/stdlib/builds/`），无项目配置时也能运行；与项目 `builds/` 保持同步（一致性测试）。
- `bin/slang` 解析符号链接；`make test` 依赖 `core`；新增 `make test-python`。

### 新增：内置 `log` 模块

- 只能显式 `(import "log")` 获得；**不在** `slang.toml` 的 `builds.files` 里，不会自动配置/加载。
- 接口：`(log.start 路径 [上限] [模式])`、`(log.stop)`、`(log.enabled)`、`(log.path)`、`(log.event/record 名称 [数据])`、`(log.info/warn/error/debug 消息 [数据])`。
- 输出 JSON Lines（逐行 flush）：定义、调用、set、load/import、print、错误、手动事件全部记录；CLI 退出时自动关闭文件。
- 日志里的值按字符上限截断，列表/字典先截断再渲染（避免内存峰值）。

### 兼容性变化

- 版本号 0.2.0 → 0.3.0（C++ 核心 `0.3.0-cpp`，ABI 仍为 2；改过 C++ 源码仍需 `make -C core`）。
- 模块不再看到主程序的全局变量：依赖隐式全局的模块需要改成显式 `import` 或把数据作为参数传入。
- `check --project` 不再执行构建文件。
- 相对 `load`/`import`/`readfile`/`writefile` 不再回退项目根/cwd。

### 已知限制

见 `README.md`「已知限制 / Roadmap」：无 quasiquote/卫生性、手写 `cons` 仍 O(n²)、无导出列表、TOML 高级语法仍依赖标准库、`\u`/`\x` 未实现等。

---

## 0.2.0 — 2026-10-20

以 `AUDIT.md` 的 P0/P1 为主的修复版。33 → 46 个端到端测试，全部通过。

### 语义修复

- 条件与布尔：`if/and/or/when/%cond` 延迟求值选中分支；`true`/`false` 为真正布尔；`nil` 统一为空列表 `[]`，`(str nil)`/`(str (do))` 都是 `"[]"`。
- 宏环境：`def` 在宏体内定义到宏调用点；宏参数展开只沿 lazy 调用帧的 caller 链，不再污染词法作用域；局部参数可正确遮蔽全局；修复用户定义名与宏参数名冲突（如 `(def value 1)`）时的二次展开。
- `set` 成为真赋值：沿环境父链更新最近绑定，闭包状态可用；新增 `%set`。
- 多表达式函数体（`fn/defn/defmacro`）；`def` 支持字符串名称；`%quote` 结果按源码显示并可结构比较。
- 标准库内部改用 `%` 原语；`import` 改为原语并在导入时对当前环境做快照（真隔离）；重复 `import` 按真实路径缓存（模块顶层只执行一次）。

### 源位置 / 加载

- AST 节点携带源文件；`load/import/load-config/readfile/writefile` 相对“语句所在文件”解析。
- 错误信息带 `文件:行:列` 与调用链（函数名不再全是 `<匿名>`）。
- 循环 `load/import` 检测；`load-config` 结束后恢复 `project_root`；函数内 `load/load-config` 的定义落到模块/全局环境。
- 项目发现：`--project` > 程序文件目录向上 > 当前目录向上；`bin/slang` 可在任意目录运行。
- 格式层：UTF-8 BOM、shebang、声明必须单独占行、空文件不警告、行号与原文一致；`@sexpr` 拒绝 `[]`/`{}`/逗号。

### C++ 核心（ABI v2，需重新编译）

- `slang_lex(src, len, mode, out_len)` 显式长度，NUL 不再截断。
- `slang_abi_version() == 2` 校验；缺失/不匹配/损坏时自动回退纯 Python 并给出原因。
- 数字按原文交给 Python 解析：大整数精确、`1e400` → inf、`1.0` 保持浮点、前导零归一化。
- 两种模式一致产出逗号；修复 `strchr` 把 NUL 当分隔符的问题。
- 新增固定种子的 600 例 C++/Python 词法差分测试。

### 性能

- 新增迭代批量原语 `%range/%map/%filter/%reduce/%take/%drop`，十万级列表可用。
- auto 运算符归约改为栈式线性折叠：20k 个 `+` 从 109 秒降到约 0.45 秒。

### 新增设施

- `%sort`（可选比较函数）、`%split`、`%join`、`%substr`，以及 base 别名 `sort/split/join/substr`。
- `check --project`；检查单文件失败后继续处理其余文件。
- 同一格式警告只输出一次。
- TOML 兜底解析支持行内注释、点号键、数字下划线/十六进制、重复键检测（高级语法仍建议 `tomllib`/`tomli`）。

### 兼容性变化

- 需要重新 `make -C core`（ABI 1 → 2）。
- `true`/`false` 现在是布尔，`nil` 与 `[]` 完全等价。
- 字符串未知转义由“静默丢反斜杠”改为“保留反斜杠”；`\`+换行报错。
- 版本号 0.1.0 → 0.2.0；新增 `pyproject.toml` 与 `slang` 命令入口。

### 已知限制

见 `README.md`「已知限制 / Roadmap」：无 quasiquote/卫生性、手写 `cons` 仍 O(n²)、无导出列表、TOML 高级语法仍依赖标准库、`\u`/`\x` 未实现等。
