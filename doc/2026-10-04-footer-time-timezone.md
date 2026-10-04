# 页脚时间的时区：交给客户端

日期：2026-10-04
来源：用户「页脚的时间，api有发送时间戳客户端自动转换本地时区的接口，研究下」+「24小时制」

## 问题

页脚时间原来是**服务端算好再发出去**：

```python
local = value.astimezone(ZoneInfo("Asia/Shanghai"))
clock = f"{local.hour:02d}:{local.minute:02d}"
return [clock, f"{local.year}年{local.month}月{local.day}日"]
```

结果：**所有用户看到的都是北京时间** —— 人在别的时区，看到的是别人的钟点。

## 调研结果

Telegram 有**时间实体**：服务端只发一个 UTC unix 时间戳，**客户端按自己的时区渲染**
（跟"消息发出多久前"那种相对时间是同一套）。

| 形态 | 结果 |
| --- | --- |
| markdown 里的链接语法 `[文字](tg://time?unix=…&format=…)` | ✗ 渲染成**字面链接** `RichTextUrl`（点了会跳 tg://，不做本地化） |
| **markdown 里直接写 `<tg-time unix=… format=…>文字</tg-time>`** | ✓ → `RichTextDateTime` |
| HTML 路径的 `<tg-time>` | ✓ 同上 |
| blocks 路径的 `RichTextDateTime(text, date, date_time_format)` | ✓ 同上 |

**关键**：markdown 路径**认 HTML 的 `<tg-time>` 标签**（虽然不认它自己的 `tg://time` 链接语法）。
所以**不用换渲染路径**，只改页脚那段字符串。

`format` 取值（`r|w?[dD]?[tT]?`，pyrogram 会校验）：

- `r` 相对时间（"3 天前"）
- `w` 星期、`d` 短日期、`D` 长日期
- `t` 短时间、`T` 长时间
- **`Dt` = 长日期 + 短时间** → 「2026年10月4日 12:34」（用户选定）
- **短时间 `t` 是 24 小时制**

实测（真机服务端块）：

```
[RichBlockFooter] RichTextDateTime  date=2026-10-03 15:00:04  format=Dt
```

`date` 存的是 **UTC 04:34 之前的那个 instant**（1791039604）—— 服务端只存时刻，不存钟面。

## 实现

`plugins/helpers.py` 的 `_format_published`：

```python
return [f'<tg-time unix="{int(local.timestamp())}" format="Dt">{fallback}</tg-time>']
```

- 标签内文字是**兜底**：老客户端不认 `tg-time` 时照常显示，内容与客户端渲染形态一致
- 时区锚点**不变**（仍是 `Asia/Shanghai`）—— 这样时间戳与改之前显示的日期一致，
  只是呈现交给了客户端
- 两段（时间 + 日期）合并成一段：一个实体渲染出「日期 时间」

## 验证

bot 309 passed（`_time_tag` helper 统一构造断言）、`check.sh` 干净；真机 `RichTextDateTime` ✓。

## 附带确认

`RichTextDateTime` 在**富文本的 markdown 与 HTML 两条路径都可用**，也能放进
`<details>` 折叠内和引用块内（都实测过）。
