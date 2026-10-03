"""bilibili: 作者主页要能拼出来 (B 站没有 @用户名, 用 UID)。"""

from parsehub.types import Platform
from parsehub.utils.helpers import profile_url


def test_bilibili_profile_url_uses_uid():
    assert profile_url(Platform.BILIBILI, user_id=12345) == "https://space.bilibili.com/12345"


def test_bilibili_profile_url_needs_an_id():
    """没有 UID 时不能编出一个坏地址"""
    assert profile_url(Platform.BILIBILI) == ""
    assert profile_url(Platform.BILIBILI, handle="someone") == ""
