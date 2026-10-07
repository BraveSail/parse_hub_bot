# fixtures —— 原始响应快照溯源

本目录下的平台响应快照由 `hashtag/话题结构化字段` 排查时落盘，**只读抓取，未改动任何生产代码/配置**。

| 文件 | 平台 | 抓取时间 (CST) | 对应请求 | 原始大小 | 备注 |
|---|---|---|---|---|---|
| `douyin_text_extra.json` | 抖音 | 2026-10-06 21:04 | 移动端签名接口 `/aweme/v1/aweme/detail/`，aweme_id `7692999234357906715`（源 URL `https://www.douyin.com/video/7692999234357906715`） | ~71 KB | 完整响应，未裁字段；web 端 `aweme/v1/web/aweme/detail/` 因生产无 douyin cookie 抓不到 |
| `kuaishou_tags.json` | 快手 | 2026-10-06 21:04 | `POST https://www.kuaishou.com/graphql`，`operationName=visionVideoDetail`，photoId `3xdmz2xiuaw7kkg`（源 URL `https://www.kuaishou.com/short-video/3xdmz2xiuaw7kkg`） | ~1 KB | 完整响应；生产未配 kuaishou cookie，走库里内置 COOKIE 兜底 |
| `xhs_tag_list.json` | 小红书 | 2026-10-06 21:07 | 笔记页匿名抓取的 `window.__INITIAL_STATE__`（`lib/src/parsehub/provider_api/xhs.py` 解析的那个对象） | ~19 KB | **未拿到带 tagList 的真实笔记**：匿名被重定向到 `/login` 或 `/404`，`note.noteDetailMap == {}`；生产未配 xhs cookie |
| `xhs_tag_list_reference.json` | 小红书 | —— | 非本次抓取 | ~2 KB | 外部**真实响应**参考（yt-dlp extractor 的真实用例 + Apify rednote-scraper 文档样本），标注字段路径与取值 |
| `bilibili_dynamic_feed.json` | B站动态 | 2026-10-06 21:03 | 动态详情 API（`modules.module_dynamic`），topic id `152946` | ~8 KB | 承载 `module_dynamic.topic = {id, jump_url, name}` 实证 |
| `bilibili_dynamic_forward.json` | B站动态 | 2026-10-06 21:03 | 转发型动态同 API | ~10 KB | 承载 `desc.rich_text_nodes[]`（`RICH_TEXT_NODE_TYPE_TOPIC` 节点自带 `jump_url`）实证 |
| `weibo_topic_struct.json` | 微博 | 2026-10-06 21:04 | 微博详情 API | ~36 KB | 正文里标签**已是 `<a href="//s.weibo.com/weibo?q=...">#话题#</a>` 锚点**；另有 `topic_struct[].topic_url` |
| `weibo_annotations_vs_topic_struct.json` | 微博 | 2026-10-06 21:04 | 同 API（另一条） | ~31 KB | 证 `annotations` 是杂项元数据（shooting/client_mblogid），**不是**话题 |
| `threads_post.json` | threads | 2026-10-06 21:05 | GraphQL `POST_DOC_ID=27419285281047858`（带 cookie） | ~149 KB | 承载 `text_post_app_info.tag_header`（整帖唯一 topic tag）实证 |
| `threads_post_inline_hashtags.json` | threads | 2026-10-06 21:05 | 同 API（正文含多个 #） | ~14 KB | 证正文 hashtag **只有纯文本**（单个 plaintext fragment，无实体） |
| `threads_login_required.json` | threads | 2026-10-06 21:05 | 同 API（**匿名**） | 119 B | 401 `require_login` 原始响应（匿名会被拒） |
| `instagram_caption.json` | instagram | 2026-10-06 21:05 | `xdt_api__v1__media__shortcode__web_info`（匿名） | ~44 KB | caption 含 `#PeakyBlinders`，证 **只有文本、无实体** |
| `instagram_post.json` | instagram | 2026-10-06 21:05 | 同 API（匿名，另一条） | ~48 KB | 对照：caption 无 hashtag |
| `instagram_cookie_execution_error.json` | instagram | 2026-10-06 21:05 | 同 API（**带 cookie**） | 263 B | `execution error` + `data:null`（IG 带 cookie 反而失败，匿名可用） |
| `bangumi_group_topic_472394.html` | bgm.tv | 2026-10-07 17:10 | 小组话题 HTML `https://bgm.tv/group/topic/472394`（匿名） | 10 KB（抽过：h1 + 主楼 + 前 5 层，含 4 条楼中楼） | 承载**楼层结构**实证：`.postTopic` / `.row_reply` / `.sub_reply_bg`，楼中楼**嵌在父楼正文容器内** |
| `bangumi_blog_381120.html` | bgm.tv | 2026-10-07 16:20 | 日志页 HTML `https://bgm.tv/blog/381120`（匿名） | 1.4 KB（抽过：只留 `.author`/`.header`/`#entry_content`） | 作者是**数字 uid**（`/user/950407`），有 1 张图与 1 个标签 |
| `bangumi_blog_381269.html` | bgm.tv | 2026-10-07 16:20 | 日志页 HTML `https://bgm.tv/blog/381269`（匿名） | 1.5 KB（同上抽法） | 作者是**用户名 slug**（`/user/air_chika`）—— 两种标识形态都要收 |
| `linuxdo_floor_24.json` | linux.do | 2026-10-07 10:40 | 带楼层号的帖子 JSON `/t/2989140/24.json`（源 URL `https://linux.do/t/topic/2989140/24`，匿名请求） | ~54 KB | 承载**楼层窗口**实证：窗口 `19..38`，**不含主楼**（对比 `linuxdo_floor_4.json` 窗口 `1..20` 含主楼） |
| `linuxdo_floor_4.json` | linux.do | 2026-10-07 10:15 | 带楼层号的帖子 JSON `/t/2989140/4.json`（源 URL `https://linux.do/t/topic/2989140/4`，匿名请求） | ~56 KB | 承载**楼层号**实证：`post_stream.posts[].post_number` / `reply_to_post_number`（第 4 楼 `reply_to_post_number=null` = 回复主楼） |
| `twitter_poll_card.json` | twitter | 2026-10-07 06:39 | 推文详情（`TweetResultByRestId`），id `2107576143285219799`（源 URL `https://twitter.com/thsottiaux/status/2107576143285219799`） | ~3.6 KB | 承载**投票卡**实证：`node["card"].legacy.name == "poll2choice_text_only"`、`binding_values` 里的 `choice{N}_label`/`choice{N}_count` |

说明：每个平台原始响应仅追加了一个 `_provenance` 键（仿 `douyin_video.json` 的 `_comment` 约定），**原有字段一个未删未改名**。
`xhs_tag_list_reference.json` 明确标注为外部参考，不是本机抓取。

**字段路径速查**（改代码时直接看这里，不用重新抓）：

- **能给出精确标签边界**（可弃用正则）：
  - twitter — `legacy.entities.hashtags[].text`（长推文另加 `note_tweet...entity_set.hashtags[]`）；**已上线**
  - B站动态 — `modules.module_dynamic.desc.rich_text_nodes[]`，节点 `type == "RICH_TEXT_NODE_TYPE_TOPIC"`，节点自带 `jump_url`（协议相对，要补 `https:`）；另有 `modules.module_dynamic.topic`。**已上线**
  - 微博 — 正文 `text` 里标签**已经是 `<a href="//s.weibo.com/weibo?q=%23...%23">#话题#</a>`**（服务端给的锚点，href 就是话题页）。**已上线**
- **有实体但不完整 / 无偏移**（只能部分替代）：
  - 抖音 — `aweme_detail.text_extra[].{hashtag_name, hashtag_id, start, end}`，`desc[start:end] == '#<hashtag_name>'`；**但实测 5 个 `#` 只收录 4 个**，`cha_list` 更少 ⇒ 仍要正则兜底
  - 快手 — `data.visionVideoDetail.tags[].{type, name}`（1:1 但**无偏移**、顺序不保证）；**主路径走 HTML `__APOLLO_STATE__` 里没有 tags**，只有 API 兜底分支有且现被丢弃
- **只有文本、拿不到实体**（正则不能弃）：
  - threads — 正文 hashtag 在 `caption.text` / 单个 plaintext fragment 里；`text_post_app_info.tag_header` 是**整帖唯一的话题**（另一回事，不是正文 hashtag 实体）
  - instagram — `caption` 只有 `{text, pk, has_translation, created_at}`
- **待验证**：小红书 — `note.noteDetailMap[<id>].note.tagList[].name`（与正文 `#[名][话题]#` 1:1 同序），但**需 cookie**；本机匿名拿不到（快照里 `noteDetailMap == {}`）

**其它字段速查**（非标签）：

- **bgm.tv 日志**（官方 API **没有日志**：`/v0/blogs/<id>` 404、`/blog/<id>.json` 0 字节 ⇒ 抓 HTML）：
  - 标题 `.header h1.title`、正文 `#entry_content`、时间 `.header .time`（`2026-10-4 19:13 · 1 分钟阅读`）
  - 作者 `.author` → `/user/<标识>`；**标识两种形态**：老用户数字 uid、新用户 slug
  - 标签 `.header .tags .badge_tag` → 标签页是**用户级**的 `/user/<uid>/blog/tag/<名>`
    （全站 `/blog/tag/<名>` 返回 0 字节空响应）
  - **日志不存在也返回 HTTP 200**，页面写「呜咕，出错了 数据库中没有查询到该日志的信息」
    ⇒ 判据是「没有 `#entry_content`」
  - 正文是 **BBCode 渲染**出来的，映射以 `bgm.tv/help/bbcode` 为准：
    `[b]`→`<strong>`、`[i]`→`<em>`、`[u]`/`[s]`/`[mask]`/`[color]`/`[size]`→**都是 `<span style=…>`**
    （只靠 style 区分！）、`[url]`→`<a class="l">`、`[img]`→`<img class="code">`
  - **表情**是 `<img class="smile" alt="(bgm116)" src="/img/smiles/…">` —— 判据看 **src 路径**
    （有表情 img 不带 class）
- **bgm.tv 小组话题**（同一套 BBCode 渲染，但页面不同构）：

  | | 日志 `/blog/<id>` | 小组话题 `/group/topic/<id>` |
  | --- | --- | --- |
  | 标题 | `.header h1.title` | `h1` 里 `<br/>` **之后**（前面是「小组 » 讨论」）|
  | 正文 | `#entry_content` | `.topic_content` / `.reply_content` / `.cmt_sub_content` |
  | 结构 | 单篇 | 主楼 + 楼层（一层一个 `div[id^=post_]`）|

  楼层号与时间同在一个 `.post_actions small` 里（`#2 - 2026-10-7 00:24`；楼中楼 `#2-1`）；
  楼中楼**嵌在父楼的正文容器里**（`div.topic_reply_<父id>`）⇒ 摘它用 `decompose()`
  会清空内容，**必须先收集节点再逆序解析**；图片也要在解析楼层**之前**抽。

- **linux.do 楼层号** — 在 `post_stream.posts[].post_number`（主楼是 `1`）；
  `reply_to_post_number` 是**被回复的楼层**（`null` = 回复主题/主楼）。
- **linux.do 楼层窗口**（关键，踩过）— 带楼层号的 URL（`/t/<id>/<n>.json`）返回**以该层为中心的
  固定 20 层窗口**，起点 = `max(1, n - 5)`：

  | 分享的楼层 | 窗口 | 含主楼 |
  | --- | --- | --- |
  | 1 ~ 6 楼 | `1..20` | **是** |
  | 7 楼 | `2..21` | 否 |
  | 24 楼 | `19..38` | 否 |
  | 30 楼 | `25..44` | 否 |

  ⇒ **n ≥ 7 时主楼不在窗口里**，必须单独补取。被回复的楼层同理可能在窗口外。
  渲染上**当前楼层**用结果的 `position_label`（`#N`），**引用块里的其它层**
  由 `_post_to_quote` 自己拼 ` · #N`。

- **linux.do 取楼层：主请求用主题端点，缺的层按 id 取** ——

  | 用途 | 请求 | 数据 |
  | --- | --- | --- |
  | **主请求** | `/t/<topic_id>.json` | 主题元数据 + `stream` + **前 20 层**（主楼总在内） |
  | 缺的层 | `posts.json?post_ids[]=<id>` | 只回那一层，**4.5KB** |
  | ⛔ 不要用 | `/t/<id>/<n>.json` | 以该层为中心的 20 层窗口（50KB+，**7 楼起不含主楼**） |
  | ⛔ 不要用 | `/t/<id>/<n>.json?print=true` | 打印端点，**会被限流**（422） |

  `stream` 是话题**所有可见层的 post id 列表**（有序），`stream[floor - 1]` 即该层 id。
  ⚠️ 话题**删过层**时位置会漂 ⇒ 按 id 取回后**必须校验 `post_number`**，不对就丢弃。

- **twitter 投票** — **不在 `legacy` 上**，只在 `node["card"].legacy`：
  `name` 形如 `poll{2,3,4}choice_{text_only,image}`，选项是 `choice{N}_label` + `choice{N}_count`
  （`string_value`），另有 `end_datetime_utc`（ISO 8601）与 `counts_are_final`（**`boolean_value`**，
  不是 `string_value` —— 取错就永远是 `None`）。票数**匿名可拿到**（实测 11805/34875）。

**凭证脱敏**：提交前已把响应里的鉴权/追踪 token 换成 `***REDACTED***` ——
`authentication_token`（抖音）、`organic_tracking_token`（instagram / threads，共 27 处）。
其余字段一律未改动（`sec_uid` / `sec_item_id` 是平台公开 ID，`mbprivilege` 是全 0 的权限位）。
脱敏只动了值，字段名与结构保持不变，测试照常可用。

解析/复现用到的探针脚本未入库；探针运行方式是「本地写脚本 → scp 到 161 服务器 → `docker cp` 进 `shirobakobot-bot-1` → `docker exec /app/.venv/bin/python`」，只读、不改生产状态。
