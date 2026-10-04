# 缓存命中时敏感内容不打码（R18 遮罩丢失的真正根因）

日期：2026-10-04
来源：用户「自动遮罩修了？？ 我刚DM还是没」+「本来自动识别R18遮罩了被你改的没遮罩了」

## 结论先说

**用户两次都对，我两次都搞错了方向。** 真实根因：

> **缓存路径（`send_cached`）从来不打码** —— 它是长期存在的 bug，不是本轮改动引入的。

## 根因

敏感内容有**两条发送路径**，只有一条会打码：

| 路径 | 媒体构造 | 打码 |
| --- | --- | --- |
| **直发**（`send_rich_media`） | `build_rich_media(...)` → `SpoilerPhotoBlock` + `is_sensitive and media_blocks` 时切 **blocks** | ✓ |
| **缓存命中**（`send_cached` → `build_cached_rich_content`） | `cache_media_blocks(entry)` → **裸 `InputMediaPhoto(file_id)`**，且用 **markdown** 发 | ✗ |

- **markdown 路径的媒体块打不了码**：官方 API 的 `InputRichBlockPhoto` 没有 spoiler 字段，
  只有 raw 的 `PageBlockPhoto/Video(spoiler=)` 才有（这条在项目文档里早就写着）。
- `cache_media_blocks` 造媒体时**完全不涉及 spoiler**。
- 讽刺的是：`CacheParseResult` **一直带着 `is_sensitive`**（`rich_cache_entry` 也一直写入），
  **只是渲染时没人读它**。

### 为什么表现成"第一次有、第二次没有"

> **同一个链接的第二次发送必然命中缓存。**

用户 DM 里反复发同一条 → 第一次（直发）有遮罩，之后全走缓存 → 没遮罩。

## 修法

1. `cache_media_blocks` 在 `entry.parse_result.is_sensitive` 时**额外产出一份 media_blocks**
   （`SpoilerPhotoBlock` / `SpoilerVideoBlock`），返回 5 元组。
2. `build_cached_rich_content` 把 blocks 透给调用方（返回 3 元组）。
3. 新增 **`cached_rich_message(markdown, media, media_blocks)`** —— 按是否敏感选路径：
   有 blocks 走 `markdown_to_blocks` + `InputRichMessage(blocks=…)`，否则维持 markdown。
4. **三条缓存调用方共用这一个 helper**（私聊/群 `send_cached`、inline `build_cached_rich_result`、
   guest 缓存分支），避免三处各写一遍判断。

## 验证

- bot **331 passed**（新增 5 条：敏感条目产出 spoiler 块 / 不敏感不产出 / 选 blocks 路径 /
  不敏感仍走 markdown；外加 guest 与契约更新）、`check.sh` 干净。
- **真机 · 用用户那条 URL 的真实缓存条目**走 `send_cached`：

```
缓存里的 is_sensitive = True
渲染路径 = blocks                                     ← 修复前是 markdown
块 (含嵌套) = [Paragraph, SpoilerPhotoBlock, Divider, Footer]
已发送 msg_id=761
   RichBlockPhoto has_spoiler=True                   ← 服务端确认打码
```

## ⚠️ 我在这一轮里犯的两个错误（都要记住）

### 1. 探针读错字段名，造出一个假故障

我用 `getattr(block, "spoiler", None)` 读打码标记，读到 `None`，据此**断定
"R18 遮罩被我改坏了"**，并**回退了无关的改动**（处理过程统一排版）。

**正确字段是 `has_spoiler`**（pyrogram 的块上）。读对之后 `has_spoiler=True`。

⇒ **用探针断言字段之前先核实字段名**（打印对象的实际字段 / 读源码）。
一个猜错的字段名会造出"功能坏了"的假象，而且自己不会怀疑它。

### 2. 只验证了一条路径就说"没问题"

我上一轮"遮罩一直是好的"结论**只对直发路径成立**。用户说"还是没"时，
我去查了直发路径、看到 `has_spoiler=True`，就没再查**缓存路径**。

⇒ **敏感内容有两条发送路径，验证时必须都过。** 用户报"还是没"时，
先问"他这次的链接命中缓存了吗" —— 命中就是另一条代码路径。

（这条项目文档里早有记录：*"两条发送路径都要覆盖，漏一条就是'第一次打码、第二次不打'"* ——
但当时没有覆盖到"默认就是缓存命中"这个高频场景。）

## 补救

新加的 4 条测试**盯住了缓存路径的 spoiler**，将来任何改动碰坏它会立刻红灯
（而不是再靠肉眼和猜测）。
