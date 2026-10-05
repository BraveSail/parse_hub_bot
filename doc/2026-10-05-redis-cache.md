# 缓存换 Redis

日期：2026-10-05
触发：用户「改成redis」

## 已确认的现状（调研，实测）

| 项 | 实况 |
| --- | --- |
| 缓存层 | 两层：`parse_cache`（进程内 dict，TTL 300s）+ `persistent_cache`（SQLite 表 `cache`） |
| 生产 DB | 宿主 `/root/parse_hub_bot/data/db/database.db`（容器 `/app/data/db/`），WAL；36 行 / 33.5 KB |
| 存什么 | 解析字段 + 媒体 **file_id 字符串**（不是文件本体） |
| 161 的 Redis | **已存在**：宝塔装的 `/www/server/redis`，`bind 127.0.0.1`、**无 requirepass**、`protected-mode yes` |
| bot 容器网络 | `shirobakobot_default`（172.19.0.2）→ 连不到宿主 loopback |
| bot 是否用本地 Bot API 容器 | **否**（`telegram-bot-api` 是别的项目的）→ 改 host 网络不会打断容器名解析 |

## 用户决策

1. **Redis 部署**：bot 容器改 **host 网络**，连宿主 `127.0.0.1:6379`（不改宝塔配置）。
2. **范围**：**两层都进 Redis**。

## 关键技术约束（决定怎么做）

1. **`ParseResult` 不是 pydantic，且没有 `from_dict`** → 反序列化要自己写。
2. **`MediaRef.asdict` 丢类型**：`VideoRef`/`AniRef` 字段集几乎相同，无法从 dict 反推类型。
   天真往返会把动图/视频搞混（影响发送形态）→ 序列化**必须显式带媒体类型**。
3. **`RichTextParseResult.content` 是派生属性**（由 `markdown_content` 算）→ 重建时**只传
   `markdown_content`**，回填 `content` 会被 `__init__` 覆盖（不报错、静默不一致）。
4. **`to_dict()` 的输出格式被既有测试逐字段冻住**（`lib/test/test_core_offline.py`
   `assertEqual(to_dict()["media"], [...])`），且它是 CLI / 落盘 JSON 的**公开格式**
   → **不动它**，另写一套给缓存用的往返函数。
5. **Redis 是共用的**（宝塔那个可能还有别的项目在用）→ **所有 key 必须带前缀**
   `shirobako:`，`/purge all` 的"清空"只删前缀匹配的 key（`SCAN` + `DEL`）。
   **绝不允许 `FLUSHDB`/`FLUSHALL`** —— 那会删掉别人的数据。

## phase0 — lib：结果对象的无损往返

产物：`lib/src/parsehub/types/serialize.py`
- `result_to_cache_dict(result) -> dict` / `result_from_cache_dict(data) -> AnyParseResult`
- 按 `type`（PostType）分发到四个具体类
- media 项**带 `kind`**（`video`/`image`/`animation`/`live_photo`），重建时按 kind 分发
- RichText：只回填 `markdown_content`，`published_at` 由 isoformat 还原成 `datetime`

验证：`lib/test/test_result_roundtrip.py` —— 四种结果类型 × 四种媒体类型往返后
**字段逐个相等**（含媒体类型、时间、tags、引用媒体计数）+ RichText 的 `content == plaintext_content`。
**自证**：故意把 kind 去掉跑一次，必须红（证明测试真的在测类型）。

## phase1 — Redis 客户端与配置

产物：
- 依赖 `redis`（asyncio）加进 `pyproject.toml`
- `core/config.py::redis_url`（默认 `redis://127.0.0.1:6379/0`）+ `cache_key_prefix`（默认 `shirobako:`）
- `services/redis_client.py`：单例连接池、`get_redis()`、`close_redis()`

验证：单测（前缀拼 key、URL 解析）；真机 `ping`。

## phase2 — 持久层换 Redis

产物：`services/cache.py::PersistentCache` 读写走 Redis
- key：`{prefix}parse:{sha256(raw_url)}`
- 值：`{"url":…, "entry": CacheEntry.model_dump(mode="json")}`（CacheEntry 已是 pydantic，往返无损）
- TTL：7 天（`SETEX`），替代原来的 stale 淘汰
- 保留既有语义：**旧缓存缺字段要重新解析**（原来靠 `model_fields_set` 判断，序列化后仍可用）
- `remove` / `clear`（`SCAN MATCH {prefix}parse:*` + `DEL`，返回条数）

**旧 SQLite 表 `cache` 不迁移**：缓存可再生（最坏是首次全部 miss，慢一次）。
表留在库里不删（避免破坏性 DDL），代码不再读写；在文档里写明。

验证：单测（key 格式 / TTL 参数 / 缺字段回退 / clear 只删前缀内的 key）。

## phase3 — 内存层换 Redis

产物：`services/cache.py::TTLCache` → `ResultCache`（接口保持 `get`/`set`/`pop`/`clear`，异步）
- key：`{prefix}result:{sha256(raw_url)}`，值 = `result_to_cache_dict` 的 JSON，TTL 300s
- 调用方（`handlers.py` / `inline.py` / `admin.py`）**不改**

验证：单测（往返后对象可用、TTL、clear）+ 真机（解析一次 → key 在 → 5 分钟内命中）。

## phase4 — 部署：host 网络

产物：
- `compose.deploy.yaml`：`bot` 加 `network_mode: host`（无 ports，无影响）
- 161 `.env`：`REDIS_URL=redis://127.0.0.1:6379/0`

**取舍（已知并接受）**：host 网络 = 失去容器的网络隔离（bot 直接暴露在宿主网络栈上）。

验证：容器内 `redis-cli -h 127.0.0.1 ping` → PONG；`redis-cli keys 'shirobako:*'` 看到 key。

## phase5 — 真机端到端

产物：容器内真跑
1. 解析一条新链接 → `shirobako:result:*` 与 `shirobako:parse:*` 出现
2. 同链接再解析 → 命中（日志/无重复下载）
3. `/purge <链接>` → 该链接的 key 消失
4. `/purge all` → 前缀内清空、**前缀外（别人的 key）不受影响**（先手动塞一个 `foreign:test` 验证它活着）

验证：`redis-cli` 前后对比 + 断言"外部 key 未被删"。

## 边界 / 不做

- 不迁移旧 SQLite 缓存数据。
- 不改 `to_dict()`（公开格式 + 测试冻住）。
- 不给 Redis 设密码（宝塔那个无密码；**host 网络 + bind 127.0.0.1 下不暴露到公网**）。
  如果以后要改 bind，必须先加 requirepass —— 记进文档。
- 不动 `telegram-bot-api` 容器。


## 落地结果 (2026-10-05, 全部验证通过)

- **lib**: 新增 `parsehub/types/serialize.py`（`result_to_cache_dict` / `result_from_cache_dict`），
  媒体带 `kind`；`lib/test/test_result_roundtrip.py` 11 条。
  写测试时抓到**两个真 bug**：所有类型的 `content` 被丢（我把 content 从公共参数里去掉避 RichText
  派生字段，忘了给其余三个补回）；`Platform(id)` 永远失败（枚举 value 是 tuple）→ platform 静默丢，
  页脚来源消失。
- **bot**: `services/redis_client.py`（前缀、失败退化、SCAN 清、ping）；`services/cache.py` 的
  `ResultCache`（Redis，TTL 300s）+ `PersistentCache`（Redis，TTL 7 天）；`TTLCache` **保留**
  （`plugins/parse/access.py` 的门禁布尔缓存仍用它）。`bot.py` 去掉 `parse_cache.start_cleanup()`
  （Redis 原生 TTL 取代）。
- **部署**: `compose.deploy.yaml` 加 `network_mode: host`；161 `.env` 加
  `REDIS_URL=redis://127.0.0.1:6379/0`。就绪 4s。
- **测试**: bot 391 passed、lib 466 passed、`check.sh` 干净。
- **真机**（161 容器）：
  - 网络模式 `host`；redis ping ✓（8.4.0）
  - 结果层 key `shirobako:result:<sha256>` TTL **300s**、持久层 `shirobako:parse:<sha256>` TTL **604800s**
  - 往返一致：类型/标题/正文/platform/raw_url/媒体类型全对
  - **前缀隔离**：塞入外部 key 后 `/purge all` 只清自己的（result 1 + parse 1），**外部 key 存活**
  - **端到端**：同一链接第一次 **4.5s**（冷启动+写两层）、第二次 **0.6s**（命中）
- **已知代价**（用户已确认接受）：host 网络 = 失去容器网络隔离；旧 SQLite 表 `cache` 不迁移
  （36 行旧数据留在库里不再读写，模型保留是为了 alembic 迁移链）；TTL 固定不随访问续期
  （原来靠 `accessed_at` 做 LRU，现在到期即重解析一次 —— 换来能真正回收空间）。
- **安全前提**：宝塔那个 redis **无密码**且 `bind 127.0.0.1`。host 网络下 bot 走 loopback 连它，
  不暴露到公网。**若以后要把 bind 改成 0.0.0.0，必须先加 `requirepass`** —— 否则等于把无密码
  Redis 放到公网。
