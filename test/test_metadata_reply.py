"""页脚统计：bgm 的「状态」与各平台的「回复 / 评论」。

页脚形如「时间 · 1,455 查看 · 158 点赞 · 32 回复」。三点要钉住：

1. **bgm 的 like 位叫「状态」** —— 它没有"点赞"，表情回应（页面的 ``data_likes_list``）
   才是同一种正反馈，站点自己叫「状态」。其余平台仍是「点赞」。
2. **微博的 reply 位叫「评论」** —— 它站点 UI 上就是"评论"（``comments_count``），
   页脚跟着站点字样。其余平台仍是「回复」。
3. **回复数是通用项** —— 平台拿不到就**不显示**（不预留位置），与浏览量/点赞同一条原则。
"""

import types

from parsehub import Platform

from plugins.helpers import build_metadata_line, metadata_like_label, metadata_reply_label

STATE, REPLY, LIKE, COMMENT = "状态", "回复", "点赞", "评论"


def _translate(key: str) -> str:
    """假的翻译器：直接把词条原文回显（测试只关心渲染出来的字样）。"""
    return key


def _result(platform=Platform.BANGUMI, **kw):
    result = types.SimpleNamespace(platform=platform, reply_count=None, like_count=None, view_count=None)
    for k, v in kw.items():
        setattr(result, k, v)
    return result


# ── 点赞那一段的文案 ───────────────────────────────────────────────────

def test_bangumi_calls_it_state():
    """**核心**: bgm 的 like 位显示「状态」"""
    assert metadata_like_label(_result(Platform.BANGUMI), _translate) == STATE


def test_other_platforms_keep_likes():
    """其余平台不给自定义文案 ⇒ 渲染层走默认的「点赞」"""
    for platform in (Platform.TWITTER, Platform.LINUXDO, Platform.BILIBILI, Platform.THREADS):
        assert metadata_like_label(_result(platform), _translate) == "", platform


def test_a_missing_platform_keeps_likes():
    assert metadata_like_label(types.SimpleNamespace(), _translate) == ""


# ── 回复那一段的文案 ───────────────────────────────────────────────────

def test_weibo_calls_it_comments():
    """**核心**: 微博的 reply 位显示「评论」（站点 UI 的字样，不是「回复」）"""
    assert metadata_reply_label(_result(Platform.WEIBO), _translate) == COMMENT


def test_other_platforms_keep_replies():
    """其余平台不给自定义文案 ⇒ 渲染层走默认的「回复」"""
    for platform in (Platform.TWITTER, Platform.LINUXDO, Platform.BILIBILI, Platform.BANGUMI):
        assert metadata_reply_label(_result(platform), _translate) == "", platform


def test_a_missing_platform_keeps_replies():
    assert metadata_reply_label(types.SimpleNamespace(), _translate) == ""


def test_the_two_labels_do_not_bleed_into_each_other():
    """微博只换 reply 位、bgm 只换 like 位 —— 互不影响"""
    assert metadata_like_label(_result(Platform.WEIBO), _translate) == ""
    assert metadata_reply_label(_result(Platform.BANGUMI), _translate) == ""


def test_weibo_footer_renders_the_comment_label():
    """端到端形态：微博页脚是「… · 40 点赞 · 8 评论」"""
    line = build_metadata_line(
        like_count=40,
        reply_count=8,
        lang="zh-hans",
        view_label="查看",
        like_label=metadata_like_label(_result(Platform.WEIBO), _translate),
        reply_label=metadata_reply_label(_result(Platform.WEIBO), _translate),
    )
    assert line == "40 点赞 · 8 评论", line


# ── 页脚组装 ───────────────────────────────────────────────────────────

def test_all_three_counts_render():
    """查看 / 状态（like 位）/ 回复 三段都在，顺序固定"""
    line = build_metadata_line(
        view_count=1455,
        like_count=158,
        reply_count=32,
        lang="zh-hans",
        view_label="查看",
        like_label=STATE,
        reply_label=REPLY,
    )
    assert line == "1,455 查看 · 158 状态 · 32 回复", line


def test_a_missing_reply_count_is_skipped_entirely():
    """**核心**: 拿不到回复数就不显示那一段（不留空占位、不留分隔符）"""
    line = build_metadata_line(view_count=10, like_count=2, lang="zh-hans", view_label="查看", like_label=LIKE)
    assert line == "10 查看 · 2 点赞", line
    assert REPLY not in line


def test_only_the_reply_count_still_renders():
    """只有回复数（平台没有浏览/点赞）时也要能单独显示"""
    line = build_metadata_line(reply_count=8, lang="zh-hans", view_label="查看", reply_label=REPLY)
    assert line == "8 回复", line


def test_zero_is_shown_not_hidden():
    """**0 也要显示** —— "0 回复"是有信息量的（"没人回复"≠"拿不到"）

    判据是 ``None``（拿不到）与 ``0``（真的是 0）分开。
    """
    line = build_metadata_line(reply_count=0, lang="zh-hans", view_label="查看", reply_label=REPLY)
    assert line == "0 回复", line


def test_no_counts_at_all_means_no_line():
    """一个统计都没有时页脚不产出那一段"""
    assert build_metadata_line(lang="zh-hans", view_label="查看") == ""


def test_the_missing_label_falls_back_to_the_locale_entry():
    """没传 label 时按语言兜底（词条「回复」）—— 与既有的「查看」「点赞」同一条路径"""
    line = build_metadata_line(reply_count=3, lang="zh-hans", view_label="查看")
    assert line.endswith(f"3 {REPLY}"), line


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
