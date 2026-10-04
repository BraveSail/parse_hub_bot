"""bilibili 转发动态的渲染: 剥 //@ 注释、引用块、引用媒体计数。"""

from parsehub.parsers.parser.bilibili import BiliParse
from parsehub.provider_api.bilibili import BiliDynamic


def _dyn(**kw) -> BiliDynamic:
    d = BiliDynamic()
    for k, v in kw.items():
        setattr(d, k, v)
    return d


# ── 剥掉 //@ 注释 ──────────────────────────────────────────────────────

def test_strip_forward_comment_keeps_the_forwarder_text():
    content = "片头曲为X演唱的《Y》。 \n\u200b//@夏日幻听MCE:10月新番《脑洞学生会！》第1话 已更新！"
    assert BiliParse._strip_forward_comment(content) == "片头曲为X演唱的《Y》。"


def test_strip_forward_comment_without_comment():
    assert BiliParse._strip_forward_comment("就是一条普通动态") == "就是一条普通动态"


def test_strip_forward_comment_keeps_only_the_outermost():
    """多层转发时只留最外层转发者的话"""
    content = "外层评论 //@A:中间 //@B:最内层"
    assert BiliParse._strip_forward_comment(content) == "外层评论"


def test_strip_forward_comment_handles_leading_marker():
    """转发者没写评论时正文就是 //@... , 剥完应为空"""
    assert BiliParse._strip_forward_comment("//@某人:原文内容") == ""


def test_strip_forward_comment_empty():
    assert BiliParse._strip_forward_comment("") == ""


# ── 引用块渲染 ──────────────────────────────────────────────────────────

def test_render_forward_has_author_link_and_title():
    forward = _dyn(author_name="夏日幻听MCE", author_mid=224267770, title="「脑洞学生会！」第1话【中文字幕】")
    quote = BiliParse._render_forward(forward)
    assert quote.startswith("> <i>")
    assert '<a href="https://space.bilibili.com/224267770">夏日幻听MCE</a>' in quote
    assert "「脑洞学生会！」第1话【中文字幕】" in quote
    # 整块斜体
    assert all(line.startswith(">") for line in quote.split("\n") if line.strip())


def test_render_forward_merges_title_and_content():
    forward = _dyn(author_name="A", title="标题", content="简介文字")
    quote = BiliParse._render_forward(forward)
    assert "标题" in quote
    assert "简介文字" in quote


def test_render_forward_content_only():
    forward = _dyn(author_name="A", content="只有正文")
    quote = BiliParse._render_forward(forward)
    assert "只有正文" in quote


def test_render_forward_without_author():
    forward = _dyn(title="匿名分享")
    quote = BiliParse._render_forward(forward)
    assert "匿名分享" in quote


def test_render_forward_empty_is_blank():
    assert BiliParse._render_forward(_dyn()) == ""


# ── 媒体转换 ────────────────────────────────────────────────────────────

def test_to_refs_handles_live_photo():
    from parsehub.provider_api.bilibili import BiliImage
    from parsehub.types import ImageRef, LivePhotoRef

    refs = BiliParse._to_refs(
        [
            BiliImage(url="http://x/a.jpg", width=100, height=200),
            BiliImage(url="http://x/b.jpg", live_url="http://x/b.mp4", width=300, height=400),
        ]
    )
    assert isinstance(refs[0], ImageRef)
    assert isinstance(refs[1], LivePhotoRef)
    assert refs[1].video_url == "http://x/b.mp4"


def test_to_refs_empty():
    assert BiliParse._to_refs(None) == []
    assert BiliParse._to_refs([]) == []
