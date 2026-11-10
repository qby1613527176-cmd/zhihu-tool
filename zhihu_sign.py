"""
zhihu_sign.py — 知乎 x-zse-96 v3 签名(SM4 变体 + 定制 Base64)

本文件以 **GNU Affero General Public License v3.0 (AGPL-3.0)** 许可——
其实现逐行移植自 RSSHub `lib/utils/zhihu.ts`(AGPL-3.0 项目),
签名配方: md5("101_3_3.0" + path + d_c0) → SM4 变体加密 → 定制 Base64 + XOR 58。
算法思想不受版权保护,但本文件的代码文本与 RSSHub 原作构成演绎关系,
故单独以 AGPL-3.0 发布,与主项目(MIT)的其它部分相隔离。

源自: https://github.com/DIYgod/RSSHub (AGPL-3.0)
"""
import hashlib
import random

# ===== x-zse-96 加密端口(RSSHub v3 血统) =====
# 轮密钥 zk(32×i32)与 S 盒 zb(256B)——自定义常量,非国标 SM4 参数
_ZK_SIGNED = [
    1170614578, 1024848638, 1413669199, -343334464, -766094290, -1373058082,
    -143119608, -297228157, 1933479194, -971186181, -406453910, 460404854,
    -547427574, -1891326262, -1679095901, 2119585428, -2029270069, 2035090028,
    -1521520070, -5587175, -77751101, -2094365853, -1243052806, 1579901135,
    1321810770, 456816404, -1391643889, -229302305, 330002838, -788960546,
    363569021, -1947871109,
]
ZK = [z & 0xFFFFFFFF for z in _ZK_SIGNED]

ZB = [
    20, 223, 245, 7, 248, 2, 194, 209, 87, 6, 227, 253, 240, 128, 222, 91,
    237, 9, 125, 157, 230, 93, 252, 205, 90, 79, 144, 199, 159, 197, 186, 167,
    39, 37, 156, 198, 38, 42, 43, 168, 217, 153, 15, 103, 80, 189, 71, 191, 97,
    84, 247, 95, 36, 69, 14, 35, 12, 171, 28, 114, 178, 148, 86, 182, 32, 83,
    158, 109, 22, 255, 94, 238, 151, 85, 77, 124, 254, 18, 4, 26, 123, 176, 232,
    193, 131, 172, 143, 142, 150, 30, 10, 146, 162, 62, 224, 218, 196, 229, 1,
    192, 213, 27, 110, 56, 231, 180, 138, 107, 242, 187, 54, 120, 19, 44, 117,
    228, 215, 203, 53, 239, 251, 127, 81, 11, 133, 96, 204, 132, 41, 115, 73, 55,
    249, 147, 102, 48, 122, 145, 106, 118, 74, 190, 29, 16, 174, 5, 177, 129,
    63, 113, 99, 31, 161, 76, 246, 34, 211, 13, 60, 68, 207, 160, 65, 111, 82,
    165, 67, 169, 225, 57, 112, 244, 155, 51, 236, 200, 233, 58, 61, 47, 100,
    137, 185, 64, 17, 70, 234, 163, 219, 108, 170, 166, 59, 149, 52, 105, 24,
    212, 78, 173, 45, 0, 116, 226, 119, 136, 206, 135, 175, 195, 25, 92, 121,
    208, 126, 139, 3, 75, 141, 21, 130, 98, 241, 40, 154, 66, 184, 49, 181, 46,
    243, 88, 101, 183, 8, 23, 72, 188, 104, 179, 210, 134, 250, 201, 164, 89,
    216, 202, 220, 50, 221, 152, 140, 33, 235, 214,
]

_FIX = [48, 53, 57, 48, 53, 51, 102, 55, 100, 49, 53, 101, 48, 49, 100, 55]  # "059053f7d15e01d7"
_SALT = "6fpLRqJO8M/c3jnYxFkUVC4ZIG12SiH=5v0mXDazWBTsuw7QetbKdoPyAl+hN9rgE"


def _rotl32(v: int, t: int) -> int:
    v &= 0xFFFFFFFF
    return ((v << t) & 0xFFFFFFFF) | (v >> (32 - t))


def _G(e: int) -> int:
    b = [(e >> 24) & 255, (e >> 16) & 255, (e >> 8) & 255, e & 255]
    r = (ZB[b[0]] << 24) | (ZB[b[1]] << 16) | (ZB[b[2]] << 8) | ZB[b[3]]
    return r ^ _rotl32(r, 2) ^ _rotl32(r, 10) ^ _rotl32(r, 18) ^ _rotl32(r, 24)


def _encrypt_block(block16: list[int]) -> list[int]:
    """__g.r: 单块 32 轮加密,反序输出"""
    n = [0] * 36
    for k in range(4):
        n[k] = ((block16[4*k] & 255) << 24 | (block16[4*k+1] & 255) << 16
                | (block16[4*k+2] & 255) << 8 | (block16[4*k+3] & 255))
    for r in range(32):
        n[r+4] = n[r] ^ _G(n[r+1] ^ n[r+2] ^ n[r+3] ^ ZK[r])
    out = [0] * 16
    for k, idx in enumerate((35, 34, 33, 32)):
        v = n[idx]
        out[4*k] = (v >> 24) & 255
        out[4*k+1] = (v >> 16) & 255
        out[4*k+2] = (v >> 8) & 255
        out[4*k+3] = v & 255
    return out


def _cbc(data: list[int], iv16: list[int]) -> list[int]:
    """__g.x: CBC 模式逐块加密"""
    out: list[int] = []
    t = list(iv16)
    i = 0
    remaining = len(data)
    while remaining > 0:
        block = [data[16*i + c] ^ t[c] for c in range(16)]
        t = _encrypt_block(block)
        out.extend(t)
        i += 1
        remaining -= 16
    return out


def _preprocess(md5str: str) -> list[int]:
    """md5 hex → 48 字节密文"""
    arr = [ord(c) for c in md5str]
    arr.insert(0, 0)
    arr.insert(0, int(random.random() * 127))
    arr.extend([14] * 15)                     # 总长 2+32+15=49
    front = [arr[k] ^ _FIX[k] ^ 42 for k in range(16)]
    block1 = _encrypt_block(front)
    rest = _cbc(arr[16:48], block1)           # 索引 16..47 = 32 字节 = 2 块
    return block1 + rest                      # 48 字节


def _encode24(param: int) -> str:
    return "".join(_SALT[(param >> x) & 63] for x in (0, 6, 12, 18))


def encrypt(md5str: str) -> str:
    """encrypt_zse: 逆序取字节 + XOR 掩码 58 循环 + 自定义 Base64"""
    processed = _preprocess(md5str)
    current = 0
    out = []
    for i in range(len(processed)):
        pop = processed[len(processed) - 1 - i]
        i4, i3 = i % 4, i % 3
        c = (58 >> (8 * i4)) & 255            # 58>>8/16/24=0 → 仅每4字节异或一次58
        current |= (pop ^ c) << (8 * i3)
        if i3 == 2:
            out.append(_encode24(current))
            current = 0
    return "".join(out)


def sign(path_with_query: str, d_c0: str,
         body: str | None = None, x_zst_81: str | None = None) -> str:
    """生成 x-zse-96 签名头值"""
    source = "+".join(x for x in ("101_3_3.0", path_with_query, d_c0, body, x_zst_81) if x)
    md5hex = hashlib.md5(source.encode("utf-8")).hexdigest()
    return "2.0_" + encrypt(md5hex)


