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

- **twitter 投票** — **不在 `legacy` 上**，只在 `node["card"].legacy`：
  `name` 形如 `poll{2,3,4}choice_{text_only,image}`，选项是 `choice{N}_label` + `choice{N}_count`
  （`string_value`），另有 `end_datetime_utc`（ISO 8601）与 `counts_are_final`（**`boolean_value`**，
  不是 `string_value` —— 取错就永远是 `None`）。票数**匿名可拿到**（实测 11805/34875）。

**凭证脱敏**：提交前已把响应里的鉴权/追踪 token 换成 `***REDACTED***` ——
`authentication_token`（抖音）、`organic_tracking_token`（instagram / threads，共 27 处）。
其余字段一律未改动（`sec_uid` / `sec_item_id` 是平台公开 ID，`mbprivilege` 是全 0 的权限位）。
脱敏只动了值，字段名与结构保持不变，测试照常可用。

解析/复现用到的探针脚本未入库；探针运行方式是「本地写脚本 → scp 到 161 服务器 → `docker cp` 进 `shirobakobot-bot-1` → `docker exec /app/.venv/bin/python`」，只读、不改生产状态。
