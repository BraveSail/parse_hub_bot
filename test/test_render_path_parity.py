"""**两条渲染路径必须产出一致** —— 防「只把改动加到一条路上」的遗漏。

这类遗漏已经犯过三次（同一个渲染参数只加在现场解析路径、缓存路径没跟上），
症状一律是「**第一次发正常，第二次发同一条链接就不对**」——
因为缓存命中走的是另一条路。

| 路径 | 入口 | 数据来源 |
| --- | --- | --- |
| 现场解析 | `build_rich_markdown(parse_result)` | 刚解析出来的 ParseResult 对象 |
| 缓存命中 | `build_rich_markdown_by_str(...)` | 缓存里的**扁平字段**（`_RichFields`） |

**本文件是防线**：同一份内容经过两条路，渲染结果必须**逐字相同**。
新增任何依赖 ParseResult 字段的渲染逻辑时，这个测试会立刻报出来（而不是等用户第二次发）。

（本次实锤：`_RichFields.platform` 被写死成 None ⇒ 缓存路径的 `link_hashtags` 拿不到
标签页 URL ⇒ 标签静默退回纯文本。现场路径有 platform 所以正常。）
"""

import types

from parsehub.types import Platform

from plugins.helpers import build_rich_markdown, build_rich_markdown_by_str

#: 覆盖几处**依赖 ParseResult 字段**的渲染：标签页链接（platform）、
#: 作者行（author_*）、页脚统计（view/like）、正文折叠（content 长度）。
CONTENT = (
    "SOS団合同お誕生日会のご案内です。\n\n"
    "本文はそこそこ長くして折り畳みも通します。" + "あ" * 520 + "\n\n"
    "#AstraeOratio #アストラエオラティオ #アスオラ"
)


def _parse_result(**overrides):
    fields = {
        "title": "タイトル",
        "content": CONTENT,
        "raw_url": "https://x.com/Asora_JP/status/2107304853395439714",
        "author_name": "【公式】アストラエ・オラティオ",
        "author_handle": "Asora_JP",
        "author_url": "https://x.com/Asora_JP",
        "published_at": None,
        "view_count": 1234,
        "like_count": 56,
        "tags": ["AstraeOratio", "アスオラ"],
        "platform": Platform.TWITTER,
    }
    fields.update(overrides)
    return types.SimpleNamespace(**fields)


def _config():
    return types.SimpleNamespace(hide_title=False, hide_desc=False, hide_source=False)


def _live(pr) -> str:
    """现场解析路径。"""
    return build_rich_markdown(pr, config=_config(), lang="zh-hans", view_label="查看")


def _cached(pr) -> str:
    """缓存命中路径（同 build_cached_rich_content 的字段转发）。"""
    return build_rich_markdown_by_str(
        pr.title,
        pr.content,
        pr.raw_url,
        config=_config(),
        lang="zh-hans",
        view_label="查看",
        author_name=pr.author_name,
        author_handle=pr.author_handle,
        author_url=pr.author_url,
        published_at=pr.published_at,
        view_count=pr.view_count,
        like_count=pr.like_count,
        tags=pr.tags,
        platform=pr.platform,
    )


def test_both_paths_render_identically():
    """核心防线: 两条路径逐字一致"""
    pr = _parse_result()
    live, cached = _live(pr), _cached(pr)
    if live != cached:
        # 给出最直观的差异位置, 便于定位
        for i, (a, b) in enumerate(zip(live, cached, strict=False)):
            if a != b:
                print(f"首个差异在 {i}: 现场={live[max(0, i - 60):i + 60]!r} 缓存={cached[max(0, i - 60):i + 60]!r}")
                break
    assert live == cached, "现场解析与缓存命中的渲染结果必须一致"


def test_hashtags_are_linked_on_both_paths():
    """标签必须**两条路都可点**（platform 漏传时缓存路径会静默退回纯文本）"""
    pr = _parse_result()
    for label, md in (("现场", _live(pr)), ("缓存", _cached(pr))):
        assert '<a href="https://x.com/hashtag/AstraeOratio">#AstraeOratio</a>' in md, f"{label} 的标签没链接化"
        assert '"https://x.com/hashtag/' in md, label


def test_platform_is_actually_used_by_the_renderer():
    """证明 platform 确实是渲染依赖项: 传 None 时标签**不链接**（这就是那个静默降级）"""
    md = build_rich_markdown_by_str(
        "", "#tag 内容", "https://x.com/a/status/1", config=_config(), lang="zh-hans", platform=None
    )
    assert "<a href=" not in md.split("<footer>")[0], md
    assert "#tag" in md, "文字还在, 只是不可点"


def test_cached_path_keeps_the_author_and_footer_fields():
    """其余依赖字段同样要一致（author_* / view / like）"""
    pr = _parse_result()
    cached = _cached(pr)
    assert "Asora_JP" in cached
    assert "1,234" in cached or "1234" in cached
    assert "56" in cached


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
