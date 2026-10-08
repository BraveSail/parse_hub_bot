# 「cannot write mode P as JPEG」：降采样按真实格式存 + twitter 图片扩展名

日期：2026-10-08
触发：用户解析 `https://twitter.com/kon_kokine/status/2107756845704360201` →
`▎媒体处理错误: cannot write mode P as JPEG`（整条解析失败）

## 根因（生产日志 + 活体复现）

生产日志（容器 `shirobakobot-bot-1`）：

```
[Pipeline][39e5244d] cannot write mode P as JPEG
  File "/app/utils/media_processing_unit.py", line 208, in _downscale_image
    resized.save(out_path)
OSError: cannot write mode P as JPEG
```

复现（同一容器跑真实 parse+download+process）：

```
media: ImageRef url=https://pbs.twimg.com/media/HUBBoF9bkAA1jCq.png?name=orig ext=jpg
下载: 001_ブーン.jpg   后缀=.jpg 真实格式=PNG mode=P size=(1654, 2756)
CDN: content-type=image/png
处理: 图片长边超限(2756px > 2560px) → ❌ OSError: cannot write mode P as JPEG
```

**两个独立缺陷叠加**：

1. **崩溃点（通用）**：`_downscale_image` 里 `resized.save(out_path)` 不传 `format=`，
   Pillow 于是用**文件后缀**推断编码器 —— 文件叫 `.jpg` 就按 JPEG 编，而内容是 **PNG 调色板图
   （mode `P`）**，JPEG 编不了它 ⇒ 抛错，整条媒体处理失败。
   （`process_image` 的 `needs_rgb = image_mode == "RGBA"` 只管了 RGBA，`P` 模式不触发任何转换，
   直接落到这里。）
2. **错误来源（twitter 这次撞上）**：`ImageRef.ext` 默认 `"jpg"`，twitter 解析器构造时没传 ext，
   而这条的 URL 明明是 `.../HUBBoF9bkAA1jCq.png?name=orig`。下载命名用
   `{index:03d}_{name}.{media.ext}`（`types/result.py:219`），于是 PNG 被命名成 `.jpg`。

## 改动

**① `utils/media_processing_unit.py::_downscale_image`（崩溃点）**

保存格式与输出后缀都跟**真实格式**走（`_PIL_FORMAT_SUFFIX` 做 Pillow 格式名 → 后缀映射），
不再靠后缀猜：后缀撒谎的 PNG 现在降采样成 `.png`（内容仍是 PNG、mode 仍是 `P`）。
拿不到真实格式时退化回原行为；真 JPEG 走原路径（后缀与格式都不变，零行为变化）。

**② `lib/src/parsehub/parsers/parser/twitter.py`（来源）**

`_image_ext_from_url()`：先取 URL **path** 上的扩展名（`.../HUBBoF9bkAA1jCq.png?name=orig`），
path 没有时取 query 的 `format=`（X 的卡片图 `.../card_img/123/AbC?format=jpg` path 无扩展名），
都没有才退回默认 `jpg`。`to_media_refs()` 的 `TwitterPhoto` 分支用它。

## 验证

- **新增 `test/test_media_processing_unit.py`（6 例）** —— 这个类**此前没有任何测试**。
- **新增 `lib/test/test_twitter_media_ext.py`（6 例）**。
- **两处都做了反向验证**：把修复临时改回去 → `cannot write mode P as JPEG` 重现在对应用例上
  （媒体处理 2 例失败、twitter ext 4 例失败），恢复后 md5 一致。
- lib 全量 **822 passed / 1 skipped**，`scripts/check.sh` 干净。
- **生产真机**（部署 `42e2252` 后，同一容器同一推文）：

  ```
  media: ImageRef url=...HUBBoF9bkAA1jCq.png?name=orig ext=png
  下载: 001_ブーン.png   后缀=.png 真实格式=PNG mode=P
  处理: 图片长边超限(2756px > 2560px) → OK -> 001_ブーン_downscaled.png
  ```

## 同类崩点排查（顺带查了）

`MediaProcessingUnit` 里其余会写图的地方对 P 模式**都安全**（实测）：

| 路径 | P 模式表现 |
| --- | --- |
| `_pad_image` → `ImageOps.expand(P, fill=(r,g,b))` | ✅ 返回 P 模式，不抛错 |
| `_do_split` → `crop` + `save(.png)` | ✅ 输出仍是 P 模式 PNG |
| `_get_dominant_color` → haishoku 读 P 模式 PNG | ✅ 能取到调色板 |
| `_downscale_image` → `save(按后缀)` | ❌ **本次崩点**（已修） |

## ⚠️ 遗留：其他平台的"后缀撒谎"（未修，需决策）

多数 parser 构造 `ImageRef(url=...)` 时**没传 ext**（用默认 `jpg`），所以只要平台图片 URL 是
`.png`/`.webp`，下载文件名就会撒谎。已修的是 twitter。

**不会再崩**（① 通用兜住了），但**文件名不准**。涉及点（源码层清单，未逐个用真实样本验证）：

`threads` / `coolapk` / `linuxdo` / `instagram` / `tieba` / `tiktok` / `zhihu` / `weibo` /
`pixiv` / `youtube` / `zuiyou` / `douyin` / `xiaoheihe` / `bilibili` / `bangumi`

（`weixin` / `xhs` / `kuaishou` 已经自己传了 ext。）

统一做法可选：把 `_image_ext_from_url` 提到 lib 的 utils，各 parser 复用；或在 `MediaRef` 层
做兜底（影响面更大，需谨慎）。**本次没做** —— 报障链路只需 twitter。
