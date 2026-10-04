# inline 的「原链接」按钮：摘键盘的调用点被误删

日期：2026-10-04 · 来源：用户「inline最开始占位消息下面有个链接按钮，删掉他」

## 背景：这个按钮是**故意挂的**，但要立刻摘掉

内联结果上挂键盘**不是为了给用户点**，而是为了拿 `inline_message_id`：

> Bot API 对 `ChosenInlineResult.inline_message_id` 的定义 ——
> *"Available only if there is an inline keyboard attached to the message."*

而句柄是**二次编辑**（把占位消息换成带媒体的完整富文本）的前提。所以约定一直是
**「挂上 → 拿到句柄 → 立刻摘掉」**，用户不该看到按钮。

摘键盘的手法本身**验证过**（commit `5ea89db`，真机实测）：

| 手法 | 结果 |
| --- | --- |
| `Ikm([])` → 序列化成 `ReplyInlineMarkup(rows=[])` | ✗ `400 REPLY_MARKUP_INVALID` |
| **`raw.types.ReplyKeyboardHide()`** | ✓ 清掉（Bot API 传空 `inline_keyboard` 映射的也是这个类型） |

⇒ `_drop_inline_keyboard()` 里用的就是 `ReplyKeyboardHide`，机制没问题。

## 根因：重构时把**调用点**删了

```
8d96874  fix(inline): restore the keyboard, remove it once the result is sent
         → 加了 _drop_inline_keyboard + 在 inline_result_download 里调用
5ea89db  → 把 Ikm([]) 换成 ReplyKeyboardHide (真机验证过)
fcc8120  feat(inline): replace the media results with the placeholder-and-edit flow
         → 重写 inline_result_download 时, **调用连同那行注释一起被删掉**
```

于是 `_drop_inline_keyboard` 变成**死代码**：函数还在、日志还在，但**没人调**。
没有任何测试会发现 —— 摘键盘是纯副作用，删掉了不会让任何断言变红，
表现只是"按钮一直留在消息上"。

## 修法

1. **恢复调用**（`inline_result_download` 里，`inline_message_id is None` 判断之后）：

```python
# 键盘只为换取 inline_message_id 而存在 (Telegram 只在消息带 inline keyboard 时
# 才回传句柄), 到手就立刻摘掉 —— 用户不该看到那个"原链接"按钮。
await _drop_inline_keyboard(cli, inline_message_id)
```

2. **失败日志 debug → warning**：摘不掉 = 用户会一直看到按钮，不是可以埋掉的噪音
   （之前正是因为 debug 级，这类问题在 INFO 日志里完全不可见）。

3. **补回归测试**（`test/test_inline_rich.py`）：

- `test_the_keyboard_is_dropped_once_the_handle_is_available`
- `test_no_handle_means_no_keyboard_to_drop`（无句柄 = 本来没挂键盘，不该尝试摘）

**并且验证过测试有效**：临时把调用改回 `pass` → 测试变红；恢复 → 变绿。
（"删掉不会红"正是这个 bug 上次溜过去的原因，所以这条测试必须自证能红。）

## 验证

- bot 323 passed（+2）、`check.sh`（ruff + pylint）干净
- 容器里确认新代码已生效（`/app/plugins/parse/inline.py:303` 有调用）
- **需用户实测**：inline 选中一条**带媒体**的结果，看"原链接"按钮是否立刻消失
  （机制在 `5ea89db` 已真机验证，这次只是把调用接回去）

## 备注：guest 不受影响

guest 的占位结果**不挂键盘**，它的 `inline_message_id` 来自
`answer_guest_query` 的**返回值**（`SentGuestMessage.inline_message_id`），
不需要靠键盘换 —— 所以那条路径没有按钮问题。
