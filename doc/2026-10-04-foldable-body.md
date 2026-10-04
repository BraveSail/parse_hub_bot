# 长正文折叠：容器从 `<blockquote expandable>` 换成 `<details>`

日期：2026-10-04 · 影响：富文本渲染（正文 / 引用块 / 折叠文案）

## 症状

用户报「没折叠啊，直接省略号了」，修掉截断后又报「还是没折叠」。
生产链路自检是好的：正文 1239 字 / 12 行 → 生成的 markdown 里确实带 `<blockquote expandable>`，
折叠阈值（200）也生效。**字符串里写着折叠标签，消息却折不起来。**

## 根因

Telegram 富文本解析器对 `<blockquote expandable>` 的处理**依赖块内容**。真机发送并 dump 服务端块类型：

| 折叠块内容 | 服务端块类型 | 结果 |
| --- | --- | --- |
| 单行长文本 | `RichBlockExpandableBlockQuotation` | 折叠 ✓ |
| 两段之间有空行 | `RichBlockBlockQuotation` | **不折叠** ✗ |
| 多段、单换行连接 | `RichBlockExpandableBlockQuotation` | 折叠，但换行被并成**空格**，段落结构全丢 |

推文正文天然是多段落（段间空行）—— 于是**长正文永远折不起来**，且即使没退化，段落也会被压成一行。
同一标签 `**作者：**` 在前、`### 标题` 在前、`<a href>` 作者链接在前都不影响结论，唯一变量就是块内空行。

## 修法

折叠容器改用 `<details><summary>…</summary>…</details>` —— markdown 模式下**唯一能折叠多段落**的容器
（caption 路径早就用它渲染长内容）。真机验证：

```
RichBlockDetails   摘要='展开全文'   is_open=False（默认收起 = 折叠）
  内部 6 个独立段落全部保留 ✓
```

- `_render_foldable(content, *, summary="")` → `<details><summary>{summary}</summary>\n\n{content}\n\n</details>`；
  `format_text` / `convert_markdown_quote` / `build_caption_by_str` / `build_caption` 逐层透传 `fold_summary`。
- 摘要文案按 locale 传：`t_[lang]("展开全文")`，16 个 locale 全补（键 = `md5(源文)[:12]`，字典序插入）。
- 渲染层不自取 locale，保持纯函数。

## 折叠态必须留预览

`<details>` 收起时**只显示 summary**，所以按上面修完，用户看到的是光秃秃一个「展开全文」，
正文一个字都没有（用户原话：「这个折叠看不到一点内容啊」）。

`split_fold_preview()` 把开头几行**留在折叠块外**当预览（`_FOLD_PREVIEW_LINES=2` / `_FOLD_PREVIEW_CHARS=100`，
任一触顶即停），只把剩余部分折起来：

```
**@Waifunomics：**

When you realize the reason Blue Archive is a normie repellent isn't because of lolis …   ← 折叠块外, 可见

<details><summary>展开全文</summary>
…（其余 5 段）
</details>
```

真机返回的块序列：`Paragraph`(作者行) → `Paragraph`(预览) → `RichBlockDetails`(收起, 内 5 段) → `Divider` → `Footer`。

细节：整条正文挤在一行时行切不出来，会**永远折不起来** —— 这种情况按字符切出预览。

## 顺带修掉的两处

1. **截断默认值反转**：`format_text` 的 `max_length` 默认由 `1000` 改为 `None`（不截断）。
   隐式截断会被每个新调用方继承，「忘了传」的代价是静默丢内容 —— 有损操作必须 opt-in。
   仅**结果文字当媒体 caption 发**的路径显式传 `_CAPTION_MAX_LENGTH=1000`（`send_raw` / `send_zip`）。
2. **guest 进度反馈**：guest 用的是丢弃全部进度的 reporter，群里只看到结果突然出现。
   改为在召唤消息上 reply 一条状态消息、最后编辑成结果（与私聊/群自动解析一致）。

## 验证

- bot 255 passed / 库 403 passed；ruff 全过。
- 本地：三种 locale 摘要正确（`展开全文` / `全文を表示` / `Show full text`），多段落长正文生成 details 结构。
- 生产容器真机发送真实推文：`Paragraph`(预览) + `RichBlockDetails`(收起, 内 5 段) + `Footer`。

## 教训

- **判折叠看服务端块类型，不看字符串。** markdown 里有 `<blockquote expandable>` 不代表会折叠。
- 估长度影响必须**两档都测**（阈值两侧 + 截断线以上）：只测 250 字恰好落在截断线以下，会得出假的「已修好」。
- 探针 session 放独立目录 + `no_updates=True`，绝不碰 bot 的 session 文件（曾导致 bot 不消费更新）。
