# 引用块跑到标签行下面：标签行落位的假设不成立

日期：2026-10-08
触发：用户分享 `https://linux.do/t/topic/2987481` 并问「引用为啥跑 tags 下面的」

## 现象

该主题（主楼 #1，tags = 纯水/人工智能/ChatGPT/OpenAI/Claude）渲染成：

```
正文…
<details><summary>展开全文</summary>…</details>

#纯水 #人工智能 #ChatGPT #OpenAI #Claude      ← 标签行
> 这里叠个甲，在不考虑 Anthropic 封号的情况下对比…   ← 引用块
---
<footer>2026年10月6日 17:12 · 5,141 查看 · 63 点赞</footer>
```

引用块是**楼主自己正文的最后一段**（Discourse cooked 里的 `blockquote`），原文里它就在正文末尾，
排在标签之前。渲染后却被标签行挤到了下面。

## 根因

`plugins/helpers.py::build_rich_markdown` 里 `parts` 的落位顺序：

| 行 | 内容 |
| --- | --- |
| ~446 | 正文 |
| **~452** | **标签行** |
| ~455 | 媒体（图集） |
| ~477 | 末尾引用卡片（`quote` / `declared_tail`） |

标签行的注释写的是「标签是正文的收尾: 放在正文之后」，但实现只挂在**正文**之后 ——
它的隐含假设是"正文之后 == 内容末尾"。而正文末尾的引用块会被 `split_quote_blocks`
**抽出来做成卡片**、在更后面（~477）才落位 ⇒ 标签行插到了卡片前面。

引用块被抽出来单独落位本身是对的（卡片要折叠、要按角色分配媒体），问题只在标签行的位置。

这是 `split_quote_blocks` 的按位置分块（开头块 = 被回复、末尾块 = 被引用）与"标签收尾"
两个设计的交叉：三个平台（twitter / threads / bilibili）的引用卡片来自解析层单独产出，
正文里没有块，所以一直没暴露；linux.do（正文自带 blockquote）与任何"正文末尾有引用块"
的内容才会撞上。

## 改动

`plugins/helpers.py`：把标签行的落位从**正文之后**移到**所有内容之后**
（正文 → 媒体 → 引用卡片 → 标签行）。

注释同步改写，写清为什么不能挂在正文后面。

## 验证

- 新增 `test/test_text_layout.py::test_tags_sit_after_a_trailing_quote_card`：
  构造"正文 + 末尾引用块 + tags"的结果，断言 `正文 < 引用块 < 标签行`。
- **反向验证**：临时把标签行改回旧位置 → 该用例失败（`assert 98 < 54`：引用块在 98、
  标签在 54，标签抢在前面 —— 正是用户报的现象）；恢复后全绿。
- **真实数据复验**（161 容器内，用 importlib 加载补丁版 helpers 对同一 topic 渲染）：

  | | 正文 | 引用块 | 标签行 | 实际顺序 |
  | --- | --- | --- | --- | --- |
  | 旧实现 | 152 | 992 | 718 | 正文 → **标签行** → 引用块 ✗ |
  | 补丁后 | 152 | 703 | 762 | 正文 → **引用块** → 标签行 ✓ |

- bot 侧全量 `uv run pytest test/` → **548 passed**；`scripts/check.sh`（ruff + pylint --errors-only）干净。

## 影响面

所有带 `tags` 的平台（linux.do / pixiv / bilibili / x / bgm）。仅当内容末尾还有
**媒体或引用卡片**时观感才会变（标签移到它们后面）；纯文字 + 标签的内容顺序不变。

`test_text_layout.py::test_tags_sit_after_the_body` 断言的是"正文 < 标签"，改动后依然成立。
