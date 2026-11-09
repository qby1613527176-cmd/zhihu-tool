#!/usr/bin/env python3
"""
kb.py — 知乎抓取结果的本地知识库层(SQLite + FTS5 全文检索)
零第三方依赖(仅标准库)。与 fetch.py 解耦: 只消费 output/*.json。

== 中文分词决策(详见 research/d4_知识库层.md) ==
SQLite FTS5 无内置中文分词器。三个候选:
  1. unicode61 默认: 连续 CJK 字符合成一个大 token(Lo 类) → "如何正确使用知乎"
     整体一个 token,搜 "知乎" 零命中 → 不可用。
  2. trigram(SQLite≥3.34): 真子串匹配,但查询必须 ≥3 字符 → "知乎"/"使用"
     两个字的查询直接无法服务 → 否决。
  3. 二字组(bigram)预处理 + unicode61: 索引前把中文切成重叠二字组
     ("山茶油"→"山茶 茶油"),查询同样变换加引号短语("山茶 茶油")。
     2 字词天然是一个 bigram,召回/精度都正确。
选型: 方案 3。已知限制: 单字查询(如 "油")无法命中 bigram 索引;CJK bigram
的 AND 语义理论上有非相邻假阳性(概率极低);拉丁词按整词索引(小写归一)。

用法:
  python3 kb.py ingest [目录]      # 灌入(默认 ./output),幂等
  python3 kb.py search "关键词" [--limit N]
  python3 kb.py stats
"""
import argparse
import json
import re
import sqlite3
import sys
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DB = BASE_DIR / "kb.sqlite3"
DEFAULT_OUTPUT = BASE_DIR / "output"

HL_L, HL_R = "【", "】"  # 高亮标记

# ---------------------------------------------------------------- 分词

_CJK_RE = re.compile(r"[一-鿿㐀-䶿]")  # CJK 统一表意文字 + 扩展A
_WORD_RE = re.compile(r"[A-Za-z0-9_]+")


def bigram_tokens(text: str) -> list:
    """索引侧: 文本 → token 列表(CJK 重叠二字组 + 拉丁整词小写)。"""
    if not text:
        return []
    tokens, run = [], []

    def flush_run():
        if len(run) == 1:
            tokens.append(run[0])  # 落单单字(罕见,保留无妨)
        else:
            for i in range(len(run) - 1):
                tokens.append(run[i] + run[i + 1])
        run.clear()

    pos = 0
    for m in _WORD_RE.finditer(text):
        for ch in text[pos:m.start()]:
            if _CJK_RE.match(ch):
                run.append(ch)
            else:
                flush_run()
        flush_run()
        if len(m.group()) > 1 or not _CJK_RE.match(m.group()):
            tokens.append(m.group().lower())
        pos = m.end()
    for ch in text[pos:]:
        if _CJK_RE.match(ch):
            run.append(ch)
        else:
            flush_run()
    flush_run()
    return tokens


def index_text(text: str) -> str:
    return " ".join(bigram_tokens(text))


def query_to_match(query: str) -> str:
    """查询侧: 连续 CJK 段 → 引号短语(强制相邻);拉丁词 → 裸 token。段间 AND。"""
    if not query or not query.strip():
        return ""
    groups, run = [], []

    def flush_run():
        if len(run) == 1:
            groups.append(f'"{run[0]}"')
        elif run:
            bg = " ".join(run[i] + run[i + 1] for i in range(len(run) - 1))
            groups.append(f'"{bg}"')
        run.clear()

    pos = 0
    for m in _WORD_RE.finditer(query):
        for ch in query[pos:m.start()]:
            if _CJK_RE.match(ch):
                run.append(ch)
            else:
                flush_run()
        flush_run()
        groups.append(m.group().lower())
        pos = m.end()
    for ch in query[pos:]:
        if _CJK_RE.match(ch):
            run.append(ch)
        else:
            flush_run()
    flush_run()
    return " ".join(groups)


def make_snippet(text: str, query: str, width: int = 80) -> str:
    """从原文(非 bigram 文本)切片并高亮查询子串;找不到则取开头。"""
    if not text:
        return ""
    text = re.sub(r"\s+", " ", text).strip()
    loc = text.lower().find(query.lower()) if query else -1
    if loc < 0:
        return text[:width] + ("…" if len(text) > width else "")
    half = max((width - len(query)) // 2, 10)
    start = max(loc - half, 0)
    end = min(loc + len(query) + half, len(text))
    out = ("…" if start > 0 else "") + text[start:end] + ("…" if end < len(text) else "")
    return out.replace(text[loc:loc + len(query)],
                       HL_L + text[loc:loc + len(query)] + HL_R, 1)


# ---------------------------------------------------------------- schema

SCHEMA = """
CREATE TABLE IF NOT EXISTS questions(
  id TEXT PRIMARY KEY, title TEXT, detail TEXT,
  answer_count INTEGER, follower_count INTEGER, visit_count INTEGER,
  created INTEGER, updated INTEGER, url TEXT,
  raw TEXT, ingested_at INTEGER);
CREATE TABLE IF NOT EXISTS answers(
  id TEXT PRIMARY KEY, question_id TEXT,
  author TEXT, author_token TEXT,
  voteup_count INTEGER, comment_count INTEGER,
  created INTEGER, updated INTEGER,
  content TEXT, content_html TEXT, url TEXT,
  raw TEXT, ingested_at INTEGER);
CREATE TABLE IF NOT EXISTS articles(
  id TEXT PRIMARY KEY, title TEXT, author TEXT,
  voteup_count INTEGER, comment_count INTEGER,
  created INTEGER, updated INTEGER,
  content TEXT, content_html TEXT, url TEXT,
  raw TEXT, ingested_at INTEGER);
CREATE TABLE IF NOT EXISTS comments(
  id TEXT PRIMARY KEY, target TEXT, parent_id TEXT,
  author TEXT, author_token TEXT, content TEXT,
  like_count INTEGER, created INTEGER, child_count INTEGER,
  raw TEXT, ingested_at INTEGER);
CREATE TABLE IF NOT EXISTS users(
  id TEXT PRIMARY KEY, name TEXT, url_token TEXT,
  headline TEXT, follower_count INTEGER, answer_count INTEGER,
  url TEXT, raw TEXT, ingested_at INTEGER);
CREATE VIRTUAL TABLE IF NOT EXISTS search_index USING fts5(
  kind, ref_id, title, author, content, tokenize='unicode61');
"""

FTS_COLS = {"question": ("questions", "title", "detail"),
            "answer": ("answers", None, "content"),
            "article": ("articles", "title", "content"),
            "comment": ("comments", None, "content"),
            "user": ("users", "name", "headline")}


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def open_db(db_path=DEFAULT_DB) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    return conn


def _upsert(conn: sqlite3.Connection, table: str, row: dict):
    """按 id 幂等 upsert; 空值(None/'')不覆盖已有值。raw/ingested_at 总刷新。"""
    row = {k: v for k, v in row.items()}
    row["ingested_at"] = int(time.time())
    cols = list(row.keys())
    placeholders = ",".join("?" for _ in cols)
    updates = ", ".join(
        f"{c}=CASE WHEN excluded.{c} IS NULL OR excluded.{c}='' "
        f"THEN {table}.{c} ELSE excluded.{c} END"
        for c in cols if c not in ("id", "ingested_at"))
    sql = (f"INSERT INTO {table}({','.join(cols)}) VALUES({placeholders}) "
           f"ON CONFLICT(id) DO UPDATE SET {updates}, ingested_at=excluded.ingested_at")
    conn.execute(sql, [row[c] for c in cols])


def refresh_fts(conn: sqlite3.Connection, kind: str, ref_id: str):
    """以 base 表当前行为准, 重建该实体的 FTS 行(先删后插)。"""
    table, title_col, body_col = FTS_COLS[kind]
    r = conn.execute(f"SELECT * FROM {table} WHERE id=?", (ref_id,)).fetchone()
    if not r:
        return
    cols = [d[1] for d in conn.execute(f"PRAGMA table_info({table})")]
    row = dict(zip(cols, r))
    conn.execute("DELETE FROM search_index WHERE kind=? AND ref_id=?",
                 (kind, ref_id))
    author = row.get("author") or row.get("name") or ""
    conn.execute(
        "INSERT INTO search_index(kind, ref_id, title, author, content) "
        "VALUES(?,?,?,?,?)",
        (kind, ref_id, index_text(row.get(title_col) or ""), index_text(author),
         index_text(row.get(body_col) or "")))


# ---------------------------------------------------------------- ingest

_QID_FROM_URL = re.compile(r"question/(\d+)")
_EM_RE = re.compile(r"</?em>")


def _norm_answer(a):
    """归一化 fast 通道回答结构(feeds.data 的 target 包裹)为扁平 dict。
    浏览器通道的扁平 answers 原样返回。"""
    if isinstance(a, dict) and isinstance(a.get("target"), dict):
        t = a["target"]
        au = t.get("author") or {}
        q = t.get("question") or {}
        return {
            "id": t.get("id"),
            "author": au.get("name"), "author_token": au.get("url_token"),
            "voteup_count": t.get("voteup_count"),
            "comment_count": t.get("comment_count"),
            "created": t.get("created_time") or t.get("created"),
            "updated": t.get("updated_time") or t.get("updated"),
            "content": t.get("content"), "excerpt": t.get("excerpt"),
            "url": t.get("url") or (
                f"https://www.zhihu.com/question/{q.get('id')}/answer/{t.get('id')}"
                if t.get("id") and q.get("id") else None),
        }
    return a


def _ingest_question(conn, d):
    q = d["question"]
    _upsert(conn, "questions", {
        "id": str(q.get("id")), "title": q.get("title"),
        "detail": q.get("detail"),
        "answer_count": _int(q.get("answer_count")),
        "follower_count": _int(q.get("follower_count")),
        "visit_count": _int(q.get("visit_count")),
        "created": _int(q.get("created")), "updated": _int(q.get("updated")),
        "url": q.get("url"), "raw": json.dumps(q, ensure_ascii=False)})
    refresh_fts(conn, "question", str(q.get("id")))
    n = 1
    answers = d.get("answers")
    if not answers:
        # fast 通道: {question, feeds:{data:[{target:{...}}]}}
        answers = [_norm_answer(x) for x in (d.get("feeds") or {}).get("data") or []]
    for a in answers:
        _ingest_answer(conn, a, question_id=str(q.get("id")))
        n += 1
    return n


def _ingest_answer(conn, a, question_id=None):
    aid = str(a.get("id"))
    qid = question_id
    if not qid:
        m = _QID_FROM_URL.search(a.get("url") or "")
        qid = m.group(1) if m else None
    _upsert(conn, "answers", {
        "id": aid, "question_id": qid,
        "author": a.get("author"), "author_token": a.get("author_token"),
        "voteup_count": _int(a.get("voteup_count", a.get("voteup"))),
        "comment_count": _int(a.get("comment_count")),
        "created": _int(a.get("created")), "updated": _int(a.get("updated")),
        "content": a.get("content") or _EM_RE.sub("", a.get("excerpt") or "") or None,
        "content_html": a.get("content_html"),
        "url": a.get("url"), "raw": json.dumps(a, ensure_ascii=False)})
    refresh_fts(conn, "answer", aid)


def _ingest_article(conn, a):
    _upsert(conn, "articles", {
        "id": str(a.get("id")), "title": a.get("title"),
        "author": a.get("author"),
        "voteup_count": _int(a.get("voteup_count", a.get("voteup"))),
        "comment_count": _int(a.get("comment_count")),
        "created": _int(a.get("created")), "updated": _int(a.get("updated")),
        "content": a.get("content") or _EM_RE.sub("", a.get("excerpt") or "") or None,
        "content_html": a.get("content_html"),
        "url": a.get("url"), "raw": json.dumps(a, ensure_ascii=False)})
    refresh_fts(conn, "article", str(a.get("id")))


def _ingest_comment(conn, c, target=None, parent_id=None):
    cid = str(c.get("id"))
    _upsert(conn, "comments", {
        "id": cid, "target": target or c.get("target"),
        "parent_id": parent_id or c.get("parent_id"),
        "author": c.get("author"), "author_token": c.get("author_token"),
        "content": c.get("content"),
        "like_count": _int(c.get("like_count")),
        "created": _int(c.get("created")),
        "child_count": _int(c.get("child_count")),
        "raw": json.dumps(c, ensure_ascii=False)})
    refresh_fts(conn, "comment", cid)


def _ingest_user(conn, u):
    _upsert(conn, "users", {
        "id": str(u.get("id")), "name": u.get("name"),
        "url_token": u.get("url_token"), "headline": u.get("headline"),
        "follower_count": _int(u.get("follower_count")),
        "answer_count": _int(u.get("answer_count")),
        "url": u.get("url"), "raw": json.dumps(u, ensure_ascii=False)})
    refresh_fts(conn, "user", str(u.get("id")))


def detect_type(d: dict) -> str:
    t = d.get("type")
    if t in ("question", "article", "comments", "user_articles",
             "user_followers", "search"):
        return t
    if "question" in d:
        return "question"
    if "article" in d:
        return "article"
    if "comments" in d:
        return "comments"
    if "results" in d and "query" in d:
        return "search"
    if "items" in d and "user" in d:
        return "user_items"
    return "unknown"


def ingest_file(conn, path: Path) -> dict:
    """灌入单个 JSON 文件;返回 {kind: count}。幂等。"""
    with open(path, encoding="utf-8") as fp:
        d = json.load(fp)
    if not isinstance(d, dict):
        return {}
    kind = detect_type(d)
    stats = {}
    if kind == "question":
        stats["question"] = _ingest_question(conn, d)
    elif kind == "article":
        _ingest_article(conn, d["article"])
        stats["article"] = 1
    elif kind == "comments":
        target = d.get("target")
        n = 0
        for c in d.get("comments") or []:
            _ingest_comment(conn, c, target=target)
            n += 1
        for c in d.get("child_comments") or []:
            _ingest_comment(conn, c, target=target,
                            parent_id=c.get("parent_id"))
            n += 1
        stats["comment"] = n
    elif kind == "user_articles":
        for it in d.get("items") or []:
            it = dict(it)
            it.setdefault("author", d.get("user"))
            _ingest_article(conn, it)
        stats["article"] = len(d.get("items") or [])
    elif kind == "user_followers":
        for u in d.get("items") or []:
            _ingest_user(conn, u)
        stats["user"] = len(d.get("items") or [])
    elif kind == "search":
        na = np = 0
        for r in d.get("results") or []:
            if r.get("type") == "answer":
                _ingest_answer(conn, r)
                na += 1
            elif r.get("type") == "article":
                _ingest_article(conn, r)
                np += 1
        stats["answer"], stats["article"] = na, np
    conn.commit()
    return stats


def ingest_dir(conn, out_dir: Path) -> dict:
    total = {}
    for p in sorted(out_dir.glob("*.json")):
        try:
            s = ingest_file(conn, p)
        except (json.JSONDecodeError, KeyError) as e:
            print(f"[skip] {p.name}: {e}", file=sys.stderr)
            continue
        for k, v in s.items():
            total[k] = total.get(k, 0) + v
    return total


# ---------------------------------------------------------------- search

def search(query: str, limit: int = 10, db_path=DEFAULT_DB) -> list:
    """全文检索 → [{kind,id,title,author,voteup,url,snippet,score}]。"""
    match = query_to_match(query)
    if not match:
        return []
    conn = open_db(db_path)
    try:
        rows = conn.execute(
            "SELECT kind, ref_id, bm25(search_index) AS score "
            "FROM search_index WHERE search_index MATCH ? "
            "ORDER BY score LIMIT ?",
            (match, limit * 4)).fetchall()
    except sqlite3.OperationalError as e:
        return [{"error": f"MATCH 查询失败: {e}", "match": match}]
    hits = []
    for kind, ref_id, score in rows:
        table, title_col, body_col = FTS_COLS[kind]
        cols = [d[1] for d in conn.execute(f"PRAGMA table_info({table})")]
        r = conn.execute(f"SELECT * FROM {table} WHERE id=?", (ref_id,)).fetchone()
        if not r:
            continue
        row = dict(zip(cols, r))
        title = row.get(title_col) if title_col else None
        body = row.get(body_col) or ""
        if kind == "answer" and row.get("question_id"):
            qr = conn.execute("SELECT title FROM questions WHERE id=?",
                              (row["question_id"],)).fetchone()
            title = f"回答 · {qr[0]}" if qr else "回答"
            url = (f"https://www.zhihu.com/question/{row['question_id']}"
                   f"/answer/{ref_id}")
        elif kind == "comment":
            title = f"评论 · {row.get('target') or ''}"
            url = ""
        elif kind == "user":
            title = f"用户 · {row.get('name')}"
            url = row.get("url") or ""
            body = row.get("headline") or ""
        else:
            url = row.get("url") or ""
        hits.append({
            "kind": kind, "id": ref_id, "title": title,
            "author": row.get("author") or row.get("name"),
            "voteup": row.get("voteup_count", row.get("follower_count")),
            "url": url, "score": round(score, 4),
            "snippet": make_snippet(body, query)})
        if len(hits) >= limit:
            break
    conn.close()
    return hits


def stats(conn) -> dict:
    out = {}
    for t in ("questions", "answers", "articles", "comments", "users"):
        out[t] = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
    out["search_index"] = conn.execute(
        "SELECT COUNT(*) FROM search_index").fetchone()[0]
    return out


# ---------------------------------------------------------------- CLI

def main():
    ap = argparse.ArgumentParser(description="知乎知识库(SQLite+FTS5)")
    ap.add_argument("--db", default=str(DEFAULT_DB))
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_ing = sub.add_parser("ingest", help="灌入 output JSON(幂等)")
    p_ing.add_argument("dir", nargs="?", default=str(DEFAULT_OUTPUT))
    p_s = sub.add_parser("search", help="全文检索")
    p_s.add_argument("query")
    p_s.add_argument("--limit", type=int, default=10)
    sub.add_parser("stats", help="条目统计")
    args = ap.parse_args()

    conn = open_db(Path(args.db))
    if args.cmd == "ingest":
        total = ingest_dir(conn, Path(args.dir))
        print(f"[ingest] {total}")
        print(f"[stats] {stats(conn)}")
    elif args.cmd == "search":
        for h in search(args.query, args.limit, Path(args.db)):
            if "error" in h:
                print(h["error"])
                break
            print(f"[{h['kind']:8s}] {h['title'] or ''}")
            print(f"  作者:{h['author']} 赞:{h['voteup']} score:{h['score']}")
            print(f"  {h['url']}")
            print(f"  {h['snippet']}")
            print()
    elif args.cmd == "stats":
        print(json.dumps(stats(conn), ensure_ascii=False, indent=1))
    conn.close()


if __name__ == "__main__":
    main()
