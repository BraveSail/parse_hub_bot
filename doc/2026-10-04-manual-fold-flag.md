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

## 实现（4 处）

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

- bot 302 passed（新增 15 条：标记解析 / 与纯链接判定共存 / 渲染 / 不留预览 / 引用块不受影响 /
  短正文不误折）、lib 435 passed、`check.sh`（ruff + pylint）干净。
- **真机服务端块**：

```
不带 /s:  Paragraph ×3 + Photo
带 /s:    Paragraph + RichBlockDetails(摘要='⚠️', 收起=True, 内 2 段) + Photo
```

## 附带查明的 `has_spoiler` 用法

- **普通消息**：`send_photo(path, has_spoiler=True)` / `InputMediaPhoto(media, has_spoiler=True)` ✓
- **富文本**：只有 `blocks` 路径有效 —— `InputRichMessage(blocks=[SpoilerPhotoBlock(InputMediaPhoto(path), spoiler=True)])`
- **项目里是自动的**：平台标记敏感（linux.do 的 NSFW / twitter 的敏感）→
  `sender.py:550`、`inline.py:345` 检测到 `is_sensitive` 且有媒体就自动切 blocks 路径，
  不需要手动传。

**一处冗余**（未改）：`inline_rich.py` 里 markdown 用的 media 项仍传 `has_spoiler=is_sensitive`
（4 处），实测无效 —— 真正生效的是同函数产出的 `media_blocks`。无害但误导。
