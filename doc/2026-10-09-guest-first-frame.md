# guest 的首帧：answer 解析结果 → 拿句柄 → 编辑（与 inline 同构）

日期：2026-10-09 · 来源：用户「guest消息没改？？」→「什么载体，你直接学inline发首帧然后编辑不行吗？」

## 结论

用户说得对。guest 就该是 **answer 首帧 → 拿句柄 → 编辑**，与 inline 同构 ——
inline 是"回调给句柄 → 编辑"，guest 只是句柄来源不同（自己 answer 出来的那条消息）。
不该为它造"载体"这种抽象。

## 我走过的两段弯路

1. **第一次**：把 guest 判成"技术必需先发占位"（"id 只有发出去之后才存在"）。错 ——
   不必在**解析前**建立：`answer_guest_query` 的返回值（`SentGuestMessage.inline_message_id`，
   底层 `SetBotGuestChatResult` 返回 `InputBotInlineMessageID`）就是可编辑句柄。
2. **第二次**：改成"reporter 内部延迟建立载体"（`first_send` 回调 + `_ensure_carrier` + 只读属性）。
   能跑，但把"发首帧"塞进了 reporter，还多一层回调注入 —— 而 inline 那边从来是
   "句柄由调用方拿到再传进来"。用户一眼看出这层封装多余。

## 现行实现（`plugins/parse/guest.py::_answer`，五步全显式）

```
① 缓存命中 → answer 结果本身（没有后续编辑，不需要句柄）
② parse_result = await service.parse(url)        ← 先拿到内容
③ mid = await _send_first_frame(...)             ← answer 首帧 = 解析到的文字排版（页脚「下载中」）
④ reporter = InlineStatusReporter(cli, mid, …)   ← 与 inline 完全同构：只负责编辑
⑤ ParsePipeline(…, parse_result=parse_result)    ← 下载/处理，一路编辑 → _deliver 编辑成结果
```

- **reporter 回归"只接受已有句柄"**：`first_send` / `_ensure_carrier` / `inline_message_id`
  属性全部删除，`__init__` 的 `inline_message_id` 又是必传的 `str`。
- **解析只做一次**：结果用 `parse_result=` 传给流水线（它内部跳过解析）。
- **不闪版**：首帧 markdown 与流水线第一次 `report_result("下 载 中...")` 逐字相同
  （都走 `build_progress_markdown(parse_result, progress=…)`）⇒ reporter 判重直接跳过那次编辑。
- **解析失败**：没有可编辑句柄，单独 `answer_guest_query` 一条错误文本（`config.hide_error` 时静默）。
- **首帧发不出去**（拿不到句柄）：`_NullReporter`，结果仍由 `_deliver` 走 answer 兜底发出。

## 与 inline 的对称

| | 句柄来源 | 编辑它的人 |
| --- | --- | --- |
| inline | 回调 `chosen_result.inline_message_id` | `InlineStatusReporter` |
| guest | 自己 answer 首帧的 `SentGuestMessage.inline_message_id` | **同一个** `InlineStatusReporter` |

## 遗留的真实约束

- 解析期间群里一条消息都没有（用户要的就是这个形态）。
- **answer 被推迟到解析完成之后** —— 若解析耗时超过客户端等待窗口，这条 guest 消息可能发不出去
  （首帧失败时结果仍走 answer 兜底，但同样受那个窗口限制）。**需要真机验证**。

## 验证

- `test/test_guest_cache.py`（12 条）：
  - `test_the_first_frame_is_the_parsed_layout` —— 第一次 `answer_guest_query` 的 markdown
    含解析到的标题/正文与页脚「下 载 中」，**不含「解 析 中」**
  - `test_the_first_frame_is_sent_before_the_pipeline_starts` —— 事件顺序必须是
    `["answer", "pipeline"]`（先发首帧拿句柄，再跑下载）
  - `test_the_pipeline_gets_the_already_parsed_result` —— 解析不重复
  - `test_without_a_handle_the_pipeline_gets_a_null_reporter` —— 拿不到句柄时退回静默
  - `test_the_parse_step_hands_the_handle_to_the_reporter` —— reporter 收到的就是首帧那个句柄
- **反向验证**：把首帧内容换回 `build_progress_markdown(None, …)`（旧的骨架行为）→
  `test_the_first_frame_is_the_parsed_layout` 立刻变红，恢复后绿。
- bot 571 passed / lib 867 passed / `scripts/check.sh`（ruff + pylint）干净。
