#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""govm_semantics.py —— Go VM 的**语义派发层**（Python）

在 `govm_makecode.py` 的取指层之上，把「程序字节 → elem → 派发器 → 语义 op + 操作数」
这条两级映射做成可调用的 Python API，并用**真机轨迹**逐条对拍。

    elem   = 1 if byte == 0 else 105 - byte      （运行时分派表 table[b].f1 的实测形态）
    (disp, op, operands) = decode(elem, pc)       （执行该 elem 的 $bound 取指器）

实测验收（build-859e，`_start_command_export` 绑定循环 + 4 个派发器入口打点）：

    4 个派发器合计 **37303 / 37303 = 1.000000** 条 op 与真机完全一致
      _0OO0Oo  16679/16679      _00oooO  2446/2446
      _OOo0o0  12428/12428      _O0O00o  5750/5750

其中 31553 条在「轨迹记录的 pc」上直接命中；5750 条因为调用方是长指令（len 8/10），
轨迹里的 pc 落在指令中部，需要回退到**绑定循环给出的指令起始 pc** 才能命中。

用法
----
  python py/govm_semantics.py --decode 100            # 反汇编 pc=100 这一条
  python py/govm_semantics.py --arms                  # 列出 4 个派发器的 switch 臂清单
  python py/govm_semantics.py --verify                # 用真机轨迹对拍（期望 1.000000）
  python py/govm_semantics.py --listing --out out     # 产出带语义 op 的反汇编
"""
import os, re, sys, json, bisect, argparse, importlib.util

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.environ.get('ZSE_ROOT') or os.path.dirname(HERE)
P = lambda *a: os.path.join(ROOT, *a)

DISPATCHERS = ['00oooO', 'OOo0o0', 'O0O00o', '0OO0Oo']


def _mk():
    spec = importlib.util.spec_from_file_location('mk', P('py', 'govm_makecode.py'))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def elem_of(b):
    """程序字节 → elem 索引。"""
    return 1 if b == 0 else 105 - b


class Semantics(object):
    def __init__(self, out='out'):
        self.mk = _mk()
        self.prog = open(os.path.join(out, 'prog.bin'), 'rb').read()
        self.fns, self.isa = self.mk.extract_isa()
        self.decompiled = open(P('.probe', 'wt', 'zse.decompiled.js'), encoding='utf8').read()

    # ---------------------------------------------------------------- 两级映射
    def decode(self, pc):
        """(elem, dispatcher, op, operands, nextpc, words) —— 取指层 + 语义派发层。"""
        if not (0 <= pc < len(self.prog)):
            return None
        e = elem_of(self.prog[pc])
        r = self.isa.get(e)
        if not r:
            return None
        d, args, npc, words = self.mk.simulate(self.fns[r['name']], r, pc, self.prog)
        return dict(pc=pc, byte=self.prog[pc], elem=e,
                    fmt=r['name'].replace('__main__0oOoOO___', ''),
                    dispatcher=d, op=(args[0] if args else None),
                    operands=(args[1:] if args and len(args) > 1 else []),
                    words=words, nextpc=npc,
                    selfdisp=bool(r.get('selfdisp')))

    def listing(self, pc_max=None):
        pc, out = 0, []
        n = len(self.prog) if pc_max is None else min(pc_max, len(self.prog))
        LEN = self._lens()
        while pc < n:
            d = self.decode(pc)
            if not d:
                out.append((pc, None)); pc += 1; continue
            out.append((pc, d))
            pc += LEN.get(d['elem'], 4)
        return out

    def _lens(self):
        p = P('out', 'len_emp.json')
        if os.path.exists(p):
            return {int(k): v for k, v in json.load(open(p)).items()}
        return {}

    # ---------------------------------------------------------------- switch 臂清单
    def arms(self):
        """把 4 个派发器的 `switch (EXPR) { case N: break $BN; ... }` 臂清单抽出来，
        连同每个臂的**起始行号**，供后续把臂体逐条移植到 Python。"""
        res = {}
        for name in DISPATCHERS:
            full = '__main__0oOoOO___' + name
            m = re.search(r'^function ' + re.escape(full) + r'\(', self.decompiled, re.M)
            if not m:
                continue
            i = self.decompiled.index('{', m.end() - 1)
            d = 0
            for j in range(i, len(self.decompiled)):
                if self.decompiled[j] == '{':
                    d += 1
                elif self.decompiled[j] == '}':
                    d -= 1
                    if d == 0:
                        break
            body = self.decompiled[i:j + 1]
            base = self.decompiled[:i].count('\n') + 1
            sw = re.search(r'switch \((.+?)\) \{', body)
            cases = [(int(c.group(1)), c.group(2), base + body[:c.start()].count('\n'))
                     for c in re.finditer(r'case (\d+): break (\$B\d+);', body)]
            res[name] = dict(lines=body.count('\n') + 1, switch=(sw.group(1) if sw else None),
                             cases=cases)
        return res

    # ---------------------------------------------------------------- 对拍
    def verify(self, verbose=True):
        """用真机轨迹对拍两级映射。轨迹来自：
             .probe/tmp/mine_dop.txt    4 个派发器入口的 op
             .probe/tmp/mine_bind.txt   绑定循环给出的指令起始 pc
        """
        dop = P('.probe', 'tmp', 'mine_dop.txt')
        bind = P('.probe', 'tmp', 'mine_bind.txt')
        if not os.path.exists(dop):
            raise SystemExit('缺 %s（跑 PROBE=1 node py/govm_pure_vm.js）' % dop)
        starts = []
        if os.path.exists(bind):
            for ln in open(bind, encoding='utf8', errors='replace'):
                m = re.match(r'seq=(\d+) BIND pc=(\d+)', ln.strip())
                if m:
                    starts.append(int(m.group(2)))
            starts = sorted(set(starts))
        per, perok, direct, fallback, bad, ex = {}, {}, 0, 0, 0, []
        for ln in open(dop, encoding='utf8', errors='replace'):
            m = re.match(r'seq=(\d+) (\S+) pc=(-?\d+) op=(-?\d+)', ln.strip())
            if not m:
                continue
            tag, pc, op = m.group(2), int(m.group(3)), int(m.group(4))
            per[tag] = per.get(tag, 0) + 1
            cand = [pc]
            if starts:
                k = bisect.bisect_right(starts, pc) - 1
                if k >= 0 and starts[k] != pc:
                    cand.append(starts[k])
            hit = None
            for F in cand:
                d = self.decode(F)
                if d and d['dispatcher'] == tag and d['op'] == op:
                    hit = F
                    break
            if hit is None:
                bad += 1
                if len(ex) < 6:
                    ex.append((tag, pc, op, self.decode(pc)))
            else:
                perok[tag] = perok.get(tag, 0) + 1
                if hit == pc:
                    direct += 1
                else:
                    fallback += 1
        tot = sum(per.values())
        got = sum(perok.values())
        if verbose:
            print('[verify] 二级映射对拍（真机轨迹）：%d / %d = %.6f' % (got, tot, got / max(1, tot)))
            print('         其中轨迹 pc 直接命中 %d，回退到绑定循环指令起始 %d' % (direct, fallback))
            for k in DISPATCHERS:
                if k in per:
                    print('         %-8s %6d / %6d = %.4f' % (k, perok.get(k, 0), per[k],
                                                              perok.get(k, 0) / per[k]))
            for x in ex:
                print('         不符: tag=%s pc=%d op=%s decode=%s' % x)
        return dict(total=tot, ok=got, direct=direct, fallback=fallback, bad=bad, per=per, perok=perok)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default=P('out'))
    ap.add_argument('--decode', type=int, default=None, help='反汇编某个 pc')
    ap.add_argument('--arms', action='store_true', help='列出 4 个派发器的 switch 臂清单')
    ap.add_argument('--verify', action='store_true', help='用真机轨迹对拍（期望 1.000000）')
    ap.add_argument('--listing', action='store_true', help='产出带语义 op 的反汇编')
    a = ap.parse_args()
    S = Semantics(a.out)
    if a.decode is not None:
        print(json.dumps(S.decode(a.decode), ensure_ascii=False))
    if a.arms:
        for name, v in S.arms().items():
            print('%-8s 函数 %d 行, switch(%s), %d 个臂' % (name, v['lines'], v['switch'], len(v['cases'])))
            for op, lbl, ln in v['cases']:
                print('    op=%-4d -> %-6s  (行 %d)' % (op, lbl, ln))
    if a.verify:
        S.verify()
    if a.listing:
        rows = []
        for pc, d in S.listing():
            if not d:
                rows.append('%6d  <无格式>' % pc); continue
            rows.append('%6d  %-4d elem=%-4d %-22s op=%-6s ops=%s' %
                        (pc, d['byte'], d['elem'],
                         d['dispatcher'] or d['fmt'], d['op'], d['operands']))
        fn = os.path.join(a.out, 'prog.sem.listing.txt')
        open(fn, 'w', encoding='utf8').write('\n'.join(rows))
        print('[listing] %d 条 -> %s' % (len(rows), fn))


if __name__ == '__main__':
    main()
