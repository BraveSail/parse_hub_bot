# 缓存把富文本存成了纯文本

日期：2026-10-07
触发：用户「https://linux.do/t/topic/2977838/16?u=libc.so.6 缓存丢格式了」

## 取证（同一条内容，两条渲染路径逐字 diff）

```
现场（24 行）: **<a href=".../u/HatsuneMiku">Angel</a>** <code>@HatsuneMiku</code> · #16
               > <i><a href=".../u/tophnanfong">Only Linux Can Do(OLCD)</a> <code>@tophnanfong</code> · #1</i>
               >
               > ---
               >
               > <i>国产手机芯片恐成最大赢家？</i>

缓存（18 行）: **<a href=".../u/HatsuneMiku">Angel</a>** <code>@HatsuneMiku</code>
               Only Linux Can Do(OLCD) @tophnanfong · #1
               国产手机芯片恐成最大赢家？
```

引用块塌成裸文字，链接与楼层号一起没了。

## 根因（三层，缺一不成立）

1. `RichTextParseResult.content` 是**派生属性** = `plaintext_content`
   —— 由 `markdown_content` 经 `md_to_html` + BeautifulSoup **转成纯文本**；
2. `rich_cache_entry` 存的是 `parse_result.content` ⇒ **缓存里躺的就是纯文本**，
   富文本结构在**写入时**就已经不存在了；
3. 渲染层 `rich_content()` 的判据是 `isinstance(parse_result, RichTextParseResult)`，
   而缓存路径喂的是 duck-type `_RichFields` —— **永远走 `content` 分支**，
   于是连 `markdown_content` 参数都轮不到用（第一版只补参数是不够的，就是这个原因）。

⇒ 影响**所有 RichText 平台**（linux.do、微博、discourse 类）。**第一次发是对的**
（走现场路径），第二次（命中缓存）才塌 —— 所以藏了很久。

**同类漏传**（同一根因：两条渲染路径的字段不一致，一并修）：

- `position_label`（楼层号）—— 缓存路径完全没有；
- `hashtags`（标签实体）—— `_RichFields` 没有，命中缓存时标签**退回正则**，
  twitter/微博 这类靠实体才对的平台会切错边界。

## 改动

| 文件 | 内容 |
| --- | --- |
| `services/cache.py` | `CacheParseResult` 加 `markdown_content` / `position_label` / `hashtags`；`get()` 加判据：**老条目缺 `markdown_content` 键 → 视为未命中** |
| `plugins/parse/inline_rich.py` | `rich_cache_entry` 写入三个字段；`build_cached_rich_content` 传给渲染入口 |
| `plugins/helpers.py` | `build_rich_markdown_by_str` 加三个参数；`_RichFields` 加对应属性；`rich_content()` 判据从 **isinstance** 改成 **"有没有 markdown 源"** |

### 为什么老条目必须作废

只改代码不作废，用户已有的缓存条目里**存的就是纯文本**，第二次发仍然是塌的。
判据沿用既有的 `model_fields_set` 模式（与 `published_at` / `tags` / `quoted_media_count`
那些一样），自动 miss 并重解析、覆盖写入，用户不需要手动清缓存。

### 为什么判据要落在字段上

`rich_content()` 原判据依赖对象类型，而缓存路径是 duck-type。
**判据落在"字段有没有"上，两条路径才能给出同一个答案** —— 这是这类 bug 的通用教训。

## 验证

**测试** `test/test_cached_rich_rendering.py`（10 passed）：

- **主判据：两条路径渲染结果逐字相等**（任何渲染字段漏传都会在这里炸，
  比逐个字段断言可靠得多）
- 引用块（`>`、`<a>`、分割线）经缓存不塌
- 缓存里存的是 **markdown 源**，不是 `content`（对照断言：`content` 里没有 `>`）
- 楼层号、标签实体经缓存保留
- 空 markdown → 退回 content（非 RichText 平台不回归）
- 老条目（缺键）→ miss；当前格式条目 → 命中

**冷热实测**（生产容器，同一条链接发两次）：

```
冷（清缓存后第一次）: position_label='#16'  缓存命中=False  发送 = True
热（命中缓存第二次）: position_label='#16'  缓存命中=True
  缓存里的 markdown_content 前 60 = '> <i><a href="https://linux.do/u/tophnanfong">Only Linux Can'
两次渲染逐字相同 = True
```

lib 618 / bot 504 全绿，`scripts/check.sh` 干净。commit `5eb6cb7`。
