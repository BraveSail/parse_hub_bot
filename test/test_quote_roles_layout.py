"""引用块的角色归位：显式声明之后，**位置不再参与**媒体的归属判断。

以前渲染层靠位置猜引用块的角色（开头→被回复、末尾→被引用），于是"正文里插一行"
就能让归位漂移 —— bgm 的归属行插在引用块前，引用块落到末尾，本层的图被插到它前面
贴住（用户报「图片和引用贴一起」）。现在角色由平台按出现顺序显式声明。

**回退路径**（没声明角色，例如老缓存）必须与引入 `quote_roles` 之前**逐字一致** ——
这里用「同一个产物 + 同一个媒体布局，声明与不声明跑出来的块结构相同」来钉住。
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
    """把产物压成"块顺序"：引用块 / 图（标注在块内还是块外）/ 正文。

    图要看它**在不在引用块里** —— ``> ![](...)`` 是块内（会被引用卡片吞下），
    裸 ``![](...)`` 是独立图片块。这一区别正是"图有没有掉出卡片"的判据。
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


# ── 显式声明：位置不再决定归属 ─────────────────────────────────────────

def test_a_leading_quote_can_take_the_quoted_media():
    """**核心**（bgm / linux.do 的形态）: 引用块在**最前**，但它的媒体在 ``quoted`` 段。

    以前这条路要靠"两个块互相兜底"才不丢图；现在按角色直接配对。
    """
    md = _render(f"{QUOTED}\n\n{BODY}", roles=["quoted"], quoted_media=QUOTED_MEDIA)
    assert _layout(md) == ["引用块", "图:q0(块内)", "正文", "图:b0"], md
    assert "> ![](tg://photo?id=q0)" in md, "引用里的图要在块内"


def test_a_trailing_quote_can_take_the_reply_media():
    """反过来也要成立：引用块在**末尾**、媒体在 ``reply`` 段"""
    md = _render(f"{BODY}\n\n{REPLY}", roles=["reply"], reply_media=REPLY_MEDIA)
    assert _layout(md) == ["正文", "图:b0", "引用块", "图:r0(块内)"], md
    assert "> ![](tg://photo?id=r0)" in md


def test_both_blocks_take_their_own_media():
    """两个块各拿自己那段（与位置恰好也一致 —— twitter 的形态）"""
    md = _render(
        f"{REPLY}\n\n{BODY}\n\n{QUOTED}",
        roles=["reply", "quoted"],
        reply_media=REPLY_MEDIA,
        quoted_media=QUOTED_MEDIA,
    )
    assert _layout(md) == ["引用块", "图:r0(块内)", "正文", "图:b0", "引用块", "图:q0(块内)"], md


def test_roles_are_not_swapped_by_position():
    """**核心**: 位置与角色**错位**时按角色走 —— 这正是以前做不到的

    （两个块都在时，位置推断必然给"头=reply、尾=quoted"；显式声明可以反过来）
    """
    md = _render(
        f"{REPLY}\n\n{BODY}\n\n{QUOTED}",
        roles=["quoted", "reply"],  # 与位置相反
        reply_media=REPLY_MEDIA,
        quoted_media=QUOTED_MEDIA,
    )
    # 头部块拿 quoted 段、末尾块拿 reply 段
    assert _layout(md) == ["引用块", "图:q0(块内)", "正文", "图:b0", "引用块", "图:r0(块内)"], md


def test_a_middle_line_does_not_disturb_the_layout():
    """**核心**: 正文里插了别的行（bgm 的归属行那种）也不影响归位。

    位置推断的脆弱点就在这里：插行会让引用块"看起来"不再在最前。
    """
    md = _render(
        f"{QUOTED}\n\n**小组** » 讨论\n\n{BODY}",
        roles=["quoted"],
        quoted_media=QUOTED_MEDIA,
    )
    assert "> ![](tg://photo?id=q0)" in md, "引用里的图仍要在块内"
    assert _layout(md)[:2] == ["引用块", "图:q0(块内)"], md


# ── 兜底：媒体不能丢 ───────────────────────────────────────────────────

def test_a_declared_role_without_its_block_keeps_the_media_in_the_body():
    """声明了 ``reply`` 但开头根本没有引用块 → 那几张图落到正文媒体（不丢）"""
    md = _render(BODY, roles=["reply"], reply_media=REPLY_MEDIA)
    assert "图:r0" in _layout(md), md


def test_more_blocks_than_declared_roles_falls_back_to_position():
    """块多于声明时，多出来的块按位置兜底（宁可保守，也别丢媒体）"""
    md = _render(
        f"{QUOTED}\n\n{BODY}\n\n{REPLY}",
        roles=["quoted"],  # 只声明了一个，但有两个块
        reply_media=REPLY_MEDIA,
        quoted_media=QUOTED_MEDIA,
    )
    # 头部块按声明拿 quoted 段；尾部（被回复）块从剩下**有媒体**的桶里拿 reply 段
    assert _layout(md) == ["引用块", "图:q0(块内)", "正文", "图:b0", "引用块", "图:r0(块内)"], md
    assert "图:r0(块内)" in _layout(md), "被回复块的图不能被扔到正文"


def test_hide_desc_still_drops_the_quote_cards():
    """``hide_desc`` 时引用块不渲染（既有语义），媒体也不发 —— 与正文一致"""
    md = _render(f"{QUOTED}\n\n{BODY}", roles=["quoted"], quoted_media=QUOTED_MEDIA, hide_desc=True)
    assert "引用块" not in _layout(md)
    assert "q0" not in md


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
    """**回归**（linux.do 的老形态）: 引用块在最前、媒体算在 quoted 段 ——

    没声明角色时靠"两个块互相兜底"，那条路径必须原样保留（老缓存走的就是它）。
    """
    md = _render(f"{QUOTED}\n\n{BODY}", quoted_media=QUOTED_MEDIA)
    assert _layout(md) == ["引用块", "图:q0(块内)", "正文", "图:b0"], md
    assert "> ![](tg://photo?id=q0)" in md, md


def test_declared_and_undeclared_agree_when_the_shapes_match():
    """形态一致时，声明与不声明必须给出**同一个布局**（回退路径不跑偏）"""
    content = f"{REPLY}\n\n{BODY}\n\n{QUOTED}"
    declared = _render(content, roles=["reply", "quoted"], reply_media=REPLY_MEDIA, quoted_media=QUOTED_MEDIA)
    undeclared = _render(content, reply_media=REPLY_MEDIA, quoted_media=QUOTED_MEDIA)
    assert declared == undeclared


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
