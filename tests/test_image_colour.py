import io

from PIL import Image, ImageCms

from app.images import process_upload


def _dirs(tmp_path):
    photos = tmp_path / "photos"
    thumbs = tmp_path / "thumbs"
    photos.mkdir()
    thumbs.mkdir()
    return photos, thumbs


def test_jpeg_icc_profile_carried_to_full_and_thumb(tmp_path):
    photos, thumbs = _dirs(tmp_path)
    profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    buf = io.BytesIO()
    Image.new("RGB", (800, 600), (200, 100, 50)).save(buf, "JPEG", icc_profile=profile)
    result = process_upload(buf.getvalue(), photos, thumbs)
    with Image.open(photos / result.filename) as out:
        assert out.info.get("icc_profile") == profile
    with Image.open(thumbs / result.thumb) as out:
        assert out.info.get("icc_profile") == profile


def test_jpeg_without_profile_saved_without_one(tmp_path):
    photos, thumbs = _dirs(tmp_path)
    buf = io.BytesIO()
    Image.new("RGB", (800, 600), (200, 100, 50)).save(buf, "JPEG")
    result = process_upload(buf.getvalue(), photos, thumbs)
    with Image.open(photos / result.filename) as out:
        assert not out.info.get("icc_profile")


def test_16bit_grey_png_becomes_mid_grey(tmp_path):
    photos, thumbs = _dirs(tmp_path)
    buf = io.BytesIO()
    Image.new("I;16", (64, 64), 30000).save(buf, "PNG")
    buf.seek(0)
    assert Image.open(buf).mode in ("I", "I;16")
    result = process_upload(buf.getvalue(), photos, thumbs)
    with Image.open(photos / result.filename) as out:
        r, g, b = out.convert("RGB").getpixel((32, 32))
        assert 100 <= r <= 140 and 100 <= g <= 140 and 100 <= b <= 140


def test_32bit_int_grey_becomes_mid_grey(tmp_path):
    photos, thumbs = _dirs(tmp_path)
    buf = io.BytesIO()
    Image.new("I", (64, 64), 30000).save(buf, "PNG")
    result = process_upload(buf.getvalue(), photos, thumbs)
    with Image.open(photos / result.filename) as out:
        r, _, _ = out.convert("RGB").getpixel((32, 32))
        assert 100 <= r <= 140
