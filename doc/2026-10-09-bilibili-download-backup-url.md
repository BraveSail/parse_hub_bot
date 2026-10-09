# bilibili 下载：传输中断误判 + 备用地址回退

## 症状

用户贴 `https://www.bilibili.com/video/BV1q3Z9BoE9G/` → bot 回「下载错误」。
线上日志：

```
WARNING parsehub.utils.downloader:_download_part:382
  分片请求被拒: url=https://upos-hz-mirrorakam.akamaized.net/...-1-192.mp4?... Range=bytes=7051084-14102167
  status=206 attempt=1/4 rate_limited=False
    响应头 = {..., 'content-range': 'bytes 7051084-14102167/14102168', 'content-length': '7051084', ...}
DownloadError: 下载错误: 下载失败: 下载错误: 分片下载失败: HTTP 206
```

`HTTP 206` 是**成功码**，却被当成失败；且 `attempt=1/4` —— 没重试就走了。

## 根因（已取证，两条独立缺陷叠加）

### 缺陷 1：传输中断被当成「状态码错误」

复现取的原始异常（161 容器内，用日志里那条真实 URL）：

```
curl_cffi.requests.exceptions.IncompleteRead: Failed to perform, curl: (18)
  end of response with 4608156 bytes missing
Traceback ... downloader.py:365  async for chunk in response.aiter_content(...)
           ... downloader.py:390  raise DownloadError(f"分片下载失败: HTTP {status}") from e
```

继承链（容器内实测）：

```
IncompleteRead → HTTPError → RequestException
parsehub/utils/http.py:  HTTPStatusError = curl_cffi.requests.exceptions.HTTPError
```

即 **`IncompleteRead` 是 `HTTPStatusError` 的子类**，而 `_download_part` 的
`except http.HTTPStatusError` 排在传输类 except **之前**，于是：

- 传输中断落进状态码分支 → 用已解析出的 `status_code`（206）拼错误信息；
- `_is_retryable_status(206)` 为假 → `attempt == 0` 就直接 `raise`，**不重试**。

同一缺陷在 `run()`（第 151 行）也有：`except http.HTTPStatusError` 同样会吞掉传输中断。

### 缺陷 2：该 CDN 副本有一处不可读

同一 URL、只变 Range 一个变量（curl 命令行，每档一次）：

| Range | 结果 |
| --- | --- |
| `0-1023` / `0-1048575` / `0-7051083` | 206 完整 |
| `7051084-14102167`（生产分片） | 206，**只给 2442928 字节，断在文件偏移 9494012** |
| `0-14102167`（全量） | 206，**同样断在 9494012** |
| `9494012-9495011` | **HTTP 503** |
| `12000000-14102167`（尾部） | 206 完整 |

- 同一 Range 连打 3 次，三次都断在**同一字节** → 稳定，非随机抖动。
- `curl` 命令行与 curl_cffi 结果一致（退出码 18、落盘字节数相同）→ 与本项目代码、并发、代理无关。
- 播放签名与 host 绑定（`bilibili.py` 注释已记载：改域名会 403/超时），所以那份地址就是下不完。

结论：**缺陷 2 归属 CDN 侧，我们修不了「让它能下」；能修的是别把整次下载钉死在一条地址上。**

## 方案（用户选定「2」）

1. 修缺陷 1：传输中断走**网络错误**分支，可重试，且错误信息给真实原因，不再出现「HTTP 206」。
2. 修缺陷 2 的**影响**：主地址下载失败时，换 **`backup_url`** 重试整个下载。

不使用「分片细分绕开坏点」——本质是拿字节级补丁换一次成功，换个坏点又会失败；而备用地址是
B 站**自己给的另一份副本**，换过去是合法的规避。

### 约束遵守

- **不增加请求数**：`backup_url` 就在**同一次** `playurl` 响应的 `data.durl[0]` 里，
  parser 只是把它从响应里读出来，没有新调用（符合项目「一次请求是核心架构约束」）。
- **不改写域名**：备用地址**原样使用**（签名与 host 绑定）。
- **不引入新依赖**：不重加 yt-dlp。

## 阶段

### phase0 — 把两个缺陷固化成可离线复现的测试

产物：`lib/test/test_downloader.py` 新增用例 + 测试服务器支持「声明 Content-Length 但少发字节后断连」。

- 服务器侧加 `truncate_at: int | None`：`do_GET` 写到该偏移即 `self.wfile.close()` / 提前截断，
  模拟 curl 18。
- 用例 A：分片响应中途断流 → 断言**错误信息不含 "206"**、且**重试确实发生过**（`attempt` 计数 > 1）。
- 用例 B：`backup_urls` 给一个能下完的地址 → 断言最终文件字节正确、且备用地址**被请求过**。

验证：先跑这两个用例，**应失败**（证明测的是当前缺陷）；记录失败输出。

### phase1 — 传输类异常不再被误判

产物：`lib/src/parsehub/utils/http.py`、`lib/src/parsehub/utils/downloader.py`

- `http.py`：补 `IncompleteRead = _curl_errors.IncompleteRead` 别名，并加进「传输类」元组。
- `downloader.py`：在 `_download_part` 与 `run` 中让**传输类**（含 `IncompleteRead`）先于
  `HTTPStatusError` 命中；传输类按网络错误处理（可重试）。
- 错误信息带上真实原因（`curl: (18) ... bytes missing`），不再拼 `status_code`。

验证：phase0 用例 A 通过；`pytest lib/test/test_downloader.py -q` 全绿。

### phase2 — 备用地址：类型 → 下载器

产物：`lib/src/parsehub/types/media_ref.py`、`lib/src/parsehub/utils/downloader.py`、
`lib/src/parsehub/types/result.py`

- `MediaRef` 加 `backup_urls: tuple[str, ...] = ()`（在 `MediaRef` 上，非 `VideoRef` 专属：
  这类 CDN 多副本是通用现象）。
- `SegmentDownloader` 接受多个地址：主地址失败 → 换下一个**整体重下**（不逐分片混源，
  避免一份文件来自多副本）。换地址后临时目录重开、进度重置。
- `download()` 与 `result._do_download` 透传 `media.backup_urls`。

验证：phase0 用例 B 通过；单地址路径行为不变（现有 4 个用例仍绿）。

### phase3 — bilibili parser 填充备用地址

产物：`lib/src/parsehub/parsers/parser/bilibili.py`

- 从 `video_playurl["data"]["durl"][0]` 读 `backup_url`（**同一次响应**），原样填入
  `VideoRef.backup_urls`；缺字段时为空 tuple（不影响现状）。

验证：新增 parser 用例（mock 响应含 backup_url）断言字段被填充且**原样**（未改写域名）。

### phase4 — 全量校验与真机验证

产物：无新代码；证据记录进 `doc/`

- `lib/test` + bot `test/` 全量跑，零回归。
- `ruff` + `pylint --errors-only`。
- 反向验证：把 phase1 的分类改动临时回退 → 用例 A 变红。
- 真机（161 容器）：等 B 站风控冷却后，用**同一条 BV** 跑一次真实解析 + 下载，
  确认走备用地址能下完（若仍失败，如实记为「备用地址同样坏，缺陷 2 无法规避」）。

## 待定 / 风险

- **风控冷却**：本轮探测过密触发了 `412 触发安全风控策略`，phase4 必须等冷却，期间不得再打 B 站。
- 备用地址是否**同样**断在 9494012 未验证 —— 若同样，方案 2 对其无效（如实报告，转方案 1 兜底）。
- `_download_part` 的 `except DownloadError` 分支只重试 `DownloadError`；换地址逻辑要放在
  **更外层**（整个下载为单位），避免与分片重试语义纠缠。

---

## 实施结果（2026-10-09 收尾）

### phase0 ✅
`lib/test/test_downloader.py`：测试服务器加 `truncate_at`（声明完整 Content-Length 但写到
固定偏移就断连）。两个新用例先跑**都失败**，第一个失败信息逐字复现线上那条
`下载错误: 分片下载失败: HTTP 206` —— 离线复现成立。

### phase1 ✅
- `http.py`：补 `IncompleteRead` 别名（`getattr` 兜底，旧版 curl_cffi 不会 ImportError）
  与 `TRANSPORT_ERRORS` 元组，注释写明它继承 `HTTPStatusError` 这一陷阱。
- `downloader.py`：`_download_part` 与 `run`/新 `_download_current_url` 都让传输类**先**命中，
  可重试，错误信息用 `_describe_transport_error()` 给真实原因。
- 用例 A 通过。

### phase2 ✅
- `media_ref.py`：`MediaRef.backup_urls: tuple[str, ...] = ()`。
- `downloader.py`：`SegmentDownloader` 接受 `backup_urls`，`url` 改为**当前候选地址的 property**；
  `run()` 按地址遍历（同一地址内先跑完重试预算，再换下一个**整体重下**，不逐分片混源）。
- `result.py`：`download()` 透传 `getattr(media, "backup_urls", ())`。
- 用例 B 通过。

### phase3 ✅
- `bilibili.py`：从**同一次** `playurl` 响应的 `durl.backup_url` 读备用地址，原样填入
  `VideoRef.backup_urls`（不改写域名，签名与 host 绑定）。
- 新增 3 个 parser 用例（填充 / 原样保留 / 缺字段为空）+ 现有「首选仍是 `durl.url`」用例全绿。

### phase4 ✅
- lib 全量 **872 passed**（零回归）；bot 侧 39 个收集错误经 git stash 对照确认是**环境缺配置**
  导致（缺 `bot_token`/`api_id`），与本次改动无关。
- 反向验证：临时移除传输类分支 → 用例 A 立刻变红并复现 `HTTP 206`；恢复后 6 passed。
- `bash scripts/check.sh`（ruff + pylint --errors-only）全绿。
- 提交 `c8fbf41`，push + 部署 161（就绪 4s）。
- **真机验证**（B 站风控未冷却，故不去打 B 站）：在**生产容器 + 已部署代码**里，主地址用日志中
  那条**真实坏 CDN URL**，备用地址用宿主上可控的正常服务 →
  下载成功 **3143775 字节**且与源**逐字节一致**；备用地址服务访问日志记下
  `HEAD /payload.bin` + `GET /payload.bin` 各一次（200），证明请求确实换到了备用地址。
- 顺带修一处契约问题：`to_dict()` 会带上 `backup_urls`，导致 2 个既有序列化用例失败 ——
  该字段是**下载策略细节**（不该进缓存/对外接口），改为 `_media_to_dict()` 序列化时剔除。

### 仍未验（如实记录）
`durl.backup_url` 那份副本是否**同样**坏在 9494012 —— 需等 B 站风控冷却后用同一条 BV 验证。
若同样坏，则此缺陷在源侧无解（方案 2 的兜底仍有效：换到别的副本/备用 CDN）。
