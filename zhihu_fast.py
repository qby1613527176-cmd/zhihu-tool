"""
zhihu_fast.py — 纯 HTTP 通道(无浏览器): x-zse-96 签名纯 Python 端口 + httpx 客户端
路线: RSSHub x-zse-96-v3.ts 血统(SM4结构变体,自定义S盒/轮密钥/Base64) 逐行移植
目标: 手机 Linux 容器(Termux/proot)可跑——无 Chrome 依赖
用法: cookies 从浏览器档案导出(cookies.json),签名本地计算,httpx 直打 API
"""
from __future__ import annotations

import hashlib
import json
import os
import random
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx

TOOL_DIR = Path(__file__).resolve().parent
COOKIES_FILE = TOOL_DIR / "cookies.json"

from zhihu_sign import sign, encrypt  # AGPL-3.0 子模块(源自 RSSHub,见 zhihu_sign.py 文件头)

# ===== 纯 HTTP 客户端 =====
class FastClient:
    """无浏览器知乎 API 客户端(复用浏览器导出的 cookie;UA 必须与导出时一致)"""

    def __init__(self, cookies_file: Path = COOKIES_FILE,
                 min_interval: float = 4.0, ua: str | None = None):
        self.cookies: dict[str, str] = json.loads(cookies_file.read_text())
        # __zse_ck 与 UA 绑定(R3 实锤)——默认用浏览器会话的 UA
        # Chrome 大版本可用环境变量 ZHIHU_CHROME_VERSION 覆盖(防指纹漂移)
        _ver = os.environ.get("ZHIHU_CHROME_VERSION", "150")
        self.ua = ua or ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                         "AppleWebKit/537.36 (KHTML, like Gecko) "
                         f"Chrome/{_ver}.0.0.0 Safari/537.36")
        self.min_interval = min_interval
        self._last = 0.0
        self._client = httpx.Client(
            base_url="https://www.zhihu.com",
            headers={"User-Agent": self.ua, "x-requested-with": "fetch"},
            timeout=30.0,
        )
        self.daily_budget = 3000        # R8: 单日预算下限档
        self._budget_file = TOOL_DIR / ".fast_budget.json"
        self.daily_count = self._load_budget()

    # ---- 日预算持久化(跨进程共享,防「实例内存计数重启归零」) ----
    def _load_budget(self) -> int:
        try:
            d = json.loads(self._budget_file.read_text())
            if d.get("date") == time.strftime("%Y-%m-%d"):
                return int(d.get("count", 0))
        except Exception:
            pass
        return 0

    def _save_budget(self):
        try:
            self._budget_file.write_text(json.dumps(
                {"date": time.strftime("%Y-%m-%d"),
                 "count": self.daily_count}, ensure_ascii=False))
        except Exception:
            pass

    def _throttle(self):
        import random as _r
        # R8: 高斯随机间隔(拒绝恒定),均值≈min_interval+1
        wait = max(1.0, _r.gauss(self.min_interval + 1.0, 1.2)) \
            - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)

    def get(self, path_with_query: str) -> dict:
        """签名 GET,带四级信号熔断: 40362/429 → 指数退避;验证码/跳登录 → 硬停"""
        backoffs = [0, 60, 300, 1800]     # 第1次立即,之后 1m/5m/30m
        last_err: Exception | None = None
        for attempt, backoff in enumerate(backoffs):
            self.daily_count = max(self.daily_count, self._load_budget())  # 跨进程重载
            if self.daily_count >= self.daily_budget:
                raise RuntimeError("日请求预算耗尽(熔断保护)")
            if backoff:
                time.sleep(backoff)
            self._throttle()
            sig = sign(path_with_query, self.cookies["d_c0"])
            resp = self._client.get(
                path_with_query,
                headers={
                    "x-zse-93": "101_3_3.0",
                    "x-zse-96": sig,
                    "cookie": "; ".join(f"{k}={v}" for k, v in self.cookies.items()),
                },
            )
            self._last = time.monotonic()
            self.daily_count += 1
            self._save_budget()             # 持久化(跨进程可见)
            if resp.status_code == 200:
                return resp.json()
            code = ""
            try:
                code = resp.json().get("error", {}).get("code")
            except Exception:
                pass
            if resp.status_code in (403, 429) and code in (40362, 40301, 429):
                last_err = ZhihuFastBlocked(
                    f"{resp.status_code} code={code} path={path_with_query[:60]} "
                    f"(第{attempt+1}次,退避后重试)")
                # 40362 最后一击前尝试纯算重铸 __zse_ck(可能是轮换导致)
                if code == 40362 and attempt == len(backoffs) - 2:
                    try:
                        self.refresh_zse_ck()
                        last_err = None  # 重铸成功,再多给一次机会
                        continue
                    except Exception:
                        pass
                continue                     # 限流信号 → 指数退避
            if resp.status_code in (301, 302) or "captcha" in resp.text[:500]:
                raise ZhihuFastBlocked(      # 验证码/跳登录 → 硬停,禁自动重试
                    f"验证码/登录墙硬信号,当日停机: {path_with_query[:60]}")
            if code == 10003:
                raise ZhihuFastBlocked(
                    f"签名失效(code=10003): x-zse-96 算法可能已轮换,需检查签名段 "
                    f"(zhihu_sign.py)——当日停机: {path_with_query[:60]}")
            resp.raise_for_status()
        raise last_err or ZhihuFastBlocked("重试耗尽")

    @staticmethod
    def bootstrap(cookies_file: Path = COOKIES_FILE, verbose: bool = True) -> "FastClient":
        """匿名自举: 零浏览器从零铸 cookie 罐(d_c0 预热 + 纯算 __zse_ck)。
        手机容器免 PC 的入口。匿名层级=问题元数据/少量回答/用户信息;
        深翻页/搜索仍需登录态(PC 扫码导出 cookies.json)。"""
        _ver = os.environ.get("ZHIHU_CHROME_VERSION", "150")
        ua = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              f"(KHTML, like Gecko) Chrome/{_ver}.0.0.0 Safari/537.36")
        if verbose:
            print("[bootstrap] 预热 d_c0…", file=sys.stderr)
        dc0 = _warmup_dc0(ua)
        if verbose:
            print("[bootstrap] 纯算铸 __zse_ck…", file=sys.stderr)
        ck = _zseck_mint(verbose=verbose)
        cookies = {"d_c0": dc0, "__zse_ck": ck}
        cookies_file.write_text(json.dumps(cookies, ensure_ascii=False, indent=1))
        if verbose:
            print(f"[bootstrap] ✓ 已写入 {cookies_file}", file=sys.stderr)
        return FastClient(cookies_file=cookies_file, ua=ua)

    def refresh_zse_ck(self) -> None:
        """__zse_ck 轮换/失效时重铸(保持 d_c0/z_c0 不动)"""
        ck = _zseck_mint()
        self.cookies["__zse_ck"] = ck
        try:
            data = json.loads(COOKIES_FILE.read_text())
            data["__zse_ck"] = ck
            COOKIES_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=1))
        except Exception:
            pass

    # 便捷方法
    def question(self, qid: int | str) -> dict:
        return self.get(f"/api/v4/questions/{qid}")

    def question_feeds(self, qid: int | str, limit: int = 5,
                       offset: int = 0, order: str = "default") -> dict:
        return self.get(f"/api/v4/questions/{qid}/feeds?limit={limit}"
                        f"&offset={offset}&order={order}&platform=desktop")

    def answer(self, aid: int | str) -> dict:
        return self.get(f"/api/v4/answers/{aid}")

    def user(self, token: str) -> dict:
        # 实测: /api/v4/people/{token} 404;成员数据在 members 路径
        return self.get(f"/api/v4/members/{token}")

    def user_answers(self, token: str, limit: int = 5, offset: int = 0) -> dict:
        return self.get(f"/api/v4/members/{token}/answers?limit={limit}&offset={offset}")

    def search(self, query: str, limit: int = 10, offset: int = 0) -> dict:
        from urllib.parse import quote
        return self.get(f"/api/v4/search_v3?t=general&q={quote(query)}"
                        f"&limit={limit}&offset={offset}")

    def search_all(self, query: str, max_items: int = 100) -> dict:
        """搜索全量翻页(实测: offset 步进有效,页界少量重叠→按 id 去重)
        返回 {results: [...], fetched: int, pages: int, truncated: bool}"""
        seen: dict[str, dict] = {}
        pages = 0
        truncated = True
        while len(seen) < max_items:
            d = self.search(query, limit=20, offset=pages * 20)
            data = d.get("data", [])
            pg = d.get("paging") or {}
            if not data:
                truncated = False
                break
            before = len(seen)
            for it in data:
                if not isinstance(it, dict):
                    continue
                obj = it.get("object", {})
                if not isinstance(obj, dict):
                    continue
                oid = str(obj.get("id", ""))
                if oid and oid != "None":
                    seen[oid] = obj
            pages += 1
            if pg.get("is_end") or len(seen) == before:
                truncated = not pg.get("is_end", False)
                break
        out = list(seen.values())[:max_items]
        return {"type": "search_all", "query": query, "fetched": len(out),
                "pages": pages, "truncated": truncated, "results": out}

    def close(self):
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


class ZhihuFastBlocked(Exception):
    """纯HTTP通道被风控(40362/40301等)——应退避或回落浏览器通道"""


VENDOR_DIR = TOOL_DIR / "vendor" / "py"


def _zseck_mint(verbose: bool = False) -> str:
    """纯算铸 __zse_ck(vendor/zhihulite 血统): 抓挑战页→抽wasm→零执行推常量→真机位图铸ck
    返回完整 cookie 值 (ck-meta)。供匿名自举/轮换续期。"""
    import base64
    import importlib.util
    import random as _r

    def _load(name):
        spec = importlib.util.spec_from_file_location(
            name, str(VENDOR_DIR / (name + ".py")))
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        return m

    fresh_ck = _load("fresh_ck")
    ds = _load("derive_session")
    record93 = _load("record93")
    pck = _load("pure_ck")
    fpg = _load("fingerprint")

    cache_dir = VENDOR_DIR / ".cache"
    cache_dir.mkdir(exist_ok=True)
    url = fresh_ck.DEFAULT_URL
    st, body, _ = fresh_ck.fetch(url)
    m = fresh_ck.META_RE.search(body or "")
    bm = fresh_ck.BUILD_RE.search(body or "")
    if not (m and bm):
        raise ZhihuFastBlocked("挑战页结构不符(无meta/build)")
    meta = m.group(1)
    js_url = bm.group(1)
    build = js_url.rsplit("/", 1)[-1][:-3]
    if verbose:
        print(f"[mint] build={build[:12]}…", file=sys.stderr)
    st2, js, _ = fresh_ck.fetch(js_url)
    i = js.find("AGFzbQ")
    j = i
    while j < len(js) and (js[j].isalnum() or js[j] in "+/="):
        j += 1
    wasm = base64.b64decode(js[i:j] + "=" * ((4 - (j - i) % 4) % 4))
    wp = cache_dir / f"blind_{build[:8]}.wasm"
    wp.write_bytes(wasm)
    toks = ds.collect_tokens()
    dr = ds.derive(str(wp), toks, verbose=False)
    if not dr:
        raise ZhihuFastBlocked("常量推导失败(build 结构变了?)")
    cands = dr["iv_candidates"] or [dr["iv"]]
    fp = fpg.fingerprint_from_preimage(fpg.load_default_preimage())
    ua = fresh_ck.UA
    for iv in cands[:80]:
        prof = {"rk": dr["rk"], "alphabet": dr["alphabet"],
                "xreg": dr["xreg"], "mask": dr["mask"], "iv": iv}
        rec = record93.build_record93(
            record93.BITMAP_REAL, fp, ua,
            w0="%04x" % _r.randrange(0x10000),
            tail4="%04x" % _r.randrange(0x10000))
        ck = pck.pure_ck(prof, rec)
        cookie = f"{ck}-{meta}"
        st3, body3, hdr3 = fresh_ck.fetch(
            url, cookie=f"__zse_ck={cookie}", follow=False)
        loc = hdr3.get("Location", "")
        cls = fresh_ck.classify(st3, body3, loc)
        if "通过" in cls and "unhuman" not in cls:
            if verbose:
                print(f"[mint] ✓ iv={iv} 完全通过", file=sys.stderr)
            return cookie
        if "unhuman" in cls:
            raise ZhihuFastBlocked("铸出的 cookie 被反自动化标记(unhuman)")
        if "40362" in cls:
            time.sleep(20)
            continue
        time.sleep(1.0)
    raise ZhihuFastBlocked("所有 iv 候选均未通过挑战提交层")


def _warmup_dc0(ua: str) -> str:
    """匿名预热拿 d_c0。2026-11 风控升级: GET /explore 已 302 不种 cookie,
    降级 POST /udid(实测稳定种 d_c0)。都失败才抛错。"""
    r = httpx.get("https://www.zhihu.com/explore",
                  headers={"User-Agent": ua}, follow_redirects=False, timeout=30)
    dc0 = r.cookies.get("d_c0")
    if dc0:
        return dc0
    try:
        r2 = httpx.post("https://www.zhihu.com/udid",
                        headers={"User-Agent": ua,
                                 "Referer": "https://www.zhihu.com/explore"},
                        timeout=30)
        dc0 = r2.cookies.get("d_c0")
    except Exception:
        dc0 = None
    if not dc0:
        raise ZhihuFastBlocked("warmup 未拿到 d_c0")
    return dc0
