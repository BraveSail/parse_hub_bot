# 统一处理：图片扩展名一律按 URL 取（`ImageRef.ext` 默认 jpg 的问题）

日期：2026-10-08
背景：修完 `doc/2026-10-08-media-p-mode-jpeg.md`（调色板图不该按后缀存）后，用户要求「统一处理」
其余平台 —— 那时**不会再崩**了，但 15 个平台构造 `ImageRef` 时没传 `ext`，图片实际是 PNG/WebP
也被命名成 `.jpg`（dataclass 默认值），下载文件名与内容不符。

## 调研（事实依据）

扫 `lib/test/fixtures/` 全部真实响应里的图片 URL：**266 条全部在 path 上带扩展名**，
query 上带格式的 0 条。代表样本：

| 平台 | 真实 URL 形态 |
| --- | --- |
| threads | `.../834098365_..._n.webp?_nc_cat=10` ← **webp**，此前被叫成 `.jpg` |
| bilibili | `http://i0.hdslb.com/bfs/vip/...png` |
| douyin | `.../feed_hot_search_icon.png` |
| facebook | `.../44073017_..._n.jpg?stp=cp0_dst-jpg_tt6` |
| instagram | `.../269621338_..._n.jpg?efg=...` |
| linuxdo | `https://cdn.ldstatic.com/images/emoji/.../distorted_face.png?v=15` |
| pixiv | `https://i.pximg.net/img-master/img/.../95276699_p0_master1200.jpg` |

⇒ 从 URL 推断扩展名对每个平台都成立。

## 改动

**① `lib/src/parsehub/utils/helpers.py` —— 单一实现**

`image_ext_from_url(url, default="jpg", exts=IMAGE_EXTS)`：path 扩展名 → query `format=`
（X 的卡片图 path 无扩展名）→ 调用方的 default。
另导出 `IMAGE_EXTS` 与 `ANIMATED_EXTS`（动图 URL 可能是视频容器，twitter 的动图就是 mp4，
所以动图场景要放宽可识别集合）。

**② 各平台接入（16 个文件）**

`threads` / `coolapk` / `linuxdo` / `instagram` / `tieba` / `tiktok` / `zhihu` / `weibo` /
`youtube` / `zuiyou` / `douyin` / `xiaoheihe` / `bilibili` / `bangumi` / `kuaishou` / `twitter`
（`ImageRef`、`AniRef`、`LivePhotoRef` 全覆盖）。coolapk 原先用 `.gif in url` 决定走 AniRef，
现在按解析出的扩展名判断。

**③ pixiv 的去重**

`provider_api/pixiv.py` 本来就有一份自己的 `_guess_ext`（同样语义）—— 已删除，改调公共实现，
保证只有一处实现在维护。

## 为什么这是安全的（纯增量）

URL 给不出可信扩展名时函数**原样返回 default**，即与"不传 ext"的行为完全一致。
所以对任何平台都是严格改进，不可能把命名改坏。这条不变量有专门测试钉住。

## 验证

**离线（`lib/test/test_image_ext_from_url.py`，26 例）**
- 各平台**真实 URL 样本**参数化（threads 的 `.webp`、bilibili 的 `.png` 等）
- query `format=` 兜底、path 优先于 query、百分号编码、大写后缀、目录里的点不算扩展名
- **纯增量保证**：无扩展名/非图片扩展名/空串 → 一律返回 default
- 动图的视频容器（`.mp4`）只在传 `ANIMATED_EXTS` 时识别，普通图片调用识别不到（钉住边界）
- 3 个平台的**入口级**断言（ext 真的传到 Ref 上了）：bilibili `BiliParse._to_refs`、
  bangumi `_refs`、twitter `TwitterParser.to_media_refs`

lib 全量 **842 passed / 1 skipped**，`ruff` 与 `scripts/check.sh` 干净。

**真机（部署 `9ccf9bd` 后，161 容器，真实链接）** —— 判据**独立于被测函数**（脚本自己解析 URL）：

| 平台 | 结果 |
| --- | --- |
| twitter（报障那条） | ✅ `ext='png'` ← `.../HUBBoF9bkAA1jCq.png?name=orig` |
| pixiv 作品 149431603 | ✅ 3 张全 `jpg` |
| pixiv 作品 95276699 | ✅ `jpg` |
| bangumi 日志 381120 | ✅ `jpg` |
| linuxdo 主题 | ✅ `jpeg`（`.jpeg` 后缀被正确保留，没有统一成 jpg） |
| youtube 帖子 | ✅ 2 张 `jpg`（URL 无扩展名 → 默认值，符合预期） |

合计 **9 个图片媒体，9 个一致**。

**⚠️ 未验证的平台（不含糊过去）**：

| 平台 | 原因 |
| --- | --- |
| zhihu | `知乎需要配置已登录的 Cookie` |
| threads / instagram | 登录态失效（此前已知） |
| xiaoheihe | 手头 link_id 已失效 |
| tieba | 试的两个帖子：一个只有视频、一个「贴子可能已被删除」 |
| douyin / weibo / tiktok / kuaishou / coolapk / zuiyou | 手头没有可用的真实链接 |

这些平台的代码路径与已验证平台**同一形态**（都是构造 Ref 时传 `ext=image_ext_from_url(url)`），
且函数级测试覆盖了它们的 URL 形态，但**没有用真实链接跑通** —— 要补验需先备好链接（cookie 类
还需先修登录态）。别把它们当"已验证"。

## 与上游的关系

这是**本地修改**（上游 `z-mio/ParseHub` 没有这套扩展名推断）。合上游时注意：
`utils/helpers.py` 的 `IMAGE_EXTS`/`ANIMATED_EXTS`/`image_ext_from_url` 与各 parser 里的
`ext=` 传参都要保留，`provider_api/pixiv.py` 里不要再长出第二份 `_guess_ext`。
