"""微博话题：边界取自**服务端给的锚点**，不用正则猜。

详情 API 的 ``text`` 字段里，话题**已经是锚点**：:

    <a href="//s.weibo.com/weibo?q=%23话题%23" target="_blank">#话题#</a>

所以话题名（与边界）直接从 ``q=`` 参数解出来即可。为什么必须这么做：
正文里出现**单个** ``#`` 时，正则 ``#[^#]+#`` 会把不相干的一段当成话题 ——
``C# 与 Python# 都常用`` 被误配成 ``# 与 Python#``，剥壳后 ``C#`` 就被破坏了。

fixture：``test/fixtures/weibo_topic_struct.json``（真实详情 API 响应）。
注意它的 ``page_info.object_type`` 是 ``ai_summary``（不在 ``MediaType`` 枚举里），
所以整条 payload 不能直接喂 ``Data.parse`` —— 这里只取用到的字段。
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
    """**核心**: 只有做成锚点的才是真话题。

    真实响应里 ``#海大数#`` 有锚点、``#中小冉#`` 没有 —— 服务端只认前者。
    正则会把两个都当话题（多识别了一个）。
    """
    assert _data().topic_names == ["海大数"]


def test_two_anchors_give_two_topics_in_order():
    """多个锚点：按出现顺序、URL 解码、去掉 ``#``"""
    text = (
        '<a href="//s.weibo.com/weibo?q=%23%E7%94%B2%23">#甲#</a> 与 '
        '<a href="//s.weibo.com/weibo?q=%23%E4%B9%99%23">#乙#</a>'
    )
    assert _data(text=text).topic_names == ["甲", "乙"]


def test_topic_names_are_empty_without_the_html_field():
    """拿不到 ``text``（老接口/字段缺失）时是空列表 —— 调用方退回正则"""
    assert _data(text=None).topic_names == []


def test_a_single_hash_does_not_break_the_text():
    """**核心**: 正文里的单个 ``#`` 不该被当成话题。

    正则版本会把 ``C# 与 Python# 都常用`` 误配成 ``# 与 Python#`` 并剥掉，
    把 ``C#`` 破坏成 ``C #``。
    """
    out = WeiboParser.hashtag_handler("形成#海大数# 。C# 与 Python# 都常用", ["海大数"])
    assert "C# 与 Python# 都常用" in out, out


def test_the_regex_fallback_is_unchanged_without_topics():
    """对照: 没 topics 时**与本改动前逐字相同**（老缓存 / 老接口不会变样）"""
    out = WeiboParser.hashtag_handler("形成#海大数# ，坚守#中小冉# 。")
    assert out == "形成 #海大数 ，坚守 #中小冉 。", out


def test_known_topics_look_byte_for_byte_like_the_regex_path():
    """**铁则**: 这次只换边界判据，**可见输出必须与正则路径逐字一致**。

    差别只应出现在"正则误伤的地方"（单个 ``#``），真话题的处理形态不变。
    """
    src = "形成#海大数# ，坚守#中小冉# 。"
    assert WeiboParser.hashtag_handler(src, ["海大数", "中小冉"]) == WeiboParser.hashtag_handler(src)


def test_the_longest_topic_name_wins():
    """两个话题名互为前缀时，先替换长的，别把短名嵌进去"""
    out = WeiboParser.hashtag_handler("#海大数# #海大#", ["海大", "海大数"])
    out_long_first = WeiboParser.hashtag_handler("#海大数# #海大#", ["海大数", "海大"])
    assert out == out_long_first, out


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
