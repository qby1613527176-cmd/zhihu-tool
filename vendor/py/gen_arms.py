#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""gen_arms.py —— 用 py/js2py.py 把 4 个语义派发器**机械转译**成 py/govm_arms.py。

为什么用「threaded-code」而不是直接嵌套转译
--------------------------------------------
混淆器把 `$B0: { $B1: { … } }` 嵌套到 **48 层**，而 Python 里每个 `try/if` 都是一个
静态嵌套块（CPython 上限 20）⇒ 直接嵌套转译必然
`SyntaxError: too many statically nested blocks`。

`js2py.stmts_threaded()` 先把语句块解析成 IR，再展平成**基本块 + 显式跳转**：

    _pc = 0
    while True:
        if _pc == 0:   <stmts>; _pc = 3
        elif _pc == 1: _pc = 4 if cond else 5
        ...
        else:          return None

这样静态嵌套只剩 2 层，彻底绕开上限，语义（含 `break $BN` / `continue $LN` / `switch`）
由 `_pc` 跳转精确表达。

用法
----
  python py/gen_arms.py            # 重新生成 py/govm_arms.py 并自检
"""
import os, re, sys, ast, json, collections, importlib.util

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.environ.get('ZSE_ROOT') or os.path.dirname(HERE)
P = lambda *a: os.path.join(ROOT, *a)

DISPATCHERS = ['00oooO', 'OOo0o0', 'O0O00o', '0OO0Oo']
DECOMPILED = P('.probe', 'wt', 'zse.decompiled.js')


def getfn(name):
    src = open(DECOMPILED, encoding='utf8').read()
    m = re.search(r'^function ' + re.escape(name) + r'\(', src, re.M)
    if not m:
        return None, 0, 0
    i = src.index('{', m.end() - 1)
    d = 0
    for k in range(i, len(src)):
        if src[k] == '{':
            d += 1
        elif src[k] == '}':
            d -= 1
            if d == 0:
                break
    return src[i + 1:k], src[:i].count('\n') + 1, src[i + 1:k].count('\n') + 1


def main():
    spec = importlib.util.spec_from_file_location('js2py', P('py', 'js2py.py'))
    j = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(j)

    bodies, blocked = {}, []
    for nm in DISPATCHERS:
        b, ln0, nlines = getfn('__main__0oOoOO___' + nm)
        if b is None:
            blocked.append((nm, 0, 0, '未找到函数'))
            continue
        py = j.stmts_threaded(b, indent=1)
        try:
            compile('\n'.join(['def f(p0, p1=0, p2=0, p3=0, p4=0):'] + py), '<t>', 'exec')
            bodies[nm] = (py, ln0, nlines)
        except SyntaxError as e:
            blocked.append((nm, ln0, nlines, str(e)))

    calls = collections.Counter()
    for nm, (py, _, _) in bodies.items():
        for l in py:
            for c in re.findall(r'([A-Za-z_][A-Za-z0-9_]*)\s*\(', l):
                calls[c] += 1
    defined = set('D_' + d for d in bodies)
    builtin = {'if', 'while', 'return', 'print', 'range', 'len', 'int', 'str', 'Brk', 'Cont',
               'Unreachable', 'ushr', 'typeof', 'globals', 'dict', 'get'}
    unresolved = sorted(c for c in calls
                        if c not in defined and c not in builtin and not c.startswith(('M', '_h', 'l')))

    out = ['# -*- coding: utf-8 -*-',
           '"""govm_arms.py —— 由 py/gen_arms.py + py/js2py.py 从 .probe/wt/zse.decompiled.js',
           '**机械转译**的 4 个语义派发器 switch 臂（Python）。**不要手改**，改转译器后重跑：',
           '',
           '    python py/gen_arms.py',
           '',
           '控制流已展平成「`_pc` 状态机 + 显式跳转」（原因：混淆器把标签块嵌套到 48 层，',
           '直接嵌套转译会撞 CPython 的 `too many statically nested blocks`）。','',
           '已转译：' + ' / '.join('%s(%d 行 JS → %d 行 py)' % (k, bodies[k][2], len(bodies[k][0]))
                                for k in bodies),
           '未转译：' + (' / '.join('%s: %s' % (n, m) for n, _, _, m in blocked) or '无'), '',
           '未实现：STUBS 里的宿主/运行时 helper（Go 运行时 + syscall/js）——',
           '        这是「零执行纯 Python 解释器」剩下的最后一块。','"""','',
           'class Brk(Exception):',
           '    def __init__(self, label): self.label = label',
           'class Cont(Exception):',
           '    def __init__(self, label): self.label = label',
           'class Unreachable(Exception): pass',
           'def ushr(a, b): return (int(a) & 0xffffffff) >> b',
           'def typeof(x): return type(x).__name__','',
           'STUBS = %r' % (unresolved,),'',
           'def _stub(name):',
           '    def f(*a, **k): raise NotImplementedError("helper 未实现: " + name)',
           '    return f',
           'for _n in STUBS:',
           '    globals()[_n] = _stub(_n)','']
    for nm in bodies:
        py, ln0, nlines = bodies[nm]
        out += ['# ' + '=' * 72,
                '# %s  (源: .probe/wt/zse.decompiled.js 第 %d 行起, %d 行 JS)' % (nm, ln0, nlines),
                '# ' + '=' * 72,
                'def D_%s(p0, p1=None, p2=0, p3=0, p4=0, sp=None):' % nm] + py + ['']
    out += ['DISPATCHERS = {' + ', '.join("'%s': D_%s" % (k, k) for k in bodies) + '}', '']
    text = '\n'.join(out) + '\n'
    ast.parse(text)
    open(P('py', 'govm_arms.py'), 'w', encoding='utf8').write(text)

    print('[gen] py/govm_arms.py  %d 行' % text.count('\n'))
    for nm in DISPATCHERS:
        if nm in bodies:
            print('      %-8s ✅ JS %4d 行 → py %4d 行' % (nm, bodies[nm][2], len(bodies[nm][0])))
    for nm, ln0, nlines, msg in blocked:
        print('      %-8s ⛔ JS %4d 行: %s' % (nm, nlines, msg))
    print('[gen] 未实现 helper %d 个: %s' % (len(unresolved), unresolved))


if __name__ == '__main__':
    main()
