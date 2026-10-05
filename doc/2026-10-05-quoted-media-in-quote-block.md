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

（↑ 以上为第一轮，已上线；下面为**第二轮**：折叠时媒体被挤出引用块。）

## 第二轮背景（2026-10-05 取证）

用户报障：「图片是引用里面的 放外面了」。真机取证（`probe_quote_media.py`）：

- `build_rich_media` 切分正确（正文 0 / 引用 1 / 回复 0），但**渲染出的 markdown 里
  引用块内没有占位符**，图被放到块外。
- 根因 = `render_quote_card` 的设计（历史取舍）：**引用块会折叠时把媒体挪到块外**。
  理由（当时真机实测）：`<blockquote expandable>` 内的 `![]()` 不解析（显示成字面
  `![]()`），换 `<img>` 又能出图但会把块退化成不可折叠。

### Bot API 文档核对（决定性）

| 块 | 内容字段 | 能否含图 |
| --- | --- | --- |
| `blockquote`（`InputRichBlockBlockQuotation`）| **`blocks: Array of InputRichBlock`** | ✅ |
| `expandable_blockquote`（`InputRichBlockExpandableBlockQuotation`）| `text: RichText` | ❌ |
| `details`（`InputRichBlockDetails`）| **`blocks: Array of InputRichBlock`** | ✅ |

- `RichText` 的成员表里**没有任何图片类型**（Bold/Italic/Underline/Strike/Spoiler/DateTime/
  Code/Url/…）⇒ 「**可折叠引用块内嵌图**」在 API 层不存在。另实测构造 `TextImage`
  放进折叠引用块被服务端拒：`RICH_MESSAGE_RICH_TEXT_UNSUPPORTED`。
- 本地 kurigram **2.2.26 = PyPI 最新**（无更新），且**已内置** `InputRichBlockBlockQuotation`
  与 `InputRichBlockDetails`。

### 用户选定形态：D1

引用块内 = **前几行预览 + `<details>` 折剩余 + 图（始终可见）**，
即把**现有折叠逻辑**（`split_fold_preview` 预览留外面 + `_render_foldable`）搬进引用块：

```
<blockquote>
{预览前几行}

<details><summary>展开全文</summary>

{剩余文字}

</details>

![](tg://photo?id=m0)
</blockquote>
```

- 「展开按钮同步现有逻辑加上前几行预览」= 用同一套 `_should_fold` 阈值 + `split_fold_preview`。
- **只有"会折叠 + 有媒体"的引用卡片**走这个新形态（`quote_will_fold(quote) and media`）：
  短引用（不折叠）现状已把图放在块内（`attach_quote_media`），不动它。
- markdown 的 `>` 块内**无法**嵌 `<details>` + 图，所以这条形态**必须走 blocks 路径**
  （与敏感内容同一机制）；发送分支的判据由 `is_sensitive and media_blocks` 扩展。

### phase4: 引用卡片的 D1 渲染（markdown 层）
- 产物：`plugins/helpers.py`
  - `render_quote_card`：会折叠 + 有媒体时产出上面的 `<blockquote>` 容器写法
    （预览用现有 `split_fold_preview`，summary 沿用 `展开全文`）；
  - 新增判据函数（供发送层复用）：`quote_card_needs_blocks(quote, media) -> bool`
    = `bool(media) and quote_will_fold(quote)`。
- 验证：新增 `test/test_quote_card_fold.py`：断言「会折叠 + 有媒体」产出
  `<blockquote>` 容器、含 `<details>` 与占位符、**预览行在 details 之外**；
  「不折叠 + 有媒体」仍走 `attach_quote_media`（`> ` 前缀）；「无媒体」不变。

### phase5: blocks 转换器支持块内嵌套
- 产物：`plugins/parse/rich_blocks.py::markdown_to_blocks` 新增对**非 expandable
  `<blockquote>…</blockquote>` 容器**的递归解析（内部可含段落 / `<details>` / 媒体占位符），
  产出 `InputRichBlockBlockQuotation([...子块...])`。现有 `>` 前缀写法保持原样。
- 验证：新增用例断言容器 → `BlockQuotation[Paragraph, Details[Paragraph], Photo]` 结构；
  且 `>` 前缀写法不回归。

### phase6: 发送分路扩展
- 产物：`plugins/parse/sender.py`、`plugins/parse/inline.py`、`plugins/parse/guest.py`
  与缓存命中路径（`cached_rich_message` / `build_cached_rich_content`）——
  切 blocks 的判据从 `is_sensitive and media_blocks` 扩为
  **`or quote_card_needs_blocks(引用正文, 引用占位符)`**，保证「首次发送」与「缓存命中」
  形态一致（既有约定）。
- 验证：新增/更新用例覆盖四个调用点（含缓存命中），断言「引用会折叠 + 有媒体」时走 blocks。

### phase7: 端到端
- 验证：这条真实链接（`asora_jp/status/2107244453484265938`）走生产 pipeline →
  按生产参数（`skip_entity_detection` 等）发送 → 读回块结构确认
  `BlockQuotation → [Paragraph(预览), Details(剩余), Photo]`；并清掉该链接旧缓存，
  让用户复验。

## 回滚
`git revert` 对应 commit（第一轮与第二轮各自独立）。

## 落地结果（2026-10-05）

- **实现**：`git log` —— `feat: the quoted post's media stays inside the quote, and long quoted
  text folds with a preview`（第二轮 phase4–7 全部完成）。
- **真机验证**（读回服务端块，实样 msg 910）：

  ```
  RichBlockBlockQuotation
    RichBlockParagraph      ← 预览（前几行，details 之外）
    RichBlockDetails        ← 折叠的剩余文字
    RichBlockPhoto          ← 图在引用块内 ✓
  ```

- **测试**：新增 `test/test_quote_card_fold.py`（9 条：markdown 形态 + blocks 结构 + 判据）；
  更新 `test/test_caption_quote.py`（折叠引用：媒体从「块外」改为「块内」）与
  `test/test_cached_rich.py`（非敏感也产出映射、但 `spoiler=False`；敏感走 blocks 需显式传参）。
  bot 436 / lib 490 全过，`scripts/check.sh` 干净。
- **同步范围**：`sender.py`（私聊/群直发 + 缓存命中）、`inline.py`（直发 + 缓存结果项）、
  `guest.py`、`inline_rich.py::cached_rich_message` —— 五处选路判据统一为
  `helpers.markdown_needs_blocks(markdown)`（看渲染产物，单一真相源）。
- **被推翻的旧行为**：折叠的引用卡片把媒体挪到块外（原因：`<blockquote expandable>` 只吃
  RichText，而 RichText 无图片类型）。文档与实测共同证明「可折叠引用块内嵌图」不存在，
  唯一出路是「blockquote 容器 + details 折叠」。
