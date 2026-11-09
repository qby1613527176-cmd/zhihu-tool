#!/usr/bin/env python3
"""zhihu_login — 登录态获取
两种模式:
  python3 login.py qr          # 二维码扫码登录(生成 QR PNG,等待扫码)
  python3 login.py cookies     # 从浏览器导入 cookie 字符串(粘到 stdin)
登录态保存在 .profile/ 持久化浏览器档案里,后续 fetch 自动复用。
"""
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from zhihu import ZhihuSession  # noqa: E402

QR_PNG = Path("/tmp/zhihu_login_qr.png")
SIGNIN = "https://www.zhihu.com/signin"


async def qr_login(timeout: int = 300):
    async with ZhihuSession(headless=True, min_interval=1.5) as zs:
        page = zs.page
        await page.goto(SIGNIN, wait_until="domcontentloaded", timeout=45000)
        # 等页面渲染,尝试切到扫码 tab
        await page.wait_for_timeout(4000)
        for sel in ["div.SignFlow-tab:has-text('扫码登录')",
                    "div[role=tab]:has-text('扫码')",
                    "button:has-text('扫码登录')",
                    "span:has-text('扫码登录')"]:
            try:
                el = await page.query_selector(sel)
                if el:
                    await el.click()
                    print(f"[qr] 已点击扫码 tab: {sel}", flush=True)
                    await page.wait_for_timeout(2500)
                    break
            except Exception:
                continue
        # 找二维码元素截图
        qr_shot = None
        for sel in [".Qrcode-img", "img.Qrcode", ".Login-qrcode img",
                    "canvas", "img[src^='data:image']"]:
            try:
                el = await page.query_selector(sel)
                if el:
                    await el.screenshot(path=str(QR_PNG))
                    qr_shot = sel
                    break
            except Exception:
                continue
        if not qr_shot:
            # 兜底:整页截图
            await page.screenshot(path=str(QR_PNG))
            print("[qr] 未定位到二维码元素,已整页截图", flush=True)
        else:
            print(f"[qr] 二维码已截图({qr_shot}) -> {QR_PNG}", flush=True)
        print("[qr] 等待扫码...", flush=True)

        # 轮询登录态: z_c0 cookie 出现或 URL 离开 signin
        t0 = time.monotonic()
        while time.monotonic() - t0 < timeout:
            await page.wait_for_timeout(3000)
            if await zs.has_login():
                print("[qr] ✅ 登录成功(z_c0 已签发)", flush=True)
                return True
            if "signin" not in page.url and "signup" not in page.url:
                # 跳走了再确认 cookie
                if await zs.has_login():
                    print(f"[qr] ✅ 登录成功(跳转 {page.url})", flush=True)
                    return True
            # 二维码可能过期,刷新重截
            if int(time.monotonic() - t0) % 60 == 0:
                try:
                    el = await page.query_selector(".Qrcode-img") \
                        or await page.query_selector(".Login-qrcode img")
                    if el:
                        await el.screenshot(path=str(QR_PNG))
                        print("[qr] 二维码已刷新", flush=True)
                except Exception:
                    pass
        print("[qr] ❌ 超时未扫码", flush=True)
        return False


async def import_cookies(raw: str):
    """导入浏览器 cookie 字符串(格式: k1=v1; k2=v2; ...)"""
    cookies = []
    for pair in raw.strip().split(";"):
        if "=" not in pair:
            continue
        k, v = pair.split("=", 1)
        k, v = k.strip(), v.strip()
        if not k:
            continue
        cookies.append({
            "name": k, "value": v,
            "domain": ".zhihu.com", "path": "/",
            "secure": True, "sameSite": "Lax",
        })
    if not any(c["name"] in ("z_c0", "d_c0") for c in cookies):
        print("[cookies] ❌ 未找到 z_c0/d_c0,导入无效", flush=True)
        return False
    async with ZhihuSession(headless=True, min_interval=1.5) as zs:
        await zs.ctx.add_cookies(cookies)
        # 验证:访问首页看是否登录
        await zs.page.goto("https://www.zhihu.com/",
                           wait_until="domcontentloaded", timeout=45000)
        await zs.page.wait_for_timeout(5000)
        ok = await zs.has_login()
        print(f"[cookies] {'✅ 登录态有效' if ok else '⚠️ z_c0 未生效'}", flush=True)
        return ok


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in ("qr", "cookies"):
        print(__doc__)
        sys.exit(1)
    if sys.argv[1] == "qr":
        ok = asyncio.run(qr_login())
    else:
        raw = sys.stdin.read()
        ok = asyncio.run(import_cookies(raw))
    sys.exit(0 if ok else 2)


if __name__ == "__main__":
    main()
