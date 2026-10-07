# 推特「统一卡片」里的媒体（unified_card）

日期：2026-10-07

## 症状

用户报 `https://x.com/Apple/status/2104922864910815586` **抓不到视频**。

## 根因

那条推文的 `legacy` 上**只有 `entities`**（连 `extended_entities` 都没有），
里面一个 media 都没有 —— 而我们的媒体只从这一条路取：

```python
media = legacy["entities"].get("media", [])   # 空
```

于是媒体数 0，发出来只剩正文。

**视频其实在「统一卡片」里**：`card.legacy.binding_values[key=unified_card]`
是一段 **JSON 字符串**（不是结构化卡片字段）：

```json
{
  "type": "video_website",
  "component_objects": {"details_1": {...}, "media_1": {"type": "media", "data": {"id": "13_<media_id>"}}},
  "destination_objects": {"browser_with_docked_media_1": {"data": {"url_data": {"url": "https://www.apple.com/…"}}}},
  "media_entities": {"13_<media_id>": {"type": "video", "video_info": {...}, "original_info": {...}}},
  "components": ["media_1", "details_1"]
}
```

视频在 `media_entities` 里（键是 `media_key`），字段与 `legacy.entities.media`
**同构**（`type` / `media_url_https` / `video_info` / `original_info`）。

## 改动

1. 媒体构造抽成一个共用函数 `_media_from_dict`（legacy 与 unified card 两条路共用 ——
   以前是内联在媒体循环里的，接卡片时才发现要复用）。
2. 新增 `_parse_unified_card_media(node)`：解析那段 JSON，取 `media_entities`。
3. `_parse_card_photo` **显式跳过** `unified_card` —— 那是推文主体的媒体，
   不是外链预览图；不跳的话同一张图会进两次。
4. **顺带修了码率选取**：`_best_video_url` 改为优先**最高码率的 mp4**。

## 顺带修的：一直在发最糊的那档

X 给的 `variants` 里 m3u8 排第一，mp4 的**顺序没有保证** —— 实测这条卡片是

```
application/x-mpegURL  (m3u8)
video/mp4  950000      480x600
video/mp4  2176000     720x900   ← 最好
video/mp4  632000      320x400   ← 最差
```

以前取 `variants[-1]`，**正好是最糊的 632k 那档**。现在按 `bitrate` 取最高
（兼容长文章那条路用的 `bit_rate` 字段名），没有 mp4 时回退最后一个变体（HLS）。

⇒ 这条对所有推特视频生效：以前发的可能一直是低码率版。

## 验证

真机端到端（生产容器，同一条流水线）：

| 项 | 值 |
| --- | --- |
| 媒体数 | 1（`VideoRef`）|
| 直链 | `…/vid/avc1/720x900/nZUAXIBlF33JkOqB.mp4`（最高码率那档）|
| 宽高 | 1440x1800 |
| 下载落地 | 500451 字节 |
| 发送 | True |

测试：`lib/test/test_twitter_unified_card.py`（13 项：真实 fixture + 码率选取 +
legacy 媒体回归 + 坏 JSON / 缺字段 / 数组形态容错）；fixture
`lib/test/fixtures/twitter_unified_card.json`（真实响应，删掉 `ext` 调色板噪音）。

## 教训

**X 的媒体不止 `legacy.entities.media` 一条路**。目前已知四条：

| 来源 | 形态 |
| --- | --- |
| `legacy.entities.media` | 推文自带的图 / 视频 / 动图 |
| `card.legacy.binding_values` 的图字段 | 外链预览图（`card_img/…`）|
| `card.legacy.binding_values.unified_card` | **JSON 字符串**，内含 `media_entities` |
| `note_tweet` | 长文章内嵌媒体 |

报「抓不到媒体」时，先把这几条都 dump 一遍再下结论 —— 只看 `entities` 会得出
「API 里没有」的错误结论。
