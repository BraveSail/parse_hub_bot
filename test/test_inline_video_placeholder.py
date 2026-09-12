"""VideoRef 的 inline 占位结果必须用 Telegram 手头已有的稳定图做 photo_url。

若 photo_url 填平台 CDN 上的封面（thumb_url），Telegram 得先去外网抓那张图；
抓完之前，那条 inline 消息的 photo 还处于「处理中」状态，此时 bot 随后发起的
媒体替换会被静默丢弃（``edit_inline_media`` 仍然返回 True），用户表现为
「封面没加载完就点 → 永远停在静态图；等封面加载出来再点 → 替换成功」。

所以：photo_url 用默认的 telegra.ph 图（TG 自家 CDN，瞬时就绪），列表缩略图
（thumb_url）继续用平台真实封面。这两个断言合起来守住这个契约。
"""

import asyncio

from parsehub.types import MultimediaParseResult, VideoRef

from plugins.parse.inline import DEFAULT_PARSE_RESULT_THUMB_URL, build_inline_results
from repo.settings import SettingsConfig

COVER = "https://scontent-icn2-1.cdninstagram.com/cover.jpg"
VIDEO = "https://scontent-icn2-1.cdninstagram.com/video.mp4"


def _video_result(*, with_cover: bool) -> MultimediaParseResult:
    ref = VideoRef(
        url=VIDEO,
        thumb_url=COVER if with_cover else None,
        width=960,
        height=720,
    )
    result = MultimediaParseResult(title="Title", content="Body", media=[ref])
    result.raw_url = "https://www.threads.com/@example/post/abc"
    return result


def test_video_ref_placeholder_uses_telegram_hosted_photo_url() -> None:
    results = asyncio.run(build_inline_results(_video_result(with_cover=True), None, "en-us", SettingsConfig()))

    assert len(results) == 1
    photo = results[0]
    # 发送时用的图必须是 TG 手头已有的稳定资源，否则替换会与封面下载竞态。
    assert photo.photo_url == DEFAULT_PARSE_RESULT_THUMB_URL
    # 列表缩略图仍应是平台真实封面。
    assert photo.thumb_url == COVER
    # id 决定点击后走 on_chosen_inline_result 的 download_ 分支。
    assert photo.id == "download_0"


def test_video_ref_without_cover_keeps_stable_photo_url() -> None:
    results = asyncio.run(build_inline_results(_video_result(with_cover=False), None, "en-us", SettingsConfig()))

    assert results[0].photo_url == DEFAULT_PARSE_RESULT_THUMB_URL
    assert results[0].thumb_url == DEFAULT_PARSE_RESULT_THUMB_URL
