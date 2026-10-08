"""媒体处理: 降采样不能按**文件后缀**猜保存格式。

背景（2026-10-08, 用户报「▎媒体处理错误: cannot write mode P as JPEG」, 推文
``kon_kokine/status/2107756845704360201``）:

那张图是 **PNG 调色板图（mode ``P``）**, 但下载时被命名成 ``.jpg``
（``ImageRef.ext`` 默认 ``jpg``, twitter 解析器没传 ext）。``_downscale_image`` 里
``resized.save(out_path)`` 不指定 format ⇒ Pillow 按**后缀**选编码器 ⇒ JPEG 编不了 ``P``
⇒ 抛错, 整条媒体处理失败（用户只看到"媒体处理错误"）。

判据: **保存格式跟真实格式走** —— 后缀撒谎的 P 模式 PNG 降采样后仍是 PNG, 输出后缀也是 ``.png``。
"""

from pathlib import Path

from PIL import Image

from utils.media_processing_unit import MediaProcessingUnit

#: 长边超限才会触发降采样（默认 2560）; 这个尺寸比例正常, 不会进填充/切割分支
_OVERSIZED = (2700, 1200)


def _unit(tmp_path: Path) -> MediaProcessingUnit:
    return MediaProcessingUnit(tmp_path / "out", segment_height=1920, logger=lambda _m: None)


def _write_image(path: Path, fmt: str, mode: str, size: tuple[int, int]) -> Path:
    Image.new(mode, size).save(path, format=fmt)
    return path


# ---------------------------------------------------------------- 核心回归


def test_palette_png_with_a_lying_extension_still_downscales(tmp_path):
    """**核心回归**: PNG 内容 + ``.jpg`` 后缀 + mode ``P`` 不能炸。

    旧实现按后缀存 JPEG, 直接 ``OSError: cannot write mode P as JPEG``。
    """
    unit = _unit(tmp_path)
    src = _write_image(tmp_path / "001_欺骗.jpg", "PNG", "P", _OVERSIZED)

    out = unit._downscale_image(src)  # noqa: SLF001 — 直接钉住这一层的行为

    assert out is not None
    assert out.suffix == ".png", "输出后缀必须跟随真实格式, 不能继续撒谎"
    with Image.open(out) as img:
        assert img.format == "PNG"
        assert img.mode == "P", "调色板模式要原样保留, 不该被转成 JPEG"
        assert max(img.size) == 2560


def test_real_jpeg_downscales_to_jpeg_as_before(tmp_path):
    """真 JPEG 走原路径: 后缀与格式都不变（这次改动对它是零变化）。"""
    unit = _unit(tmp_path)
    src = _write_image(tmp_path / "photo.jpg", "JPEG", "RGB", _OVERSIZED)

    out = unit._downscale_image(src)  # noqa: SLF001

    assert out is not None
    assert out.suffix == ".jpg"
    with Image.open(out) as img:
        assert img.format == "JPEG"
        assert max(img.size) == 2560


def test_rgba_png_downscales_as_png(tmp_path):
    """带透明的 PNG 同理: 透明通道不能因为"按后缀存"而丢掉。"""
    unit = _unit(tmp_path)
    src = _write_image(tmp_path / "alpha.png", "PNG", "RGBA", _OVERSIZED)

    out = unit._downscale_image(src)  # noqa: SLF001

    assert out is not None
    with Image.open(out) as img:
        assert img.format == "PNG"
        assert img.mode == "RGBA"


def test_webp_keeps_its_own_format(tmp_path):
    unit = _unit(tmp_path)
    src = _write_image(tmp_path / "pic.webp", "WEBP", "RGB", _OVERSIZED)

    out = unit._downscale_image(src)  # noqa: SLF001

    assert out is not None
    assert out.suffix == ".webp"
    with Image.open(out) as img:
        assert img.format == "WEBP"


def test_small_image_is_left_alone(tmp_path):
    """长边不超限 -> 不动它（返回 None, 不产生任何文件）。"""
    unit = _unit(tmp_path)
    src = _write_image(tmp_path / "small.png", "PNG", "P", (100, 80))

    assert unit._downscale_image(src) is None  # noqa: SLF001
    assert not list((tmp_path / "out").glob("*"))


# ---------------------------------------------------------------- 全链路


def test_process_image_handles_a_lying_palette_png_end_to_end(tmp_path):
    """端到端: 比例正常的"后缀撒谎"调色板图, 走 ``process_image`` 不再失败。

    （尺寸选 2700x1200 —— 横图比例 2.25 < 20, 不会进填充/切割, 只触发降采样。）
    """
    import asyncio

    unit = _unit(tmp_path)
    src = _write_image(tmp_path / "001_欺骗.jpg", "PNG", "P", _OVERSIZED)

    result = asyncio.run(unit.process_image(src))

    assert len(result.output_paths) == 1
    out = result.output_paths[0]
    with Image.open(out) as img:
        assert img.format == "PNG"
        assert max(img.size) == 2560
