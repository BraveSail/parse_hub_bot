# 文本消息一律关掉链接预览

日期：2026-10-04 · 来源：用户「处理过程占位消息把preview关了，不然还是能看到图」

## 问题

文本消息里带链接时，Telegram 会在消息**下方**挂一张 **link preview 卡片**。
卡片在正文之外 —— **折叠块盖不住它**：正文被 `#nsfw` 藏起来了，卡片上的封面图照样看得见。

真机对照（同一个 YouTube 链接）：

```
文本消息，不传 link_preview_options  ->  web_page=有   ✗
文本消息，传 is_disabled=True        ->  web_page=无   ✓
富文本消息（F/G/H 三种写法）          ->  web_page=无   ✓
```

⇒ 泄露**只来自文本消息路径**；富文本（rich message）本身不生成预览。

## 根因：三条文本路径都没关

| 路径 | 原来的行为 |
| --- | --- |
| 进度更新 `MessageStatusReporter._edit_text` | 发送/编辑都不传参数 → 允许预览 |
| 纯文本结果 `finalize_text`（`handlers._post_text` 调用） | **最容易中招**：文本结果里就有来源链接 |
| 最终富文本 `finalize` | 编辑时不传 → 保持原值（可能把前面留下的预览带着走） |

只有 `report_error` 一处显式关了。

## 修法

**① 三处显式关掉**（`plugins/parse/reporters.py`）：

- `_edit_text`：`kwargs.setdefault("link_preview_options", _NO_PREVIEW)`
  —— 用 `setdefault` 而不是直接赋值，`report_error` 已经显式传了，不能冲突。
- `finalize_text`：同样 setdefault（**这条是用户看到的那个**）。
- `finalize`：富文本也给上，免得编辑时保留旧预览。

新增模块常量 `_NO_PREVIEW = LinkPreviewOptions(is_disabled=True)`。

**② 更深的一处：`MessageSender.text()` 默认改为禁用**（`plugins/parse/sender.py`）。

它所有调用点发的都是**提示**或**结果**（handlers 的"不支持的平台"、GIF 过多提示、
状态消息），**没有一处需要预览**。所以默认值取安全的一侧：

```python
if link_preview_options is None:
    link_preview_options = LinkPreviewOptions(is_disabled=True)
```

- **显式判 `None`、不用 `or`**：调用方传什么都该被尊重（`or` 会被假值对象吃掉）。
- **默认值就是接口**：忘了传参数的调用点不该泄露内容。这与项目里"截断这类有损操作必须
  opt-in、默认应当完整渲染"是同一条原则的反面 —— **会泄露的默认值也是错的默认值**。
- `text_no_preview()` 保留（现在等价于默认行为）。

## 验证

- 真机三条路径（含链接）：`MessageSender.text` / `finalize_text` / `report` 全部 `web_page=无` ✓
- 对照实验证明不传参数时**确实有** preview（不是"链接恰好没预览"）
- bot 316 passed（新增 5 条：进度 / 收尾 / 文本结果 / 发送默认 / 显式传参不被覆盖）、
  `check.sh`（ruff + pylint）干净

## 备注

用户**自己发的那条消息**（含链接）也会被 Telegram 生成预览，那不是 bot 的消息、
改不了 —— 如果那条也要遮，得靠 `auto_delete_url` 之类的删除策略。
本次修的是**机器人发出的消息**。
