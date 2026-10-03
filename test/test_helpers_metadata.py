"""平台时间/计数归一化 helper 的用例 (各平台原始格式对标真实响应)."""

from datetime import UTC, datetime, timedelta, timezone

import pytest

from parsehub.utils.helpers import to_datetime, to_int


class TestToDatetime:
    def test_none(self):
        assert to_datetime(None) is None

    def test_empty_string(self):
        assert to_datetime("") is None

    def test_garbage(self):
        assert to_datetime("昨天") is None

    def test_bool_is_not_a_timestamp(self):
        assert to_datetime(True) is None
        assert to_datetime(False) is None

    def test_unix_seconds(self):
        """bilibili data.View.pubdate / facebook yt-dlp timestamp"""
        assert to_datetime(1759406400) == datetime(2025, 10, 2, 12, 0, tzinfo=UTC)

    def test_unix_milliseconds(self):
        """部分平台给毫秒"""
        assert to_datetime(1759406400000) == datetime(2025, 10, 2, 12, 0, tzinfo=UTC)

    def test_numeric_string(self):
        assert to_datetime("1759406400") == datetime(2025, 10, 2, 12, 0, tzinfo=UTC)

    def test_iso_with_offset(self):
        """pixiv createDate: '2025-10-02T21:00:00+09:00'"""
        parsed = to_datetime("2025-10-02T21:00:00+09:00")
        assert parsed == datetime(2025, 10, 2, 12, 0, tzinfo=UTC)
        assert parsed.utcoffset() == timedelta(hours=9)

    def test_iso_with_z(self):
        assert to_datetime("2025-10-02T12:00:00Z") == datetime(2025, 10, 2, 12, 0, tzinfo=UTC)

    def test_naive_iso_is_treated_as_utc(self):
        assert to_datetime("2025-10-02 12:00:00") == datetime(2025, 10, 2, 12, 0, tzinfo=UTC)

    def test_twitter_format(self):
        """twitter legacy.created_at: 'Wed Oct 01 12:00:00 +0000 2025'"""
        assert to_datetime("Wed Oct 01 12:00:00 +0000 2025") == datetime(2025, 10, 1, 12, 0, tzinfo=UTC)

    def test_date_only(self):
        assert to_datetime("2025-10-02") == datetime(2025, 10, 2, tzinfo=UTC)

    def test_datetime_passthrough(self):
        aware = datetime(2025, 10, 2, 12, 0, tzinfo=timezone(timedelta(hours=8)))
        assert to_datetime(aware) is aware

    def test_naive_datetime_gets_utc(self):
        parsed = to_datetime(datetime(2025, 10, 2, 12, 0))
        assert parsed == datetime(2025, 10, 2, 12, 0, tzinfo=UTC)

    @pytest.mark.parametrize("value", [0, -1, "0"])
    def test_zero_and_negative_are_dropped(self, value):
        assert to_datetime(value) is None

    def test_absurd_timestamp_does_not_raise(self):
        assert to_datetime(10**30) is None


class TestToInt:
    def test_none(self):
        assert to_int(None) is None

    def test_int(self):
        assert to_int(1455) == 1455

    def test_numeric_string(self):
        """twitter views.count 是字符串"""
        assert to_int("24574") == 24574

    def test_thousand_separator(self):
        assert to_int("1,455") == 1455

    def test_garbage(self):
        assert to_int("暂无") is None

    def test_bool_is_not_a_count(self):
        assert to_int(True) is None
