"""B站动态话题：链接化改用 ``desc.rich_text_nodes`` 的权威节点。

节点长这样（真实响应，见 ``test/fixtures/bilibili_dynamic_forward.json``）::

    {"type": "RICH_TEXT_NODE_TYPE_TOPIC",
     "text": "#三月的Phantasia#",
     "jump_url": "//search.bilibili.com/all?keyword=%E4%B8%89%E6%9C%88%E7%9A%84Phantasia"}

两个好处：

1. **类型由 B 站判定** —— 正则 ``#[^#]+#`` 在正文出现单个 ``#`` 时会错配
   （``C# 语言和 #tag#`` 里它会匹出 ``# 语言和 #``）；
2. 节点自带 ``jump_url``，不必自己按名字拼 URL。

⚠️ 节点给的 URL 是**协议相对**的（``//search.bilibili.com/…``），直接塞进 ``<a href>``
会被当成站内相对路径 —— 必须补 ``https:``（有一条测试专钉这个）。
"""

import json
from pathlib import Path

from parsehub.parsers.parser.bilibili import BiliParse
from parsehub.provider_api.bilibili import BiliDynamic

FIXTURE = Path(__file__).parent / "fixtures" / "bilibili_dynamic_forward.json"


def _module_dynamic() -> dict:
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return data["data"]["item"]["modules"]["module_dynamic"]


def test_topics_come_from_the_rich_text_nodes():
    """话题从节点取：名字去掉两侧 ``#``、地址来自节点的 ``jump_url``"""
    topics = BiliDynamic._get_desc_topics(_module_dynamic())
    names = [t["name"] for t in topics]
    assert names == ["三月的Phantasia", "七音阿卡莉", "脑洞学生会！"], names
    assert all(t["url"].startswith("//search.bilibili.com/") for t in topics), topics


def test_nodes_that_are_not_topics_are_ignored():
    """只有 ``RICH_TEXT_NODE_TYPE_TOPIC`` 才算话题（正文/@人 都不算）"""
    md = {
        "desc": {
            "rich_text_nodes": [
                {"type": "RICH_TEXT_NODE_TYPE_TEXT", "text": "普通文字"},
                {"type": "RICH_TEXT_NODE_TYPE_AT", "text": "@某人"},
                {"type": "RICH_TEXT_NODE_TYPE_TOPIC", "text": "#真话题#", "jump_url": "//x"},
            ]
        }
    }
    assert [t["name"] for t in BiliDynamic._get_desc_topics(md)] == ["真话题"]


def test_no_desc_gives_no_topics():
    assert BiliDynamic._get_desc_topics({}) == []


def test_the_node_path_matches_the_regex_path_byte_for_byte():
    """**铁则**: 节点路径与正则路径对**真话题**必须逐字一致（只换数据来源，不改外观）"""
    md = _module_dynamic()
    desc = (md.get("desc") or {}).get("text") or ""
    assert BiliParse.hashtag_handler(desc, BiliDynamic._get_desc_topics(md)) == BiliParse.hashtag_handler(desc)


def test_the_relative_url_is_completed_with_https():
    """节点 URL 是协议相对的 —— 必须补 ``https:``，否则 ``<a href="//…">`` 会被当成站内路径"""
    topics = [{"name": "三月的Phantasia", "url": "//search.bilibili.com/all?keyword=x"}]
    out = BiliParse.hashtag_handler("看 #三月的Phantasia# 吧", topics)
    assert 'href="https://search.bilibili.com/all?keyword=x"' in out, out
    assert 'href="//' not in out, out


def test_a_single_hash_does_not_create_a_bogus_topic():
    """**核心**: 正文里的单个 ``#`` 不该被当成话题的开头。

    正则会把 ``C# 语言和 #三月的Phantasia#`` 匹成 ``# 语言和 #``（一个假话题）。
    """
    desc = "正文说 C# 语言和 #三月的Phantasia# 很好"
    topics = [{"name": "三月的Phantasia", "url": "//s/x"}]
    out = BiliParse.hashtag_handler(desc, topics)
    assert "C# 语言和" in out, out
    assert "#三月的Phantasia#</a>" in out, out


def test_the_longest_topic_name_wins():
    """两个话题名互为前缀时，先换长的"""
    out = BiliParse.hashtag_handler(
        "#三月的Phantasia##三月#", [{"name": "三月", "url": "//a"}, {"name": "三月的Phantasia", "url": "//b"}]
    )
    assert "三月的Phantasia</a>#</a>" not in out, out


def test_falling_back_to_the_regex_without_topics():
    """没节点时退回正则（老接口 / 转发的 ``//@`` 段没有节点）"""
    out = BiliParse.hashtag_handler("看 #三月的Phantasia# 吧")
    assert "#三月的Phantasia#</a>" in out, out
    assert "search.bilibili.com" in out, out


def test_an_empty_topic_list_falls_back_too():
    """空列表等同"没有节点"，不能把正文原样返回（那样话题就不可点了）"""
    assert BiliParse.hashtag_handler("看 #tag# 吧", []) == BiliParse.hashtag_handler("看 #tag# 吧")


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
