#!/usr/bin/env python3
"""
exporter.py — 知乎抓取数据 → Markdown 可读档案(零第三方依赖,零网络)
数据源: harvester.py 的 harvest.sqlite3(answers 表,raw 列存完整 JSON)

用法:
  python3 exporter.py question 19550225 [-o output/export/]
  python3 exporter.py question 19550225 --top 50     # 只导前 50 赞
"""
import argparse
import html
import json
import re
import sqlite3
import time
from pathlib import Path

TOOL_DIR = Path(__file__).resolve().parent
DB = TOOL_DIR / "harvest.sqlite3"


def html_to_md(h: str) -> str:
    """知乎回答 HTML → Markdown(覆盖常见标签,容错优先)"""
    if not h:
        return ""
    s = h
    # 代码块/行内代码先行保护
    s = re.sub(r'<pre[^>]*><code[^>]*>(.*?)</code></pre>',
               lambda m: "\n```\n" + html.unescape(re.sub(r"<[^>]+>", "", m.group(1))) + "\n```\n",
               s, flags=re.S)
    s = re.sub(r'<code[^>]*>(.*?)</code>',
               lambda m: "`" + html.unescape(m.group(1)) + "`", s, flags=re.S)
    # 块级元素
    s = re.sub(r'<blockquote[^>]*>', "\n> ", s)
    s = re.sub(r'</blockquote>', "\n", s)
    s = re.sub(r'<h([1-6])[^>]*>', lambda m: "\n" + "#" * int(m.group(1)) + " ", s)
    s = re.sub(r'</h[1-6]>', "\n", s)
    s = re.sub(r'<p[^>]*>', "\n\n", s)
    s = re.sub(r'<br\s*/?>', "\n", s)
    s = re.sub(r'<hr\s*/?>', "\n\n---\n\n", s)
    # 强调
    s = re.sub(r'<(?:strong|b)[^>]*>(.*?)</(?:strong|b)>', r"**\1**", s, flags=re.S)
    s = re.sub(r'<(?:em|i)[^>]*>(.*?)</(?:em|i)>', r"*\1*", s, flags=re.S)
    # 链接与图片
    s = re.sub(r'<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
               lambda m: f"[{re.sub(r'<[^>]+>', '', m.group(2))}]({m.group(1)})",
               s, flags=re.S)
    s = re.sub(r'<img[^>]*(?:data-original|data-actualsrc|src)="([^"]+)"[^>]*/?>',
               r"![图](\1)", s)
    # 列表
    s = re.sub(r'<li[^>]*>', "\n- ", s)
    s = re.sub(r'</?(?:ul|ol)[^>]*>', "\n", s)
    # 其余标签剥掉
    s = re.sub(r"<[^>]+>", "", s)
    s = html.unescape(s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def export_question(qid: str, out_dir: Path, top: int | None = None) -> Path:
    db = sqlite3.connect(str(DB))
    rows = db.execute(
        "SELECT answer_id, voteup_count, comment_count, author_name, created_time,"
        "       updated_time, url, raw FROM answers WHERE question_id=?"
        " ORDER BY voteup_count DESC", (qid,)).fetchall()
    if top:
        rows = rows[:top]
    if not rows:
        raise SystemExit(f"库中无问题 {qid} 的回答(先跑 harvester.py {qid})")

    # 问题标题从第一条 raw 里拿(question 字段)
    title = qid
    try:
        r0 = json.loads(rows[0][7])
        title = (r0.get("question") or {}).get("title") or qid
    except Exception:
        pass

    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"q{qid}.md"
    lines = [
        f"# {title}",
        "",
        f"> 知乎问题 {qid} · 共 {len(rows)} 个回答(按赞数排序) "
        f"· 导出于 {time.strftime('%Y-%m-%d %H:%M')}",
        "",
        "---",
        "",
    ]
    for i, (aid, votes, comments, author, created, updated, url, raw) in enumerate(rows, 1):
        try:
            rj = json.loads(raw)
            content_html = rj.get("content") or rj.get("content_html") or ""
        except Exception:
            content_html = ""
        body = html_to_md(content_html)
        ts = time.strftime("%Y-%m-%d", time.localtime(created or 0))
        lines += [
            f"## {i}. {author or '匿名'}",
            "",
            f"👍 {votes} · 💬 {comments} · {ts} · [原文]({url})",
            "",
            body,
            "",
            "---",
            "",
        ]
    out.write_text("\n".join(lines), encoding="utf-8")
    return out


def main():
    ap = argparse.ArgumentParser(prog="zhihu_export")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("question")
    p.add_argument("qid")
    p.add_argument("--top", type=int, default=None)
    p.add_argument("-o", "--out", default=str(TOOL_DIR / "output" / "export"))
    args = ap.parse_args()
    if args.cmd == "question":
        out = export_question(args.qid, Path(args.out), args.top)
        size = out.stat().st_size
        print(f"[exported] {out} ({size/1024:.1f} KB)")


if __name__ == "__main__":
    main()
