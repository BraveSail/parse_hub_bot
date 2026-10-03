"""to_list: None 必须当空, 不能变成 [None]。

这个 helper 决定了"这个结果有几个媒体", 返回 [None] 会让下游
(跳过下载阈值、GIF 判断、媒体计数、封面准备) 全部算错一个。
"""

from utils.helpers import to_list


def test_none_becomes_empty():
    assert to_list(None) == []
    assert len(to_list(None)) == 0


def test_list_is_returned_as_is():
    assert to_list([1, 2]) == [1, 2]
    assert to_list([]) == []


def test_single_value_is_wrapped():
    assert to_list(1) == [1]


def test_tuple_is_kept():
    """元组也是 Sequence, 不该被包一层"""
    assert to_list((1, 2)) == (1, 2)


def test_string_is_not_split_into_characters():
    """字符串本身是 Sequence: 原样返回就好, 千万别拆成单个字符"""
    assert to_list("abc") == "abc"
    assert to_list("") == ""
