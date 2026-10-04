"""bilibili 动态: 转发的原动态 (item.orig) 要递归解析出来。

转发动态的主动态 major 为 None、正文是转发评论, 被转发的原动态整个在 orig 里
(可能是视频、图文、嵌套转发)。以前完全忽略 orig, 于是引用的媒体全丢。
"""

from parsehub.provider_api.bilibili import BiliDynamic


def _author(name="夏日幻听MCE", mid=224267770, pub_ts=1791049071):
    return {"name": name, "mid": mid, "pub_ts": pub_ts}


def _item(dynamic: dict, *, author=None, stat=None, orig=None, item_type="DYNAMIC_TYPE_DRAW"):
    item = {
        "id_str": "1",
        "type": item_type,
        "modules": {
            "module_author": author or _author(),
            "module_dynamic": dynamic,
        },
    }
    if stat is not None:
        item["modules"]["module_stat"] = {"like": {"count": stat}}
    if orig is not None:
        item["orig"] = orig
    return item


def _archive_major(bvid="BV1UqHi6uEie", title="「脑洞学生会！」第1话【中文字幕】", cover="http://i2.hdslb.com/x.jpg"):
    return {
        "type": "MAJOR_TYPE_ARCHIVE",
        "archive": {"bvid": bvid, "title": title, "cover": cover, "desc": "「脑洞学生会！」第1话"},
    }


# ── 非转发 (回归) ──────────────────────────────────────────────────────

def test_plain_dynamic_has_no_forward():
    d = BiliDynamic.parse({"item": _item({"desc": {"text": "普通动态"}, "major": None})})
    assert d.forward is None
    assert d.content == "普通动态"


def test_archive_dynamic_keeps_its_own_media():
    d = BiliDynamic.parse({"item": _item({"major": _archive_major()})})
    assert d.forward is None
    assert d.title == "「脑洞学生会！」第1话【中文字幕】"
    assert [i.url for i in (d.images or [])] == ["http://i2.hdslb.com/x.jpg"]


# ── 转发 ────────────────────────────────────────────────────────────────

def test_forward_is_parsed():
    """转发动态: 原动态要从 orig 里取出来"""
    item = _item(
        {"desc": {"text": "片头曲为… //@夏日幻听MCE:10月新番已更新！"}, "major": None},
        stat=346,
        item_type="DYNAMIC_TYPE_FORWARD",
        orig=_item({"major": _archive_major()}, item_type="DYNAMIC_TYPE_AV"),
    )
    d = BiliDynamic.parse({"item": item})

    assert d.forward is not None
    assert d.forward.title == "「脑洞学生会！」第1话【中文字幕】"
    assert [i.url for i in (d.forward.images or [])] == ["http://i2.hdslb.com/x.jpg"]
    # 主动态自己的字段不受影响
    assert d.like_count == 346
    assert d.images is None


def test_forward_carries_its_own_author():
    """被转发者的作者信息要单独留着 (引用块要署原作者的名)"""
    orig = _item({"major": _archive_major()}, author=_author(name="原作者", mid=999), item_type="DYNAMIC_TYPE_AV")
    d = BiliDynamic.parse({"item": _item({"major": None, "desc": {"text": "转发"}}, orig=orig)})
    assert d.author_name == "夏日幻听MCE"
    assert d.forward.author_name == "原作者"
    assert d.forward.author_mid == 999


def test_forward_with_opus_pictures():
    """原动态是图文时, 它的多张图也要取到"""
    opus = {
        "type": "MAJOR_TYPE_OPUS",
        "opus": {
            "title": "",
            "summary": {"text": "原文"},
            "pics": [
                {"url": "http://i0.hdslb.com/a.jpg", "live_url": None, "width": 1200, "height": 800},
                {"url": "http://i0.hdslb.com/b.jpg", "live_url": None, "width": 1200, "height": 800},
            ],
        },
    }
    orig = _item({"major": opus, "desc": None}, item_type="DYNAMIC_TYPE_DRAW")
    d = BiliDynamic.parse({"item": _item({"major": None, "desc": {"text": "转发"}}, orig=orig)})
    assert len(d.forward.images or []) == 2


def test_forward_without_media():
    """原动态没有媒体时 forward 照样要建出来 (只要文字)"""
    orig = _item({"desc": {"text": "原动态文字"}, "major": None})
    d = BiliDynamic.parse({"item": _item({"major": None, "desc": {"text": "转发"}}, orig=orig)})
    assert d.forward is not None
    assert d.forward.content == "原动态文字"
    assert not d.forward.images


def test_nested_forward():
    """嵌套转发 (orig 里还有 orig) 要递归下去"""
    inner = _item({"major": _archive_major(title="最内层视频")}, item_type="DYNAMIC_TYPE_AV")
    middle = _item({"desc": {"text": "中间层"}, "major": None}, orig=inner, item_type="DYNAMIC_TYPE_FORWARD")
    d = BiliDynamic.parse({"item": _item({"desc": {"text": "外层"}, "major": None}, orig=middle)})
    assert d.forward.forward is not None
    assert d.forward.forward.title == "最内层视频"


def test_broken_forward_does_not_break_the_main_dynamic():
    """原动态结构不认识时只丢弃 forward, 主动态照常返回"""
    broken = {
        "id_str": "2",
        "type": "DYNAMIC_TYPE_UNKNOWN",
        "modules": {"module_dynamic": {"major": {"type": "MAJOR_TYPE_NOPE"}}},
    }
    d = BiliDynamic.parse({"item": _item({"desc": {"text": "转发"}, "major": None}, orig=broken)})
    assert d.content == "转发"
    assert d.forward is None


# ── orig 空壳 -> 回退正文的 //@ 段 ─────────────────────────────────────
#
# 原动态被删/不可见时, 接口返回的 orig 是**空壳** (desc 空、major 是
# MAJOR_TYPE_NONE、author.mid=0)。以前这种情况会抛 "Unknown major type" 并被
# 静默吞掉, 引用块直接消失 (用户报「又没有引用了」)。B 站客户端此时靠正文里的
# `//@原作者:原文` 显示引用卡片, 我们也照做。


def _shell_orig():
    """真实抓包的空壳 orig 形状。"""
    return {
        "id_str": "0",
        "type": "DYNAMIC_TYPE_NONE",
        "visible": False,
        "modules": {
            "module_author": {"name": "", "mid": 0, "pub_ts": 0},
            "module_dynamic": {
                "desc": {"text": "", "rich_text_nodes": []},
                "major": {"type": "MAJOR_TYPE_NONE", "none": {}},
                "additional": None,
                "topic": None,
            },
            "module_stat": {},
        },
    }


def _forward_desc():
    """主动态正文: 转发评论 + ``//@原作者:原文`` (真实形状)。"""
    return {
        "text": "片头曲为#三月的Phantasia#演唱的。 \n\u200b//@夏日幻听MCE:10月新番《#脑洞学生会！#》第1话 已更新！",
        "rich_text_nodes": [
            {"type": "RICH_TEXT_NODE_TYPE_TEXT", "text": "片头曲为#三月的Phantasia#演唱的。 \n\u200b//"},
            {"type": "RICH_TEXT_NODE_TYPE_AT", "text": "@夏日幻听MCE", "rid": "224267770"},
            {"type": "RICH_TEXT_NODE_TYPE_TEXT", "text": ":10月新番《"},
            {"type": "RICH_TEXT_NODE_TYPE_TOPIC", "text": "#脑洞学生会！#"},
            {"type": "RICH_TEXT_NODE_TYPE_TEXT", "text": "》第1话 已更新！"},
        ],
    }


def test_shell_forward_falls_back_to_the_forward_comment():
    item = _item(
        {"desc": _forward_desc(), "major": None}, stat=439, item_type="DYNAMIC_TYPE_FORWARD", orig=_shell_orig()
    )
    d = BiliDynamic.parse({"item": item})

    assert d.forward is not None
    assert d.forward.author_name == "夏日幻听MCE"
    assert d.forward.author_mid == 224267770          # AT 节点的 rid, 引用块作者才能链主页
    assert "第1话 已更新" in d.forward.content
    assert "//@" not in d.forward.content             # 只留原文, 不带转发标记
    assert d.like_count == 439                        # 主动态字段不受影响


def test_missing_orig_also_falls_back():
    """接口连 orig 都不给时, 同样从正文还原"""
    d = BiliDynamic.parse(
        {"item": _item({"desc": _forward_desc(), "major": None}, item_type="DYNAMIC_TYPE_FORWARD")}
    )
    assert d.forward is not None
    assert d.forward.author_mid == 224267770


def test_no_forward_comment_means_no_forward():
    """既没有可用的 orig, 正文里也没有 //@ -> 确实没有转发"""
    d = BiliDynamic.parse({"item": _item({"desc": {"text": "普通动态"}, "major": None}, orig=_shell_orig())})
    assert d.forward is None


def test_unknown_major_type_degrades_instead_of_raising():
    """未知 major 类型 (含 MAJOR_TYPE_NONE) 不抛异常, 退回按 desc 文本解析"""
    orig = _item({"desc": {"text": "文字还在"}, "major": {"type": "MAJOR_TYPE_NONE", "none": {}}})
    d = BiliDynamic.parse({"item": _item({"desc": {"text": "转发"}, "major": None}, orig=orig)})
    assert d.forward is not None
    assert d.forward.content == "文字还在"
