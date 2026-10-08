# YouTube 社区帖子：一个 parser 同时管视频与帖子（并把 yt-dlp 客户端抽出来）

日期：2026-10-08
触发：用户贴 `https://www.youtube.com/post/Ugkxzq0QbgtS1VgHo8hIjZwpiwshZSvd-JtO` 报「不支持」；
随后指令：**「把 ytdlp 抽出来，然后和 post 合并成一个 parser」**。

## 根因

`lib/src/parsehub/parsers/parser/youtube.py` 的匹配规则**显式排除帖子**：

```python
__match__ = r"^(http(s)?://).*youtu(be|.be)?(\.com)?/(?!(live|post))(?!@).+"
```

（上游 2025-08-21 commit `0e8cafc`「调整 YouTube 正则规则以排除帖子」引入。）
没有 parser 接手 ⇒ bot `services/parser.py` 抛 `ValueError("不支持的平台")`。

**yt-dlp 也拿不到帖子**（不是"换个解析器就行"）：

```
$ yt_dlp --dump-single-json https://www.youtube.com/post/Ugkxzq0QbgtS1VgHo8hIjZwpiwshZSvd-JtO
ERROR: [youtube:tab] post: This channel does not have a Ugkxzq0QbgtS1VgHo8hIjZwpiwshZSvd-JtO tab
```

它把 `/post/<id>` 当成**频道 tab**。所以帖子必须另开一条取数路径。

## 方法：帖子数据在页面里的 ytInitialData

22 条真实帖子（ANIPLUS Asia / MuseAsia / AniOne Asia / MrBeast）实测：

- 页面 HTML（~814 KB，**匿名可访问、无需 cookie、无需 JS**）里带完整的
  `var ytInitialData = {...}`；帖子本体在
  `contents → twoColumnBrowseResultsRenderer → tabs[0] → tabRenderer.content →
  sectionListRenderer → contents[*] → itemSectionRenderer → contents[0] →
  backstagePostThreadRenderer → post.backstagePostRenderer`
- 附件形态分布：`backstageImageRenderer` 12 / `postMultiImageRenderer` 8（2–8 张）/ `videoRenderer` 2
- **绝对发布时间只在页头 JSON-LD**：`"datePublished":"2026-10-07T20:00:01.823371-07:00"`；
  列表里的 `publishedTimeText` 是 `"50 minutes ago"` 这类相对时间，不能当发布时间
- 点赞 `voteCount.simpleText` 是**缩写**（`9` / `1.1K` / `104K`）
- 配图可匿名直下（yt3.ggpht.com），页面给到 `=s3508-rw-nd-v1`（2480×3508）；多图给的是
  `=s1080-c-fcrop64=1,…` 的**方形裁剪**版，所以取原图要截掉 `=` 之后的部分
- **拿不到**：投票每项票数与占比（匿名只给选项文案 + 总票数，choice 里只有 `signinEndpoint`）

## 改动

### 1. yt-dlp 客户端抽到 provider 层

- 新增 `lib/src/parsehub/provider_api/ytdlp.py`：子进程调用（`extract_info` / `download_video`）、
  进度行模板与解析、cookie 与 info JSON 物化、尾部日志与错误提取 —— 这些是**通用基础设施**，
  与"谁是解析器"无关。搬运逐字一致（`diff` 只有两处函数改名：`_run_ytdlp_json`→`extract_info`、
  `_run_ytdlp_download`→`download_video`）。
- `lib/src/parsehub/parsers/base/ytdlp.py` 从 606 行瘦到 266 行，只留解析器骨架
  （`YtParser` / `YtVideoParseResult` / `YtVideoInfo`），导出面不变（bilibili / facebook /
  snapchat 的 import 一行未动）。

### 2. 帖子取数（provider）

`lib/src/parsehub/provider_api/youtube.py` 新增（与既有的链接卡片取数同文件，平台一个文件）：

- `POST_URL_RE` / `post_id_from_url()`
- `YoutubePost` / `YoutubePostImage` / `YoutubePostVideo` / `YoutubePostPoll` / `YoutubePostError`
- `parse_post_page(html)`（纯函数，便于离线测试）+ `fetch_post(url, proxy=, cookie=)`（唯一的网络入口）
- 抠 JSON 用**花括号配平**而不是正则匹配 `};</script>`（页面内联数据的结尾形态会变）

### 3. parser 合并成一个

`lib/src/parsehub/parsers/parser/youtube.py`：

- `__match__` 放开 post（保留 `(?!@)` 与 live 排除）；`__supported_type__` 增「图文」
- `_do_parse` 按 URL 分派：帖子 → `_parse_post()`（`MultimediaParseResult`，图片走 `ImageRef`）；
  其余 → `super()._do_parse()`（原 yt-dlp 路径，一行未改）
- 投票渲染成「总数 + 选项表」（与 twitter / linux.do 同为 markdown 表格，渲染层转 Table 块）；
  分享的视频只在正文放一行 `<a>` 链接 + 封面图，**不下载**（那不是帖子本体）

## 实测数据（改动后，真实链接，库入口 `ParseHub().parse`）

| 形态 | 作者 | 发布时间 | 点赞 | 媒体 | 备注 |
| --- | --- | --- | --- | --- | --- |
| 单图帖 | ANIPLUS Asia `@aniplusasia2014` | 2026-10-07 20:00:01-07:00 | 9 | 1 张 2480×3508 | 4 个标签 |
| 多图帖 | 同上 | 2026-10-02 21:00:39-07:00 | 46 | 2 张 1080×1080 | |
| 视频帖 | 同上 | 2026-10-01 21:00:00-07:00 | 1 | 封面 1 张 | 正文附 `watch?v=iAwRpKe61PA` 链接 |
| 投票帖 | MrBeast `@MrBeast` | 2026-07-19 11:32:42-07:00 | 104,000 | — | `投票 · 共 1,800,000 票` + 2 个选项 |

## 验证

- `lib` 全量：**778 passed**（新增 `lib/test/test_youtube_post.py` 21 例：匹配规则、链接分派、
  4 类 fixture 解析、缺 renderer、投票/视频的渲染形态）
- fixture 是**真实页面裁剪**（只留帖子本体 + JSON-LD），见 `lib/test/fixtures/youtube_post_*.html`
- `bash scripts/check.sh`（ruff + pylint --errors-only）干净
- `test_core_offline.py` 里把 post 从「已知不支持的 URL 形态」移到 YouTube 支持列表（有意变更）
- 视频路径回归：152 上 `Sign in to confirm you're not a bot`（YouTube 对本机出口 IP 的反爬）——
  用 yt-dlp CLI **绕过本项目**直接跑同一 URL 得到同样报错，证明与本次抽取无关；生产（161，
  配了 YouTube cookie）侧另行确认

## 遗留与注意事项

- **投票每项票数**需要登录（匿名拿不到），当前只渲染选项 + 总票数
- 帖子被删/私密时页面没有 `backstagePostRenderer` → `YoutubePostError` → `ParseError`
- 分享视频不下载（只封面 + 链接）；要下载得另走 yt-dlp
- `/root/ParseHub`（库的独立仓库）落后于本仓库的 `lib/` 子树（缺 `author_handle`/`author_url`
  等本地补丁），本次改动**只落在 shirobako**，与该仓库既有的落地约定一致
