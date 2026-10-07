"""Threads 帖子是回复时, 应把被回复帖子渲染成引用块 (markdown 引用).

数据结构依据真实 GraphQL 响应: 回复链 (父帖 + 目标帖) 同处一个
thread_items 数组, 目标帖之前的最后一条 is_reply=False 帖子即父帖.
"""

import asyncio

from parsehub.parsers.parser.threads import ThreadsParser
from parsehub.provider_api.threads import (
    ThreadsAPI,
    ThreadsMedia,
    ThreadsMediaType,
    ThreadsPost,
)


def make_spoiler_post(text: str, fragments: list[dict], username: str = "me") -> dict:
    """带文字级遮罩片段的帖子（结构照 2026-10-06 真实响应裁剪）。"""
    return {
        "code": "DeHnGiigRgh",
        "caption": {"text": text},
        "user": {"username": username, "full_name": "Me"},
        "text_post_app_info": {"is_reply": False, "text_fragments": {"fragments": fragments}},
    }


def make_post(
    code: str,
    text: str,
    username: str = "me",
    full_name: str = "Me",
    is_reply: bool = False,
    media_type: int | None = None,
) -> dict:
    post: dict = {
        "code": code,
        "caption": {"text": text},
        "user": {"username": username, "full_name": full_name},
        "text_post_app_info": {"is_reply": is_reply},
    }
    if media_type == 1:
        post["media_type"] = 1
        post["image_versions2"] = {"candidates": [{"url": "https://img.example/a.jpg", "width": 100, "height": 200}]}
    elif media_type == 2:
        post["media_type"] = 2
        post["image_versions2"] = {"candidates": [{"url": "https://img.example/thumb.jpg"}]}
        post["video_versions"] = [{"url": "https://vid.example/v.mp4"}]
        post["original_width"], post["original_height"] = 300, 400
    else:
        post["media_type"] = 19
    return post


def make_payload(*posts: dict) -> dict:
    return {
        "data": {
            "data": {
                "edges": [
                    {
                        "node": {
                            "thread_items": [{"post": p} for p in posts],
                        }
                    }
                ]
            }
        }
    }


def reply_post(text="original", handle="other", name="Other", media=None):
    return ThreadsPost(content=text, author_name=name, author_handle=handle, media=media)


# ---------- _extract_post: 父帖提取 ----------


def test_a_spoiler_text_fragment_becomes_inline_spoiler_markup():
    """文字级遮罩 → `||…||`（服务端解析成 RichTextSpoiler）。

    依据 2026-10-06 的真实响应：``caption.text`` 是**不带标记**的纯文字，
    遮罩信息只在 ``text_post_app_info.text_fragments.fragments[].styling_info.is_spoiler`` ——
    只看 caption 就会把遮罩文字当普通文字发出去（用户报「识别不到遮罩文字」）。
    """
    text = "那幾部都是 CR 的\n還有光看動畫的話，CR 的年繳比動畫瘋平時的年繳便宜"
    post = make_spoiler_post(
        text,
        [
            {"fragment_type": "plaintext", "plaintext": "那幾部都是 CR 的\n還有光看動畫的話，"},
            {
                "fragment_type": "plaintext",
                "plaintext": "CR 的年繳比動畫瘋平時的年繳便宜",
                "styling_info": {"is_bold": False, "is_spoiler": True, "is_underline": False},
            },
        ],
    )
    parsed = ThreadsPost.from_graphql(post)
    assert parsed.content.endswith("||CR 的年繳比動畫瘋平時的年繳便宜||"), parsed.content
    # 前面那段没被遮罩的保持原样
    assert parsed.content.startswith("那幾部都是 CR 的")


def test_a_post_without_spoiler_fragments_is_unchanged():
    post = make_spoiler_post(
        "普通正文",
        [{"fragment_type": "plaintext", "plaintext": "普通正文"}],
    )
    assert ThreadsPost.from_graphql(post).content == "普通正文"


def test_a_fragment_that_cannot_be_located_is_skipped():
    """片段文字在 caption 里找不到时**跳过**, 不臆造位置"""
    post = make_spoiler_post(
        "完全不同的正文",
        [{"fragment_type": "plaintext", "plaintext": "这段不在正文里", "styling_info": {"is_spoiler": True}}],
    )
    parsed = ThreadsPost.from_graphql(post)
    assert parsed.content == "完全不同的正文"
    assert "||" not in parsed.content


def test_extract_returns_parent_before_target():
    parent = make_post("PARENT", "original text", username="other", is_reply=False)
    target = make_post("TARGET", "my reply", is_reply=True)
    post, reply_to = ThreadsAPI._extract_post(make_payload(parent, target), "TARGET")
    assert post is target
    assert reply_to is parent


def test_extract_no_parent_when_target_is_first():
    target = make_post("TARGET", "standalone", is_reply=False)
    post, reply_to = ThreadsAPI._extract_post(make_payload(target), "TARGET")
    assert post is target
    assert reply_to is None


def test_extract_takes_immediate_predecessor_in_deep_chain():
    """多层回复链: 父帖是目标帖紧邻的前一条 (直接回复对象), 不跳过中间回复."""
    root = make_post("ROOT", "root text", is_reply=False)
    mid_reply = make_post("MID", "intermediate reply", is_reply=True)
    target = make_post("TARGET", "my reply", is_reply=True)
    post, reply_to = ThreadsAPI._extract_post(make_payload(root, mid_reply, target), "TARGET")
    assert post is target
    # 直接回复对象是 MID, 而不是更早的 root
    assert reply_to is mid_reply


def test_extract_target_after_parent_with_media():
    parent = make_post("PARENT", "parent with pic", media_type=1)
    target = make_post("TARGET", "reply text", is_reply=True)
    _, reply_to = ThreadsAPI._extract_post(make_payload(parent, target), "TARGET")
    assert reply_to is parent


def test_extract_no_parent_when_target_not_marked_reply():
    """目标帖不是回复时, 即便数组里另有前序帖子, 也不当作被回复对象."""
    other = make_post("OTHER", "unrelated earlier post", is_reply=False)
    target = make_post("TARGET", "standalone but has sibling", is_reply=False)
    post, reply_to = ThreadsAPI._extract_post(make_payload(other, target), "TARGET")
    assert post is target
    assert reply_to is None


def test_extract_fallback_returns_first_post_without_parent():
    """目标帖不在数组里时退回第一条, 且不把第一条当父帖."""
    first = make_post("FIRST", "someone else", is_reply=False)
    post, reply_to = ThreadsAPI._extract_post(make_payload(first), "NOTFOUND")
    assert post is first
    assert reply_to is None


# ---------- from_graphql: 字段映射 ----------


def test_from_graphql_reads_author_handle():
    post = ThreadsPost.from_graphql(make_post("A", "x", username="alice"))
    assert post.author_handle == "alice"


def test_from_graphql_reads_author_name():
    post = ThreadsPost.from_graphql(make_post("A", "x", full_name="Alice Chen"))
    assert post.author_name == "Alice Chen"


def test_from_graphql_without_user_is_empty_handle():
    raw = make_post("A", "x")
    raw.pop("user")
    post = ThreadsPost.from_graphql(raw)
    assert post.author_handle == ""
    assert post.author_name == ""


# ---------- _build_quote: Markdown 引用块 ----------


def test_quote_renders_markdown_blockquote():
    post = ThreadsPost(content="mine", reply_to=reply_post("line1\nline2", handle="other"))
    assert ThreadsParser._build_quote(post) == (
        '> <a href="https://www.threads.com/@other">@other</a>\n> line1\n> line2\n\n'
    )


def test_quote_marks_blank_lines():
    post = ThreadsPost(content="mine", reply_to=reply_post("a\n\nb"))
    assert ThreadsParser._build_quote(post) == (
        '> <a href="https://www.threads.com/@other">@other</a>\n> a\n>\n> b\n\n'
    )


def test_quote_falls_back_to_author_name():
    post = ThreadsPost(content="mine", reply_to=reply_post("x", handle="", name="夏吉ゆうこ"))
    assert ThreadsParser._build_quote(post) == "> 夏吉ゆうこ\n> x\n\n"


def test_quote_without_author():
    post = ThreadsPost(content="mine", reply_to=reply_post("x", handle="", name=""))
    assert ThreadsParser._build_quote(post) == "> x\n\n"


def test_quote_skipped_without_reply():
    assert ThreadsParser._build_quote(ThreadsPost(content="mine")) == ""


def test_quote_skipped_when_reply_has_no_text():
    post = ThreadsPost(content="mine", reply_to=reply_post(""))
    assert ThreadsParser._build_quote(post) == ""


def test_quote_skipped_when_reply_is_media_only():
    media = ThreadsMedia(type=ThreadsMediaType.IMAGE, url="https://img.example/a.jpg")
    post = ThreadsPost(content="mine", reply_to=reply_post("", media=media))
    assert ThreadsParser._build_quote(post) == ""


# ---------- _do_parse: 端到端 content 拼装 ----------


class _FakeParser(ThreadsParser, register=False):
    """测试替身: 绕过全局注册表 (否则重复注册 Threads 平台)."""

    def __init__(self, post: ThreadsPost):
        self._post = post

    async def _parse(self, url: str) -> ThreadsPost:
        return self._post


def test_do_parse_prefixes_quote():
    post = ThreadsPost(
        content="mine",
        reply_to=reply_post("original", handle="other"),
        media=None,
    )
    result = asyncio.run(_FakeParser(post)._do_parse("https://www.threads.com/@u/post/1"))
    assert result.content == '> <a href="https://www.threads.com/@other">@other</a>\n> original\n\nmine'


def test_do_parse_without_reply_is_unchanged():
    post = ThreadsPost(content="mine", media=None)
    result = asyncio.run(_FakeParser(post)._do_parse("https://www.threads.com/@u/post/1"))
    assert result.content == "mine"


def test_do_parse_keeps_media_with_quote():
    media = ThreadsMedia(type=ThreadsMediaType.IMAGE, url="https://img.example/a.jpg", width=5, height=6)
    post = ThreadsPost(content="mine", reply_to=reply_post("original"), media=media)
    result = asyncio.run(_FakeParser(post)._do_parse("https://www.threads.com/@u/post/1"))
    assert result.content == '> <a href="https://www.threads.com/@other">@other</a>\n> original\n\nmine'
    assert len(result.media) == 1
    assert result.media[0].url == "https://img.example/a.jpg"
