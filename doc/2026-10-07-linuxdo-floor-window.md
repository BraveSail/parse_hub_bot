# linux.do：窗口外的上下文楼层要补取

日期：2026-10-07
触发：用户「https://linux.do/t/topic/2989140/24?u=libc.so.6 怎么没有主楼了？」
→ 追问「为什么4楼可以？」

## 为什么 4 楼可以（真机实测 11 个楼层）

**不是 4 楼特殊** —— Discourse 的楼层窗口是**固定 20 层**，起点 = `max(1, target - 5)`：

| 分享的楼层 | 窗口 | 含主楼 |
| --- | --- | --- |
| 1 楼 | `1..20` | ✓ |
| 4 楼 | `1..20` | ✓ |
| 6 楼 | `1..20` | ✓ |
| **7 楼** | `2..21` | ✗ ← 分界 |
| 10 楼 | `5..24` | ✗ |
| 24 楼 | `19..38` | ✗ |
| 30 楼 | `25..44` | ✗ |

target ≤ 6 时 `target - 5 < 1`，窗口只能从 1 楼开始，主楼**恰好在**窗口里；
**target ≥ 7** 往前够了 5 层，主楼就被切在窗口外。

## 根因

`_context_quotes` 是在窗口里 `next((p for p in posts if post_number == 1), None)` ——
找不到就**静默跳过**，不报错也不打日志。于是"分享楼层时带上主楼"这条规则在靠后的楼层上
悄悄失效，用户看到的就是"没有主楼了"。

**被回复的楼层同理**：`reply_to_post_number` 指向的层也可能在窗口外，同样静默丢。

## 补取可行性（实测）

```
/t/<id>/24.json  → 19..38  不含主楼
/t/<id>.json     → 1..20   含主楼
/t/<id>/1.json   → 1..20   含主楼
```

⇒ 没有任何一次请求能同时含主楼与第 24 楼，**必须补一次**。

## 改动（`provider_api/linuxdo.py`）

- `_missing_context_floors(posts, wanted, reply_to)` — 纯函数，算出窗口里缺的上下文层
  （主楼、被回复的层；去重、排除当前层）
- `_with_context_floors(payload, topic_id, post_number, proxy=, cookie=)` — 缺则用**同一条件**
  请求 `/t/<id>/<floor>.json`，把回来的 posts 按 `post_number` 去重 + 排序后合并
- `parse` 在 `_from_payload` 之前调用

**三条纪律**：

- **只在缺的时候请求** —— 窗口里已有主楼（target ≤ 6）时零额外请求，行为与以前完全一致；
- **补不到不致命** —— 只记 warning，少一个上下文块比整条打不开好；
- 最多补两层（主楼 + 被回复层）。

## 验证

生产端到端（第 24 楼）：

```
position_label='#24'  media=1  quoted=1
正文里含 · #1 = True
发送 = True
```

读回的消息块结构：

```
消息 1007: 含 #24 与 #1，块含 RichBlockBlockQuotation
           （内含 段落 + 分割线 + 段落 + 照片 ← 主楼的图也进来了）
消息 1006: 只含 #24（修复前发的，无引用块）
```

补取请求次数：实测**只请求一次** `/1.json`。

测试 `lib/test/test_linuxdo_floor_window.py`（12 passed，两个真实 fixture：24 楼窗口
`linuxdo_floor_24.json`、含主楼的窗口 `linuxdo_floor_4.json`）：

- 窗口规律：24 楼 → 缺 `[1]`；4 楼 → 不缺
- 被回复层在窗口外 → `[1, 3]`；已在窗口内 → 只补主楼；reply_to=1 不重复
- **不缺时零请求**；补取失败不抛错；重叠楼层去重
- 端到端：补齐后渲染出主楼引用块（`· #1`、作者 KoaIa）

lib 609 / bot 494 全绿，`scripts/check.sh` 干净。commit `8ad8750`。
