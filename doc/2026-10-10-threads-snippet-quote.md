# threads 长文块（snippet）渲染为引用块

日期：2026-10-10（接 [[2026-10-10-threads-snippet-attachment]]）

用户要求：「这个长文改成引用块」。

## 改动

**动机**：caption 那句话是"引子"（「ご参考までに添付いたします。」＝"附上供参考"），
它引出的正是下面那封长信 —— 长文以引用块呈现与语义相符，也和其它平台"被引内容 → 引用块"
的排版一致。

**形态变化**（三段顺序不变，第三段换了标记）：

```
> <i>@death3721</i>          ← 被回复帖（reply 角色）
> 关于黑客写勒索信…

ご参考までに添付いたします。   ← caption 正文

> <i>拝啓…</i>               ← 长文块（quoted 角色，本次新形态）
```

## 实现

1. **provider**（`provider_api/threads.py`）：`ThreadsPost` 拆出 `snippet` 字段 ——
   caption 与长文**分开存**（不再在 provider 里拼成一坨），渲染形态交给 parser。
   两处都过 `_apply_text_spoilers`（长文里也可能有遮罩行）。
2. **parser**（`parsers/parser/threads.py`）：
   - `snippet_quote = format_quote_block(post.snippet)`（公共 helper，整块斜体）；
   - 三段按序拼接：被回复帖引用块 → caption → 长文引用块，
     **每段各自 `strip()`**（`format_quote_block` 末尾自带空行，直接 join 会堆多余空行）；
   - `quote_roles` 按出现顺序声明 `["reply", "quoted"]` —— 渲染层据此归位媒体，
     不再靠位置猜。没有长文时 roles 里不出现 `quoted`。
3. **无需改渲染层**：`build_rich_markdown` 已有"引用块按角色归位"机制
   （`split_quote_blocks` + `render_quote_card`），长文块长了会自动进
   `<blockquote>` + `<details>` 折叠（实测 838 字符的长文被服务端折叠）。

## 验证

- 测试 `lib/test/test_threads_snippet.py` 重写为 8 条：snippet 独立字段、
  逐字完整、无 snippet 时逐字不变、null/坏结构不炸、遮罩回归、
  **长文以 `> <i>` 引用块出现在正文后**、`quote_roles == ["reply","quoted"]`、
  无长文时无引用块且 roles 为空。
- lib 909 / bot 576 全绿，ruff + pylint 通过。
- 生产容器端到端：`quote_roles: ['reply','quoted']`、长文行 `> <i>拝啓</i>`、
  最终 markdown 含 `<blockquote>` 块。

## 边界

- 长文与 caption **不再合并进同一段文字**：''snippet'' 拿不到时输出与改动前逐字一致。
- 长文本身很长（623 字）→ 渲染为折叠的 `<blockquote>`（服务端行为，与其它平台长引用一致）。
