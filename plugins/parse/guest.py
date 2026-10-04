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

from easy_ai18n import PreLocaleSelector
from parsehub.utils.helpers import strip_spoiler_flag, url_only_message_urls
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
from plugins.helpers import build_progress_markdown, build_rich_markdown, format_label
from plugins.parse.access import access_gate
from plugins.parse.covers import prepare_video_thumbs
from plugins.parse.inline_rich import (
    build_cached_rich_content,
    build_rich_media,
    edit_inline_rich_message,
)
from plugins.parse.reporters import InlineStatusReporter
from plugins.parse.rich_blocks import markdown_to_blocks
from repo.settings import SettingsConfig
from services import ParsePipeline, ParseService, SettingsService, StatusReporter, UserService
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


async def _send_placeholder(
    cli: Client,
    guest_query_id: str,
    _t: PreLocaleSelector,
    *,
    config: SettingsConfig,
    raw_url: str = "",
) -> str | None:
    """先发一条占位结果, 返回它的 ``inline_message_id`` (拿不到则 None)。

    **为什么要先发占位**: guest 消息就是 inline 消息, 但它的 id 只有**发出之后**
    才存在 —— 想用它当进度载体, 就得先占个位。

    占位内容就是**第一帧处理过程**: 与最终结果同版式的富文本骨架
    (进度行 + 页脚的来源链接), 所以后面每次更新都不跳版。
    """
    label = _t("解 析 中...")
    markdown = build_progress_markdown(
        None,
        progress=format_label(label),
        config=config,
        lang=_t.locale,
        view_label=_t("查看"),
        raw_url=raw_url,
    )
    try:
        sent = await cli.answer_guest_query(guest_query_id, _result(label, "", markdown))
    except Exception as e:  # noqa: BLE001 - 占位失败就退回"只有结果", 不该打断解析
        logger.warning(f"guest 占位发送失败, 本次没有进度反馈: {type(e).__name__}: {str(e)[:120]}")
        return None
    mid = getattr(sent, "inline_message_id", None)
    logger.debug(f"guest 占位已发出: inline_message_id={mid}")
    return mid


class _NullReporter:
    """拿不到召唤消息时的兜底: 没有可编辑的载体, 进度只能丢弃 (结果照发)。"""

    async def report(self, text: str) -> None:  # noqa: ARG002
        return

    async def report_progress(self, text: str) -> None:  # noqa: ARG002
        return

    async def report_result(self, parse_result: object, text: str) -> None:  # noqa: ARG002
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
    inline_message_id: str | None,
    *,
    title: str,
    description: str,
    markdown: str,
    media: list | None,
    blocks: list | None = None,
) -> None:
    """把结果送出去 (缓存路径拿不到 Message, 所以不返回)。

    **两条通道**:

    ① **编辑 guest 消息本身** (主路径)。guest 消息就是一条 inline 消息
    (``SentGuestMessage``: *"inline message sent by a guest bot"*), 而 inline 消息 id
    正是 ``EditInlineBotMessage`` 要的 ``InputBotInlineMessageID`` —— 所以开头发出的
    那条占位可以一路编辑到结果, 与 inline 完全同构。

    ② **退回 ``answer_guest_query`` 直发**。编辑失败时的兜底 (占位没发出去、
    或服务端不接受编辑), 至少保证结果能送出去。

    ``blocks`` 非空时走 blocks 路径 (敏感内容的媒体要靠 raw 的
    ``PageBlockPhoto(spoiler=)`` 才能打码, 与 inline 同一套)。
    """
    if inline_message_id:
        try:
            await edit_inline_rich_message(
                cli, inline_message_id, markdown=markdown, media=media or None, blocks=blocks
            )
            logger.info("guest 结果已编辑进 guest 消息: 一条消息走完整个流程")
            return
        except Exception as e:
            logger.warning(
                f"编辑 guest 消息失败, 退回 answer_guest_query: {type(e).__name__}: {str(e)[:120]}"
            )

    try:
        sent = await cli.answer_guest_query(guest_query_id, _result(title, description, markdown, media))
    except Exception as e:
        logger.warning(f"guest 带媒体发送失败, 退回纯文字: {type(e).__name__}: {str(e)[:120]}")
        sent = await cli.answer_guest_query(guest_query_id, _result(title, description, markdown))
    logger.info(f"guest 结果已发送: inline_message_id={getattr(sent, 'inline_message_id', None)}")


async def _answer(
    cli: Client,
    guest_query_id: str,
    url: str,
    user_id: int,
    locale: str,
    spoiler_tag: str = "",
) -> bool:
    """解析一条链接并回答 guest 查询, 返回是否真的发出。

    **进度反馈**: 先把一条占位结果发进 guest 通道, 拿它的 ``inline_message_id``
    当进度载体, 一路编辑到最终结果 —— 与 inline 同构, 也是"一条消息走完整个流程"。

    以前是**在召唤消息上 reply 一条状态消息**, 但 guest 场景 bot 通常不在召唤群里
    (这正是 guest 模式的意义), 那条 reply 根本发不出去 (实测 ``400 CHANNEL_PRIVATE``,
    而且异常被吞成 debug 级), 于是 guest **从来没有处理过程、只有结果**。

    **发送必须在 with 块内**: ParsePipeline 退出时会清掉下载目录,
    媒体路径带出去就是死链 (结果是消息里没有图)。
    """
    async with get_session() as session:
        config = await SettingsService(session).get_config_by_user(user_id)
    _t = t_[locale]

    # 进度载体 = guest 消息自己: 先发一条占位, 拿 inline_message_id, 后面一路编辑。
    # 发不出去就退回静默 (只有结果), 行为与以前一致。
    service = ParseService()
    raw_url = await service.get_raw_url(url)

    # 占位 = 第一帧处理过程 (同版式的骨架), 拿它的 inline_message_id 当进度载体
    inline_message_id = await _send_placeholder(cli, guest_query_id, _t, config=config, raw_url=raw_url)
    reporter: StatusReporter = (
        InlineStatusReporter(
            cli,
            inline_message_id,
            t=_t,
            user_config=config,
            raw_url=raw_url,
            spoiler_tag=spoiler_tag,
        )
        if inline_message_id
        else _NullReporter()
    )

    # ① 缓存命中: 直接用 file_id 发, 跳过解析/下载/转码/上传 (与私聊/群/inline 一致)
    if cached := await persistent_cache.get(raw_url):
        logger.debug(f"guest: file_id 缓存命中, 直接发送 url={raw_url}")
        markdown, cached_media, cached_blocks = build_cached_rich_content(
            cached,
            raw_url,
            lang=locale,
            config=config,
            view_label=_t("查看"),
            spoiler_tag=spoiler_tag,
        )
        pr = cached.parse_result
        await _deliver(
            cli,
            guest_query_id,
            inline_message_id,
            title=_clip(pr.title, 90) or _clip(pr.content, 90) or "-",
            description=_clip(pr.content, 200),
            markdown=markdown,
            media=cached_media or None,
            # 敏感内容走 blocks 才打得了码 (缓存里带着 is_sensitive, 以前没用上)
            blocks=markdown_to_blocks(markdown, media_blocks=cached_blocks) if cached_blocks else None,
        )
        return True

    with ParsePipeline(url, raw_url, reporter, singleflight=False, t=_t) as pipeline:
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
            hide_content=spoiler_tag,
        )
        title = _clip(parse_result.title, 90) or _clip(parse_result.content, 90) or "-"
        description = _clip(parse_result.content, 200)

        await _deliver(
            cli,
            guest_query_id,
            inline_message_id,
            title=title,
            description=description,
            markdown=markdown,
            media=media or None,
            # 敏感内容的媒体要走 blocks 路径才能打码 (与 inline 同一套)
            blocks=_blocks if parse_result.is_sensitive and _blocks else None,
        )

        # 不写 file_id 缓存: guest 的结果发在**别人群里的一条 inline 消息**上,
        # 服务端不把它回传成 Message, 所以拿不到上传后的 file_id。
        # (只有私聊/群的直发路径能拿到, 那条路径在 send_rich_media 里写缓存。)
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
    # 手动打码开关同样适用: 剥掉 /s 并传下去
    _, spoiler_tag = strip_spoiler_flag(msg.text or msg.caption)
    await _answer(cli, guest_query_id, urls[0], user_id or 0, locale, spoiler_tag=spoiler_tag)
