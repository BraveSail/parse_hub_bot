# Local modifications

This repository is derived from [z-mio/ParseHub][upstream]. It is **not** a GitHub fork
(the fork relationship was dropped), and the upstream project stays the base: releases are
pulled in manually (`git fetch upstream && git merge upstream/master`).

Below is what differs from upstream, grouped by topic. Every item maps to one or more
commits in `git log upstream/master..master`.

## 1. 帖子与作者元数据（库的上层消费方依赖这些字段）

- **`is_sensitive`** — 平台有官方敏感标记时置位（pixiv `xRestrict > 0`、twitter
  `possibly_sensitive`），**不靠关键词猜**。4 个 `ParseResult` 子类同参转发 + `to_dict()` 带上。
- **`published_at` / `view_count`** — 发布时间与浏览量/播放量，拿不到就留 `None`
  （展示层不显示那一段，不留空占位）。取值来源：twitter `legacy.created_at` + `views.count`、
  threads `taken_at`、douyin `create_time` + `statistics.play_count`、bilibili `View.pubdate` +
  `stat.view`、yt-dlp `timestamp` + `view_count`、pixiv `createDate` + `viewCount`。
- **`author_name` / `author_handle` / `author_url`** — 作者显示名、用户名（不带 `@`）、主页地址。
  上游多数平台不返回作者；本仓库给所有支持平台补齐作者名，并只用**明确的作者对象**取值，
  **绝不从正文猜**（`utils/helpers.get_author_name`）。
- **`tags`** — 平台自带的标签（pixiv `tags.tags[].tag` 等），归一化（去空、忽略大小写去重、
  保持原顺序）在库层完成；没有这类字段的平台留空。

## 2. 引用 / 回复上下文

- **twitter**：被回复推文（`in_reply_to_status_id_str`）与被引用推文（`is_quote_status` +
  `quoted_status_id_str`）**互不排斥**，两条路各自生成。内嵌的 `quoted_status_result.result`
  **可能被匿名请求降级成 `TweetUnavailable`**，所以另有 `quoted_status_id` 兜底，失败只记
  warning、不阻断主解析。
- **threads**：**无需二次请求** —— 父帖与目标帖同处一个 `thread_items` 数组、目标帖紧邻的
  前一条即被回复帖（仅在 `text_post_app_info.is_reply` 为 true 时取，防误引用）。
- **t.co 还原**：twitter 正文里按 `entities.urls` 把短链换成 `expanded_url`。

## 3. 公共排版 helper（所有平台共用，不要各写一份）

- `format_author_label(name, handle)` — `名字 @handle`（相同则只留 `@handle`）。
- `format_author_link(name, handle, url)` — 同上，handle 渲染成 `<a href>`。
- `format_quote_block(text, author)` — 引用块（斜体、不写「引用/回复」字样、作者行在前）。
- `profile_url(platform, handle=, user_id=)` + `PROFILE_URL_TEMPLATES` — 各平台主页地址模板；
  平台没模板或缺所需字段时返回**空串**，调用方退回纯文本。

## 4. 平台解析修复

- **pixiv 支持（整平台新增）**：illust 解析、图片尺寸与真实后缀、下载带 `Referer`（pixiv 防盗链）、
  用 `master1200` 而非原图（体积/限流）。
- **facebook**：`watch/?v=<id>` 写法与 `v` 参数的保留（否则被通用参数清理剥掉、退化成 `/watch`）。
- **bilibili**：`view/detail` 端点匿名必被风控，需带 cookie。
- **yt-dlp**：条目可能**没有 `thumbnail` / `description`**（facebook 实测），下标访问会 KeyError
  让整条解析失败 → 一律 `.get()`；保留原语言音轨（格式排序）。

## 5. 依赖 / 构建

- 发布到 PyPI 的包名仍是 `parsehub`；上层 bot（[BraveSail/shirobako][bot]）通过 PyPI 版本依赖本库，
  再在 Docker 构建时用 `additional_contexts` 覆盖安装本仓库 checkout。

## Credits

- [ParseHub (z-mio/ParseHub)][upstream] — 本项目的基础

## License

MIT License，见 [LICENSE](LICENSE)。

[upstream]: https://github.com/z-mio/ParseHub
[bot]: https://github.com/BraveSail/shirobako
