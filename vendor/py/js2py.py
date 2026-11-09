#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""js2py.py —— 把 `.probe/wt/zse.decompiled.js` 的**受限子集**转译成 Python。

为什么需要它
------------
反编译产物是机器生成的，语句形态很规整（赋值 / if / while / switch / 带标签 break /
return / throw / 调用 / `M8|M16|M32|M64[...]` 存取），所以可以机械转译，
不必手抄 1600 行。转译后的代码由 `py/govm_arms.py` 承载。

语义映射（只列会影响正确性的几条）
----------------------------------
  `>>>`          -> `ushr(a, b)`            （Python 没有逻辑右移）
  `===` / `!==`  -> `==` / `!=`
  `&&` / `||`    -> `and` / `or`
  `!x`           -> `(not x)`
  `0n` / `Nn`    -> Python int（BigInt 字面量）
  `$B0: { ... }` -> `try: ... except Brk as _e: if _e.label != '$B0': raise`
  `break $B0;`   -> `raise Brk('$B0')`
  `continue $L1;`-> `raise Cont('$L1')`
  `throw new Error("unreachable")` -> `raise Unreachable()`
"""
import re, sys

# ------------------------------------------------------------------ 词法
OPS = ['>>>=', '===', '!==', '>>>', '<<=', '>>=', '**=', '&&=', '||=',
       '==', '!=', '<=', '>=', '&&', '||', '<<', '>>', '+=', '-=', '*=', '/=',
       '%=', '&=', '|=', '^=', '**', '=>', '++', '--',
       '+', '-', '*', '/', '%', '&', '|', '^', '~', '!', '<', '>', '=', '?', ':',
       '(', ')', '[', ']', '{', '}', ',', ';', '.']
KW = {'function', 'let', 'var', 'const', 'if', 'else', 'while', 'do', 'for',
      'switch', 'case', 'default', 'break', 'continue', 'return', 'throw', 'new',
      'true', 'false', 'null', 'undefined', 'typeof', 'in', 'instanceof', 'void'}


def lex(s):
    out, i, n = [], 0, len(s)
    while i < n:
        c = s[i]
        if c in ' \t\r\n':
            i += 1
            continue
        if c == '/' and i + 1 < n and s[i + 1] == '/':
            while i < n and s[i] != '\n':
                i += 1
            continue
        if c == '/' and i + 1 < n and s[i + 1] == '*':
            j = s.find('*/', i + 2)
            i = (j + 2) if j >= 0 else n
            continue
        if c.isdigit():
            j = i
            while j < n and (s[j].isalnum() or s[j] == '.'):
                j += 1
            out.append(('num', s[i:j]))
            i = j
            continue
        if c == '"' or c == "'":
            j = i + 1
            while j < n and s[j] != c:
                j += 2 if s[j] == '\\' else 1
            out.append(('str', s[i:j + 1]))
            i = j + 1
            continue
        if c.isalpha() or c in '_$':
            j = i
            while j < n and (s[j].isalnum() or s[j] in '_$'):
                j += 1
            w = s[i:j]
            out.append(('kw' if w in KW else 'id', w))
            i = j
            continue
        for o in OPS:
            if s.startswith(o, i):
                out.append(('op', o))
                i += len(o)
                break
        else:
            raise SyntaxError('bad char %r' % c)
    return out


# ------------------------------------------------------------------ 表达式（Pratt）
# 二元优先级（数字越大越紧）
BIN = {'=': 1, '+=': 1, '-=': 1, '*=': 1, '/=': 1, '&=': 1, '|=': 1, '^=': 1,
       '<<=': 1, '>>=': 1, '>>>=': 1,
       '||': 3, '&&': 4, '|': 5, '^': 6, '&': 7,
       '==': 8, '!=': 8, '===': 8, '!==': 8,
       '<': 9, '>': 9, '<=': 9, '>=': 9, 'in': 9, 'instanceof': 9,
       '<<': 10, '>>': 10, '>>>': 10,
       '+': 11, '-': 11, '*': 12, '/': 12, '%': 12}
ASSIGN = {'=', '+=', '-=', '*=', '/=', '&=', '|=', '^=', '<<=', '>>=', '>>>='}


class P(object):
    def __init__(self, toks):
        self.t = toks; self.i = 0

    def peek(self, k=0):
        return self.t[self.i + k] if self.i + k < len(self.t) else (None, None)

    def next(self):
        v = self.peek(); self.i += 1; return v

    def eat(self, v):
        k, x = self.peek()
        if x != v:
            raise SyntaxError('expect %r got %r' % (v, x))
        self.i += 1

    # -- 表达式
    def expr(self, minp=0):
        left = self.unary()
        while True:
            k, x = self.peek()
            if k != 'op' or x not in BIN or BIN[x] < minp:
                break
            p = BIN[x]
            self.next()
            if x in ASSIGN:
                right = self.expr(p)          # 右结合
                left = '%s %s %s' % (left, '=' if x == '=' else x, right)
                continue
            right = self.expr(p + 1)
            left = self.binary(x, left, right)
        if self.peek()[1] == '?' and minp <= 2:
            self.next()
            a = self.expr(2); self.eat(':')
            b = self.expr(2)
            left = '(%s if %s else %s)' % (a, left, b)
        return left

    def binary(self, op, a, b):
        m = {'===': '==', '!==': '!=', '&&': 'and', '||': 'or',
             '<<': '<<', '>>': '>>', '>>>': 'USHR', '&': '&', '|': '|', '^': '^'}
        if op == '>>>':
            return 'ushr(%s, %s)' % (a, b)
        if op in m:
            return '(%s %s %s)' % (a, m[op], b)
        if op == '%':
            return '((%s) %% (%s))' % (a, b)
        return '(%s %s %s)' % (a, op, b)

    def unary(self):
        k, x = self.peek()
        if k == 'op' and x in ('-', '+', '!', '~'):
            self.next()
            v = self.unary()
            return ('(not %s)' % v) if x == '!' else '(%s%s)' % (x, v)
        if k == 'op' and x in ('++', '--'):
            self.next()
            v = self.unary()
            return '(%s %s 1)' % (v, '+' if x == '++' else '-')
        if k == 'kw' and x == 'typeof':
            self.next()
            return 'typeof(%s)' % self.unary()
        if k == 'kw' and x == 'new':
            self.next()
            return 'new ' + self.postfix()
        return self.postfix()

    def postfix(self):
        v = self.primary()
        while True:
            k, x = self.peek()
            if x == '(':
                self.next()
                args = []
                if self.peek()[1] != ')':
                    args.append(self.expr(2))
                    while self.peek()[1] == ',':
                        self.next(); args.append(self.expr(2))
                self.eat(')')
                v = '%s(%s)' % (v, ', '.join(args))
            elif x == '[':
                self.next()
                idx = self.expr(2)
                self.eat(']')
                v = '%s[%s]' % (v, idx)
            elif x == '.':
                self.next()
                nm = self.next()[1]
                v = '%s.%s' % (v, nm)
            elif x in ('++', '--'):
                self.next()
                v = '(%s %s 1)' % (v, '+' if x == '++' else '-')
            else:
                break
        return v

    def primary(self):
        k, x = self.next()
        if k == 'num':
            x = x.rstrip('n')
            return x
        if k == 'str':
            return x
        if k == 'id':
            return x
        if k == 'kw':
            if x in ('true', 'false'):
                return x.capitalize()
            if x in ('null', 'undefined'):
                return 'None'
            if x == 'new':
                return 'new ' + self.postfix()
            return x
        if x == '(':
            v = self.expr(2)
            self.eat(')')
            return '(%s)' % v
        raise SyntaxError('primary: %r' % (x,))


def expr_py(js):
    return P(lex(js)).expr(0)


# ------------------------------------------------------------------ 语句块
def stmts_py(body, indent=0, label_prefix=''):
    """把一段 JS 语句块转成 Python 源码行。用**括号深度 + 花括号深度**驱动缩进。"""
    lines = [l.strip() for l in body.split('\n')]
    out = []
    return _walk(lines, 0, len(lines), indent, out)


def _skip_block(lines, i, end):
    """从 lines[i] 开始（i 指向含 `{` 的行）返回该块结束后的下标。"""
    depth = 0
    while i < end:
        depth += lines[i].count('{') - lines[i].count('}')
        i += 1
        if depth <= 0:
            return i
    return end


def _walk(lines, i, end, ind, out):
    pad = '    ' * ind
    while i < end:
        l = lines[i]
        if not l:
            i += 1; continue
        if l == '}':
            return i
        # 带标签块： $B0: {
        m = re.match(r'^\$(\w+):\s*\{\s*$', l)
        if m:
            lbl = '$' + m.group(1)
            out.append('%stry:' % pad)
            j = _walk(lines, i + 1, end, ind + 1, out)
            if j >= len(lines) or lines[j] != '}':
                j = j
            out.append('%sexcept Brk as _e:' % pad)
            out.append('%s    if _e.label != %r:' % (pad, lbl))
            out.append('%s        raise' % pad)
            i = j + 1
            continue
        # if (cond) {  ...  } [else { ... }]
        m = re.match(r'^if \((.*)\) \{\s*$', l)
        if m:
            cond = expr_py(m.group(1))
            out.append('%sif %s:' % (pad, cond))
            j = _walk(lines, i + 1, end, ind + 1, out)
            i = j + 1
            if i < end and lines[i].startswith('} else'):
                if lines[i].rstrip().endswith('{'):
                    out.append('%selse:' % pad)
                    j = _walk(lines, i + 1, end, ind + 1, out)
                    i = j + 1
                else:
                    out.append('%selse:' % pad)
                    out.append('%s    %s' % (pad, _stmt(lines[i][7:].strip())))
                    i += 1
            elif i < end and lines[i] == '}':
                pass
            continue
        # if (cond) stmt;   （单行）
        m = re.match(r'^if \((.*)\) (.+)$', l)
        if m and not l.rstrip().endswith('{'):
            cond = expr_py(m.group(1))
            out.append('%sif %s:' % (pad, cond))
            out.append('%s    %s' % (pad, _stmt(m.group(2))))
            i += 1
            continue
        # while (cond) {
        m = re.match(r'^while \((.*)\) \{\s*$', l)
        if m:
            cond = expr_py(m.group(1))
            out.append('%swhile %s:' % (pad, cond))
            j = _walk(lines, i + 1, end, ind + 1, out)
            i = j + 1
            continue
        # while (cond) stmt;
        m = re.match(r'^while \((.*)\) (.+);$', l)
        if m:
            out.append('%swhile %s:' % (pad, expr_py(m.group(1))))
            out.append('%s    %s' % (pad, _stmt(m.group(2))))
            i += 1
            continue
        # switch (expr) {
        m = re.match(r'^switch \((.*)\) \{\s*$', l)
        if m:
            out.append('%s# switch %s' % (pad, expr_py(m.group(1))))
            j = i + 1
            depth = 1
            cur = None
            while j < end:
                s = lines[j]
                if re.match(r'^case ', s) or s.startswith('default:'):
                    cur = s
                    out.append('%s#   %s' % (pad, s))
                    j += 1
                    continue
                if s == '}':
                    break
                out.append('%s#       %s' % (pad, s))
                j += 1
            i = j + 1
            continue
        out.append('%s%s' % (pad, _stmt(l)))
        i += 1
    return i


def _stmt(js):
    s = js.rstrip(';').strip()
    m = re.match(r'^break \$(\w+)$', s)
    if m:
        return 'raise Brk(%r)' % ('$' + m.group(1))
    if s == 'break':
        return 'break'
    m = re.match(r'^continue \$(\w+)$', s)
    if m:
        return 'raise Cont(%r)' % ('$' + m.group(1))
    if s == 'continue':
        return 'continue'
    if s == 'return' or s.startswith('return '):
        v = s[6:].strip()
        return 'return' if not v else 'return ' + expr_py(v)
    if s.startswith('throw '):
        return 'raise Unreachable(%r)' % s[6:].strip()
    m = re.match(r'^let (.+)$', s)
    if m:
        parts = [x.strip() for x in m.group(1).split(',')]
        outs = []
        for p in parts:
            if '=' in p:
                a, b = p.split('=', 1)
                outs.append('%s = %s' % (a.strip(), expr_py(b)))
            else:
                outs.append('%s = 0' % p.strip())
        return '; '.join(outs)
    # 逗号序列的表达式语句
    if ';' in js:
        return '; '.join(_stmt(x) for x in js.split(';') if x.strip())
    return expr_py(s)
