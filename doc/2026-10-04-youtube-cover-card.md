# 正文里的 YouTube 链接：封面卡片

日期：2026-10-04
来源：用户「https://x.com/Suikoden_anime/status/2106398886827098121 这个把引用的 youtube 链接封面加一下」+「跟bilibili那个引用」

## 需求

推文正文里引用了 YouTube 链接：

```
「Suikoden: The Anime」公式YouTubeチャンネル
　http://www.youtube.com/@SuikodenTheAnime-EN
```

原来渲染成**裸 URL** —— 点得开，但没有封面。用户要**像 bilibili 引用的视频那样**：
引用卡片 + 封面 + 标题可点。

## 调研

- **网络可达**：容器直连 `youtube.com` / `i.ytimg.com` 均 200，无代理变量。
- **封面拿得到**：
  - 视频 → oembed（`format=json`）的 `thumbnail_url` = `i.ytimg.com/vi/<id>/hqdefault.jpg`
  - 频道 → 没有 oembed，抓频道页的 `og:title` / `og:image`（频道头图/主视觉）
- **⚠️ 频道页的 og 标签在 400KB 之后**：页面约 1.1MB，前面全是内联脚本与数据。
  第一版想"只读前 400KB 找 og"，实测**匹配不到** —— 必须拿完整文本。
- **YouTube 自己的解析器不认 `@handle`**：`YtbParse.__match__` 里有 `(?!@)`，
  所以 `youtube.com/@xxx` 这类链接**从来没被解析过**（这次也没走它，是另做卡片）。

## 实现

**新文件** `lib/src/parsehub/provider_api/youtube.py`：

- `find_youtube_links(text)` —— 扫正文里的 YouTube 链接（去重、保持顺序、去掉尾部中文句读）
- `fetch_card(url)` —— 视频走 oembed，频道走 og 标签；**失败一律 None**

**`lib/src/parsehub/parsers/parser/twitter.py`**：

- `media_parse` 开头抓一次卡片，`_compose(body, tweet, extra=yt_quote)` 把卡片**附在最末**
- 封面作为 `ImageRef` 进 media，并**计入 `quoted_media_count`**

**bot 侧零改动** —— 封面走的就是 bilibili 转发封面那条通道
（`quoted_media_count` → 渲染层把末尾媒体放进引用块）。

## 几个决定

- **保留正文**（含那行链接），末尾附加卡片 —— 与 bilibili 转发一致，原文如实。
- **只取第一个抓得到的链接**：一条推文塞多张封面会喧宾夺主，而且引用块媒体是按
  **数量**切分的（`quoted_media_count` 是单个数字），无法表达"两块各有几张"。
- **抓不到就什么都不加**：封面是锦上添花，不该让解析失败。
- **标题与 URL 都 `html.escape`**（跟项目里 `format_author_link` 一致）—— 标题里的 `&` 会让 HTML 解析出错。
- **封面 URL 截掉 `=` 后的尺寸后缀**（`…=s900-c-k-c0…` → 原图），让下载器按需处理。

## 验证

- lib 444 passed（新增 9 条：链接识别 / 去重 / 中文句读 / 有封面 / 无封面 / 无链接 /
  转义 / 只取第一个可解析的）、bot 309 passed、ruff 与 check.sh 干净。
- **真机服务端块**（清掉那条 URL 的旧缓存后重解析）：

```
[RichBlockBlockQuotation]                       <-- 引用块
  [RichBlockParagraph] 链接=http://www.youtube.com/@SuikodenTheAnime-EN
  [RichBlockPhoto]                              <-- 封面
```

## 注意

`quoted_media_count` 进了缓存结构 —— **旧缓存条目里没有封面媒体**，
命中旧缓存时不会补封面。要立刻看到效果需清那条 URL 的缓存（或对同链接重新解析）。
