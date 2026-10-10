"""threads 帖子的**附加长文块**（snippet_attachment_info）不能被丢掉。

用户报「抓不到下方内容」：帖子的正文在页面上是「caption 一句 + 下方长文（6 行截断 +
Read more）」，而 GraphQL 的 ``caption.text`` **只有那一句** —— 长文在
``text_post_app_info.snippet_attachment_info.text_fragments.fragments[].plaintext``。

实测（2026-10-10，161 生产）：
- 长文只在**带 cookie**（登录态）的 GraphQL 响应里有；匿名请求该字段为 ``null``
  （页面 SSR 也有，但那条路要抓 HTML —— 生产配了 cookie，走 GraphQL 零额外请求）。
- fixture ``threads_snippet_post.json`` 是从真实响应裁剪的（caption 14 字 + 长文 623 字）。

拼法：``caption.text`` 在前、长文在后、中间一个空行（与页面上的从上到下一次序一致）。
拿不到 snippet 时**逐字不变**（老行为），不能因为加了它就让别的帖子变样。
"""

import json
from pathlib import Path

import pytest

from parsehub.provider_api.threads import ThreadsPost

FIXTURES = Path(__file__).parent / "fixtures"

CAPTION = "ご参考までに添付いたします。"
LONG_HEAD = "拝啓"
LONG_TAIL = "敬具"


def _load_target() -> dict:
    payload = json.loads((FIXTURES / "threads_snippet_post.json").read_text(encoding="utf-8"))
    items = payload["data"]["data"]["edges"][0]["node"]["thread_items"]
    return next(i["post"] for i in items if i["post"]["code"] == "DeTKrdrGjrZ")


def test_the_long_snippet_is_attached_after_the_caption():
    """**核心**: 长文（623 字）必须出现在正文里，且在 caption 之后。"""
    post = ThreadsPost.from_graphql(_load_target())

    assert post.content.startswith(CAPTION), post.content[:80]
    assert LONG_HEAD in post.content, "长文开头丢了（snippet 没被读取）"
    assert LONG_TAIL in post.content, "长文结尾丢了（只接了半截）"
    # 顺序：caption → 空行 → 长文
    assert post.content.index(CAPTION) < post.content.index(LONG_HEAD)


def test_the_snippet_survives_intact():
    """完整长文逐字保留（不是截断版）。"""
    target = _load_target()
    raw = target["text_post_app_info"]["snippet_attachment_info"]["text_fragments"]["fragments"][0]["plaintext"]
    post = ThreadsPost.from_graphql(target)

    assert raw in post.content, "长文没有逐字保留"
    assert len(post.content) >= len(raw) + len(CAPTION)


def test_without_a_snippet_the_content_is_unchanged():
    """没有 snippet 的帖子：正文**逐字等于 caption**（老行为不变）。"""
    post = ThreadsPost.from_graphql(
        {
            "code": "X",
            "caption": {"text": "普通帖子"},
            "user": {"username": "u", "full_name": "U"},
            "text_post_app_info": {"is_reply": False, "snippet_attachment_info": None},
        }
    )
    assert post.content == "普通帖子"


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


def test_a_caption_less_post_still_shows_the_snippet():
    """caption 为空时也应发出长文（不能因为空正文把整块吞掉）。"""
    post = ThreadsPost.from_graphql(
        {
            "code": "X",
            "caption": {"text": ""},
            "user": {"username": "u", "full_name": "U"},
            "text_post_app_info": {
                "snippet_attachment_info": {
                    "text_fragments": {"fragments": [{"plaintext": "只有长文"}]}
                }
            },
        }
    )
    assert post.content == "只有长文"


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


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
