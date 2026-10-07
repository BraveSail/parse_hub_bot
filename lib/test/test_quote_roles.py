"""引用块角色（``quote_roles``）：显式声明，不再靠位置猜。

**为什么有这个东西**：引用块在正文里只是 ``> `` 开头的块，没有身份标记。渲染层以前靠
**位置**猜角色（开头→被回复、末尾→被引用）并据此归位媒体，于是"谁在正文里插一行"
就会让归位漂移 —— bgm 的归属行插在引用块前，引用块落到末尾，本层的图就跟引用块
贴到一起了（用户报「图片和引用贴一起」）。

现在角色由平台显式声明（按引用块**出现顺序**），渲染层按角色取媒体通道；
位置不再参与判断。留空时渲染层回退位置推断 —— 老缓存与未改的平台照旧。
"""

import json
from datetime import UTC, datetime

import pytest

from parsehub.types.result import (
    ImageParseResult,
    MultimediaParseResult,
    ParseResult,
    RichTextParseResult,
    VideoParseResult,
)
from parsehub.types.serialize import can_rebuild_from_cache, result_from_cache_dict, result_to_cache_dict

ALL_CLASSES = [VideoParseResult, ImageParseResult, MultimediaParseResult, RichTextParseResult]


def _kwargs(cls: type) -> dict:
    """每个类各自必填的那个媒体参数（别的一律默认）。"""
    if cls is VideoParseResult:
        return {"video": "https://example.com/v.mp4"}
    if cls is ImageParseResult:
        return {"photo": "https://example.com/p.jpg"}
    return {}


# ---------------------------------------------------------------- 字段本身

def test_the_field_defaults_to_no_declaration():
    """没声明时是空列表 —— 渲染层据此回退位置推断"""
    assert MultimediaParseResult(content="x").quote_roles == []


def test_the_roles_keep_their_order():
    """顺序有意义：第 i 个角色对应正文里第 i 个引用块"""
    result = MultimediaParseResult(content="x", quote_roles=["reply", "quoted"])
    assert result.quote_roles == ["reply", "quoted"]
    # 转个方向也要保序（不是集合）
    assert MultimediaParseResult(content="x", quote_roles=["quoted", "reply"]).quote_roles == ["quoted", "reply"]


def test_blank_values_are_dropped():
    """空串/空白项剔掉（平台拼装时容易带进来）"""
    assert MultimediaParseResult(content="x", quote_roles=["reply", "", "  "]).quote_roles == ["reply"]


def test_every_subclass_carries_the_roles():
    """**四个子类都要透传** —— 漏一个那个平台的引用块就只能靠位置猜"""
    for cls in ALL_CLASSES:
        result = cls(**{**_kwargs(cls), "quote_roles": ["quoted"]})
        assert result.quote_roles == ["quoted"], cls.__name__


def test_the_public_dict_does_not_gain_a_field():
    """``to_dict()`` 是公开输出格式（被测试逐字段冻住）—— 不该多出字段（与 hashtags 同）"""
    assert "quote_roles" not in MultimediaParseResult(content="x", quote_roles=["quoted"]).to_dict()


# ---------------------------------------------------------------- 缓存不丢

@pytest.mark.parametrize("cls", ALL_CLASSES)
def test_the_roles_survive_a_cache_round_trip(cls):
    """**核心**: 缓存命中时角色不能丢 —— 丢了就是"第一次发对、第二次（缓存）不对"

    （这类字段丢过不止一次：``markdown_content`` / ``position_label`` / ``hashtags``）
    """
    result = cls(**{**_kwargs(cls), "title": "T", "quote_roles": ["reply", "quoted"]})
    payload = json.loads(json.dumps(result_to_cache_dict(result)))
    assert payload["quote_roles"] == ["reply", "quoted"]

    assert can_rebuild_from_cache(result) is True
    rebuilt = result_from_cache_dict(payload)
    assert rebuilt.quote_roles == ["reply", "quoted"], cls.__name__
    assert type(rebuilt) is cls


def test_a_legacy_cache_entry_without_the_field_still_rebuilds():
    """**向后兼容**: 老缓存没有这个字段 —— 照旧重建（空 roles = 回退位置推断）"""
    result = MultimediaParseResult(content="x", title="T")
    payload = result_to_cache_dict(result)
    payload.pop("quote_roles")
    rebuilt = result_from_cache_dict(payload)
    assert rebuilt.quote_roles == []


def test_an_explicit_empty_list_round_trips_too():
    """显式的空列表（"我声明了：没有引用块角色"）也要能回来"""
    result = MultimediaParseResult(content="x", title="T", quote_roles=[])
    rebuilt = result_from_cache_dict(result_to_cache_dict(result))
    assert rebuilt.quote_roles == []


def test_the_roles_do_not_disturb_the_other_fields():
    """回归: 加字段不能把邻居弄丢（时间 / 计数 / 位置标记）"""
    result = MultimediaParseResult(
        content="x",
        title="T",
        published_at=datetime(2026, 10, 7, tzinfo=UTC),
        quoted_media_count=2,
        reply_media_count=1,
        position_label="#4-1",
        hashtags=["t"],
        quote_roles=["reply", "quoted"],
    )
    rebuilt = result_from_cache_dict(result_to_cache_dict(result))
    assert rebuilt.published_at == datetime(2026, 10, 7, tzinfo=UTC)
    assert (rebuilt.quoted_media_count, rebuilt.reply_media_count) == (2, 1)
    assert rebuilt.position_label == "#4-1"
    assert rebuilt.hashtags == ["t"]
    assert rebuilt.quote_roles == ["reply", "quoted"]


def test_the_base_class_stores_it():
    """基类自己也要存下来（子类透传最终落到基类）"""
    assert ParseResult.__init__.__doc__ and "quote_roles" in ParseResult.__init__.__doc__


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
