# bgm 的时间是站点本地时间（北京时间），不是 UTC

日期：2026-10-07

## 症状

用户报「时间好像有问题？多 8 小时」。

## 根因

bgm 的页面只写本地时间、**不带偏移**：

```
#3 - 2026-10-5 18:47          ← 页面原文（北京时间）
```

而共享的 `to_datetime` 把裸字符串当 **UTC** 解释（`parsed.replace(tzinfo=UTC)`）。
于是页脚发出去的 `<tg-time unix=…>` 早了 8 小时 —— **任何时区**的用户看到的时间
都比原帖晚 8 小时。

## 证据：bgm 的服务器时区就是 +08:00

```
$ curl -s 'https://api.bgm.tv/v0/users/1175849/collections?limit=1'
{"data":[{"updated_at":"2026-10-07T01:18:56+08:00", …
```

bgm 自己的 API 返回的时间戳**带 +08:00** —— 站点时区是北京时间，所以页面上的
``2026-10-5 18:47`` 要按 +08:00 解释。

（另一个曾经的证据缺口：`/blog/<id>.json` 返回 0 字节、`api.bgm.tv` 无日志端点，
所以时间只能从页面取，也只能靠站点的其它接口来确认它的时区。）

## 修法

1. `to_datetime(value, *, default_tz=UTC)` —— 裸字符串按 `default_tz` 解释；
   带偏移的字符串不受影响（自己的时区优先）。**默认仍是 UTC**，其它平台不回归
   （twitter 的 `+0000`、bilibili 的 unix 时间戳、linux.do 的 ISO Z 都自带时区）。
2. `provider_api/bangumi.py` 加常量 `BGM_TIMEZONE = timezone(timedelta(hours=8))`，
   blog 与话题的时间都走 `to_datetime(text, default_tz=BGM_TIMEZONE)`。

## 验证（用页面原文自证）

| 来源 | 值 |
| --- | --- |
| 页面 `#3` 小字 | `2026-10-5 18:47` |
| 解析结果 | `2026-10-05 18:47:00+08:00` |
| 页脚兜底文字 | `2026年10月5日 18:47` |

改之前页脚会显示 `2026年10月6日 02:47`（多 8 小时）。

测试：`lib/test/test_helpers_metadata.py`（`default_tz` 的语义 + 默认不回归）、
`lib/test/test_bangumi_blog.py` / `lib/test/test_bangumi_group_topic.py`（时区断言）、
`test/test_bangumi_time.py`（**端到端**：页面文字 → 页脚 unix 换算回北京时间）。

## 教训

**抓 HTML 的平台，页面上是站点本地时间**。接新平台时先确认它的时区（最省事的判据：
看它的 API 返回的时间戳带什么偏移），不要默认 UTC —— 差 8 小时不会报错，
只会安静地错。验证也要端到端：比对**用户看到的那一行**，不是中间字段。
