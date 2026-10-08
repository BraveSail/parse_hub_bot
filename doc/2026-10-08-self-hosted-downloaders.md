# 下载器自研：facebook / snapchat / youtube 改走项目自身实现（不再调用 yt-dlp）

日期：2026-10-08
触发：用户要求「把用 ytdlp 二进制下载的平台，从 ytdlp 源码里分别抽出对应平台源码，合并成一个接口，
抛弃调用 ytdlp 二进制下载模式，走项目自身类下载」「**不抄，只移植所需要功能，我的目的是下载器自研，
不依赖第三方工具**，像 bilibili 那样」。

## 背景：谁在用 yt-dlp

分两种形状，缺一不可地扫了两遍才全（只 grep `YtParser` 的继承者会漏掉「兜底引用」）：

| 平台 | 用法 | 改动前 |
| --- | --- | --- |
| **youtube** | 视频 / 音乐**唯一路径** | `parsers/parser/youtube.py::YtbParse(YtParser)` |
| **facebook** | 整平台**唯一路径** | `parsers/parser/facebook.py::FacebookParse(YtParser)` |
| **snapchat** | 整平台**唯一路径** | `parsers/parser/snapchat.py::Snapchatarse(YtParser)` |
| bilibili | 仅 **API 失败时的兜底** | `BiliYtParse(YtParser, register=False)`，主路径早已自研 |

其余 19 个平台（twitter / threads / bilibili 主路径 / pixiv / douyin / tiktok / linux.do /
bangumi / douban / coolapk …）本来就是 `BaseParser` + 项目自研分片下载器，lib 层**零外部进程调用**。

## 各平台的数据来源（均为活体实测）

### facebook —— 页面内嵌明文渐进式直链

- 三种 URL 形态（`/watch/?v=`、`/<user>/videos/`、`/reel/`）匿名抓取都 **HTTP 200，无登录墙**
- 直链在 `<script data-sjs>` 的内嵌 JSON 里：`videoDeliveryLegacyFields.browser_native_sd_url` /
  `browser_native_hd_url` → `video-*.fbcdn.net/*.mp4`
- Range 实测 **HTTP 206**、`content-type: video/mp4`、`accept-ranges: bytes`；整文件下载 4,469,831 字节、文件头 `ftypisom`
- **watch 页与 reel 页不同构**：候选作者在 `creation_story.comet_sections` 的 `actors`（watch/post），
  reel 在 `short_form_video_context.video_owner`。按视频 id 精确选直链（页面里混有"相关视频"，防串号），HD 优先
- DASH-only 的情况**明确抛错**（`仅找到 DASH manifest，未实现 MPD 分片下载`），不半吊子解析 MPD

### snapchat —— `__NEXT_DATA__` 里的明文直链

- spotlight 页匿名 200，`props.pageProps.spotlightFeed.spotlightStories[]` 里按
  `story.storyId.value == <ID>` 选中条目 → `metadata.videoMetadata.contentUrl` = **明文 https**
  （`cf-st.sc-cdn.net/d/<id>.1034.IRZXSOY?mo=…`），无需签名解密
- 注意：`videoMetadata.description` 是 Snapchat 的**固定模板**（同页 8/8 条完全相同），
  **不当正文**；正文取视频级 `metadata.description`
- `viewCount == -1` 表示不可用 → 留空（不编造）

### youtube 视频 —— innertube player API，**client 选型是全部关键**

反爬本质是 **IP 判定**（用户的判断正确），161 上已有专用出口（宿主 `mihomo-yt`，`mixed-port: 1085`，
wireguard 出站；bot 容器 `network_mode: host` ⇒ 容器内直接 `127.0.0.1:1085`）。

拿到干净出口后，**client 的选择判据是「既要返回明文 url，又不要 PO token」—— 两条缺一即废**：

| client | 带明文 url 的流 | 直链实测下载 |
| --- | --- | --- |
| `ANDROID`（#3） | 30 条里**只有 1 条**（itag=18, 640x360 合一） | ✅ 206 / 822 KB/s |
| `ANDROID_VR`（#28） | 27 条**全部** | ❌ **403** |
| `IOS`（#5） | 0 条 | — |
| **`VISIONOS`（#101）** | **27 条全部**（含 2160p/1440p/1080p） | ✅ **206 / 830 KB/s** |

- **`ANDROID_VR` 是本次最大的坑**：它的 `url` 看着最齐（27/27），但 `GVS_PO_TOKEN_POLICY: required=True`
  ⇒ 直链**必然 403**。而 `fetch_video` 先试它、player 请求又"成功"，于是**永远轮不到兜底**，
  症状是"解析全对、下载全 403"。**别再把它加回来当兜底** —— 它只会掩盖真问题。
- `ANDROID` 能下但**只有 360p**（其余 29 条连 `cipher` 字段都没有，自研拿不到）⇒ 不能"都用 android"。
- 两者都对不上，只有 `VISIONOS` 同时满足 ⇒ `CLIENTS = (VISIONOS,)`。
- 请求必须带该 client 的 `User-Agent` + `X-Youtube-Client-Name` + `X-Youtube-Client-Version`，
  否则 HTTP 400。
- 高画质是**音视频分离流** ⇒ 分别下载后用 ffmpeg `-c copy` 合并（itag=18 才是合一档）。
- 顺带纠正一条**错误结论**：中途曾把"下载 403"归因为"WARP 出口被 googlevideo 拉黑"——
  对照组直接推翻了它（同一时刻 yt-dlp 走**同一出口**下载 11.28 MiB 成功、3.6 MiB/s）。
  **"被封/被拉黑"这类结论必须先做同出口对照，否则只是甩锅。**

## 改动

| 文件 | 内容 |
| --- | --- |
| `lib/src/parsehub/provider_api/facebook.py`（新） | 纯函数 `parse_video_html` + 唯一网络入口 `FacebookAPI.get_video`（`utils.http` curl_cffi） |
| `lib/src/parsehub/provider_api/snapchat.py`（新） | `__NEXT_DATA__` → `SnapchatVideo`，`fetch_video` 为唯一网络入口 |
| `lib/src/parsehub/provider_api/youtube_video.py`（新） | innertube player：`VISIONOS` client、`parse_player_response`、`select_streams`（≤1080p 最高画质、同分辨率优先 avc1） |
| `lib/src/parsehub/parsers/parser/{facebook,snapchat,youtube}.py` | 三个 parser 脱离 `YtParser`，改 `BaseParser`；`VideoRef.url` 是明文直链 ⇒ 基类 `_do_download` 走**项目下载器** |
| `lib/src/parsehub/parsers/parser/youtube.py` | 视频/音乐与社区帖子（`/post/<id>`）在同一个 parser 内按 URL 分派；分离流由 `YtbVideoParseResult._download_and_mux` 下两路再 ffmpeg 合并 |
| `lib/src/parsehub/provider_api/ytdlp.py` | 通用 yt-dlp 客户端（`extract_info` / `download_video`），现只剩 bilibili 兜底在用 |
| `lib/test/*` | 新增 `test_facebook.py`(15) / `test_snapchat.py`(19) / `test_youtube_video.py`(25)；fixture 全部从真实响应裁剪 |

## 验证（真实数字）

离线：`uv run pytest test/` → **824 passed, 1 skipped**；`bash scripts/check.sh`（ruff + pylint
--errors-only）干净。

**161 容器内真机端到端**（走 `127.0.0.1:1085`）—— 真解析 + 真下载到文件 + `ffprobe`：

| 用例 | 产物 | ffprobe |
| --- | --- | --- |
| youtube 视频 | **84,426,489 字节 (80.52 MB)** | `h264 1920x1080` + `aac`，duration **213.09s**（= 视频时长）|
| youtube 帖子 | 图片 112,493 字节 | — |
| facebook | **23,526,592 字节 (22.44 MB)** | `h264 720x1280` + `aac`，duration 136.21s |
| snapchat | 583,698 字节 | `h264 480x880` + `aac`，duration 4.64s |

youtube 那条尤其关键：它是**分离流 + ffmpeg `-c copy` mux** 的完整链路产物（1080p 视频轨 + aac 音频轨
都在一个 mp4 里）。容器日志无 error。

## 遗留与注意事项

- **yt-dlp 依赖仍在**：`lib/pyproject.toml` 的 `yt-dlp[default]`，以及 Dockerfile 里的 **deno**
  （那是给 yt-dlp 解 YouTube nsig 用的 JS 运行时）。要摘掉它们，得先决定
  **bilibili 的 yt-dlp 兜底要不要一起去掉** —— 去掉即 100% 无 yt-dlp，代价是 B 站 API 被风控时再无后备。
- **client 版本号会过期**：`youtube_video.py` 里的 `VISIONOS` 常量集中在文件顶部，一旦解析集体失败，
  第一件事是照上游 yt-dlp 的 `INNERTUBE_CLIENTS` 更新（文件里写了链接）。
- **代理不硬编码**：解析与下载都走既有的 `core/platform_config.py::roll_parser_proxy` /
  `roll_downloader_proxy`；**两者必须是同一出口**（googlevideo 直链与请求 IP 绑定）。
- facebook 的 `author_handle` 拿不到（页面不给用户名形式）→ 留空，不编造。
- `/share/v/<token>` 短链的跟随重定向逻辑已实现，但**没有真实 token 可实测**。
