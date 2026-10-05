# 作者行：名字做链接，@用户名 降为等宽下角标

日期：2026-10-05
来源：用户「把作者名 @用户名 改成 作者名超链接，@用户名弄成 下角标」
　　　+ 「这个下角标能不能调成灰色就是等宽那种，然后不可点击，中间隔个空格」
　　　+ 「D 然后取消冒号」

## 目标形态

```
**<a href="…">ミルクセーキ🔞</a> <sub><code>@MirukuSeki3</code></sub>**

————————————            ← 作者行与正文之间是页脚那种分割线

正文…
```

- **显示名可点**（链接指向主页）；`@handle` 只是标识，压成小字附在后面。
- **名字与 `@handle` 之间空一格**。
- **没有冒号**。

## 为什么是 `<sub><code>…</code></sub>`（三层都有理由，全部真机实测）

| 写法 | 下角标 | 等宽 | 可点击 | 结论 |
| --- | --- | --- | --- | --- |
| `<sub>@handle</sub>` | ✓ | ✗ | **✓** | ✗ 仍可点 |
| `<code>@handle</code>` | ✗ | ✓ | ✗ | ✗ 没下角标 |
| **`<sub><code>@handle</code></sub>`** | ✓ | ✓ | ✗ | **✓ 全中** |
| `` `@handle` ``（反引号） | ✗ | ✓ | ✗ | ✗ 没下角标 |
| `<tg-sub>…</tg-sub>` / `~handle~` | ✗ | ✗ | — | 服务端不认 |

**关键**：Telegram 会把裸 `@名字` **自动识别成 Mention 实体（可点击）** ——
只套 `<sub>` 仍然可点，**必须用 `<code>` 包住**它才变成普通等宽文本。
`<sub>` 负责压小，`<code>` 负责"纯文本 + 等宽"。

**关于"灰色"**：富文本**没有直接指定颜色的写法**。等宽（`<code>`）是客户端里
最接近你说的那种样式，实际观感随客户端主题（有的主题就是灰底等宽）。

## 落点

1. `lib/src/parsehub/utils/helpers.py` 的 `format_author_link`
   —— 所有平台的作者行/引用块署名都走它（改一处全平台生效）。
2. `plugins/parse/rich_blocks.py` 的 `parse_inline` 增加 `<sub>` / `<sup>` / `<code>`
   —— **blocks 路径必须自己认**（markdown 路径由服务端解析；敏感内容只走 blocks）。
3. 去掉冒号：`plugins/helpers.py` 的 `format_author_line`（作者行）
   与 lib 的 `format_quote_block`（引用块署名，保持一致）。

## 验证

- lib **453 passed**、bot **341 passed**、`check.sh` 干净（多条断言按新格式更新）。
- **真机 · 两条渲染路径**：

```
普通内容 (markdown):  **<a href="…/BArchiveWarfare">BlueArchive: Kivotos Warfare</a> <sub><code>@BArchiveWarfare</code></sub>**
  服务端块含 Subscript/Code/Url ✓
R18 敏感 (blocks):    **<a href="…/MirukuSeki3">ミルクセーキ🔞</a> <sub><code>@MirukuSeki3</code></sub>**
  服务端块含 Subscript/Code/Url ✓
```

## 边界

- **只有一个名字**（没有 `@handle`，或两者相同）时，那一个仍做成链接 —— 否则整行不可点。
  两者相同时沿用既有约定显示 `@handle` 形态（不因这次改动改语义）。
- 没有主页地址时退回纯文本（`format_author_label`）。
- **旧缓存条目**里的作者行是旧格式 —— 需重新解析才更新。
