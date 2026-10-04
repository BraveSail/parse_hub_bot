# linux.do 楼层解析：带上主楼与被回复楼层的上下文

日期：2026-10-04 · 影响：`provider_api/linuxdo.py`

## 需求（用户原话）

> 「楼层的把主楼消息做成回复」
> 「对，还有楼层回复其他楼的情况，这时候**主楼放最上面，回复中间，楼层放最下面**」

## 语义前提（取证）

Discourse 里 `reply_to_post_number` 为 `null` 表示**回复主题（主楼）**。实测该主题三层全是 `null` —— 即 3 楼确实在回应主楼。

带楼层的请求（`/t/<id>/3.json`）返回的帖子流**含主楼**（实测返回 `[1, 2, 3]`），所以主楼内容直接可得，不需要额外请求。

## 改动

`_context_quotes(posts, current)` 产出上下文引用块，**从远到近**：

| 场景 | 结果 |
| --- | --- |
| 解析楼层，该层回复主题 | 主楼引用块 → 本层正文 |
| 解析楼层，该层回复别的楼层 | 主楼 → 被回复楼层 → 本层正文 |
| 解析主楼本身 | 无上下文（不自我引用） |
| 被回复的正是主楼 | 只出现一次，不重复 |

复用公共排版 helper（`format_quote_block` / `format_author_link`），与 twitter / threads / bilibili 同一套 —— provider 只产出 markdown，bot 侧照旧走 `split_quote_blocks`。

## 纯图楼层不能丢

**用户质疑（原话）**：「为什么只有图片会变成空壳？ 不能只引用图片？？」

质疑成立。第一版用 `_post_to_quote` 返回空串当"跳过"信号 —— 主楼是纯图时整个丢掉，**连"在回复谁"都看不到**，这是有损的。

而引用块**本来就能带图**（`attach_quote_media` 就是干这个的，注释里还写着实测验证过）。所以：

- 该层**没有文字也出引用块**（只有署名行）
- 该层的图片走**引用块媒体**通道：`LinuxDoTopic.quoted_media_count` → `ParseResult.quoted_media_count` → bot 侧 `cache_media_blocks` 按计数把末尾 N 张切给引用块

`format_quote_block` 为此加了 `sign_only` 参数（默认 `False`，零回归）：
- 没有媒体配套的调用方（threads）保持"空则不显示"——一行孤零零的署名是噪音
- 有媒体配套的（linux.do、**bilibili**）传 `sign_only=True`

**顺带修掉 bilibili 的既存 bug**：`_render_forward` 判断"有图就继续"，但接着 `format_quote_block("")` 返回空 → **引用块丢失，而 `quoted_media_count` 仍算着它的媒体**，封面于是掉进正文。传 `sign_only=True` 后修复。

## 顺带修掉的作者归属 bug

真机测试暴露：解析 3 楼，`author_name` 却是**楼主**的名字。

```python
author_name = str(first.get("name") or created_by.get("name") or "")   # ← created_by 是主题创建者
```

楼层缺 `name` 字段时回落到 `created_by.name`（楼主）。`author_handle` 同样的回落，只因楼层都带 `username` 而没暴露。

**这与用户此前报过的「指定楼层的数据是错的是楼主的（时间/点赞数）」是同一类错误** —— 当时修了时间/点赞，作者名漏了。现在：`created_by` 只在该层**就是主楼**时才允许回落。

## 验证

- lib 432 passed（新增 8 条：主楼引用 / 被回复楼层顺序 / 主楼不自我引用 / 不重复 / 纯图主楼带图与 `quoted_media_count` / 作者归属 / `sign_only` 行为 / 主楼的 created_by 回落）、bot 285 passed、ruff 全过。
- 离线验证（用 `make_payload` 内联构造，符合项目约定）：三种场景的 markdown 顺序与媒体归属均正确。
- ⚠️ **真机端到端未完成** —— 调试期间请求过于频繁触发 Cloudflare 限流（403），需要在限流窗口过去后补验。

## 教训

1. **"只有图片会变空壳"是我的实现缺陷，不是事实** —— 我拿它当设计理由，没验证。用户一问就露馅。**引用块能带图**这件事代码里写着实测结论，我却没看。
2. **不要反复请求真实接口做探针**（用户原话：「你请求一次就缓存下啊，不要一直请求」）—— 密集请求会触发 Cloudflare 限流，之后连正常工作都受影响。取一次、落盘、离线分析。
3. **同一类 bug 要一次修透**：楼层 vs 主题级数据的混淆（时间 → 点赞 → 作者）已经第 3 次出现，说明当时只修了报告出来的那一项。
