#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pure_profile.py —— 「纯算 profile」的静态部分 + 三条取 profile 的通路

背景（README.md 的算法一节）
--------------------------
    ck = '005_' + radix64( encrypt_96(record93 + PKCS#7(3), rk, iv, mask), alphabet, xreg )

其中 `rk`(32×u32) / `alphabet`(64) / `xreg` / `iv` / `mask` 是**每 build 一次**的常量。
本脚本做两件事：

A. `--static`：**不执行任何东西**，纯 Python 从 build wasm 里抠出
   · 程序字节流（10565 词）        → out/prog.bin
   · 常量池原始条目（101 条）      → out/pool_raw.json
   · 取指格式表（104 条，见 govm_makecode.py）→ out/isa.json
   并做逐字节自检（与真机内存里的词低字节对拍）。

B. 取 profile 的三条通路（都不需要真浏览器）：
   · `--from-mem <mem.bin>`  —— 从任意内存镜像抠（真机驱动 / 纯 VM / 浏览器调试都行）
   · `--from-purevm`         —— 跑 py/govm_pure_vm.js（**不加载 wasm**）落内存后抠
   · `--check <profile.json>`—— 用 profile 重算 ck，与 profile 里记的那份逐字符对拍

诚实边界（已实测，别当成 bug）
----------------------------
`rk` / `alphabet` **不是静态数据**：wasm 里搜不到、blob 里也没有；它们是那段 10565 词的
程序在运行期算出来的（alphabet 是运行期 PRNG 置换，跨 build 无关；见本文件 § 输出）。
所以本脚本的「纯算」= **零 wasm / 零浏览器**；要做到「零执行」必须把程序解释器搬到 Python，
那一步尚未完成（见 makecode 的 listing 与 docs/GOVM_DECODE_TABLE.md）。
"""
import os, sys, json, subprocess, argparse, importlib.util

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.environ.get('ZSE_ROOT') or os.path.dirname(HERE)
P = lambda *a: os.path.join(ROOT, *a)


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def get_mk():
    return _load('mk', P('py', 'govm_makecode.py'))


def get_bp():
    return _load('bp', P('py', 'build_profile.py'))


# ------------------------------------------------------------------ A 静态
def do_prepare(wasm):
    """把某个 build 的 wasm 准备成 pure-VM 能吃的产物：
       _datasegs.json（数据段）+ zse.wat + zse.decompiled.js（反编译产物）。
       这样 `--from-purevm` 就能指向**任意 build**，不再绑死 859e。"""
    wasm = os.path.abspath(wasm)
    ds = P('.probe', 'zsec', '_datasegs.json')
    wat = P('.probe', 'wt', 'zse.wat')
    dec = P('.probe', 'wt', 'zse.decompiled.js')
    print('[prepare] 1/3 数据段 -> %s' % ds)
    r = subprocess.run([sys.executable, P('py', 'gen_datasegs.py'), wasm, ds],
                       capture_output=True, text=True)
    print((r.stdout or r.stderr).strip()[-300:])
    if r.returncode:
        return False
    print('[prepare] 2/3 WAT -> %s' % wat)
    r = subprocess.run(['node', P('.probe', 'wt', 'genwat.js'), wasm, wat],
                       capture_output=True, text=True, cwd=P('.probe', 'wt'))
    print((r.stdout or r.stderr).strip()[-200:])
    if r.returncode:
        return False
    print('[prepare] 3/3 反编译 -> %s' % dec)
    env = dict(os.environ, ZSE_WAT=wat, ZSE_DECOMPILED_OUT=dec)
    r = subprocess.run([sys.executable, P('.probe', 'watdecomp.py')],
                       capture_output=True, text=True, env=env)
    print((r.stdout or r.stderr).strip()[-300:])
    return r.returncode == 0


def do_static(args):
    mk = get_mk()
    os.makedirs(args.out, exist_ok=True)
    prog, blob, pool = mk.extract_program(args.wasm)
    open(os.path.join(args.out, 'prog.bin'), 'wb').write(prog)
    json.dump([list(x) for x in pool], open(os.path.join(args.out, 'pool_raw.json'), 'w'))
    fns, isa = mk.extract_isa()
    built = {}
    for idx, rec in sorted(isa.items()):
        body = fns.get(rec['name'])
        if not body:
            continue
        d, a, npc, _w = mk.simulate(body, rec, 100, prog)
        built[idx] = dict(name=rec['name'], dispatcher=d, args=a, adv=npc - 100)
    json.dump(built, open(os.path.join(args.out, 'isa.json'), 'w'), indent=1)
    print('[static] wasm=%s' % os.path.basename(args.wasm))
    print('[static] blob=%d 字节  程序=%d 词  池=%d 条  取指格式=%d 个'
          % (len(blob), len(prog), len(pool), len(built)))
    # 自检：与真机内存镜像里的程序词对拍（有就用）
    ref = P('.probe', 'zsec', 'prog_live.bin')
    if os.path.exists(ref):
        import struct
        d = open(ref, 'rb').read()
        words = struct.unpack('<%dI' % (len(d) // 4), d)
        n = min(len(words), len(prog))
        same = sum(1 for i in range(n) if words[i] == prog[i])
        print('[static] 自检 vs prog_live.bin：%d/%d 逐字节相同' % (same, n))
    return prog


# ------------------------------------------------------------------ B 取 profile
def extract_from_mem(mem_path, out_path=None, iv='d19e4a2bc57f8603'):
    """从内存镜像抠 rk/alphabet/xreg/iv/mask。

    直接复用 `py/build_profile.py` —— 它自带「逐字符重算出 ck」的自证，
    **自证不过就不写文件**，所以这里不需要再重复实现一遍。
    """
    bp = get_bp()
    buf = open(mem_path, 'rb').read()
    rk = bp.extract_rk(buf)
    cands = bp.alphabet_candidates(buf)
    print('[profile] 内存 %s（%d 字节）' % (os.path.basename(mem_path), len(buf)))
    print('[profile] rk 候选 %d 个（需要 32）；alphabet 候选 %d 个' %
          (len(rk) if rk else 0, len(cands)))
    for c in cands:
        print('           alphabet? %s' % c)
    if not rk or not cands:
        print('[profile] 缺 rk 或 alphabet 候选 ⇒ 放弃（不猜、不写）')
        return None
    cmd = [sys.executable, P('py', 'build_profile.py'), mem_path]
    if out_path:
        cmd += ['-o', out_path]
    cmd += ['--iv', iv]
    r = subprocess.run(cmd, capture_output=True, text=True)
    print(r.stdout.strip() or r.stderr.strip())
    if r.returncode != 0:
        print('[profile] build_profile.py 自证失败（returncode=%d）' % r.returncode)
        return None
    if out_path and os.path.exists(out_path):
        prof = json.load(open(out_path, encoding='utf8'))
        print('[profile] 自证通过 → %s' % out_path)
        return prof
    return None


def do_from_mem(args):
    return extract_from_mem(args.mem, args.out, args.iv)


def do_from_purevm(args):
    """零 wasm / 零浏览器：跑纯 JS VM（反编译产物解释器）落内存，再抠 profile。"""
    env = dict(os.environ, PROBE='1', ZSE_MEMDUMP='1')
    log = open(P('.probe', 'tmp', 'purevm_profile.log'), 'w')
    print('[purevm] 运行 node py/govm_pure_vm.js（不加载任何 .wasm）…')
    r = subprocess.run(['node', P('py', 'govm_pure_vm.js')], cwd=ROOT, env=env,
                       stdout=log, stderr=subprocess.STDOUT)
    log.close()
    mem = P('.probe', 'tmp', 'mine_mem.bin')
    if r.returncode != 0 or not os.path.exists(mem):
        print('[purevm] 失败：returncode=%d，内存镜像 %s 不存在（日志见 .probe/tmp/purevm_profile.log）'
              % (r.returncode, mem))
        return None
    print('[purevm] 内存镜像已落：%s' % mem)
    return extract_from_mem(mem, args.out or P('profiles', '_from_purevm.json'), args.iv)


def do_from_wasm(args):
    """零执行：只读 build wasm，从常量池里静态抠出 `rk`(32) 与 `alphabet`(64)。

    `xreg` / `iv` / `mask` 不在池里（它们是运行期/会话量），若给了 `--sample <profile.json>`
    就沿用样本里的这三个值，拼成完整 profile 落盘。
    """
    mk = get_mk()
    sp = mk.static_profile(args.from_wasm)
    if not sp:
        print('[from-wasm] 静态抠取失败（池第 0 条不是 "Go" 或结构不符）')
        return None
    print('[from-wasm] wasm = %s' % os.path.basename(args.from_wasm))
    print('[from-wasm] 池密钥 k0=%#04x xor=%#04x；rk@池#%d  alphabet@池#%d'
          % (sp['k0'], sp['xor'], sp['idx_rk'], sp['idx_alphabet']))
    print('[from-wasm] rk[0..3] = %s' % [hex(x) for x in sp['rk'][:4]])
    print('[from-wasm] alphabet = %s' % sp['alphabet'])
    prof = {'rk': sp['rk'], 'alphabet': sp['alphabet']}
    if args.sample:
        s = json.load(open(args.sample, encoding='utf8'))
        for k in ('xreg', 'iv', 'mask'):
            if k in s:
                prof[k] = s[k]
        print('[from-wasm] xreg/iv/mask 沿用样本 %s' % os.path.basename(args.sample))
    if args.out and args.sample:
        json.dump(prof, open(args.out, 'w', encoding='utf8'), ensure_ascii=False, indent=1)
        print('[from-wasm] 已写 %s' % args.out)
    return prof


def do_check(args):
    """用 profile 重算 ck，与 profile 里记的 ck 逐字符对拍。"""
    prof = json.load(open(args.check, encoding='utf8'))
    r = subprocess.run([sys.executable, P('py', 'pure_ck.py'), args.check, '--selftest'],
                       capture_output=True, text=True)
    print(r.stdout.strip() or r.stderr.strip())
    print('[check] 期望 ck 前缀: %s' % str(prof.get('ck'))[:32])
    return r.returncode == 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--static', action='store_true', help='纯 Python 静态提取程序/池/取指表')
    ap.add_argument('--wasm', default=P('.probe', 'build-859e.wasm'))
    ap.add_argument('--out', default=P('out'))
    ap.add_argument('--prepare', default=None, metavar='WASM',
                    help='把该 build 的 wasm 准备成 pure-VM 产物（数据段/WAT/反编译）')
    ap.add_argument('--from-mem', dest='mem', default=None, help='从内存镜像抠 profile')
    ap.add_argument('--from-wasm', dest='from_wasm', default=None, metavar='WASM',
                    help='零执行：只读 wasm 静态抠 rk/alphabet（--sample 可补 xreg/iv/mask）')
    ap.add_argument('--sample', default=None, help='与 --from-wasm 搭配：沿用该 profile 的 xreg/iv/mask')
    ap.add_argument('--from-purevm', action='store_true', help='跑纯 JS VM 落内存后抠 profile')
    ap.add_argument('--check', default=None, help='用 profile 重算 ck 自证')
    ap.add_argument('--iv', default='d19e4a2bc57f8603')
    a = ap.parse_args()
    did = False
    if a.prepare:
        print('[prepare] ok=%s' % do_prepare(a.prepare)); did = True
    if a.static:
        do_static(a); did = True
    if a.mem:
        do_from_mem(a); did = True
    if a.from_wasm:
        do_from_wasm(a); did = True
    if a.from_purevm:
        do_from_purevm(a); did = True
    if a.check:
        do_check(a); did = True
    if not did:
        ap.print_help()


if __name__ == '__main__':
    main()
