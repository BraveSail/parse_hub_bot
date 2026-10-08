# YouTube 帖子正文里的链接被截断：从 run 的 navigationEndpoint 还原

日期：2026-10-08
触发：用户「http://youtube.com/post/UgkxNoA6S6qRc2Yc7QXHcECdW9JHe7XLDpAB 链接怎么不完整？？
横向排查其他平台」

## 根因（活体取证）

帖子 `UgkxNoA6S6qRc2Yc7QXHcECdW9JHe7XLDpAB`（Ani-One中文官方動畫頻道）的 `contentText.runs`：

| run | 页面给的显示文本 | 同一条 run 的 navigationEndpoint 里的**真实**地址 |
| --- | --- | --- |
| 1 | `https://www.youtube.com/playlist?list...` | `commandMetadata.webCommandMetadata.url` = `/playlist?list=PLcsS6p8iu5r4`（**相对路径**）；`browseEndpoint.canonicalBaseUrl` 同值 |
| 13 | `https://x.com/fxkurumi_info/status/21...` | `urlEndpoint.url` = `youtube.com/redirect?…&q=https%3A%2F%2Fx.com%2Ffxkurumi_info%2Fstatus%2F2105266424730255588` |

**是 YouTube 自己把显示文本截断的**（页面源码里就是字面的 `list...`，实测 `list...` 在整页只出现 1 次），
而**完整地址挂在同一条 run 的 navigationEndpoint 上**。我们的 `_text_of()` 只拼 `run["text"]`，
于是正文里留下了断链。

⚠️ **踩到的坑**：第一版只在 `urlEndpoint` / `browseEndpoint` 两级取，结果 run[1] 走的是
`commandMetadata` 且给的是**相对路径**，正文里就变成 `/playlist?list=…` 这种半截链接
（测试当场抓到）。**三个载体都可能是相对路径，所以最后要统一补域名** —— 别以为"取到了就算对"。

## 改动

`lib/src/parsehub/provider_api/youtube.py`：

- 新增 `_run_link_url(run)`：按 `urlEndpoint.url` → `commandMetadata.webCommandMetadata.url` →
  `browseEndpoint.canonicalBaseUrl` 取真实地址；相对路径补 `https://www.youtube.com`；
  `/redirect?…&q=` 形态解出 `q=` 并丢掉追踪 token（新增 `_unwrap_redirect`）。
- `_text_of()`：run 的文本是 URL 形态（`^https?://` / `^www.`）且能取到真实地址时用它替换显示文本。
  **`#hashtag` 不受影响**（不以 http 开头，且它有独立的渲染通道）—— 单测钉住。

## 验证

- 新增 6 个用例（`lib/test/test_youtube_post.py`），fixture 是真实页面裁剪（新
  `youtube_post_truncated_links.html`，runs 原样保留）：两种载体各一条、redirect 解包、
  hashtag 不被替换、非 URL 散文不动、无 navigationEndpoint 时保持原样。
- **反向验证**：临时禁用还原逻辑 → 两个核心用例失败；恢复后 md5 一致。
- lib 全量 **815 passed / 1 skipped**，`scripts/check.sh` 干净。
- **生产真机**（161 容器，部署 `b5739d6` 后）：

  ```
  ► https://www.youtube.com/playlist?list=PLcsS6p8iu5r4
  ℹ️ 來源 | X @ https://x.com/fxkurumi_info/status/2105266424730255588
  ```
  残留 `list...` / `status/21...` 均为 False。

## 横向排查其他平台（用户要求）

**判据**：① 各平台 provider 是否有"从平台元数据还原完整链接"的机制；② 已有 fixture 里是否有
"URL 带省略号"；③ 用真实链接跑解析、检查结果里是否残留截断链接（正则 `https?://\S*(\.\.\.|…)`）。

| 平台 | ③ 实测结果 |
| --- | --- |
| **youtube 帖子** | ⚠️ **2 处断链**（本次修复对象，修完为 0） |
| twitter | OK（`t.co` → `entities.urls[].expanded_url` 还原；实测 1 条链接完整） |
| bilibili | OK（链接数 0，正文无 URL） |
| linux.do | OK（链接数 0） |
| threads | 未能验证（该样本需登录：`无法获取帖子内容(可能为私人或受限内容…)`） |

**② fixture 扫描**：只有 `xhs_tag_list*.json` 出现 URL 带省略号，但**不是问题** ——
截断发生在 `xsec_token` 的值里，而 xhs 解析器把 `xsec_token` 列在 `__after_clean_parameters__`
（本来就会从 URL 里清掉）；且 `xhs_tag_list_reference.json` 标注为**外部参考、非本机抓取**。

**① 源码扫描**（`provider_api/*.py` 里出现还原相关标识）：

- 有专项机制：`twitter`（expanded_url / t.co）、`youtube`（redirect / canonicalBaseUrl）、
  `douyin` / `tiktok` / `weibo` / `kuaishou`(resolve)、`xhs` / `facebook` / `instagram` /
  `snapchat` / `threads`（redirect / t.co）、`bilibili`（resolve）
- 未见：`bangumi` / `coolapk` / `douban` / `linuxdo` / `pipix` / `pixiv` / `tieba` / `weixin` /
  `xiaoheihe` / `zhihu` / `zuiyou`
- ⚠️ **这条只是源码层线索，不等于"没问题"**：这些平台本次**没有逐一用真实链接验证**（③ 只覆盖了
  5 个平台）。要下"某平台不会截断链接"的结论，得按 ③ 用真实样本跑一遍 —— 本次没做，别把
  源码扫描当结论用。
