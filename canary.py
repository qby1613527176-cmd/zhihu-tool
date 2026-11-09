#!/usr/bin/env python3
"""
canary.py — 知乎风控姿态金丝雀(defense-posture canary)

每次运行采集一份「防御姿态指纹」并落盘,支持 diff 与分级告警。
设计依据(research/ 实测结论):
  - 挑战 build 哈希高频轮换(实测 ~3 次/小时)          → 变=正常,只记 INFO
  - x-zse-96 签名算法 2022 年冻结于 v3(常量 101_3_3.0) → 变=算法换代=CRITICAL
  - __zse_ck 是 WASM PoW 挑战,位图 w4=0x4000 是过检判据 → 结构变=HIGH
  - 风控错误码体系 10003(签名)/40353(need_login)/40362(风控)/40301(限流)
    → 新码出现=中新策略=HIGH;既有映射漂移=MEDIUM

探测纪律(硬约束):
  * 总请求 ≤ --budget(默认 20),单次单发,**任何探测失败都不重试**
  * 全部匿名:只用 /explore 预热的 d_c0,**绝不读 cookies.json(登录态)**
  * 请求间隔 ≥3s(+0~0.6s 抖动)

用法:
  python3 canary.py run            # 采姿态+落盘+与上次 diff+告警
  python3 canary.py diff [a b]     # 任意两次 run 的 diff(默认最后两次)
  python3 canary.py history        # 列出历史 run
  python3 canary.py rules          # 打印告警规则表
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

TOOL_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOL_DIR))
# 复用快通道的 x-zse-96 v3 纯 Python 端口(不重复造轮子;算法换代时这里会先炸,本身就是信号)
from zhihu_fast import encrypt, sign  # noqa: E402

DATA_DIR = TOOL_DIR / "canary"
DB_FILE = DATA_DIR / "canary.db"
JSONL_FILE = DATA_DIR / "postures.jsonl"

CST = timezone(timedelta(hours=8))

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36")
HEADLESS_UA = UA.replace("Chrome/150.0.0.0", "HeadlessChrome/150.0.0.0")
XZSE93_CURRENT = "101_3_3.0"          # x-zse-96 v3 版本常量(2022 年冻结)
XZSE93_BOGUS = "101_9_9.0"            # 探测用虚假版本号

QID_DEFAULT = "19550225"              # 工具链一直用的活体靶问题

# 已知风控错误码全集(research/02,05,12a,12e 实证)——集合之外出现即「新策略」
KNOWN_CODES = {10003, 40353, 40362, 40301}

META_RE = re.compile(r'<meta id="zh-zse-ck"[^>]*content="([^"]+)"')
BUILD_RE = re.compile(
    r'src="(https://static\.zhihu\.com/zse-ck/(v\d+)/([0-9a-f]+)\.js)"')
JS_SRC_RE = re.compile(r'src="(https://static\.zhihu\.com/[^"]+\.js)"')
VER_RE = re.compile(r'\b(\d{3}_\d+_\d+\.\d+)\b')

# ---------------------------------------------------------------- 告警规则表
# (rule_id, 级别, 含义) —— 与 research/d2_风控姿态金丝雀.md 的规则表一一对应
RULES = {
    "R_BUILD_ROTATE": ("INFO",     "build 哈希轮换(路径不变):正常,实测 ~3 次/小时"),
    "R_BUILD_GEN":    ("CRITICAL", "挑战脚本路径代际变化(v4→v5?):挑战系统换代"),
    "R_CHAL_STRUCT":  ("HIGH",     "挑战页结构变化(meta 缺失/不再发挑战/拿到内容)"),
    "R_SIG_DEAD":     ("CRITICAL", "对照签名请求失败:x-zse-96 算法或常量换代,快通道死"),
    "R_VER_CONST":    ("CRITICAL", "前端 JS 内 x-zse-93 版本常量变化:签名版本换代"),
    "R_CODE_NEW":     ("HIGH",     "出现已知集合之外的新错误码:新风控策略上线"),
    "R_CODE_SHIFT":   ("MEDIUM",   "既定探测→错误码映射漂移:风控策略调整"),
    "R_WALL_TIGHT":   ("MEDIUM",   "匿名登录墙收紧(回答数变少/新接口被墙)"),
    "R_WALL_LOOSE":   ("LOW",      "匿名登录墙放宽"),
    "R_EDGE_HDRS":    ("LOW",      "EdgeOne x-secng-* 响应头集合变化:边缘层策略变动"),
    "R_ALL_40362":    ("HIGH",     "全部 API 探测 40362:本 IP 被风控/全局收紧"),
    "R_PROBE_SKIP":   ("LOW",      "部分探测因预算/前置失败被跳过,姿态不完整"),
}


# ================================================================= 采集
class Canary:
    def __init__(self, budget: int = 20, min_interval: float = 3.0,
                 qid: str = QID_DEFAULT, js_budget: int = 2):
        self.budget = budget
        self.min_interval = min_interval
        self.qid = qid
        self.js_budget = js_budget
        self.used = 0
        self.skipped: list[str] = []
        self._last = 0.0
        self.client = httpx.Client(timeout=30.0, follow_redirects=False)
        self.d_c0: str | None = None

    # ---- 底层:计数+节流+单发(不重试)
    def _req(self, tag: str, url: str, *, headers: dict | None = None,
             note: str = "") -> dict:
        if self.used >= self.budget:
            self.skipped.append(f"{tag}(预算耗尽)")
            return {"tag": tag, "skipped": "budget"}
        gap = self.min_interval + random.uniform(0, 0.6) \
            - (time.monotonic() - self._last)
        if gap > 0:
            time.sleep(gap)
        h = {"User-Agent": UA,
             "Accept": "*/*",
             "Accept-Language": "zh-CN,zh;q=0.9"}
        h.update(headers or {})
        rec: dict = {"tag": tag, "url": url[:120], "note": note}
        try:
            r = self.client.get(url, headers=h)
            self._last = time.monotonic()
            self.used += 1
            rec["status"] = r.status_code
            rec["code"] = self._extract_code(r)
            rec["layer"] = self._detect_layer(r)
            rec["headers"] = self._select_headers(r)
            rec["body_head"] = r.text[:400]
            rec["_body"] = r.text          # 内部用,落盘前剥掉
            rec["_cookies"] = r.cookies
        except Exception as e:             # 网络层失败也是姿态事实
            self.used += 1
            self._last = time.monotonic()
            rec["error"] = f"{type(e).__name__}: {e}"
        return rec

    @staticmethod
    def _extract_code(r: httpx.Response) -> int | None:
        try:
            j = r.json()
            err = j.get("error") or {}
            c = err.get("code") or j.get("code")
            if c:
                return int(c)
        except Exception:
            pass
        m = re.search(r'"code"\s*:\s*(\d+)', r.text[:2000])
        return int(m.group(1)) if m else None

    @staticmethod
    def _detect_layer(r: httpx.Response) -> str:
        head = r.text[:3000]
        if '<meta id="zh-zse-ck"' in head:
            return "zse_ck_challenge"      # __zse_ck 挑战页
        if "安全验证" in head:
            return "captcha_page"
        if "unhuman" in (r.headers.get("location") or ""):
            return "unhuman_mark"
        ct = r.headers.get("content-type", "")
        if "json" in ct:
            return "api_json"
        return "html" if "<html" in head.lower() else "other"

    @staticmethod
    def _select_headers(r: httpx.Response) -> dict:
        """只留安全姿态相关头;set-cookie 只记名不记值(卫生)"""
        out = {}
        for k, v in r.headers.items():
            lk = k.lower()
            if lk.startswith("x-") or lk in ("server", "via", "content-type"):
                out[lk] = v[:200]
            elif lk == "set-cookie":
                names = [c.split("=", 1)[0].strip() for c in v.split(",")]
                out["set-cookie-names"] = sorted(
                    n for n in names if re.fullmatch(r"[\w.-]+", n))
        return out

    # ---- x-zse-96 签名(任意版本号变体)
    @staticmethod
    def _sign_ver(ver: str, path: str, d_c0: str) -> str:
        src = "+".join([ver, path, d_c0])
        return "2.0_" + encrypt(hashlib.md5(src.encode()).hexdigest())

    def _signed_headers(self, path: str, *, ver: str = XZSE93_CURRENT,
                        cookie: str | None = None, ua: str | None = None,
                        garbage_sig: bool = False) -> dict:
        h: dict[str, str] = {}
        if ua:
            h["User-Agent"] = ua
        if garbage_sig:
            h["x-zse-93"] = ver
            h["x-zse-96"] = "2.0_" + "A" * 64
        else:
            h["x-zse-93"] = ver
            h["x-zse-96"] = self._sign_ver(ver, path, self.d_c0 or "")
        if cookie is not None:
            h["cookie"] = cookie
        elif self.d_c0:
            h["cookie"] = f"d_c0={self.d_c0}"
        h["x-requested-with"] = "fetch"
        return h

    # ================================================================ 探测套
    def run(self) -> dict:
        t0 = time.monotonic()
        base = "https://www.zhihu.com"
        R: dict[str, dict] = {}

        # 1) 匿名预热: /explore → d_c0 + 边缘头 + app JS 清单(复用 HTML 刮版本号)
        r = self._req("warmup_explore", f"{base}/explore")
        R["warmup"] = r
        if "_cookies" in r:
            self.d_c0 = r["_cookies"].get("d_c0")

        # 2) 挑战页 ×2(无 cookie): build 哈希 + meta + 会话内轮换速率
        for tag in ("challenge_a", "challenge_b"):
            r = self._req(tag, f"{base}/people/zhihuadmin/activities",
                          headers={"cookie": ""}, note="无cookie触发挑战页")
            R[tag] = r

        api = f"{base}/api/v4"
        qpath = f"/api/v4/questions/{self.qid}"

        # 3) 对照签名(匿名 d_c0): x-zse-96 v3 + 101_3_3.0 是否仍被接受
        if self.d_c0:
            R["api_control"] = self._req(
                "api_control", api + f"/questions/{self.qid}",
                headers=self._signed_headers(qpath),
                note="有效签名+有效版本+d_c0")
        else:
            self.skipped.append("api_control(warmup 未发 d_c0)")

        # 4) 畸形请求矩阵 → 错误码分类
        probes = [
            ("api_no_sig",   {"cookie": f"d_c0={self.d_c0}" if self.d_c0 else ""},
             "完全无签名头"),
            ("api_bad_sig",  self._signed_headers(qpath, garbage_sig=True),
             "伪造格式签名"),
            ("api_no_cookie", self._signed_headers(qpath, cookie=""),
             "有效签名但零 cookie"),
            ("api_headless", self._signed_headers(qpath, ua=HEADLESS_UA),
             "HeadlessChrome UA(指纹露馅)"),
            ("api_wrong_ver", self._signed_headers(qpath, ver=XZSE93_BOGUS),
             f"虚假版本号 {XZSE93_BOGUS}(签名源同步换)"),
        ]
        for tag, hdr, note in probes:
            R[tag] = self._req(tag, api + f"/questions/{self.qid}",
                               headers=hdr, note=note)

        # 5) 登录墙严格度(匿名, 签名+d_c0)
        if self.d_c0:
            fpath = (f"/api/v4/questions/{self.qid}/feeds"
                     "?limit=5&offset=0&order=default&platform=desktop")
            R["wall_feeds"] = self._req(
                "wall_feeds", api + f"/questions/{self.qid}/feeds"
                "?limit=5&offset=0&order=default&platform=desktop",
                headers=self._signed_headers(fpath), note="匿名问题回答流")
            spath = "/api/v4/search_v3?t=general&q=%E6%B5%8B%E8%AF%95&limit=5&offset=0"
            R["wall_search"] = self._req(
                "wall_search", api + "/search_v3?t=general&q=%E6%B5%8B%E8%AF%95"
                "&limit=5&offset=0",
                headers=self._signed_headers(spath), note="匿名搜索(应被墙)")
            hpath = "/api/v4/hot-lists/total?limit=5"
            R["wall_hot"] = self._req(
                "wall_hot", api + "/hot-lists/total?limit=5",
                headers=self._signed_headers(hpath), note="匿名热榜(应被墙)")

        # 6) 前端 JS 版本常量刮取(复用 warmup HTML,限额 js_budget)
        R["js_scan"] = self._scan_js_versions(R["warmup"])

        report = self._assemble(R, t0)
        return report

    def _scan_js_versions(self, warmup: dict) -> dict:
        body = warmup.get("_body") or ""
        if '<meta id="zh-zse-ck"' in body:
            return {"skipped": "warmup 拿到的是挑战页,无 app JS"}
        urls = JS_SRC_RE.findall(body)
        # 优先名字像主包的
        urls.sort(key=lambda u: (0 if re.search(r"(app|main)", u) else 1, u))
        found: dict[str, list[str]] = {}
        xzse_literals: dict[str, list[str]] = {}
        used_urls: list[str] = []
        for u in urls[: self.js_budget]:
            r = self._req("js_bundle", u, note="刮 x-zse-93 版本常量")
            used_urls.append(u.rsplit("/", 1)[-1][:80])
            jb = r.get("_body") or ""
            vers = sorted(set(VER_RE.findall(jb)))
            if vers:
                found[u.rsplit("/", 1)[-1][:80]] = vers
            lits = [lit for lit in ("x-zse-93", "x-zse-96") if lit in jb]
            if lits:
                xzse_literals[u.rsplit("/", 1)[-1][:80]] = lits
        return {"bundles": used_urls, "versions": found,
                "xzse_literals": xzse_literals}

    # ================================================================ 汇总
    def _assemble(self, R: dict, t0: float) -> dict:
        def chal(tag: str) -> dict:
            r = R.get(tag, {})
            body = r.get("_body") or ""
            m = META_RE.search(body)
            bm = BUILD_RE.search(body)
            return {
                "status": r.get("status"), "layer": r.get("layer"),
                "meta_present": bool(m), "meta_len": len(m.group(1)) if m else 0,
                "build_hash": bm.group(3) if bm else None,
                "build_path_ver": bm.group(2) if bm else None,
                "headers": r.get("headers") or {},
            }

        def api(tag: str) -> dict:
            r = R.get(tag, {})
            out = {"status": r.get("status"), "code": r.get("code"),
                   "layer": r.get("layer"), "note": r.get("note"),
                   "headers": r.get("headers") or {}}
            if r.get("error"):
                out["error"] = r["error"]
            return out

        # 登录墙:数匿名给几个回答
        wall = {}
        wf = R.get("wall_feeds", {})
        n_answers = None
        feeds_totals = None
        try:
            j = json.loads(wf.get("_body") or "{}")
            data = j.get("data") or []
            n_answers = sum(
                1 for x in data
                if (x.get("type") == "answer")
                or (isinstance(x.get("target"), dict)
                    and x["target"].get("type") == "answer"))
            if n_answers == 0 and data:
                n_answers = len(data)     # 结构变时兜底:至少记录条数
            feeds_totals = (j.get("paging") or {}).get("totals")
        except Exception:
            pass
        wall["feeds"] = {**api("wall_feeds"),
                         "answers_anon": n_answers, "totals": feeds_totals}
        wall["search"] = api("wall_search")
        wall["hot"] = api("wall_hot")

        # 边缘头汇总(所有响应里 x-secng-* 的并集)
        secng: dict[str, set[str]] = {}
        for r in R.values():
            for k, v in (r.get("headers") or {}).items():
                if k.startswith("x-secng"):
                    secng.setdefault(k, set()).add(str(v)[:80])
        edge = {"secng": {k: sorted(v) for k, v in sorted(secng.items())},
                "explore_cookie_names":
                    (R.get("warmup", {}).get("headers") or {})
                    .get("set-cookie-names")}

        # 剥离 _body/_cookies 等内部字段
        responses = {}
        for tag, r in R.items():
            rr = {k: v for k, v in r.items()
                  if not k.startswith("_") and k != "body_head"}
            responses[tag] = rr

        return {
            "ts": datetime.now(CST).isoformat(timespec="seconds"),
            "tool": "canary.py v1",
            "budget": self.budget, "requests_used": self.used,
            "skipped": self.skipped,
            "elapsed_s": round(time.monotonic() - t0, 1),
            "challenge": {"a": chal("challenge_a"), "b": chal("challenge_b")},
            "sign": {"control": api("api_control"),
                     "xzse93_sent": XZSE93_CURRENT},
            "probes": {t: api(t) for t in
                       ("api_no_sig", "api_bad_sig", "api_no_cookie",
                        "api_headless", "api_wrong_ver")},
            "login_wall": wall,
            "js_scan": {k: v for k, v in R.get("js_scan", {}).items()},
            "edge": edge,
            "responses": responses,
        }


# ================================================================= 扁平化+diff
def flatten(rep: dict) -> dict[str, str]:
    """姿态 → 可比较的标量路径表。会话噪声字段(build 哈希绝对值/meta 长度等)
    单独保留但只在规则层按 INFO 处理。"""
    f: dict[str, str] = {}
    ca, cb = rep["challenge"]["a"], rep["challenge"]["b"]
    f["challenge.meta_present"] = str(ca["meta_present"] and cb["meta_present"])
    f["challenge.build_path_ver"] = str(ca["build_path_ver"])
    f["challenge.build_hash"] = str(ca["build_hash"])
    f["challenge.status"] = str(ca["status"])
    f["challenge.layer"] = str(ca["layer"])
    ctl = rep["sign"]["control"]
    f["sign.control_status"] = str(ctl.get("status"))
    f["sign.control_code"] = str(ctl.get("code"))
    f["sign.control_layer"] = str(ctl.get("layer"))
    for t, p in rep["probes"].items():
        f[f"probe.{t}.status"] = str(p.get("status"))
        f[f"probe.{t}.code"] = str(p.get("code"))
        f[f"probe.{t}.layer"] = str(p.get("layer"))
    w = rep["login_wall"]
    f["wall.feeds_answers"] = str(w["feeds"].get("answers_anon"))
    f["wall.feeds_code"] = str(w["feeds"].get("code"))
    f["wall.search_code"] = str(w["search"].get("code"))
    f["wall.search_status"] = str(w["search"].get("status"))
    f["wall.hot_code"] = str(w["hot"].get("code"))
    f["wall.hot_status"] = str(w["hot"].get("status"))
    js = rep.get("js_scan") or {}
    vers: set[str] = set()
    for v in (js.get("versions") or {}).values():
        vers.update(v)
    f["js.version_consts"] = ",".join(sorted(vers))
    lits: set[str] = set()
    for v in (js.get("xzse_literals") or {}).values():
        lits.update(v)
    f["js.xzse_literals"] = ",".join(sorted(lits))
    f["edge.secng_keys"] = ",".join(sorted((rep["edge"]["secng"] or {}).keys()))
    f["edge.explore_cookies"] = ",".join(rep["edge"].get("explore_cookie_names") or [])
    return f


def diff_runs(old: dict, new: dict) -> list[dict]:
    fo, fn = flatten(old), flatten(new)
    keys = sorted(set(fo) | set(fn))
    out = []
    for k in keys:
        a, b = fo.get(k), fn.get(k)
        if a != b:
            out.append({"field": k, "was": a, "now": b})
    return out


# ================================================================= 告警判定
def evaluate(changes: list[dict], new: dict) -> list[dict]:
    alerts: list[dict] = []

    def add(rule: str, msg: str):
        sev, desc = RULES[rule]
        alerts.append({"rule": rule, "severity": sev, "msg": msg})

    ch = {c["field"]: c for c in changes}
    if "challenge.build_hash" in ch and "challenge.build_path_ver" not in ch:
        add("R_BUILD_ROTATE",
            f"build {ch['challenge.build_hash']['was']}→"
            f"{ch['challenge.build_hash']['now']}(路径仍 "
            f"{new['challenge']['a']['build_path_ver']},正常轮换)")
    if "challenge.build_path_ver" in ch:
        add("R_BUILD_GEN",
            f"挑战脚本代际变化 {ch['challenge.build_path_ver']['was']}→"
            f"{ch['challenge.build_path_ver']['now']}")
    if "challenge.meta_present" in ch or "challenge.layer" in ch:
        add("R_CHAL_STRUCT",
            f"挑战页结构: meta_present {ch.get('challenge.meta_present', {}).get('was')}→"
            f"{ch.get('challenge.meta_present', {}).get('now')}, layer "
            f"{ch.get('challenge.layer', {}).get('was')}→"
            f"{ch.get('challenge.layer', {}).get('now')}")
    if "sign.control_status" in ch or "sign.control_code" in ch:
        ctl = new["sign"]["control"]
        if ctl.get("status") != 200:
            add("R_SIG_DEAD",
                f"对照签名不再 200: status={ctl.get('status')} "
                f"code={ctl.get('code')} layer={ctl.get('layer')}")
    if "js.version_consts" in ch:
        was, now = ch["js.version_consts"]["was"], ch["js.version_consts"]["now"]
        if was and now:           # 两次都刮到才判,刮不到不算
            add("R_VER_CONST", f"前端版本常量 {was}→{now}")
    # 错误码:新码 + 映射漂移
    for k, c in ch.items():
        if k.startswith("probe.") and k.endswith(".code"):
            for v in (c["now"],):
                if v and v != "None" and int(v) not in KNOWN_CODES:
                    add("R_CODE_NEW", f"{k} 出现新错误码 {v}")
            if c["now"] != c["was"]:
                add("R_CODE_SHIFT", f"{k}: {c['was']}→{c['now']}")
    # 登录墙
    if "wall.feeds_answers" in ch:
        try:
            was, now = int(ch["wall.feeds_answers"]["was"]), \
                int(ch["wall.feeds_answers"]["now"])
            if now < was:
                add("R_WALL_TIGHT", f"匿名问题回答数 {was}→{now}")
            elif now > was:
                add("R_WALL_LOOSE", f"匿名问题回答数 {was}→{now}")
        except (TypeError, ValueError):
            add("R_WALL_TIGHT", "匿名回答流结构异常(拿不到回答数)")
    for k in ("wall.search_code", "wall.hot_code"):
        if k in ch:
            was, now = ch[k]["was"], ch[k]["now"]
            if now != "40353" and was == "40353":
                add("R_WALL_TIGHT", f"{k} 不再是 need_login: {was}→{now}")
            elif now == "40353" and was != "40353":
                add("R_WALL_LOOSE", f"{k} 变为 need_login: {was}→{now}")
    if "edge.secng_keys" in ch:
        add("R_EDGE_HDRS",
            f"x-secng-* 头集合 {ch['edge.secng_keys']['was']}→"
            f"{ch['edge.secng_keys']['now']}")
    # 全局 40362(看本次,不看 diff)
    codes = [p.get("code") for p in new["probes"].values()]
    ctl_code = new["sign"]["control"].get("code")
    if codes and all(c == 40362 for c in codes) and ctl_code == 40362:
        add("R_ALL_40362", "对照+全部畸形探测均 40362:IP 级风控或全局收紧")
    if new.get("skipped"):
        add("R_PROBE_SKIP", "; ".join(new["skipped"]))
    return alerts


SEV_ORDER = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3, "INFO": 4}


# ================================================================= 存储
def init_db():
    DATA_DIR.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_FILE)
    conn.execute("""CREATE TABLE IF NOT EXISTS postures(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT NOT NULL, report_json TEXT NOT NULL)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS alerts(
        id INTEGER PRIMARY KEY AUTOINCREMENT, run_id INTEGER,
        ts TEXT, rule TEXT, severity TEXT, msg TEXT)""")
    conn.commit()
    return conn


def save_run(rep: dict, alerts: list[dict]) -> int:
    conn = init_db()
    js = json.dumps(rep, ensure_ascii=False)
    cur = conn.execute("INSERT INTO postures(ts, report_json) VALUES(?,?)",
                       (rep["ts"], js))
    run_id = cur.lastrowid
    for a in alerts:
        conn.execute(
            "INSERT INTO alerts(run_id, ts, rule, severity, msg) VALUES(?,?,?,?,?)",
            (run_id, rep["ts"], a["rule"], a["severity"], a["msg"]))
    conn.commit()
    conn.close()
    with JSONL_FILE.open("a", encoding="utf-8") as f:   # 流水只 append
        f.write(js + "\n")
    return run_id


def load_runs() -> list[dict]:
    if not JSONL_FILE.exists():
        return []
    out = []
    for line in JSONL_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return out


# ================================================================= 展示
def brief(rep: dict) -> str:
    """一行姿态摘要"""
    ca = rep["challenge"]["a"]
    ctl = rep["sign"]["control"]
    codes = {t: p.get("code") for t, p in rep["probes"].items()}
    fa = rep["login_wall"]["feeds"].get("answers_anon")
    js = rep.get("js_scan") or {}
    vers: set[str] = set()
    for v in (js.get("versions") or {}).values():
        vers.update(v)
    return (f"build={str(ca.get('build_hash'))[:12]}({ca.get('build_path_ver')}) "
            f"meta={'Y' if ca.get('meta_present') else 'N'} | "
            f"control={ctl.get('status')}/{ctl.get('code')} | "
            f"畸形码={codes} | 匿名回答={fa} "
            f"search={rep['login_wall']['search'].get('code')} "
            f"hot={rep['login_wall']['hot'].get('code')} | "
            f"JS版本={sorted(vers) or '未刮到'} | "
            f"secng={len(rep['edge']['secng'])}种 | "
            f"req={rep['requests_used']}")


def cmd_run(args):
    c = Canary(budget=args.budget, qid=args.qid)
    rep = c.run()
    runs = load_runs()
    if runs:
        changes = diff_runs(runs[-1], rep)
        alerts = evaluate(changes, rep)
    else:
        changes, alerts = [], []
    rid = save_run(rep, alerts)
    print(f"== run #{rid} @ {rep['ts']} ==")
    print(brief(rep))
    if not runs:
        print("★ 首次运行:已落盘为基线姿态档案(无 diff)")
    elif not changes:
        print("✓ 与上次运行相比:姿态零变化")
    else:
        print(f"\n-- diff vs 上次({len(changes)} 项) --")
        for c_ in changes:
            print(f"  {c_['field']}: {c_['was']} → {c_['now']}")
    if alerts:
        print("\n-- 告警 --")
        for a in sorted(alerts, key=lambda x: SEV_ORDER[x["severity"]]):
            print(f"  [{a['severity']:8s}] {a['rule']}: {a['msg']}")
    else:
        print("✓ 无告警")
    print(f"\n落盘: {JSONL_FILE} + {DB_FILE} (run #{rid})")


def cmd_diff(args):
    runs = load_runs()
    if len(runs) < 2:
        print("历史不足 2 次,无法 diff"); return 1
    a = runs[args.a] if args.a is not None else runs[-2]
    b = runs[args.b] if args.b is not None else runs[-1]
    changes = diff_runs(a, b)
    print(f"diff: {a['ts']} → {b['ts']}  ({len(changes)} 项)")
    for c_ in changes:
        print(f"  {c_['field']}: {c_['was']} → {c_['now']}")
    alerts = evaluate(changes, b)
    for al in sorted(alerts, key=lambda x: SEV_ORDER[x["severity"]]):
        print(f"  [{al['severity']:8s}] {al['rule']}: {al['msg']}")
    return 0


def cmd_history(_args):
    conn = init_db()
    rows = conn.execute(
        "SELECT id, ts FROM postures ORDER BY id").fetchall()
    for rid, ts in rows:
        n = conn.execute(
            "SELECT COUNT(*) FROM alerts WHERE run_id=?", (rid,)).fetchone()[0]
        print(f"  #{rid:3d}  {ts}  alerts={n}")
    conn.close()


def cmd_rules(_args):
    print("告警规则表(与 research/d2_风控姿态金丝雀.md 对应):")
    for rid, (sev, desc) in RULES.items():
        print(f"  [{sev:8s}] {rid:15s} {desc}")


def main():
    ap = argparse.ArgumentParser(description="知乎风控姿态金丝雀")
    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("run", help="采集姿态+落盘+diff+告警")
    p.add_argument("--budget", type=int, default=20, help="请求预算上限")
    p.add_argument("--qid", default=QID_DEFAULT, help="活体靶问题 id")
    d = sub.add_parser("diff", help="两次 run 的 diff(默认最后两次)")
    d.add_argument("--a", type=int, default=None, help="旧 run 序号(0基)")
    d.add_argument("--b", type=int, default=None, help="新 run 序号(0基)")
    sub.add_parser("history", help="历史 run 列表")
    sub.add_parser("rules", help="告警规则表")
    args = ap.parse_args()
    if args.cmd in (None, "run"):
        cmd_run(args if args.cmd else
                argparse.Namespace(budget=20, qid=QID_DEFAULT))
    elif args.cmd == "diff":
        return cmd_diff(args)
    elif args.cmd == "history":
        cmd_history(args)
    elif args.cmd == "rules":
        cmd_rules(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
