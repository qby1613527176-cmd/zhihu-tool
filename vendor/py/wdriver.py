"""__zse_ck v4 payload generator: drive the per-build challenge wasm under wasmtime,
replaying the host side of the wasm_exec-derived protocol exactly as a real browser
answered it (capture: .probe/browser-proto7.json, includes per-call result-slot refs
and served content bytes).

Key protocol facts (measured):
  * copyBytesToGo(arg0=resultSlot, arg1=frameSize, refA, refB, dstPtr, dstLen, dstCap,
    p2, l2, c2, p3, l3, c3, sp2): the host writes the resulting js.Value ref (8 bytes,
    NaN-boxed) at resultSlot, fills dst/p2/p3 buffers per the operation.
  * runtime.ticks() is the clock input -> we return the CURRENT time so the minted
    payload passes the server freshness window (~minutes).
Run:  python py/wdriver.py   ->  .probe/wd-payload.txt
"""
import base64
import json
import re
import time

from wasmtime import Config, Engine, Store, Module, FuncType, ValType, Linker

I32 = ValType.i32()
I64 = ValType.i64()
F64 = ValType.f64()

WASM = ".probe/build-5e26.wasm"          # build 5e260588… (matches the capture)
CAPTURE = ".probe/browser-proto7.json"
META_FILE = ".probe/browser-meta7.txt"


class Replay:
    """Logical steps: collapse adjacent duplicate (name, args) log entries."""

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
            raise RuntimeError(f"capture exhausted for {kind}")
        s = matches[cur]
        self.cursors[kind] = cur + 1
        return s


def num(s):
    return int(s.split(":", 1)[1]) if isinstance(s, str) and s.startswith("number:") else None


def main():
    cfg = Config()
    engine = Engine(cfg)
    store = Store(engine)
    module = Module.from_file(store.engine, WASM)
    rp = Replay(CAPTURE)
    kinds = {}
    for s in rp.steps:
        kinds[s["name"]] = kinds.get(s["name"], 0) + 1
    print("logical steps:", kinds)

    mem = None
    stdout_buf = []
    events = []

    def mview():
        return mem.get_buffer_ptr(store)

    def mem_write(addr, data):
        buf = mview()
        buf[addr:addr + len(data)] = data

    def mem_read(addr, n):
        return bytes(mview()[addr:addr + n])

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
        # role map from the capture's own args: slot -> captured pointer
        ca = [num(x) for x in c["a"]]
        roles = {}  # captured ptr -> (runtime ptr, len)
        for cap_idx, run_ptr, run_len in ((0, sp, 16), (4, dstPtr, dstLen), (7, p2, l2), (10, p3, l3)):
            cap_ptr = ca[cap_idx]
            cap_len = ca[cap_idx + 1]
            if cap_ptr is not None:
                roles[cap_ptr] = (run_ptr, run_len, cap_len)
        for r in (c.get("post") or []):
            cap_ptr, cap_len, b64 = r[0], r[1], r[2]
            if cap_ptr not in roles or not b64:
                continue
            run_ptr, run_len, _ = roles[cap_ptr]
            if run_len <= 0:
                continue
            data = base64.b64decode(b64)
            data = (data + b"\x00" * run_len)[:run_len]
            mem_write(run_ptr, data[:run_len])
        events.append(("cpy", dstLen, l2, l3))
        return

    def valueNew(resultAddr, ctorRef, argvPtr, argc, argcPtr, x6):
        c = rp.next("valueNew")
        for r in (c.get("post") or []):
            if r[1] == 16 and r[2]:
                mem_write(resultAddr, base64.b64decode(r[2])[:16])
                break
        events.append(("new",))
        return

    def valueSetIndex(ref, sp, val, sp2):
        rp.next("valueSetIndex")

    def valueLength(ref, sp):
        c = rp.next("valueLength")
        ret = c.get("ret") or ""
        v = int(ret[1:], 16) if ret.startswith("B") else 0
        v &= 0xFFFFFFFF
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
        print(f"_start completed in {time.time()-t0:.2f}s")
    except Exception as e:
        print(f"_start raised after {time.time()-t0:.2f}s:", str(e)[:300])
    out = b"".join(stdout_buf)
    if out:
        print("stdout:", out[:200])
    print("cursors:", rp.cursors)
    print("events tail:", events[-6:])

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
        open(".probe/wd-payload.txt", "w").write(hits[0])


if __name__ == "__main__":
    main()
