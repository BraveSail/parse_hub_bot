# 手动折叠开关：链接后跟 `/s`

日期：2026-10-04 · 来源：用户提议（`has_spoiler` 讨论引出的更优方案）

## 需求

用户：「用户不能手动打码吗？比如链接后面加上 /s」，随后明确：

> 「用正文那个折叠功能，标记 ⚠️」
> 「我是说空格 /s」

**即：`<链接> /s` → 正文折进 `<details>`，摘要 `⚠️`**。

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
所以 `/s` 天然不通过 —— 剔掉后 `<链接> /s` 正常算纯链接（`/soccer`、URL 里的 `/s` 都不误伤）。

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

## 摘要复用现成词条（不新建翻译）

第一版新建了「内容已隐藏」并**手动插进 16 个 yaml** —— 用户指出折叠按钮的文字**本来就有**。

折叠按钮（`<summary>`）用的就是自动折叠那个词条 `展开全文`（16 语言早已就位），
所以摘要 = **`⚠️ ` + 现成的折叠文案**：

```
zh-hans  ⚠️ 展开全文        en-us  ⚠️ Show full text
zh-hant  ⚠️ 展開全文        ja-jp  ⚠️ 全文を表示
```

新键已从 16 个 yaml 里清掉。

**手插翻译还跳过了 `i18n.build` 的自校验** —— 用项目自带的构建入口验证过：
`Content unchanged, skipping build` + GuardTranslator 零触发（无漏翻、无孤儿键）。
以后新增/改动文案**一律走 `uv run python i18n.py`**（或脚本调 `i18n.build` + 守卫 translator），
不要手写 yaml。

## 实现（5 处）

1. `strip_spoiler_flag(text) -> (text, bool)`（`parsehub/utils/helpers.py`）；
   `SPOILER_FLAG = "/s"`、`SPOILER_FOLD_SUMMARY = "⚠️"`
2. `url_only_message_urls` 内先剔标记，否则 `<链接> /s` 不算纯链接
3. `build_rich_markdown(..., hide_content=True)`：正文整个进 `<details>`，**不留预览**
   —— 与自动折叠相反（那是为避开长正文，这是用户明确要藏）
4. 贯通三个入口 + inline 选中回调：
   - `ParseRequest.force_spoiler` / `send_rich_media(force_spoiler=)`
   - `build_inline_results(force_spoiler=)`
   - `guest._answer(force_spoiler=)`
   - ⚠️ **`inline_result_download`（选中回调）也要剥** —— 它重新读 `chosen_result.query`，
     不剥的话标记会被拼进 URL

## 验证

- bot 305 passed（新增 18 条：标记解析 / 与纯链接判定共存 / 渲染 / 不留预览 /
  媒体与引用媒体都在折叠内 / 摘要随语言 / 短正文不误折）、lib 435 passed、
  `check.sh`（ruff + pylint）干净。
- **真机服务端块**（全遮）：

```
块0: RichBlockSectionHeading   标题 (折叠外)
块1: RichBlockParagraph        作者 (折叠外)
块2: RichBlockDetails          摘要='⚠️ 展开全文'  收起=True
     内=['RichBlockParagraph', 'RichBlockParagraph', 'RichBlockPhoto']   ← 正文 + 图
```

## 附带查明的 `has_spoiler` 用法

- **普通消息**：`send_photo(path, has_spoiler=True)` / `InputMediaPhoto(media, has_spoiler=True)` ✓
- **富文本**：只有 `blocks` 路径有效 —— `InputRichMessage(blocks=[SpoilerPhotoBlock(InputMediaPhoto(path), spoiler=True)])`
- **项目里是自动的**：平台标记敏感（linux.do 的 NSFW / twitter 的敏感）→
  `sender.py:550`、`inline.py:345` 检测到 `is_sensitive` 且有媒体就自动切 blocks 路径，
  不需要手动传。

**一处冗余**（未改）：`inline_rich.py` 里 markdown 用的 media 项仍传 `has_spoiler=is_sensitive`
（4 处），实测无效 —— 真正生效的是同函数产出的 `media_blocks`。无害但误导。
