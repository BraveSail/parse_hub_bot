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
    entry.parse_result.content = "正文\n\n> <i>被引用的文字</i>"
    markdown, _ = build_cached_rich_content(
        entry, "https://www.pixiv.net/artworks/1", lang="zh-hans", config=_config(), view_label="查看"
    )
    assert "> <i>被引用的文字</i>" in markdown
    assert "> ![](tg://video?id=m2)" in markdown


def test_cached_rich_content_keeps_layout_tags_and_collage():
    markdown, media = build_cached_rich_content(
        _entry(), "https://www.pixiv.net/artworks/1", lang="zh-hans", config=_config(), view_label="查看"
    )
    assert "正文" in markdown
    assert "#AI画像" in markdown
    assert "<tg-collage>" in markdown  # 多图要包成图集
    assert "来源" in markdown
    assert len(media) == 3


def test_cached_rich_content_without_media():
    entry = CacheEntry(parse_result=CacheParseResult(title="T", content="正文"))
    markdown, media = build_cached_rich_content(entry, "https://x.com/a/status/1", lang="zh-hans", config=_config())
    assert "正文" in markdown
    assert media == []
    assert "<tg-collage>" not in markdown


# ── 写缓存: rich_cache_entry ────────────────────────────────────────────
#
# 这条路径原先**完全没有测试**, 于是一次"把函数挪个位置"的改动把里面的
# `from parsehub.utils.helpers import get_parse_author_name` 写错了模块
# (真名在 plugins.helpers), 延迟 import 只在真正写缓存时才炸 —— 测试全绿,
# 生产每次上传后写缓存都失败 (用户报 ImportError)。


def _any_result(**over):
    base = {
        "title": "标题",
        "content": "正文",
        "author_name": "作者名",
        "author_handle": "handle",
        "author_url": "https://example.com/u/handle",
        "is_sensitive": True,
        "published_at": None,
        "view_count": 11,
        "like_count": 22,
        "tags": ["a", "b"],
    }
    base.update(over)
    return types.SimpleNamespace(**base)


def test_rich_cache_entry_copies_every_field():
    """写缓存的字段搬运要完整 —— 少一个字段, 二次发送就丢一个信息"""
    from plugins.parse.inline_rich import rich_cache_entry
    from services.cache import CacheMediaType

    media = [CacheMedia(type=CacheMediaType.PHOTO, file_id="f1")]
    entry = rich_cache_entry(_any_result(), media, quoted_media_count=1, reply_media_count=0)

    pr = entry.parse_result
    assert pr.title == "标题"
    assert pr.content == "正文"
    assert pr.author_handle == "handle"
    assert pr.author_url == "https://example.com/u/handle"
    assert pr.is_sensitive is True
    assert pr.view_count == 11
    assert pr.like_count == 22
    assert pr.tags == ["a", "b"]
    assert pr.quoted_media_count == 1
    assert pr.reply_media_count == 0
    assert entry.media == media
    assert entry.rich is True


def test_rich_cache_entry_resolves_the_author_name():
    """作者名要走 get_parse_author_name (它认 platform-specific 的作者字段)"""
    from plugins.helpers import get_parse_author_name
    from plugins.parse.inline_rich import rich_cache_entry

    result = _any_result()
    entry = rich_cache_entry(result, [])
    assert entry.parse_result.author_name == get_parse_author_name(result)


def test_rich_cache_entry_handles_missing_optional_fields():
    """标签等可选字段缺失时不能炸"""
    from plugins.parse.inline_rich import rich_cache_entry

    bare = types.SimpleNamespace(
        title="t", content="c", author_name="a", is_sensitive=False,
        author_handle="", author_url="", published_at=None,
        view_count=None, like_count=None, tags=None,
    )
    entry = rich_cache_entry(bare, [])
    assert entry.parse_result.tags == []
    assert entry.media is None


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


# ── /s 手动打码: 缓存命中也得照遮 ───────────────────────────────────────
#
# 回归: 缓存路径原先走 build_cached_rich_content 时不带这个参数, 于是
# "先发一次普通链接建立缓存, 再加 /s 发同一链接" 会直接从 file_id 缓存发出
# 没遮的版本 —— 用户看到的表现就是"/s 不生效"。


def test_cached_content_folds_when_spoiler_is_forced():
    markdown, _ = build_cached_rich_content(
        _entry(), "https://x.com/a/status/1", lang="zh-hans", config=_config(), spoiler_tag="#nsfw"
    )
    assert "<details>" in markdown
    assert "⚠️ #nsfw" in markdown


def test_cached_media_goes_inside_the_fold_too():
    """缓存路径的媒体同样要进折叠 —— 否则图露在外面等于没遮"""
    markdown, _ = build_cached_rich_content(
        _entry(), "https://x.com/a/status/1", lang="zh-hans", config=_config(), spoiler_tag="#nsfw"
    )
    inner = markdown.split("<details>", 1)[1]
    outside = markdown.replace(inner, "")
    assert "tg://photo?id=m0" in inner
    assert "tg://photo" not in outside


def test_cached_content_is_normal_without_the_flag():
    markdown, _ = build_cached_rich_content(
        _entry(), "https://x.com/a/status/1", lang="zh-hans", config=_config()
    )
    assert "<details>" not in markdown
    assert "正文" in markdown
