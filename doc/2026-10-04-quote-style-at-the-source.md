# 富文本渲染切到 HTML 模式（去掉 markdown 来回转换）

日期：2026-10-04 · 状态：phase0 进行中

## 为什么

当前 `InputRichMessage(markdown=...)` 是 markdown 模式，遇到两个死结，只能靠"来回转换"绕过：

1. **引用块内 markdown 行内语法不解析** —— `*斜体*` 原样输出星号。补丁是 `quote_italics_to_tags()` 把 `*行*` 转 `<i>行</i>`，再把剩余 `*` 中和成 `&#42;`
2. **expandable/引用块内真换行被并成空格、真空行让块退化成不可折叠** —— 补丁是 `use_br_linebreaks()` 把所有换行改成 `<br>`

两个补丁叠加 = 转换来转换去，顺序错了就坏（`*` 先被中和就再也匹配不上斜体）。

**用户明确要求**：「现在是html接口吗？不行就学hermes直接传entities」「转换来转换去」。

## 实测结论（真机 dump 服务端块，非推断）

`InputRichMessage` 三选一：`html` / `markdown` / `blocks`。**HTML 模式全部原生可用**：

| 需求 | HTML 写法 | 服务端块 / 实体 |
| --- | --- | --- |
| 标题 | `<h3>x</h3>` | `RichBlockSectionHeading` |
| 加粗 / 斜体 / 链接 | `<b>` / `<i>` / `<a href>` | `RichTextBold` / `RichTextItalic` / `RichTextUrl` |
| **换行 / 空行** | **真换行 / 真空行** | **原样保留（markdown 模式会被吞）** |
| 引用块 | `<blockquote>x</blockquote>` | `RichBlockBlockQuotation` |
| 折叠引用 | `<blockquote expandable>x</blockquote>` | `RichBlockExpandableBlockQuotation` |
| 折叠段落 | `<details><summary>s</summary>x</details>` | `RichBlockDetails` |
| 分割线 / 页脚 | `<hr>` / `<footer>` | `RichBlockDivider` / `RichBlockFooter` |
| 图片 | `<img src="tg://photo?id=p0">` | `RichBlockPhoto` |
| 视频 / GIF | `<video src="tg://video?id=v0">` | `RichBlockAnimation` |
| 图集 | `<tg-collage><img …><img …></tg-collage>` | `RichBlockCollage` |
| 文本里的 `<` `&` | `&lt;` `&amp;` | 正确还原 |

**不选 entities**：entities 属于普通消息（`send_message(entities=…)`），会丢掉富文本的块结构（标题 / 折叠 / details）。html 模式能同时保住块结构与行内样式。

## ⛔ 方向修正：不换 HTML 模式（2026-10-04 实测否决）

补测「正文原文格式」这一项，结论推翻了上面的判断：

| 组合 | 结果 |
| --- | --- |
| markdown 模式 + markdown 正文（现状） | 加粗/斜体/标题/列表/引用 **全部正确解析** ✓ |
| html 模式 + markdown 正文 | **全部字面显示**（`**加粗**`、`# 标题`、`- 列表` 原样）✗ |
| html 模式 + html 正文 | 加粗/斜体/标题/引用可以，但 `- 列表` 不解析 |

**正文的原文格式来自 22 个 provider 产出的 markdown**，markdown 模式靠服务端原生解析它。
切 html 模式 = 要么正文格式全丢，要么再写一个 markdown→HTML 转换器 —— **那才是真正的「转换来转换去」**。

⇒ **保持 markdown 模式**。减少转换的正解是**在源头产出正确的标记**，不是事后转换。

## 目标（修正版）

原来的两个补丁里，只有一个属于"转换"：

- ❌ `quote_italics_to_tags()`：把 `*行*` 转 `<i>行</i>` —— **这是转换，删掉**。
  源头（`parsehub/utils/helpers.py` 的 `format_quote_block`）直接产出 `> <i>行</i>`，
  4 个 provider（twitter / threads / bilibili / linuxdo）共用它，一处改全部受益。
- ✅ `use_br_linebreaks()`：块内换行拼成 `<br>` —— 这不是转换，是 **markdown 模式的固有要求**
  （expandable/引用块内的真换行会被并成空格、真空行让块退化成不可折叠），无法回避。

保留：`neutralize_markdown`（中和引用内容**自身**的 markdown 定界符，源头改动不影响它）、
`_should_fold` 阈值、`split_fold_preview`、`attach_quote_media`、折叠三形态。

## 阶段（修正版）—— 已完成

### phase0 ✅ 源头产出 `<i>`
`parsehub/utils/helpers.py` 的 `format_quote_block`：`> *行*` → `> <i>行</i>`（作者行同）。
4 个 provider（twitter / threads / bilibili / linuxdo）共用它，一处改全部受益。
验证：lib 403 passed；6 个测试文件的旧格式断言已更新。

### phase1 ✅ 删掉 bot 侧转换
删 `plugins/helpers.py` 的 `quote_italics_to_tags()` 及两处调用（`fold_quote_block`、`_strip_quote_markers`）。
验证：`grep` 无残留；bot 264 passed；`plugins/helpers.py` 净删 24 行。

### phase2 —— 不做（有意保留）
`use_br_linebreaks` 保留：它不是转换，是 markdown 模式的固有要求
（expandable 块内真换行会被并成空格、真空行让块退化成不可折叠）。合并进折叠函数没有收益。

### phase3 ✅ 全量测试 + 真机端到端
bot 264 passed / lib 403 passed / ruff 全过；已部署。
生产容器发真实推文（`x.com/VincentBounce/status/2105608886996906153`）dump：
`RichBlockExpandableBlockQuotation` 内含 `RichTextItalic × 11`，斜体/链接/mention 全部正常。

### phase4 ✅ 清旧缓存
持久缓存 `stale_after=7 天`，旧条目的 content 是 `> *行*` 旧格式，没有转换层后会显示星号。
用项目自己的 `CacheRepo.remove_by_keys` 清理：24 → 0（不裸写 SQL）。

## 结论

**保持 markdown 模式**，删除事后转换改为源头产出正确标记。这就是「不转换来转换去」的正解：
`*` 从来就不该出现在产出里 —— 引用块内 markdown 不解析是既定事实，源头就该写 `<i>`。

## 回退

`docker tag shirobakobot:before-merge shirobakobot:local`（见 deploy-and-ops）。
