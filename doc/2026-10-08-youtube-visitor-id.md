# YouTube `LOGIN_REQUIRED`：真正缺的是 `X-Goog-Visitor-Id`

日期：2026-10-08
触发：用户报 `https://www.youtube.com/watch?v=jTbsEsYSnpM` 解析失败
（`VISIONOS: LOGIN_REQUIRED: Sign in to confirm you're not a bot`），
追问「全要登录？到底走没走 warp？」→「下载最新的 yt apk 解包查看请求逻辑」→「再试下不用代理」。

## 结论一句话

**player 请求缺 `X-Goog-Visitor-Id` 头**。补上它，同一条视频从「零条流」变成「23 条明文流 / 1080p」。
**代理（WARP）与这个头缺一不可** —— 161 直连时带上它仍被拦，所以 WARP 必须保留。

## 一、先排除的问题

**① 代理走着没？走着了。**
`roll_parser_proxy('youtube')` / `roll_downloader_proxy('youtube')` 各 roll 5 次全是
`socks5h://127.0.0.1:1085`；该端口是 mihomo-yt（出站 `wg` = Cloudflare WARP，
GLOBAL/PROXY 组 `now=wg`）；走它出口是 `104.28.211.105`（Cloudflare，Tokyo），
直连是 161 自身。**归属地未漂**（技能里记的「日本」仍然成立）。

**② 不是「全要登录」，是按视频 + 按 client 判定。**
同一出口同一时刻：`dQw4w9WgXcQ` 的 VISIONOS 是 `OK`（27 条流 / 2160p），
`jTbsEsYSnpM` 的 VISIONOS 是 `LOGIN_REQUIRED`。

**③ APK 解包（YouTube 21.40.161，APKPure 下载，154MB，`full_size` 校验一致）** ——
`bgag` 枚举 = innertube clientName 全表（109 项）：`WEB=1, MWEB=2, ANDROID=3, IOS=5,
TVHTML5=7, TVHTML5_SIMPLY=75, ANDROID_VR=28, VISIONOS=101`；`akwj` 负责设
`X-Youtube-Client-Name`/`-Version` 头；`akwh` 构造 `InnertubeContext$ClientInfo`
（clientName 取自枚举、版本动态取、`osName/osVersion/androidSdkVersion` 取 `Build.*`、
时区取系统）；`innertube/attestation/KeyAttestationManager` 用 **AndroidKeyStore 生成 EC
密钥对 + attestationChallenge** 做硬件密钥证明，PO token 走
`com.google.android.gms.potokens.internal.IPoTokensService`（GMS）。
⇒ **设备侧的 attestation 服务器端复现不了 —— 但这次根本不需要它**（见下）。

**④ client 矩阵（20 个 client，报障视频）**：只有 `ANDROID` 给出 `OK`，且**只 1 条**
明文流 `itag=18`（640x360 合一档，直链 206 可下）；`IOS` 是 `OK` 但 20 条流**既无 `url`
也无 `cipher`**（空壳）；其余全 `LOGIN_REQUIRED`/`UNPLAYABLE`。
ANDROID 版本 21.26.364 与 21.40.161 结果相同 ⇒ 版本不是变量。
（这条只解释了「ANDROID 能兜 360p」，**不是根因**。）

## 二、根因：单变量对照

同视频、同出口、同 client，**只变一样东西**：

| 条件 | 结果 |
| --- | --- |
| 现状（无 visitor 头） | `LOGIN_REQUIRED`，0 流 |
| **+ `X-Goog-Visitor-Id`** | **`OK`，23 条明文流，最高 1080p** |
| + 会话 cookie（网页请求建立） | `OK` 1080p（**非必需**） |
| + `playbackContext.signatureTimestamp` | `OK` 1080p（**非必需**） |

**yt-dlp 的实证**：装最新 yt-dlp（2026.08.19）+ `bgutil-ytdlp-pot-provider`，
`--print-traffic` 抓它的真实请求 —— 它用的**就是 visionos client**（`X-Youtube-Client-Name: 101`
/ `Client-Version: 1.02`），请求里**有** `X-Goog-Visitor-Id`，而且**全程没有生成 PO token**
（日志无 `Generating a ... PO Token`）就拿到了 1080p。

**代理是否必需**（用户要求「再试下不用代理」）：

| 视频 | 直连（带 visitor 头） | WARP（带 visitor 头） |
| --- | --- | --- |
| jTbsEsYSnpM（报障） | ❌ LOGIN_REQUIRED | ✅ OK 1080p |
| dQw4w9WgXcQ | ✅ OK 2160p | ✅ OK 2160p |
| jNQXAC9IVRw | ❌ LOGIN_REQUIRED | ✅ OK 240p |

⇒ 直连不行。**之前的失败不是代理的问题，是缺这个头**；换掉代理也解决不了。

## 三、visitorData 的正确来源（踩过一个坑）

⚠️ **不要抓 `/watch` 页 HTML 提 `VISITOR_DATA`**：实测该页会返回 **HTTP 429**
（连打几次就限流），一失败就退化成 `LOGIN_REQUIRED` —— 这正是诊断中「visitorData
时有时无」的原因，**不能作为生产实现**。

✅ **用 innertube 轻量接口**：`POST youtubei/v1/guide` →
`responseContext.visitorData`。稳定、不需要身份、不会被 bot 检查拦。
（`player` 响应的 `responseContext.visitorData` 也有，但 guide 不用先成功请求视频。）

**可跨视频复用**：同一个 visitor 连用 3 条视频（含报障那条）全部 `OK` ⇒ 进程内缓存，
一个 TTL 内只取一次，不做「每视频一次额外请求」。

## 四、改动

`lib/src/parsehub/provider_api/youtube_video.py`：

- `GUIDE_URL` + `_fetch_visitor_data()`：取 visitorData，任何失败返回空串
- `_get_visitor_data()`：进程内缓存（TTL 1 小时）
- `visitor_data_from_response()`：纯函数，从 `responseContext.visitorData` 取
- `_request_player(..., visitor=)`：写入 `X-Goog-Visitor-Id` 头 **与**
  `context.client.visitorData`（与 yt-dlp 对齐）
- `fetch_video()`：先取 visitor 再请求；**取不到就降级**（回到旧行为，不比现在更差）
- **代理配置未动**（`platform_config.yaml` 里 youtube 仍指 `127.0.0.1:1085`）

## 五、验证

**离线**：`lib/test/test_youtube_video.py` 新增 12 例 —— 响应形态容错、header 真的进了
player 请求、**缓存跨视频只取一次**、guide 与 player 走同一出口、降级路径。
lib 全量 **854 passed**，ruff 与 `scripts/check.sh` 干净。
**反向验证**：去掉 header 注入 → 核心用例失败。

**真机**（部署 `3ca11d3` + `61706aa` 后，161 容器，生产入口 `ParseService`）：

```
报障视频: https://www.youtube.com/watch?v=jTbsEsYSnpM
  ✅ 解析成功 YtbVideoParseResult
     标题='TVアニメ『涼宮ハルヒの憂鬱』ノンクレジットED】「止マレ！」｜TVアニメ20周年記念 参加型企画…'
     作者='KADOKAWAanime'
  ✅ 下载完成: 1 个文件  29.8MB  视频=h264,1920,1080  音频=aac     ← ffprobe 读真实产物
```

## 六、第二个坑：下载器单连接路径必须带 Range 头

visitor 修好后解析通过，**下载仍失败**：`curl: (56) Connection closed abruptly`。
分层定位：**视频流 28.7MB ✅**（> 10MB ⇒ 走 4 分片，每片独立重试）、
**音频流 1.04MB ❌**（< `min_split_size` ⇒ 走**单连接**路径，那个路径**不带 Range 头**）。

那条 URL 的 query 里有 **`rqh=1`**（googlevideo 的"要求 Range 头"标记）。实测该 URL：

| 请求 | 结果 |
| --- | --- |
| 不带 Range（原实现） | ❌ `curl: (56) Connection closed abruptly`（2/2 稳定复现）|
| `Range: bytes=0-` | ✅ 206，完整 1086442 字节（2/2）|
| 项目下载器 + 该头 | ✅ 完整 |

⇒ **整段下载也要发 `Range: bytes=0-`**（`bytes=0-` 对支持 Range 的服务器等价整段、
对不支持的服务器会被忽略，两边都安全）。改 `_download_single`。

⚠️ 两个既有下载器测试**钉的正是旧行为**（断言单连接路径不带 Range）——这类测试是在
「记录现状」而不是「记录契约」，改行为时要一并更新（同时给测试服务器补上**开放式 Range**
的解析，它原本连 `bytes=0-` 都解析不了，会在服务端抛异常、客户端只看到空响应）。

⚠️ 探针踩坑：验证脚本里下载**必须传与解析同一个 proxy** —— googlevideo 直链带
`&ip=<解析时的出口 IP>`，直连下载必 403，容易误判成"代码坏了"。
