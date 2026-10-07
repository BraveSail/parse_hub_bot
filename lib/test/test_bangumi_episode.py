"""bgm 章节讨论（``/ep/<id>``）：一整集的吐槽箱。

与话题页的三处关键差异（都是实测）：

1. **没有用户主楼** —— 主楼位置的内容是**官方章节信息**（``div.epDesc``：时长/首播 +
   简介 + STAFF）。用户给定：「主楼就抓 ep.1 …」，并把那段贴了出来。
2. **回复数用站点的** —— 页面上「吐槽箱 N」（``h2.subtitle span.tip``）。数楼层会少
   （实测 214 个楼层节点 vs 站点写 216，差值来自已删楼层）。
3. 头部是**条目名**（``#headerSubject h1``）+ **章节名**（``#columnEpA h2.title``）。

「没有就保持空白」：章节信息缺失时主楼为空、一级吐槽不引任何东西 —— 不臆造、不放占位。

fixture ``bangumi_ep_1738535.html`` 是真实页面抽的最小集。
"""

from pathlib import Path

import pytest

from parsehub.provider_api.bangumi import BangumiTopic

FIXTURES = Path(__file__).parent / "fixtures"
FIXTURE = FIXTURES / "bangumi_ep_1738535.html"

EPISODE_ID = "1738535"
#: 真实页面上的一级吐槽（用户在的那一层）与一条楼中楼
FLOOR_TOP = "2241753"
FLOOR_SUB = "2241745"

#: 真实页面上的值（站点给的回复数与状态数求和）
SITE_REPLY_COUNT = 216
STATE_TOTAL = 294


def _ep(floor_id: str = "") -> BangumiTopic:
    return BangumiTopic._from_html(FIXTURE.read_text(encoding="utf-8"), EPISODE_ID, floor_id=floor_id, kind="ep")


def _quoted(topic: BangumiTopic) -> list[str]:
    return [ln for ln in topic.markdown_content.splitlines() if ln.startswith("> ")]


# ── 头部 ───────────────────────────────────────────────────────────────

def test_the_title_is_the_subject():
    """标题是**条目名**（不是章节名）—— 章节名在归属行"""
    assert _ep().title == "生徒会にも穴はある！"


def test_the_origin_line_carries_the_episode_name():
    """**核心**: 归属行放**章节名**并链到该章节 —— 用户看的是"哪部番的哪一集" """
    origin = _ep().origin_line
    assert "ep.1" in origin, origin
    assert f"/ep/{EPISODE_ID}" in origin, origin
    assert origin.startswith("**<a href="), origin


# ── 主楼 = 官方章节信息 ────────────────────────────────────────────────

def test_the_opening_post_is_the_episode_info():
    """**核心**: 没有锚点时发的是**章节信息**（时长/首播 + 简介 + STAFF）"""
    body = _ep().markdown_content
    assert "时长:00:23:40" in body and "首播:2026-10-03" in body, body[:200]
    assert "水之江梅为了避免留级" in body, body[:300]
    assert "STAFF" in body
    assert "脚本：横谷 昌宏" in body, "STAFF 名单要完整带上"


def test_the_episode_info_is_not_a_quote():
    """章节信息就是主楼**本身** —— 不该被自己包成引用块"""
    assert _quoted(_ep()) == [], _ep().markdown_content


def test_the_episode_info_keeps_its_line_breaks():
    """**核心**: ``<br/>`` 的分行要保住 —— 每行的硬换行标记（行尾两空格）

    ⚠️ 标记必须在 ``<i>`` **外面**：包进标签里会让整个引用块被并成一行
    （实测：``> <i>行1  </i>`` 渲染成 1 行，``> <i>行1</i>  `` 才是 2 行）。
    """
    body = _ep().markdown_content
    lines = body.splitlines()
    assert len(lines) >= 8, f"章节信息被并成一行了: {body[:200]}"
    # 主楼正文里每行 BR 换行 → 行尾两空格（markdownify 的 br）
    assert any(ln.endswith("  ") for ln in lines), lines[:4]


def test_a_missing_episode_info_stays_blank():
    """**核心**（用户：「没有就保持空白」）: 没有 ``div.epDesc`` 时主楼为空，

    一级吐槽于是**不引任何东西** —— 不能拿第一层吐槽顶替主楼。
    """
    html = (
        '<html><body><div class="clearit" id="headerSubject"><h1 class="nameSingle">'
        '<a href="/subject/554779">某番</a></h1></div>'
        '<div id="columnEpA"><h2 class="title">ep.2 某集</h2></div>'
        '<div id="comment_list"><div class="light_even row row_reply clearit" id="post_1">'
        '<div class="post_actions re_info"><div class="action"><small>#1 - 2026-10-5 14:14</small></div></div>'
        '<div class="inner"><strong><a class="l" href="/user/u">甲</a></strong>'
        '<div class="reply_content"><div class="message">沙发</div></div></div></div></div>'
        "</body></html>"
    )
    topic = BangumiTopic._from_html(html, "1", kind="ep")
    assert _quoted(topic) == [], topic.markdown_content
    assert topic.markdown_content.strip() == "沙发", topic.markdown_content
    assert topic.floor_label == "#1"


def test_the_edit_links_are_stripped_out():
    """页面上给已登录用户的**编辑入口**（``/ep/<id>/edit``、``patch.bgm38.tv``）不是内容。

    （用户从浏览器复制的文本里就带着它们 —— 抓取时不能收进来。）
    """
    html = (
        '<html><body><div class="clearit" id="headerSubject"><h1><a href="/subject/1">S</a></h1></div>'
        '<div id="columnEpA"><h2 class="title">ep.1 标题 <a href="/ep/1/edit">[修改]</a>'
        '<a href="https://patch.bgm38.tv/edit/episode/1">[提供修改建议]</a></h2>'
        '<div class="epDesc"><a href="/ep/1/edit">[修改]</a>时长:00:23:40 / 首播:2026-10-03<br />'
        '简介正文<a href="https://patch.bgm38.tv/edit/episode/1">[提供修改建议]</a></div></div>'
        '<div class="postTopic light_odd clearit" id="post_1">'
        '<div class="post_actions re_info"><div class="action"><small>#1 - 2026-10-5 14:14</small></div></div>'
        '<div class="inner"><strong><a class="l" href="/user/u">甲</a></strong>'
        '<div class="topic_content">正文</div></div></div></body></html>'
    )
    topic = BangumiTopic._from_html(html, "1", kind="ep")
    assert "修改" not in topic.markdown_content, topic.markdown_content
    assert "patch.bgm38" not in topic.markdown_content
    assert "时长:00:23:40" in topic.markdown_content


# ── 楼层 ───────────────────────────────────────────────────────────────

def test_a_top_level_floor_quotes_the_episode_info():
    """一级吐槽引**章节信息**（= 主楼）—— 与话题页"一级楼层引主楼"同形态"""
    quoted = _quoted(_ep(FLOOR_TOP))
    assert quoted, "缺主楼引用块"
    assert any("时长:00:23:40" in ln for ln in quoted), quoted[:3]
    assert any("STAFF" in ln for ln in quoted), "STAFF 也要在引用块里"


def test_a_sub_reply_quotes_its_parent():
    """**核心**: 楼中楼引**父楼**（``#4``），不是主楼"""
    quoted = _quoted(_ep(FLOOR_SUB))
    assert any("#4 " in ln or "#4<" in ln for ln in quoted), quoted
    assert not any("STAFF" in ln for ln in quoted), "楼中楼不该引章节信息"


def test_a_sub_reply_without_its_parent_gets_no_context():
    """**核心**（用户：「没有就保持空白」）: 父楼不在页面上 → 楼中楼**不引任何东西**

    不能退回章节信息（那是番剧信息，与被回复的那句话无关），也不能引主楼。

    构造方式用**直接调 ``_build``**：父楼被删时它在 ``all_floors`` 里根本不存在，
    这样测的才是"找不到父楼"本身，而不是 DOM 长什么样。
    """
    from parsehub.provider_api.bangumi import BangumiFloor

    opening = BangumiFloor(label="#1", dom_id="1", markdown="章节信息", author_name="")
    orphan = BangumiFloor(
        label="#2-1", dom_id="9", is_sub=True, parent_dom_id="999", markdown="楼中楼", author_name="乙"
    )
    topic = BangumiTopic._build("1", "标题", "条目", "", opening, orphan, [opening, orphan], [], 0)
    assert topic.floor_label == "#2-1"
    assert not _quoted(topic), topic.markdown_content
    assert "楼中楼" in topic.markdown_content


def test_a_promoted_sub_reply_is_still_recognised_as_one():
    """**核心**: 父楼被删 ⇒ 楼中楼被提升成 ``comment_list`` 的直接子节点，

    这时 **DOM 层级认不出它是楼中楼** —— 但楼层号带 ``-``（``#2-1``）能认。
    认不出来的话它会被当一级楼层、去引主楼（= 编造上下文）。
    """
    html = (
        '<html><body><div class="clearit" id="headerSubject"><h1><a href="/subject/1">S</a></h1></div>'
        '<div id="columnEpA"><h2 class="title">ep.1</h2>'
        '<div class="epDesc">时长:00:23:40 / 首播:2026-10-03</div></div>'
        '<div id="comment_list"><div class="sub_reply_bg clearit" id="post_9">'
        '<div class="post_actions re_info"><div class="action"><small>#2-1 - 2026-10-5 14:30</small></div></div>'
        '<div class="inner"><strong><a class="l" href="/user/b">乙</a></strong>'
        '<div class="cmt_sub_content">楼中楼</div></div></div></div></body></html>'
    )
    topic = BangumiTopic._from_html(html, "1", floor_id="9", kind="ep")
    assert topic.floor_label == "#2-1"
    assert topic.floors and topic.floors[0].is_sub is True, topic.floors
    assert not _quoted(topic), f"认成一级楼层了，去引了章节信息: {topic.markdown_content}"


def test_the_anchored_floor_is_what_gets_sent():
    topic = _ep(FLOOR_TOP)
    assert topic.floor_label == "#8"
    assert "这片工期果然炸了" in topic.markdown_content


# ── 统计 ───────────────────────────────────────────────────────────────

def test_the_reply_count_comes_from_the_site():
    """**核心**: 回复数用页面上的「吐槽箱 N」—— 数楼层会少（已删楼层）"""
    assert _ep().reply_count == SITE_REPLY_COUNT


def test_the_state_count_sums_the_reactions():
    """状态数 = ``data_likes_list`` 求和"""
    assert _ep().state_count == STATE_TOTAL


# ── URL ────────────────────────────────────────────────────────────────

def test_the_url_parsing():
    assert BangumiTopic._topic_ref("https://bgm.tv/ep/1738535") == ("ep", "1738535")
    assert BangumiTopic._topic_ref(f"https://bgm.tv/ep/{EPISODE_ID}#post_{FLOOR_TOP}") == ("ep", EPISODE_ID)
    assert BangumiTopic.get_id_by_url("https://bgm.tv/ep/1738535") == "1738535"
    assert BangumiTopic.get_floor_id_by_url(f"https://bgm.tv/ep/{EPISODE_ID}#post_{FLOOR_TOP}") == FLOOR_TOP
    # 别的路径不该被当成章节
    assert BangumiTopic._topic_ref("https://bgm.tv/subject/554779") == ("", "")
    assert BangumiTopic._topic_ref("https://bgm.tv/blog/381120") == ("", "")


def test_the_parser_matches_the_episode_path():
    from parsehub.parsers.parser.bangumi import BangumiParser

    assert BangumiParser.match("https://bgm.tv/ep/1738535")
    assert BangumiParser.match(f"https://bgm.tv/ep/{EPISODE_ID}#post_{FLOOR_TOP}")
    assert BangumiParser.match("https://bangumi.tv/ep/1738535")
    # 既有路径不受影响
    assert BangumiParser.match("https://bgm.tv/blog/381120")
    assert BangumiParser.match("https://bgm.tv/subject/topic/41209")
    assert BangumiParser.match("https://bgm.tv/group/topic/472394")
    # 不接的
    assert not BangumiParser.match("https://bgm.tv/subject/554779")
    assert not BangumiParser.match("https://bgm.tv/subject/554779/ep")


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
