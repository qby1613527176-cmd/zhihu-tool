"""
zhihu.py — 知乎反爬突破核心模块
路线: 真实 Chrome(Playwright) + UA/指纹伪装 + SSR initialData 解析 + 滚动拦截签名API响应
原理: 签名(x-zse-96)与 zse_ck 挑战全部由知乎自己的 JS 在真实浏览器里完成,
      我们只在网络层收割响应 JSON,不逆向任何算法 → 对签名轮换免疫。
"""
from __future__ import annotations

import asyncio
import json
import os
import random
import re
import time
from pathlib import Path
from urllib.parse import quote

from playwright.async_api import async_playwright, Browser, BrowserContext, Page

TOOL_DIR = Path(__file__).resolve().parent
PROFILE_DIR = TOOL_DIR / ".profile"          # 持久化浏览器档案(cookie/登录态)
CHROME = "/usr/bin/google-chrome"

# Chrome 大版本可用环境变量 ZHIHU_CHROME_VERSION 覆盖(防指纹漂移,与 zhihu_fast.py 同源)
_UA_VER = os.environ.get("ZHIHU_CHROME_VERSION", "150")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      f"(KHTML, like Gecko) Chrome/{_UA_VER}.0.0.0 Safari/537.36")

STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'languages', {get: () => ['zh-CN', 'zh', 'en']});
Object.defineProperty(navigator, 'platform', {get: () => 'Win32'});
window.chrome = window.chrome || {runtime: {}};
Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
const origQuery = window.navigator.permissions && window.navigator.permissions.query;
if (origQuery) {
  window.navigator.permissions.query = (p) =>
    p && p.name === 'notifications'
      ? Promise.resolve({state: Notification.permission})
      : origQuery(p);
}
"""


class ZhihuBlocked(Exception):
    """风控拦截(40362/验证码/挑战未过)"""


class LoginRequired(Exception):
    """目标内容需要登录态"""


def _strip_html(html: str) -> str:
    txt = re.sub(r"<br\s*/?>", "\n", html or "")
    txt = re.sub(r"</p>", "\n\n", txt)
    txt = re.sub(r"<[^>]+>", "", txt)
    return re.sub(r"\n{3,}", "\n\n", txt).strip()


class ZhihuSession:
    def __init__(self, headless: bool = True, min_interval: float = 3.0):
        self.headless = headless
        self.min_interval = min_interval   # 导航最小间隔(秒),防风控
        self._last_nav = 0.0
        self._pw = None
        self.browser: Browser | None = None
        self.ctx: BrowserContext | None = None
        self.page: Page | None = None

    # ---------- 生命周期 ----------
    async def __aenter__(self):
        await self.start()
        return self

    async def __aexit__(self, *exc):
        await self.close()

    async def start(self):
        PROFILE_DIR.mkdir(exist_ok=True)
        self._pw = await async_playwright().start()
        # 持久化上下文: cookie 自动落盘,挑战一次通过后长期有效
        self.ctx = await self._pw.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            executable_path=CHROME,
            headless=self.headless,
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
            viewport={"width": 1366, "height": 768},
            user_agent=UA,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-infobars",
                "--window-size=1366,768",
            ],
        )
        await self.ctx.add_init_script(STEALTH_JS)
        self.page = await self.ctx.new_page()

    async def close(self):
        if self.ctx:
            await self.ctx.close()
        if self._pw:
            await self._pw.stop()

    # ---------- 内部工具 ----------
    async def _pace(self):
        dt = time.monotonic() - self._last_nav
        wait = self.min_interval + random.uniform(0, 2.0) - dt
        if wait > 0:
            await asyncio.sleep(wait)

    def _check_blocked(self, html: str, url: str):
        if '"code":40362' in html or "请求存在异常" in html:
            raise ZhihuBlocked(f"风控硬拒(40362): {url}")
        if "安全验证" in html and "js-initialData" not in html:
            raise ZhihuBlocked(f"验证码挑战未过: {url}")

    async def _goto(self, url: str, retries: int = 2) -> str:
        """带节流+挑战等待的导航,返回最终 HTML"""
        await self._pace()
        for attempt in range(retries + 1):
            try:
                await self.page.goto(url, wait_until="domcontentloaded", timeout=45000)
            except Exception:
                if attempt == retries:
                    raise
            self._last_nav = time.monotonic()
            # 给 zse_ck 挑战 JS 执行+reload 留时间(content 可能撞导航,容错)
            html = ""
            for _ in range(8):
                await self.page.wait_for_timeout(1500)
                try:
                    html = await self.page.content()
                except Exception:
                    continue
                if len(html) > 3000 and '"code":40362' not in html:
                    break
            if len(html) > 3000:
                self._check_blocked(html, url)
                return html
        raise ZhihuBlocked(f"页面加载失败(挑战未过): {url}")

    @staticmethod
    def _parse_initial_data(html: str) -> dict:
        m = re.search(
            r'<script id="js-initialData" type="text/json">(.*?)</script>',
            html, re.S)
        if not m:
            return {}
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            return {}

    async def has_login(self) -> bool:
        cookies = await self.ctx.cookies("https://www.zhihu.com")
        return any(c["name"] == "z_c0" and c.get("value") for c in cookies)

    # ---------- 采集: 问题+回答 ----------
    async def get_question(self, qid: int | str, max_answers: int = 20,
                           scroll_budget: int = 60) -> dict:
        """抓问题页: initialData 首批回答 + 滚动拦截 feeds API 翻页"""
        url = f"https://www.zhihu.com/question/{qid}"
        collected: dict[str, dict] = {}
        paging_end = False
        api_hit = False

        async def on_response(resp):
            nonlocal paging_end, api_hit
            u = resp.url
            if not re.search(rf"/api/v4/questions/{qid}/(feeds|answers)", u):
                return
            if resp.status != 200:
                return
            try:
                data = await resp.json()
            except Exception:
                return
            api_hit = True
            for item in data.get("data", []):
                t = item.get("target", item)
                if t.get("type") == "answer" or "voteup_count" in t:
                    aid = str(t.get("id"))
                    if aid and aid != "None":
                        collected[aid] = self._norm_api_answer(t)
            pg = data.get("paging") or {}
            if pg.get("is_end"):
                paging_end = True

        self.page.on("response", lambda r: asyncio.ensure_future(on_response(r)))
        html = await self._goto(url)
        init = self._parse_initial_data(html)
        ent = init.get("initialState", {}).get("entities", {})
        question = None
        for q in (ent.get("questions") or {}).values():
            question = self._norm_question(q)
        for a in (ent.get("answers") or {}).values():
            collected[str(a.get("id"))] = self._norm_ssr_answer(a)
        if not question:
            raise ZhihuBlocked(f"initialData 中未找到问题 {qid}")

        # 滚动翻页: scrollIntoView 点燃懒加载(实测 mouse.wheel 不可靠,End 键备选)
        stall = 0
        while (len(collected) < max_answers and not paging_end
               and stall < 6 and scroll_budget > 0):
            before = len(collected)
            await self.page.evaluate("""
              const items = document.querySelectorAll('.ContentItem.AnswerItem, .List-item');
              if (items.length) items[items.length-1].scrollIntoView({block:'end'});
            """)
            for _ in range(16):          # 等增长,最长 8s
                await self.page.wait_for_timeout(500)
                if len(collected) > before or paging_end:
                    break
            if len(collected) == before and not paging_end:
                await self.page.keyboard.press("End")
                for _ in range(10):
                    await self.page.wait_for_timeout(500)
                    if len(collected) > before or paging_end:
                        break
            scroll_budget -= 1
            stall = stall + 1 if len(collected) == before else 0

        answers = sorted(collected.values(),
                         key=lambda a: -a["voteup_count"])[:max_answers]
        question["fetched_answers"] = len(answers)
        question["api_pagination_seen"] = api_hit
        question["paging_is_end"] = paging_end
        question["truncated"] = not paging_end and \
            len(answers) < question.get("answer_count", 0)
        return {"type": "question", "question": question, "answers": answers}

    @staticmethod
    def _norm_question(q: dict) -> dict:
        return {
            "id": q.get("id"),
            "title": q.get("title"),
            "detail": _strip_html(q.get("detail", "")),
            "answer_count": q.get("answerCount"),
            "follower_count": q.get("followerCount"),
            "visit_count": q.get("visitCount"),
            "created": q.get("created"),
            "updated": q.get("updated"),
            "url": f"https://www.zhihu.com/question/{q.get('id')}",
        }

    @staticmethod
    def _norm_ssr_answer(a: dict) -> dict:
        author = a.get("author") or {}
        return {
            "id": a.get("id"),
            "author": author.get("name") if isinstance(author, dict) else str(author),
            "author_token": author.get("urlToken") if isinstance(author, dict) else None,
            "voteup_count": a.get("voteupCount", 0),
            "comment_count": a.get("commentCount", 0),
            "created": a.get("createdTime") or a.get("created"),
            "updated": a.get("updatedTime") or a.get("updated"),
            "content_html": a.get("content", ""),
            "content": _strip_html(a.get("content", "")),
            "url": a.get("url") or f"https://www.zhihu.com/answer/{a.get('id')}",
        }

    @staticmethod
    def _norm_api_answer(t: dict) -> dict:
        author = t.get("author") or {}
        return {
            "id": t.get("id"),
            "author": author.get("name"),
            "author_token": author.get("url_token"),
            "voteup_count": t.get("voteup_count", 0),
            "comment_count": t.get("comment_count", 0),
            "created": t.get("created_time"),
            "updated": t.get("updated_time"),
            "content_html": t.get("content", ""),
            "content": _strip_html(t.get("content", "")),
            "url": f"https://www.zhihu.com/answer/{t.get('id')}",
        }

    # ---------- 采集: 专栏文章 ----------
    async def get_article(self, aid: int | str) -> dict:
        url = f"https://zhuanlan.zhihu.com/p/{aid}"
        html = await self._goto(url)
        init = self._parse_initial_data(html)
        arts = (init.get("initialState", {}).get("entities", {})
                .get("articles") or {})
        art = None
        for a in arts.values():
            art = a
            break
        if not art:
            # 专栏页面结构可能不同,兜底从 DOM 取
            title = await self.page.title()
            content_el = await self.page.query_selector(".Post-RichTextContainer")
            content = await content_el.inner_html() if content_el else ""
            if not content:
                raise ZhihuBlocked(f"文章 {aid} 解析失败")
            return {"type": "article", "article": {
                "id": aid, "title": title.replace(" - 知乎", ""),
                "content": _strip_html(content), "content_html": content,
                "url": url}}
        author = art.get("author") or {}
        return {"type": "article", "article": {
            "id": art.get("id"),
            "title": art.get("title"),
            "author": author.get("name"),
            "voteup_count": art.get("voteupCount") or art.get("voteup_count"),
            "comment_count": art.get("commentCount") or art.get("comment_count"),
            "created": art.get("created"),
            "updated": art.get("updated"),
            "content_html": art.get("content", ""),
            "content": _strip_html(art.get("content", "")),
            "url": url,
        }}

    # ---------- 采集: 搜索 ----------
    async def search(self, query: str, max_items: int = 20) -> dict:
        url = f"https://www.zhihu.com/search?type=content&q={quote(query)}"
        items: dict[str, dict] = {}
        paging_end = False

        async def on_response(resp):
            nonlocal paging_end
            if "/api/v4/search" not in resp.url or resp.status != 200:
                return
            try:
                data = await resp.json()
            except Exception:
                return
            for it in data.get("data", []):
                if not isinstance(it, dict):
                    continue
                obj = it.get("object", {})
                if not isinstance(obj, dict):
                    continue
                otype = obj.get("type") or it.get("type")
                oid = str(obj.get("id", ""))
                if not oid:
                    continue
                if otype == "answer":
                    q = obj.get("question") or {}
                    items[f"answer:{oid}"] = {
                        "type": "answer", "id": oid,
                        "question": _strip_html(q.get("name") or q.get("title") or ""),
                        "author": (obj.get("author") or {}).get("name"),
                        "voteup": obj.get("voteup_count"),
                        "excerpt": _strip_html(obj.get("excerpt", "")),
                        "url": f"https://www.zhihu.com/answer/{oid}",
                    }
                elif otype == "article":
                    items[f"article:{oid}"] = {
                        "type": "article", "id": oid,
                        "title": _strip_html(obj.get("title", "")),
                        "author": (obj.get("author") or {}).get("name"),
                        "voteup": obj.get("voteup_count"),
                        "excerpt": _strip_html(obj.get("excerpt", "")),
                        "url": f"https://zhuanlan.zhihu.com/p/{oid}",
                    }
            pg = data.get("paging") or {}
            if pg.get("is_end"):
                paging_end = True

        self.page.on("response", lambda r: asyncio.ensure_future(on_response(r)))
        await self._goto(url)
        stall = 0
        budget = 60
        while len(items) < max_items and not paging_end and stall < 6 and budget > 0:
            before = len(items)
            await self.page.evaluate("""
              const cards = document.querySelectorAll('.SearchResult-Card, .Card, .List-item');
              if (cards.length) cards[cards.length-1].scrollIntoView({block:'end'});
            """)
            for _ in range(16):
                await self.page.wait_for_timeout(500)
                if len(items) > before or paging_end:
                    break
            if len(items) == before and not paging_end:
                await self.page.keyboard.press("End")
                for _ in range(10):
                    await self.page.wait_for_timeout(500)
                    if len(items) > before or paging_end:
                        break
            budget -= 1
            stall = stall + 1 if len(items) == before else 0
        out = list(items.values())[:max_items]
        return {"type": "search", "query": query,
                "count": len(out), "results": out,
                "paging_is_end": paging_end}

    # ---------- 采集: 热榜 ----------
    async def get_hot(self, limit: int = 30) -> dict:
        """知乎热榜(billboard),结构: initialState.topstory.hotList"""
        html = await self._goto("https://www.zhihu.com/billboard")
        init = self._parse_initial_data(html)
        lst = (init.get("initialState", {}).get("topstory", {})
               .get("hotList", []))
        items = []
        for h in lst[:limit]:
            t = h.get("target", {})
            link = (t.get("link") or {}).get("url", "")
            m = re.search(r"question/(\d+)", link)
            items.append({
                "title": (t.get("titleArea") or {}).get("text"),
                "heat": (t.get("metricsArea") or {}).get("text"),
                "excerpt": (t.get("excerptArea") or {}).get("text"),
                "url": link,
                "question_id": m.group(1) if m else None,
            })
        return {"type": "hot", "count": len(items), "items": items}

    # ---------- 采集: 评论 ----------
    async def get_comments(self, question_id: int | str,
                           answer_id: int | str | None = None,
                           max_items: int = 50,
                           include_children: bool = False) -> dict:
        """抓评论。answer_id 给了→该回答的评论;否则→问题评论。
        include_children=True 时同时点开「N 条回复」抓子评论。"""
        if answer_id:
            url = f"https://www.zhihu.com/question/{question_id}/answer/{answer_id}"
            res_type, res_id = "answers", str(answer_id)
        else:
            url = f"https://www.zhihu.com/question/{question_id}"
            res_type, res_id = "questions", str(question_id)
        comments: dict[str, dict] = {}
        children: dict[str, dict] = {}
        paging_end = False

        async def on_response(resp):
            nonlocal paging_end
            u = resp.url
            if resp.status != 200:
                return
            # 子评论: /comment_v5/comment/{parent_id}/child_comment
            mchild = re.search(r"/api/v4/comment_v5/comment/(\d+)/child_comment", u)
            if mchild and include_children:
                try:
                    data = await resp.json()
                except Exception:
                    return
                pid = mchild.group(1)
                for c in data.get("data", []):
                    cid = str(c.get("id"))
                    if not cid or cid == "None":
                        continue
                    au = c.get("author") or {}
                    children[cid] = {
                        "id": c.get("id"),
                        "parent_id": pid,
                        "author": au.get("name"),
                        "author_token": au.get("url_token"),
                        "content": _strip_html(c.get("content", "")),
                        "like_count": c.get("like_count"),
                        "created": c.get("created_time"),
                    }
                return
            if f"/api/v4/comment_v5/{res_type}/{res_id}/" not in u:
                return
            try:
                data = await resp.json()
            except Exception:
                return
            for c in data.get("data", []):
                cid = str(c.get("id"))
                if not cid or cid == "None":
                    continue
                member = c.get("author") or {}
                comments[cid] = {
                    "id": c.get("id"),
                    "author": member.get("name"),
                    "author_token": member.get("url_token"),
                    "content": _strip_html(c.get("content", "")),
                    "like_count": c.get("like_count"),
                    "created": c.get("created_time"),
                    "child_count": c.get("child_comment_count"),
                }
            pg = data.get("paging") or {}
            if pg.get("is_end"):
                paging_end = True

        self.page.on("response", lambda r: asyncio.ensure_future(on_response(r)))
        await self._goto(url)
        # 点评论按钮: 回答级=AnswerItem 内的,问题级=页面上第一个
        clicked = False
        if answer_id:
            try:
                loc = self.page.locator(
                    ".AnswerItem >> button:has-text('评论')").first
                await loc.click(timeout=5000)
                clicked = True
            except Exception:
                pass
        if not clicked:
            try:
                loc = self.page.locator("button:has-text('条评论')").first
                await loc.click(timeout=5000)
                clicked = True
            except Exception:
                pass
        if not clicked:
            raise ZhihuBlocked("未找到评论按钮")
        await self.page.wait_for_timeout(3000)
        # 评论面板内滚动翻页(+可选子评论展开)
        stall = 0
        budget = 40
        while len(comments) < max_items and not paging_end and stall < 6 and budget > 0:
            before = len(comments) + len(children)
            if include_children:
                # 每轮点开最多 3 个「N 条回复」
                await self.page.evaluate("""(() => {
                  const els = Array.from(document.querySelectorAll('button, span, a'))
                    .filter(x => /\\d+\\s*条回复|展开回复/.test(x.textContent) && x.textContent.length < 15);
                  els.slice(0, 3).forEach(x => { try { x.click(); } catch(e){} }); })()""")
                await self.page.wait_for_timeout(1500)
            await self.page.evaluate("""(() => {
              const items = document.querySelectorAll('[class*=CommentContent], .CommentItemV2');
              if (items.length) items[items.length-1].scrollIntoView({block:'end'});
              const p = document.querySelector('.Modal-content');
              if (p) p.scrollTop = p.scrollHeight; })()""")
            for _ in range(12):
                await self.page.wait_for_timeout(500)
                if len(comments) + len(children) > before or paging_end:
                    break
            budget -= 1
            stall = stall + 1 if len(comments) + len(children) == before else 0
        out = list(comments.values())[:max_items]
        result = {"type": "comments", "target": f"{res_type}/{res_id}",
                  "count": len(out), "comments": out,
                  "paging_is_end": paging_end}
        if include_children:
            result["child_comments"] = list(children.values())
            result["child_count_fetched"] = len(children)
        return result

    # ---------- 采集: 用户回答列表 ----------
    async def get_user_answers(self, token: str, max_items: int = 50) -> dict:
        """抓用户回答列表(翻页拦截 members/{token}/answers API)"""
        url = f"https://www.zhihu.com/people/{quote(token)}/answers"
        collected: dict[str, dict] = {}
        paging_end = False
        pat = rf"/api/v4/members/{re.escape(token)}/answers"

        async def on_response(resp):
            nonlocal paging_end
            if not re.search(pat, resp.url) or resp.status != 200:
                return
            try:
                data = await resp.json()
            except Exception:
                return
            for a in data.get("data", []):
                aid = str(a.get("id"))
                if not aid or aid == "None":
                    continue
                q = a.get("question") or {}
                collected[aid] = {
                    "id": a.get("id"),
                    "question_id": q.get("id"),
                    "question": _strip_html(q.get("title", "")),
                    "voteup_count": a.get("voteup_count", 0),
                    "comment_count": a.get("comment_count", 0),
                    "created": a.get("created_time"),
                    "updated": a.get("updated_time"),
                    "excerpt": _strip_html(a.get("excerpt", "")),
                    "url": f"https://www.zhihu.com/question/{q.get('id')}/answer/{a.get('id')}",
                }
            pg = data.get("paging") or {}
            if pg.get("is_end"):
                paging_end = True

        self.page.on("response", lambda r: asyncio.ensure_future(on_response(r)))
        await self._goto(url)
        stall = 0
        budget = 60
        while len(collected) < max_items and not paging_end and stall < 6 and budget > 0:
            before = len(collected)
            await self.page.evaluate("""
              const items = document.querySelectorAll('.ContentItem, .List-item');
              if (items.length) items[items.length-1].scrollIntoView({block:'end'});""")
            for _ in range(14):
                await self.page.wait_for_timeout(500)
                if len(collected) > before or paging_end:
                    break
            if len(collected) == before and not paging_end:
                await self.page.keyboard.press("End")
                for _ in range(8):
                    await self.page.wait_for_timeout(500)
                    if len(collected) > before or paging_end:
                        break
            budget -= 1
            stall = stall + 1 if len(collected) == before else 0
        out = list(collected.values())[:max_items]
        return {"type": "user_answers", "user": token,
                "count": len(out), "answers": out,
                "paging_is_end": paging_end}

    # ---------- 采集: 话题页 ----------
    async def get_topic(self, topic_id: int | str) -> dict:
        """话题元数据(hot 页 initialState.entities.topics)"""
        html = await self._goto(f"https://www.zhihu.com/topic/{topic_id}/hot")
        init = self._parse_initial_data(html)
        topics = (init.get("initialState", {}).get("entities", {})
                  .get("topics") or {})
        t = None
        for v in topics.values():
            t = v
            break
        if not t:
            raise ZhihuBlocked(f"话题 {topic_id} 解析失败")
        return {"type": "topic", "topic": {
            "id": t.get("id"),
            "name": t.get("name"),
            "introduction": _strip_html(t.get("introduction", "")),
            "followers_count": t.get("followersCount"),
            "discussions_count": t.get("discussionsCount"),
            "questions_count": t.get("questionsCount"),
            "url": f"https://www.zhihu.com/topic/{topic_id}",
        }}

    # ---------- 采集: 用户文章列表 ----------
    async def get_user_articles(self, token: str, max_items: int = 50) -> dict:
        """用户文章列表: initialData 首页 + 滚动拦截 members/{token}/articles"""
        return await self._user_list(token, kind="articles", max_items=max_items)

    # ---------- 采集: 关注者/关注中 ----------
    async def get_user_followers(self, token: str, max_items: int = 50) -> dict:
        """粉丝列表: initialData 首页 + 滚动拦截 members/{token}/followers"""
        return await self._user_list(token, kind="followers", max_items=max_items)

    async def get_user_followees(self, token: str, max_items: int = 50) -> dict:
        """关注中列表: 同上,子路径 followees"""
        return await self._user_list(token, kind="followees", max_items=max_items)

    async def _user_list(self, token: str, kind: str, max_items: int) -> dict:
        """用户子列表通用采集(articles/followers/followees)"""
        page_map = {"articles": "posts", "followers": "followers",
                    "followees": "followees"}
        url = f"https://www.zhihu.com/people/{quote(token)}/{page_map[kind]}"
        collected: dict[str, dict] = {}
        paging_end = False
        pat = rf"/api/v4/members/{re.escape(token)}/{kind}"

        def norm(item: dict) -> dict:
            if kind == "articles":
                return {
                    "id": item.get("id"),
                    "title": _strip_html(item.get("title", "")),
                    "voteup_count": item.get("voteup_count", 0),
                    "comment_count": item.get("comment_count", 0),
                    "created": item.get("created"),
                    "updated": item.get("updated"),
                    "url": f"https://zhuanlan.zhihu.com/p/{item.get('id')}",
                }
            return {  # followers/followees: 用户对象
                "id": item.get("id"),
                "name": item.get("name"),
                "url_token": item.get("url_token"),
                "headline": item.get("headline"),
                "follower_count": item.get("follower_count"),
                "answer_count": item.get("answer_count"),
                "url": f"https://www.zhihu.com/people/{item.get('url_token')}",
            }

        async def on_response(resp):
            nonlocal paging_end
            if not re.search(pat, resp.url) or resp.status != 200:
                return
            try:
                data = await resp.json()
            except Exception:
                return
            for item in data.get("data", []):
                k = str(item.get("id"))
                if k and k != "None":
                    collected[k] = norm(item)
            pg = data.get("paging") or {}
            if pg.get("is_end"):
                paging_end = True

        self.page.on("response", lambda r: asyncio.ensure_future(on_response(r)))
        html = await self._goto(url)
        # 首页 SSR 数据(initialData entities)
        ent = (self._parse_initial_data(html)
               .get("initialState", {}).get("entities", {}))
        src = ent.get("articles") if kind == "articles" else ent.get("users")
        for k, v in (src or {}).items():
            kk = str(v.get("id") or k)
            if kk and kk != "None":
                # SSR 字段是驼峰,统一下
                nv = dict(v)
                if kind != "articles":
                    nv.setdefault("url_token", v.get("urlToken"))
                    nv.setdefault("follower_count", v.get("followerCount"))
                    nv.setdefault("answer_count", v.get("answerCount"))
                else:
                    nv.setdefault("voteup_count", v.get("voteupCount"))
                    nv.setdefault("comment_count", v.get("commentCount"))
                collected[kk] = norm(nv)

        stall = 0
        budget = 60
        while len(collected) < max_items and not paging_end and stall < 6 and budget > 0:
            before = len(collected)
            await self.page.evaluate("""
              const items = document.querySelectorAll('.ContentItem, .List-item');
              if (items.length) items[items.length-1].scrollIntoView({block:'end'});""")
            for _ in range(14):
                await self.page.wait_for_timeout(500)
                if len(collected) > before or paging_end:
                    break
            if len(collected) == before and not paging_end:
                await self.page.keyboard.press("End")
                for _ in range(8):
                    await self.page.wait_for_timeout(500)
                    if len(collected) > before or paging_end:
                        break
            budget -= 1
            stall = stall + 1 if len(collected) == before else 0
        out = list(collected.values())[:max_items]
        return {"type": f"user_{kind}", "user": token,
                "count": len(out), "items": out,
                "paging_is_end": paging_end}

    # ---------- 采集: 想法 pins ----------
    async def get_pins(self, token: str, max_items: int = 50) -> dict:
        """用户想法: /people/{token}/pins 滚动拦截 /api/v4/v2/pins/{token}/moments"""
        url = f"https://www.zhihu.com/people/{quote(token)}/pins"
        collected: dict[str, dict] = {}
        paging_end = False
        pat = rf"/api/v4/v2/pins/{re.escape(token)}/moments"

        async def on_response(resp):
            nonlocal paging_end
            if not re.search(pat, resp.url) or resp.status != 200:
                return
            try:
                data = await resp.json()
            except Exception:
                return
            for p in data.get("data", []):
                pid = str(p.get("id"))
                if not pid or pid == "None":
                    continue
                parts = []
                for c in p.get("content", []):
                    if not isinstance(c, dict):
                        continue
                    ct = c.get("type")
                    if ct == "text":
                        parts.append(_strip_html(c.get("content", "")))
                    elif ct == "image":
                        parts.append(f"[图片 {c.get('url') or c.get('original_url','')}]")
                    elif ct == "link":
                        lk = c.get("link") or c.get("video") or {}
                        parts.append(f"[链接 {lk.get('title') or lk.get('url','')}]")
                    elif ct == "video":
                        parts.append("[视频]")
                au = p.get("author") or {}
                collected[pid] = {
                    "id": p.get("id"),
                    "author": au.get("name"),
                    "content": "\n".join(x for x in parts if x).strip(),
                    "like_count": p.get("like_count"),
                    "comment_count": p.get("comment_count"),
                    "created": p.get("created"),
                    "updated": p.get("updated"),
                    "url": f"https://www.zhihu.com/pin/{p.get('id')}",
                }
            pg = data.get("paging") or {}
            if pg.get("is_end"):
                paging_end = True

        self.page.on("response", lambda r: asyncio.ensure_future(on_response(r)))
        await self._goto(url)
        stall = 0
        budget = 60
        while len(collected) < max_items and not paging_end and stall < 6 and budget > 0:
            before = len(collected)
            await self.page.evaluate("""
              const items = document.querySelectorAll('.ContentItem, .List-item, .PinItem');
              if (items.length) items[items.length-1].scrollIntoView({block:'end'});""")
            for _ in range(14):
                await self.page.wait_for_timeout(500)
                if len(collected) > before or paging_end:
                    break
            if len(collected) == before and not paging_end:
                await self.page.keyboard.press("End")
                for _ in range(8):
                    await self.page.wait_for_timeout(500)
                    if len(collected) > before or paging_end:
                        break
            budget -= 1
            stall = stall + 1 if len(collected) == before else 0
        out = list(collected.values())[:max_items]
        return {"type": "pins", "user": token,
                "count": len(out), "items": out,
                "paging_is_end": paging_end}

    # ---------- 采集: 收藏夹列表 ----------
    async def get_collections(self, token: str, max_items: int = 50) -> dict:
        """用户收藏夹列表: /people/{token}/collections 滚动拦截 people/{token}/collections API"""
        url = f"https://www.zhihu.com/people/{quote(token)}/collections"
        collected: dict[str, dict] = {}
        paging_end = False
        pat = rf"/api/v4/people/{re.escape(token)}/collections"

        async def on_response(resp):
            nonlocal paging_end
            if not re.search(pat, resp.url) or resp.status != 200:
                return
            try:
                data = await resp.json()
            except Exception:
                return
            for f in data.get("data", []):
                fid = str(f.get("id"))
                if not fid or fid == "None":
                    continue
                collected[fid] = {
                    "id": f.get("id"),
                    "title": f.get("title"),
                    "answer_count": f.get("answer_count"),
                    "article_count": f.get("article_count"),
                    "follower_count": f.get("follower_count"),
                    "is_public": f.get("is_public"),
                    "updated": f.get("updated_time"),
                    "url": f"https://www.zhihu.com/favlist/{f.get('id')}",
                }
            pg = data.get("paging") or {}
            if pg.get("is_end"):
                paging_end = True

        self.page.on("response", lambda r: asyncio.ensure_future(on_response(r)))
        await self._goto(url)
        stall = 0
        budget = 60
        while len(collected) < max_items and not paging_end and stall < 6 and budget > 0:
            before = len(collected)
            await self.page.evaluate("""
              const items = document.querySelectorAll('.ContentItem, .List-item, .Card');
              if (items.length) items[items.length-1].scrollIntoView({block:'end'});""")
            for _ in range(14):
                await self.page.wait_for_timeout(500)
                if len(collected) > before or paging_end:
                    break
            if len(collected) == before and not paging_end:
                await self.page.keyboard.press("End")
                for _ in range(8):
                    await self.page.wait_for_timeout(500)
                    if len(collected) > before or paging_end:
                        break
            budget -= 1
            stall = stall + 1 if len(collected) == before else 0
        out = list(collected.values())[:max_items]
        return {"type": "collections", "user": token,
                "count": len(out), "items": out,
                "paging_is_end": paging_end}

    # ---------- 采集: 用户主页 ----------
    async def get_user(self, token: str) -> dict:
        url = f"https://www.zhihu.com/people/{quote(token)}"
        html = await self._goto(url)
        init = self._parse_initial_data(html)
        users = (init.get("initialState", {}).get("entities", {})
                 .get("users") or {})
        u = None
        for v in users.values():
            if v.get("urlToken") == token or not u:
                u = v
        if not u:
            raise ZhihuBlocked(f"用户 {token} 解析失败(可能需要登录)")
        return {"type": "user", "user": {
            "name": u.get("name"),
            "url_token": u.get("urlToken"),
            "headline": u.get("headline"),
            "description": _strip_html(u.get("description", "")),
            "follower_count": u.get("followerCount"),
            "following_count": u.get("followingCount"),
            "answer_count": u.get("answerCount"),
            "articles_count": u.get("articlesCount"),
            "voteup_count": u.get("voteupCount"),
            "gender": u.get("gender"),
            "url": url,
        }}
