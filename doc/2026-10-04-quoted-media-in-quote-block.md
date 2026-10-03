# 计划: 把被引用帖的媒体渲染进引用块

## 背景（已取证）

- **能力已确认**：真机发送验证，引用块内可以放媒体 ——
  `RichBlockBlockQuotation[RichBlockParagraph, RichBlockPhoto]`（图）与
  `...[RichBlockParagraph, RichBlockAnimation]`（视频），markdown 占位符写法与 blocks 写法都通。
- **数据已具备**：`TwitterTweet.quoted_status.media` 解析时就已保留
  （`_parse_quoted` → `_parse_result`），只是 `TwitterParser.media_parse`
  只把 `tweet.media` 转成 refs，引用帖媒体被丢弃。
- **现状**：引用块只渲染文字（`format_quote_block`），媒体全丢。

## 设计

引用帖媒体**追加在主推媒体之后**（同一个 `media` 列表），并用一个新字段记住末尾有多少个是
引用帖的：`ParseResult.quoted_media_count`。

- 追加而非独立字段 → **下载链路零改动**（`_do_download` 已经处理整个 `media`）。
- 渲染时按"末尾 N 个 ref"的占位符切分：`build_rich_media` 按 ref 分组收集占位符，
  返回 `(media, placeholders, media_blocks, quote_placeholders)`；
  `build_rich_markdown` 把 `quote_placeholders` 插进引用块内部（每行加 `> ` 前缀）。

引用块内多图是否要包 `<tg-collage>`：先按 collage 处理，实测确认。

## 阶段

### phase0: lib 字段
- 产物：`ParseResult.__init__` 加 `quoted_media_count: int = 0`；四个子类
  （Video/Image/Multimedia/RichText）加参并转发；`to_dict()` 带上；
  `test/test_core_offline.py` 契约断言同步。
- 验证：lib 全量测试通过。

### phase1: twitter 解析层
- 产物：`TwitterParser.media_parse` 把 `tweet.quoted_status.media` 的 refs 追加到
  `media` 末尾并设置 `quoted_media_count`（回复帖 reply_to 的媒体不处理，保持现状）。
  引用块文字渲染不变（媒体由发送层插入）。
- 验证：真实带图引用帖 → `media` 数量 = 主推 + 引用帖，`quoted_media_count` 正确。

### phase2: bot 渲染层
- 产物：`plugins/parse/inline_rich.py::build_rich_media` 分组返回引用块占位符；
  `plugins/helpers.py::build_rich_markdown` 增加 `quote_media_placeholders` 参数，
  把它们插进引用块末尾（`> ` 前缀）；`build_rich_markdown_by_str` 同步加参；
  `CacheParseResult` 加 `quoted_media_count` + 所有构造点 + 过期判据。
- 验证：bot 测试 + 真实渲染出的 markdown 里引用块含占位符。

### phase3: 端到端
- 验证：真实链接走生产 pipeline → 发送 → dump 服务端块，
  确认 `RichBlockBlockQuotation` 内含媒体块。

## 回滚
`git revert` 对应 commit。
