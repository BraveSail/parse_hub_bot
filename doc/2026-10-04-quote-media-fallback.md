# 引用块的图片不见了：媒体归属的兜底

日期：2026-10-04 · 症状：分享 linux.do 楼层，主楼的图不显示

## 现象与定位

用户实测后报「没有图」。日志显示图**下载并处理了**：

```
05:37:04 [Pipeline] 开始下载: media_count=1, url=https://linux.do/t/topic/2943678/3
05:37:05 [Pipeline] 流水线完成: processed_count=1
```

下载了、处理了，**发出去没有** —— 说明丢在渲染层。

## 根因

`split_quote_blocks(content)` 按**位置**分块，不是按语义：

| 引用块位置 | 归类 | 媒体计数 |
| --- | --- | --- |
| 正文**前面** | `reply_quote`（被回复块） | `reply_media_count` |
| 正文**后面** | `quote`（被引用块） | `quoted_media_count` |

linux.do 把主楼引用块放在**最上面**（用户要求的阅读顺序）→ 归到 `reply_quote`，**`quote` 为空**。

而主楼的图算在 `quoted_media_count` 里 → 分给末尾引用块 → 那个块不存在：

```python
render_quote_card("", ["![](tg://photo?id=m0)"])   # → []   媒体被丢弃
```

`render_quote_card` 第一行 `if not quote: return []` —— 媒体**静默消失**。

## 修复

`build_rich_markdown` 里补齐**双向兜底**（原来只有 reply→quote 一个方向）：

| 情况 | 媒体去向 |
| --- | --- |
| `quote` 空、`reply_quote` 有 | → 头部引用块 |
| `quote` 空、`reply_quote` 也空 | → 正文媒体 |
| `reply_quote` 空、`quote` 有 | → 末尾引用块（原有） |
| 都没有 | → 正文媒体（原有） |

**任何路径都不再可能静默丢媒体。**

## 验证

- bot 291 passed（新增 3 条：兜底给头部块 / 两个块都没有时兜给正文 / 有末尾块时不兜底回归）、lib 435 passed、check.sh（ruff + pylint）全绿。
- **服务端块证据**（真机发送）：

```
块1: RichBlockBlockQuotation  内=['RichBlockParagraph', 'RichBlockPhoto']  ← 图在引用块内
```

- 渲染只改 bot 侧（消费 content 现场渲染）→ **不需要清缓存**（判据见 deploy 文档）。

## 教训

**加法端和减法端要对账**：provider 按「媒体总数 + 引用块计数」把媒体切片，
bot 侧按「引用块在正文前/后」消费 —— 两边对不上就会丢东西。

只验证「媒体数量对不对」不够，还要确认**渲染时那个块真的存在**。
判据：`RichBlockBlockQuotation.blocks` 里能否看到 `RichBlockPhoto`。
