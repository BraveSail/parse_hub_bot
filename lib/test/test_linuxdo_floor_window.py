"""linux.do 楼层窗口：窗口外的上下文层怎么取回来。

用户报障原话：「https://linux.do/t/topic/2989140/24?u=libc.so.6 怎么没有主楼了？」
（另一条 ``/4`` 正常 —— 追问「为什么4楼可以？」）
随后用户质疑「去查discourse文档，我就不信没有querystring能筛选」—— **他是对的**。

**窗口规律**（真机实测 11 个楼层）::

    1 楼  → 1..20   含主楼
    4 楼  → 1..20   含主楼
    6 楼  → 1..20   含主楼
    7 楼  → 2..21   不含        ← 分界
    24 楼 → 19..38  不含
    30 楼 → 25..44  不含

⇒ 窗口是**固定 20 层**，起点 = ``max(1, target - 5)``（源码 ``lib/topic_view.rb`` 的
``filter_posts_near``：``posts_before = (@limit / 4).floor``，limit 默认 20 → 5）。

**querystring**（源码 ``app/controllers/topics_controller.rb`` 的 ``show`` 只收 ``page`` /
``post_number`` / ``username_filters`` / ``filter`` / ``show_deleted`` /
``replies_to_post_number`` / ``filter_upwards_post_id`` / ``filter_top_level_replies``，
外加 ``print``）::

    /t/<id>/24.json                  20 层  19..38   不含主楼
    /t/<id>/24.json?print=true       49 层  1..49    含主楼   ← print_chunk_size = 1000
    /t/<id>.json?page=2              20 层  21..40   含 24 楼（path 有楼层号时 post_number 优先）

⇒ ``print=true`` **一次**就拿到全帖（≤1000 层），比逐层补取省。

**根因**：`_context_quotes` 在窗口里 ``next((p for p in posts if post_number == 1), None)``
—— 找不到就**静默跳过**，于是"分享楼层时带上主楼"这条规则在靠后的楼层上悄悄失效。

fixture: ``linuxdo_floor_24.json`` = 24 楼的窗口（19..38，**不含主楼**）；
``linuxdo_floor_4.json`` = 含主楼的窗口（1..20），拿来当 print 的响应 / 逐层补取的响应。
"""

import asyncio
import json
from pathlib import Path

from parsehub.provider_api import linuxdo as linuxdo_mod
from parsehub.provider_api.linuxdo import LinuxDoTopic

FIXTURES = Path(__file__).parent / "fixtures"
TOPIC_ID = "2989140"


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def _window_24() -> dict:
    """第 24 楼的窗口（19..38）—— **不含主楼**"""
    return _fixture("linuxdo_floor_24.json")


def _window_with_opening() -> dict:
    """含主楼的窗口（1..20）—— 模拟 print 拿回的全帖 / 逐层补取回来的响应"""
    return _fixture("linuxdo_floor_4.json")


def _floors(payload: dict) -> list[int]:
    return [p.get("post_number") for p in (payload.get("post_stream") or {}).get("posts", [])]


# ---------------------------------------------------------------- 窗口规律（纯函数）


def test_a_late_floor_window_is_missing_the_opening_post():
    """**核心**: 24 楼的窗口里没有主楼 —— 这就是"没有主楼了"的来源"""
    payload = _window_24()
    assert 1 not in _floors(payload), "fixture 变了: 它应该是不含主楼的窗口"
    assert LinuxDoTopic._missing_context_floors(
        (payload.get("post_stream") or {}).get("posts"), wanted=24, reply_to=None
    ) == [1]


def test_an_early_floor_window_already_has_the_opening_post():
    """**核心**: 4 楼的窗口里本来就有主楼 —— 不需要任何请求（这就是"4楼可以"）"""
    posts = (_window_with_opening().get("post_stream") or {}).get("posts")
    assert 1 in [p.get("post_number") for p in posts]
    assert LinuxDoTopic._missing_context_floors(posts, wanted=4, reply_to=None) == []


def test_the_opening_post_is_not_requested_when_reading_it():
    """解析主楼本身时不该取主楼（它就在窗口里，而且不自我引用）"""
    posts = (_window_24().get("post_stream") or {}).get("posts")
    assert LinuxDoTopic._missing_context_floors(posts, wanted=1, reply_to=None) == []


def test_a_replied_floor_outside_the_window_is_also_missed():
    """被回复的层同样可能在窗口外 —— 两个都要，顺序是「主楼 → 被回复的层」"""
    posts = (_window_24().get("post_stream") or {}).get("posts")
    assert LinuxDoTopic._missing_context_floors(posts, wanted=24, reply_to=3) == [1, 3]


def test_a_replied_floor_inside_the_window_is_not_requested():
    """被回复的层已在窗口里就不取"""
    posts = (_window_24().get("post_stream") or {}).get("posts")
    inside = [p.get("post_number") for p in posts][2]
    assert LinuxDoTopic._missing_context_floors(posts, wanted=24, reply_to=inside) == [1]


def test_replying_to_the_opening_post_does_not_duplicate_it():
    """回复主楼时（reply_to=1）只算一次"""
    posts = (_window_24().get("post_stream") or {}).get("posts")
    assert LinuxDoTopic._missing_context_floors(posts, wanted=24, reply_to=1) == [1]


# ---------------------------------------------------------------- 取回（网络请求）


class _FakeResponse:
    def __init__(self, payload: dict, status: int = 200):
        self._payload = payload
        self.status_code = status

    def json(self) -> dict:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _FakeClient:
    """假的 ``http.AsyncClient``：记录请求过的 URL，按 URL 决定返回什么。

    - 带 ``print=true`` → 返回 ``print_payload``（默认：含主楼的窗口）
    - 其它 → 按 URL 末段的楼层号返回 ``floors`` 里配的响应
    """

    def __init__(
        self,
        *,
        print_payload: dict | None = None,
        floors: dict[int, dict] | None = None,
        print_status: int = 200,
        print_raises: bool = False,
        fail_floors: set[int] | None = None,
    ):
        self.print_payload = _window_with_opening() if print_payload is None else print_payload
        self.floors = floors or {}
        self.print_status = print_status
        self.print_raises = print_raises
        self.fail_floors = fail_floors or set()
        self.requested: list[str] = []

    def __call__(self, **_kwargs):  # AsyncClient(proxy=..., cookies=..., timeout=...)
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def get(self, url: str, **_kwargs) -> _FakeResponse:
        self.requested.append(url)
        if "print=true" in url:
            if self.print_raises:
                raise RuntimeError("print 请求炸了")
            return _FakeResponse(self.print_payload, self.print_status)
        floor = int(url.rstrip(".json").rsplit("/", 1)[-1])
        if floor in self.fail_floors:
            raise RuntimeError("网络炸了")
        return _FakeResponse(self.floors.get(floor, _window_with_opening()))

    def print_requests(self) -> list[str]:
        return [u for u in self.requested if "print=true" in u]

    def per_floor_requests(self) -> list[str]:
        return [u for u in self.requested if "print=true" not in u]


def _patch_client(monkeypatch, fake: _FakeClient) -> None:
    monkeypatch.setattr(linuxdo_mod.http, "AsyncClient", fake)


def _fill(payload: dict, post_number: str) -> dict:
    return asyncio.run(LinuxDoTopic._with_context_floors(payload, TOPIC_ID, post_number))


def test_the_missing_opening_post_costs_one_print_request(monkeypatch):
    """**核心**: 缺上下文时用 ``print=true`` **一次**拿全，而不是每层各请求一次"""
    fake = _FakeClient()
    _patch_client(monkeypatch, fake)

    merged = _fill(_window_24(), "24")

    assert len(fake.print_requests()) == 1, f"应该只用一次 print: {fake.requested}"
    assert fake.per_floor_requests() == [], f"print 够了就不该逐层请求: {fake.requested}"
    assert "/24.json" in fake.print_requests()[0], "应该请求**目标楼层**的 print 视图"
    floors = _floors(merged)
    assert 1 in floors, "主楼没合并进来"
    assert 24 in floors, "当前楼层丢了"
    assert floors == sorted(floors)


def test_nothing_is_requested_when_the_window_is_complete(monkeypatch):
    """**核心**: 窗口里已有主楼时零额外请求 —— 4 楼那种情况行为完全不变"""
    fake = _FakeClient()
    _patch_client(monkeypatch, fake)

    merged = _fill(_window_with_opening(), "4")

    assert fake.requested == [], f"不该发任何请求: {fake.requested}"
    assert _floors(merged) == _floors(_window_with_opening())


def test_overlapping_floors_are_deduplicated(monkeypatch):
    """print 的全帖与窗口会重叠 —— 按楼层去重，不出现重复的层"""
    fake = _FakeClient()
    _patch_client(monkeypatch, fake)

    floors = _floors(_fill(_window_24(), "24"))  # 19..38 与 1..20 重叠 19、20
    assert len(floors) == len(set(floors)), f"有重复楼层: {floors}"
    assert floors[0] == 1 and floors[-1] == 38


# ---------------------------------------------------------------- 兜底：print 不行时


def test_a_rejected_print_falls_back_to_per_floor_requests(monkeypatch):
    """print 被拒（站点关掉 print / 限流）→ 逐层补取兜底"""
    fake = _FakeClient(print_status=403, floors={1: _window_with_opening()})
    _patch_client(monkeypatch, fake)

    merged = _fill(_window_24(), "24")

    assert len(fake.print_requests()) == 1, "应该先试过 print"
    assert any("/1.json" in u for u in fake.per_floor_requests()), fake.requested
    assert 1 in _floors(merged), "逐层兜底没成功"


def test_an_exploding_print_falls_back_to_per_floor_requests(monkeypatch):
    """print 请求本身炸了（网络/超时）→ 同样逐层兜底，不抛错"""
    fake = _FakeClient(print_raises=True, floors={1: _window_with_opening()})
    _patch_client(monkeypatch, fake)

    merged = _fill(_window_24(), "24")
    assert 1 in _floors(merged)


def test_a_print_that_still_misses_a_floor_falls_back(monkeypatch):
    """print 只给前 1000 层 → 超过的楼层它也没有，逐层兜底补上（巨型话题的场合）"""
    fake = _FakeClient(print_payload=_window_24(), floors={1: _window_with_opening()})
    _patch_client(monkeypatch, fake)

    merged = _fill(_window_24(), "24")

    assert len(fake.print_requests()) == 1, "print 只试一次"
    assert 1 in _floors(merged), "print 没给主楼，逐层也没补上"
    assert any("/1.json" in u for u in fake.per_floor_requests()), fake.requested


def test_two_missing_floors_still_cost_one_request(monkeypatch):
    """主楼与被回复的层都缺 → 仍只用**一次** print（这是它相对逐层的好处）"""
    payload = _window_24()
    # 24 楼本身回复主楼（reply_to=None）。手工改成回复第 3 楼 —— 它也不在窗口里。
    for post in (payload.get("post_stream") or {}).get("posts") or []:
        if post.get("post_number") == 24:
            post["reply_to_post_number"] = 3
    fake = _FakeClient()
    _patch_client(monkeypatch, fake)

    merged = _fill(payload, "24")

    assert len(fake.requested) == 1, f"两个缺层也只该一次请求: {fake.requested}"
    assert _floors(merged) == sorted(_floors(merged))


def test_a_failed_fetch_does_not_break_the_parse(monkeypatch):
    """补不到不该让整条解析失败 —— 少一个上下文块，总好过整条打不开"""
    fake = _FakeClient(print_raises=True, fail_floors={1})
    _patch_client(monkeypatch, fake)

    merged = _fill(_window_24(), "24")
    assert 24 in _floors(merged), "原窗口的楼层应该原样保留"


# ---------------------------------------------------------------- 端到端（真实 fixture）


def test_the_floor_renders_with_the_opening_post_after_filling(monkeypatch):
    """**核心**: 补齐后第 24 楼渲染出**主楼引用块** + 自己的楼层号"""
    fake = _FakeClient()
    _patch_client(monkeypatch, fake)

    payload = _fill(_window_24(), "24")
    topic = LinuxDoTopic._from_payload(payload, TOPIC_ID, post_number="24")

    assert topic.post_number == 24
    assert topic.author_handle == "chunxiaoyi"
    markdown = topic.markdown_content
    assert "> " in markdown, f"没有主楼引用块:\n{markdown[:400]}"
    assert "· #1" in markdown, "引用块里没有主楼的楼层号"
    assert "KoaIa" in markdown, "引用块里不是主楼作者"


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
