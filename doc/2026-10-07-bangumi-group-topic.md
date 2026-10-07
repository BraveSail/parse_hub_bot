# bgm.tv 小组话题

日期：2026-10-07
触发：用户「https://bgm.tv/group/topic/472394 支持一下」（承接上一轮的日志支持）

## 与日志不同构（所以是两条解析路径）

| | 日志 `/blog/<id>` | 小组话题 `/group/topic/<id>` |
| --- | --- | --- |
| 标题 | `.header h1.title` | `h1` 里 `<br/>` **之后**（前面是「小组 » 讨论」） |
| 正文 | `#entry_content` | 主楼 `.topic_content` / 回复 `.reply_content` / 楼中楼 `.cmt_sub_content` |
| 结构 | 单篇 | 主楼 + 楼层（45 层不分页） |
| 归属 | 作者自己的日志标签 | 小组（`/group/<slug>`） |

共用的是 **BBCode → markdown 转换**（bgm 两个页面都是同一套 BBCode 渲染器出 HTML）
⇒ 把那三个函数从 `BangumiBlog` 提成模块级函数（`_simplify` / `_to_markdown` / `_extract_images`），
两个类共用，零重复。

## 楼层结构（取证）

```
主楼   : div.postTopic          → 正文 div.topic_content
一级回复: div.row_reply          → 正文 div.reply_content
楼中楼  : div.sub_reply_bg       → 正文 div.cmt_sub_content
          ⚠️ **DOM 上嵌在父楼的正文容器里**（div.topic_reply_<父id>），不是兄弟节点
楼层号  : .post_actions small 的文本，形如 `#2 - 2026-10-7 00:24`（楼中楼是 `#2-1`）
```

## 三个坑（都实测踩到）

1. **`decompose()` 会清空内容 ⇒ 必须逆序解析**
   取父楼正文时要摘掉嵌在里面的楼中楼，用的是 `decompose()` —— 它会**清空被摘节点的内容**。
   正序解析时父楼先把子楼清掉，轮到子楼就只剩空壳（实测：45 个节点只解析出 38 条，
   7 条楼中楼全空）。**先收集节点、再逆序（子 → 父）解析**才对。

2. **主楼可能同时出现在 `comment_list` 里 ⇒ 要按 id 排重**
   （抽 fixture 时我把它放进了 `comment_list`，于是主楼进两次：第一层当正文、第二层又当楼层
   出现。）代码加了按 `id` 排重，两种页面结构都稳。

3. **图片要在解析楼层之前抽**
   解析会 `decompose()` 掉嵌在父楼正文里的楼中楼节点 —— 那之后楼中楼里的图在树上已不存在。

## 形态（用户已看实样）

```
# 真的有人能看过3000+部番吗            ← 渲染层标题 + 作者行（带 #1）

**补旧番** » 讨论 · 45 层              ← 小组归属 + 层数（正文第一行）
最近视奸了一圈路人的主页…              ← 主楼正文

---                                    ← 区分「话题」与「大家怎么回」

**大夜宵** @dayexiao520 · #2 · 2026-10-7 00:24    ← 一级楼层（markdown 粗体）
两年四百+少了…

> <i>irohard @1175849 · #2-1 · 2026-10-7 00:28</i>   ← 楼中楼（引用块 + HTML 斜体）
> 经常看到你在新番里评论…
```

- **整篇只折一次**（既有机制，实测读回是 `RichBlockDetails | 展开全文` 包住所有楼中楼）
- 楼层超过 `MAX_FLOORS = 60` 只渲染前 60 层，末尾注明还剩多少（数据层面仍全解析）

## 引用块里的粗体：必须用 HTML

**症状**（读回服务端块）：楼中楼第一段是 `[ "**", {RichTextUrl…} ]` —— 两个星号成了正文。

**原因**：bgm 自己在楼中楼里插「某人 说: …」的引用（原文 `<strong>`），markdownify 转成
`**名字**`，而它落在**嵌套**引用里 —— 嵌套引用里的 markdown 星号**不被解析**。

（实测对照：**单层**引用块里的 `**` 是能渲染成 `RichTextBold` 的 —— 所以这不是
"引用块内一律不解析"，而是嵌套那一层。保守起见**一律用 HTML**，任何深度都生效。）

**改动**：`_BgMarkdownConverter.convert_strong` → `<b>`、`convert_em` → `<i>`；
楼中楼作者行用 `<i>`（与 linux.do 引用块的作者行同一写法），一级楼层仍用 `**`（段落内生效）。

**真机验证** `<b>`/`<i>` 生效（探针消息）：段落里 → `RichTextBold` / `RichTextItalic`；
引用块里 → 同样 ✓。

## 验证

测试 `lib/test/test_bangumi_group_topic.py`（26 passed，fixture = 真实话题抽最小子集）：

- 标题在 `<br/>` 之后、小组名与链接、主楼 = `#1`（作者/时间都取自它自己的楼层头）
- 楼层按序、主楼不在 `floors` 里、楼中楼标 `is_sub`
- **楼中楼正文不含父楼文字**（钉住逆序解析那条）
- **引用块里没有字面 `**`**；一级楼层仍用 markdown 粗体
- 图从任意楼层抽（含楼中楼）、表情转文本、正文不留 img
- 截断（>60 层显示前 60 + 注明）、两种错误场景可区分
- 匹配：`group/topic` ✓、`blog` ✓、`subject` ✗、`group/<slug>` ✗
- 端到端（stub 页面）→ `position_label="#1"`、作者主页、`requires_media_download`

`test_bangumi_blog.py` 的两条断言按新行为更新（粗体现在输出 `<b>`；`group/topic` 现在支持）。

**生产实样**（读回服务端块）：

```
标题 RichBlockSectionHeading → 作者行 → Divider → 小组行 → 主楼正文
→ Divider → 楼层头/正文（各一段）→ RichBlockDetails('展开全文') 内含 BlockQuotation ×N
含字面双星号 = False
```

lib 670 / bot 508 全绿，`scripts/check.sh` 干净。commits `4019b3d`（实现）、`0ebde42`（粗体修）。
