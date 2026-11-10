# 相关性分级词表快照 v3 · 2026-11-10

> 来源: corpus_export.py 当前版本(relevance() 函数的词表/正则区)。
> 用途: 为交付物可复现归档——某次导出用的哪个版本词表,以此为准。
> 变更史: v1 误杀67% → v2 降门槛+黑话 → v3 修CJK边界+功能码+型号码。

```python
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

```
