"""threads 帖子的**附加长文块**（snippet_attachment_info）不能被丢掉。

用户报「抓不到下方内容」：帖子的正文在页面上是「caption 一句 + 下方长文（6 行截断 +
Read more）」，而 GraphQL 的 ``caption.text`` **只有那一句** —— 长文在
``text_post_app_info.snippet_attachment_info.text_fragments.fragments[].plaintext``。

实测（2026-10-10，161 生产）：
- 长文只在**带 cookie**（登录态）的 GraphQL 响应里有；匿名请求该字段为 ``null``
  （页面 SSR 也有，但那条路要抓 HTML —— 生产配了 cookie，走 GraphQL 零额外请求）。
- fixture ``threads_snippet_post.json`` 是从真实响应裁剪的（caption 14 字 + 长文 623 字）。

两个契约：
1. ``ThreadsPost`` 里 caption 与长文**分开存**（``content`` / ``snippet``）——
   渲染形态由 parser 决定。
2. parser 把长文渲染成**引用块**放在正文之后（用户要求「这个长文改成引用块」），
   并在 ``quote_roles`` 里声明 ``quoted``。
"""

import asyncio
import json
from pathlib import Path

import pytest

from parsehub.parsers.parser.threads import ThreadsParser
from parsehub.provider_api.threads import ThreadsAPI, ThreadsPost

FIXTURES = Path(__file__).parent / "fixtures"

CAPTION = "ご参考までに添付いたします。"
LONG_HEAD = "拝啓"
LONG_TAIL = "敬具"


def _load_target() -> dict:
    payload = json.loads((FIXTURES / "threads_snippet_post.json").read_text(encoding="utf-8"))
    items = payload["data"]["data"]["edges"][0]["node"]["thread_items"]
    return next(i["post"] for i in items if i["post"]["code"] == "DeTKrdrGjrZ")


# ── provider: caption 与长文分开存 ──────────────────────────────────────

def test_the_post_exposes_the_snippet_separately():
    """**核心**: 长文（623 字）出现在 ``snippet`` 字段里，caption 留在 content。"""
    post = ThreadsPost.from_graphql(_load_target())

    assert post.content == CAPTION, post.content[:80]
    assert LONG_HEAD in post.snippet, "长文开头丢了（snippet 没被读取）"
    assert LONG_TAIL in post.snippet, "长文结尾丢了（只接了半截）"
    # 长文不进 content（渲染形态由 parser 决定）
    assert LONG_HEAD not in post.content


def test_the_snippet_survives_intact():
    """完整长文逐字保留（不是截断版）。"""
    target = _load_target()
    raw = target["text_post_app_info"]["snippet_attachment_info"]["text_fragments"]["fragments"][0]["plaintext"]
    post = ThreadsPost.from_graphql(target)

    assert raw in post.snippet, "长文没有逐字保留"


def test_without_a_snippet_the_content_is_unchanged():
    """没有 snippet 的帖子：content 逐字等于 caption、snippet 为空（老行为不变）。"""
    post = ThreadsPost.from_graphql(
        {
            "code": "X",
            "caption": {"text": "普通帖子"},
            "user": {"username": "u", "full_name": "U"},
            "text_post_app_info": {"is_reply": False, "snippet_attachment_info": None},
        }
    )
    assert post.content == "普通帖子"
    assert post.snippet == ""


def test_a_null_snippet_does_not_break_the_post():
    """snippet 存在是 null / 结构缺失 —— 都不能抛错（匿名响应里就是 null）。"""
    base = {
        "code": "X",
        "caption": {"text": "只有正文"},
        "user": {"username": "u", "full_name": "U"},
    }
    for tpa in (
        {"snippet_attachment_info": None},
        {"snippet_attachment_info": {}},
        {"snippet_attachment_info": {"text_fragments": None}},
        {"snippet_attachment_info": {"text_fragments": {"fragments": []}}},
        {},
    ):
        post = ThreadsPost.from_graphql({**base, "text_post_app_info": tpa})
        assert post.content == "只有正文", tpa
        assert post.snippet == "", tpa


def test_spoiler_marking_still_works_with_the_snippet():
    """遮罩仍生效：caption 里的遮罩片段照旧被包起来（回归防护）。"""
    post = ThreadsPost.from_graphql(
        {
            "code": "X",
            "caption": {"text": "秘密在此处结束"},
            "user": {"username": "u", "full_name": "U"},
            "text_post_app_info": {
                "text_fragments": {
                    "fragments": [{"plaintext": "秘密", "styling_info": {"is_spoiler": True}}]
                },
                "snippet_attachment_info": None,
            },
        }
    )
    assert "||秘密||" in post.content, post.content


# ── parser: 长文渲染成引用块 ────────────────────────────────────────────

def _parse_via_fixture(monkeypatch):
    payload = json.loads((FIXTURES / "threads_snippet_post.json").read_text(encoding="utf-8"))

    async def fake_post_graphql(self, *, doc_id, variables):  # noqa: ANN001, ARG001
        return payload

    monkeypatch.setattr(ThreadsAPI, "_post_graphql", fake_post_graphql)
    return asyncio.run(ThreadsParser()._do_parse("https://www.threads.com/@snowmaple_official/post/DeTKrdrGjrZ"))


def test_the_snippet_becomes_a_quote_block_after_the_body(monkeypatch):
    """**核心**: 长文以 ``> `` 引用块出现，位置在正文之后。"""
    result = _parse_via_fixture(monkeypatch)
    content = result.content

    # 正文（caption）仍在
    assert CAPTION in content, content[:200]
    # 长文在引用块里（每行 > 开头；公共 helper 会包 <i>）
    lines = [ln for ln in content.splitlines() if LONG_HEAD in ln]
    assert lines, "长文开头不在任何行里"
    assert lines[0].lstrip().startswith(">"), f"长文不在引用块里: {lines[0][:80]!r}"
    assert "<i>" in lines[0], "引用块应整块斜体（公共 helper 的形态）"
    # caption 在长文之前（正文在前、引用块在后）
    assert content.index(CAPTION) < content.index(LONG_HEAD)

    # 引用块整块都在（首尾行都是 > 前缀）
    quote_lines = [ln for ln in content.splitlines() if ln.lstrip().startswith(">")]
    assert any(LONG_TAIL in ln for ln in quote_lines), "长文结尾不在引用块里"


def test_the_snippet_quote_role_is_declared(monkeypatch):
    """``quote_roles`` 声明 ``quoted``（长文块），渲染层据此归位媒体。"""
    result = _parse_via_fixture(monkeypatch)

    # 这条帖子本身是被回复帖: reply（被回复帖引用块）+ quoted（长文块）
    assert result.quote_roles == ["reply", "quoted"], result.quote_roles


def test_without_a_snippet_no_quote_block_is_added(monkeypatch):
    """没有长文的帖子：正文里不出现引用块、roles 里不出现 quoted。"""
    payload = {
        "data": {
            "data": {
                "edges": [
                    {
                        "node": {
                            "thread_items": [
                                {
                                    "post": {
                                        "code": "X1",
                                        "caption": {"text": "普通帖"},
                                        "user": {"username": "u", "full_name": "U"},
                                        "media_type": 19,
                                        "image_versions2": {"candidates": []},
                                        "text_post_app_info": {"is_reply": False, "snippet_attachment_info": None},
                                    }
                                }
                            ]
                        }
                    }
                ]
            }
        }
    }

    async def fake_post_graphql(self, *, doc_id, variables):  # noqa: ANN001, ARG001
        return payload

    monkeypatch.setattr(ThreadsAPI, "_post_graphql", fake_post_graphql)
    result = asyncio.run(ThreadsParser()._do_parse("https://www.threads.com/@u/post/X1"))

    assert ">" not in result.content, result.content
    assert result.quote_roles == [], result.quote_roles


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
