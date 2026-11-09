"""__zse_ck v4 活驱动：在 wasmtime 里跑挑战 wasm，但**环境探针的结果由我们自己算**。

与 py/wdriver.py 的区别：
  * 17 条环境探针（copyBytesToGo，dstLen 45..82）不再回放捕获结果，
    而是读 wasm 内存里的探针源码 → 按「干净浏览器」模型求值 → 回写 NaN-boxed js.Value
  * #167 的 132 字节 token **不再植入**，让 wasm 现算，从内存读出
  * 其余（指针布局、非探针步、valueNew/valueLength/valueIndex）仍按捕获回放，
    以保证 ref id 序列与协议形状一致

用法:
  python py/wdriver_live.py            # 干净浏览器模型（所有 Node/jsdom/框架探针 -> 'n'）
  PROBE_MODE=node python py/wdriver_live.py   # 对照实验：假装是 Node（process 存在）
"""
import base64
import json
import os
import re
import time

from wasmtime import Config, Engine, Store, Module, FuncType, ValType, Linker

I32 = ValType.i32()
I64 = ValType.i64()
F64 = ValType.f64()

WASM = ".probe/build-859e.wasm"
CAPTURE = ".probe/browser-proto7.json"

NAN_HEAD = 0x7FF80000
T_OBJECT, T_STRING, T_SYMBOL, T_FUNCTION = 1, 2, 3, 4

MODE = os.environ.get("PROBE_MODE", "clean")

# 「干净浏览器」模型：这些名字在真实浏览器里都不存在
PRESENT_CLEAN = set()
# 对照：Node 里存在
PRESENT_NODE = {"process", "require", "module", "exports", "__dirname", "__filename"}

WINDOW_CTOR_STR = "function Window() { [native code] }"


def num(s):
    return int(s.split(":", 1)[1]) if isinstance(s, str) and s.startswith("number:") else None


class Replay:
    def __init__(self, path):
        raw = json.load(open(path, encoding="utf-8"))
        self.steps = []
        i = 0
        while i < len(raw):
            e = raw[i]
            j = i + 1
            while j < len(raw) and raw[j]["name"] == e["name"] and raw[j]["a"] == e["a"]:
                j += 1
            self.steps.append(e)
            i = j
        self.cursors = {}

    def next(self, kind):
        cur = self.cursors.get(kind, 0)
        matches = [s for s in self.steps if s["name"] == kind]
        if cur >= len(matches):
            raise RuntimeError("capture exhausted for " + kind)
        s = matches[cur]
        self.cursors[kind] = cur + 1
        return s


def main():
    cfg = Config()
    engine = Engine(cfg)
    store = Store(engine)
    module = Module.from_file(store.engine, WASM)
    rp = Replay(CAPTURE)

    mem = None
    stdout_buf = []
    events = []
    values = {}          # id -> python 值
    next_id = [0]
    probe_log = []

    def mview():
        return mem.get_buffer_ptr(store)

    def mem_write(addr, data):
        mview()[addr:addr + len(data)] = data

    def mem_read(addr, n):
        return bytes(mview()[addr:addr + n])

    def reg(v, tag):
        i = next_id[0]
        next_id[0] += 1
        values[i] = v
        boxed = NAN_HEAD | (tag << 32) | (i & 0xFFFFFFFF)
        return boxed.to_bytes(8, "little") + b"\x00" * 8

    def eval_probe(src):
        """按环境模型求值探针源码；返回 (值, typeFlag)"""
        s = src.strip()
        present = PRESENT_NODE if MODE == "node" else PRESENT_CLEAN
        m = re.match(r"return typeof ([A-Za-z_$][\w$.]*) !== 'undefined' \? 'y':'n'", s)
        if m:
            name = m.group(1)
            root = name.split(".")[0]
            ok = (root in present) or (root == "window" and name.split(".")[-1] in present)
            return ("y" if ok else "n"), T_STRING
        if "ocke" in s:
            return ("n", T_STRING)
        if "window.constructor.toString()" in s:
            return (WINDOW_CTOR_STR, T_STRING)
        if s == "getElementsByTagName":
            return ("<fn getElementsByTagName>", T_FUNCTION)
        return ("n", T_STRING)

    def fd_write(fd, iovs, cnt, nwritten):
        total = 0
        for k in range(cnt):
            base = iovs + k * 8
            ptr = int.from_bytes(mem_read(base, 4), "little")
            ln = int.from_bytes(mem_read(base + 4, 4), "little")
            total += ln
            stdout_buf.append(mem_read(ptr, ln))
        mem_write(nwritten, total.to_bytes(4, "little"))
        return 0

    def ticks():
        return time.time() * 1000.0

    def finalizeRef(ref, sp):
        rp.next("finalizeRef")

    def copyBytesToGo(sp, fsz, refA, refB, dstPtr, dstLen, dstCap, p2, l2, c2, p3, l3, c3, sp2):
        c = rp.next("copyBytesToGo")
        ca = [num(x) for x in c["a"]]
        roles = {}
        for cap_idx, run_ptr, run_len in ((0, sp, 16), (4, dstPtr, dstLen), (7, p2, l2), (10, p3, l3)):
            cap_ptr = ca[cap_idx]
            cap_len = ca[cap_idx + 1]
            if cap_ptr is not None:
                roles[cap_ptr] = (run_ptr, run_len, cap_len)

        is_probe = bool(dstLen) and dstLen >= 20 and dstLen not in (136041, 41892, 132)
        src = None
        if is_probe:
            try:
                src = mem_read(dstPtr, dstLen).decode("utf-8", "replace")
            except Exception:
                src = None

        for r in (c.get("post") or []):
            cap_ptr, cap_len, b64 = r[0], r[1], r[2]
            if cap_ptr not in roles or not b64:
                continue
            if is_probe and cap_ptr == ca[0]:
                continue          # 结果槽由我们写，不回放
            run_ptr, run_len, _ = roles[cap_ptr]
            if run_len <= 0:
                continue
            data = base64.b64decode(b64)
            if dstLen == 132:
                pass              # 活驱动：不植入 token，让 wasm 现算
            else:
                data = (data + b"\x00" * run_len)[:run_len]
                mem_write(run_ptr, data[:run_len])

        if is_probe and src is not None:
            val, tag = eval_probe(src)
            mem_write(sp, reg(val, tag))
            probe_log.append({"step": rp.cursors["copyBytesToGo"] - 1, "src": src[:70], "val": val})
        events.append(("cpy", dstLen, l2, l3))

    def valueNew(resultAddr, ctorRef, argvPtr, argc, argcPtr, x6):
        c = rp.next("valueNew")
        for r in (c.get("post") or []):
            if r[1] == 16 and r[2]:
                mem_write(resultAddr, base64.b64decode(r[2])[:16])
                break
        events.append(("new",))

    def valueSetIndex(ref, sp, val, sp2):
        rp.next("valueSetIndex")

    def valueLength(ref, sp):
        c = rp.next("valueLength")
        ret = c.get("ret") or ""
        if ret.startswith("B"):
            v = int(ret[1:], 16) & 0xFFFFFFFF
        else:
            try:
                v = int(float(ret)) & 0xFFFFFFFF
            except ValueError:
                v = 0
        return v - (1 << 32) if v >= 0x80000000 else v

    def valueIndex(ref, sp, sp2):
        c = rp.next("valueIndex")
        ret = c.get("ret") or ""
        v = int(ret[1:], 16) if ret.startswith("B") else 0
        return v & 0xFFFFFFFFFFFFFFFF

    linker = Linker(engine)
    linker.define_func("wasi_snapshot_preview1", "fd_write", FuncType([I32, I32, I32, I32], [I32]), fd_write)
    linker.define_func("gojs", "runtime.ticks", FuncType([], [F64]), ticks)
    linker.define_func("env", "syscall/js.finalizeRef", FuncType([I64, I32], []), finalizeRef)
    linker.define_func("env", "syscall/js.copyBytesToGo",
                       FuncType([I32, I32, I64, I64, I32, I32, I32, I32, I32, I32, I32, I32, I32, I32], []),
                       copyBytesToGo)
    linker.define_func("env", "syscall/js.valueSetIndex", FuncType([I64, I32, I64, I32], []), valueSetIndex)
    linker.define_func("env", "syscall/js.valueNew", FuncType([I32, I64, I32, I32, I32, I32], []), valueNew)
    linker.define_func("env", "syscall/js.valueLength", FuncType([I64, I32], [I32]), valueLength)
    linker.define_func("env", "syscall/js.valueIndex", FuncType([I64, I32, I32], [I64]), valueIndex)

    inst = linker.instantiate(store, module)
    exp = inst.exports(store)
    mem = exp["memory"]
    t0 = time.time()
    try:
        exp["_start"](store)
        print("_start completed in %.2fs" % (time.time() - t0))
    except Exception as e:
        print("_start raised after %.2fs: %s" % (time.time() - t0, str(e)[:200]))

    out = b"".join(stdout_buf)
    if out:
        print("stdout:", out[:200])
    print("PROBE_MODE =", MODE)
    print("活算的探针数:", len(probe_log))
    for p in probe_log:
        print("   step=%-4d val=%-3s src=%s" % (p["step"], p["val"], p["src"]))

    buf = bytes(mview())
    hits = []
    for m in re.finditer(rb"005_[A-Za-z0-9+/=]{128}", buf):
        s = m.group().decode()
        if s not in hits:
            hits.append(s)
    print("payload candidates:", len(hits))
    for h in hits[:3]:
        print("   ", h)
    if hits:
        open(".probe/wd-live-payload.txt", "w").write(hits[0])
        print("已写 .probe/wd-live-payload.txt")


if __name__ == "__main__":
    main()
