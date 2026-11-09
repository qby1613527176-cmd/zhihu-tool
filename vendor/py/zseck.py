"""__zse_ck (v4 / "005_") token structure, measured from the current live build.

Every constant here comes from an observation, and the two things that are still
unknown are stated as unknown rather than assumed.

Proven structure
  __zse_ck = "005_" + payload(128 chars) + "-" + meta(172 chars)
  * meta is the page's <meta id="zh-zse-ck" content="..."> echoed VERBATIM
    (glue source: t = __g.ck + '-' + document.getElementById("zh-zse-ck").getAttribute("content"))
    meta is standard base64 -> 128 bytes.
  * payload uses a radix-64 alphabet in which '=' is an ordinary symbol, so
    atob()/binascii reject it. 128 chars -> 96 bytes.
  * 96 bytes are high entropy and CHANGE EVERY RUN even when the meta and the
    whole environment are identical => a per-run nonce/IV is folded in.

Alphabets (found by scanning wasm linear memory of live runs)
  0x4CD8C static, inside the wasm data segment  -> ALPHABET_WASM
  ~0xA63C0 runtime copy                         -> ALPHABET_RT1
  ~0x20E98x near the payload builder (3 copies) -> per-BUILD, changes with the
      served JS/wasm batch: run batch 5f8df3e1 gave
      xIERZwn9FQ4SteCvDicjgzpyU2Yl5o7dV6GbfX3=uWAmJPhK/M1qkB8HTNs0OrLa,
      a fresh batch gave mu=2i7KJwrtkONpAdjSHR148ehgsIQ5xbGU60P/yETMDq9fWocCnFYlLBVZv3azX
      It is NOT derived from the meta (same across metas for one batch).
  0x4C638 standard base64                       -> ALPHABET_STD

  Consequence: which table encodes the payload is still open, and decode_payload()
  with ALPHABET_WASM is therefore a *candidate* decode, not a proven one. Use
  alphabets_from_image() to pull the tables out of a captured memory dump and
  test each.

Still unrecovered (the actual blocking work)
  * the transform that produces the 96 bytes, and its key. It is NOT native
    code in the wasm: exhaustive scan finds no S-box permutation, no SM4 FK/CK,
    no AES/RSA/SHA/MD5 tables, zero i32.rotl/rotr, and the module imports only
    8 host functions. The transform runs inside a bytecode VM
    (main._00oooO, opcode = byte - 10, program base linear 0x11800) fed by
    base64 blobs embedded in the data segment.
  * the 96-byte plaintext layout (which environment fields, in what order).
"""

import base64
import json
import re
import urllib.error
import urllib.request

VERSION = "005_"
PAYLOAD_LEN = 128
META_LEN = 172
BLOCK = 16

ALPHABET_STD = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
ALPHABET_WASM = "rBPa7xz6ReFTKuVHLE=IS0sJ5jwf2YCAGqXdWhQvy/OM3nk9cotp1DmZbUN4lg8i"
ALPHABET_RT1 = "AMVL3T/dGNenw9l6=2OZfDhJr71j0kCyiRXavSxHqIpBPm4u5KUgsz8FWtYocbEQ"
ALPHABET_RT2 = "xIERZwn9FQ4SteCvDicjgzpyU2Yl5o7dV6GbfX3=uWAmJPhK/M1qkB8HTNs0OrLa"
ALPHABET_RT3 = "mu=2i7KJwrtkONpAdjSHR148ehgsIQ5xbGU60P/yETMDq9fWocCnFYlLBVZv3azX"
ALPHABETS = {"wasm": ALPHABET_WASM, "rt1": ALPHABET_RT1, "rt2": ALPHABET_RT2, "rt3": ALPHABET_RT3, "std": ALPHABET_STD}


def alphabets_from_image(data: bytes):
    """Every 64-byte window in a memory dump that is a permutation of the radix-64
    symbols -> [(offset, alphabet)]. Used to recover the per-build table."""
    allowed = set(ALPHABET_STD.encode() + b"=")
    out, i, n = [], 0, len(data)
    while i + 64 <= n:
        if data[i] in allowed:
            w = data[i:i + 64]
            if len(set(w)) == 64 and set(w) <= allowed:
                out.append((i, w.decode("latin1")))
                i += 64
                continue
        i += 1
    return out

# Produced by the wasm when it is handed no environment (empty __g): Node/jsdom
# runs land here, and the server rejects it. Same value every run.
DEFAULT_CK = ("005_1j56NEBZ3RhMLdBHUXtC4hqpNRhgiezH2t9BtfemdajFFRjQA8=8J1vFu/6icPkTFFAl"
              "4GVSpIzdfAFesQVUy1MugCgPNkY1F8IBXHCeXrAL55pOj9vwgBtnRbEhLGjN")


def _tables(alphabet):
    fwd = {c: i for i, c in enumerate(alphabet)}
    return fwd, {i: c for c, i in fwd.items()}


def decode_payload(payload, alphabet=ALPHABET_WASM):
    """radix-64 chars -> bytes, 6-bit groups packed MSB first, no padding rules."""
    fwd, _ = _tables(alphabet)
    if len(payload) % 4:
        raise ValueError("payload length must be a multiple of 4")
    out = bytearray()
    for i in range(0, len(payload), 4):
        n = (fwd[payload[i]] << 18) | (fwd[payload[i + 1]] << 12) | (fwd[payload[i + 2]] << 6) | fwd[payload[i + 3]]
        out += bytes(((n >> 16) & 0xFF, (n >> 8) & 0xFF, n & 0xFF))
    return bytes(out)


def encode_payload(data, alphabet=ALPHABET_WASM):
    _, rev = _tables(alphabet)
    if len(data) % 3:
        raise ValueError("data length must be a multiple of 3")
    out = []
    for i in range(0, len(data), 3):
        n = (data[i] << 16) | (data[i + 1] << 8) | data[i + 2]
        out += [rev[(n >> 18) & 63], rev[(n >> 12) & 63], rev[(n >> 6) & 63], rev[n & 63]]
    return "".join(out)


def parse(token):
    """-> (payload, meta). Works for 005_ and older 00N_ prefixes."""
    body = token.split("=", 1)[1] if token.startswith("__zse_ck=") else token
    i = body.index("-")
    return body[len(VERSION):i] if body.startswith(VERSION) else body[:i], body[i + 1:]


def build(payload, meta):
    return VERSION + payload + "-" + meta


def decode_meta(meta):
    """The echoed challenge: 172 standard-base64 chars -> 128 bytes.

    The meta has NO cryptographic role: swapping it (A*172 vs C*172) with all
    randomness and the clock pinned leaves the payload byte-identical.
    """
    return base64.b64decode(meta)


def decode_inner_program(wasm_bytes, alphabet=ALPHABET_RT1):
    """The wasm embeds a 181,388-char radix-64 blob at file offset 0x15276 which
    decodes to the 136,041-byte obfuscated JS program executed by the wasm-side
    JS interpreter. Verified byte-for-byte against a live capture; ALPHABET_WASM
    (the static table at linear 0x4CD8C) yields garbage here."""
    off, ln = 0x15276, 181388
    return decode_payload(wasm_bytes[off:off + ln].decode("latin1"), alphabet)


BASE = "https://www.zhihu.com"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
PAGE = "/people/zhihuadmin/activities"


def fetch_challenge():
    """-> (meta, js_hash). Only HTML routes serve the challenge page (HTTP 403)."""
    req = urllib.request.Request(BASE + PAGE, headers={
        "user-agent": UA, "referer": BASE + "/", "accept": "text/html,application/xhtml+xml"})
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            h = r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        h = e.read().decode("utf-8", "replace")
    meta = re.search(r'id="zh-zse-ck"[^>]*content="([^"]*)"', h)
    js = re.search(r"zse-ck/v4/([a-f0-9]+)\.js", h)
    return (meta.group(1) if meta else None, js.group(1) if js else None)


def check_token(token):
    """Oracle: accepted -> ~70KB real page + API 200; rejected -> challenge/risk page."""
    out = {}
    for name, url in (("page", BASE + PAGE), ("api", BASE + "/api/v4/members/zhihuadmin")):
        req = urllib.request.Request(url, headers={
            "user-agent": UA, "referer": BASE + "/", "cookie": "__zse_ck=" + token,
            "accept": "text/html,application/xhtml+xml,application/json"})
        try:
            with urllib.request.urlopen(req, timeout=25) as r:
                body = r.read().decode("utf-8", "replace")
            out[name] = (r.status, len(body), "challenge" if 'id="zh-zse-ck"' in body
                         else "human-verify" if ("安全验证" in body or "unhuman" in body) else "real")
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")
            out[name] = (e.code, len(body), "risk-block" if "请求存在异常" in body else "denied")
    return out


def _selftest(path=".probe/browser-token.json"):
    tok = json.load(open(path))
    tok = tok.get("zse") or tok.get("token") or list(tok.values())[0]
    payload, meta = parse(tok)
    data = decode_payload(payload)
    assert len(payload) == PAYLOAD_LEN, len(payload)
    assert len(meta) == META_LEN, len(meta)
    assert len(decode_meta(meta)) == 128
    assert len(data) == 96 and len(data) % BLOCK == 0
    assert encode_payload(data) == payload, "round-trip mismatch: bit order or alignment wrong"
    assert build(payload, meta) == VERSION + payload + "-" + meta
    return {
        "payload_head": payload[:24] + "...", "cipher_bytes": len(data),
        "blocks": len(data) // BLOCK, "meta_bytes": 128, "roundtrip": True,
        "alphabet_candidates": list(ALPHABETS),
        "note": "structure + encodings verified; the 96-byte transform itself is NOT yet recovered",
    }


if __name__ == "__main__":
    print(json.dumps(_selftest(), indent=1))
