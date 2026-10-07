"""bgm.tv 小组话题（``/group/topic/<id>``）解析。

与日志**不同构**（选择器、正文容器、楼层结构都不一样），所以是独立的一条路径；
只有 BBCode → markdown 的转换是共用的。

**取证的三个关键结构**（真实页面）::

    标题 : h1 里是「<a>小组名</a> » <a>讨论</a><br/>真正的标题」—— 标题在 <br/> 之后
    主楼 : div.postTopic          → 正文 div.topic_content
    回复 : div.row_reply          → 正文 div.reply_content
    楼中楼: div.sub_reply_bg      → 正文 div.cmt_sub_content
            ⚠️ **DOM 上嵌在父楼的正文容器里**（div.topic_reply_<父id>），不是兄弟节点
    楼层号: .post_actions small 的文本，形如 ``#2 - 2026-10-7 00:24``（楼中楼是 ``#2-1``）

fixture ``bangumi_group_topic_472394.html`` 是真实话题（抽过：只留 h1 + 主楼 + 前 5 层）。
"""

import asyncio
from pathlib import Path

import pytest

from parsehub.parsers.parser.bangumi import BangumiParser, BangumiParseResult
from parsehub.provider_api.bangumi import MAX_FLOORS, BangumiError, BangumiGroupTopic

FIXTURES = Path(__file__).parent / "fixtures"
FIXTURE = FIXTURES / "bangumi_group_topic_472394.html"


def _topic() -> BangumiGroupTopic:
    return BangumiGroupTopic._from_html(FIXTURE.read_text(encoding="utf-8"), "472394")


def _from_html(html: str, topic_id: str = "1", *, main: str | None = None) -> BangumiGroupTopic:
    """``html`` 是 **comment_list 的内容**（楼层）；主楼按真实结构放在它**外面**。"""
    page = f"""<html><body><div id="viewEntry">
      <div id="pageHeader"><h1>
        <span><a href="/group/testgrp"><img class="avatar" src="//lain.bgm.tv/g.jpg"/>测试小组</a>
        » <a href="/group/testgrp/forum">讨论</a></span><br/>话题标题</h1>
      </div>
      {main if main is not None else _main()}
      <div id="comment_list" class="commentList">{html}</div>
    </div></body></html>"""
    return BangumiGroupTopic._from_html(page, topic_id)


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
    """主楼节点（``postTopic`` + ``topic_content``）—— ``_from_html`` 靠它认页面。"""
    return _floor(
        "0", "#1", "2026-10-7 00:19", "楼主", "op", body,
        cls="postTopic light_odd clearit", body_cls="topic_content",
    )


# ---------------------------------------------------------------- 元信息

def test_the_header_gives_the_title_and_the_group():
    """**核心**: 标题在 ``<br/>`` 之后；小组名与链接在 ``<br/>`` 之前那一串里"""
    topic = _topic()
    assert topic.title == "真的有人能看过3000+部番吗"
    assert topic.group_name == "补旧番"
    assert topic.group_url == "https://bgm.tv/group/fillgrids"


def test_the_main_post_is_floor_one():
    """主楼 = #1，作者/时间取自它自己的楼层头（不是主题级字段）"""
    topic = _topic()
    assert topic.author_name == "irohard"
    assert topic.author_handle == "1175849"
    assert topic.published_at == "2026-10-7 00:19"


def test_the_body_is_the_main_post_only():
    """主楼正文与楼层分开 —— 正文里不该混进回复"""
    topic = _topic()
    assert "最近视奸了一圈路人的主页" in topic.markdown_content
    assert topic.markdown_content.index("最近视奸") < topic.markdown_content.index("---")


# ---------------------------------------------------------------- 楼层与层级

def test_floors_are_parsed_in_order():
    """楼层按页面顺序，且**主楼不在 floors 里**（它已经当了正文）"""
    topic = _topic()
    labels = [f.label for f in topic.floors]
    assert labels[0] == "#2"
    assert labels == sorted(labels, key=lambda s: [int(x) for x in s.lstrip("#").split("-")])
    assert "#1" not in labels


def test_sub_replies_are_marked():
    """楼中楼（``sub_reply_bg``）要标出来 —— 它渲染成引用块"""
    topic = _topic()
    subs = [f for f in topic.floors if f.is_sub]
    # fixture 抽的是「主楼 + 前 5 个一级回复」，所以楼中楼来自 #2 与 #6
    assert [f.label for f in subs] == ["#2-1", "#2-2", "#2-3", "#6-1"]
    assert all("-" in f.label for f in subs)


def test_a_sub_reply_body_does_not_contain_the_parent_text():
    """**核心**: 楼中楼摘出来当独立楼层 —— 父楼的正文里不能混进它的文字。

    ⚠️ 摘用的是 ``decompose()``，它会**清空被摘节点的内容** —— 所以必须**先收集节点、
    再逆序解析**（子 → 父）。正序解析时父楼先把子楼清掉，轮到子楼就只剩空壳
    （实测：45 个节点只解析出 38 条，7 条楼中楼全空）。
    """
    topic = _topic()
    parent = next(f for f in topic.floors if f.label == "#2")
    sub = next(f for f in topic.floors if f.label == "#2-1")

    assert "两年四百+少了" in parent.markdown  # 父楼自己的话
    assert "经常看到你在新番里评论" not in parent.markdown, "父楼正文里混进了楼中楼的文字"
    assert "经常看到你在新番里评论" in sub.markdown  # 楼中楼自己的话
    assert "两年四百+少了" not in sub.markdown


def test_every_floor_keeps_its_own_author():
    topic = _topic()
    by_label = {f.label: f for f in topic.floors}
    assert by_label["#2"].author_name == "大夜宵"
    assert by_label["#2"].author_handle == "dayexiao520"
    assert by_label["#3"].author_name == "Hoozy（已失业）"


# ---------------------------------------------------------------- 渲染形态

def test_the_group_line_comes_first_and_carries_the_floor_count():
    """小组归属单独一行（读者一眼知道出处），层数在那里说明"""
    topic = _topic()
    first = topic.markdown_content.splitlines()[0]
    assert "补旧番" in first
    assert "https://bgm.tv/group/fillgrids" in first
    assert "层" in first


def test_the_main_post_and_the_discussion_are_separated_by_a_rule():
    """主楼与讨论之间一条分割线 —— 区分「话题」与「大家怎么回」"""
    topic = _topic()
    assert "\n---\n" in topic.markdown_content


def test_a_floor_head_carries_author_label_and_time():
    """楼层头 = 作者（**粗体、名字可点**）+ ``@handle`` + 楼层号 + 时间"""
    topic = _topic()
    md = topic.markdown_content
    head = (
        '**<a href="https://bgm.tv/user/dayexiao520">大夜宵</a>** '
        "<code>@dayexiao520</code> · #2 · 2026-10-7 00:24"
    )
    assert head in md


def test_a_sub_reply_is_rendered_as_a_quote_block():
    """**核心**: 楼中楼用引用块（``> ``）表达层级 —— 与 linux.do 的楼层上下文同形"""
    topic = _topic()
    lines = topic.markdown_content.splitlines()
    sub_at = next(i for i, ln in enumerate(lines) if "#2-1" in ln)
    assert lines[sub_at].startswith("> "), lines[sub_at]
    # 紧随其后的正文也在引用块里
    assert any(ln.startswith("> ") and "经常看到你在新番里评论" in ln for ln in lines[sub_at : sub_at + 6])


def test_a_sub_reply_head_uses_html_not_markdown_asterisks():
    """**核心**: 楼中楼作者行用 HTML ``<i>``，不用 markdown 星号。

    实测（读回服务端块）: 引用**嵌套**时 markdown 星号会字面显示 —— 内容是
    ``[ "**", {RichTextUrl…} ]``，星号成了正文（bgm 自己的「某人 说:」引用正好是嵌套的）。
    单层引用块里的 ``**`` 其实是生效的，但**一律用 HTML 更稳**（任何深度都生效）。
    """
    topic = _topic()
    lines = topic.markdown_content.splitlines()
    sub_line = next(ln for ln in lines if ln.startswith("> ") and "#2-1" in ln)
    assert "<i>" in sub_line, sub_line
    assert "**" not in sub_line, f"引用块里的作者行不能带 markdown 星号: {sub_line}"


def test_the_whole_quote_block_carries_no_literal_asterisks():
    """整条楼中楼（含正文）都不该出现字面 ``**`` —— 它们在引用块里不会被解析。

    bgm 自己会在楼中楼里插「某人 说: …」的嵌套引用（原文是 ``<strong>``）——
    那一条也会变成字面星号，所以粗体得走 HTML（``<b>``）。
    """
    topic = _topic()
    quoted = [ln for ln in topic.markdown_content.splitlines() if ln.startswith(">")]
    assert quoted, "fixture 里应该有楼中楼"
    bad = [ln for ln in quoted if "**" in ln]
    assert not bad, bad


def test_a_top_level_reply_head_keeps_markdown_bold():
    """一级楼层在普通段落里，markdown 生效 —— 保持粗体（与其它平台的作者行一致）"""
    topic = _topic()
    lines = topic.markdown_content.splitlines()
    top_line = next(ln for ln in lines if "· #2 ·" in ln and not ln.startswith(">"))
    assert top_line.startswith("**<a href="), top_line
    assert top_line.count("**") == 2, "粗体只该包名字"


def test_a_top_level_reply_is_not_a_quote_block():
    """一级回复不是引用块（只有楼中楼才是）"""
    topic = _topic()
    lines = topic.markdown_content.splitlines()
    at = next(i for i, ln in enumerate(lines) if "· #2 ·" in ln)
    assert not lines[at].startswith(">")


def test_every_floor_appears_in_the_markdown():
    topic = _topic()
    md = topic.markdown_content
    for floor in topic.floors:
        assert floor.label in md, floor.label


# ---------------------------------------------------------------- 图片与表情

def test_uploaded_images_are_collected_from_any_floor():
    """图片从**所有楼层**的正文里抽（楼中楼里也可能有图）"""
    nested = _floor(
        "2",
        "#2-1",
        "2026-10-7 00:28",
        "某人",
        "someone",
        '<img class="code" src="//lain.bgm.tv/pic/photo/l/deep.jpg"/>',
        cls="sub_reply_bg clearit",
        body_cls="cmt_sub_content",
    )
    topic = _from_html(
        _floor(
            "1", "#2", "2026-10-7 00:24", "甲", "a",
            '<img class="code" src="//lain.bgm.tv/pic/photo/l/a.jpg"/>', nested=nested,
        )
    )
    urls = [i.url for i in topic.images]
    assert "https://lain.bgm.tv/pic/photo/l/a.jpg" in urls
    assert "https://lain.bgm.tv/pic/photo/l/deep.jpg" in urls, "楼中楼里的图也要收（它在树上要先抽）"


def test_smiles_stay_text_and_do_not_become_media():
    """表情是文字不是图（与日志同一判据：看 src 路径）"""
    topic = _from_html(
        _floor("1", "#2", "2026-10-7 00:24", "甲", "a",
               '好笑<img class="smile" alt="(bgm38)" src="/img/smiles/tv/15.gif"/>')
    )
    assert topic.images == []
    assert "(bgm38)" in topic.markdown_content


def test_the_body_keeps_no_image_tag():
    """图抽走之后正文里不能再留 img（否则 Telegram 会去抓外链，抓不到就整张丢）"""
    topic = _from_html(
        _floor("1", "#2", "2026-10-7 00:24", "甲", "a", '<img class="code" src="//lain.bgm.tv/pic/photo/l/a.jpg"/>')
    )
    assert "lain.bgm.tv/pic/photo" not in topic.markdown_content


# ---------------------------------------------------------------- 边界

def test_too_many_floors_are_truncated_with_a_note():
    """几百层的长帖不该整篇塞进一条消息 —— 截断并注明还剩多少层"""
    floors = "".join(
        _floor(str(i), f"#{i}", "2026-10-7 00:24", f"人{i}", f"u{i}", f"第 {i} 层的正文")
        for i in range(2, MAX_FLOORS + 12)
    )
    topic = _from_html(floors)
    assert len(topic.floors) > MAX_FLOORS  # 解析层面全都要（数据完整）
    assert "未显示" in topic.markdown_content
    # 显示的是前 MAX_FLOORS 层（#2..#MAX_FLOORS+1），再往后就不渲染了
    assert f"#{MAX_FLOORS + 1}" in topic.markdown_content
    assert f"#{MAX_FLOORS + 2}" not in topic.markdown_content
    assert "未显示" in topic.markdown_content


def test_a_missing_topic_raises_with_the_sites_wording():
    """话题不存在时仍是 HTTP 200，页面写「呜咕，出错了 数据库中没有…」"""
    with pytest.raises(BangumiError) as exc:
        BangumiGroupTopic._from_html("<html><body>呜咕，出错了 数据库中没有查询到该小组话题的信息</body></html>", "9")
    assert "不存在" in str(exc.value)


def test_an_unknown_shape_raises_a_different_message():
    with pytest.raises(BangumiError) as exc:
        BangumiGroupTopic._from_html("<html><body>什么都没有</body></html>", "1")
    assert "改版" in str(exc.value) or "结构" in str(exc.value)


def test_the_topic_id_comes_out_of_the_url():
    assert BangumiGroupTopic.get_id_by_url("https://bgm.tv/group/topic/472394") == "472394"
    assert BangumiGroupTopic.get_id_by_url("https://bgm.tv/blog/381120") == ""


# ---------------------------------------------------------------- parser

def test_the_parser_matches_group_topics():
    assert BangumiParser.match("https://bgm.tv/group/topic/472394")
    assert BangumiParser.match("https://bangumi.tv/group/topic/472394")


def test_the_parser_still_matches_blogs_and_refuses_other_paths():
    """**回归**: 加小组话题不能把日志弄丢；条目页与小组首页也不该被接走"""
    assert BangumiParser.match("https://bgm.tv/blog/381120")
    assert not BangumiParser.match("https://bgm.tv/subject/400602")
    assert not BangumiParser.match("https://bgm.tv/group/fillgrids")
    assert not BangumiParser.match("https://bgm.tv/group/fillgrids/forum")


def test_the_group_result_carries_floor_one_as_its_position():
    """主楼是第 1 层 —— 交给渲染层的 ``position_label``（各平台同一机制）"""

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
        result = asyncio.run(BangumiParser()._do_parse("https://bgm.tv/group/topic/472394"))
    finally:
        mod.http.AsyncClient = original

    assert result.title == "真的有人能看过3000+部番吗"
    assert result.position_label == "#1"
    assert result.author_handle == "1175849"
    assert result.author_url == "https://bgm.tv/user/1175849"
    assert BangumiParseResult.requires_media_download is True


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
