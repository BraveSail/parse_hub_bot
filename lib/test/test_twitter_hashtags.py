"""标签取自**平台实体**，不用正则猜边界。

起因（2026-10-06，用户）: 一条推文的标签被识别成 `#FX戦士くるみちゃん」第一話より` ——
标签把后面的 `」第一話より` 也吃进去了，因为正则的终止字符集没枚举 `」`。

而 API 早就给了精确边界::

    legacy.entities.hashtags = [{"indices": [41, 52], "text": "FX戦士くるみちゃん"}]

``text`` 与 x.com 上 hashtag 链接的最后一段**逐字一致**。

⚠️ **不用 ``indices``**: 它指向**原始** ``full_text`` 的位置，而正文到渲染层时已被加工
（短链展开、t.co 去掉）—— 位置会漂。按名字匹配更稳。

fixture 取自真实响应（`https://x.com/fxkurumi_info/status/2107410546899451980`），只保留必要字段。
"""

from parsehub.provider_api.twitter import Twitter

FULL_TEXT = "TVアニメ「#FX戦士くるみちゃん」第一話より https://t.co/cl4HMa5dzc"


def _legacy(**overrides) -> dict:
    legacy = {
        "full_text": FULL_TEXT,
        "created_at": "Tue Oct 06 10:00:00 +0000 2026",
        "favorite_count": 382,
        "entities": {"hashtags": [{"indices": [41, 52], "text": "FX戦士くるみちゃん"}], "urls": [], "media": []},
    }
    legacy.update(overrides)
    return legacy


def _node(**overrides) -> dict:
    node = {
        "rest_id": "2107410546899451980",
        "legacy": _legacy(),
        "core": {"user_results": {"result": {"legacy": {"name": "くるみ", "screen_name": "fxkurumi_info"}}}},
        "views": {"count": "11319"},
    }
    node.update(overrides)
    return node


def _hashtags(node: dict) -> list[str]:
    return Twitter()._parse_result(node).hashtags


def test_the_entity_supplies_the_exact_tag_name():
    """实体给的标签名就是网页上 hashtag 链接的那一段 —— 没被 `」` 带跑"""
    assert _hashtags(_node()) == ["FX戦士くるみちゃん"]


def test_the_tag_name_never_keeps_the_hash():
    """我们统一存**不含 `#`** 的名字（渲染时自己加），实体里本来也不带；
    但万一有实现带上，也要剥掉"""
    node = _node(legacy=_legacy(entities={"hashtags": [{"text": "#tag"}], "urls": [], "media": []}))
    assert _hashtags(node) == ["tag"]


def test_a_wrapped_response_also_yields_the_tags():
    """响应有两种形态（包装 / 平铺）—— 标签在 node 上，两条路都要能取到"""
    wrapped = {"tweet": _node()}
    assert _hashtags(wrapped) == ["FX戦士くるみちゃん"]


def test_note_tweet_tags_are_merged():
    """长推文的标签在 ``note_tweet...entity_set`` 里 —— 与 legacy 的合并（去重保序）"""
    node = _node(
        note_tweet={
            "note_tweet_results": {
                "result": {"text": "长文", "entity_set": {"hashtags": [{"text": "長文タグ"}]}}
            }
        }
    )
    assert _hashtags(node) == ["FX戦士くるみちゃん", "長文タグ"]


def test_duplicate_tags_across_both_sources_appear_once():
    node = _node(
        note_tweet={
            "note_tweet_results": {
                "result": {"entity_set": {"hashtags": [{"text": "FX戦士くるみちゃん"}]}}
            }
        }
    )
    assert _hashtags(node) == ["FX戦士くるみちゃん"]


def test_no_entity_means_no_tags():
    """拿不到实体时是空列表 —— 渲染层据此退回正则，不是"没有标签" """
    node = _node(legacy=_legacy(entities={"urls": [], "media": []}))
    assert _hashtags(node) == []


def test_blank_entity_names_are_dropped():
    node = _node(legacy=_legacy(entities={"hashtags": [{"text": "  "}, {"text": None}, {"text": "ok"}]}))
    assert _hashtags(node) == ["ok"]


def test_the_article_branch_also_carries_the_tags():
    """article（长文）分支是另一个构造点 —— 漏了它就会静默丢标签"""
    node = _node(
        article={
            "article_results": {
                "result": {
                    "title": "T",
                    "content_state": {"blocks": []},
                    "media_entities": [],
                }
            }
        }
    )
    parsed = Twitter()._parse_result(node)
    assert parsed.hashtags == ["FX戦士くるみちゃん"]


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
