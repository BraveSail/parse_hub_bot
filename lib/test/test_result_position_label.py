"""位置标记（楼层号）：字段本身 + 缓存往返 + linux.do 的填充。

用户报障原话：「https://linux.do/t/topic/2989140/4?u=libc.so.6 主楼标楼层号了但是回复没标」

**取证**（真实响应 `fixtures/linuxdo_floor_4.json`）: 分享的是第 4 楼（`MystDove`，
`reply_to_post_number=null` → 回复主楼），渲染出来::

    **<a href=".../u/MystDove">@MystDove</a>**        ← 本层(4楼): 没标

    > …Leo… · #1                                ← 主楼引用块: 标了

⇒ 缺口是**当前楼层的作者行**没有楼层号: 引用块里的其它层由 `_post_to_quote` 拼
`` · #N``, 而本层走 bot 侧 `format_author_line`, 那条路上**根本拿不到楼层号**
（`LinuxDoTopic` / `ParseResult` 都没有承载它的字段）。

⇒ 修法: 结果基类加一个**通用可选**字段 `position_label`（平台没有位置概念就留空 ——
不为一个平台引入第二种作者行形态），linux.do 填 `#<当前楼层>`。
"""

import asyncio
import json
from pathlib import Path

from parsehub.parsers.parser.linuxdo import LinuxDoParser
from parsehub.provider_api.linuxdo import LinuxDoTopic
from parsehub.types import (
    ImageParseResult,
    MultimediaParseResult,
    ParseResult,
    RichTextParseResult,
    VideoParseResult,
)
from parsehub.types.serialize import result_from_cache_dict, result_to_cache_dict

FIXTURE = Path(__file__).parent / "fixtures" / "linuxdo_floor_4.json"
TOPIC_ID = "2989140"
URL = "https://linux.do/t/topic/2989140/4"


def _payload() -> dict:
    return json.loads(FIXTURE.read_text())


# ---------------------------------------------------------------- 通用字段


def test_the_base_class_defaults_to_an_empty_label():
    """通用结果对象默认没有位置标记 —— 没有位置概念的平台不受影响"""
    assert ParseResult().position_label == ""


def test_the_label_is_stripped():
    assert ParseResult(position_label="  #4  ").position_label == "#4"


def test_every_result_subclass_accepts_it():
    """四个子类都要能收 —— 它们**各自重写了 ``__init__``**, 漏一个就 TypeError

    （改基类签名时很容易只改自己、忘了子类；这里逐个钉住。）
    """
    assert MultimediaParseResult(media=[], position_label="#7").position_label == "#7"
    assert VideoParseResult(video="https://x/v.mp4", position_label="#7").position_label == "#7"
    assert ImageParseResult(photo="https://x/p.jpg", position_label="#7").position_label == "#7"
    assert RichTextParseResult(markdown_content="正文", position_label="#7").position_label == "#7"


# ---------------------------------------------------------------- 缓存往返


def test_the_label_survives_the_cache_roundtrip():
    """**核心**: 缓存命中也得带楼层号 —— 漏了就是"第一次发有、第二次发没有"。

    这个项目的缓存坑: ``to_dict()`` 是公开输出格式（被测试冻住）不加字段,
    所以凡是渲染层要用的都得在 ``result_to_cache_dict`` 里**显式**带上。
    """
    original = MultimediaParseResult(content="正文", position_label="#4")
    rebuilt = result_from_cache_dict(result_to_cache_dict(original))
    assert rebuilt.position_label == "#4"


def test_an_empty_label_roundtrips_as_empty():
    """没有位置标记的结果往返后仍是空串"""
    rebuilt = result_from_cache_dict(result_to_cache_dict(MultimediaParseResult(content="正文")))
    assert rebuilt.position_label == ""


def test_an_old_cache_entry_without_the_key_still_loads():
    """改动**之前**写下的缓存条目没有这个键 —— 取默认空串, 不是崩掉"""
    data = result_to_cache_dict(MultimediaParseResult(content="正文"))
    data.pop("position_label")
    assert result_from_cache_dict(data).position_label == ""


# ---------------------------------------------------------------- linux.do 填充


def test_the_provider_records_the_requested_floor():
    """**核心**: 解析第 4 楼时, topic 记下的是 4"""
    topic = LinuxDoTopic._from_payload(_payload(), TOPIC_ID, post_number="4")
    assert topic.post_number == 4
    assert topic.author_handle == "MystDove"


def test_the_provider_records_the_opening_post_as_floor_one():
    """不带楼层号 = 主楼 = 1（主楼也标号, 免得不分享楼层时忽然没标记）"""
    assert LinuxDoTopic._from_payload(_payload(), TOPIC_ID).post_number == 1


def _topic(**kwargs) -> LinuxDoTopic:
    base = {
        "topic_id": TOPIC_ID,
        "title": "标题",
        "markdown_content": "正文",
        "author_name": "MystDove",
        "author_handle": "MystDove",
    }
    base.update(kwargs)
    return LinuxDoTopic(**base)


def _parse(monkeypatch, topic: LinuxDoTopic):
    async def fake_parse(*_a, **_k):
        return topic

    monkeypatch.setattr(LinuxDoTopic, "parse", fake_parse)
    return asyncio.run(LinuxDoParser()._do_parse(URL))


def test_the_parser_puts_the_floor_on_the_result(monkeypatch):
    """**核心**: parser 把楼层号交给结果对象（渲染层要用的东西必须到这一层）"""
    assert _parse(monkeypatch, _topic(post_number=4)).position_label == "#4"


def test_the_parser_leaves_the_label_empty_without_a_floor(monkeypatch):
    """平台没给楼层号时留空（不能让渲染层凭空多个 `#None`）"""
    assert _parse(monkeypatch, _topic(post_number=None)).position_label == ""


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
