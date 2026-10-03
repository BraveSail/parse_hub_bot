from ...types.platform import Platform
from ..base.ytdlp import YtParser


class FacebookParse(YtParser):
    __platform__ = Platform.FACEBOOK
    __supported_type__ = ["视频"]
    # watch?v=<id> 里的 v 是定位视频所必需的, 不保留就会退化成 /watch 导致 yt-dlp 解析失败
    __match__ = r"^(http(s)?://)?.+facebook.com/(watch/?\?v|share/[v,r]|.+/videos/|reel/).*"
    __reserved_parameters__ = ["v"]


__all__ = ["FacebookParse"]
