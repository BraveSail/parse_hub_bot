"""inline 媒体计数 (判定结果项要不要挂键盘时用)。"""

from plugins.parse.inline import count_inline_media


def test_count_inline_media():
    assert count_inline_media(None) == 0
    assert count_inline_media([]) == 0
    assert count_inline_media(object()) == 1  # 单个媒体对象（非序列）
    assert count_inline_media(["a", "b", "c"]) == 3


def test_count_inline_media_treats_missing_as_zero():
    """结果没有媒体时必须是 0 —— 不能因为 to_list(None) 而算成 1"""
    assert count_inline_media(None) == 0
