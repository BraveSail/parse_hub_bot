# 计划: 白名单门禁 —— 只有"与 bot 同群"的用户能用 inline / guest

## 背景（已取证）

- **guest mode 已开着**（`getMe.supports_guest_queries=True`、raw `bot_guestchat=True`），
  但**没有任何 guest handler** —— 收到 guest query 直接丢弃，实际不可用，也无门禁。
- **inline 无任何权限检查**：任何人任何聊天都能用。
- **bot 不能列自己所在的群**（`messages.GetDialogs` → `400 BOT_METHOD_INVALID`，实测）。
- **bot 能对指定群查成员**：`getChatMember(chat_id, user_id)` —— bot 不在的群报 `ChannelInvalid`（实测）。
- bot 已知的群：`chats` 表（DB 实测有 `-1001481033767`）。

## 用户决策

- 只认**一个白名单群**（用户原话："只能弄一个群，然后加入这个群的用户可以调用，不然群多了就flood了"）
  → 判定只需**一次** API 调用，不遍历、不 flood。
- 私聊的 inline **放行**（用户主动找 bot，无扩散问题）；群/频道里的 inline 与 guest 才要求同群。
- 判定按**发起用户**（inline 拿不到发生群，只能这样；guest 虽带发生群，但场景上同样收敛到用户判定）。

## 设计

配置：`BotSettings.guest_whitelist_group_id: int = 0`（0 = 不启用门禁，保持现状向后兼容）。
填在 `.env` / compose 环境变量 `GUEST_WHITELIST_GROUP_ID`。

门禁模块 `plugins/parse/access.py`：

```python
async def is_allowed(cli, user_id) -> bool:
    if not bs.guest_whitelist_group_id: return True      # 未配置 = 不限制
    # TTL 缓存命中直接返回
    # 否则 getChatMember(group, user_id): 成功且非 LEFT/BANNED → True
```

- 缓存：通过 → 10 分钟；拒绝 → 1 分钟（用户可能刚入群，不能长缓存）。
- 任何 API 异常 → **拒绝**（fail-closed，门禁不能因为异常而放行）。

## 阶段

### phase0: 配置 + 门禁模块
- 产物：`core/config.py` 加 `guest_whitelist_group_id`；`plugins/parse/access.py`
  （`is_allowed` + TTL 缓存 + 可注入的 client 便于测试）。
- 验证：单测覆盖 未配置放行 / 命中放行 / 不在群拒绝 / 异常拒绝 / 缓存生效（断言 API 只调一次）。

### phase1: inline 门禁
- 产物：`plugins/parse/inline.py` 两个 inline handler 都检查；不通过 → 返回
  "无权限"提示项（i18n 文案，16 语言）。
- 验证：真实调用未白名单用户的 inline → 看到提示而非解析结果。

### phase2: guest handler
- 产物：新建 `plugins/parse/guest.py`，`@Client.on_guest_message()`；
  门禁 → 检测纯链接 → 解析 → `answer_guest_query` 回复。
- **前置验证**：guest 回复能否再编辑成带媒体（EditInlineBotMessage + 返回的
  `InputBotInlineMessageID`）—— 不能的话就只回富文本/单媒体，并如实说明。

### phase3: 生产验证
- 产物：161 部署 + 真实群调用取证（白名单内放行、外拒绝）。

## 回滚
`git revert`；配置项默认 0 = 不启用，不会影响现有行为。
