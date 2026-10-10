"""微博页脚统计：发布时间 / 点赞 / 评论 / 视频播放量（全部来自同一次响应）。

实测（2026-10-10，161 容器）：

| 数据 | 字段 | 形态 |
| --- | --- | --- |
| 发布时间 | ``created_at`` | ``"Sat Oct 10 15:17:21 +0800 2026"`` |
| 点赞 | ``attitudes_count`` | 整数或字符串 |
| 评论 | ``comments_count`` | 整数或字符串 |
| 视频播放量 | ``page_info.media_info.online_users_number`` | **精确整数**（字段名是微博的历史遗留）|
| 视频播放量（TV 接口）| ``play_count`` | **中文缩写** ``"8.1万"`` |

⚠️ **找不到**：图微博的浏览量（``reads_count`` 恒空，平台只对博主可见）→ 不接。

``online_users_number`` 的语义是**播放量**不是"当前在线"：与 TV 接口的 ``play_count``
对照 6 个样本全部吻合（81818 vs "8.1万"、8338 vs "8,340"、……）。
"""

import asyncio
from pathlib import Path

import pytest

from parsehub.parsers.parser.weibo import WeiboParser
from parsehub.provider_api.weibo import WeiboAPI, WeiboContent, WeiboTVContent, parse_weibo_count

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> dict:
    import json

    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _fake_statuses_show(body: dict):
    async def fake(self, bid):  # noqa: ANN001, ARG001
        return body

    return fake


# ── 中文缩写计数 ───────────────────────────────────────────────────────

@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("8.1万", 81000),
        ("8,340", 8340),
        ("1.2亿", 120000000),
        ("31万", 310000),
        ("40.8万", 408000),
        ("81818", 81818),
        (81818, 81818),
        ("0", 0),
        (None, None),
        ("", None),
        ("暂无", None),
    ],
)
def test_parse_weibo_count(raw, expected):
    """TV 接口给的是**中文缩写**（``"8.1万"``），要能转回整数。"""
    assert parse_weibo_count(raw) == expected


# ── provider：Data 的页脚字段 ──────────────────────────────────────────

def test_image_post_exposes_footer_fields():
    """真实图微博 fixture：时间 / 点赞 / 评论都能归一化出来。"""
    body = _load("weibo_image_post.json")
    data = WeiboContent.parse(body).data

    assert data.created_at == "Sat Oct 10 15:17:21 +0800 2026"
    assert data.published_at is not None
    assert data.published_at.year == 2026 and data.published_at.month == 10
    assert data.like_count == body["attitudes_count"]
    assert data.reply_count == body["comments_count"]
    # 图微博**没有**播放量可拿（平台只对博主可见）
    assert data.page_info is None or data.page_info.media_info is None


def test_video_post_exposes_the_play_count():
    """视频微博：播放量在 ``media_info.online_users_number``（字段名误导，实测是播放量）。"""
    body = _load("weibo_video_post.json")
    data = WeiboContent.parse(body).data

    media_info = data.page_info.media_info
    assert media_info.online_users_number == 81818
    assert media_info.play_count == 81818


def test_tv_content_exposes_the_abbreviated_play_count():
    """TV 接口（video.weibo.com/show）：``play_count`` 是中文缩写，``real_date`` 是时间。"""
    body = _load("weibo_tv_post.json")
    tv = WeiboTVContent.parse(body)

    assert tv.play_count == 81000, tv.play_count
    assert tv.published_at is not None and tv.published_at.year == 2026
    assert tv.like_count == 63
    assert tv.reply_count == 2


def test_footer_fields_degrade_to_none():
    """字段缺失时归一化成 None（渲染层跳过那一段），**绝不抛错**。"""
    data = WeiboContent.parse({"id": "1", "mid": "1", "text_raw": "x"}).data
    assert data.published_at is None
    assert data.like_count is None
    assert data.reply_count is None


# ── parser：三个构造点都要带上页脚字段 ─────────────────────────────────

def test_image_parse_carries_the_footer(monkeypatch):
    body = _load("weibo_image_post.json")
    monkeypatch.setattr(WeiboAPI, "statuses_show", _fake_statuses_show(body))

    result = asyncio.run(WeiboParser().parse("https://weibo.com/1886672467/Rm1w5iaAD"))

    assert result.published_at is not None
    assert result.like_count == body["attitudes_count"]
    assert result.reply_count == body["comments_count"]
    assert result.view_count is None, "图微博不该有播放量（拿不到就不显示）"


def test_video_parse_carries_the_play_count(monkeypatch):
    body = _load("weibo_video_post.json")
    monkeypatch.setattr(WeiboAPI, "statuses_show", _fake_statuses_show(body))

    result = asyncio.run(WeiboParser().parse("https://weibo.com/1135787567/RhvyqEuTH"))

    assert result.view_count == 81818
    assert result.like_count == body["attitudes_count"]
    assert result.reply_count == body["comments_count"]


def test_tv_parse_carries_the_footer(monkeypatch):
    """TV 分支（video.weibo.com 或 /tv/show）与详情 API 分支是两个不同的构造点。"""
    body = _load("weibo_tv_post.json")
    tv = WeiboTVContent.parse(body)

    async def fake_parse(self, url):  # noqa: ANN001, ANN202, ARG001
        return tv

    monkeypatch.setattr(WeiboAPI, "parse", fake_parse)
    result = asyncio.run(WeiboParser()._do_parse("https://weibo.com/tv/show/1034:5341728635551801"))

    assert result.view_count == 81000, "TV 的中文缩写播放量要转成整数"
    assert result.like_count == 63
    assert result.reply_count == 2
    assert result.published_at is not None


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
