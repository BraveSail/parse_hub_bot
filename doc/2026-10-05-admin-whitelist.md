# 管理命令：白名单 + /add /list /purge

日期：2026-10-05
触发：用户「加一个清理缓存命令 /purge」→ 追加「权限弄白名单，我负责用 /add 加白，
我可以用 /add 直接回复某人信息给他加白，也可以 /add uid 加白，/list 查看白名单列表，
这些命令仅白名单用户可用」

## 背景（为什么需要）

这个项目有两层缓存，**旧缓存会让渲染/统计停留在旧格式**：
之前多次遇到「旧缓存里的作者行还是旧格式」「统计是旧值」—— 用户只能靠"重发一次"碰运气
（命中缓存反而看不到新效果）。

| 层 | 存什么 | 接口 |
| --- | --- | --- |
| `persistent_cache`（DB） | 解析字段 + 媒体 **file_id**，按 `sha256(raw_url)` | `remove(url)` |
| `parse_cache`（内存 TTL 5 分钟） | 解析结果对象，key = `raw_url` | `pop(key)` |

`/jxjx` 是**绕过**缓存（不清），`/purge` 是**清掉**缓存 —— 两者互补。

## 设计

```
/add <uid>          加白 (数字 ID)
/add                回复某人的消息 → 取其 id
                      私聊里优先取被回复消息的 forward_from (原转发者)
/list               列出白名单
/purge <链接>        清该链接的解析缓存 (persistent + 内存)
/purge              回复一条含链接的消息时清那条
```

### 权限：白名单

**三个命令都仅白名单用户可用**（普通解析不变 —— 已有规则「与 bot 同群可用」照旧）。

- **第一人从配置读**：`core/config.py::admin_users`（逗号分隔 ID）—— 改 env 即生效，
  不用改代码；这是"永远允许"的一层。
- **之后靠 `/add`**：存 DB 新表 `admin_users`。
- 判定 = **配置里的 ∪ DB 里的**。
- 非白名单调用 → 回「无权使用该命令」（不静默：静默会让人以为 bot 坏了，与 `/cfg` 既有做法一致）。

**为什么新建表而不是给 `users` 加列**：`users` 表是"用户偏好"（语言等），授权是另一回事；
混在一起以后清用户数据会误伤授权。

**不需要 alembic 迁移**：`db/init.py` 里 `Base.metadata.create_all` **先跑**，
新建的表会自动创建（它只建缺失的表、不改已有表）。加列才需要迁移。

**反馈**：逐条回「已清除 / 没有缓存」，不要只说"完成"（用户要看得出哪条真的清了）。

**为什么不提供"清空全部"**：一次清掉几万条 file_id 缓存，之后所有人的解析都会变慢，
破坏性远大于收益；按链接清已经覆盖实际需求。

## phase0 — 白名单底座（配置 + 表 + repo + service）

产物：
- `core/config.py::admin_users`（逗号分隔的 ID 串 + 解析辅助）
- `db/models/admin_user.py`（`telegram_user_id` 唯一 / `added_by` / `created_at`）
- `repo/admin_user.py`（get / add / list_all）
- `services/admin_user.py`（`is_allowed` / `add` / `list_all`，合并配置那层）

验证：单测（配置里的算白名单 / 重复 add 不炸且不重复插入 / list 稳定排序）。

## phase1 — 三个命令

产物：`plugins/admin.py`（`/add`、`/list`、`/purge`，都先过 `is_allowed`）。

`/add` 取人的顺序：命令参数是数字 → 用它；否则取被回复消息的
**`forward_from`（原转发者）→ 当前消息发送者**（私聊里回复的其实是转发内容）。

`/purge` 链接取法与 `/jx` 一致：命令参数优先，否则被回复消息的 text/caption。

验证：单测（白名单外拒绝 / 回复转发取原发送者 / `/add uid` / purge 两层都清）。

## phase2 — 清缓存

```python
raw_url = await ParseService().get_raw_url(url)   # 与写入时同一把 key
await persistent_cache.remove(raw_url)
await parse_cache.pop(raw_url)
```
**必须走 `get_raw_url`** —— 缓存 key 是 `sha256(raw_url)`，拿用户原始输入去清会 hash
对不上、**清不掉**。

验证：单测（断言两处都用 `get_raw_url` 的结果；且确实调了 remove/pop）。

## phase3 — i18n

新增词条（**走 `uv run python i18n.py` / `i18n.build`，不手写 yaml**）：
`已清除缓存`、`没有缓存`、`无权使用该命令`、`已加入白名单`、`已在白名单中`、
`白名单为空`、`请回复某人的消息，或给出用户 ID`；复用已有的
`请加上链接或回复一条消息`。

`plugins/helpers.py::build_start_text` 的命令列表补三行。

验证：`i18n.build` + 守卫 translator 输出 `Content unchanged, skipping build`。

## phase4 — 真机验证

产物：容器内对一条**已缓存**的链接执行 `/purge`，再 `/add` + `/list`。
验证：purge 后 `persistent_cache.get(raw_url)` 由有变无。

## 边界

- **不碰** `access_gate` 的缓存（那是 inline/guest 门禁，与解析缓存无关）。
- 清缓存**不删下载目录**（那是临时的，本来就按次清理）。
- 白名单**不做 `/del`**（用户没要求；要删直接改 DB/配置）。

## 落地结果 (2026-10-05)

- `plugins/admin.py` 三个命令; 白名单 = 配置 (`ADMIN_USERS`) ∪ DB `admin_users` 表。
- i18n: 8 条新词条 × 16 语言 (key = `md5(原文)[:12]`, 手工插入 —— 环境里没有 `OPENAI_API_KEY`,
  `i18n.py` 的 LLM 翻译跑不了)。
- 测试: `test/test_admin_commands.py` 20 条; bot 367 passed; `check.sh` 干净。
- 真机 (161 容器): 配置白名单 `[1879026273]`; 陌生人拒绝; 加白幂等; 列表合并排序;
  清缓存 `前 True → 后 False`; `/list` 与 `/purge` 两个 handler 实际执行并发出回复。
- **管理命令不进公开命令菜单** —— 它们是私有工具, 列出来只会让普通用户被拒一次。


## 追加 (同日): `/purge all` 与 `/del`

- `/purge all` 清空**两层**缓存并报出各清多少条。多于一个参数时**不**当清空处理
  (多一个词不该静默清库)。不做"按平台清"之类的中间档 —— 代价一样难解释。
- `/del <uid|@用户名|回复>` 与 `/add` 同一套取人逻辑。
- **配置里的用户删不掉**（那层在 `.env`）—— `/del` 回的是「在配置 (ADMIN_USERS) 里，需改 .env
  才能移除」而**不是**「已移出」。谎称删掉的话用户会发现人还在白名单里。
- i18n: 4 条新词条 × 16 语言。

## 缓存在哪 (用户问的，实测)

**不是 redis**，两层：

| 层 | 位置 | 内容 | TTL |
| --- | --- | --- | --- |
| `parse_cache` | **进程内存** dict | 解析结果对象 | 300s |
| `persistent_cache` | **SQLite** 表 `cache` | 解析字段 + 媒体 **file_id** | 7 天(stale 淘汰) |

- 生产：宿主 `/root/parse_hub_bot/data/db/database.db`（容器内 `/app/data/db/`；
  compose 挂 `./data:/app/data`）。161 的 `.env` **没有** `DATABASE_URL` → 用默认
  `sqlite+aiosqlite:///data/db/database.db`。WAL 模式。
- 实测规模：**36 行 / 33.5 KB / 平均 954 bytes**。
- 媒体存的是 **file_id 字符串**（`AgACAgUAAxUAAWrCRY1…`，约 80 字符）**不是文件本体**：
  缓存命中省下的是**重新下载 + 重新上传媒体**，不是一次 DB 查询。所以 `/purge all`
  的代价是"之后每条链接都要重新走完整流程"，DB 那 33 KB 不是重点。

## 真机验证 (2026-10-05)

- `/purge all`：cache **36 → 0**；验完把备份的行**写回**（缓存可再生，但用户没要求清空，
  不该顺手清掉）。恢复后 36 行。
- `/del`：临时用户加入 → 删除 → 白名单回到原样；`/del` 配置里的人走提示分支、白名单不变。
- 两条消息都真的出现在 DM 里（handler 走的是真 `MessageSender`）。
