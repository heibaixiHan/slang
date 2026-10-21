"""token 流 -> AST。

两种解析模式：
  sexpr —— 严格 S 表达式：(add 1 2)（不接受 [] {} 与逗号）
  auto  —— 通用宽松转换（供未适配格式使用）：
           兼容 f(a, b)、[ ] { } 分组、逗号分隔、
           以及 + - * / % < <= > >= == != 中缀运算（含顶层表达式）。
"""
from .ast_nodes import Call, Nil, Num, Str, Sym
from .errors import ParseError

OP_SET = {"+", "-", "*", "/", "%", "<", "<=", ">", ">=", "==", "!="}
# 数值越大优先级越高；相等性运算低于关系运算，与 C/Python 一致。
PRECEDENCE = {
    "==": 0,
    "!=": 0,
    "<": 1,
    "<=": 1,
    ">": 1,
    ">=": 1,
    "+": 2,
    "-": 2,
    "*": 3,
    "/": 3,
    "%": 3,
}
CLOSE_OF = {"LP": "RP", "LB": "RB", "LC": "RC"}


def _node_tok(node):
    """把 AST 节点伪装成 token，用于错误定位。"""
    return ("NODE", "", getattr(node, "line", None), getattr(node, "col", None), None)


class _Parser:
    def __init__(self, tokens, mode, filename):
        self.tokens = tokens
        self.i = 0
        self.mode = mode
        self.filename = filename

    def peek(self):
        return self.tokens[self.i] if self.i < len(self.tokens) else None

    def take(self):
        t = self.tokens[self.i]
        self.i += 1
        return t

    def error(self, message, tok=None):
        line = tok[2] if tok else None
        col = tok[3] if tok else None
        raise ParseError(message, self.filename, line, col)

    def parse_forms(self):
        forms = []
        need_form = True
        while True:
            t = self.peek()
            if t is None:
                return forms
            if t[0] in ("RP", "RB", "RC"):
                self.error("多余的右括号 %r" % t[1], t)
            if t[0] == "COMMA":
                if self.mode != "auto":
                    self.error("逗号 ',' 只能用于 @auto 格式", t)
                if need_form:
                    self.error("逗号前缺少表达式", t)
                self.take()
                need_form = True
                continue
            if self.mode == "auto":
                forms.append(self.parse_auto_form())
            else:
                forms.append(self.parse_expr())
            need_form = False

    def parse_auto_form(self):
        """auto 模式顶层：解析一个表达式，若后面跟中缀运算符则折叠。"""
        first = self.parse_expr()
        t = self.peek()
        if t is None or not self.is_operator(t):
            return first
        items = [("node", first)]
        while True:
            t = self.peek()
            if t is None or not self.is_operator(t):
                break
            self.take()
            items.append(("op", t[1], t))
            nxt = self.peek()
            if nxt is None or nxt[0] in ("RP", "RB", "RC"):
                self.error("运算符 %r 后缺少表达式" % t[1], t)
            items.append(("node", self.parse_expr()))
        return self.fold(items, None)

    def is_operator(self, tok):
        if tok[0] == "OP":
            return True
        if tok[0] == "SYM" and self.mode == "auto" and tok[1] in OP_SET:
            return True
        return False

    def parse_expr(self):
        t = self.peek()
        if t is None:
            self.error("意外的文件结尾")
        kind = t[0]

        if kind in ("LP", "LB", "LC"):
            if self.mode != "auto" and kind != "LP":
                self.error("@sexpr 只接受圆括号，不接受 %r" % t[1], t)
            self.take()
            return self.finish_group(t)

        if kind == "STR":
            self.take()
            return Str(t[1], t[2], t[3], self.filename)

        if kind == "NUM":
            self.take()
            return Num(t[1], t[2], t[3], self.filename)

        if kind == "SYM":
            self.take()
            node = Sym(t[1], t[2], t[3], self.filename)
            nxt = self.peek()
            if self.mode == "auto":
                return self.maybe_call(node, t)
            if (
                nxt is not None
                and nxt[0] in ("LP", "LB", "LC")
                and nxt[2] == t[2]
                and nxt[3] == t[4]
            ):
                self.error(
                    "这里像是函数调用语法 %s(...)，与 @sexpr 格式不符；"
                    "请在文件首行声明 @auto，或改写为 (%s ...)" % (t[1], t[1]),
                    t,
                )
            return node

        if kind == "OP":
            if self.mode == "sexpr":
                self.take()
                return Sym(t[1], t[2], t[3], self.filename)
            if t[1] in ("+", "-"):
                # auto 词法器不再把 +/- 吞进数字；这里把前缀符号解析成
                # 一元表达式，于是 `1 +2` / `1 -2` 与 `1 *2` 一致。
                self.take()
                nxt = self.peek()
                if nxt is None or nxt[0] in ("RP", "RB", "RC", "COMMA"):
                    self.error("运算符 %r 后缺少表达式" % t[1], t)
                operand = self.parse_expr()
                if t[1] == "+":
                    return operand
                return Call(
                    Sym("-", t[2], t[3], self.filename),
                    [operand],
                    t[2], t[3], self.filename,
                )
            self.error("表达式不能以运算符 %r 开头" % t[1], t)

        if kind == "COMMA":
            self.error("逗号 ',' 只能用于 @auto 格式", t)

        self.error("多余的右括号 %r" % t[1], t)

    def maybe_call(self, node, tok):
        nxt = self.peek()
        if (
            nxt is not None
            and nxt[0] in ("LP", "LB", "LC")
            and nxt[2] == tok[2]
            and nxt[3] == tok[4]
        ):
            self.take()
            close = CLOSE_OF[nxt[0]]
            items = self.collect(close, nxt, keep_commas=True)
            return Call(node, self.arglist(items, nxt), tok[2], tok[3], self.filename)
        return node

    def arglist(self, items, open_tok):
        """把参数表条目按逗号分块：有运算符的块折叠为一个表达式，否则逐个作为参数。"""
        args = []
        chunk = []
        comma_count = 0
        last_was_comma = False
        for pos, it in enumerate(items):
            if it[0] == "comma":
                if not chunk:
                    if not args and comma_count == 0:
                        self.error("参数表不能以逗号开头（空参数）", it[2])
                    if last_was_comma:
                        self.error("连续逗号产生空参数", it[2])
                    # 末尾逗号是允许的；中间的空块由下面 last_was_comma 判断。
                else:
                    self.flush_chunk(chunk, args, open_tok)
                comma_count += 1
                last_was_comma = True
            else:
                if last_was_comma:
                    last_was_comma = False
                chunk.append(it)
        if chunk:
            self.flush_chunk(chunk, args, open_tok)
        elif items and last_was_comma and not args:
            # 只有逗号，例如 f(,)
            self.error("参数表不能只包含逗号（空参数）", open_tok)
        return args

    def flush_chunk(self, chunk, args, open_tok):
        if any(it[0] == "op" for it in chunk):
            args.append(self.fold(chunk, open_tok))
        else:
            args.extend(it[1] for it in chunk)
        chunk.clear()

    def collect(self, close_kind, open_tok, keep_commas=False):
        """收集分组内的条目，直到遇到对应的右括号。"""
        items = []
        while True:
            t = self.peek()
            if t is None:
                self.error("括号未闭合（缺少 %r）" % close_kind, open_tok)
            if t[0] == close_kind:
                self.take()
                return items
            if t[0] in ("RP", "RB", "RC"):
                self.error("括号不匹配: 遇到 %r" % t[1], t)
            if t[0] == "COMMA":
                if self.mode != "auto":
                    self.error("逗号 ',' 只能用于 @auto 格式", t)
                self.take()
                if keep_commas:
                    items.append(("comma", ",", t))
                continue
            if self.mode == "auto" and self.is_operator(t):
                self.take()
                items.append(("op", t[1], t))
            else:
                items.append(("node", self.parse_expr()))

    def finish_group(self, open_tok):
        close = CLOSE_OF[open_tok[0]]
        items = self.collect(close, open_tok)
        if any(it[0] == "op" for it in items):
            if self.mode != "auto":
                self.error("不支持的语法", open_tok)
            return self.fold(items, open_tok)
        nodes = [it[1] for it in items]
        if not nodes:
            return Nil(open_tok[2], open_tok[3], self.filename)
        if self.mode == "auto" and len(nodes) == 1:
            # auto 模式下 (x) / [x] / {x} 是分组，不是零参调用
            return nodes[0]
        return Call(nodes[0], nodes[1:], open_tok[2], open_tok[3], self.filename)

    def fold(self, items, open_tok):
        """按优先级把 节点/运算符 序列折叠为嵌套调用（运算符栈，O(n)）。"""
        # 先处理 auto 模式下位于“期望表达式”位置的一元 +/-。
        # collect() 会把所有 OP 都收成 op 项，因此这里统一归一化。
        items = self._normalize_unary(items)
        nodes = []
        ops = []
        expect_node = True
        for it in items:
            if expect_node:
                if it[0] != "node":
                    self.error("运算符 %r 前缺少表达式" % it[1], it[2])
                nodes.append(it[1])
                expect_node = False
            else:
                if it[0] == "op":
                    ops.append(it)
                    expect_node = True
                else:
                    self.error("两个表达式之间缺少运算符", _node_tok(it[1]))
        if expect_node:
            self.error("运算符后缺少表达式", ops[-1][2] if ops else open_tok)
        if not ops:
            return nodes[0]
        if not nodes:
            self.error("空的分组", open_tok)

        def apply(calls, op):
            right = calls.pop()
            left = calls.pop()
            opname = op[1]
            # + - * / 都是左结合：把同一优先级的链扁平化，避免 20k 个
            # 运算符生成 20k 层 AST 导致递归爆栈。对应的原语支持 n 元折叠。
            if opname in ("+", "-", "*", "/") \
                    and isinstance(left, Call) \
                    and isinstance(left.head, Sym) and left.head.name == opname:
                left.args.append(right)
                calls.append(left)
                return
            calls.append(
                Call(
                    Sym(opname, op[2][2], op[2][3], self.filename),
                    [left, right],
                    getattr(left, "line", None) or op[2][2],
                    getattr(left, "col", None) or op[2][3],
                    self.filename,
                )
            )

        calls = [nodes[0]]
        stack = []
        for idx, op in enumerate(ops):
            prec = self.prec(op)
            while stack and self.prec(stack[-1]) >= prec:
                apply(calls, stack.pop())
            stack.append(op)
            calls.append(nodes[idx + 1])
        while stack:
            apply(calls, stack.pop())
        return calls[0]

    def _normalize_unary(self, items):
        """把前缀 +/- 折叠成节点；二元运算符保持原样。"""
        out = []
        expect_node = True
        i = 0
        while i < len(items):
            it = items[i]
            if it[0] == "node":
                out.append(it)
                expect_node = False
                i += 1
                continue
            if not expect_node:
                out.append(it)
                expect_node = True
                i += 1
                continue
            # 期望表达式时遇到运算符：只允许一元 +/-。
            if it[1] not in ("+", "-"):
                self.error("运算符 %r 前缺少表达式" % it[1], it[2])
            if i + 1 >= len(items) or items[i + 1][0] != "node":
                self.error("运算符 %r 后缺少表达式" % it[1], it[2])
            operand = items[i + 1][1]
            if it[1] == "-":
                operand = Call(
                    Sym("-", it[2][2], it[2][3], self.filename),
                    [operand],
                    it[2][2], it[2][3], self.filename,
                )
            out.append(("node", operand))
            expect_node = False
            i += 2
        if expect_node:
            self.error("运算符后缺少表达式", items[-1][2] if items else None)
        return out

    def prec(self, op_item):
        name = op_item[1]
        if name not in PRECEDENCE:
            self.error("不支持的运算符 %r" % name, op_item[2])
        return PRECEDENCE[name]


def parse(tokens, mode="sexpr", filename=None):
    return _Parser(tokens, mode, filename).parse_forms()
