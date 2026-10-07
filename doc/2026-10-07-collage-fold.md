# 图集超过 4 张折进按钮

日期：2026-10-07
触发：用户「https://www.threads.com/@nichi_studio/post/DeKDRoXEoi9 这种图片超过4张的也按钮折叠一下」

## 真机取证（动手前）

这条帖子 **13 张图**，全部一次铺在正文里（图集）。

折叠能不能装图，先发两条测试消息实测服务端返回的块：

```
<details> + 3 张独立图    → RichBlockDetails 内含 3 × RichBlockPhoto   ✓
<details> + <tg-collage>  → RichBlockDetails 内含 RichBlockCollage     ✓
外层 <tg-collage> + details 并存                                       ✓
```

⇒ 折叠装图**可行**，不必新造机制：markdown 路径由服务端原生解析；blocks 路径
（打码/引用卡）由 `rich_blocks.py` 的 details 分支递归处理内层，一行没改。

## 改法

`plugins/helpers.py`

- 常量 `_COLLAGE_FOLD_THRESHOLD = 4`（用户的 4，有测试钉住）
- 抽出 `_collage()`；`wrap_collage(placeholders, *, fold_summary="")` 超阈值且给了
  摘要时切成「前 4 张图集 + `<details>` 折其余图集」
- `build_rich_markdown` 里生成摘要（`t_[lang](f"🖼 展开其余 {count} 张图片")`）

### 两个设计决定

- **摘要里的张数先赋给局部变量再进 f-string**：i18n 的 key 取的是占位符**源码**，
  直接写 `{len(media_placeholders) - _COLLAGE_FOLD_THRESHOLD}` 会把整串表达式写进键名。
- **`fold_summary` 为空就不折**：这是"已经在折叠块里"的调用方保持原样的方式 ——
  引用卡片的媒体（`attach_quote_media` / `render_folded_quote_card`）与手动打码的整组内容
  都不传摘要，**避免嵌套折叠**（客户端对嵌套没有保证）。

## i18n

新词条源文本 `🖼 展开其余 {count} 张图片`，key `23d39ae9e4f8`
（`Text.id_of`，动手前先用既有词条 `展开全文` → `f1d192b54c3b` 对照验证了算法）。
本机无 `OPENAI_API_KEY` ⇒ 手工插 16 个 yaml（按 12 位 hex 字符串序、文本级插入），
diff 恰好「16 files changed, 16 insertions」。

体检：用守卫 translator 跑 `i18n.build` → `Content unchanged`（同时证明无缺、无余）。

## 验证

生产实样（消息 1003）的服务端块结构：

```
正文段落 × 3
RichBlockDetails = '展开全文'            ← 正文长文折叠（既有行为，不受影响）
RichBlockCollage → 4 × RichBlockPhoto   ← 前 4 张留外面
RichBlockDetails = '🖼 展开其余 9 张图片'  ← 其余 9 张
  RichBlockCollage（9 块）
```

13 张 = 4 张外露 + 9 张折叠，一张不少；两个折叠按钮**互相独立**（正文一个、图集一个），
没有嵌套。

测试 `test/test_render_collage_fold.py`（13 passed）：阈值两侧（4 张不折 / 5 张折）、
13 张一张不丢、摘要写对张数、摘要为空不折、引用卡片不折、打码不嵌套。
lib 587 / bot 487 全绿，`scripts/check.sh` 干净。commit `772d3b4`。
