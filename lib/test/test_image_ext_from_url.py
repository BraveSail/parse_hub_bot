"""`image_ext_from_url`：图片下载命名要跟 URL 上的真实扩展名。

背景（2026-10-08，用户报「▎媒体处理错误: cannot write mode P as JPEG」，推文
`kon_kokine/status/2107756845704360201`）：`ImageRef.ext` 默认 `"jpg"`，各平台 parser 构造时
普遍没传 ext ⇒ 图片实为 PNG/WebP 也被命名成 `.jpg` ⇒ 下载文件名与内容不符，
下游按后缀处理媒体时炸掉整条解析。

判据：ext 跟 URL 走（path 扩展名优先 → query `format=` 兜底 → 都没有才退默认）。

**这是纯增量改动**：URL 给不出可信扩展名时必须原样返回默认值，绝不能凭空改写命名。
"""

import pytest

from parsehub.utils.helpers import ANIMATED_EXTS, image_ext_from_url

# ------------------------------------------------------------ 真实平台样本

#: 取自 `lib/test/fixtures/` 里各平台**真实响应**的图片 URL（扫过 266 条，全部在 path 带扩展名）
REAL_URLS = [
    # threads —— 这条正是"会撒谎"的例子：webp 却被命名为 jpg
    (
        "https://scontent-nrt1-1.cdninstagram.com/v/t51.82787-15/"
        "834098365_18143584570642560_880307419716513217_n.webp?_nc_cat=10",
        "webp",
    ),
    (
        "https://scontent-nrt1-1.cdninstagram.com/v/t51.82787-15/"
        "715304374_17992254998969625_712221579133782869_n.jpg?stp=c47.0.6",
        "jpg",
    ),
    # bilibili
    ("http://i0.hdslb.com/bfs/new_dyn/0c6e62ed166e34af97a1c46e536ee00f489004577.jpg", "jpg"),
    ("http://i0.hdslb.com/bfs/vip/10df1f7f4af1292f773489e36554a05177785143.png", "png"),
    # douyin
    ("https://sf1-ttcdn-tos.pstatp.com/obj/ttfe/hot_lists/feed_hot_search_icon.png", "png"),
    # facebook
    (
        "https://scontent-icn2-1.xx.fbcdn.net/v/t1.6435-1/"
        "44073017_10213692485609996_943335011891806208_n.jpg?stp=cp0_dst-jpg_tt6",
        "jpg",
    ),
    # instagram
    (
        "https://scontent-nrt1-1.cdninstagram.com/v/t51.2885-19/"
        "269621338_610445123508729_113414489171777160_n.jpg?efg=eyJ2ZW5jb2",
        "jpg",
    ),
    # linuxdo
    ("https://cdn.ldstatic.com/images/emoji/twemoji/distorted_face.png?v=15", "png"),
    # twitter（报障那条）
    ("https://pbs.twimg.com/media/HUBBoF9bkAA1jCq.png?name=orig", "png"),
]


@pytest.mark.parametrize(("url", "expected"), REAL_URLS)
def test_real_platform_urls_give_the_right_ext(url, expected):
    assert image_ext_from_url(url) == expected


def test_the_reported_case_is_png_not_the_default_jpg():
    """**核心回归**：报障那条推文 —— 必须是 `png`，不能是默认的 `jpg`。"""
    assert image_ext_from_url("https://pbs.twimg.com/media/HUBBoF9bkAA1jCq.png?name=orig") == "png"


# ------------------------------------------------------------ query 兜底


def test_format_query_is_used_when_the_path_has_no_extension():
    """X 的卡片图 path 无扩展名，格式只在 query 上。"""
    card = "https://pbs.twimg.com/card_img/2107314611443879936/KtNXXpoz?format=png&name=orig"

    assert image_ext_from_url(card) == "png"


def test_path_wins_over_query():
    """两者都在时以 path 为准（path 是实际文件名，query 常是缩放/转码参数）。"""
    assert image_ext_from_url("https://example.com/a.png?format=jpg") == "png"


# ------------------------------------------------------------ 纯增量（不能凭空改写）


@pytest.mark.parametrize(
    "url",
    [
        "https://pbs.twimg.com/media/AAA",  # 完全无扩展名
        "https://pbs.twimg.com/media/AAA?name=orig",  # 只有 name
        "https://example.com/page.html",  # 不是图片扩展名
        "https://pbs.twimg.com/media/AAA.php?x=1",
        "https://example.com/v1.2/pic",  # 点在目录上，不是扩展名
        "",
    ],
)
def test_untrustworthy_urls_keep_the_default(url):
    assert image_ext_from_url(url) == "jpg"


def test_default_is_overridable_for_animated_media():
    """动图（``AniRef``）默认值是 gif；URL 给不出扩展名时要保住这个默认。"""
    assert image_ext_from_url("https://example.com/anim", default="gif") == "gif"
    assert image_ext_from_url("https://example.com/anim.gif", default="gif") == "gif"


def test_animated_media_accepts_video_containers():
    """动图常是 mp4（twitter 就是）—— 但**只有传了 ``ANIMATED_EXTS`` 才算数**，
    普通图片调用不能把 mp4 当图片扩展名（那样 ``ImageRef`` 会拿到视频后缀）。"""
    assert image_ext_from_url("https://example.com/anim.mp4", default="gif", exts=ANIMATED_EXTS) == "mp4"
    assert image_ext_from_url("https://example.com/anim.webm", default="gif", exts=ANIMATED_EXTS) == "webm"
    # 默认集合下 mp4 不可信 ⇒ 退回 default（明确钉住这个边界）
    assert image_ext_from_url("https://example.com/anim.mp4", default="gif") == "gif"


# ------------------------------------------------------------ 边角


def test_percent_encoded_path_is_decoded():
    assert image_ext_from_url("https://example.com/a%20b/pic%20one.PNG") == "png"


def test_uppercase_extension_is_lowercased():
    assert image_ext_from_url("https://example.com/PHOTO.JPEG") == "jpeg"


def test_a_dot_in_the_directory_is_not_an_extension():
    assert image_ext_from_url("https://example.com/a.b/c") == "jpg"


# ------------------------------------------------- 各平台接入：ext 真的传到 Ref 上了

#: 这些平台的"媒体 -> 下载 Ref"入口是可直接调用的静态方法，所以行为能离线钉住；
#: 其余平台（构造发生在 `_do_parse` 里）由真机验证覆盖，见
#: `doc/2026-10-08-unify-image-ext.md`。


def test_bilibili_refs_carry_the_real_ext():
    """B 站图片 URL 明写 ``.png`` 时不能叫 jpg（实况照片同理）。"""
    from parsehub.parsers.parser.bilibili import BiliParse
    from parsehub.provider_api.bilibili import BiliImage

    refs = BiliParse._to_refs(
        [
            BiliImage(url="http://i0.hdslb.com/bfs/vip/10df1f7f4af1292f773489e36554a05177785143.png"),
            BiliImage(url="http://i0.hdslb.com/bfs/new_dyn/0c6e62ed166e34af97a1c46e536ee00f489004577.jpg"),
            BiliImage(url="http://i0.hdslb.com/bfs/x/y.PNG", live_url="https://example.com/live.mp4"),
        ]
    )

    assert [r.ext for r in refs] == ["png", "jpg", "png"]


def test_bangumi_refs_carry_the_real_ext():
    from parsehub.parsers.parser.bangumi import _refs
    from parsehub.provider_api.bangumi import BangumiImage

    refs = _refs(
        [
            BangumiImage(url="https://lain.bgm.tv/pic/photo/l/51/aa_1.png"),
            BangumiImage(url="https://lain.bgm.tv/pic/photo/l/51/bb_1.webp"),
        ]
    )

    assert [r.ext for r in refs] == ["png", "webp"]


def test_twitter_card_photo_carries_the_real_ext():
    """卡片图（外链预览）也走同一条路。"""
    from parsehub.provider_api.twitter import TwitterPhoto

    refs = _refs_for_twitter(
        [
            TwitterPhoto(
                url="https://pbs.twimg.com/card_img/2107314611443879936/KtNXXpoz?format=png&name=orig",
                height=1,
                width=1,
            )
        ]
    )

    assert refs[0].ext == "png"


def _refs_for_twitter(media):
    from parsehub.parsers.parser.twitter import TwitterParser

    return TwitterParser.to_media_refs(media)

