import io

import pillow_heif
import pytest
from PIL import Image

from app import images
from app.images import ImageError, delete_files, process_upload


def _dirs(tmp_path):
    photos = tmp_path / "photos"
    thumbs = tmp_path / "thumbs"
    photos.mkdir()
    thumbs.mkdir()
    return photos, thumbs


def _jpeg(size, exif=None):
    buf = io.BytesIO()
    img = Image.new("RGB", size, (200, 100, 50))
    if exif is not None:
        img.save(buf, "JPEG", exif=exif)
    else:
        img.save(buf, "JPEG")
    return buf.getvalue()


def test_exif_orientation_applied_and_stripped(tmp_path):
    photos, thumbs = _dirs(tmp_path)
    exif = Image.Exif()
    exif[0x0112] = 6
    exif[0x8825] = {1: "N"}
    result = process_upload(_jpeg((4000, 3000), exif), photos, thumbs)
    assert (result.width, result.height) == (1920, 2560)
    with Image.open(photos / result.filename) as out:
        assert out.size == (1920, 2560)
        assert out.format == "JPEG"
        assert "exif" not in out.info
        assert len(out.getexif()) == 0


def test_png_alpha_becomes_rgb_jpeg(tmp_path):
    photos, thumbs = _dirs(tmp_path)
    buf = io.BytesIO()
    Image.new("RGBA", (300, 200), (255, 0, 0, 0)).save(buf, "PNG")
    result = process_upload(buf.getvalue(), photos, thumbs)
    with Image.open(photos / result.filename) as out:
        assert out.format == "JPEG"
        assert out.mode == "RGB"
        assert max(out.getpixel((10, 10))) < 10


def test_small_image_not_upscaled(tmp_path):
    photos, thumbs = _dirs(tmp_path)
    result = process_upload(_jpeg((800, 600)), photos, thumbs)
    assert (result.width, result.height) == (800, 600)
    with Image.open(photos / result.filename) as out:
        assert out.size == (800, 600)


def test_portrait_stored_wide_enough_to_cover(tmp_path):
    photos, thumbs = _dirs(tmp_path)
    result = process_upload(_jpeg((3000, 4000)), photos, thumbs)
    assert (result.width, result.height) == (1920, 2560)
    with Image.open(photos / result.filename) as out:
        assert out.size == (1920, 2560)


def test_landscape_stored_to_cover_both_edges(tmp_path):
    photos, thumbs = _dirs(tmp_path)
    result = process_upload(_jpeg((4000, 3000)), photos, thumbs)
    assert (result.width, result.height) == (1920, 1440)


def test_wide_panorama_keeps_full_screen_height(tmp_path):
    photos, thumbs = _dirs(tmp_path)
    result = process_upload(_jpeg((6000, 1500)), photos, thumbs)
    assert (result.width, result.height) == (4320, 1080)


def test_image_smaller_than_screen_on_one_edge_not_resized(tmp_path):
    photos, thumbs = _dirs(tmp_path)
    result = process_upload(_jpeg((1600, 2400)), photos, thumbs)
    assert (result.width, result.height) == (1600, 2400)


def test_thumb_long_edge(tmp_path):
    photos, thumbs = _dirs(tmp_path)
    result = process_upload(_jpeg((4000, 3000)), photos, thumbs)
    assert result.thumb == result.filename.replace(".jpg", "_t.jpg")
    with Image.open(thumbs / result.thumb) as t:
        assert max(t.size) == 400
        assert t.size == (400, 300)


def test_heic_converts(tmp_path):
    photos, thumbs = _dirs(tmp_path)
    src = tmp_path / "in.heic"
    try:
        Image.new("RGB", (640, 480), (10, 20, 30)).save(src, "HEIF")
    except (OSError, ValueError, KeyError, RuntimeError) as e:
        pytest.skip(f"pillow-heif cannot write HEIC on this machine: {e}")
    assert pillow_heif.is_supported(src.read_bytes())
    result = process_upload(src.read_bytes(), photos, thumbs)
    assert (result.width, result.height) == (640, 480)
    with Image.open(photos / result.filename) as out:
        assert out.format == "JPEG"


def test_text_raises(tmp_path):
    photos, thumbs = _dirs(tmp_path)
    with pytest.raises(ImageError, match="cannot open"):
        process_upload(b"hello, this is not an image", photos, thumbs)


def test_unsupported_format_raises(tmp_path):
    photos, thumbs = _dirs(tmp_path)
    buf = io.BytesIO()
    Image.new("RGB", (10, 10)).save(buf, "GIF")
    with pytest.raises(ImageError, match="unsupported format"):
        process_upload(buf.getvalue(), photos, thumbs)


def test_too_large_rejected_before_open(tmp_path, monkeypatch):
    photos, thumbs = _dirs(tmp_path)

    def boom(*a, **k):
        raise AssertionError("opened")

    monkeypatch.setattr(images.Image, "open", boom)
    with pytest.raises(ImageError, match="too large"):
        process_upload(b"\0" * (51 * 1024 * 1024), photos, thumbs)


def test_delete_files_ignores_missing(tmp_path):
    photos, thumbs = _dirs(tmp_path)
    result = process_upload(_jpeg((800, 600)), photos, thumbs)
    delete_files(result.filename, result.thumb, photos, thumbs)
    assert not (photos / result.filename).exists()
    assert not (thumbs / result.thumb).exists()
    delete_files(result.filename, result.thumb, photos, thumbs)
