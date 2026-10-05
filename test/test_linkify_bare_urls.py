"""裸 URL 链接化: 因为富文本发送关掉了服务端的实体自动识别。

背景（2026-10-05）: 作者行的 `@handle` 要**不可点击**, 现有 API 里唯一手段是整条消息
`skip_entity_detection=True`（关掉实体自动识别）—— 代价是正文里的裸 URL / `#标签`
不再自动变可点。所以自己把它们写成显式链接（实测该开关不影响显式 `<a href>`）。
"""

from plugins.helpers import linkify_bare_urls


def test_a_bare_url_becomes_a_link():
    out = linkify_bare_urls("看这个 https://example.com/a 挺好的")
    assert '<a href="https://example.com/a">https://example.com/a</a>' in out


def test_trailing_punctuation_stays_outside_the_link():
    out = linkify_bare_urls("见 https://example.com/a。")
    assert out.endswith("</a>。"), out

    out = linkify_bare_urls("seems https://example.com/a, right")
    assert '<a href="https://example.com/a">https://example.com/a</a>, right' in out


def test_an_existing_anchor_is_never_wrapped_twice():
    """已经写好的链接不能被再包一层 (那会把 href 写坏)"""
    src = '<a href="https://example.com/a">文字</a>'
    assert linkify_bare_urls(src) == src


def test_the_source_link_in_the_footer_is_untouched():
    """页脚的来源链接是显式锚点, 不能被重新链接化"""
    src = '正文\n\n---\n\n<footer><a href="https://x.com/u/status/1">来源（Twitter）</a></footer>'
    out = linkify_bare_urls(src)
    assert out.count("<a href=") == 1, out
    assert out == src


def test_a_markdown_link_target_is_not_wrapped():
    """`[文字](url)` 的目标部分已经有链接语义, 不能再包"""
    src = "[例子](https://example.com/a)"
    assert linkify_bare_urls(src) == src


def test_the_anchor_text_may_still_contain_a_url():
    """锚点内**显示**的 URL 也不该被包 —— 整段锚点都跳过"""
    src = '<a href="https://example.com/a">https://example.com/a</a>'
    assert linkify_bare_urls(src) == src


def test_text_without_urls_is_returned_unchanged():
    src = "没有链接的一段话 **粗体**"
    assert linkify_bare_urls(src) == src


def test_multiple_bare_urls_are_all_linked():
    out = linkify_bare_urls("a https://e.com/1 b https://e.com/2")
    assert out.count("<a href=") == 2, out


def test_the_skip_flag_is_set_on_every_rich_send():
    """盯住调用点: **每一处**构建 `InputRichMessage` 都要带 `skip_entity_detection=True`。

    这是**纯副作用**式的一行 (删掉后作者行的 @handle 会变回可点), 用 AST 校验
    (不数文本 —— 文档字符串里也有示例写法, 数文本会误判)。
    """
    import ast
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    checked = 0
    for rel in ("plugins/parse/sender.py", "plugins/parse/inline_rich.py"):
        tree = ast.parse((root / rel).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if getattr(node.func, "id", None) != "InputRichMessage":
                continue
            checked += 1
            kwargs = {kw.arg: kw.value for kw in node.keywords}
            assert "markdown" in kwargs or "blocks" in kwargs, f"{rel}:{node.lineno} 富文本没给内容"
            assert "skip_entity_detection" in kwargs, f"{rel}:{node.lineno} 少了 skip_entity_detection"
            assert kwargs["skip_entity_detection"].value is True, f"{rel}:{node.lineno} skip 必须是 True"
    assert checked >= 4, f"只检查到 {checked} 处, 断言没覆盖到位"
