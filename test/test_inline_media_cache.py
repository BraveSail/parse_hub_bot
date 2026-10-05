"""inline 的媒体记账: 借上传之机取出 file_id, 让下一次选中零下载/零上传。

背景（2026-10-05）: inline 消息只能用 `EditInlineBotMessage` 更新, 它**只回 Bool** ——
拿不到服务端生成的 file_id, 所以 inline 路径从来不写 file_id 缓存, 每次选中都要
重新下载 + 上传。而 `InputRichMessageMedia.write` 内部上传后 file_id 就留在库里拿不出来,
所以这里**先自己上传**, 从 `messages.UploadMedia` 的 raw 结果构造 file_id, 并把媒体换成
file_id 引用 (编辑时不再二次上传)。
"""

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from pyrogram.types import InputMediaPhoto, InputMediaVideo, InputRichMessageMedia

from plugins.parse.inline_rich import upload_media_for_cache
from services.cache import CacheMediaType


def _raw_photo(*, dc_id: int = 5, media_id: int = 6203886642406298436):
    return SimpleNamespace(
        dc_id=dc_id,
        id=media_id,
        access_hash=123456789,
        file_reference=b"\x01\x02\x03",
        sizes=[SimpleNamespace(type="m", w=320, h=240), SimpleNamespace(type="x", w=1280, h=960)],
    )


def _raw_document(*, dc_id: int = 5, media_id: int = 777):
    return SimpleNamespace(dc_id=dc_id, id=media_id, access_hash=987654321, file_reference=b"\x04\x05")


def _cli(*, photo=None, document=None) -> SimpleNamespace:
    uploaded = SimpleNamespace(photo=photo, document=document)
    return SimpleNamespace(invoke=AsyncMock(return_value=uploaded), save_file=AsyncMock(return_value="handle"))


def _local(tmp_path: Path, name: str = "a.jpg") -> str:
    path = tmp_path / name
    path.write_bytes(b"x" * 32)
    return str(path)


def test_a_local_photo_is_uploaded_and_swapped_for_its_file_id(tmp_path):
    """本地照片: 上传 → 换成 file_id 引用 (编辑时不再上传) → 返回可缓存条目"""
    source = _local(tmp_path)
    item = InputRichMessageMedia(id="m0", media=InputMediaPhoto(source))
    cli = _cli(photo=_raw_photo())

    entries = asyncio.run(upload_media_for_cache(cli, [item]))

    assert len(entries) == 1
    assert entries[0].type is CacheMediaType.PHOTO
    assert entries[0].file_id, "file_id 不能为空"
    # 关键: 就地换成了 file_id 引用 (否则编辑阶段会再上传一次)。
    # 注意 file_id 本身就是字符串 —— 判据是"等于刚记账的 file_id", 不是"不是字符串"
    assert item.media.media == entries[0].file_id, "媒体应已换成 file_id 引用"
    cli.invoke.assert_awaited_once()


def test_the_constructed_file_id_encodes_the_photo_identity(tmp_path):
    """构造出的 file_id 必须带上传返回的身份 (media_id/dc_id 可解出)"""
    from pyrogram.file_id import FileId

    source = _local(tmp_path)
    item = InputRichMessageMedia(id="m0", media=InputMediaPhoto(source))
    entries = asyncio.run(upload_media_for_cache(_cli(photo=_raw_photo(media_id=4242)), [item]))

    decoded = FileId.decode(entries[0].file_id)
    assert decoded.media_id == 4242
    assert decoded.dc_id == 5


def test_a_local_video_is_accounted_as_video(tmp_path):
    source = _local(tmp_path, "v.mp4")
    item = InputRichMessageMedia(id="m0", media=InputMediaVideo(source))
    cli = _cli(document=_raw_document())

    entries = asyncio.run(upload_media_for_cache(cli, [item]))

    assert len(entries) == 1
    assert entries[0].type is CacheMediaType.VIDEO
    assert item.media.media == entries[0].file_id


def test_an_existing_file_id_is_left_alone(tmp_path):
    """已经是 file_id (缓存命中/引用) 的项不该被上传, 也不产生条目"""
    item = InputRichMessageMedia(id="m0", media=InputMediaPhoto("AgACAgUAAxUAAWrCRY1FAKE"))
    cli = _cli(photo=_raw_photo())

    entries = asyncio.run(upload_media_for_cache(cli, [item]))

    assert entries == []
    cli.invoke.assert_not_awaited()


def test_an_upload_failure_does_not_raise_and_records_nothing(tmp_path):
    """记账失败**不能**影响发送本身 —— 只是这次不写缓存 (退化成改动前的行为)"""
    source = _local(tmp_path)
    item = InputRichMessageMedia(id="m0", media=InputMediaPhoto(source))
    cli = SimpleNamespace(
        invoke=AsyncMock(side_effect=RuntimeError("upload boom")),
        save_file=AsyncMock(return_value="handle"),
    )

    entries = asyncio.run(upload_media_for_cache(cli, [item]))

    assert entries == []
    assert item.media.media == source, "失败时保持原样 (仍是本地路径), 交给后续步骤上传"


def test_a_partial_batch_is_reported_as_partial(tmp_path):
    """一条本地 + 一条已是 file_id: 只记账 1 条 —— 调用方据此**放弃写缓存**
    (部分成功时引用/回复媒体的计数会对不上, 写进去会让消息把图放错位置)"""
    local_item = InputRichMessageMedia(id="m0", media=InputMediaPhoto(_local(tmp_path)))
    cached_item = InputRichMessageMedia(id="m1", media=InputMediaPhoto("AgACAgUAAxUAAWrCRY1FAKE"))

    entries = asyncio.run(upload_media_for_cache(_cli(photo=_raw_photo()), [local_item, cached_item]))

    assert len(entries) == 1
    assert len(entries) != 2, "调用方会因为这个不等而跳过写缓存"


def test_no_media_is_a_noop():
    assert asyncio.run(upload_media_for_cache(_cli(photo=_raw_photo()), [])) == []


def test_the_media_count_mismatch_guard_exists_in_the_handler():
    """盯住调用点的保护: 只有**全部**媒体都记上账才写缓存。

    这是**纯副作用**的一行判断 (删掉不会让别的测试变红), 所以用源码断言看着。
    """
    source = Path(__file__).resolve().parent.parent / "plugins" / "parse" / "inline.py"
    text = source.read_text(encoding="utf-8")
    assert "len(uploaded_media) == len(media)" in text, "写缓存前必须确认全部媒体都记上账"
    assert "upload_media_for_cache" in text
    assert "rich_cache_entry" in text


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
