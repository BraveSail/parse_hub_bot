# 抖音：点赞缺失、幽灵「0 查看」、作者主页链接

日期：2026-10-05
来源：用户「https://www.douyin.com/video/7692999234357906715 api没有返回数据吗，观看是0，点赞没有，作者主页链接也没有」

## 结论：**接口数据都在，是解析层没取**

原始响应已落盘 `/app/data/douyin_probe.json`（69KB，一次请求）。关键片段：

```json
"statistics": {"digg_count": 391, "collect_count": 36, "comment_count": 59,
               "play_count": 0, "forward_count": 0, "share_count": 10, ...},
"author": {"nickname": "青蜂侠",
           "sec_uid": "MS4wLjABAAAAA203AQ9dxu6_ftaFa0AjzsoW1L0SVWdIfzjr7Jv-Y_Y",
           "uid": 71058463678}
```

三条各自的原因：

| 现象 | 根因 |
| --- | --- |
| 点赞没有 | `statistics.digg_count` 一直存在，**parser 从没取过它**（只看了 `play_count`） |
| 观看是 0 | 移动端接口**固定返回 `play_count: 0`**（不是缺失，就是 0），代码 `to_int(0)` → 显示「0 查看」 |
| 作者主页没有 | `PROFILE_URL_TEMPLATES` 里**根本没有抖音**；而且抖音主页要用 **`sec_uid`**（数字 `uid` 打不开） |

## 修法

1. `DouyinApiResult` 加 `author_sec_uid` / `like_count` 两个字段。
2. `parse()`：
   - `like_count = to_int(statistics.digg_count)`
   - **`view_count = play_count or None`** —— 0 与缺失一样当作"没有"
     （项目原则：拿不到就不显示那一段，绝不留空占位；显示「0 查看」等于把"不知道"说成了"是 0"）
   - `author_sec_uid = author.sec_uid`
3. `_build_video_result` / `_build_image_result` 传 `like_count` +
   `author_url=profile_url(Platform.DOUYIN, user_id=sec_uid)`。
4. `PROFILE_URL_TEMPLATES` 加 `Platform.DOUYIN: "https://www.douyin.com/user/{id}"`
   （`author_url` 与 `like_count` 都是 `ParseResult` **既有字段**，不属于"新增平台字段"那套完整落地流程）。

## 验证

- lib **453 passed**（新增 9 条）、bot 341 passed、`check.sh` 干净。
- **测试用真实响应做 fixture**：`lib/test/fixtures/douyin_video.json`
  （从线上响应抽取最小子集，只把带时效签名的 URL 换成占位，其余值未改）——
  手写的 fixture 正是"结构与真实不符"的温床。
- **真机**：

```
author_url = 'https://www.douyin.com/user/MS4wLjABAAAA…'   ← 有了
view_count = None        ← 不再显示 "0 查看"
like_count = 399         ← 有了 (实时值; 落盘时是 391)

页脚:   2026年10月5日 10:06 · 399 点赞 · 来源（抖音）
作者行: **<a href="…/user/MS4wLjABAAAA…">青蜂侠</a>：**     ← 作者名变成主页链接
```

## 备注

- 作者名链接挂在**显示名**上（抖音没有 @handle）—— 与 B 站（只有 UID）同一做法。
- **缓存**：旧缓存条目里存的是旧统计（`like_count=None` / `view_count=0`），
  需重新解析才更新。
- 统计行仍只有 **查看 / 点赞** 两档，`comment_count` 等**不新增**（用户没要求）。
