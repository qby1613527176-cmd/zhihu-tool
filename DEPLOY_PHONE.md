# 手机 Linux 容器部署指南（Termux / proot-distro）

> 目标：在手机上的 Linux 容器里跑 `zhihu_tool` 纯 HTTP 通道，并被 AI 代理（OpenClaw/其他 MCP 客户端）调用。
> 关键：**无需浏览器**——签名纯 Python 计算，cookie 从 PC 浏览器档案导出一次，拷贝到手机。

## 架构

```
PC 端(一次性):  浏览器扫码登录 → 导出 cookies.json ──┐
                                                    │ 拷贝(scp/微信文件/云盘)
手机容器(日常):  python3 fetch.py --fast ...        │
                 python3 mcp_server.py   ← AI 代理调用(MCP stdio)
                 (依赖仅 python3 + httpx,均为纯 pip 安装,ARM64 无障碍)
```

## 一、Termux（直接 Python）

```bash
pkg update && pkg install python -y
pip install httpx            # 唯一依赖
# 拷贝 zhihu_tool 目录到手机(需要: zhihu_fast.py fetch.py mcp_server.py vendor/py/ cookies.json)
cd zhihu_tool
python3 fetch.py bootstrap                    # 匿名自举(免PC,零浏览器)
python3 fetch.py --fast question 19550225     # 冒烟
```

## 二、proot-distro（Ubuntu/Debian 容器）

```bash
pkg install proot-distro -y
proot-distro install debian && proot-distro login debian
apt update && apt install -y python3 python3-httpx   # 或 pip install httpx
# 拷贝工具文件同上
```

## 三、cookie 生命周期

| cookie | 寿命 | 过期怎么办 |
|---|---|---|
| `z_c0`(登录) | 数月 | PC 重跑 `login.py qr` 扫码,重新导出 |
| `d_c0`(设备) | 长期 | 同上 |
| `__zse_ck`(挑战) | 数天轮换 | ① PC 重跑任意浏览器 fetch 自动刷新→重导 ② Node 求解器见下 |

### __zse_ck 自助续期（2026-11-06 攻克 ✅）

**`fetch.py bootstrap` 零浏览器零 JS 执行铸 cookie 罐**（实测全新环境 question/feeds/user 全 200）：
抓挑战页 → 抽内嵌 wasm → 零执行推常量（rk/alphabet/xreg/mask/iv）→ 真机位图 profile（w4=0x4000）→ 纯算术铸 __zse_ck。
源自 zhihulite/zhihu_zse_ck（vendor/py/ 保留原样）。关键实锤：PoW 前 48 hex 是 12×16 位环境位图，**w4=0x4000 是服务端判据**；沙箱假环境被位图出卖（沙箱实测 w4=0x5C36+ → unhuman/40362）。build 轮换快（小时级）无妨——管线按当次 build 现推常量。

```bash
python3 fetch.py bootstrap   # 从零铸匿名罐: 问题元数据/3回答/用户信息
```

**校验模型（实测）**：登录会话信任在 z_c0+老 d_c0+会话历史，__zse_ck 轮换免疫；匿名会话信任=纯算铸的 __zse_ck（bootstrap 负责，40362 时 FastClient 自动重铸重试）。

**导出命令（PC 端）**：`cd zhihu_tool && python3 -c "
import asyncio,sys,json; sys.path.insert(0,'.')
from zhihu import ZhihuSession
async def m():
    async with ZhihuSession(min_interval=1) as z:
        cs={c['name']:c['value'] for c in await z.ctx.cookies('https://www.zhihu.com')}
        json.dump(cs, open('cookies.json','w'), ensure_ascii=False, indent=1)
        print('导出', len(cs), '个')
asyncio.run(m())"`

**UA 红线**：`__zse_ck` 与 UA 绑定。`zhihu_fast.py` 默认 UA（Chrome/150 Windows）必须与导出 cookie 时的浏览器会话一致。Chrome 大版本升级后要同步改 `zhihu_fast.py` 的默认 UA（和 `zhihu.py`）。

## 四、AI 调用（MCP stdio）

```bash
python3 /path/zhihu_tool/mcp_server.py     # JSON-RPC 2.0 over stdio
```

工具：`zhihu_question` / `zhihu_question_feeds` / `zhihu_search` / `zhihu_user` / `zhihu_user_answers` / `zhihu_status`
OpenClaw 注册：`mcp.servers` 加 `{ "command": "python3", "args": ["/path/zhihu_tool/mcp_server.py"] }`

## 五、风控纪律（手机端同守）

- 高斯随机间隔（已内建）；日预算 3000（FastClient.daily_budget，可调）
- 40362/429 → 自动指数退避（已内建）；验证码/跳登录 → 硬停（**不要**自动重试）
- 手机流量 IP=住宅移动网络，天然优于机房 IP——但别因此提速
- **新鲜匿名 d_c0 直打 API = 40362**（实测）：新设备无浏览历史就是机器人画像，必须先用带历史的 cookie 罐
