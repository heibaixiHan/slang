# `.slang` 格式规范（v1，随 slang 0.5.0 起发布）

`.lang` 是严格 S 表达式，适合机器生成和精确控制；`.slang` 是同一语义层上的
**人类友好前端**：用缩进表示块、用中缀写运算、用 `def` / `if` / `f"..."` 写代码。
两者最终生成**完全相同的 AST**，因此：

- `.slang` 能调用的东西和 `.lang` 完全一样，没有第二套运行时；
- `.lang` 的覆盖、模块隔离、`%` 原语、`log` / `ffi` 等语义原样适用；
- `slang decode` 可以把 `.slang` 解码成等价的、可直接运行的 `@sexpr` `.lang`。

---

## 1. 文件识别与编码

- 扩展名 `.slang` 自动按本格式解析，首行不需要写 `@slang`。
- `.lang` 文件可以在首行写 `@slang` 显式切换到本格式。
- `.slang` 文件如果写了 `@slang`，同样合法；写了 `@sexpr` 则以显式声明为准
  （此时文件内容必须真的是 S 表达式）。
- 编码 UTF-8；允许 BOM；换行支持 LF、CRLF、CR（内部统一为 LF）。
- 首行可以是 shebang（`#!...`），按注释处理。
- `slang check`、`slang lex`、`slang run` 都支持 `.slang`；
  `slang decode` 专门输出 `.lang`。

---

## 2. 词法

### 2.1 注释与语句分隔

```slang
# 到行尾都是注释
x = 1; y = 2   # 分号可以在同一行分隔语句
```

- `#` 开始行注释；字符串里的 `#` 不算注释。
- `;` 是语句分隔符（等价于换行），不能出现在括号内部。
- 空行和纯注释行不产生缩进 token。

### 2.2 缩进

- 用缩进表示块；同一块内缩进必须一致。
- 一个制表符按 **4 列**计算；同一行缩进里混用空格和制表符会报错。
- 推荐每层 4 个空格。
- 缩进只能出现在 `:` 结尾的块头之后；否则报「意外的缩进」。
- 括号 `()` / `[]` 内部换行不参与缩进，可自由折行；括号外如果一行以
  运算符、逗号、`and`、`or`、`not` 结尾，会自动续到下一行（不需要 `\`）。
  也可以用行尾 `\` 显式续行。

### 2.3 名字

- 首字符：字母（含中文等 Unicode 字母）或 `_`。
- 后续字符：字母、数字、`_`、`?`。
- 支持点号成员名：`math.square`、`a.b.c`（每一段都必须以名字字符开头）。
- `%` 开头的名字表示核心原语，例如 `%add`、`%ifc`；`%` 后面必须紧跟名字。
  在表达式位置、且前面不是值的时候，`%` 才会被识别为原语前缀；
  `a % b` 里的 `%` 是取模运算符。
- 关键字是上下文相关的：`def`、`let`、`set`、`if`、`elif`、`else`、`when`、
  `do`、`fn`、`import`、`from`、`as`、`load`、`load-config`、`defmacro`、
  `and`、`or`、`not`、`true`、`false`、`nil`。
  其中 `true` / `false` / `nil` 仍然按普通符号解析，可以被覆盖。

### 2.4 数字

```slang
42           # 整数
3.14         # 浮点
.5           # 0.5
1.           # 1.0
1e9          # 浮点
6.02e23
0x2A         # 十六进制
0b1010       # 二进制
0o755        # 八进制
1_000_000    # 下划线可读性分隔
```

- 负号是前缀运算符，不是数字字面量的一部分。
- 数字后面不能紧跟名字字符或 `.`（避免 `1foo`、`1.2.3` 被静默拆分）。

### 2.5 字符串

支持四种写法：

```slang
"双引号，支持转义"
'单引号，支持转义'
"""三引号
可以跨行"""
r"原始字符串，反斜杠不转义"
```

- 普通转义：`\n` `\t` `\r` `\\` `\"` `\'` `\0`，以及 `\xHH`、`\uHHHH`。
- `.lang` 现在也支持 `\xHH`、`\uHHHH`（`\u` 固定四位），两套词法器行为一致。
- 未知转义保留反斜杠（与 `.lang` 一致），例如 `"\q"` 是 `\q`。
- 单行字符串不能直接跨行；三引号可以。
- 原始字符串里 `\` 不参与转义，但 `\"` 仍然可以用来避免提前闭合。

### 2.6 插值字符串

在引号前加 `f`（大小写均可）：

```slang
name = "world"
println(f"hello, {name}!")
println(f"{1 + 2} / {{字面量花括号}}")
println(f'{join(["a", "b"], "-")}')
```

- `{表达式}` 会解析成一个完整的 `.slang` 表达式；插值表达式必须写在一行内。
- `{{` 和 `}}` 分别表示字面量 `{` 和 `}`。
- 插值结果按 `concat` 的显示规则拼接（列表显示为 `[1 2 3]` 等）。
- 不支持 Python 的 `!r` / `!s` 转换和 `:` 格式说明；需要格式化时先调用 `str(...)`，或用 `join` / `+` 拼接。
- 插值里可以嵌套**不同引号**的 f-string（如 `f"outer {f'inner {x}'}"`）；同引号嵌套请先赋值或改用不同引号。
- 只有一个文本片段时，`f"..."` 等价于普通字符串。

### 2.7 运算符与标点

```
+  -  *  /  %       算术
== != < <= > >=     比较
and or not          逻辑（短路）
=                   定义/赋值语句专用，不是表达式运算符
( )  [ ]  ,  :  ;   分组、调用、列表、参数、块头、语句分隔
```

- 相等比较必须写 `==`；在表达式里写 `=` 会报错并提示。
- 不支持链式比较（`1 < x < 10` 会报错；请写 `1 < x and x < 10`）。
- 平级运算符左结合。优先级从高到低：
  1. 后缀：调用 `f(...)`、下标 `xs[i]`、成员 `a.b`
  2. 前缀：`-`、`+`、`not`
  3. `* / %`
  4. `+ -`
  5. `< <= > >=`
  6. `== !=`
  7. `and`
  8. `or`
  9. 条件表达式 `a if cond else b`

---

## 3. 语法

### 3.1 程序与块

```ebnf
program   := { NEWLINE | statement }
statement := def | set | if | when | do | import | from | load | assign | expr

body      := NEWLINE INDENT { statement } DEDENT
           | statement                      (* 同一行的单语句块 *)

def       := ("def" | "let") NAME "=" expr
           | ("def" | "let") NAME "(" params ")" "=" expr
           | ("def" | "let") NAME "(" params ")" ":" body

defmacro  := "defmacro" NAME "(" params ")" ("=" expr | ":" body)
set       := "set" NAME "=" expr
assign    := NAME "=" expr                  (* 等价于 set *)

if        := "if" expr ":" body
             { "elif" expr ":" body }
             [ "else" ":" body ]
when      := "when" expr ":" body
do        := "do" ":" body

import    := "import" STRING [ "as" NAME ]
from      := "from" STRING "import" NAME { "," NAME }
load      := ("load" | "load-config") STRING

params    := [ NAME ["..." ] { "," NAME ["..."] } [","] ]
expr      := ...                            (* 见 3.2 *)
```

### 3.2 表达式

```ebnf
expr        := or_expr [ "if" or_expr "else" expr ]     (* 条件表达式 *)

or_expr     := and_expr { "or" and_expr }
and_expr    := not_expr { "and" not_expr }
not_expr    := "not" not_expr | comparison
comparison  := additive [ CMP additive ]                (* 不支持链式 *)
additive    := multiplicative { ("+" | "-") multiplicative }
multiplicative := unary { ("*" | "/" | "%") unary }
unary       := ("+" | "-") unary | postfix
postfix     := primary { "(" args ")" | "[" expr "]" }

primary     := NUMBER | STRING | FSTRING
             | NAME
             | "fn" "(" params ")" ("=" expr | ":" body)
             | "(" expr ")"
             | "(" ")"                                  (* 空列表 nil *)
             | "[" [ expr { "," expr } [","] ] "]"
args        := [ expr { "," expr } [","] ]
```

`fn` 既可以写在一行，也可以带缩进块：

```slang
add = fn(a, b) = a + b
classify = fn(n):
    if n < 0:
        "negative"
    else:
        "non-negative"
```

### 3.3 函数体与返回值

函数体是**表达式序列**，最后一个表达式的值就是返回值：

```slang
def abs(n):
    if n < 0:
        -n
    else:
        n
```

没有 `return`。需要提前返回时，把逻辑改写成 `if` 表达式，或把结果赋给变量后
在末尾返回。写 `return` 会得到明确的报错提示。

### 3.4 函数、变长参数与宏

```slang
def add(a, b) = a + b

def show(label, xs...):
    println(label + ": " + str(xs))

show("rest", 1, 2, 3)       # rest: [1 2 3]

defmacro unless(cond, body):
    if cond:
        nil
    else:
        body

unless(false, println("会执行"))
```

- 参数表与 `.lang` 完全一致；`xs...` 是变长参数，必须是最后一个。
- `defmacro` 绑定惰性函数，参数是原始 AST；宏体里用 `%eval` / `%quote` 等
  原语可以继续展开代码。
- 函数定义可以嵌套；在函数体内 `def` 绑定到该函数的调用环境。

### 3.5 赋值与作用域

```slang
x = 1              # 等价于 (set x 1)
x = x + 1          # 更新已有绑定
def y = 10         # 等价于 (def y 10)
set y = y + 5      # 显式 set
```

- `set` / 裸赋值沿环境链查找同名绑定；找到就更新，找不到就在当前目标环境新建。
- `def` 直接在当前目标环境定义/覆盖；`let` 是 `def` 的别名，不是块级局部绑定。
- 点号名是只读成员访问，不能赋值。
- 名字不能包含 `.`（点号保留给模块成员）。

### 3.6 条件、块与短路

```slang
if score >= 90:
    println("A")
elif score >= 60:
    println("B")
else:
    println("C")

when flag:
    println("flag 为真")       # 假时返回 nil

do:
    println("第一条")
    println("第二条")

value = "yes" if ok else "no"
```

- `if` / `elif` / `else` 只求值命中的分支。
- `and` / `or` 短路并返回实际值（与 `.lang` 相同）。
- 块里有多条语句时，`if` 分支会降级为 `(do ...)`；函数体则直接使用
  `defn` 的多表达式函数体，不额外包 `do`。
- 同一行的单语句块（`if ok: println("x")`）合法，但不能在同一行再写 `else`；
  需要 `else` 时请用多行块或条件表达式。

### 3.7 列表、下标与调用

```slang
xs = [1, 2, 3]
println(str(xs[1]))          # 2
println(str(len(xs)))        # 3
println(str(map(square, xs)))
println(str(reduce((fn (a, b) = a + b), 0, xs)))
```

- `[a, b]` → `(list a b)`；`[]` → `(list)`；`()` → 空列表字面量 `nil`。
- `xs[i]` → `(nth xs i)`；下标越界由运行时给出带位置的错误。
- 调用必须带括号：`f(a, b)`；`f()` 是零参调用。
- 支持尾逗号和多行参数表：

```slang
println(join(
    map(str, [1, 2, 3]),
    "-"
))
```

### 3.8 模块与加载

```slang
import "math_utils.slang" as math
from "math_utils.slang" import square, cube

println(str(math.square(6)))
println(str(square(7)))
```

- `import "路径" as 名字` → `(def 名字 (import "路径"))`。
- `from "路径" import a, b` 会生成一个内部临时模块别名（`__slang_import_N`），
  再逐个绑定成员；临时别名会留在当前环境。该前缀是保留的，请勿手动定义同名变量。
- 相对路径以**写这条语句的源文件目录**为基准，和 `.lang` 一致。
- 模块看不到主程序全局变量；模块之间要协作必须显式 import。
- `load` / `load-config` 同样可用：

```slang
load "extra.slang"
load-config "slang.toml"
```

---

## 4. 到 `.lang` 的映射

| `.slang` | 等价 `.lang` |
| --- | --- |
| `def x = e` | `(def x e)` |
| `def f(a, b) = e` | `(defn f (a b) e)` |
| `def f(a):` + 块 | `(defn f (a) e1 e2 ...)` |
| `x = e` / `set x = e` | `(set x e)` |
| `if c:` + 块 + `else:` + 块 | `(if c (do ...) (do ...))` |
| `when c:` + 块 | `(when c (do ...))` |
| `do:` + 块 | `(do ...)` |
| `a + b` | `(+ a b)` |
| `-a` | `(- a)` |
| `a if c else b` | `(if c a b)` |
| `[a, b]` | `(list a b)` |
| `xs[i]` | `(nth xs i)` |
| `f(a, b)` | `(f a b)` |
| `fn(a) = e` | `(fn (a) e)` |
| `import "m" as m` | `(def m (import "m"))` |
| `from "m" import a` | `(do (def __slang_import_1 (import "m")) (def a __slang_import_1.a))` |
| `f"x = {e}"` | `(concat "x = " e)` |
| `defmacro m(a):` + 块 | `(defmacro m (a) ...)` |

`slang decode` 输出的 `.lang` 带 `@sexpr` 头，可以直接用
`python3 -m slang run out.lang` 运行。解码不会保留注释和源码空白。
如果源文件里有相对 `import` / `load`，请把输出放在源文件同目录（或手动
改成正确的相对路径），否则运行时相对路径会按输出文件的位置解析。

```sh
python3 -m slang decode examples/slang_demo.slang
python3 -m slang decode examples/slang_demo.slang -o examples/slang_demo.decoded.lang --width 60
```

运行时不会“先打印成 `.lang` 再重新解析”：`.slang` 直接解析成同一棵 AST，
`decode` 只是可选的导出/检查工具。

---

## 5. 错误处理

所有错误都带文件、行、列，且不会打印 Python traceback。常见错误：

| 情况 | 提示 |
| --- | --- |
| 缩进对不齐 | `缩进不匹配：这一行缩进 N 列，无法与外层对齐` |
| 空格/制表符混用 | `缩进里混用了空格和制表符` |
| 块头缺少 `:` | `if 条件后面需要 ':'` |
| 表达式里写 `=` | `相等比较请用 '=='；'=' 只用于 def/set 赋值` |
| 链式比较 | `不支持链式比较；请写成 ...` |
| 切片 | `slang 不支持切片；请用 take/drop 或循环构造新列表` |
| 关键字参数 | `slang 不支持关键字参数；请按位置传参` |
| 参数默认值 | `slang 不支持参数默认值；请在函数体里用 if 处理` |
| 解构赋值 | `slang 不支持解构赋值或逗号表达式；请分开赋值` |
| 花括号 / 字典字面量 | `.slang` 不支持花括号（没有字典字面量） |
| `return` | `slang 函数没有 return：函数体最后一个表达式的值就是返回值` |
| `for` / `while` | `slang 没有内置循环：请用 range/map/filter/reduce 或递归` |
| 括号未闭合 | `括号未闭合：'('` |
| f-string 插值未闭合 | `f-string 的插值缺少 '}'` |
| `else` 没有 `if` | `'else' 必须紧跟在 if 块之后...` |

---

## 6. 与 `.lang` 的差异与限制

`.slang` 是语法糖，不引入新的运行时能力。当前刻意不支持：

- `return`、`while`、`for`、`break`、`continue`；
- 字典/结构体**字面量**（`{}`）；`ffi` 返回的 dict 仍然可以用 `type`/索引访问；
- 切片 `xs[a:b]`、索引赋值、关键字参数、参数默认值、解构赋值；
- 链式比较、`?:` 三元运算符（用 `a if c else b`）；
- 宏的 quasiquote/卫生性（与 `.lang` 的限制相同）。

已知边界：

- 点号成员名只能作为名字的一部分（`m.x`、`a.b.c`），不支持 `xs[0].x` 这类后缀成员访问；请先 `item = xs[0]`，再写 `item.x`。
- `.slang` 的语法关键字（`def`、`if`、`fn`、`and`、`or` 等）在语法层面保留；可以 `def if = ...` 覆盖绑定，但不能用本格式把它们当普通函数值或调用（`not` 例外，可作值传给 `map`）。需要完全的关键字覆盖请用 `.lang`。
- 同一行的单语句块只能有一条语句；需要多条请用缩进块。
- 带缩进块的 `fn` 只能出现在语句层（赋值右侧、`def` 的 `=` 右侧等）；括号内部不产生缩进 token，因此 `f(fn(x): ...)` 不支持，参数表里请写单行 `fn(x) = ...`。
- f-string 插值表达式不能跨行（三引号 f-string 的**文本**可以跨行）。
- 分号不能出现在括号内部。
- `decode` 不保留注释；生成的 `.lang` 是语义等价，不是逐字符还原。

---

## 7. 完整示例

`examples/slang_demo.slang`：

```slang
import "slang_math.slang" as math
from "slang_math.slang" import square

def fib(n):
    if n < 2:
        n
    else:
        fib(n - 1) + fib(n - 2)

def classify(n):
    if n < 0:
        "negative"
    elif n == 0:
        "zero"
    else:
        "positive"

xs = [1, 2, 3, 4, 5]
println(f"fib(15) = {fib(15)}")
println(f"square(9) = {square(9)}, math.cube(3) = {math.cube(3)}")
println(f"xs = {xs}")
println(f"sum = {reduce((fn (a, b) = a + b), 0, xs)}")
```

运行与解码：

```sh
python3 -m slang run examples/slang_demo.slang
python3 -m slang decode examples/slang_demo.slang -o "$TMPDIR/slang_demo.lang"
python3 -m slang run "$TMPDIR/slang_demo.lang"
```
