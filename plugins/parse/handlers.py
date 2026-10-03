import asyncio
import re
from dataclasses import replace

from parsehub.types import AniRef, RichTextParseResult
from pyrogram import Client, filters
from pyrogram.types import Message

from core import bs
from db import get_session
from i18n import t_
from log import logger
from plugins.context import get_config_target
from plugins.filters import (
    allow_channel_auto_forward_parse_filter,
    forwarded_from_bot_filter,
    platform_filter,
    via_me_filter,
)
from plugins.helpers import build_caption, format_label
from plugins.parse.context import GIF_ONLY_SKIP_DOWNLOAD_COUNT_THRESHOLD, ParseOptions, ParseRequest
from plugins.parse.reporters import MessageStatusReporter, disable_progress_on_report_forbidden
from plugins.parse.sender import (
    MessageSender,
    build_gif_button,
    send_cached,
    send_raw,
    send_rich_media,
    send_zip,
)
from repo.settings import ParseMode
from services import ParsePipeline, ParseService, SettingsService, UserService
from services.cache import parse_cache, persistent_cache
from utils.helpers import to_list, with_request_id
from utils.rate_limit import ParseRateLimitExceeded, parse_rate_limit

logger = logger.bind(name="Parse")


@Client.on_message(
    filters.command(["jx", "jxjx", "raw", "zip"])
    | (
        (filters.text | filters.caption)
        & ~via_me_filter
        & platform_filter(True)
        & ~forwarded_from_bot_filter
        & allow_channel_auto_forward_parse_filter
    )
)
async def parse(cli: Client, msg: Message) -> None:
    bypass_cache = False
    lang = None
    mode = ParseMode.PREVIEW

    async with get_session() as session:
        if msg.from_user:
            lang = await UserService(session).get_lang(msg.from_user.id)
        config = await SettingsService(session).get_config(get_config_target(msg))
        mode = config.default_mode

    _t = t_[lang]

    if msg.command:
        match msg.command[0]:
            case "raw":
                mode = ParseMode.RAW
            case "jx":
                mode = ParseMode.PREVIEW
            case "jxjx":
                mode = ParseMode.PREVIEW
                bypass_cache = True
            case "zip":
                mode = ParseMode.ZIP

        text = " ".join(msg.command[1:]) if msg.command[1:] else ""
        if not text and msg.reply_to_message:
            text = msg.reply_to_message.text or msg.reply_to_message.caption or ""
        if not text:
            await MessageSender(cli, msg, config).text(format_label(_t("请加上链接或回复一条消息")))
            return
    else:
        text = msg.text or msg.caption or ""

    lines = text.strip().split()
    urls = list({i for i in lines if ParseService().parser.get_platform(i)})[:10]

    if not urls:
        await MessageSender(cli, msg, config).text(format_label(_t("不支持的平台")))
        return

    custom_content = ""
    if len(urls) == 1 and config.custom_content and msg.text:
        raw_text = msg.text
        if msg.entities:
            raw_text = cli.parser.unparse(msg.text, msg.entities, is_html=True)  # type: ignore[assignment]
        items: list[str] = [m.group(2) for m in re.finditer(r"^(={2})(.*?)\1", raw_text, flags=re.S | re.M)]
        custom_content = items[0].strip() if items else ""

    tasks = [
        _handle_parse_request(
            ParseRequest(
                cli=cli,
                msg=msg,
                url=url,
                mode=mode,
                config=config,
                t_=_t,
                bypass_cache=bypass_cache,
                delete_share_url_msg=config.auto_delete_url,
                custom_content=custom_content,
            )
        )
        for url in urls
    ]
    await asyncio.gather(*tasks)


async def parse_url(cli: Client, msg: Message, url: str) -> None:
    """按用户默认设置解析单个链接并发送。

    给"非文本入口"用: 走与普通消息完全相同的解析/发送流程 (含限流与状态消息)。
    """
    async with get_session() as session:
        if msg.from_user:
            lang = await UserService(session).get_lang(msg.from_user.id)
        else:
            lang = bs.language
        config = await SettingsService(session).get_config(get_config_target(msg))
    _t = t_[lang]
    await _handle_parse_request(
        ParseRequest(
            cli=cli,
            msg=msg,
            url=url,
            mode=config.default_mode,
            config=config,
            t_=_t,
        )
    )


@with_request_id
async def _handle_parse_request(req: ParseRequest) -> None:
    try:
        r = await handle_parse(req)
    except ParseRateLimitExceeded as e:
        if e.should_notify:
            logger.warning(f"速率限制 {e.retry_after:.1f}s, chat_id={req.chat_id}, msg_id={req.msg.id}")
            text = format_label(req.t_(f"解析过于频繁, 请在 {e.retry_after:.1f}s 后重试"))
            if bs.demo_mode:
                text += req.t_(
                    "\n\n>**为保障所有用户的使用体验, 当前已启用速率限制**\n\n"
                    ">本项目为开源项目, 如有高频或批量解析需求, 建议自行部署实例, "
                    "以免触发 Telegram API 全局速率限制\n\n"
                    "**开源地址: [GitHub](https://github.com/z-mio/parse_hub_bot)**"
                )
            await MessageSender(req.cli, req.msg, req.config).delete_after(e.retry_after).text_no_preview(text)
    else:
        if not r:
            return
        if req.delete_share_url_msg:
            logger.debug(f"自动删除分享链接消息: chat_id={req.chat_id}, msg_id: {req.msg.id}")
            try:
                await req.msg.delete()
            except Exception as e:
                logger.warning(f"删除分享链接消息失败: chat_id={req.chat_id}, msg_id: {req.msg.id}, error: {e}")


def _get_parse_user_id(req: ParseRequest) -> int | None:
    return req.chat_id


@parse_rate_limit(_get_parse_user_id)
async def handle_parse(req: ParseRequest) -> bool:
    options = ParseOptions.from_mode(req.mode, bypass_cache=req.bypass_cache)
    logger.info(f"收到解析请求: url={req.url}, chat_id={req.chat_id}, msg_id={req.msg.id}, mode={req.mode}")
    if req.bypass_cache:
        logger.debug("bypass_cache=True 绕过缓存")

    reporter = MessageStatusReporter(
        req.cli, req.msg, t=req.t_, config=req.config, on_forbidden=disable_progress_on_report_forbidden
    )
    sender = MessageSender(req.cli, req.msg, req.config)
    try:
        raw_url = await ParseService().get_raw_url(req.url)
    except Exception as e:
        await reporter.report_error(req.t_("获取原始链接"), e)
        return False

    if options.use_caching and not req.bypass_cache and (cached := await persistent_cache.get(raw_url)):
        logger.debug("file_id 缓存命中, 直接发送")
        try:
            await send_cached(sender, cached, raw_url, custom_content=req.custom_content, _t=req.t_)
        except Exception as e:
            logger.exception(e)
            logger.error("从缓存发送失败, 以上为错误信息")
            return False
        else:
            return True

    cached_parse_result = None if req.bypass_cache else await parse_cache.get(raw_url)
    with ParsePipeline(
        req.url,
        raw_url,
        reporter,
        parse_result=cached_parse_result,
        singleflight=options.singleflight,
        skip_media_processing=options.skip_media_processing,
        gif_only_skip_download_count_threshold=options.gif_only_skip_download_count_threshold,
        save_metadata=options.save_metadata,
        t=req.t_,
    ) as pipeline:
        if (result := await pipeline.run()) is None:
            if pipeline.waited:
                logger.debug("Singleflight 等待完成, 重新检查缓存")
                if not req.bypass_cache and (cached := await persistent_cache.get(raw_url)):
                    try:
                        await send_cached(sender, cached, raw_url, custom_content=req.custom_content, _t=req.t_)
                    except Exception as e:
                        logger.exception(e)
                        logger.error("从缓存发送失败, 以上为错误信息")
                        return False
                    else:
                        return True
                else:
                    return await handle_parse(replace(req, delete_share_url_msg=False))

            else:
                logger.debug("Pipeline 返回 None, 跳过后续处理")
            return False

        parse_result = result.parse_result
        await parse_cache.set(raw_url, parse_result)

        if isinstance(parse_result, RichTextParseResult):
            # 长文与普通结果同一条路径: 正文取 markdown_content, 排版交给富文本
            await sender.typing()
            await send_rich_media(
                sender,
                parse_result,
                result.processed_list,
                _t=req.t_,
                custom_content=req.custom_content,
                raw_url=raw_url,
            )
            await reporter.dismiss()
            return True

        caption = build_caption(
            parse_result,
            config=req.config,
            custom_content=req.custom_content,
            lang=req.t_.locale,
            view_label=req.t_("查看"),
        )
        media_refs = to_list(parse_result.media)
        # 显式要求非空: all() 对空列表返回 True, 不能让它把"没有媒体"当成"全是 GIF"
        gif_only = bool(media_refs) and all(isinstance(i, AniRef) for i in media_refs)
        if (
            req.mode == ParseMode.PREVIEW
            and gif_only
            and len(media_refs) > GIF_ONLY_SKIP_DOWNLOAD_COUNT_THRESHOLD
        ):
            await sender.typing()
            await sender.text_no_preview(caption, reply_markup=build_gif_button(to_list(parse_result.media)))
            await reporter.dismiss()
            return True

        if not result.processed_list:
            logger.debug("无媒体文件, 仅发送文本")
            await sender.typing()
            # 富文本的正文是 Telegram 服务端解析的 markdown, 与缓存里的 caption 格式不同,
            # 所以这条路径不写缓存 (下次重新解析)
            await send_rich_media(
                sender, parse_result, [], _t=req.t_, custom_content=req.custom_content, raw_url=raw_url
            )
            await reporter.dismiss()
            return True

        if req.mode == ParseMode.RAW:
            await send_raw(sender, result, reporter, _t=req.t_, custom_content=req.custom_content)
            return True
        if req.mode == ParseMode.ZIP:
            await send_zip(sender, result, reporter, _t=req.t_, custom_content=req.custom_content)
            return True

        logger.debug(f"开始上传媒体: media_count={len(result.processed_list)}")
        await reporter.report(req.t_("上 传 中..."))
        try:
            # 敏感内容的媒体打码在 send_rich_media 内部切到 blocks 路径 (官方 API 的
            # 富文本媒体块没有 spoiler, 只有 raw 的 PageBlockPhoto/Video 有)
            await send_rich_media(
                sender,
                parse_result,
                result.processed_list,
                _t=req.t_,
                custom_content=req.custom_content,
                raw_url=raw_url,
            )
            await reporter.dismiss()
            return True
        except Exception as e:
            logger.exception(e)
            logger.error("上传失败, 以上为错误信息")
            await reporter.report_error(req.t_("上传"), e)
            return False
