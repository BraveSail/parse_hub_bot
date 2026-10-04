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

## 引用块的折叠形态：整块一个 expandable

被回复/被引用的卡片**原先根本没走折叠**：它们在 `build_rich_markdown` 里直接拼进 `parts`，
只有正文经过 `format_text` —— 于是 1294 字的回复块整屏铺开（用户报「回复没折叠」）。

第一次修用了 details + 预览，用户反馈「**引用块被按钮分割, 割裂感太强了**」——
预览行 / 按钮 / 折起部分被切成三段。**引用块改回老的 `<blockquote expandable>`**（整块一起折，
客户端自己显示开头几行），`fold_quote_block()` 即此形态。

### `<br>` 是让 expandable 可用的关键

`<blockquote expandable>` 直接放真换行有两个死穴，实测：

| 块内内容 | 结果 |
| --- | --- |
| 真换行 | 换行被**并成空格**（所有行挤成一行） |
| 真空行 | **退化成普通引用块**，完全不折叠 |
| 无空行 + `<br>` | `RichBlockExpandableBlockQuotation`，换行**保留** ✓ |
| 含空行 + `<br><br>` | `RichBlockExpandableBlockQuotation`，空行**也保留** ✓ |

所以块内换行一律写成 `<br>`（`use_br_linebreaks()`）—— 两个死穴一起解决。

另两个细节：
- 折叠时**只剥 `>` 前缀，不做 markdown 中和**（区别于 `convert_markdown_quote`）：
  引用块内容本身就是 `*斜体*` 和 `<a href>`，中和会渲染成字面实体（`&#42;`）。
- 引用块里嵌 `<details>` 会被服务端忽略；`<details>` 包住引用块虽然有效，但形态就是上面被否掉的割裂感。

真机块序列（不含 details）：

```
块0 Paragraph                        作者行
块1 RichBlockExpandableBlockQuotation  整块折叠 (内部换行保留)
块2 Paragraph                        正文
块3 Divider
块4 Footer
```

## 顺带修掉的两处

1. **截断默认值反转**：`format_text` 的 `max_length` 默认由 `1000` 改为 `None`（不截断）。
   隐式截断会被每个新调用方继承，「忘了传」的代价是静默丢内容 —— 有损操作必须 opt-in。
   仅**结果文字当媒体 caption 发**的路径显式传 `_CAPTION_MAX_LENGTH=1000`（`send_raw` / `send_zip`）。
2. **guest 进度反馈**：guest 用的是丢弃全部进度的 reporter，群里只看到结果突然出现。
   改为在召唤消息上 reply 一条状态消息、最后编辑成结果（与私聊/群自动解析一致）。

## 验证

- bot 255 passed / 库 403 passed；ruff 全过。
- 本地：三种 locale 摘要正确（`展开全文` / `全文を表示` / `Show full text`），多段落长正文生成 details 结构。
- 生产容器真机发送真实推文：
  - 正文折叠：`Paragraph`(预览) + `RichBlockDetails`(收起, 内 5 段) + `Footer`。
  - 引用块折叠：整块 `RichBlockExpandableBlockQuotation`（换行以 `<br>` 写入，服务端保留 `\n`/`\n\n`）。
- bot 262 passed。

## 教训

- **判折叠看服务端块类型，不看字符串。** markdown 里有 `<blockquote expandable>` 不代表会折叠。
- 估长度影响必须**两档都测**（阈值两侧 + 截断线以上）：只测 250 字恰好落在截断线以下，会得出假的「已修好」。
- 探针 session 放独立目录 + `no_updates=True`，绝不碰 bot 的 session 文件（曾导致 bot 不消费更新）。
