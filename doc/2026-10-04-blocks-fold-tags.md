# blocks 路径不认识折叠标签（手动折叠 + 自动遮罩同时坏的根因）

日期：2026-10-04
来源：用户「tag手动折叠还是坏的」「手动折叠的 自动遮罩没工作」

## 现象（用户贴的原文）

```
ミルクセーキ🔞 @MirukuSeki3：
<details><summary>⚠️ #不可以色色</summary>
</details>
2026年10月1日 19:48 · 138,460 查看 · 21,380 点赞 · 来源（Twitter）
```

看起来是"手动折叠没生效 + 自动遮罩没工作"**两个问题**，实际**一个根因**。

## 根因

`markdown_to_blocks`（blocks 路径的转换器）**不认识 `<details>`**：

```
### 修复前, 用户那条 (#不可以色色 + is_sensitive) 转换出的块:
B| InputRichBlockParagraph        ← 字面 "<details><summary>⚠️ #不可以色色</summary>"
B| InputRichBlockParagraph
B| SpoilerPhotoBlock spoiler=True ← **媒体被排到折叠外面!**
B| InputRichBlockParagraph        ← 字面 "</details>"
```

- 标签不被识别 → 变成普通段落 → **字面显示**；
- 媒体占位符**不在折叠块内** → 掉到外面 → **看起来"手动折叠时自动遮罩没工作"**。

而**只有敏感内容**走 blocks 路径（markdown 路径的媒体块打不了码）——
所以"手动折叠 + 敏感"这个组合才暴露，普通内容走 markdown 由服务端解析，一直正常。

## 修法

`markdown_to_blocks` 增加两个折叠容器：

| 源标签 | 目标块 |
| --- | --- |
| `<details><summary>S</summary>…</details>` | `InputRichBlockDetails(parse_inline(S), …)` |
| `<blockquote expandable>…</blockquote>` | `InputRichBlockExpandableBlockQuotation(…)` |

**细节**：
- details 的内容**递归转换** (`markdown_to_blocks(body, media_blocks=…)`) ——
  媒体占位符必须换成对应的（打码）块，且**留在折叠块内**。
- 支持单行与跨行两种写法；`<summary>` 取出来单独当摘要，不能带标签。
- `blockquote expandable` 块内的 `<br>` 要转回**真换行**（源 markdown 里用 `<br>` 是因为
  真换行会被服务端并成空格）。

## 验证

- bot **334 passed**（新增 3 条：details 解析 / **折叠内的媒体仍在折叠内且保留打码** /
  expandable 引用块）、`check.sh` 干净。
- **真机 · 用户那条 URL 的真实缓存条目 + `#不可以色色`**：

```
修复后转换: InputRichBlockParagraph → InputRichBlockDetails(摘要='⚠️ #不可以色色')
              → SpoilerPhotoBlock(折叠内)
服务端实际: RichBlockParagraph → RichBlockDetails 收起=True
              → RichBlockPhoto has_spoiler=True        ← 折叠 + 打码同时成立
            → RichBlockDivider → RichBlockFooter
```

## 这一类 bug 的通用规律（两次踩同一个）

> **markdown 路径由服务端解析的标签，blocks 路径必须自己认。**

已踩两次：
1. `<tg-time>`（时间实体）→ 敏感内容的页脚显示裸标签；
2. `<details>` / `<blockquote expandable>`（折叠容器）→ 折叠失效 + **媒体掉出折叠**。

⇒ **往正文/页脚新增任何标签语法时，两条路径都要过一遍**（`plugins/parse/rich_blocks.py`
的 `markdown_to_blocks` / `_INLINE_PATTERNS` 是 blocks 侧的清单）。
只验证 markdown 路径 = 敏感内容出问题，而敏感内容是**少数**，所以很容易漏到线上。
