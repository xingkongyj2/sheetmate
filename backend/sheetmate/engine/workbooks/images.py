"""验证图片原件，并为视觉模型生成有界的首帧副本。"""

from __future__ import annotations

import base64
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps, UnidentifiedImageError

from sheetmate.engine.workbooks.reader import ReadError

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
_FORMATS = {".png": "PNG", ".jpg": "JPEG", ".jpeg": "JPEG", ".webp": "WEBP", ".gif": "GIF"}
_MEDIA_TYPES = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp", "GIF": "image/gif"}
_MAX_IMAGE_PIXELS = 20_000_000
_MAX_ANIMATION_PIXELS = 100_000_000
_MAX_ANIMATION_FRAMES = 500
_MODEL_MAX_SIDE = 2048


def _check_dimensions(size: tuple[int, int]) -> None:
    width, height = size
    if width <= 0 or height <= 0 or width * height > _MAX_IMAGE_PIXELS:
        raise ReadError("图片尺寸过大，请使用不超过 2000 万像素的图片")


def image_profile(path: Path) -> dict[str, Any]:
    """扩展名必须与真实栅格格式一致，完整解码通过后才允许保存或内联展示。"""
    expected = _FORMATS.get(path.suffix.lower())
    if expected is None:
        raise ReadError("图片只支持 PNG / JPG / JPEG / WebP / GIF")
    try:
        with Image.open(path, formats=list(_MEDIA_TYPES)) as image:
            if image.format != expected:
                raise ReadError("图片格式与文件扩展名不一致")
            _check_dimensions(image.size)
            width, height = image.size
            media_type = _MEDIA_TYPES[image.format]
            image.verify()
        # verify() 不会解码像素；重新打开并解码每一帧，拒绝截断或损坏的原件。
        with Image.open(path, formats=[expected]) as image:
            total_pixels = 0
            frame = 0
            while True:
                _check_dimensions(image.size)
                total_pixels += image.width * image.height
                if frame >= _MAX_ANIMATION_FRAMES or total_pixels > _MAX_ANIMATION_PIXELS:
                    raise ReadError("动图内容过大，请减少帧数或使用静态图片")
                image.load()
                frame += 1
                try:
                    image.seek(frame)
                except EOFError:
                    break
    except ReadError:
        raise
    except Image.DecompressionBombError as exc:
        raise ReadError("图片尺寸过大，请使用不超过 2000 万像素的图片") from exc
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError) as exc:
        raise ReadError("图片无法读取或已损坏，请重新选择 PNG / JPG / JPEG / WebP / GIF 图片") from exc
    return {"sheets": [], "file_only": True, "media_type": media_type, "width": width, "height": height}


def model_image_data_url(path: Path) -> str:
    """只发送去元数据、校正方向和缩小后的首帧，绝不改动上传的原件。"""
    image_profile(path)
    try:
        with Image.open(path, formats=list(_MEDIA_TYPES)) as source:
            source.seek(0)
            image = ImageOps.exif_transpose(source)
            has_alpha = "A" in image.getbands() or "transparency" in image.info
            image = image.convert("RGBA" if has_alpha else "RGB")
            image.thumbnail((_MODEL_MAX_SIDE, _MODEL_MAX_SIDE), Image.Resampling.LANCZOS)
            # 不沿用 info，避免原图 EXIF、ICC、文本等元数据进入模型请求。
            image.info.clear()
            buffer = BytesIO()
            if has_alpha:
                image.save(buffer, format="PNG")
                media_type = "image/png"
            else:
                image.save(buffer, format="JPEG", quality=90)
                media_type = "image/jpeg"
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError) as exc:
        raise ReadError("图片无法读取或已损坏，请重新上传") from exc
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:{media_type};base64,{encoded}"
