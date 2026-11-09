#!/usr/bin/env python3
"""zhihu_fetch — 知乎采集 CLI
用法:
  python3 fetch.py question <qid> [--max 20] [-o out.json]
  python3 fetch.py article  <aid> [-o out.json]
  python3 fetch.py user     <url_token> [-o out.json]
  python3 fetch.py search   <关键词> [--max 20] [-o out.json]
  python3 fetch.py status                    # 登录态/cookie 状态
"""
import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from zhihu_fast import FastClient, ZhihuFastBlocked  # noqa: E402


def _browser():
    """浏览器通道懒加载——仅 --fast 模式不需要 playwright(手机部署零依赖)"""
    from zhihu import ZhihuSession, ZhihuBlocked, LoginRequired  # noqa: E402
    return ZhihuSession, ZhihuBlocked, LoginRequired


def _dump(data: dict, out: str | None):
    text = json.dumps(data, ensure_ascii=False, indent=2)
    if out:
        Path(out).write_text(text, encoding="utf-8")
        print(f"[saved] {out} ({len(text)} bytes)", file=sys.stderr)
    else:
        print(text)


def fast_main(args):
    """纯 HTTP 通道(无浏览器): question/feeds/user/user-answers/search/status"""
    try:
        with FastClient(min_interval=args.interval) as c:
            if args.cmd == "question":
                q = c.question(args.qid)
                feeds = c.question_feeds(args.qid, limit=args.max) if args.max > 1 else {}
                data = {"type": "question", "question": q,
                        "feeds": feeds}
            elif args.cmd == "user":
                data = {"type": "user", "user": c.user(args.token)}
            elif args.cmd == "user-answers":
                data = c.user_answers(args.token, limit=args.max)
            elif args.cmd == "search":
                data = c.search(args.query, limit=args.max)
            elif args.cmd == "status":
                data = {"logged_in": "z_c0" in c.cookies,
                        "cookies": sorted(c.cookies.keys()),
                        "channel": "fast(纯HTTP)",
                        "daily_count": c.daily_count}
            else:
                print(f"[fast] 命令 {args.cmd} 不支持纯HTTP通道(用浏览器模式)",
                      file=sys.stderr)
                sys.exit(3)
            _dump(data, getattr(args, "out", None))
    except ZhihuFastBlocked as e:
        print(f"[BLOCKED-fast] {e}", file=sys.stderr)
        sys.exit(2)


async def main():
    ap = argparse.ArgumentParser(prog="zhihu_fetch")
    ap.add_argument("--headed", action="store_true",
                    help="有头模式(需 xvfb-run),风控更松")
    ap.add_argument("--fast", action="store_true",
                    help="纯HTTP通道(无浏览器,支持 question/user/user-answers/search/status)")
    ap.add_argument("--interval", type=float, default=3.0,
                    help="导航最小间隔秒数(防风控)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("question"); p.add_argument("qid"); p.add_argument("--max", type=int, default=20); p.add_argument("--budget", type=int, default=60, help="最大滚动轮数(全量抓取调大,如 400)"); p.add_argument("-o", "--out")
    p = sub.add_parser("article");  p.add_argument("aid");  p.add_argument("-o", "--out")
    p = sub.add_parser("user");     p.add_argument("token"); p.add_argument("-o", "--out")
    p = sub.add_parser("search");   p.add_argument("query"); p.add_argument("--max", type=int, default=20); p.add_argument("-o", "--out")
    p = sub.add_parser("hot");      p.add_argument("--max", type=int, default=30); p.add_argument("-o", "--out")
    p = sub.add_parser("comments"); p.add_argument("qid"); p.add_argument("--aid", default=None, help="给了→该回答的评论;否则→问题评论"); p.add_argument("--max", type=int, default=50); p.add_argument("--children", action="store_true", help="同时抓子评论"); p.add_argument("-o", "--out")
    p = sub.add_parser("user-answers"); p.add_argument("token"); p.add_argument("--max", type=int, default=50); p.add_argument("-o", "--out")
    p = sub.add_parser("topic");    p.add_argument("tid"); p.add_argument("-o", "--out")
    p = sub.add_parser("user-articles"); p.add_argument("token"); p.add_argument("--max", type=int, default=50); p.add_argument("-o", "--out")
    p = sub.add_parser("user-followers"); p.add_argument("token"); p.add_argument("--max", type=int, default=50); p.add_argument("-o", "--out")
    p = sub.add_parser("user-followees"); p.add_argument("token"); p.add_argument("--max", type=int, default=50); p.add_argument("-o", "--out")
    p = sub.add_parser("pins");     p.add_argument("token"); p.add_argument("--max", type=int, default=50); p.add_argument("-o", "--out")
    p = sub.add_parser("collections"); p.add_argument("token"); p.add_argument("--max", type=int, default=50); p.add_argument("-o", "--out")
    sub.add_parser("status")
    sub.add_parser("bootstrap", help="匿名自举: 零浏览器从零铸 cookie 罐(vendor/zhihulite 纯算)")

    args = ap.parse_args()
    if args.cmd == "bootstrap":
        from zhihu_fast import FastClient as _FC
        _FC.bootstrap(verbose=True)
        return
    if args.fast:
        fast_main(args)
        return
    ZhihuSession, ZhihuBlocked, LoginRequired = _browser()  # 浏览器通道才加载 playwright
    try:
        async with ZhihuSession(headless=not args.headed,
                                min_interval=args.interval) as zs:
            if args.cmd == "question":
                data = await zs.get_question(args.qid, max_answers=args.max,
                                             scroll_budget=args.budget)
            elif args.cmd == "article":
                data = await zs.get_article(args.aid)
            elif args.cmd == "user":
                data = await zs.get_user(args.token)
            elif args.cmd == "search":
                data = await zs.search(args.query, max_items=args.max)
            elif args.cmd == "hot":
                data = await zs.get_hot(limit=args.max)
            elif args.cmd == "comments":
                data = await zs.get_comments(args.qid, answer_id=args.aid,
                                             max_items=args.max,
                                             include_children=args.children)
            elif args.cmd == "user-answers":
                data = await zs.get_user_answers(args.token, max_items=args.max)
            elif args.cmd == "topic":
                data = await zs.get_topic(args.tid)
            elif args.cmd == "user-articles":
                data = await zs.get_user_articles(args.token, max_items=args.max)
            elif args.cmd == "user-followers":
                data = await zs.get_user_followers(args.token, max_items=args.max)
            elif args.cmd == "user-followees":
                data = await zs.get_user_followees(args.token, max_items=args.max)
            elif args.cmd == "pins":
                data = await zs.get_pins(args.token, max_items=args.max)
            elif args.cmd == "collections":
                data = await zs.get_collections(args.token, max_items=args.max)
            elif args.cmd == "status":
                cookies = await zs.ctx.cookies("https://www.zhihu.com")
                data = {
                    "logged_in": await zs.has_login(),
                    "cookies": sorted(c["name"] for c in cookies),
                    "profile_dir": str(Path(__file__).parent / ".profile"),
                }
            else:
                return
            _dump(data, getattr(args, "out", None))
    except (ZhihuBlocked, LoginRequired) as e:
        print(f"[BLOCKED] {e}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    asyncio.run(main())
