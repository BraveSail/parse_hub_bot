"""linux.do 楼层窗口：窗口外的上下文层怎么取回来。

用户报障原话：「https://linux.do/t/topic/2989140/24?u=libc.so.6 怎么没有主楼了？」
（另一条 ``/4`` 正常 —— 追问「为什么4楼可以？」）
随后质疑「去查discourse文档，我就不信没有querystring能筛选」—— **他是对的**；
见我把主路径改成 ``print=true`` 后又质疑「你这样还不如第二次请求1.json？」—— **又对了**。

**窗口规律**（真机实测 11 个楼层）::

    1 楼  → 1..20   含主楼
    4 楼  → 1..20   含主楼
    6 楼  → 1..20   含主楼
    7 楼  → 2..21   不含        ← 分界
    24 楼 → 19..38  不含
    30 楼 → 25..44  不含

⇒ 窗口是**固定 20 层**，起点 = ``max(1, target - 5)``（源码 ``lib/topic_view.rb`` 的
``filter_posts_near``：``posts_before = (@limit / 4).floor``，limit 默认 20 → 5）。
target ≤ 6 时起点被夹到 1，主楼恰好还在窗口里。

**取回方式（实测对比）**::

    a) /<id>/1.json                       20 层  53.6KB  0.18s   ✓
    b) posts.json?post_ids[]=<主楼 id>      1 层   4.5KB  0.21s   ✓  ← 现行主路径
    c) /<id>/1.json?print=true             49 层  117KB          ✗ 限流

- ``stream`` 数组是话题里**所有可见层的 id 列表**，所以 ``stream[floor - 1]`` 就能定位那一层，
  按 id 精确取只要 4.5KB（比窗口小 12 倍）。位置在话题删过层时会漂 ⇒ **必须校验楼层号**。
- ``?print=true`` 确实能把 chunk_size 从 20 提到 1000（``TopicView.print_chunk_size``），
  但它是**打印视图端点、服务端挂了限流**：连打几次后返回
  ``422 {"errors":["You've performed this action too many times..."]}`` —— 不能当常规路径。

**根因**：`_context_quotes` 在窗口里 ``next((p for p in posts if post_number == 1), None)``
—— 找不到就**静默跳过**，于是"分享楼层时带上主楼"这条规则在靠后的楼层上悄悄失效。

fixture: ``linuxdo_floor_24.json`` = 24 楼的窗口（19..38，**不含主楼**）；
``linuxdo_floor_4.json`` = 含主楼的窗口（1..20），拿来当补取的响应。
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


def _posts(payload: dict) -> list[dict]:
    return (payload.get("post_stream") or {}).get("posts") or []


def _layer(payload: dict, floor: int) -> dict:
    """从 payload 里挑出某一层，包成"按 id 取单层"那种响应"""
    for post in _posts(payload):
        if post.get("post_number") == floor:
            return {"post_stream": {"posts": [post]}}
    return {"post_stream": {"posts": []}}


# ---------------------------------------------------------------- 窗口规律（纯函数）


def test_a_late_floor_window_is_missing_the_opening_post():
    """**核心**: 24 楼的窗口里没有主楼 —— 这就是"没有主楼了"的来源"""
    payload = _window_24()
    assert 1 not in _floors(payload), "fixture 变了: 它应该是不含主楼的窗口"
    assert LinuxDoTopic._missing_context_floors(_posts(payload), wanted=24, reply_to=None) == [1]


def test_an_early_floor_window_already_has_the_opening_post():
    """**核心**: 4 楼的窗口里本来就有主楼 —— 不需要任何请求（这就是"4楼可以"）"""
    posts = _posts(_window_with_opening())
    assert 1 in [p.get("post_number") for p in posts]
    assert LinuxDoTopic._missing_context_floors(posts, wanted=4, reply_to=None) == []


def test_the_opening_post_is_not_requested_when_reading_it():
    """解析主楼本身时不该取主楼（它就在窗口里，而且不自我引用）"""
    assert LinuxDoTopic._missing_context_floors(_posts(_window_24()), wanted=1, reply_to=None) == []


def test_a_replied_floor_outside_the_window_is_also_missed():
    """被回复的层同样可能在窗口外 —— 两个都要，顺序是「主楼 → 被回复的层」"""
    assert LinuxDoTopic._missing_context_floors(_posts(_window_24()), wanted=24, reply_to=3) == [1, 3]


def test_a_replied_floor_inside_the_window_is_not_requested():
    """被回复的层已在窗口里就不取"""
    posts = _posts(_window_24())
    inside = [p.get("post_number") for p in posts][2]
    assert LinuxDoTopic._missing_context_floors(posts, wanted=24, reply_to=inside) == [1]


def test_replying_to_the_opening_post_does_not_duplicate_it():
    """回复主楼时（reply_to=1）只算一次"""
    assert LinuxDoTopic._missing_context_floors(_posts(_window_24()), wanted=24, reply_to=1) == [1]


# ---------------------------------------------------------------- 楼层 → id


def test_the_stream_gives_the_id_for_a_floor():
    """``stream`` 是话题所有可见层的 id 列表 —— 位置就是楼层号减一"""
    payload = _window_24()
    stream = (payload.get("post_stream") or {}).get("stream") or []
    assert LinuxDoTopic._post_id_for_floor(payload, 1) == stream[0]
    assert LinuxDoTopic._post_id_for_floor(payload, 24) == stream[23]


def test_a_floor_past_the_stream_has_no_id():
    """stream 比楼层号短（话题删过层/被过滤）→ 拿不到 id，只能走窗口"""
    payload = _window_24()
    payload["post_stream"]["stream"] = [1, 2, 3]
    assert LinuxDoTopic._post_id_for_floor(payload, 24) is None


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
    """假的 ``http.AsyncClient``：按 URL 分辨"按 id 精确取"与"取楼层窗口"。

    - URL 含 ``posts.json`` → 按 ``post_ids[]`` 里的 id 反查楼层，返回**单层**
    - 其它 → 按 URL 末段的楼层号返回 ``windows`` 里配的响应（默认：含主楼的窗口）
    """

    def __init__(
        self,
        *,
        topic: dict | None = None,
        windows: dict[int, dict] | None = None,
        id_layers: dict[int, dict] | None = None,
        id_status: int = 200,
        id_raises: bool = False,
        id_returns_wrong_floor: bool = False,
        window_raises: bool = False,
        fail_windows: set[int] | None = None,
    ):
        self.topic = topic or _window_with_opening()
        self.stream = (self.topic.get("post_stream") or {}).get("stream") or []
        self.windows = windows or {}
        self.id_layers = id_layers or {}
        self.id_status = id_status
        self.id_raises = id_raises
        self.id_returns_wrong_floor = id_returns_wrong_floor
        self.window_raises = window_raises
        self.fail_windows = fail_windows or set()
        self.requested: list[str] = []

    def __call__(self, **_kwargs):  # AsyncClient(proxy=..., cookies=..., timeout=...)
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def get(self, url: str, **_kwargs) -> _FakeResponse:
        self.requested.append(url)
        if "posts.json" in url:
            if self.id_raises:
                raise RuntimeError("按 id 取炸了")
            post_id = int(url.rsplit("=", 1)[-1])
            floor = self.stream.index(post_id) + 1 if post_id in self.stream else None
            if floor is None:
                return _FakeResponse({"post_stream": {"posts": []}})
            if self.id_returns_wrong_floor:
                return _FakeResponse(_layer(self.topic, floor + 1), self.id_status)
            return _FakeResponse(self.id_layers.get(floor) or _layer(self.topic, floor), self.id_status)

        floor = int(url.rstrip(".json").rsplit("/", 1)[-1])
        if self.window_raises or floor in self.fail_windows:
            raise RuntimeError("窗口取炸了")
        return _FakeResponse(self.windows.get(floor, _window_with_opening()))

    def id_requests(self) -> list[str]:
        return [u for u in self.requested if "posts.json" in u]

    def window_requests(self) -> list[str]:
        return [u for u in self.requested if "posts.json" not in u]


def _patch_client(monkeypatch, fake: _FakeClient) -> None:
    monkeypatch.setattr(linuxdo_mod.http, "AsyncClient", fake)


def _fill(payload: dict, post_number: str) -> dict:
    return asyncio.run(LinuxDoTopic._with_context_floors(payload, TOPIC_ID, post_number))


# ---------------------------------------------------------------- 便宜那条路（按 id）


def test_the_missing_opening_post_is_fetched_by_id(monkeypatch):
    """**核心**: 缺主楼时按 **id 精确取**那一层（4.5KB），而不是取它的 20 层窗口（53.6KB）"""
    fake = _FakeClient()
    _patch_client(monkeypatch, fake)

    merged = _fill(_window_24(), "24")

    assert len(fake.id_requests()) == 1, f"应该按 id 取一次: {fake.requested}"
    assert fake.window_requests() == [], f"便宜那条路成了就不该再取窗口: {fake.requested}"
    floors = _floors(merged)
    assert 1 in floors, "主楼没合并进来"
    assert 24 in floors, "当前楼层丢了"
    assert floors == sorted(floors)


def test_the_id_request_asks_for_the_opening_post(monkeypatch):
    """请求的 id 就是主楼那一层（stream 的第一项）"""
    fake = _FakeClient()
    _patch_client(monkeypatch, fake)

    _fill(_window_24(), "24")
    opener_id = ((_window_24().get("post_stream") or {}).get("stream") or [])[0]
    assert f"post_ids[]={opener_id}" in fake.id_requests()[0], fake.id_requests()[0]


def test_two_missing_floors_cost_two_requests(monkeypatch):
    """主楼与被回复的层都缺 → 两层各一次精确取（都便宜）"""
    payload = _window_24()
    # 24 楼本身回复主楼（reply_to=None）。手工改成回复第 3 楼 —— 它也不在窗口里。
    for post in _posts(payload):
        if post.get("post_number") == 24:
            post["reply_to_post_number"] = 3
    fake = _FakeClient()
    _patch_client(monkeypatch, fake)

    merged = _fill(payload, "24")

    assert len(fake.id_requests()) == 2, f"两个缺层各一次: {fake.requested}"
    assert fake.window_requests() == [], fake.requested
    assert 1 in _floors(merged) and 3 in _floors(merged)


def test_nothing_is_requested_when_the_window_is_complete(monkeypatch):
    """**核心**: 窗口里已有主楼时零额外请求 —— 4 楼那种情况行为完全不变"""
    fake = _FakeClient()
    _patch_client(monkeypatch, fake)

    merged = _fill(_window_with_opening(), "4")

    assert fake.requested == [], f"不该发任何请求: {fake.requested}"
    assert _floors(merged) == _floors(_window_with_opening())


def test_overlapping_floors_are_deduplicated(monkeypatch):
    """补回来的层与原窗口会重叠（19、20 两层）—— 按楼层去重，不出现重复的层"""
    fake = _FakeClient()
    _patch_client(monkeypatch, fake)

    floors = _floors(_fill(_window_24(), "24"))  # 19..38 与 1..20 重叠 19、20
    assert len(floors) == len(set(floors)), f"有重复楼层: {floors}"
    assert floors[0] == 1 and floors[-1] == 38


# ---------------------------------------------------------------- 退回窗口那条路


def test_a_wrong_floor_from_the_id_request_falls_back_to_the_window(monkeypatch):
    """**核心**: 按 id 取回来的不是那一层（话题删过层导致位置漂移）→ 改取该层窗口"""
    fake = _FakeClient(id_returns_wrong_floor=True, windows={1: _window_with_opening()})
    _patch_client(monkeypatch, fake)

    merged = _fill(_window_24(), "24")

    assert len(fake.id_requests()) == 1, "应该先试过 id"
    assert any("/1.json" in u for u in fake.window_requests()), fake.requested
    assert 1 in _floors(merged), "退回窗口后仍要拿到主楼"


def test_a_rejected_id_request_falls_back_to_the_window(monkeypatch):
    """按 id 取返回非 200 → 改取窗口"""
    fake = _FakeClient(id_status=404, windows={1: _window_with_opening()})
    _patch_client(monkeypatch, fake)

    merged = _fill(_window_24(), "24")

    assert len(fake.id_requests()) == 1
    assert any("/1.json" in u for u in fake.window_requests()), fake.requested
    assert 1 in _floors(merged)


def test_an_exploding_id_request_falls_back_to_the_window(monkeypatch):
    """按 id 请求本身炸了（网络/超时）→ 改取窗口，不能让这一层直接丢"""
    fake = _FakeClient(id_raises=True, windows={1: _window_with_opening()})
    _patch_client(monkeypatch, fake)

    merged = _fill(_window_24(), "24")

    assert any("/1.json" in u for u in fake.window_requests()), fake.requested
    assert 1 in _floors(merged)


def test_a_floor_without_an_id_goes_straight_to_the_window(monkeypatch):
    """响应里没有 stream（拿不到 id）→ 直接取该层窗口，不发无用的 id 请求"""
    payload = _window_24()
    payload["post_stream"]["stream"] = []
    fake = _FakeClient(windows={1: _window_with_opening()})
    _patch_client(monkeypatch, fake)

    merged = _fill(payload, "24")

    assert fake.id_requests() == [], f"没有 id 就别发: {fake.requested}"
    assert any("/1.json" in u for u in fake.window_requests()), fake.requested
    assert 1 in _floors(merged)


def test_a_failed_fetch_does_not_break_the_parse(monkeypatch):
    """两条路都取不到 → 只记 warning，不抛错（少一个上下文块好过整条打不开）"""
    fake = _FakeClient(id_raises=True, window_raises=True)
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
