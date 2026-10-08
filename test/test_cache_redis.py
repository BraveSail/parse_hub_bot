"""缓存层换 Redis 之后的语义。

三件必须钉死的事:

1. **前缀隔离**: Redis 是共用实例 (161 上宝塔那个), 清空只能删自己前缀内的 key。
2. **Redis 挂了 bot 不能挂**: 缓存是加速手段 —— 连接/读写失败要退化成"没有缓存",
   继续走完整解析, 而不是把异常抛给解析流程。
3. **key 用的是写入时那把** (``sha256(raw_url)``), 否则清不掉。
"""

import asyncio
import hashlib
import json
from unittest.mock import AsyncMock, MagicMock, patch

from parsehub.types.media_ref import AniRef, ImageRef
from parsehub.types.result import MultimediaParseResult

from services.cache import CacheEntry, CacheMedia, CacheMediaType, CacheParseResult, PersistentCache, ResultCache

URL = "https://x.com/handle/status/1"


def _sha(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------- key 与隔离


def test_both_layers_prefix_their_keys():
    """前缀是共用实例上的隔离界线 —— 少了它就会和别的项目撞 key"""
    assert ResultCache._key(URL) == f"shirobako:result:{_sha(URL)}"
    assert PersistentCache._key(URL) == f"shirobako:parse:{_sha(URL)}"


def test_the_two_layers_never_share_a_key():
    assert ResultCache._key(URL) != PersistentCache._key(URL)


def test_the_prefix_comes_from_config():
    """前缀来自配置 (换实例/换项目时改配置即可, 不用改代码)"""
    from core import bs

    assert bs.cache_key_prefix == "shirobako:"


def test_clearing_never_uses_flush():
    """清空只按前缀 ``SCAN`` + ``DEL``, **绝不能** FLUSHDB/FLUSHALL"""
    with patch("services.cache.delete_by_prefix", AsyncMock(return_value=0)) as dbp:
        asyncio.run(ResultCache().clear())
        asyncio.run(PersistentCache().clear())
    patterns = [c.args[0] for c in dbp.await_args_list]
    assert patterns == ["shirobako:result:*", "shirobako:parse:*"]
    assert all(p.startswith("shirobako:") for p in patterns)


# ---------------------------------------------------------------- Redis 不可用


def test_a_broken_redis_reads_as_a_miss_instead_of_raising():
    """Redis 读不了 = 没有缓存, 继续解析。**不能让异常冒到解析流程里**"""
    from services import redis_client

    broken = MagicMock()
    broken.get = AsyncMock(side_effect=ConnectionError("redis is down"))
    with patch.object(redis_client, "get_redis", return_value=broken):
        assert asyncio.run(redis_client.get_optional("k")) is None


def test_a_broken_redis_write_is_not_fatal():
    from services import redis_client

    broken = MagicMock()
    broken.set = AsyncMock(side_effect=ConnectionError("redis is down"))
    with patch.object(redis_client, "get_redis", return_value=broken):
        assert asyncio.run(redis_client.set_with_ttl("k", "v", 60)) is False


def test_result_cache_get_is_a_miss_when_redis_is_down():
    from services import redis_client

    broken = MagicMock()
    broken.get = AsyncMock(side_effect=ConnectionError("down"))
    with patch.object(redis_client, "get_redis", return_value=broken):
        assert asyncio.run(ResultCache().get(URL)) is None


def test_a_corrupt_entry_is_deleted_and_treated_as_a_miss():
    """坏条目要删掉, 否则每次解析都白读一遍"""
    from services import redis_client

    broken = MagicMock()
    broken.get = AsyncMock(return_value="这不是 JSON")
    broken.delete = AsyncMock(return_value=1)
    with patch.object(redis_client, "get_redis", return_value=broken):
        assert asyncio.run(ResultCache().get(URL)) is None
    broken.delete.assert_awaited_once_with(ResultCache._key(URL))


def test_an_entry_that_needs_runtime_state_is_a_miss_not_a_downgrade():
    """缓存里的类**构造不出来**时按未命中处理 —— 绝不降级成通用类。

    2026-10-06 的故障（用户报 ``ffprobe failed to get container``）: yt-dlp 系的
    ``YtbVideoParseResult`` 需要运行期句柄 ``dl``, 缓存里不可能有; 旧行为把它降级成
    ``VideoParseResult`` —— 那个类没有 yt-dlp 的下载实现, 于是用基类的分片下载器去下
    ``VideoRef.url``（``www.youtube.com/shorts/...`` 的**页面 URL**）, 拿回 1.2MB HTML,
    产物不是媒体 → 媒体处理阶段 ffprobe 直接失败。

    正确行为: 删掉这条缓存 + 当未命中, 让调用方**重新解析**（行为与现场解析一致）。
    """
    from parsehub.types.result import VideoParseResult
    from parsehub.types.serialize import result_to_cache_dict

    from services import redis_client

    payload = result_to_cache_dict(VideoParseResult(title="t", content="c"))
    payload["impl"] = "YtbVideoParseResult"  # 类存在, 但必填 dl
    fake = MagicMock()
    fake.get = AsyncMock(return_value=json.dumps(payload))
    fake.delete = AsyncMock(return_value=1)
    with patch.object(redis_client, "get_redis", return_value=fake):
        assert asyncio.run(ResultCache().get(URL)) is None
    fake.delete.assert_awaited_once_with(ResultCache._key(URL))


def test_a_result_that_cannot_be_rebuilt_is_not_written():
    """读不回来的结果**不要写结果层** —— 写了也永远重建不了, 每次读都要删+重解析+重写。

    判据是"构造需要解析现场才有的句柄"（历史上 yt-dlp 系的类必填 `dl`；那套已随
    yt-dlp 移除，这里用测试自建的类钉住机制本身）。
    """
    from parsehub.types.result import VideoParseResult

    from services import redis_client

    class _NeedsRuntimeState(VideoParseResult):
        def __init__(self, *, dl, title: str = "", **kwargs):
            self.dl = dl
            super().__init__(title=title, **kwargs)

    result = _NeedsRuntimeState(dl=object(), title="t")

    fake = MagicMock()
    fake.set = AsyncMock()
    with patch.object(redis_client, "get_redis", return_value=fake):
        asyncio.run(ResultCache().set(URL, result))

    fake.set.assert_not_awaited()


def test_a_normal_result_is_still_written():
    """反面: 能重建的结果照常写入（跳过规则不能误伤普通结果）"""
    from services import redis_client

    fake = MagicMock()
    fake.set = AsyncMock()
    with patch.object(redis_client, "get_redis", return_value=fake):
        asyncio.run(ResultCache().set(URL, MultimediaParseResult(title="t")))

    fake.set.assert_awaited_once()


# ---------------------------------------------------------------- 写入


def test_result_cache_roundtrips_a_result_object():
    """存进去、取出来, 对象要能真的用 (媒体类型不能变)"""
    from services import redis_client

    result = MultimediaParseResult(
        title="标题",
        media=[ImageRef(url="https://cdn/a.jpg"), AniRef(url="https://cdn/b.gif")],
        content="正文",
    )
    store: dict[str, str] = {}
    fake = MagicMock()
    fake.set = AsyncMock(side_effect=lambda k, v, ex: store.__setitem__(k, v))
    fake.get = AsyncMock(side_effect=lambda k: store.get(k))

    with patch.object(redis_client, "get_redis", return_value=fake):
        cache = ResultCache()
        asyncio.run(cache.set(URL, result))
        back = asyncio.run(cache.get(URL))

    assert back is not None
    assert back.title == "标题"
    assert back.content == "正文"
    assert [type(m) for m in back.media] == [ImageRef, AniRef]  # 类型没串


def test_the_result_cache_sets_a_ttl():
    """结果层是短 TTL —— 忘了带 ex 就会永久留在 Redis 里"""
    from services import redis_client

    fake = MagicMock()
    fake.set = AsyncMock()
    with patch.object(redis_client, "get_redis", return_value=fake):
        asyncio.run(ResultCache(ttl=300).set(URL, MultimediaParseResult(title="t")))

    assert fake.set.await_args.kwargs["ex"] == 300


def test_the_persistent_cache_sets_the_configured_ttl(  ):
    fake = MagicMock()
    fake.set = AsyncMock()
    entry = CacheEntry(
        parse_result=CacheParseResult(title="t"),
        media=[CacheMedia(type=CacheMediaType.PHOTO, file_id="AgAC")],
    )
    with patch("services.redis_client.get_redis", return_value=fake):
        asyncio.run(PersistentCache(ttl=7 * 24 * 3600).set(URL, entry))

    assert fake.set.await_args.kwargs["ex"] == 7 * 24 * 3600


def test_the_persistent_entry_format_is_unchanged():
    """持久层的序列化格式与 SQLite 时代一致 (值就是 ``CacheEntry.model_dump``) ——

    这样"旧缓存缺字段要重新解析"那套 ``model_fields_set`` 判断依旧成立。
    """
    fake = MagicMock()
    fake.set = AsyncMock()
    entry = CacheEntry(
        parse_result=CacheParseResult(title="标题", author_name="作者", view_count=12),
        media=[CacheMedia(type=CacheMediaType.VIDEO, file_id="BAAC")],
    )
    with patch("services.redis_client.get_redis", return_value=fake):
        asyncio.run(PersistentCache().set(URL, entry))

    payload = json.loads(fake.set.await_args.args[1])
    assert payload["parse_result"]["title"] == "标题"
    assert payload["media"][0]["file_id"] == "BAAC"
    # 反序列化回来还是同一份 (pydantic 往返)
    assert CacheEntry.model_validate(payload).parse_result.view_count == 12


def test_persistent_cache_keeps_the_old_entry_checks():
    """缺字段的旧条目仍然要**重新解析** (不能让用户看到缺统计/缺标签的结果)"""
    from services import redis_client

    # 只存了 title: 缺 published_at / like_count / tags → 应当视为需要重新解析
    partial = {"parse_result": {"title": "旧"}, "media": None, "rich": False}
    fake = MagicMock()
    fake.get = AsyncMock(return_value=json.dumps(partial))
    fake.delete = AsyncMock(return_value=1)
    with patch.object(redis_client, "get_redis", return_value=fake):
        assert asyncio.run(PersistentCache().get(URL)) is None


def test_persistent_remove_uses_the_written_key():
    """清单条用的 key 必须与写入时一致 (sha256(raw_url)), 否则静默清不掉"""
    from services import redis_client

    fake = MagicMock()
    fake.delete = AsyncMock(return_value=1)
    with patch.object(redis_client, "get_redis", return_value=fake):
        asyncio.run(PersistentCache().remove(URL))

    fake.delete.assert_awaited_once_with(PersistentCache._key(URL))
