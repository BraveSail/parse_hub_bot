"""管理命令: 白名单判定 / ``/add`` 取人 / ``/purge`` 清缓存。

这些命令是**破坏性**的 (清掉别人解析用的 file_id 缓存) 且是**权限**相关, 所以
把三件事都钉死:
- 谁算白名单 (配置 ∪ DB)、重复加白不写库;
- ``/add`` 回复转发消息时取的是**原转发者** (私聊里最容易加错人的地方);
- ``/purge`` 用的是 ``get_raw_url`` 那把 key (拿原始输入去清会 hash 对不上、清不掉)。
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from services.admin_user import AdminUserService

CONFIGURED = 1879026273
FROM_DB = 111222333
STRANGER = 999888777


def _repo(*, existing: int | None = None, rows: list | None = None) -> MagicMock:
    repo = MagicMock()
    repo.get_by_tg_user_id = AsyncMock(return_value=SimpleNamespace(telegram_user_id=existing) if existing else None)
    repo.add = AsyncMock(return_value=SimpleNamespace(telegram_user_id=0))
    repo.list_all = AsyncMock(return_value=[SimpleNamespace(telegram_user_id=i) for i in (rows or [])])
    return repo


def _service(repo: MagicMock) -> AdminUserService:
    """绕过 __init__ 的 session 依赖, 直接把 mock 仓储塞进去。"""
    svc = AdminUserService.__new__(AdminUserService)
    svc._session = None
    svc._repo = repo
    return svc


# ---------------------------------------------------------------- 白名单判定


def test_a_configured_id_is_allowed():
    repo = _repo()
    svc = _service(repo)
    with patch("services.admin_user.bs", SimpleNamespace(admin_user_ids=[CONFIGURED])):
        assert asyncio.run(svc.is_allowed(CONFIGURED)) is True
    repo.get_by_tg_user_id.assert_not_awaited()  # 配置命中就不必查库


def test_an_id_added_at_runtime_is_allowed():
    repo = _repo(existing=FROM_DB)
    svc = _service(repo)
    with patch("services.admin_user.bs", SimpleNamespace(admin_user_ids=[CONFIGURED])):
        assert asyncio.run(svc.is_allowed(FROM_DB)) is True


def test_a_stranger_is_rejected():
    repo = _repo()
    svc = _service(repo)
    with patch("services.admin_user.bs", SimpleNamespace(admin_user_ids=[CONFIGURED])):
        assert asyncio.run(svc.is_allowed(STRANGER)) is False


def test_no_user_is_rejected():
    """频道消息没有 from_user —— 不能因此放行"""
    repo = _repo()
    svc = _service(repo)
    with patch("services.admin_user.bs", SimpleNamespace(admin_user_ids=[CONFIGURED])):
        assert asyncio.run(svc.is_allowed(None)) is False


def test_the_configured_list_is_parsed_leniently():
    """``.env`` 里手写的东西: 逗号或空白分隔, 认不出来的片段跳过而不是让 bot 起不来"""
    from core.config import BotSettings

    assert BotSettings(admin_users="1, 2  3").admin_user_ids == [1, 2, 3]
    assert BotSettings(admin_users="").admin_user_ids == []
    assert BotSettings(admin_users="me,@name,").admin_user_ids == []


# ---------------------------------------------------------------- 加白


def test_adding_a_new_user_writes_to_the_db():
    repo = _repo(existing=None)
    svc = _service(repo)
    with patch("services.admin_user.bs", SimpleNamespace(admin_user_ids=[])):
        assert asyncio.run(svc.add(STRANGER, added_by=CONFIGURED)) is True
    repo.add.assert_awaited_once()


def test_adding_twice_does_not_insert_again():
    repo = _repo(existing=STRANGER)
    svc = _service(repo)
    with patch("services.admin_user.bs", SimpleNamespace(admin_user_ids=[])):
        assert asyncio.run(svc.add(STRANGER, added_by=CONFIGURED)) is False
    repo.add.assert_not_awaited()


def test_a_configured_user_is_not_copied_into_the_db():
    """配置里已有的不写库 —— 否则从配置删掉时会以为还在白名单里"""
    repo = _repo(existing=None)
    svc = _service(repo)
    with patch("services.admin_user.bs", SimpleNamespace(admin_user_ids=[CONFIGURED])):
        assert asyncio.run(svc.add(CONFIGURED, added_by=CONFIGURED)) is False
    repo.add.assert_not_awaited()


def test_list_merges_config_and_db_sorted_and_deduped():
    repo = _repo(rows=[FROM_DB, CONFIGURED])  # DB 里也有一条与配置重复的
    svc = _service(repo)
    with patch("services.admin_user.bs", SimpleNamespace(admin_user_ids=[CONFIGURED])):
        assert asyncio.run(svc.list_ids()) == sorted({CONFIGURED, FROM_DB})


# ---------------------------------------------------------------- /add 取谁


def test_reply_to_a_forwarded_message_takes_the_original_sender():
    """私聊里 /add 回复的其实是转发过来的内容 —— 要加的是**原发送者**,

    看 ``from_user`` 会加白转发的中间人 (甚至 bot 自己)。
    """
    from pyrogram.types import MessageOriginUser

    from plugins.admin import _target_user_id_from_reply

    reply = SimpleNamespace(
        forward_origin=MessageOriginUser(sender_user=SimpleNamespace(id=FROM_DB)),
        from_user=SimpleNamespace(id=CONFIGURED, is_bot=False),
    )
    assert _target_user_id_from_reply(reply) == FROM_DB


def test_reply_to_a_plain_message_takes_its_sender():
    from plugins.admin import _target_user_id_from_reply

    reply = SimpleNamespace(forward_origin=None, from_user=SimpleNamespace(id=FROM_DB, is_bot=False))
    assert _target_user_id_from_reply(reply) == FROM_DB


def test_reply_to_a_bot_message_is_not_a_target():
    from plugins.admin import _target_user_id_from_reply

    reply = SimpleNamespace(
        forward_origin=None,
        from_user=SimpleNamespace(id=555000111, is_bot=True),
    )
    assert _target_user_id_from_reply(reply) is None


def test_a_numeric_argument_is_the_target():
    from plugins.admin import _resolve_target

    msg = SimpleNamespace(command=["add", "123456789"], reply_to_message=None)
    assert asyncio.run(_resolve_target(MagicMock(), msg)) == 123456789


def test_a_username_argument_is_resolved_through_the_api():
    from plugins.admin import _resolve_target

    cli = MagicMock()
    cli.get_users = AsyncMock(return_value=SimpleNamespace(id=FROM_DB))
    msg = SimpleNamespace(command=["add", "@someone"], reply_to_message=None)
    assert asyncio.run(_resolve_target(cli, msg)) == FROM_DB


def test_an_unresolvable_argument_gives_no_target():
    from pyrogram.errors import UsernameNotOccupied

    from plugins.admin import _resolve_target

    cli = MagicMock()
    cli.get_users = AsyncMock(side_effect=UsernameNotOccupied())
    msg = SimpleNamespace(command=["add", "@nobody"], reply_to_message=None)
    assert asyncio.run(_resolve_target(cli, msg)) is None


# ---------------------------------------------------------------- 权限守卫


def _sender_recorder():
    sender = MagicMock()
    sender.return_value.text = AsyncMock()
    return sender


def test_a_non_whitelisted_user_gets_a_refusal_and_nothing_else():
    from plugins import admin

    cli, sender = MagicMock(), _sender_recorder()
    msg = SimpleNamespace(from_user=SimpleNamespace(id=STRANGER), command=["purge", "https://x.com/a/status/1"])

    with (
        patch.object(admin, "is_admin_user", AsyncMock(return_value=False)),
        patch.object(admin, "_context", AsyncMock(return_value=("zh-hans", SimpleNamespace()))),
        patch.object(admin, "MessageSender", sender),
        patch.object(admin, "parse_cache") as cache,
    ):
        asyncio.run(admin.purge_cache(cli, msg))

    said = sender.return_value.text.await_args.args[0]
    assert said == "无权使用该命令"
    cache.pop.assert_not_called()  # 没执行任何清理


# ---------------------------------------------------------------- /purge


def _purge(url_arg: str = "https://x.com/a/status/1", *, raw: str = "https://x.com/a/status/1", had=True):
    """跑一次 /purge, 返回 (回复文本, 两层缓存 mock, ParseService mock)。"""
    from plugins import admin

    cli, sender = MagicMock(), _sender_recorder()
    msg = SimpleNamespace(from_user=SimpleNamespace(id=CONFIGURED), command=["purge", url_arg], reply_to_message=None)

    parser = MagicMock()
    parser.get_platform = MagicMock(return_value=SimpleNamespace(id="twitter"))
    svc = MagicMock()
    svc.get_raw_url = AsyncMock(return_value=raw)
    svc.parser = parser

    persistent, memory = MagicMock(), MagicMock()
    persistent.get = AsyncMock(return_value=SimpleNamespace() if had else None)
    persistent.remove = AsyncMock()
    memory.get = AsyncMock(return_value=None)
    memory.pop = AsyncMock()

    with (
        patch.object(admin, "is_admin_user", AsyncMock(return_value=True)),
        patch.object(admin, "_context", AsyncMock(return_value=("zh-hans", SimpleNamespace()))),
        patch.object(admin, "MessageSender", sender),
        patch.object(admin, "ParseService", MagicMock(return_value=svc)),
        patch.object(admin, "persistent_cache", persistent),
        patch.object(admin, "parse_cache", memory),
    ):
        asyncio.run(admin.purge_cache(cli, msg))

    said = sender.return_value.text.await_args.args[0]
    return said, persistent, memory


def test_purge_clears_both_cache_layers_with_the_raw_url_key():
    """key 必须是 ``get_raw_url`` 的结果 —— 写入时用的就是它, 拿用户输入去清会清不掉"""
    said, persistent, memory = _purge("https://x.com/a/status/1?s=20", raw="https://x.com/a/status/1")
    assert persistent.remove.await_args.args[0] == "https://x.com/a/status/1"
    assert memory.pop.await_args.args[0] == "https://x.com/a/status/1"
    assert "已清除缓存" in said


def test_purge_says_so_when_there_was_no_cache():
    said, persistent, memory = _purge(had=False)
    assert "没有缓存" in said
    persistent.remove.assert_awaited()  # 仍然清一次 (幂等, 不做判断分支)


def test_purge_asks_for_a_link_when_given_none():
    from plugins import admin

    cli, sender = MagicMock(), _sender_recorder()
    msg = SimpleNamespace(from_user=SimpleNamespace(id=CONFIGURED), command=["purge"], reply_to_message=None)

    with (
        patch.object(admin, "is_admin_user", AsyncMock(return_value=True)),
        patch.object(admin, "_context", AsyncMock(return_value=("zh-hans", SimpleNamespace()))),
        patch.object(admin, "MessageSender", sender),
    ):
        asyncio.run(admin.purge_cache(cli, msg))

    assert sender.return_value.text.await_args.args[0] == "请加上链接或回复一条消息"


def test_purge_reads_the_link_from_the_replied_message():
    from plugins import admin

    cli, sender = MagicMock(), _sender_recorder()
    reply = SimpleNamespace(text="", caption="看这个 https://x.com/a/status/7")
    msg = SimpleNamespace(from_user=SimpleNamespace(id=CONFIGURED), command=["purge"], reply_to_message=reply)

    parser = MagicMock()
    parser.get_platform = MagicMock(return_value=SimpleNamespace(id="twitter"))
    svc = MagicMock()
    svc.get_raw_url = AsyncMock(return_value="https://x.com/a/status/7")
    svc.parser = parser
    persistent, memory = MagicMock(), MagicMock()
    persistent.get = AsyncMock(return_value=None)
    persistent.remove = AsyncMock()
    memory.get, memory.pop = AsyncMock(return_value=None), AsyncMock()

    with (
        patch.object(admin, "is_admin_user", AsyncMock(return_value=True)),
        patch.object(admin, "_context", AsyncMock(return_value=("zh-hans", SimpleNamespace()))),
        patch.object(admin, "MessageSender", sender),
        patch.object(admin, "ParseService", MagicMock(return_value=svc)),
        patch.object(admin, "persistent_cache", persistent),
        patch.object(admin, "parse_cache", memory),
    ):
        asyncio.run(admin.purge_cache(cli, msg))

    assert svc.get_raw_url.await_args.args[0] == "https://x.com/a/status/7"
    assert memory.pop.await_args.args[0] == "https://x.com/a/status/7"
