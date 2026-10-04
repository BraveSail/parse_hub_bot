# Instagram 页脚只有来源

日期：2026-10-04 · 影响：Instagram 解析结果的页脚（时间/点赞/播放）

## 症状

用户：**「页脚只有来源」**（`https://www.instagram.com/reel/Dd_pnzHSKs8/`）。

## 根因

provider **已经归一化好了**这些字段（`_convert_v1_media` 写进 node）：

- `taken_at_timestamp` ← `media["taken_at"]`
- `edge_media_preview_like.count` ← `media["like_count"]`
- `video_view_count` ← `media["view_count"]`

但 **`InstagramPost` 类没暴露它们**，`_do_parse` 也只传了 `title` / `content` / `author_name`
→ `published_at` / `view_count` / `like_count` 全是 None → 页脚只剩来源。

## 改动

1. `InstagramPost` 加三个属性：`published_at`（unix 秒，交给 `ParseResult` 的 `to_datetime` 转换）、
   `like_count`、`view_count`（用现成的 `to_int`）。
2. `_do_parse` 的四个分支（SIDECAR / IMAGE / VIDEO）统一带上 `published_at` / `like_count` / `view_count`。

修后页脚：

```
22:05 · 2026年10月2日 · 348 点赞 · 来源（Instagram）
```

`view_count` **仍是 None**：Instagram 的接口不给 reel 播放数（yt-dlp 同样拿不到，实测 `view_count=None`）。
按既定规则「拿不到就不显示那一段」，页脚自动跳过 —— 不是 bug。

## 验证

- lib 424 passed（新增 2 条：四分支都带字段 / 拿不到时保持 None）、bot 271 passed、ruff 全过。
- 真机：`published_at=2026-10-02 14:05:45+00`、`like_count=348`、`view_count=None`。
- 缓存已清（parser 改动会固化在 content 里）。

## ⚠️ 这不是孤例（审计发现）

`grep` 各 parser 的 `ParseResult(...)` 返回值，**18 个平台里只有 4 个传了页脚字段**：

| 已接 | 缺 |
| --- | --- |
| bilibili、twitter、threads、douyin（缺 like） | coolapk、douban、instagram✦、kuaishou、linuxdo、pipix、tieba、tiktok、weibo、weixin、xhs、xiaoheihe、zhihu、zuiyou |

✦ 本次已修。

**但"没在 return 里传"≠"数据拿不到"** —— Instagram 就是 provider 有、parser 没接。
要判定每个平台，得先看它的 provider 有没有对应字段，再决定接不接。系统补齐是独立一轮工作。
