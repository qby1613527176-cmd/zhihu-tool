#!/usr/bin/env python3
"""corpus_hydrate.py — corpus.db 全文补水(复用 hydrate 机制,对象是语料库)
文章 → /api/v4/articles/{id}?include=content;回答 → /api/v4/answers/{id}?include=content
断点续补: content_len>500 或 _hydrated 标记跳过。限速内建 FastClient。
用法: python3 corpus_hydrate.py [--limit N] [--status]"""
import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path

TOOL_DIR = Path(__file__).resolve().parent
DB = TOOL_DIR / "corpus" / "corpus.db"
sys.path.insert(0, str(TOOL_DIR))
from zhihu_fast import FastClient, ZhihuFastBlocked  # noqa: E402


def pending(db):
    return db.execute(
        "SELECT key, type, id FROM items WHERE type IN ('article','answer')"
        " AND hydrated = 0").fetchall()


def pending_targets(db):
    """目标集模式: 按 hydrate_targets.json 优先级顺序,跳过已补"""
    tfile = TOOL_DIR / "corpus" / "raw" / "hydrate_targets.json"
    keys = json.loads(tfile.read_text())
    done = {r[0] for r in db.execute(
        "SELECT key FROM items WHERE hydrated = 1")}
    rows = {r[0]: (r[1], r[2]) for r in db.execute(
        "SELECT key, type, id FROM items WHERE type IN ('article','answer')")}
    return [(k, rows[k][0], rows[k][1]) for k in keys if k in rows and k not in done]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--targets", action="store_true", help="只补 hydrate_targets.json 清单")
    args = ap.parse_args()

    db = sqlite3.connect(str(DB))
    todo = pending_targets(db) if args.targets else pending(db)
    total = db.execute(
        "SELECT COUNT(*) FROM items WHERE type IN ('article','answer')").fetchone()[0]
    print(f"[hydrate] 语料 {total} 条, 待补水 {len(todo)} 条")
    if args.status or not todo:
        return
    batch = todo[: args.limit] if args.limit else todo
    ok = fail = 0
    with FastClient(min_interval=5.0) as c:
        for i, (key, typ, iid) in enumerate(batch, 1):
            try:
                path = f"/api/v4/articles/{iid}" if typ == "article" \
                    else f"/api/v4/answers/{iid}"
                d = c.get(path + "?include=content,voteup_count,comment_count")
                content = d.get("content") or ""
                db.execute(
                    "UPDATE items SET content=?, content_len=?, "
                    "voteup=?, comment_count=?, fetched_at=?, hydrated=1 WHERE key=?",
                    (content, len(content),
                     d.get("voteup_count") or 0, d.get("comment_count") or 0,
                     int(time.time()), key))
                db.commit()
                ok += 1
                if i % 20 == 0:
                    print(f"[{i}/{len(batch)}] ok={ok} fail={fail}", flush=True)
            except ZhihuFastBlocked as e:
                print(f"[{i}/{len(batch)}] 风控熔断: {e}——硬停", flush=True)
                fail += 1
                break
            except Exception as e:
                fail += 1
                print(f"[{i}/{len(batch)}] {key} 异常: {type(e).__name__}", flush=True)
                time.sleep(4)
    print(f"[hydrate] 本轮: 成功 {ok} 失败 {fail} 剩余 {len(todo) - ok - fail}")


if __name__ == "__main__":
    main()
