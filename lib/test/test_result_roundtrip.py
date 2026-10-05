"""结果对象的缓存往返: 必须**无损**, 尤其是媒体类型与派生字段。

为什么要专门测: 缓存命中时对象是从 JSON 重建的, 一旦重建出的对象与原来行为不同,
表现是"第二次发同一条链接效果变了"(媒体类型错、正文丢失), 而这**不会报错**。
"""

import json
import unittest
from datetime import datetime, timedelta, timezone

from parsehub.types.media_ref import AniRef, ImageRef, LivePhotoRef, VideoRef
from parsehub.types.platform import Platform
from parsehub.types.result import (
    ImageParseResult,
    MultimediaParseResult,
    RichTextParseResult,
    VideoParseResult,
)
from parsehub.types.serialize import result_from_cache_dict, result_to_cache_dict

PUBLISHED = datetime(2026, 10, 1, 19, 48, tzinfo=timezone(timedelta(hours=8)))


def _roundtrip(result):
    """过一次 JSON (缓存里存的就是字符串) —— 不经过 JSON 就测不出真实往返。"""
    return result_from_cache_dict(json.loads(json.dumps(result_to_cache_dict(result))))


class RoundtripTest(unittest.TestCase):
    def test_video_result_keeps_every_field(self):
        result = VideoParseResult(
            title="标题",
            video=VideoRef(url="https://cdn.example/v.mp4", thumb_url="https://cdn.example/t.jpg", duration=93),
            content="正文内容",
            author_name="作者",
            author_handle="handle",
            author_url="https://x.com/handle",
            is_sensitive=True,
            published_at=PUBLISHED,
            view_count=138460,
            like_count=21380,
            tags=["AI画像", "足裏"],
            quoted_media_count=1,
            reply_media_count=2,
        )
        result.raw_url = "https://x.com/handle/status/1"
        result.platform = Platform.TWITTER

        back = _roundtrip(result)

        self.assertIsInstance(back, VideoParseResult)
        self.assertEqual(back.title, result.title)
        self.assertEqual(back.content, result.content)
        self.assertEqual(back.author_name, result.author_name)
        self.assertEqual(back.author_handle, result.author_handle)
        self.assertEqual(back.author_url, result.author_url)
        self.assertIs(back.is_sensitive, True)
        self.assertEqual(back.published_at, PUBLISHED)
        self.assertEqual(back.view_count, 138460)
        self.assertEqual(back.like_count, 21380)
        self.assertEqual(back.tags, ["AI画像", "足裏"])
        self.assertEqual(back.quoted_media_count, 1)
        self.assertEqual(back.reply_media_count, 2)
        # raw_url 不在构造参数里 —— 忘了赋回就会丢
        self.assertEqual(back.raw_url, "https://x.com/handle/status/1")
        self.assertEqual(back.platform, Platform.TWITTER)
        # 视频是唯一可能是**单个** ref 的类型: 形态要保持
        self.assertIsInstance(back.media, VideoRef)
        self.assertEqual(back.media.url, "https://cdn.example/v.mp4")
        self.assertEqual(back.media.duration, 93)

    def test_media_types_survive_the_roundtrip(self):
        """**核心**: 动图/实况照片不能被还原成视频或图片。

        ``MediaRef`` 没有类型判别字段, 靠字段集反推区分不了 VideoRef/AniRef ——
        所以序列化必须显式带 kind。
        """
        refs = {
            "video": VideoRef(url="https://cdn.example/v.mp4", duration=10),
            "image": ImageRef(url="https://cdn.example/i.jpg", width=100),
            "animation": AniRef(url="https://cdn.example/a.gif", duration=3),
            "live_photo": LivePhotoRef(url="https://cdn.example/l.jpg", video_url="https://cdn.example/l.mp4"),
        }
        result = MultimediaParseResult(title="t", media=list(refs.values()), content="c")

        back = _roundtrip(result)

        self.assertEqual(len(back.media), 4)
        for expected, actual in zip(refs.values(), back.media, strict=True):
            self.assertIs(type(actual), type(expected), f"{type(expected).__name__} 变了: {type(actual).__name__}")
            self.assertEqual(actual.url, expected.url)

    def test_the_kind_tag_is_what_makes_types_survivable(self):
        """自证: 把 kind 抹掉后必须**响亮地失败**, 而不是猜一个类型出来。"""
        data = result_to_cache_dict(MultimediaParseResult(title="t", media=[AniRef(url="https://c/a.gif")]))
        for item in data["media"]:
            item.pop("kind")
        with self.assertRaises(ValueError):
            result_from_cache_dict(data)

    def test_image_result_keeps_the_list_shape(self):
        result = ImageParseResult(
            title="图集",
            photo=[ImageRef(url="https://cdn.example/1.jpg"), ImageRef(url="https://cdn.example/2.jpg", ext="png")],
            content="正文",
        )
        back = _roundtrip(result)

        self.assertIsInstance(back, ImageParseResult)
        self.assertIsInstance(back.media, list)
        self.assertEqual([m.url for m in back.media], ["https://cdn.example/1.jpg", "https://cdn.example/2.jpg"])
        self.assertEqual([m.ext for m in back.media], ["jpg", "png"])

    def test_richtext_keeps_markdown_and_derives_content(self):
        """``content`` 是派生属性 —— 重建时只能喂 ``markdown_content``。

        如果重建时把原来的 content 也塞进去, ``__init__`` 会用 markdown 覆盖它
        (不报错), 于是"缓存命中的文章正文"和首次解析的不一样。
        """
        markdown = "## 标题\n\n正文一段\n\n![图](https://cdn.example/a.jpg)"
        result = RichTextParseResult(
            title="文章",
            media=[ImageRef(url="https://cdn.example/a.jpg")],
            markdown_content=markdown,
        )
        result.raw_url = "https://linux.do/t/1"

        back = _roundtrip(result)

        self.assertIsInstance(back, RichTextParseResult)
        self.assertEqual(back.markdown_content, markdown)
        # content 由 markdown 派生, 必须与"现场解析"的那份一致
        self.assertEqual(back.content, RichTextParseResult(markdown_content=markdown).content)
        self.assertEqual(back.raw_url, "https://linux.do/t/1")
        self.assertIsInstance(back.media[0], ImageRef)

    def test_no_media_roundtrips_as_none(self):
        back = _roundtrip(VideoParseResult(title="无媒体", content="c"))
        self.assertIsNone(back.media)

    def test_string_media_is_refused_rather_than_guessed(self):
        """不是媒体对象也不是序列时**不猜** (猜错就是静默发错媒体)"""
        result = RichTextParseResult(title="t", markdown_content="m")
        result.media = "https://cdn.example/x.jpg"  # 故意塞错
        with self.assertRaises(TypeError):
            result_to_cache_dict(result)

    def test_an_unknown_type_is_refused(self):
        with self.assertRaises(ValueError):
            result_from_cache_dict({"type": "brand_new_type"})

    def test_an_unknown_platform_degrades_to_none(self):
        """平台枚举变了 (老缓存) 不该抛异常 —— 退化成拿不到来源即可"""
        data = result_to_cache_dict(VideoParseResult(title="t", content="c"))
        data["platform"] = "platform_that_was_removed"
        self.assertIsNone(result_from_cache_dict(data).platform)

    def test_the_dict_is_json_serializable(self):
        """缓存里存的是字符串 —— 不可 JSON 化就等于写不进去"""
        result = MultimediaParseResult(
            title="t",
            media=[VideoRef(url="https://c/v.mp4", duration=1), AniRef(url="https://c/a.gif")],
            content="c",
            published_at=PUBLISHED,
        )
        json.dumps(result_to_cache_dict(result))

    def test_post_type_is_preserved(self):
        for result in (
            VideoParseResult(title="t"),
            ImageParseResult(title="t"),
            MultimediaParseResult(title="t"),
            RichTextParseResult(title="t"),
        ):
            with self.subTest(type=result.type):
                back = _roundtrip(result)
                self.assertEqual(back.type, result.type)
                self.assertIs(type(back), type(result))


if __name__ == "__main__":
    unittest.main()
