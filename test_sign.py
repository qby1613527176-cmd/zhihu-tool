#!/usr/bin/env python3
"""test_sign.py — 签名段(zhihu_sign.py, AGPL)回归对拍测试
钉的是**确定性密码原语**(块加密/轮函数/循环左移)——签名本体含随机字节不可钉值,
但底层原语必须逐字节稳定;格式断言签名输出结构。
跑法: python3 test_sign.py(零依赖,直接断言,非 pytest 也可跑)"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from zhihu_sign import _encrypt_block, _G, _rotl32, sign, encrypt  # noqa: E402

FAIL = []


def check(name, got, want):
    ok = got == want
    print(f"  {'✅' if ok else '❌'} {name}")
    if not ok:
        FAIL.append(f"{name}: 得 {got!r}, 期望 {want!r}")


print("== 确定性原语对拍 ==")
check("_encrypt_block(range(16))",
      _encrypt_block(list(range(16))),
      [8, 147, 88, 133, 246, 37, 39, 40, 78, 171, 69, 186, 197, 48, 151, 151])
check("_G(0x12345678)", hex(_G(0x12345678)), "0xdf198dff")
check("_rotl32(0x12345678, 8)", hex(_rotl32(0x12345678, 8)), "0x34567812")

print("== 签名输出结构 ==")
sig = sign("/api/v4/questions/19550225", "fixed_d_c0")
check("sign 前缀 2.0_", sig[:4], "2.0_")
check("sign 长度 68", len(sig), 68)
check("sign 字符集在定制字母表内",
      all(c in "6fpLRqJO8M/c3jnYxFkUVC4ZIG12SiH=5v0mXDazWBTsuw7QetbKdoPyAl+hN9rgE" for c in sig[4:]),
      True)

print()
if FAIL:
    print("❌ 回归失败:")
    for f in FAIL:
        print("  -", f)
    sys.exit(1)
print("✅ 签名段回归对拍全部通过(原语逐字节稳定+输出结构合规)")
