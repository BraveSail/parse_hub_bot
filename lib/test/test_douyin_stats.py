"""抖音的统计与作者主页: 点赞 / 观看 / 作者链接。

用户报「观看是0，点赞没有，作者主页链接也没有」—— 取原始响应后确认
**接口数据都在**，是解析层没取：

```
statistics = {"digg_count": 391, ..., "play_count": 0, ...}
author: sec_uid = "MS4wLjABAAAA…", uid = 71058463678
```

三处分别是: 点赞字段从没取; ``play_count`` 移动端固定 0 而代码照发"0 查看";
``PROFILE_URL_TEMPLATES`` 没有抖音 (抖音主页要用 ``sec_uid``, 数字 uid 打不开)。

用例跑的是**真实响应的最小子集**（`fixtures/douyin_video.json`，从线上响应抽取，
字段值未改动）—— 编一份 fixture 很容易与真实结构不符。
"""

import json
from pathlib import Path

from parsehub.parsers.parser.douyin import DouyinApiResult
from parsehub.types.platform import Platform
from parsehub.utils.helpers import profile_url

FIXTURE = Path(__file__).parent / "fixtures" / "douyin_video.json"
SEC_UID = "MS4wLjABAAAAA203AQ9dxu6_ftaFa0AjzsoW1L0SVWdIfzjr7Jv-Y_Y"


def _payload() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


# ── 点赞 ───────────────────────────────────────────────────────────────


def test_digg_count_becomes_like_count():
    """`statistics.digg_count` 就是点赞 —— 以前完全没取 (用户报"点赞没有")"""
    assert DouyinApiResult.parse(_payload()).like_count == 391


def test_missing_digg_count_is_none_not_zero():
    """拿不到就是 None (不显示那一段), 不能变成 0"""
    payload = _payload()
    payload["aweme_detail"]["statistics"] = {"play_count": 0}
    assert DouyinApiResult.parse(payload).like_count is None


# ── 观看 ───────────────────────────────────────────────────────────────


def test_zero_play_count_is_treated_as_missing():
    """移动端接口固定返回 ``play_count=0`` (这条真实样本就是 0) ——

    显示"0 查看"是误导, 按项目原则"拿不到就不显示那一段", 所以 0 与缺失一样当作没有。
    """
    assert DouyinApiResult.parse(_payload()).view_count is None


def test_a_real_play_count_is_kept():
    payload = _payload()
    payload["aweme_detail"]["statistics"] = {"digg_count": 1, "play_count": 12345}
    assert DouyinApiResult.parse(payload).view_count == 12345


def test_missing_statistics_does_not_break():
    payload = _payload()
    payload["aweme_detail"].pop("statistics")
    result = DouyinApiResult.parse(payload)
    assert result.view_count is None and result.like_count is None


# ── 作者主页 ───────────────────────────────────────────────────────────


def test_sec_uid_is_captured():
    assert DouyinApiResult.parse(_payload()).author_sec_uid == SEC_UID


def test_the_numeric_uid_is_captured_for_display():
    """用户选了数字 uid 当作者行的 ``@标识`` —— 抖音没有 @用户名,

    而 ``sec_uid`` 太长 (60+ 字符) 会拖满整行。
    """
    assert DouyinApiResult.parse(_payload()).author_uid == "71058463678"


def test_the_uid_is_the_handle_but_sec_uid_is_the_url():
    """两者分工: **uid 只用于展示, 主页地址仍用 sec_uid** (uid 打不开主页)"""
    from parsehub.parsers.parser.douyin import DouyinParser

    built = DouyinParser._build_video_result(DouyinApiResult.parse(_payload()))
    assert built.author_handle == "71058463678", "@标识 用数字 uid"
    assert built.author_url == f"https://www.douyin.com/user/{SEC_UID}", "主页仍用 sec_uid"


def test_douyin_profile_url_uses_sec_uid():
    """抖音主页要用 sec_uid; 数字 uid 打不开"""
    assert profile_url(Platform.DOUYIN, user_id=SEC_UID) == f"https://www.douyin.com/user/{SEC_UID}"


def test_profile_url_is_empty_without_sec_uid():
    """没有 sec_uid 时返回空串 (调用方退回纯文本), 不能拼半截链接"""
    assert profile_url(Platform.DOUYIN, user_id="") == ""


# ── 其它字段没被带坏 (真实样本回归) ────────────────────────────────────


def test_the_rest_of_the_parse_still_works():
    result = DouyinApiResult.parse(_payload())
    assert result.author_name == "青蜂侠"
    assert result.published_at is not None
    assert result.type.value == "video"
    assert result.video is not None, "视频地址仍要解析出来"
