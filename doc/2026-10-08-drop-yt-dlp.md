# 摘掉 yt-dlp：去 bilibili 兜底 + 删依赖与 deno

日期：2026-10-08
触发：用户对「bilibili 的 yt-dlp 兜底要不要一起摘」的回答：**「摘掉」**。

## 背景

facebook / snapchat / youtube 视频已在上一轮改自研（见 `2026-10-08-self-hosted-downloaders.md`）。
剩下的 yt-dlp 引用只有一条：**bilibili 在自身 API 解析失败时的兜底**
（`BiliYtParse(YtParser, register=False)`）。它连着两个模块与两项外部依赖：

```
bilibili.py (ytp_parse / BiliYtParse / BiliYtVideoParseResult)
  → parsers/base/ytdlp.py (YtParser / YtVideoParseResult / YtVideoInfo)
    → provider_api/ytdlp.py (extract_info / download_video，子进程调用 yt-dlp)
  → lib/pyproject.toml 的 yt-dlp[default]
  → Dockerfile 里安装的 deno（yt-dlp 解 YouTube nsig 的 JS 运行时，全仓库只有它用）
```

摘掉即 **100% 无 yt-dlp**。

## 改动

| 文件 | 内容 |
| --- | --- |
| `lib/.../parser/bilibili.py` | 删 `ytp_parse` / `BiliYtParse` / `BiliYtVideoParseResult` 与相关 import；`_do_parse` 只走 `bili_api_parse` |
| `lib/.../parsers/base/ytdlp.py`、`lib/.../provider_api/ytdlp.py` | **删除** |
| `lib/.../parsers/base/__init__.py` | 去掉 `YtParser` 导出 |
| `lib/pyproject.toml` + `uv.lock` | 删 `yt-dlp[default]`；`uv lock` 连带移除 **yt-dlp-ejs / brotli / brotlicffi / mutagen / websockets**（都只是 yt-dlp 的传递依赖，已确认项目未直接使用） |
| `Dockerfile` / `Dockerfile.deploy` | 删 deno 安装与 `DENO_INSTALL` / PATH 条目（**保留 ffmpeg** —— YouTube 1080p 仍需它 mux） |
| `lib/.../types/serialize.py` | "构造不了就不缓存"的**机制保留**，把注释里的例子从 yt-dlp 系类改成"历史案例 + 该判据依然有效" |
| 测试（lib + bot） | `test_result_roundtrip.py` / bot `test_cache_redis.py` 改用**测试自建**的"需要运行期句柄"结果类（比借用生产类更贴切）；`test_platform_metadata.py` / `test_author_metadata.py` 删 yt-dlp 段；`test_youtube_post.py` 的守卫从 patch `YtParser._parse` 改成 patch `fetch_video` |

**顺手修掉一个既有缺陷**：原兜底把失败原因吞成一句 `ParseError("Bilibili 解析失败")` —— 风控 / cookie 失效 / 接口变更全都看不出来。现在把真实原因带出来：

```
Bilibili 解析失败: 由于触发哔哩哔哩安全风控策略，该次访问请求被拒绝。
```

## 验证

- **环境里已无 yt-dlp**：`uv sync --all-groups` 后 `import yt_dlp` → `ModuleNotFoundError`；
  `lib` 全量 **809 passed / 1 skipped**、bot **548 passed**、`scripts/check.sh` 干净
  （能在这个环境下跑通即证明不再依赖它）。
- **镜像**：`docker exec … python -c "import yt_dlp"` → 失败；`command -v deno` 无输出。
- **B 站真机端到端**（161 容器内，走 `ParseService`）：
  解析 `BiliVideoParseResult` 标题『【MV】保加利亚妖王AZIS视频合辑』、作者『冰封.虾子』、
  播放量 **45,818,990**；下载 **7,871,518 字节**（upos-sz-mirrorcosov.bilivideo.com）；
  ffprobe `h264 512x288 + aac`，duration **199.33s**。

## ⚠️ 排查中踩的坑（值得记住）

1. **B 站的 `view/detail` 对匿名请求直接风控**（代码注释里本来就写着）。
   我用 `ParseHub().parse(url)`（**不带 cookie**）做端到端，稳定复现
   `由于触发哔哩哔哩安全风控策略…`，一度误以为"删兜底把 B 站搞坏了"。
   **端到端验证必须走 `ParseService`**（它会 roll cookie），或显式传 cookie。
2. **B 站风控按请求频率判定**：密集探测（每次 parse 打 3 个请求，失败还会重试 ×3）会把自己打进
   412，**冷却数分钟后恢复**（实测同一出口单次请求 `code=0`）。排查这类问题时必须克制探测，
   并用"同进程 A/B"（手动调用 vs parser 调用）而不是靠时间窗口猜。
3. **摘除的风险如实记录**：B 站 API 被风控时**不再有后备路径**（改动前会尝试 yt-dlp —— 它走
   `player` 等**不同端点**与 wbi 签名，是否真能救回未验证）。本次没有"兜底从未被使用"的可靠证据：
   容器日志每次部署 `--force-recreate` 就清空，Redis 缓存里 106 条也**没有任何 B 站条目**（说明近期
   没人分享过 B 站，而非兜底没被用过）。
