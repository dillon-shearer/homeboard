import io
import uuid
from dataclasses import dataclass
from pathlib import Path

import pillow_heif
from PIL import Image, ImageOps

from app import settings

pillow_heif.register_heif_opener()

MAX_UPLOAD_BYTES = 50 * 1024 * 1024
LONG_EDGE = settings.LONG_EDGE
THUMB_EDGE = 400
JPEG_QUALITY = 85
ALLOWED_FORMATS = {"JPEG", "PNG", "HEIF", "HEIC", "WEBP"}


class ImageError(Exception):
    pass


@dataclass
class ProcessedImage:
    filename: str
    thumb: str
    width: int
    height: int


def _to_rgb(img: Image.Image) -> Image.Image:
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


def _save(img: Image.Image, path: Path) -> None:
    img.save(path, "JPEG", quality=JPEG_QUALITY, progressive=True, optimize=True)


def process_upload(data: bytes, photos_dir, thumbs_dir) -> ProcessedImage:
    if len(data) > MAX_UPLOAD_BYTES:
        raise ImageError("too large")
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except Exception as e:
        raise ImageError("cannot open") from e
    if img.format not in ALLOWED_FORMATS:
        raise ImageError("unsupported format")
    img = _to_rgb(ImageOps.exif_transpose(img))
    full = _fit(img, LONG_EDGE)
    thumb = _fit(full, THUMB_EDGE)
    stem = uuid.uuid4().hex
    filename = f"{stem}.jpg"
    thumb_name = f"{stem}_t.jpg"
    _save(full, Path(photos_dir) / filename)
    _save(thumb, Path(thumbs_dir) / thumb_name)
    return ProcessedImage(filename, thumb_name, full.width, full.height)


def delete_files(filename, thumb, photos_dir, thumbs_dir) -> None:
    for d, name in ((photos_dir, filename), (thumbs_dir, thumb)):
        if name:
            (Path(d) / name).unlink(missing_ok=True)
