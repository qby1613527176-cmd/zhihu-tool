#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""zse_ck.py —— 单一入口。常用流程各一条命令，不用记一堆脚本名。

    python py/zse_ck.py token            抓当次 build → 零执行推常量 → 出 ck → 提交（看 Location）
    python py/zse_ck.py token --dry      同上，但**不提交**（只打印 ck 与 cookie）
    python py/zse_ck.py consts <wasm>    只打印该 build 的每-build 常量（rk / alphabet / xreg / mask / iv）
    python py/zse_ck.py trace            跑纯 JS VM 落轨迹 → 语义对拍真机轨迹（一步）
    python py/zse_ck.py trace --listing  额外产出「轨迹驱动的权威反汇编」out/prog.listing.trace.txt
    python py/zse_ck.py check            全部验收门（--fast 跳过慢门）
    python py/zse_ck.py driver [--dry]   对照路径：用 Node 驱动跑真机代码出 ck

`token` / `consts` / `check` 只是转发到既有脚本；`trace` 把「跑 VM + 对拍」合成一步。
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.environ.get('ZSE_ROOT') or os.path.dirname(HERE)
P = lambda *a: os.path.join(ROOT, *a)
PY = sys.executable
NODE = 'node'


def _run(cmd, env=None, quiet=False):
    e = dict(os.environ)
    if env:
        e.update(env)
    r = subprocess.run(cmd, cwd=ROOT, env=e,
                       stdout=subprocess.DEVNULL if quiet else None)
    return r.returncode


def cmd_token(argv):
    return _run([PY, P('py', 'derive_and_submit.py')] + argv)


def cmd_consts(argv):
    if not argv:
        print('用法: python py/zse_ck.py consts <build.wasm>')
        return 2
    return _run([PY, P('py', 'derive_session.py')] + argv)


def cmd_check(argv):
    return _run([PY, P('tools', 'verify_all.py')] + argv)


def cmd_driver(argv):
    return _run([PY, P('py', 'fresh_ck.py')] + argv)


def cmd_trace(argv):
    """一步：跑纯 JS VM（PROBE=1 落轨迹）→ 语义对拍真机轨迹。"""
    listing = '--listing' in argv
    print('· 跑纯 JS VM（PROBE=1，轨迹落 .probe/tmp/）')
    rc = _run([NODE, P('py', 'govm_pure_vm.js')], env={'PROBE': '1'})
    if rc != 0:
        print('  纯 VM 退出码 %d' % rc)
    dop = P('.probe', 'tmp', 'mine_dop.txt')
    n = sum(1 for _ in open(dop, encoding='utf8', errors='replace')) if os.path.exists(dop) else 0
    print('  轨迹 %d 行' % n)
    if listing:
        print('· 产出轨迹驱动反汇编')
        _run([PY, P('py', 'govm_makecode.py'), '--trace-dump', '--out', P('out')])
    print('· 语义对拍真机轨迹')
    return _run([PY, P('py', 'govm_semantics.py'), '--verify'])


CMDS = {
    'token': cmd_token,
    'consts': cmd_consts,
    'trace': cmd_trace,
    'check': cmd_check,
    'driver': cmd_driver,
}


def main():
    if len(sys.argv) < 2 or sys.argv[1] in ('-h', '--help'):
        print(__doc__.strip())
        return 0 if len(sys.argv) > 1 else 2
    cmd = sys.argv[1]
    if cmd not in CMDS:
        print('未知子命令 %r；可用：%s' % (cmd, ' / '.join(CMDS)))
        return 2
    return CMDS[cmd](sys.argv[2:])


if __name__ == '__main__':
    sys.exit(main())
