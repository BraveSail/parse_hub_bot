# bgm 讨论话题：只发一层

日期：2026-10-07

## 背景

`/group/topic/<id>` 与 `/subject/topic/<id>` 都是**讨论话题**：一个主楼加若干楼层，
一层一个 `div[id^="post_"]`。第一版把主楼加全部楼层铺出来（"还原原帖"），
被 45 层的帖子打脸——整篇塞满一条消息。用户要求改成和论坛帖子一样的做法：

> 你现在把楼层全爬出来了，改成和linux.do那种，只发一层

## 现在的行为

| 链接 | 发出 |
| --- | --- |
| `/group/topic/472394` | 主楼（`#1`）|
| `/group/topic/472394#post_4062141` | **第 2 层**，主楼作为引用块放在上面当上下文 |
| `/subject/topic/41209` | 主楼（`#1`）|
| `/subject/topic/41209#post_411463` | **第 3 层**，主楼作为引用块放在上面 |
| `/rakuen/topic/subject/41119#post_410747` | **第 4-1 层**（楼中楼），父楼作为引用块放在上面 |

形态与论坛帖子一致：引用块在前当上下文，本层正文在后。主楼的图走
`quoted_media_count` 通道进引用块内部。

引用块**整块斜体**（与其他平台同一形态），由公共 helper `format_quote_block` 产出 ——
第一版在这儿手拼了 `"> "` 前缀，结果它是唯一一个引用块不是斜体的平台
（用户报「其他平台都是斜体，这个不是」）。现在有测试把两者的输出逐行对照。
块内**不写 markdown 星号**：嵌套引用里会字面显示。

## 三个入口指向同一个话题

| 入口 | 归属 | 说明 |
| --- | --- | --- |
| `/group/topic/<id>` | 小组（`/group/<slug>`）| 规范路径 |
| `/subject/topic/<id>` | 条目（`/subject/<id>`）| 规范路径 |
| `/rakuen/topic/<归属>/<id>` | 同上 | 「超展开」列表里的入口 |

**实测它们是同一个话题**：`/rakuen/topic/subject/41119` 与 `/subject/topic/41119`
的正文**逐块相同**（11 块对 11 块）、楼层号一致、`#post_410747` 两边都在。

rakuen 版的差别：页面更精简（**16KB vs 26KB**）、只有 **1 个 `h1`**、没有
`#headerSubject` / `.comment-header`，而且它的 `#pageHeader h1` 里归属链指向
**条目**（`/subject/622288`）而不是小组 —— 直接解析它需要另写一条 header 分支。

⇒ **归一化到规范路径再抓**（`_TOPIC_APIS[归属]`），复用已验证的两条路径，零新增解析逻辑。
测试 `test_a_rakuen_link_is_fetched_from_the_canonical_path` 断言**实际请求的 URL**
是规范路径 —— 归一化最容易悄悄退化成"抓 rakuen 页"。

`/rakuen/topic/privatetopic/<id>` 实测返回 **0 字节**，不接。

## 页面形态：同构的楼层，不同的头部

**楼层结构两个页面完全一样**（所以是一条解析路径）：

| | 选择器 |
| --- | --- |
| 主楼 | `.postTopic` |
| 一级回复 | `.row_reply` |
| 楼中楼 | `.sub_reply_bg` |
| 正文容器 | `.topic_content` / `.reply_content` / `.cmt_sub_content` |
| 楼层号 + 时间 | `.post_actions small`（`#2 - 2026-10-7 00:24`；楼中楼 `#2-1`）|

**头部不一样**（所以要分派）：

- **小组话题**：`#pageHeader h1`，内容是「小组 » 讨论<br/>标题」，标题在 `<br/>` 之后
- **条目讨论版**：页面上有**两个 `h1`**——`#headerSubject` 里那个是**条目名**，
  真正的标题在 `.comment-header h1`。归属是条目（`/subject/<id>`）。

⚠️ 直接取 `h1` 会把条目名当话题标题（实测踩到）；因此 `_parse_header` 先试
`#pageHeader h1`，没有才走条目那条路。

## 楼层定位：锚点用的是**节点 id**，不是楼层号

URL 锚点 `#post_4062141` 里的数字就是楼层节点的 `id`（`post_<数字>`）。
所以 `BangumiFloor` 记了 `dom_id`，选层时**按 id 找**——楼中楼的 `#2-1`
是 bgm 自己编的显示编号，与节点 id 无关，按它找会找错。

**锚点失效时退回主楼**（楼层被删、链接手改过）：宁可少一层上下文，
也不能因为一个坏锚点整条解析失败。

## 踩到的坑

1. **引用块媒体必须在末尾**。`quoted_media_count` 的约定是「图片列表**末尾** N 张
   属于引用块」，而图片是按 DOM 顺序抽的——主楼在 `comment_list` **之前**，
   主楼的图就排在前面了。必须重排：本层的图在前，主楼的图挪到末尾。
   （测试 `test_the_quote_media_count_covers_the_opening_posts_images` 钉住。）
2. **`decompose()` 会清空内容 ⇒ 必须逆序解析**（子 → 父）。楼中楼 DOM 上嵌在
   父楼的正文容器里（`div.topic_reply_<父id>`），正序解析时父楼先把子楼清掉，
   轮到子楼只剩空壳（实测 45 个节点只解析出 38 条）。
3. **图片要在 `_simplify` 之前抽**——`_simplify` 会 `decompose()` 掉图片节点。
4. **主楼可能同时出现在 `comment_list` 里** ⇒ 按 id 排重。
