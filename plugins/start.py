from pyrogram import Client, filters
from pyrogram.types import LinkPreviewOptions, Message

from db import get_session
from plugins.helpers import build_start_text
from services import UserService


@Client.on_message(filters.command(["start", "help"]))
async def start(cli: Client, msg: Message) -> None:
    if not msg.from_user:
        return

    async with get_session() as session:
        lang = await UserService(session).ensure_lang(msg.from_user.id, msg.from_user.language_code)

    await msg.reply(
        build_start_text()[lang],
        link_preview_options=LinkPreviewOptions(is_disabled=True),
    )
