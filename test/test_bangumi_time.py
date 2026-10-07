"""bgm 的页脚时间 —— 端到端: 页面上的时刻 → 页脚的 unix 时间戳。

用户报「时间好像有问题？多 8 小时」。根因是 bgm 的页面只写本地时间
(``2026-10-4 19:13``, 没有偏移), 而 ``to_datetime`` 默认把裸字符串当 UTC ——
于是页脚的 unix 时间戳早了 8 小时, **任何时区**的用户看到的时间都比原帖晚 8 小时。

证据: ``api.bgm.tv`` 返回的时间戳带偏移 (``"updated_at":"2026-10-07T01:18:56+08:00"``),
所以 bgm 的服务器时区就是 +08:00, 页面上的时间要按北京时间解释。

这里不看中间字段, 只比对**用户看到的那一行**: 页脚 ``<tg-time unix=…>`` 换算回北京时间,
必须与页面上的时刻一致。
"""

import re
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

from parsehub.provider_api.bangumi import BangumiBlog, BangumiTopic

from plugins.helpers import build_metadata_line

FIXTURES = Path(__file__).parent.parent / "lib" / "test" / "fixtures"
BLOG_FIXTURE = FIXTURES / "bangumi_blog_381120.html"
TOPIC_FIXTURE = FIXTURES / "bangumi_group_topic_472394.html"


def _unix(line: str) -> int:
    match = re.search(r'<tg-time unix="(\d+)"', line)
    assert match, f"页脚没有 tg-time 实体: {line!r}"
    return int(match.group(1))


def _beijing(ts: int) -> datetime:
    return datetime.fromtimestamp(ts, tz=timezone(timedelta(hours=8)))


def test_the_blog_footer_time_matches_the_page_clock():
    """**核心**: 页面写 ``2026-10-4 19:13`` → 页脚的 unix 换算回北京时间必须还是 19:13"""
    blog = BangumiBlog._from_html(BLOG_FIXTURE.read_text(encoding="utf-8"), "381120")
    line = build_metadata_line(published_at=blog.published_at, lang="zh-hans")
    clock = _beijing(_unix(line))
    assert (clock.year, clock.month, clock.day, clock.hour, clock.minute) == (2026, 10, 4, 19, 13)
    # 兜底文字（老客户端显示它）也要是同一时刻
    assert "2026年10月4日 19:13" in line, line
    # 按 UTC 解释会得到 19:13Z —— unix 早 8 小时, 那正是这次的 bug
    assert _unix(line) != int(datetime(2026, 10, 4, 19, 13, tzinfo=UTC).timestamp())


def test_the_topic_footer_time_matches_the_page_clock():
    """话题楼层同一个坑（页面 ``2026-10-7 00:24``）"""
    topic = BangumiTopic._from_html(
        TOPIC_FIXTURE.read_text(encoding="utf-8"), "472394", floor_id="4062141"
    )
    line = build_metadata_line(published_at=topic.published_at, lang="zh-hans")
    clock = _beijing(_unix(line))
    assert (clock.year, clock.month, clock.day, clock.hour, clock.minute) == (2026, 10, 7, 0, 24)
    assert "2026年10月7日 00:24" in line, line


def test_a_utc_platform_is_not_shifted():
    """**回归**: 带偏移的平台不受影响 —— 它们的字符串自己就有 tzinfo"""
    from parsehub.utils.helpers import to_datetime

    # Twitter/X 的格式自带 +0000
    parsed = to_datetime("Wed Oct 01 12:00:00 +0000 2025")
    line = build_metadata_line(published_at=parsed, lang="zh-hans")
    assert _beijing(_unix(line)) == datetime(2025, 10, 1, 20, 0, tzinfo=timezone(timedelta(hours=8)))
