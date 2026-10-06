"""微博话题：边界取自**服务端给的锚点**，不用正则猜。

详情 API 的 ``text`` 字段里，话题**已经是锚点**：:

    <a href="//s.weibo.com/weibo?q=%23话题%23" target="_blank">#话题#</a>

所以话题名（与边界）直接从 ``q=`` 参数解出来即可。为什么必须这么做：
正文里出现**单个** ``#`` 时，正则 ``#[^#]+#`` 会把不相干的一段当成话题 ——
``C# 与 Python# 都常用`` 被误配成 ``# 与 Python#``，剥壳后 ``C#`` 就被破坏了。

fixture：``test/fixtures/weibo_topic_struct.json``（真实详情 API 响应）。
它的 ``page_info.object_type`` 是 ``ai_summary`` —— 这个值一度让**整条微博解析失败**
（枚举里没有它就抛 ValueError，``media_info`` 又是硬索引），现已修，下面有专门的回归测试。
"""

from parsehub.parsers.parser.weibo import WeiboParser
from parsehub.provider_api.weibo import Data

#: 真实响应里 ``text`` 字段的片段（话题是锚点）
REAL_TEXT = (
    '以下是与微博智搜关于<a href="//s.weibo.com/weibo?q=%23%E6%B5%B7%E5%A4%A7%E6%95%B0%23" '
    'target="_blank">#海大数#</a> 的对话内容，'
    '形成"海量大数据"#海大数# ，坚守#中小冉# 。'
)


def _data(**overrides) -> Data:
    payload = {"id": "1", "mid": "1", "text": REAL_TEXT, "text_raw": "形成…", "author_name": "A"}
    payload.update(overrides)
    return Data.from_kwargs(**payload)


def test_only_anchored_topics_are_taken():
    """**核心**: 话题名只从锚点取 —— 正文里裸的 ``#xxx#`` **不算**。

    这里用的是**手写片段**（只给一个锚点），所以只应取到那一个。
    真实响应里三个话题**都有锚点**，见 ``test_the_real_fixture_yields_all_topics``。
    """
    assert [t["name"] for t in _data().topics] == ["海大数"]


def test_the_real_fixture_yields_all_topics():
    """用**真实响应**核对：三个话题都带锚点，全部取到（顺序即出现顺序）。

    ⚠️ 这条是必要的纠正 —— 早先我用手写片段（只放一个锚点）得出过
    "服务端认为 ``#中小冉#`` 不是话题"的结论，那是**自造样本**造成的误判。
    """
    import json
    from pathlib import Path

    raw = json.loads((Path(__file__).parent / "fixtures" / "weibo_topic_struct.json").read_text(encoding="utf-8"))
    topics = Data.from_kwargs(**{k: v for k, v in raw.items() if k in {"id", "mid", "text", "text_raw"}}).topics
    names = [t["name"] for t in topics]
    assert names == ["从“苏大强”到全国“X大Y”话题矩阵", "海大数", "中小冉"], names


def test_two_anchors_give_two_topics_with_their_urls():
    """多个锚点：按出现顺序、URL 解码、去掉 ``#``；``url`` 补上 https:"""
    text = (
        '<a href="//s.weibo.com/weibo?q=%23%E7%94%B2%23">#甲#</a> 与 '
        '<a href="//s.weibo.com/weibo?q=%23%E4%B9%99%23">#乙#</a>'
    )
    topics = _data(text=text).topics
    assert [t["name"] for t in topics] == ["甲", "乙"]
    assert all(t["url"].startswith("https://s.weibo.com/weibo?q=") for t in topics), topics


def test_topics_are_empty_without_the_html_field():
    """拿不到 ``text``（老接口/字段缺失）时是空列表 —— 调用方退回正则"""
    assert _data(text=None).topics == []


def test_an_unknown_card_type_no_longer_kills_the_post():
    """**回归**: 不认识的卡片类型不该让整条微博挂掉。

    实测（真实响应）: ``page_info.object_type == "ai_summary"``（微博智搜卡片）——
    ``MediaType`` 枚举里没有它 ⇒ ``ValueError``；而且 ``PageInfo.parse`` 对
    ``media_info`` 是**硬索引**，智搜卡片没这个键 ⇒ ``KeyError``。两个都会让整条
    **解析失败**（用户发这类微博直接报错）。卡片不认识顶多是"不特殊处理"。
    """
    import json
    from pathlib import Path

    raw = json.loads((Path(__file__).parent / "fixtures" / "weibo_topic_struct.json").read_text(encoding="utf-8"))
    data = Data.parse(raw)  # 修复前这里会抛
    assert data.page_info is not None
    assert str(data.page_info.object_type) == "MediaType.UNKNOWN", data.page_info.object_type
    assert data.page_info.media_info is None


def test_a_single_hash_does_not_break_the_text():
    """**核心**: 正文里的单个 ``#`` 不该被当成话题。

    正则版本会把 ``C# 与 Python# 都常用`` 误配成 ``# 与 Python#`` 并剥掉，
    把 ``C#`` 破坏成 ``C #``。
    """
    topics = [{"name": "海大数", "url": "https://s.weibo.com/weibo?q=%23x%23"}]
    out = WeiboParser.hashtag_handler("形成#海大数# 。C# 与 Python# 都常用", topics)
    assert "C# 与 Python# 都常用" in out, out


def test_the_regex_fallback_is_unchanged_without_topics():
    """对照: 没 topics 时**与本改动前逐字相同**（老缓存 / 老接口不会变样）"""
    out = WeiboParser.hashtag_handler("形成#海大数# ，坚守#中小冉# 。")
    assert out == "形成 #海大数 ，坚守 #中小冉 。", out


def test_known_topics_become_links():
    """**核心改进**: 服务端给了话题，就做成**可点的链接**（与其它平台形态一致）。

    以前这里剥壳成纯文本（话题不可点）—— 那是唯一与其它平台不一致的地方。
    链接地址用服务端的 ``href``（``//s.weibo.com/…`` → 补 https:）。
    """
    topics = [{"name": "海大数", "url": "https://s.weibo.com/weibo?q=%23%E6%B5%B7%E5%A4%A7%E6%95%B0%23"}]
    out = WeiboParser.hashtag_handler("形成#海大数# 。", topics)
    assert '<a href="https://s.weibo.com/weibo?q=%23%E6%B5%B7%E5%A4%A7%E6%95%B0%23">#海大数#</a>' in out, out


def test_the_longest_topic_name_wins():
    """两个话题名互为前缀时，先替换长的，别把短名嵌进去"""
    topics = [{"name": "海大", "url": "https://a"}, {"name": "海大数", "url": "https://b"}]
    out = WeiboParser.hashtag_handler("#海大数# #海大#", topics)
    assert 'href="https://b">#海大数#</a>' in out, out


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
