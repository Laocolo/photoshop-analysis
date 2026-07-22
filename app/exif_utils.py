"""读取照片 EXIF 拍摄参数（光圈/快门/ISO/焦距/拍摄时间/相机）。

普通格式（JPG/PNG 等）用 Pillow 解析；
RAW 格式（.arw/.cr2/.nef 等 TIFF 结构）用 exifread 解析。
"""
from __future__ import annotations

from PIL import Image

from .image_utils import is_raw

# EXIF tag 编号（Pillow 路径用）
_FNUMBER = 33437       # ExifIFD: 光圈值
_EXPOSURE_TIME = 33434  # ExifIFD: 快门速度
_ISO = 34855            # ExifIFD: ISO
_FOCAL_LENGTH = 37386   # ExifIFD: 焦距
_DATETIME_ORIGINAL = 36867  # ExifIFD: 原始拍摄时间
_MAKE = 271             # IFD0: 厂商
_MODEL = 272            # IFD0: 型号
_DATETIME = 306         # IFD0: 修改时间（兜底）

_FIELDS = ("aperture", "shutter", "iso", "focal_length", "datetime", "camera")


def _empty() -> dict:
    return {key: None for key in _FIELDS}


def read_exif(path: str) -> dict:
    """读取 EXIF，返回 dict；缺失字段为 None（界面可手填）。

    字段：aperture / shutter / iso / focal_length / datetime / camera
    """
    if is_raw(path):
        return _read_exif_raw(path)
    return _read_exif_pillow(path)


# ---- 数值与格式化工具 ----

def _to_float(value):
    """EXIF 有理数（IFDRational/元组/数字）转 float，失败返回 None。"""
    if isinstance(value, (tuple, list)) and len(value) == 2:
        try:
            return float(value[0]) / float(value[1])
        except (TypeError, ValueError, ZeroDivisionError):
            return None
    try:
        return float(value)
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def _format_shutter_value(v: float | None) -> str | None:
    if v is None or v <= 0:
        return None
    if v >= 1:
        return f"{v:g}s"
    return f"1/{round(1 / v)}s"


def _format_shutter(value) -> str | None:
    return _format_shutter_value(_to_float(value))


def _join_camera(make: str, model: str) -> str | None:
    if model and make and not model.lower().startswith(make.lower()):
        return f"{make} {model}"
    return model or make or None


# ---- 普通格式：Pillow ----

def _read_exif_pillow(path: str) -> dict:
    result = _empty()
    try:
        with Image.open(path) as img:
            exif = img.getexif()
    except Exception:
        return result
    if not exif:
        return result

    try:
        sub = exif.get_ifd(0x8769)  # Exif IFD
    except KeyError:
        sub = {}

    fnum = _to_float(sub.get(_FNUMBER))
    if fnum:
        result["aperture"] = f"f/{fnum:g}"

    result["shutter"] = _format_shutter(sub.get(_EXPOSURE_TIME))

    iso = sub.get(_ISO)
    if isinstance(iso, (tuple, list)):
        iso = iso[0] if iso else None
    if iso:
        result["iso"] = f"ISO {iso}"

    focal = _to_float(sub.get(_FOCAL_LENGTH))
    if focal:
        result["focal_length"] = f"{focal:g}mm"

    dto = sub.get(_DATETIME_ORIGINAL) or exif.get(_DATETIME)
    if dto:
        result["datetime"] = str(dto).strip() or None

    make = str(exif.get(_MAKE) or "").strip()
    model = str(exif.get(_MODEL) or "").strip()
    result["camera"] = _join_camera(make, model)

    return result


# ---- RAW：exifread ----

def _values(tags: dict, name: str):
    tag = tags.get(name)
    return tag.values if tag is not None else None


def _raw_number(value) -> float | None:
    """exifread 的值（Ratio/列表/数字）转 float。"""
    if isinstance(value, (list, tuple)):
        value = value[0] if value else None
    if value is None:
        return None
    num = getattr(value, "num", None)
    den = getattr(value, "den", None)
    if num is not None and den is not None:
        try:
            return float(num) / float(den)
        except (TypeError, ZeroDivisionError):
            return None
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return None


def _raw_text(tags: dict, name: str) -> str:
    tag = tags.get(name)
    return str(tag).strip() if tag is not None else ""


def _read_exif_raw(path: str) -> dict:
    result = _empty()
    try:
        import exifread

        with open(path, "rb") as f:
            tags = exifread.process_file(f, details=False)
    except Exception:
        return result
    if not tags:
        return result

    fnum = _raw_number(_values(tags, "EXIF FNumber"))
    if fnum:
        result["aperture"] = f"f/{fnum:g}"

    result["shutter"] = _format_shutter_value(_raw_number(_values(tags, "EXIF ExposureTime")))

    iso = _raw_number(_values(tags, "EXIF ISOSpeedRatings"))
    if iso:
        result["iso"] = f"ISO {iso:g}"

    focal = _raw_number(_values(tags, "EXIF FocalLength"))
    if focal:
        result["focal_length"] = f"{focal:g}mm"

    dto = _raw_text(tags, "EXIF DateTimeOriginal") or _raw_text(tags, "Image DateTime")
    result["datetime"] = dto or None

    result["camera"] = _join_camera(_raw_text(tags, "Image Make"), _raw_text(tags, "Image Model"))

    return result
