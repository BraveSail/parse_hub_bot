# Local fork modifications

This repository is derived from [z-mio/parse_hub_bot][upstream-bot]. It is no longer a
GitHub fork — it has been detached from the fork network and renamed to `shirobako` — but
the upstream project stays the base and changes are pulled in manually.

Below is what differs from upstream, grouped by topic. Every item has a matching commit in
`git log upstream/main..main`.

## 1. 发送形态：富文本（rich message）是唯一路径

上游用「媒体组 + caption」发送：媒体堆在顶部，正文当 caption，排版被压平。本仓库改成
Telegram 的富文本消息（`sendRichMessage`），正文按原文 markdown 还原，统计与来源进页尾。

- **HTML 锚点**：footer 与正文里的链接必须写 `<a href="url">文字</a>` —— 富文本的 markdown
  链接语法在 footer 块里不生效，会原样显示成 `[文字](url)`。
- **页尾统计行**：发布时间 + 浏览量，**一律 24 小时制**（中文只有日期部分是中文格式，
  如 `19:00 · 2026年10月3日`），拿不到的项直接不显示，不留空占位。
- **标签行**：平台提供的标签渲染成指向该平台标签页的链接；行首 `#标签` 必须转义
  （否则被当成一级标题，字号巨大），裸 hashtag 遇到 `・` 之类的字符会被服务端截断，
  链接形式两个问题都没有。标签多时按显示宽度截断（`TAG_LINE_DISPLAY_BUDGET`）。
- **多图 = 图集**：正文里并列多个媒体占位会被渲染成各自独立的图片，必须包进
  `<tg-collage>`（blocks 路径用 `InputRichBlockCollage`）。
- **单换行会被吞成空格**：行尾补两个空格才保留换行（threads 这类一行一条的排版依赖它）。
- **删除的路径**（`feat!: rich message is the only send path`）：
  - 「媒体组 + caption」老发送路径
  - `rich_mode` 开关（不再有第二种模式，开关无意义）
  - 长文的 Telegraph 路径（富文本已能直接还原 markdown 正文）
  - `/raw`、`/zip` 两个显式命令模式**保留**（用户敲命令才走，不是默认路径）
- **缓存路径同样走富文本**：`send_cached` 与 inline 共用 `build_cached_rich_content`，
  从缓存字段重建同一套排版（否则同一链接第二次发送会变成另一种格式）；发送后把服务端
  返回的媒体 file_id 写回缓存，下次发送零上传。

## 2. inline 模式

- **一个链接只给一项**（`RICH_RESULT_ID`），Media 走「占位 → 用户选中 → 下载上传 → 编辑」
  流程：回答查询时不能带外链媒体（Telegram 回 `400 EXTERNAL_MEDIA_NOT_SUPPORTED`），
  编辑（`messages.EditInlineBotMessage`）不受这条限制。
- **不要用 `InlineQueryResultPhoto` 做封面占位**：那样消息本体就是「一张图 + caption」，
  富文本的排版 / 图集 / 标签 / 页脚全部丢失。封面只当结果列表的缩略图（`thumb_url`）。
- **键盘的用途**：Telegram 只在消息带 inline keyboard 时才回传 `inline_message_id`，
  这是二次编辑的前提；选中后立刻用 `ReplyKeyboardHide` 摘掉（空的 `ReplyInlineMarkup` 非法）。
- **多图全量获取**：结果过多时用 `switch_pm_text` 深链跳转私聊继续发送
  （`services/inline_share.py`，`token_urlsafe(16)` 做 TTL 映射，参数限 `A-Za-z0-9_-`）。
- **兜底**：`answer()` 被拒时先剔掉富文本项重试，再回落纯文字提示 —— 拒答发生在
  `answer()` 这一步，不兜底客户端只会一直转圈。
- **折叠**：长正文按 350 字符 / 8 行折叠（`<blockquote expandable>`，不可嵌套）。

## 3. 元数据与作者

- **库侧新增字段的消费端**：`published_at` / `view_count` / `author_handle` / `author_url` /
  `tags`（对应 [BraveSail/ParseHub][fork-lib] 的改动）。每个字段的落地清单是
  库 → 直发路径 → 缓存路径（`build_rich_markdown_by_str`）→ `CacheParseResult` →
  「缺字段即过期」的重解析判据，漏一处就是「有时候有、有时候没有」。
- **作者行**：`名字 @handle：`，其中 `@handle` 链到作者主页；显示名与用户名相同时只写
  `@handle`。主页地址由库的 `profile_url(platform, handle=, user_id=)` 按模板生成。
- **引用 / 回复块**：整块斜体、**不写「引用」「回复」字样**（引用块本身已表明关系）、
  作者 handle 链到主页；主推自己的媒体排在引用卡片**之上**（与 X 的观感一致）。
- **跨平台统一**：引用块 / 作者标签 / 主页地址的排版全走库里的公共 helper
  （`format_quote_block` / `format_author_link` / `profile_url`），平台解析器只取字段，
  不再各写一份。

## 4. 敏感内容遮罩

- 平台有官方标记才打码（pixiv `xRestrict > 0`、twitter `possibly_sensitive`），不靠关键词猜。
- 直发：`has_spoiler`。
- 富文本：官方 API 的富文本媒体块**没有 spoiler 字段**，敏感内容自动切到自造的 raw
  blocks 路径（`plugins/parse/rich_blocks.py`，`PageBlockPhoto/Video(spoiler=True)`）。
- inline：占位封面那截打不了码（`InlineQueryResultPhoto` 走 `InputWebDocument`，协议层
  没有该字段）；选中后编辑成的富文本那截**能**打码（必须走 blocks，markdown+media 路径会静默丢遮罩）。

## 5. 构建与部署

- **`uv.lock` 不能把 parsehub 写成 git 源**：builder 镜像里没有 git，`uv sync --frozen` 会
  直接失败。保持 `pyproject.toml` 写 PyPI 版本，Dockerfile 再用 `additional_contexts` 覆盖安装。
- 新增 `Dockerfile.deploy` + `compose.deploy.yaml`（本仓库的部署方式）。

## 6. i18n

新增文案（「查看」、「获取全部 N 项」、「上 传 中...」等）补齐 16 种语言：de / en / es /
fr / id / it / ja / ko / nl / pl / pt-br / ru / th / tr / vi / zh-hant。

## 依赖库

本仓库依赖 [BraveSail/ParseHub][fork-lib]（上游 [z-mio/ParseHub][upstream-lib]）的衍生版本，
相关的库侧改动（富文本排版所需的字段与公共 helper）记在该仓库自己的说明里。

## Credits

- [ParseHubBot (z-mio/parse_hub_bot)][upstream-bot] — 本项目的基础
- [ParseHub (z-mio/ParseHub)][upstream-lib] — 解析库

## License

MIT License，见 [LICENSE](LICENSE)。

[upstream-bot]: https://github.com/z-mio/parse_hub_bot
[upstream-lib]: https://github.com/z-mio/ParseHub
[fork-lib]: https://github.com/BraveSail/ParseHub
