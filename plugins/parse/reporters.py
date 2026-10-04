import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from easy_ai18n import PreLocaleSelector
from pyrogram import Client
from pyrogram.errors import FloodWait, Forbidden, SlowmodeWait
from pyrogram.types import InputRichMessage, LinkPreviewOptions, Message

from core import bs
from db import get_session
from log import logger
from plugins.context import get_config_target
from plugins.helpers import format_label
from plugins.parse.sender import MessageSender
from repo.settings import SettingsConfig
from services import SettingsService, StatusReporter

logger = logger.bind(name="ParseReporter")

#: 状态消息收尾时编辑成的内容。
#:
#: **收尾用编辑、不用删除**: 删除会在 Telegram 的后台留下删除记录 (用户明确要求避免)。
#: 也不能编辑成空串/零宽字符/空格 —— 实测 Telegram 一律回 ``400 MESSAGE_EMPTY``
#: (U+200B、U+200D、空格、空串都试过), 所以只能用最小的可见字符。
STATUS_DONE_TEXT = "·"

#: 状态/占位消息一律关掉链接预览。
#:
#: 不关的话, 只要消息文本里带链接 (文本结果、GIF 提示、错误信息里的 URL), Telegram
#: 就会在消息下面挂一张 **link preview 卡片** —— 卡片在正文之外, 折叠块盖不住它,
#: 于是"内容已经遮住了但封面图还露在外面" (用户报「不然还是能看到图」)。
#:
#: 实测: 含链接的文本消息不传该参数 -> ``web_page`` 有值; 传 ``is_disabled=True`` -> 无。
#: 富文本消息本身不生成预览 (实测), 但这里一并传, 免得编辑时把旧预览留下来。
_NO_PREVIEW = LinkPreviewOptions(is_disabled=True)


async def disable_progress_on_report_forbidden(msg: Message, config: SettingsConfig) -> None:
    """状态消息无权限时自动关闭解析进度。"""
    config.noprogress = True
    target = get_config_target(msg, include_member=False)
    async with get_session() as session:
        await SettingsService(session).patch_config(target=target, noprogress=True)
    logger.warning(f"已自动关闭解析进度: {target}")


class MessageStatusReporter(StatusReporter):
    """基于 Telegram Message 的状态报告器"""

    def __init__(
        self,
        cli: Client,
        user_msg: Message,
        *,
        t: PreLocaleSelector,
        config: SettingsConfig,
        on_forbidden: Callable[[Message, SettingsConfig], Awaitable[None]] | None = None,
    ):
        self._cli = cli
        self._user_msg = user_msg
        self._msg: Message | None = None
        self._t = t
        self._config = config
        self._on_forbidden = on_forbidden

    async def report(self, text: str) -> None:
        if self._config.noprogress:
            return
        await self._edit_text(format_label(text))

    async def report_error(self, stage: str, error: Exception) -> None:
        if self._config.hide_error:
            return

        t = format_label(self._t(f"{stage}错误:"))
        text = self._t(f"{t} \n```\n{error}```")
        if bs.demo_mode:
            text += self._t("\n\n<b>问题反馈: @MisakaSisters</b>")
        await self._edit_text(
            text,
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        )
        if self._config.keep_error_log:
            return

        async def fn() -> None:
            await asyncio.sleep(15)
            await self.dismiss()

        loop = asyncio.get_running_loop()
        loop.create_task(fn())

    @property
    def has_message(self) -> bool:
        """是否已经发出过状态消息 (noprogress 或首条发送失败时为 False)。"""
        return self._msg is not None

    async def finalize(self, rich_message: InputRichMessage) -> Message | None:
        """把状态消息**编辑成最终结果** (同一条消息走完整个流程)。

        这样聊天里只留一条消息 —— 从"解析中"一路编辑到最终富文本, 既没有删除记录,
        也没有多余的临时消息。

        没有状态消息时返回 None (调用方改为新建); 编辑失败**不吞异常** ——
        由调用方决定回退(新建)还是报错, 免得结果静默丢失。
        """
        if self._msg is None:
            return None
        logger.debug(f"编辑状态消息为最终结果: msg_id={self._msg.id}")
        # 富文本自身不生成预览 (实测), 但这里显式关掉 —— 免得把状态消息阶段可能留下的
        # 预览保留下来 (编辑时不传该参数 = 保持原值)
        edited = await self._msg.edit_text(rich_message=rich_message, link_preview_options=_NO_PREVIEW)
        return edited or self._msg

    async def finalize_text(self, text: str, **kwargs: Any) -> Message | None:
        """把状态消息编辑成**纯文本**结果 (不新建消息)。

        给不走富文本的结果用 (例如 GIF 过多只发文字提示那种)。失败不吞异常 ——
        调用方据此回退新建。
        """
        if self._msg is None:
            return None
        # 文本结果里常带来源链接 —— 不关预览就会挂出一张封面卡片 (折叠盖不到)
        kwargs.setdefault("link_preview_options", _NO_PREVIEW)
        logger.debug(f"编辑状态消息为文本结果: msg_id={self._msg.id}")
        edited = await self._msg.edit_text(text, **kwargs)
        return edited or self._msg

    async def dismiss(self) -> None:
        """收尾: 把状态消息编辑成最小标记, **而不是删除**。

        删除会在聊天后台留下"消息已删除"的记录; 编辑不会。用户明确要求避免前者。

        这里吞掉所有异常: dismiss 是流程末尾的清理动作, 它失败不该让调用方把
        已经成功的解析误判成失败 (handlers 里 dismiss 在 try 块内, 抛错会被当成"上传失败")。
        """
        if self._msg is None:
            return
        try:
            await self._edit_text(STATUS_DONE_TEXT)
        except Exception as e:  # noqa: BLE001 - 清理动作不该影响结果
            logger.debug(f"状态消息收尾失败 (已忽略): {type(e).__name__}: {e}")

    async def _edit_text(self, text: str, **kwargs: Any) -> None:
        # 状态/占位消息默认关预览: 文本里万一带链接, 预览卡片会把内容露在折叠之外
        kwargs.setdefault("link_preview_options", _NO_PREVIEW)
        try:
            if self._msg is None:
                self._msg = await MessageSender(self._cli, self._user_msg, self._config).text(text, **kwargs)
            else:
                if self._msg.text != text:
                    await self._msg.edit_text(text, **kwargs)
        except (FloodWait, SlowmodeWait):
            pass
        except Forbidden as e:
            logger.warning(f"状态消息发送失败, Bot 无权限: {e}")
            if self._on_forbidden:
                await self._on_forbidden(self._user_msg, self._config)
        except Exception as e:  # noqa: BLE001
            # 状态消息是尽力而为: 它被用户手动删掉、或编辑被拒, 都不该打断解析本身
            logger.debug(f"状态消息更新失败 (已忽略): {type(e).__name__}: {e}")


class InlineStatusReporter(StatusReporter):
    """基于 inline_message_id 的状态报告器"""

    def __init__(
        self,
        cli: Client,
        inline_message_id: str,
        caption: str = "",
        *,
        t: PreLocaleSelector,
        user_config: SettingsConfig,
    ):
        self._cli = cli
        self._mid = inline_message_id
        self._caption = caption
        self._last_text: str | None = None
        self._t = t
        self._user_config = user_config

    async def report(self, text: str) -> None:
        text = format_label(text)
        full = f"{self._caption}\n{text}" if self._caption else text
        if full == self._last_text:
            return
        self._last_text = full
        await self._edit_inline_text(inline_message_id=self._mid, text=full)

    async def report_error(self, stage: str, error: Exception) -> None:
        if self._user_config.hide_error:
            return

        text = self._t(f"{format_label(f'{stage}错误:')} \n```\n{error}```")
        if bs.demo_mode:
            text += self._t("\n\n<b>问题反馈: @MisakaSisters</b>")
        await self._edit_inline_text(
            inline_message_id=self._mid, text=text, link_preview_options=LinkPreviewOptions(is_disabled=True)
        )

        if self._user_config.keep_error_log:
            return

        async def fn() -> None:
            await asyncio.sleep(15)
            await self._edit_inline_text(
                inline_message_id=self._mid,
                text=self._caption,
                link_preview_options=LinkPreviewOptions(is_disabled=True),
            )

        loop = asyncio.get_running_loop()
        loop.create_task(fn())

    async def _edit_inline_text(self, **kwargs: Any) -> None:
        try:
            await self._cli.edit_inline_text(**kwargs)
        except (FloodWait, SlowmodeWait):
            pass
        except Forbidden as e:
            logger.warning(f"消息发送失败, Bot 无权限: {e}")

    async def dismiss(self) -> None:
        pass
