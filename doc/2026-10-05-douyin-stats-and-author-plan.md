# 抖音统计与作者主页缺失

日期：2026-10-04
触发：用户「https://www.douyin.com/video/7692999234357906715 api没有返回数据吗，观看是0，点赞没有，作者主页链接也没有」

## 取证（已做，原始响应已落盘 `/app/data/douyin_probe.json`，69KB）

走的是**移动端接口**（配置里没有 douyin cookie → `_fetch_api_result` 直接走 mobile）：

```
statistics = {"digg_count": 391, "collect_count": 36, "comment_count": 59,
              "play_count": 0, "forward_count": 0, "share_count": 10, ...}
author: sec_uid = "MS4wLjABAAAAA203AQ9dxu6_ftaFa0AjzsoW1L0SVWdIfzjr7Jv-Y_Y",
        uid = 71058463678, nickname = "青蜂侠"
```

⇒ **接口返回的数据是有的**，三条都是我们自己没取／取错：

| 现象 | 根因 |
| --- | --- |
| 点赞没有 | `digg_count: 391` 存在，**parser 从没取过它** |
| 观看是 0 | `play_count: 0`（移动端就是 0），代码 `to_int(0)` → 显示"0 查看"；按项目原则**拿不到就不显示** |
| 作者主页没有 | `sec_uid` 存在，但 `PROFILE_URL_TEMPLATES` 里**没有 DOUYIN** → `profile_url()` 返回空串 |

## phase0 — 主页模板

`lib/src/parsehub/utils/helpers.py`：
```python
Platform.DOUYIN: "https://www.douyin.com/user/{id}",   # id = sec_uid
```
验证：单测 `profile_url(Platform.DOUYIN, user_id="MS4w…")`。

## phase1 — 取点赞 / 修观看 / 取 sec_uid

`lib/src/parsehub/parsers/parser/douyin.py`：
- `DouyinApiResult` 加 `author_sec_uid: str = ""`、`like_count: int | None = None`
- `parse()`：
  - `like_count = to_int(statistics.digg_count)`
  - `view_count`：`play_count` 为 **0 或缺** → `None`（不显示，不留 "0 查看"）
  - `author_sec_uid = author.sec_uid`
- `_build_video_result` / `_build_image_result` 传 `like_count` +
  `author_url=profile_url(Platform.DOUYIN, user_id=result.author_sec_uid)`

验证：单测（有 digg_count / play_count=0 / 缺 play_count / 有 sec_uid 四种）。

## phase2 — 真机验证

产物：解析该 URL 的最终结果对象 + 渲染出的页脚统计行。
验证：页脚出现「点赞 391」、**不出现**「0 查看」、作者行可点（指向 douyin.com/user/sec_uid）。

## 风险

- `statistics` 里的 `comment_count` 等其它指标**不新增**（用户没要求；统计行固定 查看/点赞 两档）。
- web 路径（配了 cookie 时）字段名相同，一并受益；如有差异以真机为准。
- 既有缓存条目里的统计是旧值 —— 需重解析才更新（与之前同类问题一致）。
