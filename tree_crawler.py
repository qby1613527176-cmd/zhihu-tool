#!/usr/bin/env python3
"""tree_crawler.py — 类型学问题树 BFS 深爬
机制: 问题 → feeds cursor 链全量回答 → 回答正文里正则提取链接问题 → 下层 BFS
输出: 新回答(增量,与 corpus.db 去重) + 新问题(下一种子) + 新作者
用法: python3 tree_crawler.py [--seeds N] [--max-pages 40] [--depth 1]"""
import argparse
import json
import re
import sqlite3
import sys
import time
from pathlib import Path
from urllib.parse import urlparse, parse_qs

TOOL_DIR = Path(__file__).resolve().parent
DB = TOOL_DIR / "corpus" / "corpus.db"
OUT = TOOL_DIR / "corpus" / "raw"
sys.path.insert(0, str(TOOL_DIR))
from zhihu_fast import FastClient, ZhihuFastBlocked  # noqa: E402

QLINK_RE = re.compile(r"zhihu\.com/question/(\d{6,})")


def norm_answer(t, qid, qtitle):
    return {
        "type": "answer", "id": str(t.get("id")),
        "title": "", "question_title": qtitle, "question_id": str(qid),
        "author_name": (t.get("author") or {}).get("name", ""),
        "author_token": (t.get("author") or {}).get("url_token", ""),
        "voteup": t.get("voteup_count", 0), "comment_count": t.get("comment_count", 0),
        "url": f"https://www.zhihu.com/question/{qid}/answer/{t.get('id')}",
        "keyword": "树爬", "raw": t,
    }


class TreeCrawler:
    def __init__(self, max_pages=40, interval=6):
        self.c = FastClient(min_interval=interval)
        self.max_pages = max_pages
        self.db = sqlite3.connect(str(DB))
        self.known_answers = {r[0] for r in self.db.execute(
            "SELECT id FROM items WHERE type='answer'")}
        self.known_questions = {r[0] for r in self.db.execute(
            "SELECT DISTINCT question_id FROM items WHERE question_id != ''")}

    def crawl_question(self, qid):
        """沿 cursor 链抓一个问题的全部回答。返回 (新回答list, 链接问题set, 作者set)"""
        new_answers, linked_qs, authors = [], set(), set()
        url = (f"/api/v4/questions/{qid}/feeds?limit=20&offset=0"
               f"&order=default&platform=desktop")
        pages = 0
        while url and pages < self.max_pages:
            path = urlparse(url).path + "?" + urlparse(url).query \
                if url.startswith("http") else url
            d = self.c.get(path)
            items = d.get("data", [])
            if not items:
                break
            for it in items:
                t = it.get("target", it)
                aid = str(t.get("id", ""))
                if not aid or aid == "None":
                    continue
                qtitle = (d.get("question") or {}).get("title", "") \
                    if pages == 0 else ""
                if aid not in self.known_answers:
                    new_answers.append(norm_answer(t, qid, qtitle))
                    self.known_answers.add(aid)
                au = (t.get("author") or {}).get("url_token")
                if au:
                    authors.add(au)
                for m in QLINK_RE.findall(t.get("content", "") or ""):
                    if m not in self.known_questions and m != str(qid):
                        linked_qs.add(m)
            pages += 1
            pg = d.get("paging") or {}
            if pg.get("is_end"):
                break
            url = pg.get("next")
        return new_answers, linked_qs, authors, pages


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=5, help="试航种子数")
    ap.add_argument("--start", type=int, default=0, help="种子起始下标(车道分片)")
    ap.add_argument("--end", type=int, default=0, help="种子结束下标(0=全部)")
    ap.add_argument("--out", default="tree_pilot.jsonl")
    ap.add_argument("--state", default="tree_done.json", help="断点状态文件")
    ap.add_argument("--max-pages", type=int, default=40)
    ap.add_argument("--seeds-file", default="seed_questions.json", help="种子问题文件")
    ap.add_argument("--interval", type=float, default=6, help="请求间隔秒(多车道时用 12+)")
    args = ap.parse_args()

    all_seeds = json.loads((OUT / args.seeds_file).read_text())
    end = args.end if args.end else len(all_seeds)
    seeds = all_seeds[args.start:end]
    state_path = OUT / args.state
    done = set()
    if state_path.exists():
        done = set(json.loads(state_path.read_text()))
    seeds = [s for s in seeds if s not in done]
    print(f"[tree] 本车道 {len(seeds)} 个种子待爬(已跳过 {len(done)} 个已完成)")
    tc = TreeCrawler(max_pages=args.max_pages, interval=args.interval)
    out_f = open(OUT / args.out, "a", encoding="utf-8")
    total_new, all_links, all_authors = 0, set(), set()
    for i, qid in enumerate(seeds, 1):
        try:
            ans, links, authors, pages = tc.crawl_question(qid)
            for a in ans:
                out_f.write(json.dumps(a, ensure_ascii=False) + "\n")
            out_f.flush()
            total_new += len(ans)
            all_links |= links
            all_authors |= authors
            done.add(qid)
            state_path.write_text(json.dumps(sorted(done)))
            if i % 10 == 0:
                print(f"[{i}/{len(seeds)}] Q{qid}: {pages}页 新回答{len(ans)} "
                      f"链接问题{len(links)} 作者{len(authors)}", flush=True)
        except ZhihuFastBlocked as e:
            print(f"[{i}] Q{qid} 风控: {e} —— 停", flush=True)
            break
        except Exception as e:
            print(f"[{i}] Q{qid} 异常: {type(e).__name__} {str(e)[:60]}", flush=True)
            time.sleep(5)
    out_f.close()
    print(f"\n[tree] 本轮: 新回答 {total_new}, 链接新问题 {len(all_links)}, 新作者 {len(all_authors)}")


if __name__ == "__main__":
    main()
