# twitter 投票：选项与票数

日期：2026-10-07
触发：用户「https://twitter.com/thsottiaux/status/2107576143285219799 投票处理下」

## 根因（真机取证）

响应快照存 `lib/test/fixtures/twitter_poll_card.json`：

```
tweet.full_text = "Vote"                     ← 正文只有作者写的这一个词
legacy 里没有任何 poll 字段                   ← 关键：不在 legacy 上
node["card"].legacy.name = "poll2choice_text_only"
  choice1_label = "👌(good day)"         choice1_count = "11805"
  choice2_label = "🫨 (needs a reset)"   choice2_count = "34875"
  end_datetime_utc = "2026-10-07T00:58:03Z"
  counts_are_final = {boolean_value: false}
```

投票**只在 `card` 里**。这个项目里媒体在 `legacy.entities.media`、标签在 `entities`、
投票在 `card` —— 三条路各走各的，只看 `legacy` 就会整条丢掉。票数**匿名可拿到**。

## 改动

`lib/src/parsehub/provider_api/twitter.py`

- 新增 `TwitterPoll`（`choices: list[(文案, 票数)]` / `end_datetime` / `is_final`）
- 新增 `Twitter._parse_poll(node)`：`card.name` 以 `poll` 开头时解析
- `TwitterTweet` 增加 `poll` 字段，`_parse_result` 两处返回点都带上
- `_parse_card_photo` 跳过 poll 卡片（选项配图不是"外链预览图"）

`lib/src/parsehub/parsers/parser/twitter.py`

- 新增 `_build_poll`（表格）与 `_poll_cell`（转义 `|`）
- `_compose` 把主帖投票接在正文后
- `_quote_block` 把被引用/被回复帖的投票接在**它自己的引用块**里

## 两个坑

- **选项数不固定**：卡片名是 `poll{2,3,4}choice_{text_only,image}`，键是
  `choice{N}_label` / `choice{N}_count` ⇒ 按 N 递增取到没有为止，不能硬编码 2 个。
- **`counts_are_final` 是 `boolean_value`**，其余字段是 `string_value`。只取
  `string_value` 会永远得到 `None`，**且不报错**。

## 形态

与 linux.do 的投票**同一形态**（选项 / 票数 / 占比三列），表格由渲染层转成服务端 Table 块。

## 验证

真实响应：

```
Vote

| 选项 | 票数 | 占比 |
| --- | --- | --- |
| 👌(good day) | 11805 | 25% |
| 🫨 (needs a reset) | 34875 | 75% |
```

占比 = round(票数 × 100 / 总票数)（11805/46680=25%、34875/46680=75%）。

生产端到端（这条推文其实是回复，被回复内容渲染成引用块、投票接在正文后）：

```
> <i><a href="https://x.com/thsottiaux">Tibo</a> <code>@thsottiaux</code></i>
> <i>Roundup of Day 2/</i>
> …

Vote

| 选项 | 票数 | 占比 |
| --- | --- | --- |
| 👌(good day) | 11805 | 25% |
| 🫨 (needs a reset) | 34875 | 75% |
```

测试：`lib/test/test_twitter_poll.py`（16 passed，真实 fixture + 边界：2/3/4 选项、
票数缺失、无投票、选项含 `|`、零票不崩、引用/回复里的投票落在自己的块内）。
lib 587 / bot 474 全绿，`scripts/check.sh` 干净。commit `544e4e5`。
