"""Guest mode: 用户在 bot 不在的群里 @它或回复它的消息时, 直接在该群回一条解析结果。

Telegram 的 guest 机制：用户在群里提到 bot 的 @username（或回复 bot 的消息）时,
bot 会收到 `UpdateBotGuestChatQuery`（pyrogram: `on_guest_message`）,
并可用 `answer_guest_query` **直接在该群贴一条消息** —— 不需要是群成员。

⚠️ 回复**必须**走 `answer_guest_query`：bot 不在那个群里, 普通的
`Message.reply_*` / `send_*` 根本发不出去。它接受的 result 与 inline 是同一套类型,
所以富文本排版可以复用 `build_rich_markdown` / `build_rich_media`。

与 inline 的分工: inline 是用户自己在输入框发起、结果由用户选中后发出;
guest 是 bot 自己在群里发一条 —— 后者要求"只能被允许的人召唤",
所以入口处先过门禁、再要求消息是纯链接。
"""

from __future__ import annotations

from parsehub.utils.helpers import url_only_message_urls
from pyrogram import Client
from pyrogram.types import (
    InlineQueryResultArticle,
    InputRichMessage,
    InputRichMessageContent,
    Message,
)

from db import get_session
from i18n import t_
from log import logger
from plugins.helpers import build_rich_markdown
from plugins.parse.access import access_gate
from plugins.parse.covers import prepare_video_thumbs
from plugins.parse.inline_rich import build_rich_media
from services import ParsePipeline, ParseService, SettingsService, UserService
from utils.helpers import to_list, with_request_id

logger = logger.bind(name="GuestMode")

#: guest 结果项的固定 id (与 inline 的 rich 项区分开)
GUEST_RESULT_ID = "guest-rich"


class _SilentReporter:
    """guest 场景没有可编辑的状态消息（不能改用户的消息，也还没有结果消息），
    所以进度一律丢弃 —— 只保证流水线接口齐全。"""

    async def report(self, text: str) -> None:  # noqa: ARG002
        return

    async def report_error(self, stage: str, error: Exception) -> None:  # noqa: ARG002
        logger.debug(f"guest 流水线错误: stage={stage} error={error}")

    async def dismiss(self) -> None:
        return


def _clip(text: str | None, limit: int) -> str:
    """压平换行并截断 (inline/guest 结果项的 title/description 只是一行)。"""
    flat = " ".join((text or "").split())
    return flat if len(flat) <= limit else flat[:limit] + "…"


def _result(title: str, description: str, markdown: str, media: list | None = None) -> InlineQueryResultArticle:
    return InlineQueryResultArticle(
        id=GUEST_RESULT_ID,
        title=title,
        description=description,
        input_message_content=InputRichMessageContent(InputRichMessage(markdown=markdown, media=media or None)),
    )


async def _answer(cli: Client, guest_query_id: str, url: str, user_id: int, locale: str) -> bool:
    """解析一条链接并回答 guest 查询, 返回是否真的发出。

    **发送必须在 with 块内**: ParsePipeline 退出时会清掉下载目录,
    媒体路径带出去就是死链 (结果是消息里没有图)。
    """
    async with get_session() as session:
        config = await SettingsService(session).get_config_by_user(user_id)
    _t = t_[locale]

    service = ParseService()
    raw_url = await service.get_raw_url(url)
    with ParsePipeline(url, raw_url, _SilentReporter(), singleflight=False, t=_t) as pipeline:
        result = await pipeline.run()
        if result is None:
            logger.warning(f"guest 解析失败: url={url}")
            return None

        parse_result = result.parse_result
        media_refs = to_list(parse_result.media)
        video_thumbs = (
            await prepare_video_thumbs(media_refs, platform=parse_result.platform)
            if config.video_cover
            else {}
        )
        media, placeholders, _blocks, quoted_ph, reply_ph = build_rich_media(
            media_refs,
            result.processed_list,
            video_cover=config.video_cover,
            is_sensitive=parse_result.is_sensitive,
            video_thumbs=video_thumbs,
            quoted_media_count=getattr(parse_result, "quoted_media_count", 0),
            reply_media_count=getattr(parse_result, "reply_media_count", 0),
        )
        markdown = build_rich_markdown(
            parse_result,
            config=config,
            lang=locale,
            view_label=_t("查看"),
            media_placeholders=placeholders,
            quote_media_placeholders=quoted_ph,
            reply_media_placeholders=reply_ph,
        )
        title = _clip(parse_result.title, 90) or _clip(parse_result.content, 90) or "-"
        description = _clip(parse_result.content, 200)

        # 带媒体一次到位; 媒体被拒时退回纯文字 (至少内容要看得到)
        try:
            sent = await cli.answer_guest_query(guest_query_id, _result(title, description, markdown, media))
        except Exception as e:
            logger.warning(f"guest 带媒体发送失败, 退回纯文字: {type(e).__name__}: {str(e)[:120]}")
            sent = await cli.answer_guest_query(guest_query_id, _result(title, description, markdown))
        logger.info(f"guest 结果已发送: inline_message_id={getattr(sent, 'inline_message_id', None)}")
        return True


@Client.on_guest_message()
@with_request_id
async def guest_parse(cli: Client, msg: Message) -> None:
    """处理 guest 查询: 群里 @bot 或回复 bot 的消息。

    只认**纯链接消息**（与自动解析规则一致）—— guest 是"在任何群都能召唤"的入口,
    不加限制等于把解析能力开放给全 Telegram。
    """
    user_id = msg.from_user.id if msg.from_user else None
    guest_query_id = getattr(msg, "guest_query_id", None)
    logger.info(
        f"收到 guest 查询: from_user={user_id}, chat_id={getattr(msg.chat, 'id', None)}, "
        f"guest_query_id={guest_query_id!r}, text={(msg.text or msg.caption or '')[:80]!r}"
    )

    if not guest_query_id:
        logger.warning("guest 查询缺少 guest_query_id, 无法回复")
        return

    if not await access_gate.is_allowed(cli, user_id):
        logger.info(f"guest 查询被门禁拦截: from_user={user_id}")
        return

    urls = url_only_message_urls(msg.text or msg.caption)
    if not urls:
        logger.debug("guest 查询不是纯链接消息, 跳过")
        return

    async with get_session() as session:
        locale = await UserService(session).get_lang(user_id) if user_id else ""

    await _answer(cli, guest_query_id, urls[0], user_id or 0, locale)
