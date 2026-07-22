"""配置（config.json）与历史记录（history/）存储。

开发时保存在项目目录；打包成 exe 后保存在 exe 所在目录，
避免写入 PyInstaller 的临时解压目录导致配置丢失。
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent
else:
    BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = BASE_DIR / "config.json"
HISTORY_DIR = BASE_DIR / "history"

DEFAULT_CONFIG = {
    "api_key": "",
    "base_url": "https://api.moonshot.cn/v1",
    "model": "kimi-latest",
}


def load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    try:
        cfg.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
    except Exception:
        pass
    return cfg


def save_config(cfg: dict) -> None:
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def save_record(image_name: str, image, params: dict, extra_time: str, intent: str, result_text: str) -> Path:
    """保存一次点评：缩略图 + meta.json + result.md，返回记录目录。

    image_name 为原文件名；image 为 PIL Image（用户旋转调整后的画面）。
    """
    rec_dir = HISTORY_DIR / time.strftime("%Y%m%d_%H%M%S")
    suffix = 1
    while rec_dir.exists():  # 同一秒多次点评时避免覆盖
        rec_dir = HISTORY_DIR / f"{time.strftime('%Y%m%d_%H%M%S')}_{suffix}"
        suffix += 1
    rec_dir.mkdir(parents=True)

    meta = {
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "image_name": image_name,
        "params": params,
        "extra_time": extra_time,
        "intent": intent,
    }
    (rec_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    (rec_dir / "result.md").write_text(result_text, encoding="utf-8")

    try:
        img = image.copy()
        img.thumbnail((320, 320))
        img.convert("RGB").save(rec_dir / "thumb.jpg", "JPEG", quality=80)
    except Exception:
        pass
    return rec_dir


def list_records() -> list:
    """按时间倒序返回 [(记录目录, meta_dict), ...]。"""
    records = []
    if not HISTORY_DIR.is_dir():
        return records
    for d in sorted(HISTORY_DIR.iterdir(), reverse=True):
        meta_file = d / "meta.json"
        if d.is_dir() and meta_file.exists():
            try:
                records.append((d, json.loads(meta_file.read_text(encoding="utf-8"))))
            except Exception:
                continue
    return records


def load_record(rec_dir: Path):
    """读取一条历史记录，返回 (meta, 点评选文, 缩略图路径)；不存在返回 None。"""
    result_file = rec_dir / "result.md"
    if not result_file.exists():
        return None
    meta = {}
    meta_file = rec_dir / "meta.json"
    if meta_file.exists():
        try:
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
        except Exception:
            pass
    return meta, result_file.read_text(encoding="utf-8"), rec_dir / "thumb.jpg"
