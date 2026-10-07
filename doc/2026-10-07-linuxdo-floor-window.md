# linux.do：窗口外的上下文楼层，按 id 精确取

日期：2026-10-07
触发：用户「https://linux.do/t/topic/2989140/24?u=libc.so.6 怎么没有主楼了？」
→ 「为什么4楼可以？」
→ 「去查discourse文档，我就不信没有querystring能筛选」 ← 对，第一版漏了参数
→ 「你这样还不如第二次请求1.json？」 ← 也对，第二版把限流端点当成了主路径

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

## 根因

`_context_quotes` 在窗口里 `next((p for p in posts if post_number == 1), None)` ——
找不到就**静默跳过**，不报错也不打日志。于是"分享楼层时带上主楼"这条规则在靠后的楼层上
悄悄失效。**静默跳过比报错更难发现**，这是它藏这么久的原因。被回复的楼层同理。

## 取单层的三条路（实测对比，这是本节的结论）

```
a) /t/<id>/1.json                     20 层  53.6KB  200  ✓     ← 兜底
b) /t/<id>/posts.json?post_ids[]=<id>  1 层   4.5KB  200  ✓     ← 现行主路径
c) /t/<id>/1.json?print=true          49 层  117KB  422  ✗     ← 限流
```

**`stream` 是关键**：响应里的 `post_stream.stream` 是话题**所有可见层的 post id 列表**
（有序），所以 `stream[floor - 1]` 就定位那一层，按 id 精确取只有 4.5KB —— 比取它的
20 层窗口小一个数量级。

**`print=true` 不能用**（第二版踩的坑）：它确实能把 `chunk_size` 从 20 提到 1000
（`TopicView.print_chunk_size`），一次拿全帖看着很美 —— 但它是**打印视图端点，服务端挂了
rate limiter**（`max_prints_per_hour_per_user`）：

```json
{"errors":["You've performed this action too many times, please try again later."]}
```

连打 6 次之后就开始返回 422（我自测时就撞上了），而同一时刻 `/1.json` 与 `posts.json`
都是 200。**数据量也更大**（117KB vs 4.5KB）。拿它当常规路径 = 迟早整条解析没有上下文。

## 改动（`provider_api/linuxdo.py`）

- `_missing_context_floors(posts, wanted, reply_to)` — 纯函数，算出窗口里缺的上下文层
- `_post_id_for_floor(payload, floor)` — `stream[floor - 1]`，拿不到返回 `None`
- `_fetch_floor(client, payload, topic_id, floor)` — **两级取法**：
  1. 有 id → `posts.json?post_ids[]=<id>`（4.5KB），**校验取回的 `post_number`**
     （话题删过层时 stream 位置会漂）；
  2. 拿不到 id / 校验不过 / 请求失败 → 退回 `/<floor>.json`（该层窗口，必含它自己）
- `_with_context_floors(...)` — 缺则逐层取回并合并（按 `post_number` 去重 + 排序）；
  取不到**只记 warning**，不让整条解析失败

## 验证

生产端到端（第 24 楼），**拦住所有 linux.do 请求记账**：

```
200  56.2KB  /t/2989140/24.json                       ← 窗口（保证目标层在内）
200   4.8KB  /t/2989140/posts.json?post_ids[]=23380274 ← 主楼，按 id 精确取
请求数 = 2   总下载 = 60.9KB
position_label='#24'  quoted=1
引用块里有 #1 = True
发送 = True
```

对比第二版（print）：`56.2 + 117 = 173KB` 且会被 422 ⇒ 现在省 65% 流量且稳定。

测试 `lib/test/test_linuxdo_floor_window.py`（19 passed，两个真实 fixture）：

- 窗口规律：24 楼 → 缺 `[1]`；4 楼 → 不缺；被回复层在窗口外 → `[1, 3]`
- `stream[floor-1]` 定位；stream 太短 → 拿不到 id
- **缺主楼走 id 精确取**（一次，且**不取窗口**）；请求的 id 就是主楼
- 两个缺层 → 两次精确取
- **不缺时零请求**
- 退回窗口的三条理由：取回的楼层不对 / 返回非 200 / 请求本身炸了
- 无 id 时直接走窗口（不发无用请求）；两条路都失败不抛错；重叠楼层去重
- 端到端：补齐后渲染出主楼引用块（`· #1`、作者 KoaIa）

lib 616 / bot 494 全绿，`scripts/check.sh` 干净。commits `9ecb69c`（实现）、`58cf28b`（文档）。
