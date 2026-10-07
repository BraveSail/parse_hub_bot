"""图片与引用卡片之间要有间隔（用户报「图片和引用贴在一起」）。

用**分割线**（``_QUOTE_GAP = "---"``，实测产出独立的 ``Divider`` 块）。
曾经试过全角空格段：也能产出独立段落，但**太宽**（用户「这个不行太宽了，试试分割线」）。

⚠️ 空行与半角空格段都会被服务端**折叠**（实测块结构与不加时完全相同）——
间距会静默消失，改动时别退回这两种写法。

两个方向都要：

- 卡片在前、图片在后（bgm 的纯图楼层：上下文卡片 + 本层的图）
- 图片在前、卡片在后（twitter：主帖的图 + 末尾的引用卡片）

**卡片内部的图不参与** —— ``> ![](…)`` 是卡片的一部分，和卡片贴在一起才对
（加了间距就掉出卡片）。
"""

import types

from plugins.helpers import build_rich_markdown

GAP = "---"

REPLY = "> <i>回复块的作者</i>\n> <i>回复正文</i>"
QUOTED = "> <i>引用块的作者</i>\n> <i>引用正文</i>"
BODY = "本帖正文"

BODY_MEDIA = ("![](tg://photo?id=b0)",)
REPLY_MEDIA = ("![](tg://photo?id=r0)",)
QUOTED_MEDIA = ("![](tg://photo?id=q0)",)


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


def _render(content, *, roles=None, reply_media=(), quoted_media=(), body_media=BODY_MEDIA):
    return build_rich_markdown(
        _result(content=content, quote_roles=roles),
        config=_Cfg(),
        media_placeholders=list(body_media),
        quote_media_placeholders=list(quoted_media),
        reply_media_placeholders=list(reply_media),
    )


def _blocks(md: str) -> list[str]:
    """按空行切段，忽略段内换行 —— 段的顺序就是块顺序。

    （分割线本身就是有内容的段，``strip()`` 判空不受影响；当初用全角空格做间距时
    这里必须特判 —— ``str.strip()`` 会抹掉全角空格、看起来像空段。）
    """
    return [seg.strip() for seg in md.split("\n\n") if seg.strip()]


def _gap_positions(md: str) -> list[int]:
    """**间距**分割线的下标 —— 只数"卡片与媒体相邻"的那些。

    ⚠️ 不能数全局的 ``---``：作者行与内容之间本来就有一条同样的分割线
    （``_meta_divider``），那是元信息分区，不是这里的间距。
    """
    blocks = _blocks(md)
    out: list[int] = []
    for i, seg in enumerate(blocks):
        if seg != GAP or i == 0 or i + 1 >= len(blocks):
            continue
        before, after = blocks[i - 1], blocks[i + 1]
        if (_is_card(before) and _is_media(after)) or (_is_media(before) and _is_card(after)):
            out.append(i)
    return out


def _is_card(seg: str) -> bool:
    head = seg.lstrip()
    return head.startswith(">") or head.startswith("<blockquote")


def _is_media(seg: str) -> bool:
    head = seg.lstrip()
    return head.startswith("![]") or head.startswith("<tg-collage>")


# ── 卡片在前、图片在后（bgm 纯图楼层） ─────────────────────────────────

def test_a_picture_after_the_card_gets_a_gap():
    """**核心**（bgm 纯图楼层的形态）: 正文为空 ⇒ 卡片后面紧跟本层的图，中间要空一行"""
    md = _render(REPLY, roles=["reply"], reply_media=REPLY_MEDIA)
    blocks = _blocks(md)
    idx = next(i for i, seg in enumerate(blocks) if seg.startswith("> "))
    assert blocks[idx].startswith("> <i>回复块的作者"), blocks
    assert blocks[idx + 1] == GAP, blocks
    assert blocks[idx + 2] == BODY_MEDIA[0], blocks


def test_the_gap_is_exactly_one_segment():
    """只插一段间距，不堆叠"""
    md = _render(REPLY, roles=["reply"], reply_media=REPLY_MEDIA)
    assert len(_gap_positions(md)) == 1, _blocks(md)


# ── 图片在前、卡片在后（twitter） ──────────────────────────────────────

def test_a_picture_before_the_card_gets_a_gap():
    """**核心**（twitter 的形态）: 主帖的图在引用卡片**上面**，中间也要空一行"""
    md = _render(f"{BODY}\n\n{QUOTED}", roles=["quoted"], quoted_media=QUOTED_MEDIA)
    blocks = _blocks(md)
    # 作者行 → 分隔线 → 正文 → 正文媒体 → 间距 → 卡片
    body_idx = blocks.index(BODY)
    assert blocks[body_idx + 1] == BODY_MEDIA[0], blocks
    assert blocks[body_idx + 2] == GAP, blocks
    assert blocks[body_idx + 3].startswith("> <i>引用块的作者"), blocks


# ── 卡片内部的图不受影响 ───────────────────────────────────────────────

def test_a_picture_inside_the_card_stays_glued():
    """**核心**: 卡片**内部**的图（``> ![]``）与卡片贴在一起才对 —— 不能加间距

    （加了它就掉出卡片，变成卡片外面的独立图片块。）

    ⚠️ 这个场景要**没有正文媒体**才验得准 —— 卡片外面还有图时，那段间距是给
    外面那张图用的，与卡内图无关。
    """
    md = _render(QUOTED, roles=["quoted"], quoted_media=QUOTED_MEDIA, body_media=())
    assert f"> {QUOTED_MEDIA[0]}" in md, md
    assert _gap_positions(md) == [], "卡片内部有图时不该再插间距"


def test_no_picture_means_no_gap():
    """**回归**: 没有图片时产物不变（不产生多余的空段）"""
    without = _render(f"{REPLY}\n\n{BODY}", roles=["reply"], body_media=())
    assert _gap_positions(without) == [], _blocks(without)
    assert BODY in without and "> <i>回复块的作者" in without


def test_a_card_next_to_text_gets_no_gap():
    """卡片旁边只是**文字**（不是图片）时不插间距 —— 用户要的是"图片和引用"分开"""
    md = _render(f"{REPLY}\n\n{BODY}", roles=["reply"], body_media=())
    assert _gap_positions(md) == [], _blocks(md)
    assert BODY in md


def test_two_cards_and_two_pictures_each_get_their_gap():
    """两个卡片、两张图时各在自己的相邻处插一段（不多不少）"""
    # 卡片与卡片不相邻（中间隔着正文）→ 没有间距
    md = _render(
        f"{REPLY}\n\n{BODY}\n\n{QUOTED}",
        roles=["reply", "quoted"],
        reply_media=(),
        quoted_media=(),
        body_media=(),
    )
    assert len(_gap_positions(md)) == 0, _blocks(md)

    md2 = _render(
        f"{REPLY}\n\n{BODY}\n\n{QUOTED}",
        roles=["reply", "quoted"],
        reply_media=REPLY_MEDIA,
        quoted_media=QUOTED_MEDIA,
    )
    # 头卡片（含它自己的图）→ 间距 → 正文 → 正文图 → 间距 → 尾卡片（含图）……
    blocks = _blocks(md2)
    assert blocks.count(GAP) >= 1, blocks


# ── 回退路径（没声明角色）也一样 ───────────────────────────────────────

def test_the_undeclared_path_gets_the_gap_too():
    """位置推断那条路径（老缓存 / 未改的平台）同样要有间距 —— 两套路径观感要一致"""
    md = _render(REPLY, quoted_media=QUOTED_MEDIA)
    blocks = _blocks(md)
    # 这个形态下卡片落到末尾（媒体算在 quoted 段）⇒ 间距在**卡片之前**
    idx = next(i for i, seg in enumerate(blocks) if seg.startswith("> "))
    assert blocks[idx - 1] == GAP, blocks
    assert blocks[idx - 2] == BODY_MEDIA[0], blocks


def test_the_undeclared_path_does_not_gap_distant_things():
    """**回归**: 卡片与图不相邻（中间隔着正文）→ 位置路径也不该插间距"""
    md = _render(f"{QUOTED}\n\n{BODY}", quoted_media=QUOTED_MEDIA)
    assert _gap_positions(md) == [], _blocks(md)


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
