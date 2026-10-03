"""bilibili 动态: 作者 UID 与互动数都要取到。

动态接口的 JSON 里 module_author 有 mid/pub_ts, module_stat.like 有 count。
之前只取了作者名, 于是动态的作者不可点、也没有统计行。
"""

from parsehub.provider_api.bilibili import BiliDynamic


def _dynamic(**author_overrides) -> dict:
    author = {"name": "夏日幻听MCE", "mid": 224267770, "pub_ts": 1791049071}
    author.update(author_overrides)
    return {
        "item": {
            "modules": {
                "module_author": author,
                "module_stat": {"like": {"count": 254}, "comment": {"count": 3}},
                "module_dynamic": {
                    "desc": {"text": "正文"},
                    "major": {
                        "type": "MAJOR_TYPE_OPUS",
                        "opus": {"title": "", "summary": {"text": "正文"}, "pics": []},
                    },
                },
            }
        }
    }


def test_dynamic_reads_author_mid_and_stats():
    d = BiliDynamic.parse(_dynamic())
    assert d.author_name == "夏日幻听MCE"
    assert d.author_mid == 224267770
    assert d.published_at == 1791049071
    assert d.like_count == 254


def test_dynamic_tolerates_missing_mid_and_stats():
    """接口字段缺失时不能炸, 取到多少算多少 (统计行会据此省略对应段落)"""
    d = BiliDynamic.parse(_dynamic(mid=None, pub_ts=None, name="某人"))
    d.like_count = None
    assert d.author_mid is None
    assert d.published_at is None
    assert d.author_name == "某人"


def test_dynamic_without_stat_module():
    data = _dynamic()
    data["item"]["modules"].pop("module_stat")
    d = BiliDynamic.parse(data)
    assert d.like_count is None
    assert d.author_mid == 224267770
