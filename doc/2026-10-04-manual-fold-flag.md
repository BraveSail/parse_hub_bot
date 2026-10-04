# 手动折叠开关：链接后跟 `#nsfw` / `#spoiler`

日期：2026-10-04 · 来源：用户提议（`has_spoiler` 讨论引出的更优方案）

## 需求

用户：「用户不能手动打码吗？比如链接后面加上 /s」，随后明确：

> 「用正文那个折叠功能，标记 ⚠️」
> 「我是说空格 /s」

后来（同日）改标记，并要求摘要跟着变：

> 「把手动折叠的 /s 改成 #nsfw 和 #spoiler，过滤方式不变，然后去掉 叹号emoji后面的文字
> 换成叹号空格 #nsfw 或者 #spoiler」

再陆续补了几批标记：`#r18`（不分大小写）、`#劇透`、`#剧透`、`#色色`、`#不可以色色`。

**即：`<链接> #nsfw`（或 `#spoiler`）→ 内容折进 `<details>`，摘要 `⚠️ #nsfw` / `⚠️ #spoiler`**。

### 变更：标记与摘要（`/s` → `#nsfw` / `#spoiler`）

| 项 | 之前 | 现在 |
| --- | --- | --- |
| 标记 | `/s` | `#nsfw` `#spoiler` `#r18` `#劇透` `#剧透` `#色色` `#不可以色色`（**全部等价**） |
| 判定 | 独立 token | **不变**（`#nsfwxx`、URL 里的片段都不算） |
| 大小写 | — | **不敏感**（`#R18` / `#NSFW` 都算），但返回值保持用户原形 |
| 摘要 | `⚠️ 展开全文` | `⚠️ <用户写的那个标记>` |

- **解析函数返回标记本身**（不是 bool）—— 摘要要显示是哪个，让读者知道为什么藏。
- **大小写不敏感但保留原形**：`#R18` 触发后摘要写 `#R18`（不是规范化成小写）——
  摘要显示的是用户自己写的字。实现是 `token.lower() in _SPOILER_LOOKUP` 查表。
- **`#劇透` 与 `#剧透` 都登记**：同一个词的繁简两种写法，用户用哪种都该触发。
- **完全匹配，不是子串**：`#不可以色色` 与 `#色色` 是两个独立标记，互不覆盖
  （`#色色` 不会误触发 `#不可以色色` 那条，反之亦然）。
- **两个都写时以先出现的为准**（`#nsfw #spoiler` → `#nsfw`），不随缘。
- **摘要不再走 i18n**：标记是用户输入，不翻译，任何语言下原样显示。
  原来的「展开全文」是复用自动折叠的按钮文案 —— 那个文字对"为什么被藏"没有信息量。
- **`#nsfw` 会被 Telegram 自动识别成 hashtag**（真机块里是 `RichTextHashtag`，可点击）。
  这是客户端行为；要避免就得关掉整条消息的实体检测（`skip_entity_detection`），
  那会连链接一起关，得不偿失。

## 为什么不用 spoiler

先调研了 `has_spoiler`（见下），但真机验证发现**富文本 markdown 路径打不了码**：

| 路径 | 服务端块 `has_spoiler` |
| --- | --- |
| 普通消息 API（`send_photo` / `InputMediaPhoto`） | ✓ 生效 |
| 富文本 **markdown** 路径 + `has_spoiler=True` | ✗ **被忽略**（恒为 False） |
| 富文本 **blocks** 路径（`SpoilerPhotoBlock`） | ✓ 生效 |

官方 `InputRichBlockPhoto` 没有 spoiler 字段，只有 raw `PageBlockPhoto(spoiler=)` 有。
要遮图必须切 blocks 路径，而**用户要的是遮文字** —— 折叠更直接，也不依赖 spoiler。

## 为什么是「空格 + 独立 token」

| 方案 | 问题 |
| --- | --- |
| URL 参数 `?spoiler=1` | 被 `get_raw_url` 的清理逻辑摘掉（除非加进 `__reserved_parameters__`） |
| URL 路径后缀 `/s` | 污染缓存 key（`sha256(raw_url)`）；部分 provider 按固定段数解析路径会出错 |
| **空格分隔的独立 token** ✓ | URL 本体完全不变；纯链接判定剔掉它即可 |

实测 `url_only_message_urls` 的判定是「按空白切分后每个片段必须本身就是完整链接」，
所以标记天然不通过 —— 剔掉后 `<链接> #nsfw` 正常算纯链接
（`#nsfwxx`、URL 里的 `nsfw` 片段都不误伤）。

## 遮住的范围：**除了标题与作者，全部进折叠**

用户要求（原话）：**「图也遮上，我要的是所有东西都遮，不然我直接用 spoiler 了」**

第一版只折了正文 —— 图、引用块、标签全露在外面，等于没遮。现在实现按"元信息 / 内容"分组：

| 组 | 内容 | 位置 |
| --- | --- | --- |
| 元信息 | 标题、作者 | **折叠外** —— 不然只剩一个警告三角, 看不出解析了什么 |
| 内容 | 正文、引用块（含被引用媒体的块）、标签行、媒体（含 `<tg-collage>` 图集） | **全部进 details** |

**实测媒体能进 details**（这是前提，先验证再改）：

```
details 内含占位符   -> RichBlockDetails 内=['Paragraph', 'Photo']    ✓
details 内含图集     -> RichBlockDetails 内=['Paragraph', 'Collage']  ✓
```

于是实现改成：先把 parts 拆成 `meta_parts`（标题/作者）与 `parts`（其余），
`hide_content` 时把整个 `parts` 拼进一个 `<details>`，再用 `meta_parts` 打头。

## 摘要：从「展开全文」到 `⚠️ <标记>`

演进过两版：

1. 第一版新建了「内容已隐藏」并**手动插进 16 个 yaml** —— 用户指出折叠按钮的文字
   **本来就有**，于是改成复用自动折叠的词条 `展开全文`（16 语言早已就位）。
2. 用户随后要求**去掉「叹号 emoji 后面的文字」**，换成 `⚠️ #nsfw` / `⚠️ #spoiler`。

现在是**标记本身**：`<details><summary>⚠️ #nsfw</summary>`。

- **不走 i18n**：标记是用户输入，不翻译 —— 任何语言下都原样显示。
- 放弃「展开全文」是因为它对"**为什么**被藏"没有信息量；标记既说明原因，又和用户在
  消息里写的字面一致。
- 为此解析函数改为**返回命中的标记**（`str`）而不是 `bool`，一路传到渲染层。

用 `i18n.build` + 守卫 translator 验证过：`Content unchanged, skipping build`
（无漏翻、无孤儿键）。

**手插翻译还跳过了 `i18n.build` 的自校验** —— 用项目自带的构建入口验证过：
`Content unchanged, skipping build` + GuardTranslator 零触发（无漏翻、无孤儿键）。
以后新增/改动文案**一律走 `uv run python i18n.py`**（或脚本调 `i18n.build` + 守卫 translator），
不要手写 yaml。

## 实现（5 处）

1. `strip_spoiler_flag(text) -> (text, 命中的标记或空串)`（`parsehub/utils/helpers.py`）；
   `SPOILER_FLAGS = ("#nsfw", "#spoiler")`、`SPOILER_FOLD_SUMMARY = "⚠️"`（兜底）
2. `url_only_message_urls` 内先剔标记，否则 `<链接> #nsfw` 不算纯链接
3. `build_rich_markdown(..., hide_content=<标记>)`：内容整个进 `<details>`，**不留预览**
   —— 与自动折叠相反（那是为避开长正文，这是用户明确要藏）。
   参数是**标记字符串**（空串=不遮），非空即遮，摘要直接用它
4. 贯通三个入口 + inline 选中回调：
   - `ParseRequest.spoiler_tag` / `send_rich_media(spoiler_tag=)`
   - `build_inline_results(spoiler_tag=)`
   - `guest._answer(spoiler_tag=)`
   - ⚠️ **`inline_result_download`（选中回调）也要剥** —— 它重新读 `chosen_result.query`，
     不剥的话标记会被拼进 URL

## 坑：缓存命中时标记曾完全失效

用户报「我 url /s 不行」。日志给了根因：

```
06:05:47 | Parse: 收到解析请求: url=https://x.com/i/status/2106362173111034087, chat_id=-1001481033767
06:11:31 | Parse: 收到解析请求: url=https://x.com/i/status/2106362173111034087, chat_id=-1001481033767  ← 带 /s
```

**同一条链接几分钟前已解析过 → file_id 缓存命中 → 走缓存直发路径**：

```
handle_parse → persistent_cache.get(raw_url) 命中
             → _try_send_cached → send_cached → build_cached_rich_content
```

而 `build_cached_rich_content` 一直**不知道 `force_spoiler`** —— 它在缓存历次改动里
只加了 `view_label`/`custom_content`，没有任何渲染开关。于是带 `/s` 发送时，
排版照旧不带折叠，表现就是"/s 没用"。

**关键认识**：`CacheEntry` 存的是**解析字段**（title/content/author/…）+ 媒体 file_id，
**不含渲染后的 markdown**，`rich` 只是个标志。所以"遮不遮"是**发送时的排版决定**，
缓存命中时完全可以照做 —— 不需要跳过缓存，只要把参数传下去。

**修复**（3 条缓存路径，6 处）：

| 路径 | 链路 |
| --- | --- |
| 私聊/群 | `handle_parse` → `_try_send_cached` → `send_cached` → `build_cached_rich_content` |
| inline | `_call_inline_parse` → `build_cached_rich_result` → 同上 |
| guest | `_answer` → 同上 |

`spoiler_tag` 依次传进 `send_cached` / `build_cached_rich_result` / `build_cached_rich_content`
→ `build_rich_markdown_by_str` 的 `hide_content` → `build_rich_markdown`。

另外**请求日志加上 `spoiler_tag=`** —— 这次的日志里 url 已被剥掉标记，无法分辨
用户到底带没带 `/s`，只能靠时间线推断缓存命中。以后一眼可见。

## 验证

- bot 309 passed（新增 22 条：标记解析 / 与纯链接判定共存 / 渲染 / 不留预览 /
  媒体与引用媒体都在折叠内 / 摘要显示标记 / 摘要不随语言 / 短正文不误折 /
  **缓存路径渲染与传参**）、lib 444 passed、`check.sh`（ruff + pylint）干净。
- **真机 · 用真实缓存条目**（用户报障的那条 URL，缓存已命中）走 `send_cached`：

```
不带标记: 折叠=False
带标记:   折叠=True  媒体在折叠内=True      ← 修复后才对
```
- **真机服务端块**（改标记后的最终形态）：

```
块0: RichBlockParagraph   作者 (折叠外)
块1: RichBlockDetails     收起=True
     摘要节点 = [文字 '⚠️ ' , RichTextHashtag '#nsfw']
     内 = ['RichBlockParagraph', 'RichBlockParagraph', 'RichBlockPhoto']   ← 正文 + 图
```

所有标记都被 Telegram 识别成 **`RichTextHashtag`**（用户消息里也是同一种实体），
折叠本身不受影响。实测 7 个标记 + `#R18`：全部 `RichBlockDetails`（收起）+ 内嵌正文与 `RichBlockPhoto`。

## 附带查明的 `has_spoiler` 用法

- **普通消息**：`send_photo(path, has_spoiler=True)` / `InputMediaPhoto(media, has_spoiler=True)` ✓
- **富文本**：只有 `blocks` 路径有效 —— `InputRichMessage(blocks=[SpoilerPhotoBlock(InputMediaPhoto(path), spoiler=True)])`
- **项目里是自动的**：平台标记敏感（linux.do 的 NSFW / twitter 的敏感）→
  `sender.py:550`、`inline.py:345` 检测到 `is_sensitive` 且有媒体就自动切 blocks 路径，
  不需要手动传。

**一处冗余**（未改）：`inline_rich.py` 里 markdown 用的 media 项仍传 `has_spoiler=is_sensitive`
（4 处），实测无效 —— 真正生效的是同函数产出的 `media_blocks`。无害但误导。
