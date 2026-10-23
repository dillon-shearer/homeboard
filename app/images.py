import contextlib
import io
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path

import pillow_heif
from PIL import Image, ImageOps

from app import settings

pillow_heif.register_heif_opener()

MAX_UPLOAD_BYTES = 50 * 1024 * 1024
SCREEN_WIDTH = settings.SCREEN_WIDTH
SCREEN_HEIGHT = settings.SCREEN_HEIGHT
THUMB_EDGE = 400
JPEG_QUALITY = 85
FULL_JPEG_QUALITY = 92
ALLOWED_FORMATS = {"JPEG", "MPO", "PNG", "HEIF", "HEIC", "WEBP"}
DRAFT_FORMATS = {"JPEG", "MPO"}
MAX_PIXELS = 210_000_000
MAX_FULL_DECODE_PIXELS = 178_956_970
MAX_CONCURRENT = 1
Image.MAX_IMAGE_PIXELS = MAX_PIXELS

_slots = threading.BoundedSemaphore(MAX_CONCURRENT)


class ImageError(Exception):
    pass


@dataclass
class ProcessedImage:
    filename: str
    thumb: str
    width: int
    height: int


def _to_rgb(img: Image.Image) -> Image.Image:
    if img.mode == "I" or img.mode.startswith("I;16"):
        img = img.convert("I").point(lambda v: v / 256).convert("L")
    if img.mode in ("RGBA", "LA", "PA") or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        bg = Image.new("RGB", rgba.size, (0, 0, 0))
        bg.paste(rgba, mask=rgba.getchannel("A"))
        return bg
    return img.convert("RGB")


def _fit(img: Image.Image, edge: int) -> Image.Image:
    w, h = img.size
    scale = edge / max(w, h)
    if scale >= 1:
        return img
    size = (max(1, round(w * scale)), max(1, round(h * scale)))
    return img.resize(size, Image.Resampling.LANCZOS)


def _cover(img: Image.Image, width: int, height: int) -> Image.Image:
    w, h = img.size
    scale = max(width / w, height / h)
    if scale >= 1:
        return img
    size = (max(1, round(w * scale)), max(1, round(h * scale)))
    return img.resize(size, Image.Resampling.LANCZOS)


def _save(
    img: Image.Image, path: Path, quality: int = JPEG_QUALITY, icc_profile: bytes | None = None
) -> None:
    extra = {"icc_profile": icc_profile} if icc_profile else {}
    img.save(path, "JPEG", quality=quality, progressive=True, optimize=True, **extra)


def _draft_size(img: Image.Image) -> tuple[int, int]:
    orientation = None
    with contextlib.suppress(Exception):
        orientation = img.getexif().get(0x0112)
    if orientation in (5, 6, 7, 8):
        return SCREEN_HEIGHT, SCREEN_WIDTH
    return SCREEN_WIDTH, SCREEN_HEIGHT


def process_upload(data: bytes, photos_dir, thumbs_dir) -> ProcessedImage:
    if len(data) > MAX_UPLOAD_BYTES:
        raise ImageError("too large")
    with _slots:
        return _process(data, photos_dir, thumbs_dir)


def _process(data: bytes, photos_dir, thumbs_dir) -> ProcessedImage:
    try:
        img = Image.open(io.BytesIO(data))
    except Exception as e:
        raise ImageError("cannot open") from e
    if img.format not in ALLOWED_FORMATS:
        raise ImageError("unsupported format")
    pixels = img.width * img.height
    if pixels > MAX_PIXELS or (img.format not in DRAFT_FORMATS and pixels > MAX_FULL_DECODE_PIXELS):
        raise ImageError("too large")
    try:
        if img.format in DRAFT_FORMATS:
            img.draft("RGB", _draft_size(img))
        img.load()
    except Exception as e:
        raise ImageError("cannot open") from e
    icc = img.info.get("icc_profile") if img.mode in ("RGB", "RGBA", "RGBX") else None
    img = _to_rgb(ImageOps.exif_transpose(img))
    full = _cover(img, SCREEN_WIDTH, SCREEN_HEIGHT)
    thumb = _fit(full, THUMB_EDGE)
    stem = uuid.uuid4().hex
    filename = f"{stem}.jpg"
    thumb_name = f"{stem}_t.jpg"
    full_path = Path(photos_dir) / filename
    thumb_path = Path(thumbs_dir) / thumb_name
    full_tmp = full_path.with_suffix(".tmp")
    thumb_tmp = thumb_path.with_suffix(".tmp")
    try:
        _save(full, full_tmp, FULL_JPEG_QUALITY, icc)
        _save(thumb, thumb_tmp, icc_profile=icc)
        full_tmp.replace(full_path)
        thumb_tmp.replace(thumb_path)
    except BaseException:
        for path in (full_tmp, thumb_tmp, full_path, thumb_path):
            path.unlink(missing_ok=True)
        raise
    return ProcessedImage(filename, thumb_name, full.width, full.height)


def delete_files(filename, thumb, photos_dir, thumbs_dir) -> None:
    for d, name in ((photos_dir, filename), (thumbs_dir, thumb)):
        if name:
            (Path(d) / name).unlink(missing_ok=True)
