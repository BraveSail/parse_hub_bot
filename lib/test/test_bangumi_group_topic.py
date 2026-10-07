"""bgm.tv 讨论话题（``/group/topic/<id>`` 与 ``/subject/topic/<id>``）解析。

**只发一层**（用户要求，与 linux.do 的楼层处理同一形态）：

- 链接不带楼层锚点 → 发**主楼**
- 链接带 ``#post_<id>`` 锚点 → 发**那一层**，主楼作为引用块当上下文

（上一版把主楼 + 全部楼层铺出来 —— 45 层的帖子整篇塞满消息，用户要求改掉。）

**两个页面同构但头部不同**（实测）::

    group/topic   : 标题在 ``#pageHeader h1`` 的 ``<br/>`` 之后；归属是小组（/group/<slug>）
    subject/topic : 页面上**两个 h1** —— ``#headerSubject`` 里是条目名，
                    真正的标题在 ``.comment-header h1``；归属是条目（/subject/<id>）

**楼层结构两个页面完全一样**（主楼 ``.postTopic`` / 回复 ``.row_reply`` /
楼中楼 ``.sub_reply_bg``，正文容器 ``.topic_content`` / ``.reply_content`` / ``.cmt_sub_content``）。

fixture ``bangumi_group_topic_472394.html`` 是真实话题（抽过：h1 + 主楼 + 前 5 层）。
"""

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest

from parsehub.parsers.parser.bangumi import BangumiParser, BangumiParseResult
from parsehub.provider_api.bangumi import BGM_TIMEZONE, BangumiError, BangumiTopic

FIXTURES = Path(__file__).parent / "fixtures"
FIXTURE = FIXTURES / "bangumi_group_topic_472394.html"


def _topic(floor_id: str = "") -> BangumiTopic:
    return BangumiTopic._from_html(FIXTURE.read_text(encoding="utf-8"), "472394", floor_id=floor_id)


def _floor(
    pid: str,
    label: str,
    when: str,
    author: str,
    uid: str,
    body: str,
    *,
    cls: str = "light_even row row_reply clearit",
    body_cls: str = "reply_content",
    nested: str = "",
) -> str:
    head = (
        f'<div class="post_actions re_info"><div class="action"><small>'
        f'<a class="floor-anchor" href="#post_{pid}">{label}</a> - {when}'
        f"</small></div></div>"
    )
    return f"""<div class="{cls}" id="post_{pid}">
      {head}
      <a class="avatar" href="/user/{uid}"><span class="avatarNeue"></span></a>
      <div class="inner">
        <strong><a class="l" href="/user/{uid}">{author}</a></strong>
        <div class="{body_cls}">{body}{nested}</div>
      </div>
    </div>"""


def _main(body: str = "主楼正文") -> str:
    return _floor(
        "0", "#1", "2026-10-7 00:19", "楼主", "op", body,
        cls="postTopic light_odd clearit", body_cls="topic_content",
    )


def _group_header(title: str = "话题标题") -> str:
    return f"""<div id="pageHeader"><h1>
      <span><a href="/group/testgrp"><img class="avatar" src="//lain.bgm.tv/g.jpg"/>测试小组</a>
      » <a href="/group/testgrp/forum">讨论</a></span><br/>{title}</h1></div>"""


def _subject_header(subject: str = "無職転生Ⅲ", title: str = "条目话题标题") -> str:
    return f"""<div id="headerSubject"><h1 class="nameSingle">
        <a href="/subject/501963">{subject}</a></h1></div>
      <div class="comment-header"><h1>{title}</h1></div>"""


def _from_html(
    html: str,
    topic_id: str = "1",
    *,
    floor_id: str = "",
    main: str | None = None,
    header: str | None = None,
) -> BangumiTopic:
    """``html`` 是 **comment_list 的内容**；主楼按真实结构放在它**外面**。"""
    page = f"""<html><body><div id="viewEntry">
      {header if header is not None else _group_header()}
      {main if main is not None else _main()}
      <div id="comment_list" class="commentList">{html}</div>
    </div></body></html>"""
    return BangumiTopic._from_html(page, topic_id, floor_id=floor_id)


# ---------------------------------------------------------------- 只发一层

def test_the_opening_post_is_sent_when_there_is_no_anchor():
    """**核心**: 不带锚点 → 只发**主楼**，**不铺楼层**"""
    topic = _topic()
    assert topic.floor_label == "#1"
    assert topic.markdown_content.count("最近视奸") == 1
    # 楼层内容一律不出现（这是本版的核心改动）
    for floor in topic.floors:
        if floor.markdown:
            assert floor.markdown.splitlines()[0] not in topic.markdown_content


def test_the_opening_post_has_no_context_quote():
    """主楼不给自己做引用块"""
    assert not [ln for ln in _topic().markdown_content.splitlines() if ln.startswith("> ")]


def test_an_anchored_floor_is_what_gets_sent():
    """**核心**: 带 ``#post_<id>`` 锚点 → 发**那一层**"""
    topic = _topic("4062141")
    assert topic.floor_label == "#2"
    assert topic.author_name == "大夜宵"
    assert "两年四百+少了" in topic.markdown_content


def test_an_anchored_floor_brings_the_opening_post_as_a_quote():
    """**核心**: 分享楼层时把**主楼**做成引用块（与 linux.do 同一形态）"""
    quoted = [ln for ln in _topic("4062141").markdown_content.splitlines() if ln.startswith("> ")]
    assert quoted, "缺主楼引用块"
    assert any("最近视奸" in ln for ln in quoted), "引用块里不是主楼正文"
    assert any("irohard" in ln for ln in quoted), "引用块里没有主楼作者"
    assert any("#1" in ln for ln in quoted), "引用块里的主楼没有楼层号"


def test_the_quote_carries_no_emphasis_markup_at_all():
    """引用块**不用斜体**（用户: 「引用/回复 不是斜体」），也不用 markdown 星号

    （引用块内、尤其**嵌套**引用里的 markdown 星号会字面显示 —— 实测读到过孤立的 ``"**"``）
    """
    quoted = [ln for ln in _topic("4062141").markdown_content.splitlines() if ln.startswith("> ")]
    line = next(ln for ln in quoted if "irohard" in ln)
    assert "<i>" not in line and "**" not in line, line
    assert all("<i>" not in ln and "**" not in ln for ln in quoted), quoted


def test_an_anchored_sub_reply_can_be_selected_too():
    """锚点指向**楼中楼**（它也有 ``post_<id>``）"""
    topic = _topic("4062147")
    assert topic.floor_label == "#2-1"
    assert topic.author_name == "irohard"
    assert "经常看到你在新番里评论" in topic.markdown_content


def test_a_bad_anchor_falls_back_to_the_opening_post():
    """锚点在页面上不存在（楼层被删 / 链接被手改）→ 退回主楼，**不是**解析失败"""
    topic = _topic("99999999")
    assert topic.floor_label == "#1"
    assert "最近视奸" in topic.markdown_content


def test_the_quote_media_count_covers_the_opening_posts_images():
    """主楼的图走 ``quoted_media_count`` 通道（bot 侧放进引用块内部）"""
    main = _main('主楼正文<img class="code" src="//lain.bgm.tv/pic/photo/l/op.jpg"/>')
    reply = _floor(
        "6", "#2", "2026-10-7 00:24", "甲", "a",
        '<img class="code" src="//lain.bgm.tv/pic/photo/l/r.jpg"/>',
    )
    topic = _from_html(reply, floor_id="6", main=main)
    assert [i.url for i in topic.images] == [
        "https://lain.bgm.tv/pic/photo/l/r.jpg",
        "https://lain.bgm.tv/pic/photo/l/op.jpg",
    ]
    assert topic.quoted_media_count == 1, "只有主楼那张属于引用块"

    # 分享主楼时没有引用块 —— 计数必须是 0（否则图会被切进不存在的卡片）
    assert _from_html(reply, main=main).quoted_media_count == 0


# ---------------------------------------------------------------- 两个页面形态

def test_the_group_header_gives_the_group_and_the_title():
    topic = _topic()
    assert topic.title == "真的有人能看过3000+部番吗"
    assert topic.context_name == "补旧番"
    assert topic.context_url == "https://bgm.tv/group/fillgrids"


def test_the_subject_header_gives_the_subject_and_the_second_h1():
    """**核心**: 条目讨论版的标题是页面**第二个** ``h1`` —— 第一个是条目名。

    直接取 ``h1`` 会把条目名当话题标题（实测就是这个坑）。
    """
    topic = _from_html("", header=_subject_header("無職転生Ⅲ", "我说涩谷亮介身边缺一个平野宏树有没有懂的"))
    assert topic.title == "我说涩谷亮介身边缺一个平野宏树有没有懂的"
    assert topic.context_name == "無職転生Ⅲ"
    assert topic.context_url == "https://bgm.tv/subject/501963"


def test_the_floor_time_is_beijing_time_too():
    """楼层时间与日志同一个坑：页面给 ``2026-10-7 00:24``（无偏移）→ 按 +08:00 解释

    按 UTC 解释的话渲染出来晚 8 小时（用户报「时间好像有问题？多 8 小时」）。
    """
    topic = _topic("4062141")
    assert topic.published_at is not None
    assert topic.published_at.utcoffset() == BGM_TIMEZONE.utcoffset(None)
    # 页面上写的是 10-7 00:24（北京）→ UTC 是前一日的 16:24
    assert topic.published_at.astimezone(UTC) == datetime(2026, 10, 6, 16, 24, tzinfo=UTC)


def test_a_floor_without_a_time_parses_to_none():
    main = _floor(
        "0", "#1", "", "楼主", "op", "没有任何时间", cls="postTopic light_odd clearit", body_cls="topic_content"
    )
    topic = _from_html("", main=main)
    assert topic.published_at is None


def test_the_context_line_carries_the_affiliation_only():
    """归属行只写「谁家的讨论」（层数不再需要 —— 只发一层）"""
    first = _topic().markdown_content.splitlines()[0]
    assert "补旧番" in first and "» 讨论" in first
    assert "层" not in first


# ---------------------------------------------------------------- 楼层解析（内部）

def test_floors_are_parsed_with_dom_ids():
    """``dom_id`` 就是节点的 ``post_<数字>`` 后那串 —— URL 锚点按它定位"""
    by_label = {f.label: f for f in _topic().floors}
    assert by_label["#2"].dom_id == "4062141"
    assert by_label["#2-1"].dom_id == "4062147"


def test_a_sub_reply_body_does_not_contain_the_parent_text():
    """**核心**: 楼中楼摘出来当独立条目 —— 父楼正文里不能混进它的文字。

    ⚠️ 摘用的是 ``decompose()``，它会**清空被摘节点的内容** ⇒ 必须**先收集节点、再逆序解析**
    （正序时父楼先把子楼清掉，轮到子楼就只剩空壳：实测 45 个节点只解析出 38 条）。
    """
    floors = {f.label: f for f in _topic().floors}
    parent, sub = floors["#2"], floors["#2-1"]
    assert "两年四百+少了" in parent.markdown
    assert "经常看到你在新番里评论" not in parent.markdown
    assert "经常看到你在新番里评论" in sub.markdown
    assert "两年四百+少了" not in sub.markdown


def test_sub_replies_are_marked():
    subs = [f for f in _topic().floors if f.is_sub]
    assert [f.label for f in subs] == ["#2-1", "#2-2", "#2-3", "#6-1"]


# ---------------------------------------------------------------- 图片与表情

def test_uploaded_images_are_collected_from_any_floor():
    """图片从所有楼层抽（目标层可能在后面，图得先收全）"""
    nested = _floor(
        "7", "#2-1", "2026-10-7 00:28", "甲", "a",
        '<img class="code" src="//lain.bgm.tv/pic/photo/l/deep.jpg"/>',
        cls="sub_reply_bg clearit", body_cls="cmt_sub_content",
    )
    topic = _from_html(
        _floor("6", "#2", "2026-10-7 00:24", "甲", "a",
               '<img class="code" src="//lain.bgm.tv/pic/photo/l/a.jpg"/>', nested=nested)
    )
    urls = [i.url for i in topic.images]
    assert "https://lain.bgm.tv/pic/photo/l/a.jpg" in urls
    assert "https://lain.bgm.tv/pic/photo/l/deep.jpg" in urls, "楼中楼里的图也要收（树上要先抽）"


def test_smiles_stay_text_and_do_not_become_media():
    topic = _from_html(
        _floor("6", "#2", "2026-10-7 00:24", "甲", "a",
               '好笑<img class="smile" alt="(bgm38)" src="/img/smiles/tv/15.gif"/>'),
        floor_id="6",
    )
    assert topic.images == []
    assert "(bgm38)" in topic.markdown_content


def test_the_body_keeps_no_image_tag():
    topic = _from_html(
        _floor("6", "#2", "2026-10-7 00:24", "甲", "a", '<img class="code" src="//lain.bgm.tv/pic/photo/l/a.jpg"/>'),
        floor_id="6",
    )
    assert "lain.bgm.tv/pic/photo" not in topic.markdown_content


# ---------------------------------------------------------------- 边界

def test_a_missing_topic_raises_with_the_sites_wording():
    with pytest.raises(BangumiError) as exc:
        BangumiTopic._from_html("<html><body>呜咕，出错了 数据库中没有查询到该小组话题的信息</body></html>", "9")
    assert "不存在" in str(exc.value)


def test_an_unknown_shape_raises_a_different_message():
    with pytest.raises(BangumiError) as exc:
        BangumiTopic._from_html("<html><body>什么都没有</body></html>", "1")
    assert "改版" in str(exc.value) or "结构" in str(exc.value)


def test_the_ids_come_out_of_the_url():
    assert BangumiTopic.get_id_by_url("https://bgm.tv/group/topic/472394") == "472394"
    assert BangumiTopic.get_id_by_url("https://bgm.tv/subject/topic/41209") == "41209"
    assert BangumiTopic.get_id_by_url("https://bgm.tv/blog/381120") == ""
    assert BangumiTopic.get_floor_id_by_url("https://bgm.tv/group/topic/472394#post_4062141") == "4062141"
    assert BangumiTopic.get_floor_id_by_url("https://bgm.tv/group/topic/472394") == ""


# ---------------------------------------------------------------- parser

def test_the_parser_matches_both_topic_paths():
    assert BangumiParser.match("https://bgm.tv/group/topic/472394")
    assert BangumiParser.match("https://bgm.tv/subject/topic/41209")
    assert BangumiParser.match("https://bangumi.tv/group/topic/472394")


def test_the_parser_still_matches_blogs_and_refuses_other_paths():
    """**回归**: 加话题不能把日志弄丢；条目页本身与小组首页不该被接走"""
    assert BangumiParser.match("https://bgm.tv/blog/381120")
    assert not BangumiParser.match("https://bgm.tv/subject/400602")
    assert not BangumiParser.match("https://bgm.tv/group/fillgrids")
    assert not BangumiParser.match("https://bgm.tv/group/fillgrids/forum")


def test_the_result_carries_the_floor_label_and_the_quote_count():
    """``position_label`` = 本层楼层号；引用块里的作者行不带 markdown 星号"""

    class _Resp:
        status_code = 200

        def __init__(self, text: str):
            self.content = text.encode("utf-8")

        def raise_for_status(self) -> None:
            pass

    class _Client:
        def __call__(self, **_kwargs):
            return self

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return False

        async def get(self, _url: str, **_kwargs):
            return _Resp(FIXTURE.read_text(encoding="utf-8"))

    from parsehub.provider_api import bangumi as mod

    original = mod.http.AsyncClient
    mod.http.AsyncClient = _Client()
    try:
        result = asyncio.run(BangumiParser()._do_parse("https://bgm.tv/group/topic/472394#post_4062141"))
    finally:
        mod.http.AsyncClient = original

    assert result.position_label == "#2"
    assert result.author_handle == "dayexiao520"
    assert result.author_url == "https://bgm.tv/user/dayexiao520"
    assert BangumiParseResult.requires_media_download is True
    quoted = [ln for ln in result.markdown_content.splitlines() if ln.startswith("> ")]
    assert quoted and all("**" not in ln for ln in quoted)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
