# E4 · Markdown 导出器

> 2026-11-06 · 代码: exporter.py(主代理亲写,子代理超时无产出) + hydrate.py(全文补水)

## 关键发现（验收中实测）

- harvester 的 feeds 抓取只拿到 **excerpt 摘要**（feeds API 截断，content_need_truncated=true）
- **全文补水通道**：`/api/v4/answers/{aid}?include=content` 实测返回全文（10716 字符 vs 摘要 204）
- `hydrate.py`：断点续补，逐条落库，风控熔断硬停；小样本 5/5 全通，全量 994 条后台跑

## 导出器

`python3 exporter.py question 19550225 [--top N]` → output/export/q{id}.md
HTML→MD 转换覆盖: p/br/strong/em/a/img/blockquote/code/pre/h1-6/ul/ol/hr
标题+元信息头+按赞排序+作者/赞数/时间/原文链接+正文

## 质量抽查

元信息渲染正确；正文依赖补水完成度（未补水的回答只有 excerpt）。先 hydrate 再 export。
