"""bgm 话题的页脚统计：emoji 状态数 + 回复数。

数据来自页面**内联的脚本**（不用额外请求）::

    var data_likes_list = {"<post_id>": {"<value>": {"total": 6, "emoji": "101",
                                                      "users": [...]}}};

- 每个 emoji 的 ``total`` = 贴这个表情的**人数**（实测与 ``users`` 数组长度逐一对齐）
- ``likes_grid_<post_id>`` 是**空容器**（JS 填充），数据只能从脚本取
- **状态数 = 所有楼层该值之和**；**回复数 = 楼层总数 − 1**（主楼不算回复）

fixture ``bangumi_subject_topic_41209.html`` 是真实页面抽的最小集（主楼 + 一层 + 那段脚本）。
"""

from pathlib import Path

import pytest

from parsehub.provider_api.bangumi import BangumiTopic

FIXTURES = Path(__file__).parent / "fixtures"
FIXTURE = FIXTURES / "bangumi_subject_topic_41209.html"

#: 真实页面上的两个状态：主楼 6 人、#2 层 2 人
REAL_STATE_TOTAL = 8


def _topic(floor_id: str = "") -> BangumiTopic:
    return BangumiTopic._from_html(FIXTURE.read_text(encoding="utf-8"), "41209", floor_id=floor_id)


# ── 状态数 ─────────────────────────────────────────────────────────────

def test_the_state_count_sums_every_floor():
    """**核心**: 状态数是**所有楼层** emoji 的 total 之和（主楼 6 + #2 层 2 = 8）"""
    assert _topic().state_count == REAL_STATE_TOTAL


def test_the_state_count_is_the_same_whichever_floor_is_sent():
    """整帖统计 —— 分享哪一层都一样（页脚显示的是话题的规模，不是这一层的）"""
    assert _topic("411451").state_count == REAL_STATE_TOTAL


def test_the_state_count_is_the_people_not_the_kinds():
    """是**人数**而不是 emoji 种类数：两层各一种 emoji，但值是 6 + 2 而不是 2

    （判据来自实测：``total`` 与 ``users`` 数组长度逐一对齐。）
    """
    html = (
        '<html><body><div id="pageHeader"><h1><span><a href="/subject/1">S</a> » '
        '<a href="/subject/1/board">讨论</a></span><br/>T</h1></div>'
        '<script>var data_likes_list = {"1":{"a":{"total":3,"users":[{},{},{}]},'
        '"b":{"total":4,"users":[{},{},{},{}]}}};</script>'
        '<div class="postTopic light_odd clearit" id="post_1">'
        '<div class="post_actions re_info"><div class="action"><small>#1 - 2026-10-5 14:14</small></div></div>'
        '<div class="inner"><strong><a class="l" href="/user/u">甲</a></strong>'
        '<div class="topic_content">正文</div></div></div></body></html>'
    )
    assert BangumiTopic._from_html(html, "1").state_count == 7


def test_no_likes_script_means_zero():
    """页面里没有那段脚本（或没人贴状态）→ 0，页脚据此不显示"""
    html = (
        '<html><body><div id="pageHeader"><h1><span><a href="/subject/1">S</a> » '
        '<a href="/subject/1/board">讨论</a></span><br/>T</h1></div>'
        '<div class="postTopic light_odd clearit" id="post_1">'
        '<div class="post_actions re_info"><div class="action"><small>#1 - 2026-10-5 14:14</small></div></div>'
        '<div class="inner"><strong><a class="l" href="/user/u">甲</a></strong>'
        '<div class="topic_content">正文</div></div></div></body></html>'
    )
    assert BangumiTopic._from_html(html, "1").state_count == 0


def test_a_broken_script_is_skipped_not_fatal():
    """脚本坏了（X 改版 / 截断）→ 状态数 0，**别让整条解析失败**"""
    html = (
        '<html><body><div id="pageHeader"><h1><span><a href="/subject/1">S</a> » '
        '<a href="/subject/1/board">讨论</a></span><br/>T</h1></div>'
        '<script>var data_likes_list = {不是 json};</script>'
        '<div class="postTopic light_odd clearit" id="post_1">'
        '<div class="post_actions re_info"><div class="action"><small>#1 - 2026-10-5 14:14</small></div></div>'
        '<div class="inner"><strong><a class="l" href="/user/u">甲</a></strong>'
        '<div class="topic_content">正文</div></div></div></body></html>'
    )
    topic = BangumiTopic._from_html(html, "1")
    assert topic.state_count == 0
    assert topic.reply_count == 0, "坏脚本不该影响回复数"


# ── 回复数 ─────────────────────────────────────────────────────────────

def test_the_reply_count_excludes_the_opening_post():
    """**核心**: 回复数 = 楼层总数 − 1（主楼不算回复）。

    fixture 里是主楼 + 一层 ⇒ 1 条回复。
    """
    assert _topic().reply_count == 1


def test_the_reply_count_counts_sub_replies_too():
    """楼中楼也算一条回复（它是回复的一种）"""
    html = (
        '<html><body><div id="pageHeader"><h1><span><a href="/subject/1">S</a> » '
        '<a href="/subject/1/board">讨论</a></span><br/>T</h1></div>'
        '<div class="postTopic light_odd clearit" id="post_1">'
        '<div class="post_actions re_info"><div class="action"><small>#1 - 2026-10-5 14:14</small></div></div>'
        '<div class="inner"><strong><a class="l" href="/user/u">楼主</a></strong>'
        '<div class="topic_content">主楼</div></div></div>'
        '<div id="comment_list"><div class="light_even row row_reply clearit" id="post_2">'
        '<div class="post_actions re_info"><div class="action"><small>#2 - 2026-10-5 14:23</small></div></div>'
        '<div class="inner"><strong><a class="l" href="/user/a">甲</a></strong>'
        '<div class="reply_content"><div class="message">回复</div>'
        '<div class="topic_sub_reply" id="topic_reply_2">'
        '<div class="sub_reply_bg clearit" id="post_3">'
        '<div class="post_actions re_info"><div class="action"><small>#2-1 - 2026-10-5 14:30</small></div></div>'
        '<div class="inner"><strong><a class="l" href="/user/b">乙</a></strong>'
        '<div class="cmt_sub_content">楼中楼</div></div></div>'
        "</div></div></div></div></body></html>"
    )
    topic = BangumiTopic._from_html(html, "1")
    # 楼层: 主楼 + #2 + #2-1 ⇒ 回复数 2
    assert topic.reply_count == 2


def test_a_lone_opening_post_has_no_replies():
    """只有主楼 ⇒ 0 条回复（不是 -1）"""
    html = (
        '<html><body><div id="pageHeader"><h1><span><a href="/subject/1">S</a> » '
        '<a href="/subject/1/board">讨论</a></span><br/>T</h1></div>'
        '<div class="postTopic light_odd clearit" id="post_1">'
        '<div class="post_actions re_info"><div class="action"><small>#1 - 2026-10-5 14:14</small></div></div>'
        '<div class="inner"><strong><a class="l" href="/user/u">楼主</a></strong>'
        '<div class="topic_content">主楼</div></div></div></body></html>'
    )
    assert BangumiTopic._from_html(html, "1").reply_count == 0


# ── parser 透传 ────────────────────────────────────────────────────────

def test_the_parser_puts_the_state_count_in_like_count():
    """状态数走 ``like_count`` 位（bgm 的表情状态就是它的"点赞"形态）；回复数走 ``reply_count``"""
    result = _parse_via_parser()
    assert result.like_count == REAL_STATE_TOTAL
    assert result.reply_count == 1


def _parse_via_parser():
    import asyncio

    from parsehub.parsers.parser.bangumi import BangumiParser
    from parsehub.provider_api import bangumi as mod

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

    original = mod.http.AsyncClient
    mod.http.AsyncClient = _Client()
    try:
        return asyncio.run(BangumiParser()._do_parse("https://bgm.tv/subject/topic/41209"))
    finally:
        mod.http.AsyncClient = original


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
