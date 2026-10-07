"""引用块的角色：**由平台显式声明**，位置不参与任何判断。

以前渲染层靠位置猜两件事 —— 块是"被回复"还是"被引用"、媒体取哪一段 ——
于是"谁在正文里插一行"就能让归位漂移（bgm 的归属行插在引用块前，引用块落到末尾，
本层的图被插到它前面贴住；把归属行挪走后，纯图楼层又因为**没有正文**而被
`split_quote_blocks` 判成"末尾块"，图同样排到了块前面）。

现在角色同时决定两件事：

- ``reply``  → 卡片渲染在正文**之前**，媒体取 ``reply_media_count`` 那一段
- ``quoted`` → 卡片渲染在正文**之后**，媒体取 ``quoted_media_count`` 那一段

所以块在产物里的位置怎么写都不影响结果。没声明角色时（老缓存 / 未改的平台）
退回原来的位置推断，那条路径与引入 ``quote_roles`` 之前**逐字一致**。
"""

import types

from plugins.helpers import build_rich_markdown

REPLY = "> <i>回复块的作者</i>\n> <i>回复正文</i>"
QUOTED = "> <i>引用块的作者</i>\n> <i>引用正文</i>"
BODY = "本帖正文"

REPLY_MEDIA = ("![](tg://photo?id=r0)",)
QUOTED_MEDIA = ("![](tg://photo?id=q0)",)
BODY_MEDIA = ("![](tg://photo?id=b0)",)


class _Cfg(types.SimpleNamespace):
    hide_title = False
    hide_desc = False
    hide_source = True


def _result(**kw):
    result = types.SimpleNamespace(
        title="",
        content="",
        raw_url="https://x.com/a/status/1",
        author_name="作者",
        author_handle="",
        author_url="",
        published_at=None,
        view_count=None,
        like_count=None,
        tags=None,
        platform=None,
        media=None,
        quote_roles=None,
        origin_line="",
    )
    for k, v in kw.items():
        setattr(result, k, v)
    return result


def _render(content: str, *, roles=None, reply_media=(), quoted_media=(), body_media=BODY_MEDIA, hide_desc=False):
    return build_rich_markdown(
        _result(content=content, quote_roles=roles),
        config=_Cfg(hide_desc=hide_desc),
        media_placeholders=list(body_media),
        quote_media_placeholders=list(quoted_media),
        reply_media_placeholders=list(reply_media),
    )


def _layout(md: str) -> list[str]:
    """产物压成"块顺序"：引用块 / 图（标注是否在块内）/ 正文。

    图在不在引用块里是"有没有掉出卡片"的判据 —— ``> ![](...)`` 在块内。
    """
    out: list[str] = []
    in_quote = False
    for line in md.splitlines():
        quoted_line = line.lstrip().startswith(">")
        if quoted_line:
            if not in_quote:
                out.append("引用块")
                in_quote = True
        elif in_quote:
            in_quote = False
        body = line.lstrip("> ").strip()
        if body.startswith("![]"):
            mid = body.split("id=")[1].rstrip(")")
            out.append(f"图:{mid}{'(块内)' if quoted_line else ''}")
        elif body and not quoted_line and not body.startswith(("#", "---")) and "作者" not in body:
            out.append("正文")
    return out


# ── 角色决定**顺序**（位置无关） ────────────────────────────────────────

def test_a_reply_role_puts_the_card_before_the_body():
    """``reply`` → 卡片在正文**前**（块写在正文后面也一样）"""
    md = _render(f"{BODY}\n\n{REPLY}", roles=["reply"], reply_media=REPLY_MEDIA)
    assert _layout(md) == ["引用块", "图:r0(块内)", "正文", "图:b0"], md


def test_a_quoted_role_puts_the_card_after_the_body():
    """``quoted`` → 卡片在正文**后**（块写在正文前面也一样）"""
    md = _render(f"{QUOTED}\n\n{BODY}", roles=["quoted"], quoted_media=QUOTED_MEDIA)
    assert _layout(md) == ["正文", "图:b0", "引用块", "图:q0(块内)"], md


def test_a_bodyless_floor_keeps_the_picture_behind_the_card():
    """**核心**（bgm 纯图楼层）: 正文为空、只有一个引用块 ——

    图必须排在卡片**之后**。以前这种产物里引用块被 `split_quote_blocks` 判成
    "末尾块"，媒体就排到了它前面（真机实测：图在前、引用块在后）。
    """
    md = _render(REPLY, roles=["reply"], reply_media=REPLY_MEDIA, body_media=())
    assert _layout(md) == ["引用块", "图:r0(块内)"], md


def test_two_blocks_land_on_their_own_sides():
    """两个块：``reply`` 在前、``quoted`` 在后"""
    md = _render(
        f"{REPLY}\n\n{BODY}\n\n{QUOTED}",
        roles=["reply", "quoted"],
        reply_media=REPLY_MEDIA,
        quoted_media=QUOTED_MEDIA,
    )
    assert _layout(md) == ["引用块", "图:r0(块内)", "正文", "图:b0", "引用块", "图:q0(块内)"], md


def test_roles_are_matched_in_body_order():
    """角色按块的**出现顺序**配对 —— 反过来写结果也反过来"""
    md = _render(
        f"{REPLY}\n\n{BODY}\n\n{QUOTED}",
        roles=["quoted", "reply"],
        reply_media=REPLY_MEDIA,
        quoted_media=QUOTED_MEDIA,
    )
    # 先出现的块声明成 quoted → 到正文**后**；后出现的块声明成 reply → 到正文**前**。
    # 位置不再是判据：它们的**内容**分别落在哪一侧由角色说了算。
    layout = _layout(md)
    assert layout[0] == "引用块" and layout[1] == "图:r0(块内)", layout  # reply 块在前，拿 reply 段
    assert layout[-1] == "图:q0(块内)", layout  # quoted 块在后，拿 quoted 段


def test_a_middle_line_does_not_disturb_anything():
    """正文里插别的行（当年 bgm 的归属行）也不影响 —— 这正是要修的那个坑"""
    md = _render(f"{REPLY}\n\n**某来源** » 讨论\n\n{BODY}", roles=["reply"], reply_media=REPLY_MEDIA)
    assert _layout(md)[0] == "引用块", md
    assert "> ![](tg://photo?id=r0)" in md


# ── 兜底：媒体不能丢 ───────────────────────────────────────────────────

def test_a_role_without_its_block_keeps_the_media_in_the_body():
    """声明了 ``reply`` 但正文里没有块 → 那些图落到正文媒体（不丢）"""
    md = _render(BODY, roles=["reply"], reply_media=REPLY_MEDIA)
    assert "图:r0" in _layout(md), md


def test_more_blocks_than_declared_roles_falls_back_per_block():
    """块多于声明 → 多出来的块按位置兜底（别丢媒体）"""
    md = _render(
        f"{QUOTED}\n\n{BODY}\n\n{REPLY}",
        roles=["quoted"],
        reply_media=REPLY_MEDIA,
        quoted_media=QUOTED_MEDIA,
    )
    layout = _layout(md)
    assert "图:q0(块内)" in layout and "图:r0(块内)" in layout, layout


def test_hide_desc_drops_the_cards():
    """``hide_desc`` 时不渲染卡片（既有语义），媒体也不发 —— 与正文一致"""
    md = _render(f"{REPLY}\n\n{BODY}", roles=["reply"], reply_media=REPLY_MEDIA, hide_desc=True)
    assert "引用块" not in _layout(md)


# ── 回退：没声明时与改动前逐字一致 ─────────────────────────────────────

def test_without_roles_the_position_still_decides():
    """没声明 → 位置推断：开头块=reply、末尾块=quoted（改动前的行为）"""
    md = _render(
        f"{REPLY}\n\n{BODY}\n\n{QUOTED}",
        reply_media=REPLY_MEDIA,
        quoted_media=QUOTED_MEDIA,
    )
    assert _layout(md) == ["引用块", "图:r0(块内)", "正文", "图:b0", "引用块", "图:q0(块内)"], md


def test_without_roles_the_old_fallback_still_works():
    """**回归**（linux.do 的老形态）: 块在最前、媒体算在 quoted 段 ——

    没声明时靠"两个块互相兜底"（老缓存走的就是这条路径），必须原样保留。
    """
    md = _render(f"{QUOTED}\n\n{BODY}", quoted_media=QUOTED_MEDIA)
    assert _layout(md) == ["引用块", "图:q0(块内)", "正文", "图:b0"], md


def test_declared_and_undeclared_agree_when_the_shapes_match():
    """形态一致时，声明与不声明给出**同一个布局**（回退路径不跑偏）"""
    content = f"{REPLY}\n\n{BODY}\n\n{QUOTED}"
    declared = _render(content, roles=["reply", "quoted"], reply_media=REPLY_MEDIA, quoted_media=QUOTED_MEDIA)
    undeclared = _render(content, reply_media=REPLY_MEDIA, quoted_media=QUOTED_MEDIA)
    assert declared == undeclared


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
