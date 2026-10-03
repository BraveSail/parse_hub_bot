"""流水线: 富文本是否能跳过下载 (决定图片会不会被丢掉)。"""

from parsehub.types import PostType, RichTextParseResult

from services.pipeline import should_skip_richtext_download


class Article(RichTextParseResult):
    """图片内嵌在正文里 (知乎/微信这类文章)"""


class Attachment(RichTextParseResult):
    """图片被抽成 media 附件 (linux.do 这类帖子)"""

    requires_media_download = True


def test_article_richtext_skips_download():
    assert should_skip_richtext_download(Article(markdown_content="正文"), richtext_skip_download=True)


def test_attachment_richtext_downloads():
    """平台的图片在 media 里, 跳过下载等于把图片丢了"""
    assert not should_skip_richtext_download(Attachment(markdown_content="正文"), richtext_skip_download=True)


def test_switch_off_always_downloads():
    assert not should_skip_richtext_download(Article(), richtext_skip_download=False)


def test_non_richtext_is_unaffected():
    """非富文本结果本来就会下载, 这个判断不该插手"""
    from parsehub.types import ImageParseResult

    assert not should_skip_richtext_download(ImageParseResult(), richtext_skip_download=True)


def test_linuxdo_result_declares_attachment():
    """回归防线: linux.do 的结果类型必须声明需要下载"""
    from parsehub.parsers.parser.linuxdo import LinuxDoRichTextParseResult

    assert LinuxDoRichTextParseResult.requires_media_download is True
    assert LinuxDoRichTextParseResult.type == PostType.RICHTEXT
