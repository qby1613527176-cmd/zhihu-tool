#!/usr/bin/env python3
"""
health.py — 知乎双罐(cookie 登录罐 + 匿名罐)会话健康探针与自愈

风险模型(research/e6_会话健康.md):
  - cookies.json(登录罐) z_c0 数月后过期,过期后登录层能力**静默死亡** → 探测+告警
  - cookies_anon.json(匿名罐) __zse_ck 会轮换/失效 → 自动 bootstrap 重铸(可自愈)
  - z_c0 失效**不可自愈**(重新扫码必须人工)→ 只告警,CRITICAL

探测纪律(与 canary.py 同):
  * 纯 HTTP 单发,任何探针**不重试**(失败本身是信号)
  * 探针请求 ≤5 次,间隔 ≥3s(+0~0.6s 抖动)
  * 签名复用 zhihu_fast.sign(x-zse-96 v3 纯 Python 端口)

用法:
  python3 health.py check            # 分级健康报告(OK/WARN/CRITICAL + 修复建议)
  python3 health.py check --json     # 机器可读输出(供 automation 消费)
  python3 health.py check --no-heal  # 只探不修(纯探测)
  python3 health.py check --warn-days 180 --qid 19550225
退出码: 0=OK 1=WARN 2=CRITICAL
"""
from __future__ import annotations

import argparse
import json
import random
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

TOOL_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOL_DIR))
from zhihu_fast import FastClient, sign  # noqa: E402

LOGIN_JAR = TOOL_DIR / "cookies.json"
ANON_JAR = TOOL_DIR / "cookies_anon.json"

CST = timezone(timedelta(hours=8))
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36")

QID_DEFAULT = "19550225"          # 工具链活体靶问题(canary.py 同)
MIN_INTERVAL = 3.0                # 探针最小间隔(秒)
PROBE_BUDGET = 5                  # 单次 check 探针请求上限

# 分级常量
OK, WARN, CRITICAL = "OK", "WARN", "CRITICAL"
_LEVEL_RANK = {OK: 0, WARN: 1, CRITICAL: 2}

# 风控错误码(canary.py 实证集合): 40353=need_login(z_c0 死实锤)
# 40362=风控(歧义:可能是 IP 风控而非 cookie 死) 40301=限流 10003=签名
CODE_NEED_LOGIN = 40353
CODE_RISK = {40362, 40301}


# ===== 底层:节流 + 单发签名 GET(不重试) =====
class Prober:
    def __init__(self, budget: int = PROBE_BUDGET):
        self.budget = budget
        self.used = 0
        self._last = 0.0
        self.client = httpx.Client(timeout=30.0, follow_redirects=False)

    def _throttle(self):
        gap = MIN_INTERVAL + random.uniform(0, 0.6) - (time.monotonic() - self._last)
        if gap > 0:
            time.sleep(gap)

    def signed_get(self, path: str, cookies: dict[str, str]) -> dict:
        """单发签名 GET。返回 {status, code, layer, body, error}。预算耗尽返回 skipped。"""
        if self.used >= self.budget:
            return {"skipped": "probe_budget_exhausted"}
        self._throttle()
        rec: dict = {"path": path[:80]}
        try:
            r = self.client.get(
                "https://www.zhihu.com" + path,
                headers={
                    "User-Agent": UA,
                    "Accept": "*/*",
                    "Accept-Language": "zh-CN,zh;q=0.9",
                    "x-requested-with": "fetch",
                    "x-zse-93": "101_3_3.0",
                    "x-zse-96": sign(path, cookies.get("d_c0", "")),
                    "cookie": "; ".join(f"{k}={v}" for k, v in cookies.items()),
                },
            )
            self.used += 1
            self._last = time.monotonic()
            rec["status"] = r.status_code
            rec["code"] = _extract_code(r)
            rec["layer"] = _detect_layer(r)
            if rec["layer"] == "api_json":
                try:
                    rec["body"] = r.json()
                except Exception:
                    pass
        except Exception as e:      # 网络层失败:记录,不证明 cookie 死
            self.used += 1
            self._last = time.monotonic()
            rec["error"] = f"{type(e).__name__}: {e}"
        return rec

    def close(self):
        self.client.close()


def _extract_code(r: httpx.Response) -> int | None:
    try:
        j = r.json()
        c = (j.get("error") or {}).get("code") or j.get("code")
        if c:
            return int(c)
    except Exception:
        pass
    m = re.search(r'"code"\s*:\s*(\d+)', r.text[:2000])
    return int(m.group(1)) if m else None


def _detect_layer(r: httpx.Response) -> str:
    head = r.text[:3000]
    if '<meta id="zh-zse-ck"' in head:
        return "zse_ck_challenge"   # __zse_ck 挑战页(匿名罐死信号)
    if "安全验证" in head:
        return "captcha_page"
    if "unhuman" in (r.headers.get("location") or ""):
        return "unhuman_mark"
    if "json" in r.headers.get("content-type", ""):
        return "api_json"
    return "html" if "<html" in head.lower() else "other"


# ===== L0/L1:结构 + 龄期(零请求) =====
_ISSUE_RE = re.compile(r"\|10:(\d{9,11})\|")   # z_c0/captcha_session_v2 内嵌签发时间
_HM_RE = re.compile(r"^(\d{10}),")             # Hm_lvt 前导 unix


def jar_structure(path: Path, required: list[str]) -> dict:
    """L0 结构检查:文件存在/可解析/必需键齐全"""
    rep: dict = {"path": str(path), "exists": path.exists()}
    if not path.exists():
        rep["verdict"] = CRITICAL
        rep["problems"] = ["jar_missing"]
        return rep
    try:
        data = json.loads(path.read_text())
        assert isinstance(data, dict)
    except Exception as e:
        rep["verdict"] = CRITICAL
        rep["problems"] = [f"jar_unparseable: {e}"]
        return rep
    missing = [k for k in required if not data.get(k)]
    rep["keys"] = len(data)
    rep["missing"] = missing
    rep["verdict"] = CRITICAL if missing else OK
    return rep


def jar_age(path: Path, cookies: dict[str, str] | None = None) -> dict:
    """L1 龄期:文件 mtime + cookie 内嵌签发时间(近似过期参考)"""
    now = time.time()
    rep: dict = {}
    mt = path.stat().st_mtime
    rep["file_mtime"] = datetime.fromtimestamp(mt, CST).isoformat(timespec="seconds")
    rep["file_age_days"] = round((now - mt) / 86400, 1)
    embedded: dict[str, str] = {}
    if cookies:
        for name in ("z_c0", "captcha_session_v2"):
            v = cookies.get(name, "")
            m = _ISSUE_RE.search(v)
            if m:
                embedded[name] = datetime.fromtimestamp(int(m.group(1)), CST).isoformat(timespec="seconds")
        hv = cookies.get("Hm_lvt_98beee57fd2ef70ccdd5ca52b9740c49", "")
        m = _HM_RE.match(hv)
        if m:
            embedded["Hm_lvt"] = datetime.fromtimestamp(int(m.group(1)), CST).isoformat(timespec="seconds")
    rep["embedded_issue"] = embedded
    if "z_c0" in embedded:
        issue_ts = int(_ISSUE_RE.search(cookies["z_c0"]).group(1))  # type: ignore[index]
        rep["z_c0_age_days"] = round((now - issue_ts) / 86400, 1)
    return rep


def _load_cookies(path: Path) -> dict[str, str]:
    try:
        d = json.loads(path.read_text())
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


# ===== L2:活性探针 =====
def probe_login(prober: Prober, jar: Path = LOGIN_JAR) -> dict:
    """登录罐活性:GET /api/v4/me。200+id=活;401/40353=死;40362/40301=风控歧义"""
    cookies = _load_cookies(jar)
    rec = prober.signed_get("/api/v4/me", cookies)
    rep: dict = {"probe": "login_z_c0", "target": "/api/v4/me",
                 "status": rec.get("status"), "code": rec.get("code"),
                 "layer": rec.get("layer")}
    if rec.get("skipped") or rec.get("error"):
        rep.update(verdict=WARN, note=rec.get("error") or rec["skipped"],
                   advice="网络层失败,不能证明 z_c0 死;稍后复探")
        return rep
    body = rec.get("body") or {}
    if rec["status"] == 200 and isinstance(body, dict) and (body.get("id") or body.get("url_token")):
        rep.update(verdict=OK, note=f"登录态存活: {body.get('name') or body.get('url_token')}")
    elif rec["status"] == 401 or rec.get("code") == CODE_NEED_LOGIN:
        rep.update(verdict=CRITICAL, note=f"z_c0 已失效(status={rec['status']} code={rec.get('code')})",
                   advice="登录层能力静默死亡中。人工修复: python3 login.py 重新扫码导出 cookies.json")
    elif rec.get("code") in CODE_RISK or rec.get("layer") in ("zse_ck_challenge", "captcha_page"):
        rep.update(verdict=WARN, note=f"风控歧义(status={rec['status']} code={rec.get('code')} layer={rec.get('layer')})",
                   advice="可能是 IP 风控而非 z_c0 死;换网络/稍后复探;若持续 CRITICAL 化")
    else:
        rep.update(verdict=WARN, note=f"非预期响应 status={rec['status']}",
                   advice="人工复核(接口结构可能变化)")
    return rep


def probe_anon(prober: Prober, jar: Path = ANON_JAR, qid: str = QID_DEFAULT) -> dict:
    """匿名罐活性:GET /api/v4/questions/{qid}(匿名层轻量接口)"""
    cookies = _load_cookies(jar)
    path = f"/api/v4/questions/{qid}"
    rec = prober.signed_get(path, cookies)
    rep: dict = {"probe": "anon_zse_ck", "target": path,
                 "status": rec.get("status"), "code": rec.get("code"),
                 "layer": rec.get("layer")}
    if rec.get("skipped") or rec.get("error"):
        rep.update(verdict=WARN, note=rec.get("error") or rec["skipped"],
                   advice="网络层失败,不能证明 __zse_ck 死;稍后复探")
        return rep
    if rec["status"] == 200 and rec.get("layer") == "api_json":
        rep.update(verdict=OK, note="匿名层存活")
    elif (rec.get("code") in CODE_RISK or rec["status"] in (401, 403)
          or rec.get("layer") in ("zse_ck_challenge", "captcha_page")):
        rep.update(verdict=CRITICAL, note=f"__zse_ck 失效(status={rec['status']} code={rec.get('code')} layer={rec.get('layer')})",
                   advice="可自愈: bootstrap 重铸")
    else:
        rep.update(verdict=WARN, note=f"非预期响应 status={rec['status']}",
                   advice="人工复核")
    return rep


# ===== 自愈:匿名罐重 bootstrap(登录罐永不自修) =====
def heal_anon(jar: Path = ANON_JAR) -> dict:
    """匿名罐自愈: FastClient.bootstrap() 纯算重铸 d_c0+__zse_ck。
    注意: 绕开 zhihu_fast.refresh_zse_ck()(它写回硬编码 cookies.json,会写错罐)。"""
    rep: dict = {"action": "bootstrap", "jar": str(jar)}
    t0 = time.time()
    try:
        FastClient.bootstrap(cookies_file=jar, verbose=False)
        rep.update(ok=True, elapsed_s=round(time.time() - t0, 1))
    except Exception as e:
        rep.update(ok=False, error=f"{type(e).__name__}: {e}",
                   elapsed_s=round(time.time() - t0, 1))
    return rep


# ===== 汇总 =====
def _worst(levels: list[str]) -> str:
    return max(levels, key=lambda x: _LEVEL_RANK[x]) if levels else OK


def run_check(*, heal: bool = True, warn_days: float = 180.0,
              qid: str = QID_DEFAULT) -> dict:
    prober = Prober()
    R: dict = {
        "ts": datetime.now(CST).isoformat(timespec="seconds"),
        "jars": {}, "probes": [], "heal": None,
        "warnings": [], "criticals": [],
    }
    try:
        # --- L0/L1 结构+龄期(零请求) ---
        login_struct = jar_structure(LOGIN_JAR, ["z_c0", "d_c0"])
        anon_struct = jar_structure(ANON_JAR, ["d_c0", "__zse_ck"])
        login_cookies = _load_cookies(LOGIN_JAR)
        login_age = jar_age(LOGIN_JAR, login_cookies) if LOGIN_JAR.exists() else {}
        anon_age = jar_age(ANON_JAR) if ANON_JAR.exists() else {}
        R["jars"]["login"] = {**login_struct, "age": login_age}
        R["jars"]["anon"] = {**anon_struct, "age": anon_age}

        struct_levels = [login_struct["verdict"], anon_struct["verdict"]]
        if login_struct["verdict"] == OK:
            zc0_days = login_age.get("z_c0_age_days")
            if zc0_days is not None and zc0_days >= warn_days:
                R["warnings"].append(
                    f"z_c0 签发已 {zc0_days:.0f} 天(阈值 {warn_days:.0f}),"
                    f"临近寿命终点;建议近期人工重扫码刷新 cookies.json")
                struct_levels.append(WARN)

        # --- L2 活性探针(≤3 请求:me + anon + 复探) ---
        if login_struct["verdict"] != CRITICAL:
            lp = probe_login(prober)
            R["probes"].append(lp)
        else:
            lp = {"probe": "login_z_c0", "verdict": CRITICAL,
                  "note": "结构检查未过,跳过活性探针",
                  "advice": "修复罐文件结构(必需键 z_c0/d_c0)"}
            R["probes"].append(lp)

        if anon_struct["verdict"] != CRITICAL:
            ap = probe_anon(prober, qid=qid)
            # 自愈:匿名罐死了 → bootstrap 重铸 → 复探一次
            if ap["verdict"] == CRITICAL and heal:
                hr = heal_anon()
                R["heal"] = hr
                if hr.get("ok"):
                    ap2 = probe_anon(prober, qid=qid)
                    ap2["note"] = "自愈后复探: " + ap2.get("note", "")
                    ap2["healed"] = True
                    if ap2["verdict"] == OK:
                        R["warnings"].append(
                            "匿名罐 __zse_ck 已失效并完成自愈(bootstrap 重铸 + 复探通过)")
                    else:
                        R["criticals"].append("匿名罐自愈后复探仍失败——人工介入")
                    ap = ap2
                else:
                    R["criticals"].append(f"匿名罐自愈失败: {hr.get('error')}")
            elif ap["verdict"] == CRITICAL and not heal:
                R["criticals"].append("匿名罐 __zse_ck 失效(--no-heal 未自愈)")
            R["probes"].append(ap)
        else:
            R["probes"].append({"probe": "anon_zse_ck", "verdict": CRITICAL,
                                "note": "结构检查未过,跳过活性探针",
                                "advice": "重新 bootstrap: python3 -c "
                                          "\"from zhihu_fast import FastClient; "
                                          "FastClient.bootstrap(cookies_file='cookies_anon.json')\""})

        # --- 分级汇总 ---
        probe_levels = [p["verdict"] for p in R["probes"]]
        R["level"] = _worst(struct_levels + probe_levels)
        for p in R["probes"]:
            if p["verdict"] == CRITICAL and p.get("note") not in R["criticals"]:
                R["criticals"].append(f"{p['probe']}: {p.get('note')} → {p.get('advice', '')}")
            elif p["verdict"] == WARN:
                R["warnings"].append(f"{p['probe']}: {p.get('note')}")
        R["probe_requests"] = prober.used
        R["exit_code"] = {OK: 0, WARN: 1, CRITICAL: 2}[R["level"]]
        return R
    finally:
        prober.close()


def _fmt_human(R: dict) -> str:
    L = []
    icon = {OK: "✅", WARN: "⚠️", CRITICAL: "🔴"}[R["level"]]
    L.append(f"{icon} 知乎双罐健康: {R['level']}  ({R['ts']})")
    for name, jar in R["jars"].items():
        age = jar.get("age") or {}
        bits = [f"{name}: {jar['verdict']}"]
        if age.get("file_age_days") is not None:
            bits.append(f"罐龄 {age['file_age_days']}d")
        if age.get("z_c0_age_days") is not None:
            bits.append(f"z_c0 签发 {age['z_c0_age_days']}d")
        if jar.get("missing"):
            bits.append(f"缺键 {jar['missing']}")
        L.append("  " + " | ".join(bits))
    for p in R["probes"]:
        st = f"status={p.get('status')}" if p.get("status") else ""
        cd = f"code={p.get('code')}" if p.get("code") else ""
        L.append(f"  探针 {p['probe']}: {p['verdict']} {st} {cd} — {p.get('note', '')}")
        if p.get("advice") and p["verdict"] != OK:
            L.append(f"    → 建议: {p['advice']}")
    if R.get("heal"):
        h = R["heal"]
        L.append(f"  自愈 bootstrap: {'✓ 成功' if h.get('ok') else '✗ 失败: ' + str(h.get('error'))} ({h.get('elapsed_s')}s)")
    for w in R["warnings"]:
        L.append(f"  ⚠️ {w}")
    for c in R["criticals"]:
        L.append(f"  🔴 {c}")
    L.append(f"  (探针请求 {R['probe_requests']}/{PROBE_BUDGET})")
    return "\n".join(L)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="知乎双罐会话健康探针与自愈")
    sub = ap.add_subparsers(dest="cmd")
    ck = sub.add_parser("check", help="跑健康检查")
    ck.add_argument("--json", action="store_true", help="机器可读输出(供 automation)")
    ck.add_argument("--no-heal", action="store_true", help="只探不修")
    ck.add_argument("--warn-days", type=float, default=180.0,
                    help="z_c0 签发龄期告警阈值(天,默认 180)")
    ck.add_argument("--qid", default=QID_DEFAULT, help="匿名探针靶问题")
    args = ap.parse_args(argv)

    if args.cmd in (None, "check"):
        R = run_check(heal=not getattr(args, "no_heal", False),
                      warn_days=getattr(args, "warn_days", 180.0),
                      qid=getattr(args, "qid", QID_DEFAULT))
        if getattr(args, "json", False):
            print(json.dumps(R, ensure_ascii=False, indent=1))
        else:
            print(_fmt_human(R))
        return R["exit_code"]
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
