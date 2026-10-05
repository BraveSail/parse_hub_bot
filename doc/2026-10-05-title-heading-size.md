# 帖子标题的字号：`###` → `#`

日期：2026-10-05
触发：用户「标题没用标题字号吗？」

## 根因（真机取证）

这个 API 的标题块 **`RichBlockSectionHeading.size` 有 6 级，1 最大、6 最小**
（pyrogram 文档原文：*"Relative size of the text font, 1-6. 1 is the largest, 6 is the smallest."*）。

我们写的是 `### {title}` ⇒ **size 3**；而 discourse 帖子正文里的 `# 小节` 是 **size 1**。

读回某 linux.do 帖的真实块：

| 块 | 改前 | 改后 |
| --- | --- | --- |
| 帖子标题 | size **3** | size **1** |
| 正文 `#` 小节 | size 1 | size 1 |
| 正文 `##` 小节 | size 2 | size 2 |

⇒ 改前**标题比它自己的正文小节还小**，所以看着不像标题。

## 改法

`plugins/helpers.py::build_rich_markdown` 的标题从 `### ` 改成 `# `（size 1）。

**这是可用范围内的上限**：raw 层确实有 `PageBlockTitle` / `PageBlockSubtitle`，
但 pyrogram 的 `RichBlock` 对外类型里没有它们，markdown 也没有对应语法 —— 所以
`#`（一级标题）就是能做到的最大字号。

blocks 路径（敏感内容走它）**无需改动**：`markdown_to_blocks` 一直按 `#` 的个数当 size
（`InputRichBlockSectionHeading(..., len(hashes))`），与服务端映射一致，改源头即两条路径同步。

## 验证

- 单测：标题渲染为 `# 标题`（且不是 `##`，避免又小于正文小节）；两条路径的 size 映射一致
  （`#` → 1、`###` → 3）。bot 423 passed、lib 484 passed、`check.sh` 干净。
- 真机：改后同一条 linux.do 帖的标题块实测 **size=1**（改前 3）。
