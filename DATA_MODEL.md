# DATA_MODEL.md — 数据库契约(一页)

> 分库是按「生命周期 / 写者 / 再生性 / 备份价值」的刻意隔离，不是散漫。
> 合库 = 收割事故炸掉全部资产 + 锁竞争。本页是各库的唯一权威清单。

| 数据库 | 内容 | 写者 | 读者 | 可重建性 | 备份价值 |
|---|---|---|---|---|---|
| `harvest.sqlite3` | 问题全量回答收割（answers+pages+state 断点） | harvester.py / comment_harvester.py(同库) | exporter.py | ✅ 可重抓重建 | 中 |
| `corpus/corpus.db` | **语料总库**(11万+条目: 全波次合并+门派分类+全文) | merge_corpus.py / corpus_hydrate.py / tree_crawler.py | corpus_export.py / kb.py | ❌ **不可重建(1GB 心血)** | **极高** |
| `kb.sqlite3` | FTS5 全文检索索引(语料的搜索镜像) | kb.py(ingest) | kb.py(search) / MCP | ✅ 从 corpus.db 重建 | 低(可再生) |
| `watch/watch.db` | 情报监控快照(关键词定时盯梢) | watch.py | watch.py | ✅ 可重采 | 低 |
| `canary/canary.db` | 风控姿态指纹(build/错误码/端点矩阵) | canary.py | canary.py | ✅ 可重测 | 低 |

## 铁律
1. **corpus.db 永不推公网**（含 UGC 全文，.gitignore 物理隔离）
2. 收割器只写自己的库，不碰 corpus.db（只有 merge_corpus.py 能写总库）
3. 再生性决定备份优先级：corpus.db > harvest.sqlite3 > 其余
