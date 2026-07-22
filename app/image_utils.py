"""统一取图入口：普通格式直接打开，RAW 抽取内嵌预览图，HEIC 经 pillow-heif 注册后走 Pillow。"""
from __future__ import annotations

import io
from pathlib import Path

from PIL import Image, ImageOps
from pillow_heif import register_heif_opener

register_heif_opener()  # 让 Pillow 支持 HEIC/HEIF（iPhone 默认原图格式）

# 支持的 RAW 后缀（均为 TIFF 结构；佳能新格式 .cr3 暂不支持）
RAW_SUFFIXES = {".arw", ".cr2", ".nef", ".dng", ".rw2", ".orf", ".raf"}

# iPhone 等手机的 HEIC/HEIF 后缀
HEIF_SUFFIXES = {".heic", ".heif"}


def is_raw(path) -> bool:
    return Path(str(path)).suffix.lower() in RAW_SUFFIXES


def is_heif(path) -> bool:
    return Path(str(path)).suffix.lower() in HEIF_SUFFIXES


def open_as_pil(path: str) -> Image.Image:
    """以 PIL Image（RGB）返回图片。

    RAW 文件优先抽取内嵌 JPG 预览（快，不做 RAW 解码）；
    没有内嵌预览时降级为半尺寸解码（慢一些，但保证有图）。
    """
    if not is_raw(path):
        img = Image.open(path)
        img = ImageOps.exif_transpose(img)  # 按 EXIF 方向标记自动转正（手机照片常见）
        return img.convert("RGB")

    import rawpy  # 延迟导入：处理普通图片时不依赖

    with rawpy.imread(path) as raw:
        try:
            thumb = raw.extract_thumb()
        except rawpy.LibRawNoThumbnailError:
            thumb = None
        if thumb is not None and thumb.format == rawpy.ThumbFormat.JPEG:
            img = Image.open(io.BytesIO(thumb.data))
        elif thumb is not None:  # BITMAP 格式缩略图
            img = Image.fromarray(thumb.data)
        else:
            img = Image.fromarray(raw.postprocess(half_size=True))
    return img.convert("RGB")
