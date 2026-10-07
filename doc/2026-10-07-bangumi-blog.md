# 接入 bangumi（bgm.tv）日志

日期：2026-10-07
触发：用户「https://bangumi.github.io/api/ 评估下抓取 bgm.tv 先从 https://bgm.tv/blog/381120 这个入手」
→ 评估后定案：**表情转成文本、分类标签当超链接 tags、落地**
→ 中途给了「https://bgm.tv/help/bbcode」（权威映射表，纠正了我一版错的方向）

## 结论

```
主请求 : GET https://bgm.tv/blog/<id>            → HTML（匿名可抓）
解析   : 正文是**用户写的 BBCode 渲染出来的 HTML**
范围   : 只做 /blog/<id>（小组话题、条目页另论）
```

**官方 API 不支持日志**（实测，不是文档没看全）：

- OAS spec（2026-07-24）**46 个端点**：条目/章节/角色/人物/用户收藏/搜索/修订 —— **没有 blog**
- `api.bgm.tv/v0/blogs/<id>` → **404**；`bgm.tv/blog/<id>.json` → 200 但 **0 字节**

⇒ 只能抓网页，稳定性依赖页面结构（站点改版要改选择器）。这是不可避免的代价。

## 抓取条件（实测）

- 日志页 ~22KB / **0.3s**；连打 6 次全 200、同尺寸 ⇒ **无限流**
- **无 Cloudflare**
- **图片无防盗链**：带不带 Referer 都 200 / 441KB 同尺寸 ⇒ 不用像 B 站那样注入 Referer
- ⚠️ **必须显式按 utf-8 解码**：站点不声明字符集，requests 会猜成 latin-1、标题乱码

## BBCode → HTML（权威映射，来自 `bgm.tv/help/bbcode`）

那页本身就是 bgm 的渲染器渲染的，所以是权威清单：

| BBCode | HTML |
| --- | --- |
| `[b]` / `[i]` | `<strong>` / `<em>` |
| `[u]` / `[s]` | `<span style="text-decoration:underline/line-through">` |
| `[mask]`（马赛克） | `<span style="background-color:#555; color:#555; border:1px solid #555">` |
| `[color=x]` / `[size=n]` | `<span style="color:x">` / `<span style="font-size:npt">` |
| `[url]` | `<a class="l" href>` |
| `[img]` | `<img class="code">` |
| 表情（编辑器插入） | `<img class="smile" alt="(bgm116)" src="/img/smiles/…">` |
| `[quote]`（指南没列，真实日志里有） | `<div class="quote"><q>…</q></div>` |

⚠️ **span 有 5 种 style，只能按 style 分派**：粗体是 `<strong>` **不是** span font-weight
（编辑器会另出 `font-weight:bold` 的 span，一并认）。我第一版计划只认 `font-weight`，方向错了。

## 渲染决策

| 形态 | 处理 |
| --- | --- |
| 上传图 | 抽成 media（**正文里去掉**），`requires_media_download = True` |
| 表情 | **转成它的 alt 文本**（`(bgm116)`，用户定案） |
| `[mask]` | → Telegram 原生剧透 **`\|\|文字\|\|`**（真机验证：`||x||` → `RichTextSpoiler`） |
| `[u]` / `[s]` | 保留 **HTML** 写法（markdown 没有下划线语法，markdownify 会整段丢 `<u>`） |
| `[color]` / `[size]` | 富文本没有颜色与字号 ⇒ **只留文字** |
| 链接 | 一律写 **HTML `<a>`**（项目既有结论：markdown 链接在引用块内不解析） |
| `[quote]` | → 标准 `<blockquote>`（markdownify 加 `> `） |

## 两个坑（都实测踩到）

1. **表情 ≠ 图片，判据看 src 路径**：表情是 `/img/smiles/…`，而**有些表情 img 不带 class**
   —— 只看 class 会把表情当图片发（每条带表情的日志多出一堆 gif）。
2. **作者标识有两种形态**：老用户是数字 uid（`/user/950407`），新用户是 slug（`/user/eidosair`）
   —— 正则只认数字的话，**新用户的作者名与主页整个丢失**（实测：381265/381269 一开始都是空）。

另：**日志不存在时仍是 HTTP 200**（不是 404），页面写「呜咕，出错了 数据库中没有查询到该日志的信息」
⇒ 判据是「没有 `#entry_content`」，且要与「页面改版」区分开（两条不同的报错）。

## 标签是用户级的

bgm 的日志标签挂在**作者名下**：`/user/<uid>/blog/tag/<名>`
（全站 `/blog/tag/<名>` 实测返回 0 字节空响应，指过去等于死链）。

⇒ `TAG_PAGE_URLS[BANGUMI]` 带 `{id}` 占位，`tag_page_url` / `_render_tag` / `format_tags`
支持传作者标识；**模板要 `{id}` 而拿不到时返回空串**（宁可退回纯文本，也不生成打不开的链接）。
默认参数 ⇒ 其它平台零回归。

主页模板同理：`PROFILE_URL_TEMPLATES[BANGUMI] = "https://bgm.tv/user/{id}"`，
传参要用 `user_id=`（按 `handle` 位置传会静默拼出空串 ⇒ 作者行不可点，我踩了一次）。

## 验证

**测试**（`lib/test/test_bangumi_blog.py`，25 passed；fixture 用真实页面）：
真实页面（标题/作者/时间/标签/图）、slug 作者、头像链接不抢名字、图抽走且正文不留 img、
协议相对图补 https、表情转文本且不进 media、**无 class 的表情仍要当表情**、
mask→`||…||`（含跨行压平）、下划线/删除线/粗体、color 与 size 只留文字、引用块、
链接是 HTML、无"只有空格的行"、两种错误场景可区分、域名匹配（bgm.tv / bangumi.tv）
且**不接走** group/subject、`requires_media_download`、端到端（stub 页面）。

`test/test_text_layout.py` 扩展：bgm 标签 → 用户级标签页；**拿不到作者标识时退回纯文本**；
其它平台不受 `{id}` 模板影响。

`test_author_metadata.py`：把 bangumi 加进 `FORWARD_CASES`（那个测试要求**每个平台都有
真实的作者字段用例**，光加进白名单不算）。

**生产端到端**（读回服务端块）：

```
消息 1016（数字 uid，1 图）
  RichBlockSectionHeading = 【学生会也有洞！】ep1观后感
  RichBlockParagraph = [RichTextUrl(text=RichTextBold('HuangfengXwX'), url='https://bgm.tv/user/950407'), ' ', RichTextCode('@950407')]
  RichBlockParagraph = RichTextUrl(text='#动画', url='https://bgm.tv/user/950407/blog/tag/%E5%8A%A8%E7%94%BB')
  RichBlockPhoto ×1

消息 1017（slug 作者，47 图，正文有 17 个引用块）
  RichBlockSectionHeading = 平家进军！平家进军！（二）：女子落语
  RichBlockBlockQuotation ×17           ← 引用块全在
  RichBlockCollage（4 张）+ RichBlockDetails = 🖼 展开其余 43 张图片（内含 43 张）
                                        ← 47 张 = 4 + 43，图集折叠生效
```

lib 644 / bot 508 全绿，`scripts/check.sh` 干净。commit `b8e87c9`。

## 不做（明确记录）

- **小组话题** `/group/topic/<id>`：结构不同构（正文 `.topic_content`、楼层 `.postTopic`、
  没有 `h1.title`），要另行设计 —— 所以 `__match__` 只接 `/blog/<数字>`
- **条目页** `/subject/<id>`：官方 API 已覆盖，没必要抓 HTML
- **点赞数 / 浏览量**：前端 JS 加载（`likes_grid_<id>` 与 `/like/list/blog/<id>` 都是空壳），抓不到
- **阅读时长**：平台算好的字符串，但页脚没有它的位置
