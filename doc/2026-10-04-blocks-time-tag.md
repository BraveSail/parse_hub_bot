# blocks 路径必须自己认富文本标签（`<tg-time>` 字面显示的根因）

日期：2026-10-04
来源：用户「成功了，但是 `<tg-time unix="…">2026年10月1日 19:48</tg-time>` · 138,460 查看 · …」

## 问题

敏感内容（`is_sensitive=True`）走 **blocks 路径**，页脚里那段时间戳**字面显示**。

## 根因

时间实体是 `<tg-time unix="…" format="…">文字</tg-time>`：

| 路径 | 谁解析这个标签 |
| --- | --- |
| **markdown**（`InputRichMessageMarkdown`） | **Telegram 服务端** —— 自动解析成 `RichTextDateTime` |
| **blocks**（自己构造 `InputRichBlockFooter`） | **必须我们自己的转换器认** |

而 `plugins/parse/rich_blocks.py` 的 `_INLINE_PATTERNS` 只认
**链接 / 粗体 / 斜体 / 行内代码 / 删除线** —— `<tg-time>` 不在其中，于是
**原样进了 footer 块**，服务端收到的是纯文本 → 用户看到裸标签。

**为什么只有敏感推文出问题**：只有敏感内容走 blocks 路径。

### 这是我的漏测

时间戳那次改动（`6ebb5a2`）**只验证了 markdown 路径**，没有验证 blocks 路径 ——
而项目里"两条路径都要过一遍"的原则早就写在文档里（footer 的 HTML 链接就是
因此加进 `parse_inline` 的）。

## 修法

`parse_inline` 增加时间戳模式 → `RichTextDateTime`：

```python
_TIME_TAG_RE = re.compile(r'<tg-time\s+unix="(\d+)"(?:\s+format="([^"]*)")?\s*>(.*?)</tg-time>', re.S)
_INLINE_PATTERNS = [
    (re.compile(r'<a\s+href="([^"]+)"\s*>(.*?)</a>', re.S), RichTextUrl),
    (_TIME_TAG_RE, RichTextDateTime),     # ← 新增
    ...
]
```

特判分支（构造参数与其它行内类型不同：要 `datetime` + `format`）：

```python
if cls is RichTextDateTime:
    stamp = datetime.fromtimestamp(int(m.group(1)), tz=UTC)
    parts.append(RichTextDateTime(parse_inline(label), stamp, m.group(2) or None))
```

**原则**：**markdown 路径交给服务端解析的标签，blocks 路径必须自己认**。

## 验证

- bot 327 passed（新增 2 条：时间实体解析 / 时间实体与后续链接各自独立解析）、
  `check.sh` 干净。
- **真机 · 敏感推文走 blocks**：

```
块 = [Paragraph, RichBlockPhoto, Divider, Footer]
Footer = [★时间实体★ date=2026-10-01 11:48:31 fmt=Dt, 文字" · 138,516 查看 · 21,388 点赞 · ", 链接]
媒体块 has_spoiler=True
```

## ⚠️ 同一轮里我犯的诊断错误（值得记住）

排查时我用 `getattr(block, "spoiler", None)` 读打码标记，一直读到 `None`，
据此**误判"R18 自动遮罩被我的改动弄坏了"**，还顺着这个错误方向查了很久。

**正确的字段名是 `has_spoiler`**（块上的字段，不是 `spoiler`）。
读对之后：`has_spoiler=True` —— **遮罩一直是好的**。

教训：**探针读字段名之前先核实**（print 出对象的所有字段，而不是凭印象取名字）。
一个读错的字段会造出一个不存在的故障，并把排查带向完全错误的方向。
