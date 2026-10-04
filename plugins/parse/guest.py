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
    InputTextMessageContent,
    LinkPreviewOptions,
    Message,
)

from db import get_session
from i18n import t_
from log import logger
from plugins.helpers import build_rich_markdown
from plugins.parse.access import access_gate
from plugins.parse.covers import prepare_video_thumbs
from plugins.parse.inline_rich import (
    build_cached_rich_content,
    build_rich_media,
    extract_cache_media,
    rich_cache_entry,
)
from plugins.parse.reporters import MessageStatusReporter
from services import ParsePipeline, ParseService, SettingsService, UserService
from services.cache import persistent_cache
from utils.helpers import to_list, with_request_id

logger = logger.bind(name="GuestMode")

#: guest 结果项的固定 id (与 inline 的 rich 项区分开)
GUEST_RESULT_ID = "guest-rich"


# guest 的状态反馈走 MessageStatusReporter: 它 reply 那条召唤消息发一条进度,
# 最后把同一条**编辑成结果** —— 与私聊/群自动解析的体验一致 (以前这里进度全丢,
# 用户只看到"突然出现结果", 没有任何过程)。


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


class _NullReporter:
    """拿不到召唤消息时的兜底: 没有可编辑的载体, 进度只能丢弃 (结果照发)。"""

    async def report(self, text: str) -> None:  # noqa: ARG002
        return

    async def report_error(self, stage: str, error: Exception) -> None:  # noqa: ARG002
        logger.debug(f"guest 流水线错误: stage={stage} error={error}")

    async def dismiss(self) -> None:
        return


def _denied_result(text: str) -> InlineQueryResultArticle:
    """门禁不通过时回一条说明, 而不是静默 —— 用户得知道为什么没反应。"""
    return InlineQueryResultArticle(
        id=GUEST_RESULT_ID,
        title=text,
        description=text,
        input_message_content=InputTextMessageContent(text, link_preview_options=LinkPreviewOptions(is_disabled=True)),
    )


async def _deliver(
    cli: Client,
    guest_query_id: str,
    reporter: MessageStatusReporter | None,
    *,
    title: str,
    description: str,
    markdown: str,
    media: list | None,
) -> Message | None:
    """把结果送出去, 返回承载结果的 Message (缓存路径要拿它取 file_id)。

    两条通道: ① 编辑召唤消息上的状态消息 (一条消息走完); ② 退回 guest 通道
    (bot 不在群里时唯一可用的方式)。
    """
    if reporter is not None and reporter.has_message:
        try:
            edited = await reporter.finalize(InputRichMessage(markdown=markdown, media=media or None))
            if edited is not None:
                logger.info(f"guest 结果已编辑进状态消息: msg_id={edited.id}")
                return edited
        except Exception as e:
            logger.warning(f"编辑状态消息失败, 退回 answer_guest_query: {type(e).__name__}: {str(e)[:120]}")

    try:
        sent = await cli.answer_guest_query(guest_query_id, _result(title, description, markdown, media))
    except Exception as e:
        logger.warning(f"guest 带媒体发送失败, 退回纯文字: {type(e).__name__}: {str(e)[:120]}")
        sent = await cli.answer_guest_query(guest_query_id, _result(title, description, markdown))
    logger.info(f"guest 结果已发送: inline_message_id={getattr(sent, 'inline_message_id', None)}")
    return None          # guest 通道拿不到 Message, 也就拿不到 file_id


async def _answer(
    cli: Client, guest_query_id: str, url: str, user_id: int, locale: str, caller_msg: Message | None = None
) -> bool:
    """解析一条链接并回答 guest 查询, 返回是否真的发出。

    **进度反馈**: 在召唤消息上 reply 一条状态消息, 跟着把**同一条**编辑成结果
    (与私聊/群自动解析一致)。召唤消息拿不到时就退回静默 (只有结果)。

    **发送必须在 with 块内**: ParsePipeline 退出时会清掉下载目录,
    媒体路径带出去就是死链 (结果是消息里没有图)。
    """
    async with get_session() as session:
        config = await SettingsService(session).get_config_by_user(user_id)
    _t = t_[locale]

    # 有召唤消息就用它做状态载体; 否则静默
    reporter = MessageStatusReporter(cli, caller_msg, t=_t, config=config) if caller_msg is not None else None
    reporter_impl = reporter or _NullReporter()

    service = ParseService()
    raw_url = await service.get_raw_url(url)

    # ① 缓存命中: 直接用 file_id 发, 跳过解析/下载/转码/上传 (与私聊/群/inline 一致)
    if cached := await persistent_cache.get(raw_url):
        logger.debug(f"guest: file_id 缓存命中, 直接发送 url={raw_url}")
        markdown, cached_media = build_cached_rich_content(
            cached, raw_url, lang=locale, config=config, view_label=_t("查看")
        )
        pr = cached.parse_result
        await _deliver(
            cli,
            guest_query_id,
            reporter,
            title=_clip(pr.title, 90) or _clip(pr.content, 90) or "-",
            description=_clip(pr.content, 200),
            markdown=markdown,
            media=cached_media or None,
        )
        return True

    with ParsePipeline(url, raw_url, reporter_impl, singleflight=False, t=_t) as pipeline:
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

        delivered = await _deliver(
            cli,
            guest_query_id,
            reporter,
            title=title,
            description=description,
            markdown=markdown,
            media=media or None,
        )

        # 写回 file_id 缓存: 下次同一链接零下载零上传
        # (只有编辑状态消息那条路能拿到 Message, guest 通道拿不到 —— 拿不到就不写)
        if delivered is not None:
            cached_media = extract_cache_media(getattr(delivered, "rich_message", None))
            if cached_media:
                quoted_items = min(len(quoted_ph), len(cached_media))
                reply_items = min(len(reply_ph), len(cached_media) - quoted_items)
                await persistent_cache.set(
                    raw_url,
                    rich_cache_entry(
                        parse_result,
                        cached_media,
                        quoted_media_count=quoted_items,
                        reply_media_count=reply_items,
                    ),
                )
                logger.debug(
                    f"guest: 富文本媒体已写入缓存 count={len(cached_media)} 引用{quoted_items} 回复{reply_items}"
                )
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

    async with get_session() as session:
        locale = (
            await UserService(session).ensure_lang(user_id, msg.from_user.language_code if msg.from_user else None)
            if user_id
            else ""
        )
    _t = t_[locale]

    if not await access_gate.is_allowed(cli, user_id):
        logger.info(f"guest 查询被门禁拦截: from_user={user_id}")
        # 明确回一条"无权限": 静默拒绝会让用户以为 bot 坏了
        await cli.answer_guest_query(guest_query_id, _denied_result(_t("无权限")))
        return

    # guest 靠"消息里提到 bot"触发, 所以判定纯链接前必须先把 bot 的 @username 剔掉,
    # 否则 "@bot <链接>" 永远不算纯链接 (那 guest 就没法用了)
    bot_username = getattr(getattr(cli, "me", None), "username", "") or ""
    urls = url_only_message_urls(msg.text or msg.caption, ignore_mentions=(bot_username,))
    if not urls:
        logger.debug(f"guest 查询不是纯链接消息, 跳过: text={(msg.text or msg.caption or '')[:80]!r}")
        return

    # 用召唤消息做进度载体: reply 它一条状态, 最后编辑成结果
    await _answer(cli, guest_query_id, urls[0], user_id or 0, locale, caller_msg=msg)
