"""twitter 图片扩展名：按 URL 取**真实**扩展名，不能一律当 jpg。

背景（2026-10-08，用户报「▎媒体处理错误: cannot write mode P as JPEG」）：
`kon_kokine/status/2107756845704360201` 的配图是 **PNG 调色板图**，URL 明写
`.../HUBBoF9bkAA1jCq.png?name=orig`，但 `ImageRef.ext` 默认 `"jpg"` 而解析器没传 ext
⇒ 下载成 `001_ブーン.jpg`（内容其实是 PNG）⇒ 下游媒体处理**按后缀**存图时炸掉整条解析。

判据：ext 跟 URL 走（path 上的扩展名优先，path 没有时看 `?format=`），两者都没有才退回默认 jpg。
"""

from parsehub.parsers.parser.twitter import TwitterParser, _image_ext_from_url
from parsehub.provider_api.twitter import TwitterPhoto


def _refs(url: str):
    return TwitterParser.to_media_refs([TwitterPhoto(url=url, height=10, width=10)])


# ---------------------------------------------------------------- 真实场景


def test_the_reported_png_photo_is_named_png():
    """**核心回归**：报障那条推文的地址 —— 必须给出 `png`，不能是默认的 `jpg`。"""
    refs = _refs("https://pbs.twimg.com/media/HUBBoF9bkAA1jCq.png?name=orig")

    assert refs[0].ext == "png"


def test_other_formats_follow_the_url_too():
    for url, ext in (
        ("https://pbs.twimg.com/media/AAA.jpg?name=orig", "jpg"),
        ("https://pbs.twimg.com/media/AAA.jpeg?name=large", "jpeg"),
        ("https://pbs.twimg.com/media/AAA.webp?name=small", "webp"),
        ("https://pbs.twimg.com/media/AAA.png", "png"),
    ):
        assert _refs(url)[0].ext == ext, url


def test_format_query_is_used_when_the_path_has_no_extension():
    """X 的卡片图 path 没有扩展名，格式只在 query 上（`.../card_img/123/AbC?format=png`）。"""
    refs = _refs("https://pbs.twimg.com/card_img/2107314611443879936/KtNXXpoz?format=png&name=orig")

    assert refs[0].ext == "png"


# ---------------------------------------------------------------- 兜底


def test_trailing_query_and_unknown_extension_fall_back_to_jpg():
    """拿不到可信扩展名时保持旧行为（jpg），不能凭空改命名。"""
    for url in (
        "https://pbs.twimg.com/media/AAA",  # 完全无扩展名
        "https://pbs.twimg.com/media/AAA?name=orig",  # 只有 name
        "https://example.com/page.html",  # 不是图片扩展名
        "https://pbs.twimg.com/media/AAA.php?x=1",
    ):
        assert _refs(url)[0].ext == "jpg", url


def test_percent_encoded_path_is_decoded():
    """路径里有百分号编码时也要认出扩展名。"""
    assert _image_ext_from_url("https://example.com/a%20b/pic%20one.PNG") == "png"


def test_the_dot_must_belong_to_a_filename():
    """`/v1.2/pic` 这种把点放在目录上的路径不算扩展名。"""
    assert _image_ext_from_url("https://example.com/v1.2/pic") == "jpg"
