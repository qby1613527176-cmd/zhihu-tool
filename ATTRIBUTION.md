# 致谢与来源 (Attribution)

本项目在研究与实现过程中参考或内嵌了以下开源工作：

## 内嵌代码 (vendored)
- **`vendor/py/`** — 来自 [zhihulite/zhihu_zse_ck](https://github.com/zhihulite/zhihu_zse_ck)。
  `__zse_ck` 挑战 Cookie 的**纯 Python 静态铸造**实现（环境位图 w4=0x4000 判据、
  record93 结构、SM4 变体加密），是其「纯算术铸造、零 JS 执行」路线的完整复刻。
  本项目以其库形式直接引用（`derive_session.py` 链路），权利归原作者所有。

## 算法参考
- **RSSHub** `lib/utils/zhihu.ts` — `x-zse-96` v3 签名算法（`101_3_3.0` 常量、
  SM4-like 密码、定制 base64 字母表、fixArr XOR 42），本项目的 `zhihu_fast.py`
  签名实现为其逐行 Python 移植。

## 调研参考（research/ 报告中引用）
- **MediaCrawler**（70k★，架构范式对标）
- **zhihu-proxy**（zse_ck WASM PoW 的 Node 沙箱求解路线）
- **Starzzb/zhihu-zse96-v2-python**、**zhihu-plus-plus**（v4 签名变体常量）

## 合规声明
本工具仅供学习研究。使用者须遵守知乎服务条款与 robots 协议、所在司法辖区法律，
控制请求频率，不得用于商业抓取、侵犯隐私或任何违法违规用途。数据版权归原内容作者。
