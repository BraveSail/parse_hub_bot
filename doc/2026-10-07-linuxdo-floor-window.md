# linux.do：窗口外的上下文楼层，用 `print=true` 一次取全

日期：2026-10-07
触发：用户「https://linux.do/t/topic/2989140/24?u=libc.so.6 怎么没有主楼了？」
→ 「为什么4楼可以？」
→ **「去查discourse文档，我就不信没有querystring能筛选」** ← 这句是对的，我第一版漏了参数

## 为什么 4 楼可以（真机实测 11 个楼层）

**不是 4 楼特殊** —— 窗口是**固定 20 层**，起点 = `max(1, target - 5)`
（源码 `lib/topic_view.rb` 的 `filter_posts_near`：`posts_before = (@limit / 4).floor`，
limit 默认 = chunk_size = 20 → 5）：

| 分享的楼层 | 窗口 | 含主楼 |
| --- | --- | --- |
| 1 楼 | `1..20` | ✓ |
| 4 楼 | `1..20` | ✓ |
| 6 楼 | `1..20` | ✓ |
| **7 楼** | `2..21` | ✗ ← 分界 |
| 24 楼 | `19..38` | ✗ |
| 30 楼 | `25..44` | ✗ |

## querystring（第一版漏了这部分）

`app/controllers/topics_controller.rb` 的 `show` 只把这几项放进 options：
`page` / `post_number` / `username_filters` / `filter` / `show_deleted` /
`replies_to_post_number` / `filter_upwards_post_id` / `filter_top_level_replies`，
另外 `opts[:print] = true if params[:print] == "true"`。

实测：

```
/t/<id>/24.json                20 层  19..38   不含主楼
/t/<id>/24.json?print=true     49 层  1..49    含主楼   ← print_chunk_size = 1000
/t/<id>.json?page=2            20 层  21..40   含 24 楼（path 有楼层号时 post_number 优先）
/t/<id>/24.json?page=1         20 层  19..38   page 被 post_number 压过
```

⇒ **`print=true` 把 chunk_size 从 20 提到 1000**（`TopicView.print_chunk_size`），
一次请求带回全帖。

**print 的三个副作用，都实测过**：连续 6 次 print 全部 200（没触发
`max_prints_per_hour_per_user` 限流，单次 0.2~0.3s）；返回字段完整
（`post_number`/`cooked`/`username`/`name`/`created_at`/`reply_to_post_number` 一个不缺）；
拿它的 payload 直接喂 `_from_payload` 能正常渲染（引用块 + `· #1` + 图片 + 计数全对）。

## 根因

`_context_quotes` 在窗口里 `next((p for p in posts if post_number == 1), None)` ——
找不到就**静默跳过**，不报错也不打日志。于是"分享楼层时带上主楼"这条规则在靠后的楼层上
悄悄失效。**静默跳过比报错更难发现**，这是它藏这么久的原因。

被回复的楼层（`reply_to_post_number`）同理。

## 改动（`provider_api/linuxdo.py`）

- `_missing_context_floors(posts, wanted, reply_to)` — 纯函数，算出窗口里缺的上下文层
- `_with_context_floors(...)` — 缺则取回并合并，**从省到贵两级**：
  1. **`?print=true` 一次拿全**（缺几层都够，一次请求）
  2. **逐层补取兜底** —— print 够不到的场合：超过 1000 层的巨型话题、站点关掉 print
     （`max_prints_per_hour_per_user` 为 0 时服务端直接拒）、被限流

合并时按 `post_number` 去重 + 排序（窗口与全帖必然重叠）。任何一级失败都**只记 warning**。

## 为什么不是"一开始就 print"（只 1 次请求）

那也能拿到目标层，但 **print 是打印视图端点，服务端对它挂了 rate limiter**
（`max_prints_per_hour_per_user`）—— 不该让**每次**解析都走它。而且超过 1000 层的帖子
print 只给前 1000 层，那时目标楼层根本不在结果里，仍要退回窗口请求。

所以：**窗口请求保底**（保证目标层一定在）+ **print 只在缺上下文时用**。

## 验证

生产端到端（第 24 楼），**拦住所有 linux.do 请求记账**：

```
linux.do 请求次数 = 2
  1. /t/2989140/24.json              ← 窗口（保证目标层在内）
  2. /t/2989140/24.json?print=true    ← 一次拿全，补主楼
含 print=true 的请求数 = 1
position_label='#24'  quoted=1
引用块里有 #1 = True
发送 = True
```

测试 `lib/test/test_linuxdo_floor_window.py`（15 passed，两个真实 fixture）：

- 窗口规律：24 楼 → 缺 `[1]`；4 楼 → 不缺
- 被回复层在窗口外 → `[1, 3]`；已在窗口内 → 只补主楼；reply_to=1 不重复
- **缺上下文时只用一次 print**，且**没有逐层请求**；**不缺时零请求**
- 两个缺层仍只一次 print
- 兜底：print 返回 403 / print 请求炸了 / print 只给前 1000 层（仍缺）→ 逐层补上
- 补取全失败不抛错；重叠楼层去重
- 端到端：补齐后渲染出主楼引用块（`· #1`、作者 KoaIa）

lib 612 / bot 494 全绿，`scripts/check.sh` 干净。commit `14ed0db`。
