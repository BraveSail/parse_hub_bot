"""图集超过 4 张时把**多出来的**折进按钮。

用户报障原话：「https://www.threads.com/@nichi_studio/post/DeKDRoXEoi9 这种图片超过4张的
也按钮折叠一下」—— 那条帖子 13 张图一次铺满整屏。

**真机取证**（两条测试消息实测，服务端返回的 blocks）::

    <details><summary>…</summary> + 3 张独立图  → RichBlockDetails 内含 3 × RichBlockPhoto
    <details><summary>…</summary> + <tg-collage> → RichBlockDetails 内含 RichBlockCollage
    外层 <tg-collage> 与 details 并存           → 各自成立

⇒ 折叠装图可行（markdown 路径服务端原生解析；blocks 路径由 ``rich_blocks.py`` 的
details 分支递归处理内层）。

**形态**（与正文长文折叠同一套观感 —— 收起时要有东西可看）: 前 4 张留在外面，
第 5 张起折进按钮。

**不该折的两处**（客户端对嵌套折叠没有保证）: 引用卡片内部、手动打码的整组内容。
"""

import types

from plugins.helpers import (
    _COLLAGE_FOLD_THRESHOLD,
    build_rich_markdown,
    render_quote_card,
    wrap_collage,
)

MEDIA = [f"![](tg://photo?id=m{i})" for i in range(13)]


def _result(**kwargs):
    fields = {
        "title": "",
        "content": "正文内容",
        "raw_url": "https://www.threads.com/@x/post/1",
        "author_name": "作者",
        "author_handle": "handle",
        "author_url": "https://www.threads.com/@handle",
        "published_at": None,
        "view_count": None,
        "like_count": None,
        "tags": None,
        "platform": None,
        "is_sensitive": False,
        "quoted_media_count": 0,
        "reply_media_count": 0,
    }
    fields.update(kwargs)
    return types.SimpleNamespace(**fields)


def _config():
    return types.SimpleNamespace(hide_title=False, hide_desc=False, hide_source=False)


def _render(placeholders, *, spoiler_tag: str = "", **kwargs) -> str:
    return build_rich_markdown(
        _result(**kwargs),
        config=_config(),
        lang="zh-hans",
        media_placeholders=placeholders,
        hide_content=spoiler_tag,
    )


# ---------------------------------------------------------------- 阈值边界


def test_the_threshold_is_four():
    """阈值就是用户说的 4 —— 改动了会被这里抓住"""
    assert _COLLAGE_FOLD_THRESHOLD == 4


def test_four_images_are_not_folded():
    """**恰好 4 张不折**（边界内侧）"""
    out = wrap_collage(MEDIA[:4], fold_summary="展开其余 0 张图片")
    assert len(out) == 1
    assert "<details>" not in out[0]


def test_five_images_are_folded():
    """**5 张就折**（边界外侧）—— 前 4 张在外, 第 5 张进按钮"""
    out = wrap_collage(MEDIA[:5], fold_summary="展开其余 1 张图片")
    assert len(out) == 2
    assert out[0].startswith("<tg-collage>") and "m3" in out[0] and "m4" not in out[0]
    assert "<details>" in out[1] and "m4" in out[1]


def test_a_single_image_is_returned_as_is():
    """单张不进图集、更不折叠"""
    assert wrap_collage(MEDIA[:1], fold_summary="展开其余 0 张图片") == MEDIA[:1]


# ---------------------------------------------------------------- 13 张（用户报的那条）


def test_thirteen_images_leave_four_outside_and_fold_nine():
    """**核心**: 13 张 → 前 4 张在外、后 9 张进按钮, 一张都不能丢"""
    out = wrap_collage(MEDIA, fold_summary="🖼 展开其余 9 张图片")
    joined = "\n".join(out)
    assert len(out) == 2
    outside = out[0]
    inside = out[1]
    for i in range(4):
        assert f"m{i})" in outside, f"第 {i} 张应该留在外面"
        assert f"m{i})" not in inside
    for i in range(4, 13):
        assert f"m{i})" in inside, f"第 {i} 张应该在折叠块里"
    # 13 张一张不少
    assert sum(joined.count(f"m{i})") for i in range(13)) == 13


def test_the_summary_carries_the_hidden_count():
    """摘要写的是**折起来的张数**, 不是总数"""
    out = _render(MEDIA)
    assert "展开其余 9 张图片" in out, out


# ---------------------------------------------------------------- 集成到 build_rich_markdown


def test_the_fold_lands_below_the_body_text():
    """按钮在正文之后（媒体本来就在正文后面）"""
    out = _render(MEDIA)
    assert out.index("正文内容") < out.index("<details>")


def test_the_hidden_images_are_wrapped_in_a_collage():
    """折起来的那些自己也是一个图集（展开后是图集而不是散图）"""
    out = _render(MEDIA)
    tail = out[out.index("<details>") :]
    assert "<tg-collage>" in tail


def test_small_galleries_are_unchanged():
    """4 张以内的帖子渲染结果不含任何 details（不能把普通帖子也折了）"""
    for count in (1, 2, 3, 4):
        out = _render(MEDIA[:count])
        assert "<details>" not in out, count


# ---------------------------------------------------------------- 不该折的两处


def test_no_summary_means_no_fold():
    """没有摘要就不折 —— 这是"已经在折叠块里"的调用方保持原样的方式"""
    out = wrap_collage(MEDIA)
    assert len(out) == 1
    assert "<details>" not in out[0]


#: 长引用（超阈值）—— 卡片的文字会折, 但它的媒体不该**再**折一层
LONG_QUOTE = "> 作者\n" + "\n".join(f"> 第 {i} 行引用内容" for i in range(1, 14))


def test_a_short_quoted_card_does_not_fold_its_media():
    """短引用卡片不折（本来就没到阈值）"""
    out = render_quote_card("> 作者\n> 引用正文", MEDIA[:8], summary="展开全文")[0]
    assert "<details>" not in out, out
    for i in range(8):
        assert f"m{i})" in out


def test_a_long_quoted_card_folds_only_its_text():
    """长引用卡片: 只有"折文字"那**一个**按钮, 媒体不再自己折一层（嵌套无保证）"""
    out = render_quote_card(LONG_QUOTE, MEDIA[:8], summary="展开全文")[0]
    assert out.count("<details>") == 1, out
    # 8 张图一张不少, 且都在折叠块**外**的图集里
    assert sum(out.count(f"m{i})") for i in range(8)) == 8


def test_a_spoiled_post_does_not_nest_a_second_fold():
    """手动打码时整组内容本来就要进 details —— 媒体不再自己折一层"""
    out = _render(MEDIA, spoiler_tag="#nsfw")
    assert out.count("<details>") == 1, out
    # 13 张图仍在（都要被遮住）
    assert sum(out.count(f"m{i})") for i in range(13)) == 13


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
