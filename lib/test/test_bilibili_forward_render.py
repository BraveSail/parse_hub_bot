"""bilibili 转发动态的渲染: 剥 //@ 注释、引用块、引用媒体计数。"""

from parsehub.parsers.parser.bilibili import BiliParse
from parsehub.provider_api.bilibili import BiliDynamic


def _dyn(**kw) -> BiliDynamic:
    d = BiliDynamic()
    for k, v in kw.items():
        setattr(d, k, v)
    return d


# ── 剥掉 //@ 注释 ──────────────────────────────────────────────────────

def test_split_forward_comment_returns_both_halves():
    """拆成 (转发者的话, //@ 段原文) —— **原文不能丢**。

    原文是被转发动态的正文, 而引用块里来自 orig 的是视频标题/简介, 两者不是一回事
    (用户报「为什么丢了 //@夏日幻听MCE:10月新番《脑洞学生会！》第1话 已更新！」)。
    """
    content = "片头曲为X演唱的《Y》。 \n\u200b//@夏日幻听MCE:10月新番《脑洞学生会！》第1话 已更新！"
    comment, original = BiliParse._split_forward_comment(content)
    assert comment == "片头曲为X演唱的《Y》。"
    assert original == "10月新番《脑洞学生会！》第1话 已更新！"      # 作者名前缀已剥掉
    assert "@" not in original


def test_split_forward_comment_without_comment():
    assert BiliParse._split_forward_comment("就是一条普通动态") == ("就是一条普通动态", "")


def test_split_forward_comment_keeps_only_the_outermost():
    """多层转发时只取最外层"""
    comment, original = BiliParse._split_forward_comment("外层评论 //@A:中间 //@B:最内层")
    assert comment == "外层评论"
    assert original == "中间 //@B:最内层"


def test_split_forward_comment_handles_leading_marker():
    """转发者没写评论时, 评论为空但原文仍在"""
    assert BiliParse._split_forward_comment("//@某人:原文内容") == ("", "原文内容")


def test_split_forward_comment_empty():
    assert BiliParse._split_forward_comment("") == ("", "")


# ── 引用块渲染 ──────────────────────────────────────────────────────────

def test_render_forward_has_author_link_and_title():
    forward = _dyn(author_name="夏日幻听MCE", author_mid=224267770, title="「脑洞学生会！」第1话【中文字幕】")
    quote = BiliParse._render_forward(forward)
    assert quote.startswith("> ")
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


def test_render_forward_links_the_video_title():
    """被转发的是视频时, 标题要链到视频页 —— 引用块里只有封面, 没有视频本身"""
    forward = _dyn(
        author_name="夏日幻听MCE",
        author_mid=224267770,
        title="「脑洞学生会！」第1话【中文字幕】",
        content="「脑洞学生会！」第1话",
        bvid="BV1UqHi6uEie",
    )
    quote = BiliParse._render_forward(forward)
    assert '<a href="https://www.bilibili.com/video/BV1UqHi6uEie">「脑洞学生会！」第1话【中文字幕】</a>' in quote
    # 简介跟在后一行, 不带链接
    assert "> 「脑洞学生会！」第1话" in quote
    assert '<a href="https://www.bilibili.com/video/BV1UqHi6uEie">「脑洞学生会！」第1话</a>' not in quote


def test_render_forward_includes_the_original_text():
    """//@ 段的原文要进引用块 —— orig 是视频时它不在 title/简介里, 丢了就是丢内容"""
    forward = _dyn(
        author_name="夏日幻听MCE",
        author_mid=224267770,
        title="「脑洞学生会！」第1话【中文字幕】",
        content="「脑洞学生会！」第1话",
        bvid="BV1UqHi6uEie",
    )
    quote = BiliParse._render_forward(forward, extra_text="10月新番《脑洞学生会！》第1话 已更新！")
    assert "10月新番《脑洞学生会！》第1话 已更新！" in quote
    # 原文在视频信息之前
    assert quote.index("10月新番") < quote.index("【中文字幕】")


def test_render_forward_skips_duplicate_original_text():
    """原文已包含在引用块内容里时不要重复渲染"""
    forward = _dyn(author_name="A", content="转发了一条视频")
    quote = BiliParse._render_forward(forward, extra_text="转发了一条视频")
    assert quote.count("转发了一条视频") == 1


def test_render_forward_original_text_survives_normalisation_differences():
    """归一化后相同 (空白/标点/话题符号差异) 也算重复"""
    forward = _dyn(author_name="A", content="#脑洞学生会# 第1话已更新")
    quote = BiliParse._render_forward(forward, extra_text="脑洞学生会 第1话已更新")
    assert quote.count("第1话已更新") == 1


def test_render_forward_without_bvid_keeps_the_title_plain():
    """不是视频 (没有 BV 号) 时标题保持纯文本"""
    quote = BiliParse._render_forward(_dyn(author_name="A", title="一条图文动态"))
    assert "一条图文动态" in quote
    assert "bilibili.com/video" not in quote


def test_render_forward_without_author():
    forward = _dyn(title="匿名分享")
    quote = BiliParse._render_forward(forward)
    assert "匿名分享" in quote


def test_render_forward_empty_is_blank():
    assert BiliParse._render_forward(_dyn()) == ""


# ── 话题 (#xxx#) 渲染成搜索链接 ───────────────────────────────────────────

def test_hashtag_becomes_a_search_link():
    """#话题# 要链到 B 站搜索页 (用户明确要求)"""
    out = BiliParse.hashtag_handler("片头曲为#三月的Phantasia#演唱的")
    assert (
        '<a href="https://search.bilibili.com/all?keyword=%E4%B8%89%E6%9C%88%E7%9A%84Phantasia">'
        "#三月的Phantasia#</a>"
    ) in out
    assert "片头曲为" in out and "演唱的" in out


def test_hashtag_does_not_add_stray_spaces():
    """不额外补空格 —— 旧实现会变成「《 #话题 》」, 空格留在标点里很难看"""
    out = BiliParse.hashtag_handler("10月新番《#脑洞学生会！#》第1话")
    assert "《<a" in out
    assert "</a>》第1话" in out
    assert " #" not in out and "# " not in out


def test_hashtag_at_line_start_is_not_a_heading():
    """行首话题要包在 <a> 里 —— 裸 # 会被 markdown 当成标题"""
    out = BiliParse.hashtag_handler("#行首话题# 正文")
    assert out.startswith("<a href=")


def test_plain_text_without_hashtag_is_unchanged():
    assert BiliParse.hashtag_handler("无话题的普通正文") == "无话题的普通正文"
    assert BiliParse.hashtag_handler("") == ""


def test_hashtag_keeps_surrounding_spaces():
    assert BiliParse.hashtag_handler("A #话题# B") == (
        'A <a href="https://search.bilibili.com/all?keyword=%E8%AF%9D%E9%A2%98">#话题#</a> B'
    )


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
