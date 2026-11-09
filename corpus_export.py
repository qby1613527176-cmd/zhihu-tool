#!/usr/bin/env python3
"""corpus_export.py — 语料交付导出
corpus.db → 按门派分类的 Markdown 档案 + JSONL + 统计报告
用法: python3 corpus_export.py [--school AP|PY|cross|unclear] [--min-quality 0]"""
import argparse
import html
import json
import re
import sqlite3
import time
from pathlib import Path

TOOL_DIR = Path(__file__).resolve().parent
DB = TOOL_DIR / "corpus" / "corpus.db"
OUT = TOOL_DIR / "corpus" / "export"


def html_to_md(h: str) -> str:
    if not h:
        return ""
    s = h
    s = re.sub(r'<pre[^>]*><code[^>]*>(.*?)</code></pre>',
               lambda m: "\n```\n" + html.unescape(re.sub(r"<[^>]+>", "", m.group(1))) + "\n```\n",
               s, flags=re.S)
    s = re.sub(r'<code[^>]*>(.*?)</code>', lambda m: "`" + html.unescape(m.group(1)) + "`", s, flags=re.S)
    s = re.sub(r'<blockquote[^>]*>', "\n> ", s)
    s = re.sub(r'</blockquote>', "\n", s)
    s = re.sub(r'<h([1-6])[^>]*>', lambda m: "\n" + "#" * int(m.group(1)) + " ", s)
    s = re.sub(r'</h[1-6]>', "\n", s)
    s = re.sub(r'<p[^>]*>', "\n\n", s)
    s = re.sub(r'<br\s*/?>', "\n", s)
    s = re.sub(r'<hr\s*/?>', "\n\n---\n\n", s)
    s = re.sub(r'<(?:strong|b)[^>]*>(.*?)</(?:strong|b)>', r"**\1**", s, flags=re.S)
    s = re.sub(r'<(?:em|i)[^>]*>(.*?)</(?:em|i)>', r"*\1*", s, flags=re.S)
    s = re.sub(r'<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
               lambda m: f"[{re.sub(r'<[^>]+>', '', m.group(2))}]({m.group(1)})", s, flags=re.S)
    s = re.sub(r'<img[^>]*(?:data-original|data-actualsrc|src)="([^"]+)"[^>]*/?>', r"![图](\1)", s)
    s = re.sub(r'<li[^>]*>', "\n- ", s)
    s = re.sub(r'</?(?:ul|ol)[^>]*>', "\n", s)
    s = re.sub(r"<[^>]+>", "", s)
    s = html.unescape(s)
    return re.sub(r"\n{3,}", "\n\n", s).strip()


# 相关性分级词汇(收割期实证词表)
_STRONG = ["态度类型", "心理智学", "psychosophy", "attitudinal psyche",
           "attitudinalpsyche", "态度心理", "克难学", "attitude type"]
_STRUCT = ["亚型", "功能组", "象限", "反应组", "e情绪", "f物理", "v意志", "l逻辑",
           "amatorics", "superatorics", "1e", "2e", "3e", "1v", "2v", "3v", "1f", "2f", "3f", "1l", "2l", "3l",
           "主导功能", "辅助功能", "功能位", "tritype", "侧翼", "本能类型", "本能副型",
           "dichos", "主副型", "主型"]
_ADJACENT = ["mbti", "九型", "socionics", "荣格", "判型", "tritype", "类型学",
             "人格类型", "enneagram", "自判", "外倾思维", "内倾", "外倾感觉", "内倾感觉",
             "外倾情感", "内倾情感", "外倾直觉", "内倾直觉", "八维"]
# 功能码/型号码: 以非ASCII字母数字为界(中文紧邻也算界,解决「大家的Si」「Se前两位」漏匹配)
_FUNC_RE = re.compile(r"(?<![A-Za-z0-9])(Te|Ti|Fe|Fi|Se|Si|Ne|Ni)(?![A-Za-z0-9])")
_NUMTYPE_RE = re.compile(r"[1-9]号(人格|主|型)|[1-9]主")   # 九型口语: 3号人格/6主
_MBTI_RE = re.compile(r"(?<![A-Za-z0-9])[IE][NS][TF][JP](?![A-Za-z0-9])")  # MBTI 型号码(intp/ENFJ...)
_COMMUNITY = {"意外活着", "余音", "早霜真琴", "Lappland", "云烟向晚",
              "不能含有特殊字符", "诗篇", "冷熵", "三光之泷", "兮墨official",
              "游三令客", "bblgh", "氵氵"}


def relevance(title: str, excerpt: str, content: str, author: str, school: str) -> tuple[str, int]:
    """相关性分级: core(门派命中)/relevant(类型学语境)/community(社群作者)/adjacent(邻近)/noise"""
    if school in ("AP", "PY", "cross"):
        return "core", 100
    if school == "rescued":           # LLM 子代理从噪音层语义救回
        return "relevant", 50
    raw_text = title + " " + excerpt + " " + (content or "")[:3000]
    t = raw_text.lower()
    score = 0
    score += 40 * any(s in t for s in _STRONG)
    score += 15 * sum(1 for s in _STRUCT if s in t)
    score += 8 * sum(1 for s in _ADJACENT if s in t)
    func_hits = len(set(_FUNC_RE.findall(raw_text)))   # 功能码(原文大小写)
    score += 10 * min(func_hits, 3)
    if _NUMTYPE_RE.search(raw_text):
        score += 8
    if _MBTI_RE.search(raw_text):
        score += 8
    if author in _COMMUNITY:
        score += 35
    if score >= 40:
        return "relevant", score
    if score >= 8:                     # 邻近类型学 1 个词即保留(下游按 tier 自选)
        return "adjacent", score
    if author in _COMMUNITY:
        return "community", 15
    return "noise", score


def quality(row) -> float:
    """质量分: 赞×1 + 评论×0.5 + 长度权重(>2000字=2,>8000=4) + 文章加成1"""
    voteup, comments, clen, typ = row
    q = (voteup or 0) + (comments or 0) * 0.5
    q += 2 if clen > 2000 else 0
    q += 4 if clen > 8000 else 0
    q += 1 if typ == "article" else 0
    return q


def load_verdicts() -> dict:
    """加载 LLM 语义筛 verdict(judge_*.jsonl): key -> (verdict, school)"""
    out = {}
    for f in sorted((TOOL_DIR / "corpus" / "raw").glob("judge_*.jsonl")):
        for line in f.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                v = json.loads(line)
                out[v["key"]] = (v.get("verdict"), v.get("school"))
            except (json.JSONDecodeError, KeyError):
                continue
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--school", default=None)
    ap.add_argument("--min-quality", type=float, default=0)
    args = ap.parse_args()

    db = sqlite3.connect(str(DB))
    where = "WHERE type IN ('article','answer')"
    if args.school:
        where += f" AND school='{args.school}'"
    rows = db.execute(
        f"SELECT key,type,id,title,question_title,author_name,author_token,"
        f"voteup,comment_count,url,school,content,content_len,keywords,raw FROM items {where}",
    ).fetchall()

    OUT.mkdir(parents=True, exist_ok=True)
    schools = {}
    tier_stats = {"core": 0, "relevant": 0, "adjacent": 0, "community": 0, "noise": 0, "rescued": 0}
    verdicts = load_verdicts()
    jsonl = open(OUT / "corpus_all.jsonl", "w", encoding="utf-8")
    for r in rows:
        (key, typ, iid, title, qtitle, author, atok, voteup, comments,
         url, school, content, clen, kws, raw) = r
        excerpt = ""
        try:
            robj = json.loads(raw or "{}")
            excerpt = robj.get("excerpt") or robj.get("excerpt_new") or ""
        except Exception:
            pass
        tier, rscore = relevance(title or qtitle or "", excerpt, content, author, school or "unclear")
        # LLM 语义筛裁决覆盖规则判级: keep 拯救为 rescued 档
        if tier == "noise" and key in verdicts and verdicts[key][0] == "keep":
            tier = "rescued"
            rscore = 5
            tier_stats["rescued"] = tier_stats.get("rescued", 0) + 1
        tier_stats[tier] += 1
        if tier == "noise":
            continue                      # 噪音不进交付语料
        q = quality((voteup, comments, clen, typ))
        if q < args.min_quality:
            continue
        rec = {"id": iid, "type": typ, "school": school, "tier": tier,
               "relevance": rscore,
               "title": title or qtitle, "author": author, "author_token": atok,
               "voteup": voteup, "comments": comments, "url": url,
               "quality": round(q, 1), "content_len": clen,
               "keywords": json.loads(kws or "[]"),
               "content_md": html_to_md(content or "")}
        jsonl.write(json.dumps(rec, ensure_ascii=False) + "\n")
        schools.setdefault(school or "unclear", []).append(rec)
    jsonl.close()

    # 按门派分档 Markdown
    for school, recs in schools.items():
        recs.sort(key=lambda x: -x["quality"])
        out = OUT / f"corpus_{school}.md"
        with open(out, "w", encoding="utf-8") as f:
            f.write(f"# 知乎语料 · {school} 门派\n\n")
            f.write(f"> {len(recs)} 条 · 按质量分排序 · 导出 {time.strftime('%Y-%m-%d %H:%M')}\n\n---\n\n")
            for i, rec in enumerate(recs, 1):
                f.write(f"## {i}. [{rec['type']}] {rec['title']}\n\n")
                f.write(f"作者:{rec['author']} · 赞{rec['voteup']} · 评{rec['comments']} "
                        f"· 质量分{rec['quality']} · [{rec['url']}]({rec['url']})\n\n")
                f.write((rec["content_md"] or "*(全文待补水)*") + "\n\n---\n\n")

    # 统计报告
    total_chars = sum(r["content_len"] for recs in schools.values() for r in recs)
    report = {
        "exported_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "total_items": sum(len(v) for v in schools.values()),
        "tier_stats": tier_stats,
        "by_school": {k: len(v) for k, v in schools.items()},
        "total_content_chars": total_chars,
        "top_quality": sorted(
            ({"title": r["title"], "author": r["author"], "quality": r["quality"], "url": r["url"]}
             for recs in schools.values() for r in recs),
            key=lambda x: -x["quality"])[:20],
    }
    (OUT / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1))
    print(json.dumps(report, ensure_ascii=False, indent=1)[:800])


if __name__ == "__main__":
    main()
