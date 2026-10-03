# 计划: 视频封面 + 合并重复的富文本媒体构建 + bilibili 作者链接

## 背景（取证结论）

1. **视频没封面（所有平台）**：`build_rich_media` / `build_media_items` 只传了
   `video_cover=<thumb_url>`（URL），**从未传 `thumb`**。pyrogram 的
   `_get_input_document()` 只返回 `InputDocument(id, access_hash, file_reference)`，
   `video_cover` 被丢弃 → 服务端 document 无封面。
   实测：传本地 `thumb=<320px JPEG>` 后，服务端返回的 document `thumbs[0]` 存在
   （320x180 / 5970 字节）→ **封面能保留，方案可行**。
2. **同一件事有两份实现**：`plugins/parse/sender.py::build_rich_media` 与
   `plugins/parse/inline_rich.py::build_media_items`（一个用类型 match，一个用
   类名字符串匹配）。用户明确要求过统一。
3. **bilibili 作者**：parser 只传 `author_name`；`view["owner"]["mid"]` 可用但没传，
   导致 `author_url` 为空、名字不可点（模板 `https://space.bilibili.com/{id}` 已存在）。

## 阶段

### phase0: 合并媒体构建为单一实现
- 产物：`plugins/parse/inline_rich.py::build_rich_media`（唯一实现，含 `thumb` 逻辑），
  `sender.py::build_rich_media` 删除，改为从 inline_rich import；
  `build_media_items` 保留为同名别名（或直接删除并改调用点）以免破坏导入。
- 验证：`grep -c "def build_rich_media\|def build_media_items"` 只剩一处；
  bot 测试全过。

### phase1: 视频封面
- 产物：新 helper `plugins/parse/covers.py::prepare_thumb(thumb_url, *, proxy) -> Path | None`
  （下载 → PIL 缩放 ≤320px → JPEG → 缓存目录按 URL hash 命名；失败返回 None 静默降级）。
  `build_rich_media` 对 `VideoFile`/`LivePhotoFile` 传 `thumb=`（本地文件）
  并保留 `video_cover=`（URL 双保险）。函数变 async，两个调用方同步改。
- 验证：真实 bilibili 链接走生产 pipeline → 断言服务端 document `thumbs[0]` 非空。

### phase2: bilibili 作者链接
- 产物：`lib/src/parsehub/parsers/parser/bilibili.py` 传 `author_url`
  （`profile_url(Platform.BILIBILI, user_id=owner["mid"])`）+ `author_handle`。
  `plugins/helpers.py::format_author_line` 支持"有 url 无 handle 时把名字做成链接"。
- 验证：真实链接渲染出 `**<a href="https://space.bilibili.com/...">名字</a>：**`。

## 回滚
`git revert` 对应 commit；生产 `docker tag shirobakobot:before-merge shirobakobot:local` 可用。
