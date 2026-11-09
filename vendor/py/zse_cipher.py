"""zse_ck 005_ 密码核心 (纯 Python, 零依赖) —— 从 wasm 解释器级 trace 完整恢复

结构 (全部实测验证):
  96B 明文 = record93 + PKCS#7(3x03)
  6 块 CBC:  input_b = P_b ^ MASK ^ C_{b-1},  C_{-1} = IV
  每块 32 轮: X_{i+4} = X_i ^ L(A_i),  A_i = S(X_{i+1}^X_{i+2}^X_{i+3} ^ RK_i)
              L(x) = x ^ rotl(x,2) ^ rotl(x,10) ^ rotl(x,18) ^ rotl(x,24)   # 同标准 SM4
  输出 = X35||X34||X33||X32
  96B 密文 -> radix64(每会话 64 字符字母表, **按密文逆序取字节**) -> payload(128)

每会话参数: ROUND_KEYS(32 字)、MASK(单字节重复)、IV(16 字符)。
S 盒是**全局常量**(跨会话零冲突, 256/256 单一分量)。
(S^-1, RK) 有规范自由度: S^-1 全体异或 C 同时 RK 异或 C*0x01010101 等价; 本文件取 C=0。
"""

SBOX_INV = [
    0xd4, 0xd3, 0x6e, 0x0f, 0x19, 0x12, 0xac, 0xb9, 0x3d, 0x4b, 0x0d, 0x30, 0x98, 0xed, 0x83, 0x37,
    0xd2, 0x43, 0x11, 0x8b, 0x52, 0x07, 0x97, 0x93, 0x63, 0x47, 0x60, 0xbe, 0x75, 0x38, 0xae, 0xe4,
    0x4c, 0x6a, 0x5b, 0xe6, 0x8a, 0xab, 0xcc, 0xa9, 0xf6, 0xc7, 0x17, 0x3b, 0x58, 0xdf, 0xd7, 0x9f,
    0x54, 0xb7, 0x33, 0x0c, 0xc3, 0x50, 0x34, 0x6f, 0xef, 0x27, 0xcf, 0x18, 0xd0, 0x51, 0x9a, 0xa5,
    0x94, 0x02, 0x87, 0x0e, 0x92, 0x4a, 0x68, 0x03, 0x86, 0x2c, 0xa2, 0x40, 0xda, 0x8e, 0x62, 0xb1,
    0x26, 0x76, 0xf9, 0x36, 0x01, 0x9c, 0x48, 0x31, 0xa3, 0x32, 0x7a, 0x79, 0x7f, 0x45, 0x28, 0x61,
    0xf7, 0x96, 0x7e, 0xc6, 0x65, 0xa7, 0xa4, 0xf0, 0x59, 0xf5, 0x04, 0xff, 0xbb, 0xf4, 0x4d, 0x3c,
    0xfa, 0xaa, 0xdd, 0x74, 0x1d, 0xe1, 0xc1, 0x91, 0xdc, 0xe7, 0x25, 0xb0, 0xc4, 0xa6, 0xd8, 0x3f,
    0x56, 0x49, 0x1a, 0x84, 0xa8, 0xb3, 0xea, 0x2d, 0x95, 0x0a, 0x0b, 0xc9, 0x5c, 0x10, 0x2f, 0x8f,
    0xa0, 0x73, 0x99, 0xfb, 0xc8, 0xec, 0x9d, 0xc5, 0xe5, 0x7c, 0xe8, 0x1e, 0x16, 0x55, 0x15, 0x6c,
    0xd1, 0xca, 0x5e, 0x08, 0x23, 0x82, 0x1f, 0x00, 0x14, 0x53, 0x64, 0xb6, 0x66, 0x5f, 0x7d, 0x09,
    0x2b, 0x29, 0x6d, 0xf8, 0x90, 0xe9, 0xb8, 0xdb, 0x3e, 0xa1, 0xeb, 0xf1, 0x9b, 0xd9, 0x7b, 0x41,
    0xfe, 0xd6, 0x8d, 0xc2, 0x85, 0xf2, 0x6b, 0xbf, 0x5d, 0x22, 0x39, 0x71, 0x88, 0x06, 0xde, 0xc0,
    0x80, 0xf3, 0xd5, 0xe0, 0xce, 0x05, 0x35, 0xb2, 0x72, 0x57, 0x8c, 0xb5, 0x13, 0xfc, 0x4f, 0x24,
    0x1b, 0xcd, 0x46, 0x78, 0x4e, 0xe3, 0xba, 0x1c, 0xbc, 0x21, 0x5a, 0x89, 0x44, 0x20, 0xbd, 0xaf,
    0x70, 0xcb, 0x9e, 0xb4, 0xe2, 0x2e, 0x2a, 0x81, 0x3a, 0x67, 0xad, 0xee, 0xfd, 0x77, 0x42, 0x69,
]
SBOX = [0]*256
for _v,_p in enumerate(SBOX_INV): SBOX[_p]=_v

DEFAULT_MASK = 0x4D          # harness 会话值
DEFAULT_IV   = b"d19e4a2bc57f8603"   # harness 会话值 (由 record 前 40 字符派生)


def _rotl(x, r):
    return ((x << r) | (x >> (32 - r))) & 0xffffffff

def _L(x):
    return x ^ _rotl(x, 2) ^ _rotl(x, 10) ^ _rotl(x, 18) ^ _rotl(x, 24)

def encrypt_block(block16, rk, sbox=SBOX):
    X = [int.from_bytes(block16[4*i:4*i+4], 'big') for i in range(4)]
    for i in range(32):
        w = X[i+1] ^ X[i+2] ^ X[i+3] ^ rk[i]
        a = int.from_bytes(bytes(sbox[b] for b in w.to_bytes(4, 'big')), 'big')
        X.append(X[i] ^ _L(a))
    return (X[35].to_bytes(4,'big') + X[34].to_bytes(4,'big') +
            X[33].to_bytes(4,'big') + X[32].to_bytes(4,'big'))

def decrypt_block(block16, rk, sbox=SBOX):
    X = [0]*36
    X[35] = int.from_bytes(block16[0:4], 'big'); X[34] = int.from_bytes(block16[4:8], 'big')
    X[33] = int.from_bytes(block16[8:12], 'big'); X[32] = int.from_bytes(block16[12:16], 'big')
    for i in range(31, -1, -1):
        w = X[i+1] ^ X[i+2] ^ X[i+3] ^ rk[i]
        a = int.from_bytes(bytes(sbox[b] for b in w.to_bytes(4, 'big')), 'big')
        X[i] = X[i+4] ^ _L(a)
    return b''.join(X[k].to_bytes(4,'big') for k in range(4))

def encrypt_96(pt96, rk, iv=DEFAULT_IV, mask=DEFAULT_MASK, sbox=SBOX):
    """96B 明文 -> 96B 密文 (6 块 CBC + 明文单字节掩码)"""
    assert len(pt96) == 96
    prev = iv; out = b''
    for b in range(6):
        inp = bytes(x ^ y ^ mask for x, y in zip(pt96[16*b:16*b+16], prev))
        c = encrypt_block(inp, rk, sbox); out += c; prev = c
    return out

def decrypt_96(ct96, rk, iv=DEFAULT_IV, mask=DEFAULT_MASK, sbox=SBOX):
    assert len(ct96) == 96
    prev = iv; out = b''
    for b in range(6):
        c = ct96[16*b:16*b+16]
        inp = decrypt_block(c, rk, sbox)
        out += bytes(x ^ y ^ mask for x, y in zip(inp, prev))
        prev = c
    return out

def record93_to_pt(rec93):
    """record93(93B) + PKCS#7 补 3 个 0x03 -> 96B"""
    assert len(rec93) == 93
    return bytes(rec93) + bytes([3,3,3])

def radix64_input_order(ct96):
    """radix64 编码器按**密文逆序**逐字节取 (已实测: 96/96 字节吻合)。"""
    return ct96[::-1]


# ---------------------------------------------------------------- radix64 (已完全破解)
# 编码器按**密文逆序**逐字节消费；每 3 字节一组，组 k 的基准 base = (0,24,16,8)[k%4]。
#   寄存器 V = OR( byte_j << ((base+8j) mod 32) ) & 0xFFFFFFFF      (32 位)
#   V ^= XREG                                                        (每会话 8 位常量; harness 0x30)
#   4 个字符 = V 在 (base+6k) mod 32 处的 **环形** 6 位切片, 查字母表
XREG_DEFAULT = 0x30

def _circ_slice(v, s):
    return (((v >> s) | (v << ((32 - s) % 32))) & 0xffffffff) & 63

def radix64_encode(ct96, alphabet, xreg=XREG_DEFAULT):
    """96B 密文 -> 128 字符 payload"""
    assert len(ct96) == 96 and len(alphabet) == 64
    seq = ct96[::-1]
    out = []
    for g in range(32):
        base = (0, 24, 16, 8)[g % 4]
        v = 0
        for j in range(3):
            v |= seq[g*3 + j] << ((base + 8*j) % 32)
        v = (v & 0xffffffff) ^ xreg
        for k in range(4):
            out.append(alphabet[_circ_slice(v, (base + 6*k) % 32)])
    return ''.join(out)

def radix64_decode(payload, alphabet, xreg=XREG_DEFAULT):
    """128 字符 payload -> 96B 密文"""
    inv = {c: i for i, c in enumerate(alphabet)}
    seq = [0]*96
    for g in range(32):
        base = (0, 24, 16, 8)[g % 4]
        q = [inv[payload[g*4 + k]] for k in range(4)]
        v = 0
        for k in range(4):
            s_ = (base + 6*k) % 32
            v |= ((q[k] << s_) | (q[k] >> ((32 - s_) % 32))) & 0xffffffff
        v = (v & 0xffffffff) ^ xreg
        for j in range(3):
            o = (base + 8*j) % 32
            seq[g*3 + j] = ((v >> o) | (v << ((32 - o) % 32))) & 0xff
    return bytes(seq[::-1])

def generate_token(rec93, rk, alphabet, iv=DEFAULT_IV, mask=DEFAULT_MASK,
                   xreg=XREG_DEFAULT, sbox=SBOX):
    """record93(93B) -> 完整 token  '005_' + payload(128)"""
    ct = encrypt_96(record93_to_pt(rec93), rk, iv, mask, sbox)
    return '005_' + radix64_encode(ct, alphabet, xreg)
