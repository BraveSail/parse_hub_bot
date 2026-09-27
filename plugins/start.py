from pyrogram import Client, filters
from pyrogram.types import LinkPreviewOptions, Message

from db import get_session
from i18n import t_
from plugins.helpers import build_start_text
from plugins.parse.handlers import parse_url
from services import UserService, inline_start_link


@Client.on_message(filters.command(["start", "help"]))
async def start(cli: Client, msg: Message) -> None:
    if not msg.from_user:
        return

    async with get_session() as session:
        lang = await UserService(session).get_lang(msg.from_user.id)

    # inline 多图结果带来的深链参数: /start <token> → 直接在私聊里发全部媒体
    command = msg.command or []
    if len(command) > 1:
        if raw_url := await inline_start_link.resolve(command[1]):
            await parse_url(cli, msg, raw_url)
            return
        await msg.reply(
            t_[lang]("链接已失效, 请直接在聊天中发送链接"),
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        )
        return

    await msg.reply(
        build_start_text()[lang],
        link_preview_options=LinkPreviewOptions(is_disabled=True),
    )
