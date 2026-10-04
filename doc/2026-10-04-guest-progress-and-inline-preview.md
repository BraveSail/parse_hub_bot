# inline 的预览 & guest 的处理过程

日期：2026-10-04 · 来源：用户「inline处理过程还是有预览，只有同群回复消息没有，还有guest消息只有finalize没有处理编辑过程」

上一轮把文本消息的 link preview 关了（见 `2026-10-04-no-link-preview.md`），
用户实测后指出**还有两处**。

## 一、inline 的进度仍带预览

`InlineStatusReporter.report()`：

```python
full = f"{self._caption}\n{text}" if self._caption else text
await self._edit_inline_text(inline_message_id=self._mid, text=full)   # ← 没传参数
```

- inline 的进度消息 = **原 caption + 进度文字**（`_caption` 是用户选中那条消息的原文）。
  caption 里带链接 → 进度消息照样挂预览。
- 只有 `report_error` 传了 `link_preview_options`，`report()` 没有。

**修法**：把默认值下沉到 `_edit_inline_text`，与 `MessageStatusReporter._edit_text` 同一位置：

```python
kwargs.setdefault("link_preview_options", _NO_PREVIEW)
```

这样**所有** inline 编辑路径（进度 / 错误 / 收尾）都覆盖到，不必逐处记得传。
`report_error` 里那处冗余的显式传参删掉（同一个值，避免两处维护）。

> `edit_inline_rich_message`（编辑成富文本）走 raw `EditInlineBotMessage`，
> raw 层没有 link preview 字段、富文本本身也不生成预览 —— 不用改。

## 二、guest 从来没有"处理过程"

### 根因（实测）

guest 的进度被设计成**在召唤消息上 reply 一条状态消息**。但 guest 模式的前提
恰恰是 **bot 不在那个群里** —— 实测：

```
get_chat_member -> 400 CHANNEL_PRIVATE (You haven't joined this channel/supergroup)
send_message    -> 400 CHANNEL_PRIVATE
```

那条 reply **从来没成功过**，异常还被 `except Exception` 吞成 debug 级
（INFO 级日志里什么都看不见），于是：

```
report() 每次都失败 → _msg 一直是 None → has_message=False
→ _deliver 走 guest 通道 → 用户只看到结果, 没有任何过程
```

**这不是环境问题，是设计缺陷**：那条通道在 guest 场景下**不可能**通。

### 载体其实一直在：guest 消息本身就是 inline 消息

pyrogram 对 `SentGuestMessage` 的定义：

> Describes an **inline message** sent by a guest bot.

字段就是 `inline_message_id`（`InputBotInlineMessageID`）—— 正是
`messages.EditInlineBotMessage` 要的类型。实测它能被 `unpack_inline_message_id`
正常解出：

```
BQAAAH8EAACb7f-02gbl1xUz2F4 -> dc_id=5 id=-5404339777845590913 access_hash=...
```

⇒ **和 inline 完全同构**，可以一路编辑。

### 实现：与 inline 同构（先占位 → 一路编辑）

```
① _send_placeholder: answer_guest_query 一条"解 析 中..."占位 -> 拿 inline_message_id
② InlineStatusReporter(cli, mid, ...) 做进度 (复用 inline 那套, 含预览关闭)
③ _deliver: edit_inline_rich_message(cli, mid, markdown=..., media=..., blocks=...)
④ 占位拿不到 / 编辑失败 -> 退回 answer_guest_query 直发 (改动前的行为)
```

- **复用 `InlineStatusReporter`**，不新写一套进度机制（统一接口）。
- **`caller_msg` 参数删掉** —— 它只为了那条永远失败的 reply。
- **敏感内容带上 `blocks`**：guest 本来就在算 `_blocks`，却从没用过，现在走与 inline
  相同的打码路径。
- **不再写 file_id 缓存**（与改动前一致）：guest 的消息在别人群里，服务端不回传
  `Message`，拿不到上传后的 file_id。这块死代码一并删掉。
  - *可改进点*：`edit_inline_rich_message` 内部是真上传（`rich.write`），
    理论上能把上传后的 document 取出来回传 file_id —— 用户没要求，先不做。

## 验证

- bot 321 passed（新增 6 条：占位 id 传给 `_deliver` / 占位失败退回 `_NullReporter` /
  编辑走 guest 消息 / 编辑失败退回 `answer_guest_query` / 无占位直接走 guest 通道）、
  lib 444 passed、`check.sh`（ruff + pylint）干净。
- **需要实测确认的两点**（代码层面已按官方文档同构实现，但服务端对"编辑 guest 消息"
  的接受度只能真机验）：
  1. inline 进度消息不再有预览卡片
  2. 群里 @bot 时能看到"解 析 中..."→ 结果（一条消息走完）
- 最坏情况 = 改动前的行为（占位或编辑失败都退回 `answer_guest_query`）。
