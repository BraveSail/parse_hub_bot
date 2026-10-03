"""缓存路径也走富文本: 字段与媒体 file_id 都从缓存重建, 零上传。"""

import types

from plugins.parse.inline_rich import build_cached_rich_content, cache_media_blocks, extract_cache_media
from services.cache import CacheEntry, CacheMedia, CacheMediaType, CacheParseResult


def _config():
    return types.SimpleNamespace(
        hide_title=False, hide_desc=False, hide_source=False, video_cover=False, rich_mode=True
    )


def _entry(**kwargs):
    fields = {
        "title": "無題",
        "content": "正文",
        "author_name": "隣人X",
        "author_handle": "user_ydyj5227",
        "author_url": "https://www.pixiv.net/users/123",
        "tags": ["AI画像", "足裏"],
    }
    fields.update(kwargs)
    media = [
        CacheMedia(type=CacheMediaType.PHOTO, file_id="f1"),
        CacheMedia(type=CacheMediaType.PHOTO, file_id="f2"),
        CacheMedia(type=CacheMediaType.VIDEO, file_id="f3", cover_file_id="c3"),
    ]
    return CacheEntry(parse_result=CacheParseResult(**fields), media=media, rich=True)


def test_cache_media_blocks_reuses_file_ids_without_upload():
    """file_id 直接进 InputMedia, 不触发上传"""
    media, placeholders, quoted, reply = cache_media_blocks(_entry())
    assert len(media) == 3
    assert placeholders == [
        "![](tg://photo?id=m0)",
        "![](tg://photo?id=m1)",
        "![](tg://video?id=m2)",
    ]
    assert quoted == []
    assert reply == []
    # InputMediaPhoto 把 file_id 存在 media 字段里
    assert media[0].media.media == "f1"


def test_cache_media_blocks_splits_the_quoted_tail():
    """末尾 N 项属于引用块: 要单独给出来, 否则被引用内容的媒体会摆到正文后面"""
    entry = _entry()
    entry.parse_result.quoted_media_count = 1
    media, placeholders, quoted, reply = cache_media_blocks(entry)
    assert placeholders == ["![](tg://photo?id=m0)", "![](tg://photo?id=m1)"]
    assert quoted == ["![](tg://video?id=m2)"]
    assert reply == []


def test_cached_rich_content_puts_quoted_media_in_the_quote_block():
    """缓存路径同样要把引用媒体放进引用块内部 (每行带 > 前缀)"""
    entry = _entry()
    entry.parse_result.quoted_media_count = 1
    entry.parse_result.content = "正文\n\n> *被引用的文字*"
    markdown, _ = build_cached_rich_content(
        entry, "https://www.pixiv.net/artworks/1", lang="zh-hans", config=_config(), view_label="查看"
    )
    assert "> *被引用的文字*" in markdown
    assert "> ![](tg://video?id=m2)" in markdown


def test_cached_rich_content_keeps_layout_tags_and_collage():
    markdown, media = build_cached_rich_content(
        _entry(), "https://www.pixiv.net/artworks/1", lang="zh-hans", config=_config(), view_label="查看"
    )
    assert "正文" in markdown
    assert "#AI画像" in markdown
    assert "<tg-collage>" in markdown  # 多图要包成图集
    assert "Source" in markdown
    assert len(media) == 3


def test_cached_rich_content_without_media():
    entry = CacheEntry(parse_result=CacheParseResult(title="T", content="正文"))
    markdown, media = build_cached_rich_content(entry, "https://x.com/a/status/1", lang="zh-hans", config=_config())
    assert "正文" in markdown
    assert media == []
    assert "<tg-collage>" not in markdown


def test_extract_cache_media_reads_file_ids_from_blocks():
    """发送后从服务端返回的块里取回 file_id (写缓存用)"""

    class FakePhoto:
        def __init__(self, fid):
            self.file_id = fid

    class RichBlockPhoto:
        def __init__(self, fid):
            self.photo = FakePhoto(fid)

    class RichBlockCollage:
        def __init__(self, blocks):
            self.blocks = blocks

    class RichMessage:
        blocks = [RichBlockCollage([RichBlockPhoto("a"), RichBlockPhoto("b")])]

    found = extract_cache_media(RichMessage())
    assert [m.file_id for m in found] == ["a", "b"]
    assert all(m.type == CacheMediaType.PHOTO for m in found)


def test_extract_cache_media_on_empty_message():
    assert extract_cache_media(None) == []
    assert extract_cache_media(types.SimpleNamespace(blocks=None)) == []
