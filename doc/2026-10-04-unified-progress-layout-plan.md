# 处理过程消息统一成最终排版（富文本）

日期：2026-10-04
触发：用户「处理过程的消息能不能和最终消息保持一致？现在格式还是老格式，只有最终消息是新的，
而且群里自动触发的只有处理中这三个字，只有inline有格式，能不能统一一下啊」

## 现状（已核实）

| 入口 | 处理过程 | 最终结果 |
| --- | --- | --- |
| 私聊/群自动 | `msg.reply("<b>▎解 析 中...</b>")` → 纯文本，只有一片小字 | **富文本** |
| inline | `edit_inline_text(caption + "<b>▎…</b>")` → 带上原文（所以"看起来有格式"） | **富文本** |
| guest | 同 inline（刚统一成 `InlineStatusReporter`） | **富文本** |

- 全部三个入口的处理过程都走 `StatusReporter.report(text)` —— **只吃纯文本**。
- 阶段文案：`解 析 中...` / `下 载 中...` / `处 理 中...` / `上 传 中...`（`format_label` 包成
  `<b>▎…</b>`）。
- 所以从"处理过程"到"结果"有一次**排版突变**：小字 → 标题/正文/标签/页脚。

## 目标

**从第一秒到最后都是同一种排版**：内容逐步充实，而不是格式跳变。

- 阶段1（解析中）：还没结果 → 给**同版式的骨架**（进度行 + footer 来源链接）。
- 阶段2+（下载/处理/上传）：**已有 `parse_result`** → 渲染**完整排版、无媒体**
  （标题/作者/正文/标签/引用块/页脚全部就位），只差媒体。
- 最终：同版式 + 媒体（编辑进去）。

⇒ 视觉上就是"正文先出来，图随后出现"，不再有格式突变。

## 关键设计

### `StatusReporter` 增加一个方法

```python
class StatusReporter(Protocol):
    async def report(self, text: str) -> None: ...              # 阶段文案（无结果）→ 骨架富文本
    async def report_result(self, parse_result, text: str) -> None: ...   # 有结果 → 完整排版(无媒体)
    async def report_error(self, stage, error) -> None: ...
    async def dismiss(self) -> None: ...
```

**为什么不让 pipeline 传 markdown**：渲染逻辑在 `plugins/helpers.py`，而 reporter 就在
`plugins/parse/reporters.py` —— **同层，直接 import 即可**，不必把 markdown 甩过 services 边界。

### reporter 需要的上下文（构造时注入）

- `config` / `_t`（已有）
- **`raw_url` + `platform`**：骨架阶段的 footer 来源要用
- **`spoiler_tag`**：⚠️ **必须贯穿处理过程**，否则"下载中"就把该遮的内容露出来了
- `custom_content`（用户自定义内容）

### 判重必须换掉

`_edit_text` 现在靠 `if self._msg.text != text` 判重。富文本消息的 `.text` 为空 →
判重失效、每次都比不相等。改成 reporter 自己记**上次渲染的内容签名**。

### 频率控制（新增，避免 FloodWait）

`PipelineProgressCallback` 会**按下载进度频繁调用** `report()`。文本编辑很轻，
富文本编辑要重渲染 + 发更大的 payload。所以：

- reporter 内部加**最小编辑间隔**（~1.5s），间隔内的进度更新丢弃；
- **阶段切换（`report_result`）永远立即发**，保证用户看到阶段推进。

## phase0 — 接口与共用渲染

产物：`services/pipeline.py` 的 `StatusReporter` 加 `report_result`；
`plugins/parse/reporters.py` 加共用渲染函数 `render_progress_markdown(...)`。
验证：离线单测（骨架含 footer；有结果时与 `build_rich_markdown` 输出同版式、无媒体占位）。

## phase1 — `MessageStatusReporter` 走富文本

产物：`report` / `report_result` 都发/编辑**富文本**（首条用 `MessageSender.rich_message`）。
验证：单测断言 `rich_message` 被传、判重用签名、间隔节流生效。

## phase2 — `InlineStatusReporter` 走富文本

产物：同上，走 `edit_inline_rich_message`；`caption` 参数退役（不再拼老 caption）。
验证：单测。

## phase3 — pipeline 调用点

产物：解析完成后改调 `report_result(parse_result, <阶段文案>)`。
验证：单测（`_execute` 在有结果后用 report_result）。

## phase4 — 各入口注入上下文

产物：handlers / inline / guest 构造 reporter 时传 `raw_url` / `platform` / `spoiler_tag` / `custom_content`。
验证：单测 + 真机。

## phase5 — 真机验证

产物：三个入口各发一次，dump 服务端块。
验证：处理过程的消息块类型是富文本（`RichBlockSectionHeading` / `RichBlockParagraph` /
`RichBlockFooter`），与最终结果**同版式**；打码在处理过程就生效。

## 风险与边界

- **raw / zip 模式**：最终是**文件消息**（Telegram 不允许文本编辑成文件），本质上无法与处理过程
  一致。处理过程仍按富文本做（各入口统一），这类路径的最终形态例外。
- **缓存直发**：一次发完、不经处理过程，不受影响。
- **富文本编辑丢媒体**：处理过程没有媒体（媒体在上传完成后才进 rich_message），不冲突。
- **进度百分比**：进正文，但受节流；阶段切换必发。
