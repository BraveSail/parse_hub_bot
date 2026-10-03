import re

from parsehub import AnyParseResult
from parsehub.types import (
    AniRef,
    ImageRef,
    RichTextParseResult,
    VideoRef,
)
from pyrogram import Client, raw, utils
from pyrogram.errors import BadRequest
from pyrogram.types import (
    ChosenInlineResult,
    InlineQuery,
    InlineQueryResult,
    InlineQueryResultAnimation,
    InlineQueryResultArticle,
    InlineQueryResultCachedAnimation,
    InlineQueryResultCachedDocument,
    InlineQueryResultCachedPhoto,
    InlineQueryResultCachedVideo,
    InlineQueryResultPhoto,
    InlineQueryResultVideo,
    InputMediaVideo,
    InputRichMessage,
    InputRichMessageContent,
    InputTextMessageContent,
    LinkPreviewOptions,
)
from pyrogram.types import (
    InlineKeyboardButton as Ikb,
)
from pyrogram.types import (
    InlineKeyboardMarkup as Ikm,
)

from db import get_session
from i18n import t_
from log import logger
from plugins.filters import platform_filter
from plugins.helpers import (
    build_caption,
    build_caption_by_str,
    build_metadata_line,
    build_rich_markdown,
    build_rich_markdown_by_str,
    build_start_text,
    create_richtext_telegraph,
)
from plugins.parse.inline_rich import RICH_RESULT_ID, build_media_items, edit_inline_rich_message
from plugins.parse.reporters import InlineStatusReporter
from repo.settings import SettingsConfig
from services import ParseService, SettingsService, UserService, inline_start_link
from services.cache import CacheEntry, CacheMediaType, parse_cache, persistent_cache
from services.pipeline import ParsePipeline
from utils.helpers import to_list, with_request_id

logger = logger.bind(name="InlineParse")

INLINE_TITLE_LIMIT = 90
"""inline 结果项标题上限 (列表里只显示一行, 超长会把结果项撑爆)"""

INLINE_DESC_LIMIT = 200
"""inline 结果项描述上限"""

INLINE_SWITCH_PM_MIN_MEDIA = 2
"""给"获取全部"按钮的最小媒体数 (inline 一次只能发一个媒体)"""


def clip_inline_text(text: str | None, limit: int) -> str:
    """压平换行并截断, 用于 inline 结果项的 title/description。

    有些平台的标题承载正文 (抖音把文案塞进 title, yt-dlp 对 Facebook 返回
    "27K views · … | 正文 | 频道名"), 不截断会让 inline 结果列表变得极长。
    """
    flat = " ".join((text or "").split())
    return flat if len(flat) <= limit else flat[:limit] + "…"


def count_inline_media(media: object) -> int:
    """inline 结果对应的媒体数量 (无媒体/None 都算 0)。"""
    return 0 if media is None else len(to_list(media))  # type: ignore[arg-type]


async def build_switch_pm(media_count: int, raw_url: str, lang: str) -> tuple[str, str]:
    """多图时给 inline 结果加"切到私聊发全部"的 switch_pm 参数。

    Telegram 的 switch_pm 会在 inline 结果上方显示一行文字, 点击后切到 bot 私聊
    并发送 `/start <parameter>`。inline 一次只能发一个媒体, 多图作品只能这样把
    整份内容交给 bot 在私聊里发 (相册), 所以仅媒体数 >= 2 时下发。

    parameter 只允许 A-Za-z0-9_- 且不超过 64 字符, 装不下链接, 所以用短期映射的
    token 代替 (见 services/inline_share.py)。无需按钮时返回两个空串 (pyrogram
    只在 text 非空时才把 switch_pm 发给 Telegram)。
    """
    if media_count < INLINE_SWITCH_PM_MIN_MEDIA:
        return "", ""
    _t = t_[lang]
    token = await inline_start_link.register(raw_url)
    return _t(f"获取全部 {media_count} 项"), token


SEARCH_ICON = "https://i.imgloc.com/2023/06/15/Vbfazk.png"
DEFAULT_PARSE_RESULT_THUMB_URL = "https://telegra.ph/file/cdfdb65b83a4b7b2b6078.png"
LINK_ICON_URL = "https://i.iij.li/i/20260627/6a3fb12066abb.png"
LINK_ICON_WIDTH = 72
LINK_ICON_HEIGHT = 72


@Client.on_inline_query(~platform_filter(False))
async def inline_parse_tip(_: Client, inline_query: InlineQuery) -> None:
    async with get_session() as session:
        lang = await UserService(session).get_lang(inline_query.from_user.id)
    _t = t_[lang]
    results: list[InlineQueryResult] = [
        InlineQueryResultArticle(
            title=_t("聚合解析"),
            description=_t("请在聊天框输入链接"),
            input_message_content=InputTextMessageContent(
                build_start_text()[lang], link_preview_options=LinkPreviewOptions(is_disabled=True)
            ),
            thumb_url=SEARCH_ICON,
        )
    ]
    await inline_query.answer(results=results, cache_time=1)


async def answer_inline(
    inline_query: InlineQuery,
    results: list[InlineQueryResult],
    *,
    lang: str,
    cache_time: int = 0,
    switch_pm_text: str = "",
    switch_pm_parameter: str = "",
    fallback_results: list[InlineQueryResult] | None = None,
) -> None:
    """回答内联查询, 失败时退化成一条可读的错误结果。

    Telegram 对内联回答很挑 (例如富文本里带外部媒体会直接
    ``400 EXTERNAL_MEDIA_NOT_SUPPORTED``); 一旦答案送不出去, 客户端只会一直转圈,
    用户完全看不到原因。所以这里兜一层: 先按完整结果发, 失败就把富文本项剔掉再试,
    仍失败则回一条纯文字提示。
    """
    _t = t_[lang]
    try:
        await inline_query.answer(
            results[:50],
            cache_time=cache_time,
            switch_pm_text=switch_pm_text,
            switch_pm_parameter=switch_pm_parameter,
        )
        return
    except BadRequest as e:
        logger.warning(f"内联回答被拒: {e}")

    # 富文本项最可能是元凶 (它依赖的设置最多), 去掉后再试一次
    plain = [r for r in results if not _is_rich_result(r)]
    if not plain and fallback_results:
        plain = fallback_results  # 结果全是富文本时换用调用方给的备选 (通常是媒体项)
    if plain and plain != results:
        try:
            await inline_query.answer(
                plain[:50],
                cache_time=cache_time,
                switch_pm_text=switch_pm_text,
                switch_pm_parameter=switch_pm_parameter,
            )
            return
        except BadRequest as e:
            logger.warning(f"去掉富文本项后仍被拒: {e}")

    await inline_query.answer(
        results=[
            InlineQueryResultArticle(
                title=_t("解析失败"),
                description=_t("请稍后重试, 或直接把链接发到聊天里"),
                input_message_content=InputTextMessageContent(
                    _t("解析失败"), link_preview_options=LinkPreviewOptions(is_disabled=True)
                ),
            )
        ],
        cache_time=1,
    )


def inline_cover_url(parse_result: AnyParseResult) -> str:
    """取一个可用于 inline 占位的封面 URL (Telegram 服务端去抓)。

    视频/动图用平台的缩略图, 图片用它自己的地址 —— 这正是老模式的做法:
    消息里先出现封面, 选中后处理完再换成真正的媒体。
    """
    for ref in to_list(parse_result.media):
        thumb = getattr(ref, "thumb_url", None)
        if isinstance(ref, VideoRef | AniRef) and thumb:
            return str(thumb)
        if isinstance(ref, ImageRef):
            return str(thumb or ref.url)
    return ""


def inline_reply_markup(parse_result: AnyParseResult) -> Ikm | None:
    """富文本结果项的键盘。

    只有**有媒体**时才挂: 它的唯一作用是让 Telegram 回传 inline_message_id,
    以便选中后把消息编辑成带媒体的版本。纯文字结果不需要二次编辑, 也就不要键盘。
    """
    # 用 count_inline_media 判空: to_list(None) 会给出 [None], 不能直接当"有媒体"
    if not count_inline_media(parse_result.media):
        return None
    return Ikm([[Ikb("原链接", url=parse_result.raw_url)]])


def _is_rich_result(result: InlineQueryResult) -> bool:
    """结果项是不是那条需要 Telegram 特殊支持的富文本结果 (被拒时优先剔掉它)。"""
    if getattr(result, "id", None) == RICH_RESULT_ID:
        return True
    return isinstance(getattr(result, "input_message_content", None), InputRichMessageContent)


@Client.on_inline_query(platform_filter(False))
@with_request_id
async def call_inline_parse(cli: Client, inline_query: InlineQuery) -> None:
    logger.info(f"收到内联解析请求: query={inline_query.query}, from_user={inline_query.from_user.id}")
    try:
        await _call_inline_parse(cli, inline_query)
    except Exception as e:
        # 解析异常同样不能让客户端转圈: 给一条可读的结果
        logger.opt(exception=e).warning(f"内联解析失败: {e}")
        try:
            async with get_session() as session:
                lang = await UserService(session).get_lang(inline_query.from_user.id)
        except Exception:  # noqa: BLE001 - 兜底路径不再抛错
            lang = ""
        await answer_inline(inline_query, [], lang=lang)


async def _call_inline_parse(cli: Client, inline_query: InlineQuery) -> None:
    raw_url = await ParseService().get_raw_url(inline_query.query)
    async with get_session() as session:
        lang = await UserService(session).get_lang(inline_query.from_user.id)
        config = await SettingsService(session).get_config_by_user(inline_query.from_user.id)
    if cached := await persistent_cache.get(raw_url):
        logger.debug("inline: 缓存命中, 构建富文本结果")
        # 缓存里已有 file_id: 富文本项可以直接带上媒体, 无需二次编辑 (file_id 复用不上传)。
        # 没有 file_id 的旧缓存则给纯文字占位, 选中后由 inline_result_download 补齐媒体。
        results = [build_cached_rich_result(cached, raw_url, lang, config)]
        switch_pm_text, switch_pm_parameter = await build_switch_pm(
            count_inline_media(cached.media), raw_url, lang
        )
        await answer_inline(
            inline_query,
            results,
            lang=lang,
            cache_time=60,
            switch_pm_text=switch_pm_text,
            switch_pm_parameter=switch_pm_parameter,
        )
        return

    parse_result = await parse_cache.get(raw_url)
    if parse_result is None:
        parse_result = await ParseService().parse(inline_query.query)
        await parse_cache.set(raw_url, parse_result)

    results = await build_inline_results(parse_result, cli, lang, config)
    logger.debug(f"inline 查询完成, 返回 {len(results)} 个结果")
    switch_pm_text, switch_pm_parameter = await build_switch_pm(
        count_inline_media(parse_result.media), raw_url, lang
    )
    await answer_inline(inline_query, results, lang=lang, cache_time=0,
                        switch_pm_text=switch_pm_text, switch_pm_parameter=switch_pm_parameter)


async def _drop_inline_keyboard(cli: Client, inline_message_id: str) -> None:
    """摘掉 inline 结果自带的键盘。

    带键盘是 Telegram 回传 inline_message_id 的前提, 但用户并不需要这个按钮,
    所以一拿到句柄就立刻清掉。

    不能用空的 InlineKeyboardMarkup: 它序列化成 ReplyInlineMarkup(rows=[]),
    会被 Telegram 以 REPLY_MARKUP_INVALID 拒掉。移除键盘要用 ReplyKeyboardHide
    (Bot API 传空 inline_keyboard 时映射的也是这个类型)。
    """
    try:
        unpacked = utils.unpack_inline_message_id(inline_message_id)
        session = await cli.get_session(unpacked.dc_id, is_media=True)
        await session.invoke(
            raw.functions.messages.EditInlineBotMessage(
                id=unpacked, reply_markup=raw.types.ReplyKeyboardHide()
            )
        )
    except Exception as e:
        logger.debug(f"摘除 inline 键盘失败: {e}")


@Client.on_chosen_inline_result()
@with_request_id
async def inline_result_download(cli: Client, chosen_result: ChosenInlineResult) -> None:
    """选中富文本结果后, 下载 → 上传 → 把消息编辑成带媒体的富文本。"""
    logger.info(
        f"收到 inline 选中回调: result_id={chosen_result.result_id!r}, "
        f"inline_message_id={chosen_result.inline_message_id!r}, query={chosen_result.query!r}"
    )
    if chosen_result.result_id != RICH_RESULT_ID:
        logger.info(f"跳过: result_id={chosen_result.result_id!r} 非富文本结果")
        return

    inline_message_id = chosen_result.inline_message_id
    if inline_message_id is None:
        # 没有键盘就不会有句柄: 说明这条结果本来没媒体, 无需二次编辑
        logger.info("inline 选中回调缺少 inline_message_id, 无需编辑")
        return

    async with get_session() as session:
        lang = await UserService(session).get_lang(chosen_result.from_user.id)
        config = await SettingsService(session).get_config_by_user(chosen_result.from_user.id)
        _t = t_[lang]

    query = chosen_result.query
    raw_url = await ParseService().get_raw_url(query)
    cached_result = await parse_cache.get(raw_url)
    caption = (
        build_caption(cached_result, config=config, allow_expandable=True, lang=lang, view_label=_t("查看"))
        if cached_result
        else ""
    )
    reporter = InlineStatusReporter(cli, inline_message_id, caption, t=_t, user_config=config)

    with ParsePipeline(query, raw_url, reporter, parse_result=cached_result, singleflight=False, t=_t) as pipeline:
        if (result := await pipeline.run()) is None:
            return

        parse_result = result.parse_result
        await reporter.report(_t("上 传 中..."))
        try:
            media_refs = to_list(parse_result.media)
            media, placeholders = build_media_items(
                result.processed_list,
                media_refs,
                is_sensitive=parse_result.is_sensitive,
            )
            markdown = build_rich_markdown(
                parse_result,
                config=config,
                lang=lang,
                view_label=_t("查看"),
                media_placeholders=placeholders,
            )
            logger.debug(f"inline 编辑为富文本: media={len(media)}, markdown={len(markdown)}")
            await edit_inline_rich_message(cli, inline_message_id, markdown=markdown, media=media)
        except Exception as e:
            logger.opt(exception=e).debug("详细堆栈")
            logger.error(f"inline 富文本编辑失败: {e}")
            await reporter.report_error(_t("上传"), e)
        finally:
            logger.debug("inline 富文本任务完成")


def _build_cached_rich_media(entry: CacheEntry):
    """把缓存里的 file_id 变成富文本的 media 块 + markdown 占位符。

    用已存在的 file_id 走 ``InputMediaPhoto/Document``: pyrogram 的 ``_get_input_photo``
    遇到 ``InputMediaPhoto`` 直接返回它的 id, **不触发任何上传**, 所以 inline 里也是即时可用。
    """
    from pyrogram.types import (
        InputMediaAnimation,
        InputMediaDocument,
        InputMediaPhoto,
        InputRichMessageMedia,
    )

    media: list[InputRichMessageMedia] = []
    placeholders: list[str] = []
    for index, m in enumerate(entry.media or []):
        match m.type:
            case CacheMediaType.PHOTO:
                kind, item = "photo", InputMediaPhoto(m.file_id)
            case CacheMediaType.VIDEO:
                kind, item = "video", InputMediaVideo(m.file_id)
            case CacheMediaType.ANIMATION:
                kind, item = "video", InputMediaAnimation(m.file_id)
            case CacheMediaType.DOCUMENT:
                kind, item = "document", InputMediaDocument(m.file_id)
            case _:
                continue
        media_id = f"m{index}"
        media.append(InputRichMessageMedia(media_id, item))
        placeholders.append(f"![](tg://{kind}?id={media_id})")
    return media, placeholders


def build_cached_rich_result(
    entry: CacheEntry, raw_url: str, lang: str, config: SettingsConfig
) -> InlineQueryResult:
    """缓存路径的富文本结果项 (带 file_id 媒体, 一项同时给图和页脚)。"""
    _t = t_[lang]
    media, placeholders = _build_cached_rich_media(entry)
    markdown = build_rich_markdown_by_str(
        entry.parse_result.title,
        entry.parse_result.content,
        raw_url,
        config=config,
        lang=lang,
        view_label=_t("查看"),
        author_name=entry.parse_result.author_name,
        author_handle=entry.parse_result.author_handle,
        published_at=entry.parse_result.published_at,
        view_count=entry.parse_result.view_count,
        media_placeholders=placeholders,
    )
    # 没有 file_id 时挂键盘: 提示客户端保留句柄, 选中后可补上媒体
    reply_markup = None if media else Ikm([[Ikb("原链接", url=raw_url)]])
    return InlineQueryResultArticle(
        id=RICH_RESULT_ID,
        title=clip_inline_text(entry.parse_result.title, INLINE_TITLE_LIMIT) or "-",
        description=clip_inline_text(entry.parse_result.content, INLINE_DESC_LIMIT),
        input_message_content=InputRichMessageContent(InputRichMessage(markdown=markdown, media=media or None)),
        reply_markup=reply_markup,
    )


def build_cached_inline_results(
    entry: CacheEntry, raw_url: str, lang: str, config: SettingsConfig
) -> list[InlineQueryResult]:
    """有 file_id 缓存时，构建 cached 类型的 inline 结果（Telegram 服务端直发）"""
    _t = t_[lang]

    content = entry.parse_result.content
    caption = build_caption_by_str(
        entry.parse_result.title,
        content,
        raw_url,
        entry.telegraph_url,
        hide_source=config.hide_source,
        author_name=entry.parse_result.author_name,
        hide_title=config.hide_title,
        hide_desc=config.hide_desc,
        # inline 也开折叠: 实测 pyrogram 会把 <blockquote expandable> 解析成
        # collapsed=True 的 blockquote 实体, Telegram 服务端存成 expandable_blockquote
        allow_expandable=True,
        metadata_line=build_metadata_line(
            published_at=entry.parse_result.published_at,
            view_count=entry.parse_result.view_count,
            lang=lang,
            view_label=_t("查看"),
        ),
    )
    title = clip_inline_text(entry.parse_result.title, INLINE_TITLE_LIMIT) or "-"

    results: list[InlineQueryResult] = []

    if config.enable_inline_raw_url:
        results.append(
            InlineQueryResultArticle(
                title=_t("原始链接"),
                description=raw_url,
                input_message_content=InputTextMessageContent(
                    raw_url, link_preview_options=LinkPreviewOptions(is_disabled=True)
                ),
                thumb_url=LINK_ICON_URL,
                thumb_width=LINK_ICON_WIDTH,
                thumb_height=LINK_ICON_HEIGHT,
            )
        )

    # 富文本
    if entry.telegraph_url:
        results.append(
            InlineQueryResultArticle(
                title=title,
                input_message_content=InputTextMessageContent(
                    caption,
                    link_preview_options=LinkPreviewOptions(show_above_text=True),
                ),
            )
        )
        return results

    if not entry.media:
        results.append(
            InlineQueryResultArticle(
                title=title,
                description=clip_inline_text(content, INLINE_DESC_LIMIT),
                input_message_content=InputTextMessageContent(
                    caption,
                    link_preview_options=LinkPreviewOptions(is_disabled=True),
                ),
            )
        )
        return results

    for m in entry.media:
        match m.type:
            case CacheMediaType.PHOTO:
                results.append(
                    InlineQueryResultCachedPhoto(
                        photo_file_id=m.file_id,
                        title=title,
                        caption=caption,
                        description=clip_inline_text(content, INLINE_DESC_LIMIT),
                    )
                )
            case CacheMediaType.VIDEO:
                results.append(
                    InlineQueryResultCachedVideo(
                        video_file_id=m.file_id,
                        title=title,
                        caption=caption,
                        description=clip_inline_text(content, INLINE_DESC_LIMIT),
                    )
                )
            case CacheMediaType.ANIMATION:
                results.append(
                    InlineQueryResultCachedAnimation(
                        animation_file_id=m.file_id,
                        title=title,
                        caption=caption,
                    )
                )
            case CacheMediaType.DOCUMENT:
                results.append(
                    InlineQueryResultCachedDocument(
                        document_file_id=m.file_id,
                        title=title,
                        caption=caption,
                        description=clip_inline_text(content, INLINE_DESC_LIMIT),
                    )
                )

    return results


#: markdown 里的图片/视频语法 (![](url) / ![alt](url)); inline 不支持外部媒体, 必须剥掉
_MEDIA_MARKDOWN_RE = re.compile(r"!\[[^\]]*\]\([^)\s]+\)")


def strip_media_markdown(markdown: str) -> str:
    """剥掉正文里的媒体语法。

    inline 结果**不支持外部媒体** —— 带 URL 的图片会让 Telegram 直接回
    ``400 EXTERNAL_MEDIA_NOT_SUPPORTED``, 整条 inline 回答失败 (表现是客户端一直转圈)。
    所以 inline 的富文本只保留文字排版, 图由单独的 InlineQueryResultPhoto/Video 提供。
    """
    return _MEDIA_MARKDOWN_RE.sub("", markdown or "")


def build_inline_rich_content(
    parse_result: AnyParseResult, *, lang: str, config: SettingsConfig
) -> InputRichMessageContent:
    """inline 结果的富文本内容 (纯文字排版 + 页尾, 不带任何媒体)。"""
    _t = t_[lang]
    markdown = build_rich_markdown(parse_result, config=config, lang=lang, view_label=_t("查看"))
    return InputRichMessageContent(InputRichMessage(markdown=strip_media_markdown(markdown)))


async def build_inline_results(
    parse_result: AnyParseResult, cli: Client, lang: str, config: SettingsConfig
) -> list[InlineQueryResult]:
    """根据解析结果构建内联查询结果列表"""
    logger.debug(f"构建 inline 结果: type={parse_result.type}, title={parse_result.title}")
    _t = t_[lang]

    title = clip_inline_text(parse_result.title, INLINE_TITLE_LIMIT) or "-"
    media_list = to_list(parse_result.media)
    # 这个键盘不是为了给用户点: Telegram 只在消息带 inline keyboard 时才回传
    # inline_message_id, 没有它 bot 既不能更新进度也无法把封面图替换成视频。
    # 选中后 inline_result_download 会第一时间把它摘掉, 用户看不到按钮。
    reply_markup = Ikm([[Ikb(_t("原链接"), url=parse_result.raw_url)]])

    results: list[InlineQueryResult] = []
    if config.enable_inline_raw_url:
        results.append(
            InlineQueryResultArticle(
                title=_t("原始链接"),
                description=parse_result.raw_url,
                input_message_content=InputTextMessageContent(
                    parse_result.raw_url, link_preview_options=LinkPreviewOptions(is_disabled=True)
                ),
                thumb_url=LINK_ICON_URL,
                thumb_width=LINK_ICON_WIDTH,
                thumb_height=LINK_ICON_HEIGHT,
            )
        )

    # ── 富文本 (rich message): 还原原文排版, 统计与来源进页尾 ──
    # 所有类型都用这一项。回答查询时不能带媒体 (Telegram 会拒 EXTERNAL_MEDIA_NOT_SUPPORTED),
    # 所以这里只发文字占位; 用户选中后由 inline_result_download 下载+上传, 再编辑成带媒体的
    # 富文本。键盘是为了换取 inline_message_id (编辑的前提), 选中后立刻摘掉。
    if config.rich_mode:
        # 有封面可用的媒体: 用封面图做占位 (消息里先出现一张图, 与老模式观感一致),
        # 选中后再由 inline_result_download 编辑成带媒体的富文本。
        # 富文本本身在回答查询时不能带外链媒体, 但 InlineQueryResultPhoto 的 URL 是
        # Telegram 服务端去抓的, 这里没问题。
        if cover := inline_cover_url(parse_result):
            first = to_list(parse_result.media)[0]
            results.append(
                InlineQueryResultPhoto(
                    cover,
                    id=RICH_RESULT_ID,
                    thumb_url=cover,
                    photo_width=max(getattr(first, "width", 0) or 0, 0),
                    photo_height=max(getattr(first, "height", 0) or 0, 0),
                    title=title,
                    description=clip_inline_text(parse_result.content, INLINE_DESC_LIMIT),
                    caption=build_caption(
                        parse_result, config=config, allow_expandable=True, lang=lang, view_label=_t("查看")
                    ),
                    reply_markup=inline_reply_markup(parse_result),
                )
            )
            return results

        results.append(
            InlineQueryResultArticle(
                id=RICH_RESULT_ID,
                title=title,
                description=clip_inline_text(parse_result.content, INLINE_DESC_LIMIT),
                input_message_content=build_inline_rich_content(parse_result, lang=lang, config=config),
                reply_markup=inline_reply_markup(parse_result),
            )
        )
        return results

    # ── 富文本直接 telegraph 发送 ──
    if isinstance(parse_result, RichTextParseResult):
        url = await create_richtext_telegraph(cli, parse_result)
        caption = build_caption(
            parse_result, url, config=config, allow_expandable=True, lang=lang, view_label=_t("查看")
        )
        results.append(
            InlineQueryResultArticle(
                title=title,
                description=clip_inline_text(parse_result.content, INLINE_DESC_LIMIT),
                input_message_content=InputTextMessageContent(
                    caption,
                    link_preview_options=LinkPreviewOptions(show_above_text=True),
                ),
            )
        )
        return results

    # inline 结果同样折叠长正文 (同 build_cached_inline_results 的理由)
    caption = build_caption(
        parse_result, config=config, allow_expandable=True, lang=lang, view_label=_t("查看")
    )

    if not media_list:
        results.append(
            InlineQueryResultArticle(
                title=title,
                description=clip_inline_text(parse_result.content, INLINE_DESC_LIMIT),
                input_message_content=InputTextMessageContent(
                    caption,
                    link_preview_options=LinkPreviewOptions(is_disabled=True),
                ),
            )
        )
        return results

    for index, media_ref in enumerate(media_list):
        if isinstance(media_ref, ImageRef):
            results.append(
                InlineQueryResultPhoto(
                    media_ref.url,
                    thumb_url=media_ref.thumb_url,
                    photo_width=media_ref.width,
                    photo_height=media_ref.height,
                    caption=caption,
                    title=title,
                    description=clip_inline_text(parse_result.content, INLINE_DESC_LIMIT),
                )
            )
        elif isinstance(media_ref, VideoRef):
            results.append(
                InlineQueryResultPhoto(
                    media_ref.thumb_url or DEFAULT_PARSE_RESULT_THUMB_URL,
                    photo_width=media_ref.width,
                    photo_height=media_ref.height,
                    id=f"download_{index}",
                    title=title,
                    caption=caption,
                    reply_markup=reply_markup,
                )
            )
        elif isinstance(media_ref, AniRef):
            if media_ref.ext != "gif":
                results.append(
                    InlineQueryResultVideo(
                        media_ref.url,
                        media_ref.thumb_url or DEFAULT_PARSE_RESULT_THUMB_URL,
                        caption=caption,
                        title=title,
                        description=clip_inline_text(parse_result.content, INLINE_DESC_LIMIT),
                    )
                )
            else:
                results.append(
                    InlineQueryResultAnimation(
                        media_ref.url,
                        thumb_url=media_ref.thumb_url,
                        caption=caption,
                        title=title,
                        description=clip_inline_text(parse_result.content, INLINE_DESC_LIMIT),
                    )
                )

    logger.debug(f"inline 结果构建完成: count={len(results)}")
    return results
