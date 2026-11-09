#!/usr/bin/env python3
"""Devirtualize the 950-word CVM program into JS.

Input : .probe/map/full_disasm_new.txt  (listing), .probe/map/blob_constants_dec.json (D pool)
Output: js/cvm.decompiled.js  -- basic-block CFG, real JS control flow, constants inlined.

The 16-op ISA (as rendered by the disassembler):
  A[i] = #k / A[i] = l[j] / l[i] = A[j] / PUSH A[i]
  A[i] = dec(D[k]) / PUSH dec(D[k])            ; string constant
  MEMBER.get|set|eval  A[i]/A[j]               ; pop obj,key -> push member  (set: pop val,obj,key)
  A[i] = A[j] <op> A[k]                        ; arithmetic/logic/typeof
  A[i] = A[j][m]() argc=N                      ; method call, N args popped
  A[i] = A[j]new argc=N                        ; constructor call
  F = A[i] ; JMP[c] N                          ; c=0 -> unconditional, 1 -> if(F), 2 -> if(!F)
  CALL i=A[n] argc=k +frame / RET / RESET
"""
import json
import re
import sys

LISTING = sys.argv[1] if len(sys.argv) > 1 else ".probe/map/full_disasm_new.txt"
CONSTS = sys.argv[2] if len(sys.argv) > 2 else ".probe/map/blob_constants_dec.json"
OUT = sys.argv[3] if len(sys.argv) > 3 else "js/cvm.decompiled.js"

raw = json.load(open(CONSTS, encoding="utf-8"))
if isinstance(raw, dict):
    for k in ("constants", "dec", "values", "D"):
        if k in raw:
            raw = raw[k]
            break
D = raw if isinstance(raw, list) else list(raw.values())


HOIST = {}      # long string -> const name, emitted once at the top


def js_lit(s):
    if isinstance(s, str):
        # D-pool entries carry a "<type>|" tag ("标签 '0|' 需剥离")
        s = re.sub(r"^\d+\|", "", s)
        if len(s) > 200:
            if s not in HOIST:
                HOIST[s] = "SRC%d" % len(HOIST)
            return HOIST[s]
        return json.dumps(s, ensure_ascii=False)
    return json.dumps(str(s), ensure_ascii=False)


prog = {}
for line in open(LISTING, encoding="utf-8", errors="replace"):
    m = re.match(r"\s*(\d+)\s+(.*)$", line)
    if not m:
        continue
    prog[int(m.group(1))] = m.group(2).strip()
N = len(prog)

OPS = {}


def parse(addr, s):
    """-> (list_of_js_stmts, kind, target)"""
    if s == "RESET":
        return ["S.length = 0; F = undefined;"], "seq", None
    if s == "RET":
        return ["return { A: A, l: l };"], "ret", None
    m = re.fullmatch(r"A\[(\d+)\] = #(-?\d+)", s)
    if m:
        return [f"A[{m.group(1)}] = {m.group(2)};"], "seq", None
    m = re.fullmatch(r"A\[(\d+)\] = l\[(\d+)\]", s)
    if m:
        return [f"A[{m.group(1)}] = l[{m.group(2)}];"], "seq", None
    m = re.fullmatch(r"l\[(\d+)\] = A\[(\d+)\]", s)
    if m:
        return [f"l[{m.group(1)}] = A[{m.group(2)}];"], "seq", None
    m = re.fullmatch(r"PUSH A\[(\d+)\]", s)
    if m:
        return [f"S.push(A[{m.group(1)}]);"], "seq", None
    m = re.fullmatch(r"A\[(\d+)\] = dec\(D\[(\d+)\]\)", s)
    if m:
        k = int(m.group(2))
        lit = js_lit(D[k]) if k < len(D) and isinstance(D[k], str) else f"DEC[{k}]"
        return [f"A[{m.group(1)}] = {lit};"], "seq", None
    m = re.fullmatch(r"PUSH dec\(D\[(\d+)\]\)", s)
    if m:
        k = int(m.group(1))
        lit = js_lit(D[k]) if k < len(D) and isinstance(D[k], str) else f"DEC[{k}]"
        return [f"S.push({lit});"], "seq", None
    m = re.fullmatch(r"MEMBER\.(get|set|eval) A\[(\d+)\]/A\[(\d+)\]", s)
    if m:
        kind, dst, obj = m.group(1), m.group(2), m.group(3)
        # ground truth (folded interpreter .probe/bvm-t.folded.js):
        #   get : F[P] = F[T][R.pop()]                     -> obj from REGISTER, pops 1
        #   set : F[T][k] = v                              -> k = top, v = next, pops 2
        #   eval: F[P] = eval(R.pop())                     -> resolve global BY NAME, pops 1
        if kind == "get":
            return [f"A[{dst}] = M_GET(A[{obj}], S.pop());"], "seq", None
        if kind == "set":
            return ["{ const k = S.pop(), v = S.pop(); M_SET(A[%s], k, v); }" % obj], "seq", None
        return [f"A[{dst}] = M_EVAL(S.pop());"], "seq", None
    m = re.fullmatch(r"A\[(\d+)\] = A\[(\d+)\] (\S+) A\[(\d+)\]", s)
    if m:
        d, a, op, b = m.groups()
        jsop = {"<<": "<<", ">>": ">>>", "|": "|", "^": "^", "&": "&", "+": "+", "-": "-",
                "*": "*", "%": "%", "==": "===", "!==": "!==", ">": ">", "<": "<",
                ">=": ">=", "<=": "<=", "typeof": "typeof_"}.get(op, op)
        if jsop == "typeof_":
            return [f"A[{d}] = typeof A[{b}];"], "seq", None
        if op == "neg":
            return [f"A[{d}] = -A[{b}];"], "seq", None
        return [f"A[{d}] = A[{a}] {jsop} A[{b}];"], "seq", None
    m = re.fullmatch(r"A\[(\d+)\] = A\[(\d+)\]\[\"?([^\"\]]+)\"?\]\(\) argc=(\d+)", s)
    if not m:
        m = re.fullmatch(r"A\[(\d+)\] = A\[(\d+)\]\[(\w+)\]\(\) argc=(\d+)", s)
    if m:
        d, o, _meth, n = m.groups()
        n = int(n)
        # ground truth: F[3] = F[Z][R.pop()](...args)  -- the METHOD NAME is popped
        # from the stack (pushed last), args were collected by unshift(R.pop())
        # which preserves the original push order == S.splice(-N).
        args = "...S.splice(-%d)" % n if n else ""
        return [f"{{ const _m = S.pop(); A[{d}] = A[{o}][_m]({args}); }}"], "seq", None
    m = re.fullmatch(r"A\[(\d+)\] = A\[(\d+)\]\(\) argc=(\d+)", s)
    if m:
        d, o, n = m.groups()
        return [f"A[{d}] = A[{o}]({('...S.splice(-%d)' % int(n)) if int(n) else ''});"], "seq", None
    m = re.fullmatch(r"A\[(\d+)\] = A\[(\d+)\]new argc=(\d+)", s)
    if m:
        d, o, n = m.groups()
        return [f"A[{d}] = new A[{o}]({('...S.splice(-%d)' % int(n)) if int(n) else ''});"], "seq", None
    m = re.fullmatch(r"F = A\[(\d+)\]", s)
    if m:
        return [f"F = !!A[{m.group(1)}];"], "seq", None
    m = re.fullmatch(r"JMP\[(\d)\] (-?\d+)", s)
    if m:
        c, t = int(m.group(1)), int(m.group(2))
        if c == 0:
            return [], "jmp", t
        if c == 1:
            return [], "ifF", t
        return [], "ifnotF", t
    m = re.fullmatch(r"CALL i=A\[(\d+)\] argc=(\d+) \+frame", s)
    if m:
        i, n = m.groups()
        # ground truth: entry pc lives in the REGISTER (A[0] = #877 at the call site),
        # the nested frame returns its result in F[3].
        return [f"A[3] = SUBCALL(A, l, S, {i}, {n});"], "seq", None
    OPS.setdefault(s.split(" ")[0], 0)
    OPS[s.split(" ")[0]] += 1
    return [f"/* UNPARSED: {s} */"], "seq", None


# ---- build CFG: leaders = 0, every jump target, every successor of a branch,
# ---- and every SUBCALL entry (the `A[n] = #pc` immediately before `CALL i=A[n]`) ----
leaders = {0}
for a, s in prog.items():
    m = re.fullmatch(r"JMP\[(\d)\] (-?\d+)", s)
    if m:
        t = int(m.group(2))
        if t >= 0:
            leaders.add(t)
        leaders.add(a + 1)
    elif s == "RET":
        leaders.add(a + 1)
    elif s.startswith("CALL i=A["):
        cm = re.fullmatch(r"CALL i=A\[(\d+)\] argc=\d+ \+frame", s)
        prev = prog.get(a - 1, "")
        lm = re.fullmatch(r"A\[%s\] = #(\d+)" % cm.group(1), prev) if cm else None
        if lm:
            leaders.add(int(lm.group(1)))   # nested frame entry pc
sorted_lead = sorted(x for x in leaders if 0 <= x < N)

blocks = []
for bi, start in enumerate(sorted_lead):
    end = sorted_lead[bi + 1] if bi + 1 < len(sorted_lead) else N
    body, kind, tgt = [], "seq", None
    for a in range(start, end):
        if a not in prog:
            kind = "dead"
            break
        st, k, t = parse(a, prog[a])
        body += st
        if k != "seq":
            kind, tgt = (k, t) if t is not None else (k, None)
            break
        kind, tgt = k, t
    blocks.append((start, end, body, kind, tgt))

out = []
for txt, nm in HOIST.items():
    out.append("const %s = %s;" % (nm, json.dumps(txt, ensure_ascii=False)))
if HOIST:
    out.append("")
out.append("// CVM 950-word program, devirtualized from .probe/map/full_disasm_new.txt")
out.append("// by py/devirt.py  --  basic-block CFG, constants inlined from blob_constants_dec.json")
out.append("// Helper semantics: M_GET(o,k)=o[k]; M_EVAL resolves a member/call site; M_SET(o,k,v).")
out.append("// Registers: A[0..3] value stack slots, l[] locals, S[] operand stack, F flag, D[] const pool.")
out.append("// ground truth from the folded interpreter (.probe/bvm-t.folded.js):")
out.append("//   eval : F[P] = eval(R.pop())          -> globals resolved BY NAME, 1 pop")
out.append("//   get  : F[P] = F[T][R.pop()]          -> object comes from a register")
out.append("//   set  : F[T][k] = v                   -> k = top of stack, then v")
out.append("//   call : F[3] = F[Z][R.pop()](...args) -> method name popped, args unshifted")
out.append("//   frame: child.B = [0,...parent.B], child.B[0..argc-1] = args, result = child.F[3]")
out.append("const DEC_POOL = %s;" % json.dumps(
    [re.sub(r"^\d+\|", "", x) if isinstance(x, str) else x for x in D], ensure_ascii=False))
out.append("function M_GET(o, k) { return (o === undefined || o === null) ? undefined : o[k]; }")
out.append("function M_SET(o, k, v) { if (o !== undefined && o !== null) o[k] = v; return v; }")
out.append("function M_EVAL(name) { return eval(name); }")
out.append("function SUBCALL(A, l, S, i, argc) {")
out.append("  const entry = A[i];")
out.append("  if (typeof entry !== 'number') return undefined;")
out.append("  const args = argc ? S.splice(-argc) : [];")
out.append("  const cl = [0].concat(l);")
out.append("  for (let t = 0; t < args.length; t++) cl[t] = args[t];")
out.append("  const r = RUN(cl, DEC_POOL, undefined, entry);   // nested frame, result slot F[3]")
out.append("  return r && r.A ? r.A[3] : undefined;")
out.append("}")
out.append("")
out.append("function RUN(l, D, A, pc) {")
out.append("  A = A || [undefined, undefined, undefined, undefined];")
out.append("  l = l || [];")
out.append("  pc = pc || 0;")
out.append("  const S = []; let F = false; let _n = 0;")
out.append("  while (true) {")
out.append("    if (typeof TRACE === 'function') TRACE(pc, A, l, S, F, ++_n);")
out.append("    switch (pc) {")
for start, end, body, kind, tgt in blocks:
    out.append(f"    case {start}: {{  // instrs {start}..{end - 1}")
    for b in body:
        out.append("      " + b)
    if kind == "ifF":
        out.append(f"      if (F) {{ pc = {tgt}; continue; }}")
        out.append(f"      pc = {end}; continue; }}")
    elif kind == "ifnotF":
        out.append(f"      if (!F) {{ pc = {tgt}; continue; }}")
        out.append(f"      pc = {end}; continue; }}")
    elif kind == "jmp":
        out.append(f"      pc = {tgt}; continue; }}")
    elif kind == "ret":
        out.append("      }")   # the RET instruction already emitted `return { A, l }`
    else:
        out.append(f"      pc = {end}; continue; }}")
out.append("    default: throw new Error('bad pc ' + pc);")
out.append("  } }")
out.append("}")
out.append("if (typeof module !== 'undefined') { module.exports = { RUN: RUN, M_GET: M_GET, M_SET: M_SET, M_EVAL: M_EVAL, DEC: DEC_POOL }; }")
open(OUT, "w", encoding="utf-8").write("\n".join(out) + "\n")

un = sum(OPS.values())
print(f"blocks={len(blocks)}  instructions={N}  unparsed={un}  ->  {OUT}")
if OPS:
    print("未解析形态:", dict(OPS))
