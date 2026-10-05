"""管理命令: ``/add`` ``/list`` ``/purge`` —— **仅白名单用户可用**。

白名单 = 配置 (``.env`` 的 ``ADMIN_USERS``, 逗号分隔) ∪ DB ``admin_users`` 表。
配置那一层是**永远允许**的, 也解决"第一个人怎么进来" —— 否则 ``/add`` 谁也调不了。

**普通解析不受影响** —— 它有自己的规则 (与 bot 同群可用)。这里只管这三条命令。
"""

from pyrogram import Client, filters
from pyrogram.types import Message

from core import bs
from db import get_session
from i18n import t_
from log import logger
from plugins.context import get_config_target
from plugins.parse.sender import MessageSender
from repo.settings import SettingsConfig
from services import (
    AdminUserService,
    ParseService,
    SettingsService,
    UserService,
    is_admin_user,
    parse_cache,
    persistent_cache,
)

logger = logger.bind(name="Admin")


async def _context(msg: Message) -> tuple[str, SettingsConfig]:
    """取 (语言, 配置) —— 与其他命令同一路径 (顺带完成用户语言的首次初始化)。"""
    async with get_session() as session:
        lang = bs.language
        if msg.from_user:
            lang = await UserService(session).ensure_lang(msg.from_user.id, msg.from_user.language_code)
        config = await SettingsService(session).get_config(get_config_target(msg))
    return lang, config


async def _require_admin(cli: Client, msg: Message) -> bool:
    """白名单校验: 不是白名单就回一条提示 (不静默 —— 静默会让人以为 bot 坏了)。"""
    uid = msg.from_user.id if msg.from_user else None
    if await is_admin_user(uid):
        return True
    lang, config = await _context(msg)
    await MessageSender(cli, msg, config).text(t_[lang]("无权使用该命令"))
    logger.warning(f"管理命令被非白名单用户调用: user_id={uid} cmd={msg.command}")
    return False


def _target_user_id_from_reply(reply: Message) -> int | None:
    """从被回复的消息里取"某人"的 ID。

    **优先取原转发者** —— 私聊里被回复的其实是转发过来的内容, 直接看 ``from_user``
    会加白转发的中间人 (甚至加白 bot 自己), 而不是内容真正的作者。
    """
    sender_user = getattr(reply.forward_origin, "sender_user", None)
    if sender_user:
        return sender_user.id
    if reply.from_user and not reply.from_user.is_bot:
        return reply.from_user.id
    return None


async def _resolve_target(cli: Client, msg: Message) -> int | None:
    """``/add`` 要加的人: 命令参数优先, 否则看被回复的消息。"""
    if msg.command and len(msg.command) > 1:
        arg = msg.command[1].strip()
        if arg.lstrip("-").isdigit():
            return int(arg)
        # 非数字: 试一次用户名/链接 (用户可能写 @name 而不是数字)
        try:
            user = await cli.get_users(arg)
        except Exception as e:  # noqa: BLE001 - 解析不到就当没给, 由调用方提示
            logger.warning(f"/add 无法解析目标: arg={arg} err={type(e).__name__}: {e}")
            return None
        return user.id if user else None
    if reply := msg.reply_to_message:
        return _target_user_id_from_reply(reply)
    return None


@Client.on_message(filters.command("add"))
async def add_admin_user(cli: Client, msg: Message) -> None:
    """``/add <uid>`` 或回复某人的消息 —— 把他加进白名单。"""
    if not await _require_admin(cli, msg):
        return

    lang, config = await _context(msg)
    _t = t_[lang]

    target = await _resolve_target(cli, msg)
    if target is None:
        await MessageSender(cli, msg, config).text(_t("请回复某人的消息，或给出用户 ID"))
        return

    async with get_session() as session:
        added = await AdminUserService(session).add(target, added_by=msg.from_user.id if msg.from_user else None)

    label = _t("已加入白名单") if added else _t("已在白名单中")
    await MessageSender(cli, msg, config).text(f"{label}: {target}")


@Client.on_message(filters.command("list"))
async def list_admin_users(cli: Client, msg: Message) -> None:
    """``/list`` —— 列出白名单。"""
    if not await _require_admin(cli, msg):
        return

    lang, config = await _context(msg)
    _t = t_[lang]

    async with get_session() as session:
        ids = await AdminUserService(session).list_ids()

    if not ids:
        await MessageSender(cli, msg, config).text(_t("白名单为空"))
        return
    body = "\n".join(f"• {uid}" for uid in ids)
    await MessageSender(cli, msg, config).text(f"{_t('白名单')} ({len(ids)}):\n{body}")


@Client.on_message(filters.command("purge"))
async def purge_cache(cli: Client, msg: Message) -> None:
    """``/purge <链接>`` 或回复一条含链接的消息 —— 清掉该链接的解析缓存。

    两层都要清: ``persistent_cache`` (DB 里的解析字段 + 媒体 file_id) 与
    ``parse_cache`` (内存 TTL)。**key 必须用 ``get_raw_url`` 的结果** ——
    写入时用的就是它, 拿用户原始输入去清会 hash 对不上、清不掉。
    """
    if not await _require_admin(cli, msg):
        return

    lang, config = await _context(msg)
    _t = t_[lang]

    text = " ".join(msg.command[1:]) if msg.command and msg.command[1:] else ""
    if not text and msg.reply_to_message:
        text = msg.reply_to_message.text or msg.reply_to_message.caption or ""
    if not text:
        await MessageSender(cli, msg, config).text(_t("请加上链接或回复一条消息"))
        return

    urls = list(dict.fromkeys(i for i in text.split() if ParseService().parser.get_platform(i)))[:10]
    if not urls:
        await MessageSender(cli, msg, config).text(_t("不支持的平台"))
        return

    lines: list[str] = []
    for url in urls:
        try:
            raw_url = await ParseService().get_raw_url(url)
        except Exception as e:  # noqa: BLE001 - 单条失败不该让整批中断
            logger.warning(f"清缓存取原始链接失败: url={url} err={type(e).__name__}: {e}")
            lines.append(f"⚠️ {url}\n{type(e).__name__}: {e}")
            continue
        # remove() 不返回是否命中, 所以先看有没有, 才能回答"清了"还是"本来就没有"
        had = bool(await persistent_cache.get(raw_url)) or bool(await parse_cache.get(raw_url))
        await persistent_cache.remove(raw_url)
        await parse_cache.pop(raw_url)
        logger.info(f"清缓存: url={raw_url} had_cache={had} by={msg.from_user.id if msg.from_user else None}")
        lines.append(f"{_t('已清除缓存') if had else _t('没有缓存')}\n{raw_url}")

    await MessageSender(cli, msg, config).text("\n\n".join(lines))
