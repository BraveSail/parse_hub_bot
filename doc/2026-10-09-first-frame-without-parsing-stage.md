# 处理过程的首帧：不再发「解析中」

日期：2026-10-09 · 来源：用户「消息首帧取消掉解析中，直接发解析到的文字结果，然后页脚的下载中处理状态保留」

## 改动

`services/pipeline.py::_execute` 的**解析阶段不再发进度消息**：

```diff
- await self._reporter.report(self._t("解 析 中..."))
  parse_result = await self._step("解析", lambda: ps.parse(self._url))
```

于是消息序列从

```
解析中(骨架) → 下载中(完整排版) → 处理中 → … → 结果
```

变成

```
              下载中(完整排版, 首帧) → 处理中 → … → 结果
```

**首帧就是解析完的文字排版**（标题/作者/正文/标签；页脚第一段仍是「下载中」，
即用户要保留的那部分处理状态）。少了一次「发空骨架 → 再编辑成正文」的跳版。

## 为什么不是"发一条空的但别的样子的"

首帧之所以以前是骨架，是因为解析结果还没有、没东西可发。用户这次要的是**干脆不发**——
所以唯一的实现位置就是**去掉那次 `report`**，让第一次真正发送落到解析之后的
`report_result`。

| 关注点 | 结论 |
| --- | --- |
| 首帧的发送路径 | `report_result` → `reporters._send_rich`：`self._msg is None` → **新建消息**（不是编辑）。这条路径本来就有（缓存命中场景一直走它） |
| 解析失败 | `_step` 的 `report_error` → `reporters._edit_text`：`_msg is None` → 新建一条纯文本错误消息（用户仍能看到失败原因） |
| 缓存命中 / 传入 parse_result | 本来就跳过解析分支，行为不变 |
| 跳过下载那几条分支（富文本/媒体过多/纯 GIF） | 不经过 `report_result`，于是全程无进度消息 → 由 handlers 新建最终结果（`finalize` 返回 None 时回退新建，既有逻辑） |
| inline / guest | `InlineStatusReporter` 的载体由 answer 阶段建立，不受影响；`report()` 保留 |

## `report()` 还剩谁在用

| 调用点 | 用途 | 保留理由 |
| --- | --- | --- |
| `pipeline.py` singleflight 分支 | 「已有相同任务正在解析, 等待解析完成...」 | 这不是"解析中"骨架，而是**必须让用户知道在等**的提示（解析要等另一个任务结束，可能很久） |
| `plugins/parse/guest.py::_send_placeholder` | guest 的 answer 占位 | **技术必需**：guest 消息就是 inline 消息，它的 id 只有发出去之后才存在，而进度需要一个载体 —— 只能先占位。占位内容就是第一帧处理过程（否则后面每次更新都跳版） |

⇒ `build_progress_markdown(parse_result=None)` 的骨架分支**不能删**，它支撑上面两处。

## 验证

- `test/test_pipeline_richtext.py` 新增两条（跑真实 pipeline，只把 `ParseService` 换成替身）：
  - `test_the_parse_stage_sends_no_progress_message` —— 解析阶段一个 `report` 都没有
  - `test_the_first_message_is_the_parsed_text_with_the_download_stage` —— 首个通知是
    `report_result("下 载 中...")`，且阶段推进（≥2 次 result）还在
- **反向验证**：把 `report("解 析 中...")` 加回去 → 两条用例立刻变红（已实测），恢复后绿。
- bot 566 passed / lib 867 passed / `scripts/check.sh`（ruff + pylint）干净。
