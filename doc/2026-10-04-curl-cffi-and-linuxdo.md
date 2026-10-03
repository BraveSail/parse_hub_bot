# 库内 HTTP 客户端全部换成 curl_cffi（chrome150 指纹）+ 接入 linux.do

用户指令（2026-10-04）：
1. 「把所有的请求都换成 curlcffi，指纹选最新版 chrome，删掉原请求方法」
2. （前一轮遗留）「https://linux.do/t/topic/2979226 测试接入」

## phase0 — 取证

- 库里 **22 个文件用 httpx**（`src/parsehub/{provider_api,parsers,utils}`），全部 `httpx.AsyncClient`（61 处），
  无同步 `Client` / `requests` / `aiohttp`；**bot 侧零 HTTP 客户端**（改动只在库侧）。
- 用到的能力：`raise_for_status`(47)、异常 15 处（`HTTPError`/`HTTPStatusError`/`TimeoutException`/
  `NetworkError`/`RemoteProtocolError`/`ReadError`/`RequestError`/`ReadTimeout`/`ConnectTimeout`）、
  `follow_redirects`(18)、`proxy`(70)、`.stream()`+`iter_bytes()`(2)、类型注解 `Proxy`/`Timeout`/`Limits`/`Headers`/`Cookies`。
- **curl_cffi 0.16.3 实测**：最新指纹 `chrome150`（= `DEFAULT_CHROME`）；`AsyncSession` 支持
  `impersonate`/`proxy`/`proxies`/`cookies`/`timeout`/`allow_redirects`；`Response` 有
  `status_code/json()/text/content/headers/url/raise_for_status()`（抛 `HTTPError` 带 `.response`）；
  **没有** `aclose()` / `is_closed` / `is_error`；`aiter_content()` 是异步生成器；`chunk_size` 被忽略。
- **决定性对照（linux.do）**：同一份 cookie 下 `httpx → 403`、`curl_cffi → 200`、`curl_cffi 无 cookie → 404`。

## phase1 — 封装层 `lib/src/parsehub/utils/http.py`

一个薄壳，保持原有调用形态与异常名：`IMPERSONATE = "chrome150"`、`AsyncClient`（构造参数与 httpx 对齐、
`proxy` → `proxies={"all": …}`、`limits.max_connections` → `max_clients`）、异常别名、类型占位
（`Proxy`/`Timeout`/`Limits`/`Headers`/`Cookies`）。

**补齐 curl_cffi 缺的能力**：`aclose()`（只有同步 `close()`）、`is_closed`、`__aexit__`；
`follow_redirects` 在**会话级与请求级**都翻译成 `allow_redirects`（覆盖 `request()`，因为 `get/post/head` 都走它）。

验证：离线脚本跑通 GET/json/raise_for_status/流式/HEAD/重定向语义/cookie/代理构造，以及真实 linux.do 200。

## phase2 — 下载器（`utils/downloader.py`）

`client.stream(...)` + `iter_bytes()` → `stream=True` + `aiter_content()`（两处：单请求与分片）。
真实验证：pixiv 3 张 jpeg（2.57 MB）与 **bilibili 视频 187 MB**（必然走分片）下载成功、格式嗅探正确。

## phase3 — 批量替换 22 个文件

`import httpx` → `from ...utils import http`（一度用 `as httpx` 别名，**最终改名成 `http.`**，
避免代码里留下让人误解的 httpx 字样）：90 处调用点机械替换，54 个模块导入冒烟通过。

## phase4 — 依赖

库 `pyproject` 删 `httpx[socks]`、加 `curl_cffi>=0.16.3`；bot `pyproject` 删 `httpx[socks]`。
`uv lock` 重生成。**生产镜像验证**：`curl_cffi 0.16.3` 在、`import httpx` 报 ImportError
（httpx 仅作为 dev 组 `easy-ai18n[builder] → googletrans` 的间接依赖存在，`--no-dev` 镜像里没有）。

## phase5 — 测试

- 库 333 passed（含新增 17 个 linux.do 用例）
- bot 165 passed；ruff 全过
- **测试的 mock 目标要跟着改**：`patch.object(httpx.AsyncClient, "get")` 换底层后静默失效 →
  新建 `test/_fakes.py`（假响应 + `patch_async_get`），patch 到 `parsehub.utils.http.AsyncClient.get`

## phase6 — 真实平台验证（生产容器内，带 cookie）

| 平台 | 结果 |
|---|---|
| twitter | ✓ 浏览 26282 / 赞 209 |
| threads | ✓ 8 媒体 / 赞 304 |
| pixiv | ✓ 3 媒体 / 浏览 4988 / 赞 159 / 8 标签 / 下载 2.57 MB |
| bilibili | ✓ 1 媒体 / 浏览 5,531,414 / **下载 187 MB** |
| linux.do | ✓ 见下 |

## phase7 — 接入 linux.do（Discourse 平台）

- `Platform.LINUXDO = ("linuxdo", "linux.do")`；`profile_url` 模板 `https://linux.do/u/{handle}`
- `provider_api/linuxdo.py`：读 `/t/topic/<id>.json`；映射 title、楼主帖正文（`cooked` HTML → markdown）、
  作者（含 `details.created_by` 回退）、`created_at`、`views`、`like_count`、`reply_count`、`tags`；
  **媒体取 `a.lightbox` 的 href（原图）**，`img` 宽高只声明比例；`NSFW` 标签 → `is_sensitive`
- 正文清洗：展开 `details/summary`（富文本不支持折叠）、移除 `div.lightbox-wrapper`（图片走 media 单发）
- `parsers/parser/linuxdo.py` + `parsers/parser/__init__.py` 注册 + 作者覆盖契约测试清单
- cookie 配到生产的 `platforms.linuxdo.cookies`（含 `cf_clearance` / `_forum_session`），重启容器生效
- 生产端到端验证：标题、作者+主页、时间、浏览 927、赞 125、标签、敏感 True、媒体 1 张，
  **真实下载 1.75 MB jpeg 成功**（`cdn3.ldstatic.com` 实测无防盗链）

## 坑与教训

1. **curl_cffi 缺 `aclose`/`is_closed`** → 调用点报 AttributeError，**把真实异常盖掉**（bilibili 上白查一轮）。
   封装层补齐后正常。教训：换 HTTP 底层要**系统枚举**调用点用到的每个能力（本次用 AST 提取
   `AsyncClient(...)` 的全部 kwarg 与响应属性用法），不能只改 import。
2. **`follow_redirects` 默认值相反** → 不翻译会静默改变行为（下载器的 Range 探测最敏感）。
3. **测试 mock 目标失效**是这类迁移的常见回归，且表现为“原来绿的测试红了”，容易误判成实现坏了。
4. **验证样本必须真实存在**：随手编的 bilibili BV 号返回 `code=-404 啥都木有`，我先怀疑改动；
   换成真实 BV 号立刻成功。判据：先用不依赖业务权限的接口（`x/web-interface/nav` → `isLogin`）
   区分“网络/指纹问题”与“样本/权限问题”。
5. 一条 `curl_cffi + cookie` 的 403 也可能只是 **cookie 里的 `cf_clearance` 过期** —— 报错文案要写清
   “请在平台配置里更新 cookie”，别让用户以为是解析器坏了。
