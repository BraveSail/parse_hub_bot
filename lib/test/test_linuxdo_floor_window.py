"""linux.do 楼层窗口：窗口外的上下文层要**补取**。

用户报障原话：「https://linux.do/t/topic/2989140/24?u=libc.so.6 怎么没有主楼了？」
（另一条 ``/4`` 正常 —— 追问「为什么4楼可以？」）

**窗口规律**（同一话题真机实测 11 个楼层）::

    1 楼  → 1..20   含主楼
    4 楼  → 1..20   含主楼
    6 楼  → 1..20   含主楼
    7 楼  → 2..21   不含        ← 分界
    10 楼 → 5..24   不含
    24 楼 → 19..38  不含
    30 楼 → 25..44  不含

⇒ Discourse 的楼层窗口是**固定 20 层**，起点 = ``max(1, target - 5)``。
target ≤ 6 时起点被夹到 1，主楼恰好还在窗口里；**target ≥ 7 主楼就落在窗口外**。

**根因**：`_context_quotes` 在窗口里 ``next((p for p in posts if post_number == 1), None)``
—— 找不到就**静默跳过**，不报错。于是"分享楼层时带上主楼"这条规则在靠后的楼层上悄悄失效。

**补取可行性**（实测）: ``/t/<id>.json`` 与 ``/t/<id>/1.json`` 都返回 ``1..20``（含主楼）。

fixture: ``linuxdo_floor_24.json`` = 24 楼的真实响应（19..38，不含主楼）；
``linuxdo_floor_4.json`` = 含主楼的真实窗口（1..20），拿来当补取的响应。
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
    """含主楼的窗口（1..20）—— 模拟补取回来的响应"""
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
    """**核心**: 4 楼的窗口里本来就有主楼 —— 不需要任何补取（这就是"4楼可以"）"""
    posts = (_window_with_opening().get("post_stream") or {}).get("posts")
    assert 1 in [p.get("post_number") for p in posts]
    assert LinuxDoTopic._missing_context_floors(posts, wanted=4, reply_to=None) == []


def test_the_opening_post_is_not_requested_when_reading_it():
    """解析主楼本身时不该补取主楼（它就在窗口里，而且不自我引用）"""
    posts = (_window_24().get("post_stream") or {}).get("posts")
    assert LinuxDoTopic._missing_context_floors(posts, wanted=1, reply_to=None) == []


def test_a_replied_floor_outside_the_window_is_also_missed():
    """被回复的层同样可能在窗口外 —— 两个都要补，顺序是「主楼 → 被回复的层」"""
    posts = (_window_24().get("post_stream") or {}).get("posts")
    assert LinuxDoTopic._missing_context_floors(posts, wanted=24, reply_to=3) == [1, 3]


def test_a_replied_floor_inside_the_window_is_not_requested():
    """被回复的层已在窗口里就不补"""
    posts = (_window_24().get("post_stream") or {}).get("posts")
    numbers = [p.get("post_number") for p in posts]
    inside = numbers[2]  # 窗口内的某一层
    assert LinuxDoTopic._missing_context_floors(posts, wanted=24, reply_to=inside) == [1]


def test_replying_to_the_opening_post_does_not_duplicate_it():
    """回复主楼时（reply_to=1）只补一次"""
    posts = (_window_24().get("post_stream") or {}).get("posts")
    assert LinuxDoTopic._missing_context_floors(posts, wanted=24, reply_to=1) == [1]


# ---------------------------------------------------------------- 补取（网络请求）


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
    """假的 ``http.AsyncClient``: 记录请求过的 URL, 按楼层返回窗口。"""

    def __init__(self, windows: dict[int, dict], *, fail: set[int] | None = None):
        self.windows = windows
        self.fail = fail or set()
        self.requested: list[str] = []

    def __call__(self, **_kwargs):  # 当 AsyncClient(proxy=..., cookies=..., timeout=...) 用
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def get(self, url: str, **_kwargs) -> _FakeResponse:
        self.requested.append(url)
        floor = int(url.rstrip(".json").rsplit("/", 1)[-1])
        if floor in self.fail:
            raise RuntimeError("网络炸了")
        return _FakeResponse(self.windows[floor])


def _patch_client(monkeypatch, fake: _FakeClient) -> None:
    monkeypatch.setattr(linuxdo_mod.http, "AsyncClient", fake)


def _fill(payload: dict, post_number: str) -> dict:
    return asyncio.run(LinuxDoTopic._with_context_floors(payload, TOPIC_ID, post_number))


def test_the_missing_opening_post_is_fetched_and_merged(monkeypatch):
    """**核心**: 缺主楼时补一次请求, 合并后帖子流里两层都在"""
    fake = _FakeClient({1: _window_with_opening()})
    _patch_client(monkeypatch, fake)

    merged = _fill(_window_24(), "24")
    floors = _floors(merged)

    assert len(fake.requested) == 1, f"应该只请求一次: {fake.requested}"
    assert "/1.json" in fake.requested[0], fake.requested[0]
    assert 1 in floors, "主楼没补进来"
    assert 24 in floors, "当前楼层丢了"
    assert floors == sorted(floors), "楼层应该有序"


def test_nothing_is_requested_when_the_window_is_complete(monkeypatch):
    """**核心**: 窗口里已有主楼时零额外请求 —— 4 楼那种情况行为完全不变"""
    fake = _FakeClient({1: _window_with_opening()})
    _patch_client(monkeypatch, fake)

    merged = _fill(_window_with_opening(), "4")

    assert fake.requested == [], f"不该发任何请求: {fake.requested}"
    assert _floors(merged) == _floors(_window_with_opening())


def test_a_failed_fetch_does_not_break_the_parse(monkeypatch):
    """补不到不该让整条解析失败 —— 少一个上下文块, 总好过整条打不开"""
    fake = _FakeClient({1: _window_with_opening()}, fail={1})
    _patch_client(monkeypatch, fake)

    merged = _fill(_window_24(), "24")
    assert 24 in _floors(merged), "原窗口的楼层应该原样保留"


def test_overlapping_floors_are_deduplicated(monkeypatch):
    """窗口与补取回来的两段会重叠 —— 按楼层去重, 不出现重复的层"""
    fake = _FakeClient({1: _window_with_opening()})  # 1..20, 与 19..38 重叠 19、20
    _patch_client(monkeypatch, fake)

    floors = _floors(_fill(_window_24(), "24"))
    assert len(floors) == len(set(floors)), f"有重复楼层: {floors}"
    assert floors[0] == 1 and floors[-1] == 38


def test_both_missing_floors_are_fetched(monkeypatch):
    """主楼与被回复的层**都**在窗口外 → 两次请求，都补回来"""
    payload = _window_24()
    # 24 楼本身回复主楼（reply_to=None）。这里手工改成回复第 3 楼 —— 它也不在窗口里。
    for post in (payload.get("post_stream") or {}).get("posts") or []:
        if post.get("post_number") == 24:
            post["reply_to_post_number"] = 3
    fake = _FakeClient({1: _window_with_opening(), 3: _window_with_opening()})
    _patch_client(monkeypatch, fake)

    merged = _fill(payload, "24")

    assert len(fake.requested) == 2, fake.requested
    assert any("/1.json" in u for u in fake.requested)
    assert any("/3.json" in u for u in fake.requested)
    assert _floors(merged) == sorted(_floors(merged))


# ---------------------------------------------------------------- 端到端（真实 fixture）


def test_the_floor_renders_with_the_opening_post_after_filling(monkeypatch):
    """**核心**: 补齐后第 24 楼渲染出**主楼引用块** + 自己的楼层号"""
    fake = _FakeClient({1: _window_with_opening()})
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
