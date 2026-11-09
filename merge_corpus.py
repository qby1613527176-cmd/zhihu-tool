#!/usr/bin/env python3
"""merge_corpus.py — 合并 h1-h4 收割产物 → 去重 → 分类 → 质量评分 → corpus.db"""
import json
import re
import sqlite3
from pathlib import Path

TOOL_DIR = Path(__file__).resolve().parent
RAW = TOOL_DIR / "corpus" / "raw"
DB = TOOL_DIR / "corpus" / "corpus.db"

# 分类信号(侦察+收割期实证词汇)
AP_SIGNALS = ["态度类型", "态度心理", "attitudinal psyche", "attitudinalpsyche",
              "态度类型学", "attitude type"]
PY_SIGNALS = ["心理智学", "psychosophy", "克难学", "功能组", "象限",
              "amatorics", "superatorics", "反应组"]
# 共享术语(单出不算门派证据): 亚型/E情绪/F物理/V意志/L逻辑/功能


def classify(text: str) -> tuple[str, list[str]]:
    t = text.lower()
    ap_hits = [s for s in AP_SIGNALS if s.lower() in t]
    py_hits = [s for s in PY_SIGNALS if s.lower() in t]
    if ap_hits and py_hits:
        return "cross", ap_hits + py_hits
    if ap_hits:
        return "AP", ap_hits
    if py_hits:
        return "PY", py_hits
    return "unclear", []


def main():
    db = sqlite3.connect(str(DB))
    db.executescript("""
    CREATE TABLE IF NOT EXISTS items(
      key TEXT PRIMARY KEY,          -- type:id
      type TEXT, id TEXT,
      title TEXT, question_title TEXT, question_id TEXT,
      author_name TEXT, author_token TEXT,
      voteup INTEGER, comment_count INTEGER,
      url TEXT, keywords TEXT,       -- 命中关键词集合
      school TEXT, school_signals TEXT,
      content TEXT,                  -- 全文(补水后填)
      content_len INTEGER DEFAULT 0,
      raw TEXT, fetched_at INTEGER DEFAULT 0);
    CREATE INDEX IF NOT EXISTS idx_items_type ON items(type);
    CREATE INDEX IF NOT EXISTS idx_items_school ON items(school);
    CREATE INDEX IF NOT EXISTS idx_items_author ON items(author_token);
    """)

    n_raw = n_dup = 0
    for f in sorted(RAW.glob("*.jsonl")):
        for line in f.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            n_raw += 1
            typ = r.get("type")
            iid = str(r.get("id") or "")
            if not iid or iid == "None":
                continue
            key = f"{typ}:{iid}"
            text_for_cls = " ".join(str(r.get(k) or "") for k in
                                    ("title", "question_title")) + " " + \
                           str((r.get("raw") or {}).get("excerpt", ""))
            school, signals = classify(text_for_cls)
            url = r.get("url") or ""
            if typ == "article" and "/p/" not in url:
                url = f"https://zhuanlan.zhihu.com/p/{iid}"
            # 已存在则并关键词,取最大赞数
            cur = db.execute("SELECT keywords, voteup, comment_count FROM items WHERE key=?",
                             (key,)).fetchone()
            if cur:
                n_dup += 1
                kws = sorted(set(json.loads(cur[0]) + [r.get("keyword")]))
                db.execute("UPDATE items SET keywords=?, voteup=MAX(voteup,?), "
                           "comment_count=MAX(comment_count,?) WHERE key=?",
                           (json.dumps(kws, ensure_ascii=False), r.get("voteup") or 0,
                            r.get("comment_count") or 0, key))
            else:
                db.execute(
                    "INSERT INTO items(key,type,id,title,question_title,question_id,"
                    "author_name,author_token,voteup,comment_count,url,keywords,"
                    "school,school_signals,raw) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (key, typ, iid, r.get("title") or "", r.get("question_title") or "",
                     str(r.get("question_id") or ""), r.get("author_name") or "",
                     r.get("author_token") or "", r.get("voteup") or 0,
                     r.get("comment_count") or 0, url,
                     json.dumps([r.get("keyword")], ensure_ascii=False),
                     school, json.dumps(signals, ensure_ascii=False),
                     json.dumps(r.get("raw") or {}, ensure_ascii=False)))
    db.commit()

    # 统计
    stats = {}
    for row in db.execute("SELECT type, school, COUNT(*) FROM items GROUP BY type, school"):
        stats[f"{row[0]}/{row[1]}"] = row[2]
    total = db.execute("SELECT COUNT(*) FROM items").fetchone()[0]
    authors = db.execute(
        "SELECT author_token, author_name, COUNT(*) c, SUM(voteup) v FROM items "
        "WHERE author_token != '' GROUP BY author_token ORDER BY c DESC LIMIT 15").fetchall()
    print(f"原始 {n_raw} 行 → 去重并入 {n_dup} → 独立条目 {total}")
    print("分类×类型分布:", json.dumps(stats, ensure_ascii=False, indent=1))
    print("\n作者产出 top15(滚雪球候选):")
    for tok, name, c, v in authors:
        print(f"  {name or tok}: {c} 条, 总赞 {v}")


if __name__ == "__main__":
    main()
