# guest 的首帧：也是解析结果，不再是「解析中」占位

日期：2026-10-09 · 来源：用户「guest消息没改？？」（承接同日「消息首帧取消掉解析中」）

## 我上一轮的错误结论

同日的 `2026-10-09-first-frame-without-parsing-stage.md` 里，我把 guest 归成"技术必需"：

> guest 消息就是 inline 消息，它的 id 只有发出去之后才存在，而进度需要一个载体 —— 只能先占位。

**前半句对，结论错**。「载体只能靠发消息拿到」不等于「载体必须在解析前拿到」——
真正要建立的只是"发第一条消息"这件事，而**内容可以是解析完的那份**。
用户当场追问，说明这个结论没验证就下了。

## 现在：载体**延迟建立**

`InlineStatusReporter` 新增 ``first_send`` 回调（`plugins/parse/reporters.py`）：
第一次真要发内容（`_edit_rich` / `_edit_inline_text`）时，如果还没有载体，
就把**这条内容本身**发出去（`_ensure_carrier`），拿回的 `inline_message_id` 之后用于编辑。

```
解析（无任何消息）→ report_result(解析结果, 下载中) → first_send: answer 这条内容 ← 首帧
                  → 处理中 → … → _deliver: 编辑成最终结果（带媒体）
```

- guest 侧删掉 `_send_placeholder`（那条「解 析 中...」骨架）与 `_NullReporter`，
  改为传 `first_send=_send_first_frame`（内部 `answer_guest_query`）。
- `_answer` 末尾用 `reporter.inline_message_id` 交给 `_deliver` —— 载体是延迟建立的，
  所以由 reporter 持有（新增只读属性）。
- **缓存命中路径**同样受益：不再先发"解析中"再编辑成结果，直接 answer 结果本身。

## 与 inline 的关系

inline 的载体由 answer 阶段给出（`chosen_result.inline_message_id`，且调用点已有
"为 None 就 return"的守卫），所以 inline **不传** `first_send`，行为逐字不变。

## 代价（真实约束，不是借口）

- **解析期间群里一条消息都没有**（与私聊/群一致：用户要的就是这个形态）。
- **answer 被推迟到解析完成之后** —— 若解析耗时超过客户端等待窗口，这条 guest 消息
  可能发不出去（`first_send` 返回 None 只是"本次没有进度"，结果仍由 `_deliver` 走
  answer 直发兜底，但同样受那个窗口限制）。**这条需要真机验证**。

## 验证

- `test/test_guest_cache.py`：
  - `test_the_first_frame_is_the_parsed_layout`（**核心**）—— 跑真实 `_answer`（流水线替身
    会真的调 `reporter.report_result`），断言**第一次** `answer_guest_query` 的 markdown
    含解析到的标题/正文与页脚「下 载 中」，且**不含「解 析 中」**
  - `test_the_reporter_can_build_its_carrier_on_first_send` —— 载体在解析前不存在、但带 `first_send`
  - 旧契约的两条（占位当载体 / 无占位退回 `_NullReporter`）改造成新契约
- **反向验证**：让 `_edit_rich` 在无载体时直接 return（= 旧行为）→ 核心用例立刻变红，恢复后绿。
- bot 567 passed / lib 867 passed / `scripts/check.sh` 干净。
