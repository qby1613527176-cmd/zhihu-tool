"""__zse_ck (v4 / "005_") generator -- pure Python, no browser / jsdom / Node in the loop.

Pipeline (verified against the two-endpoint oracle):
  1. GET the challenge page            -> meta (172 chars, per-request) + build hash
  2. token = "005_" + payload(128) + "-" + meta        (meta is echoed verbatim by the
     glue: `t = __g.ck + '-' + document.getElementById("zh-zse-ck").content`; measured:
     swapping the meta byte-for-byte leaves the payload identical -> no crypto role)
  3. send requests with the cookie; server answers 200 + real content (~70KB) and the
     member API 200.

payload sources, best first:
  * REPLAY  -- py/wdriver.py drives the per-build wasm under wasmtime replaying the
    captured host side of the protocol (see docs/METHOD.md); payload is computed fresh.
  * STORE   -- py/payload_store.json holds a payload minted by the browser capture
    recipe (docs/BITMAP.md).  Measured: a 10-minute-old token still passed the
    oracle, and a token minted under UA-A passed with UA-B in the request, so the
    server does not tightly bind time or User-Agent.

Usage:
  python py/zse_gen.py                 # generate + verify on both endpoints
  python py/zse_gen.py --n 5           # repeat verification 5 times
"""
import json
import re
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, "py")

BASE = "https://www.zhihu.com"
PAGE = "/people/zhihuadmin/activities"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

STORE = "py/payload_store.json"


def http(url, ua=UA, cookie=None, timeout=25):
    req = urllib.request.Request(url, headers={
        "user-agent": ua, "referer": BASE + "/",
        "accept": "text/html,application/xhtml+xml,application/json"})
    if cookie:
        req.add_header("cookie", cookie)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def fetch_challenge(ua=UA):
    """-> (meta, build_hash) from a fresh challenge response (no cookie)."""
    _, body = http(BASE + PAGE, ua=ua)
    meta = re.search(r'id="zh-zse-ck"[^>]*content="([^"]*)"', body)
    hash_ = re.search(r"zse-ck/v4/([a-f0-9]+)\.js", body)
    return (meta.group(1) if meta else None, hash_.group(1) if hash_ else None, body)


def load_payload(store=STORE):
    return json.load(open(store, encoding="utf-8"))


def gen_token(meta, payload):
    assert len(payload) == 128, len(payload)
    return "005_" + payload + "-" + meta


def check_token(token, ua=UA):
    out = {}
    for name, url in (("page", BASE + PAGE), ("api", BASE + "/api/v4/members/zhihuadmin")):
        status, body = http(url, ua=ua, cookie="__zse_ck=" + token)
        out[name] = (status, len(body),
                     "challenge" if 'id="zh-zse-ck"' in body
                     else "human-verify" if ("安全验证" in body or "unhuman" in body) else "real")
    return out


def main():
    n = 1
    if "--n" in sys.argv:
        n = int(sys.argv[sys.argv.index("--n") + 1])
    st = load_payload()
    payload = st["payload"]
    print(f"payload from store (build {st.get('build_hash', '?')[:16]}…, minted under "
          f"UA[{(st.get('ua') or UA)[:40]}…])")
    ok = 0
    for i in range(n):
        meta, hash_, _ = fetch_challenge()
        if not meta:
            print(f"[{i+1}] no challenge served (cookie jar already valid?)")
            break
        tok = gen_token(meta, payload)
        res = check_token(tok)
        good = res["page"][0] == 200 and res["page"][2] == "real" and res["api"][0] == 200
        ok += good
        print(f"[{i+1}] build={hash_[:12]}… token={tok[:28]}… -> "
              f"page={res['page']} api={res['api']} {'PASS' if good else 'FAIL'}")
        time.sleep(1)
    print(f"{ok}/{n} passed")


if __name__ == "__main__":
    main()
