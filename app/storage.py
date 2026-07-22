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
    "access_token": "",  # 网页版访问密码，为空则不校验（仅局域网使用时建议留空）
    "active_provider": "",  # 当前关联的服务商名；空 = 手动配置
    "providers": [],  # 已保存的服务商：[{"name", "base_url", "model", "api_key"}]
}

# 内置服务商模板：UI 选择后自动填入 base_url / model，用户只需补 API Key
PROVIDER_PRESETS = [
    {"name": "Kimi（Moonshot）", "base_url": "https://api.moonshot.cn/v1", "model": "kimi-latest"},
    {"name": "Kimi Code", "base_url": "https://api.kimi.com/coding/v1", "model": "kimi-for-coding"},
    {"name": "豆包（火山方舟）", "base_url": "https://ark.cn-beijing.volces.com/api/v3", "model": ""},
    {"name": "Agnes AI", "base_url": "https://apihub.agnes-ai.com/v1", "model": "agnes-2.0-flash"},
]


def load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    try:
        cfg.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
    except Exception:
        pass
    return cfg


def save_config(cfg: dict) -> None:
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


# ---- 服务商管理 ----
# 规则：AI 调用永远读顶层 api_key/base_url/model；"启用服务商"就是把该服务商的
# 三字段拷到顶层并记 active_provider；手动改设置则 active_provider 置空。

def find_provider(cfg: dict, name: str) -> dict | None:
    for p in cfg.get("providers") or []:
        if p.get("name") == name:
            return p
    return None


def upsert_provider(cfg: dict, name: str, base_url: str, model: str, api_key: str | None = None) -> dict:
    """按名称新建或更新服务商；api_key 传 None 表示沿用已保存的 key。"""
    p = find_provider(cfg, name)
    if p is None:
        p = {"name": name, "base_url": "", "model": "", "api_key": ""}
        cfg.setdefault("providers", []).append(p)
    p["base_url"] = base_url
    p["model"] = model
    if api_key is not None:
        p["api_key"] = api_key
    return p


def remove_provider(cfg: dict, name: str) -> bool:
    providers = cfg.get("providers") or []
    before = len(providers)
    cfg["providers"] = [p for p in providers if p.get("name") != name]
    if len(cfg["providers"]) == before:
        return False
    if cfg.get("active_provider") == name:
        cfg["active_provider"] = ""  # 顶层字段保留，服务不受影响
    return True


def activate_provider(cfg: dict, name: str) -> bool:
    """把服务商的配置拷贝为当前生效配置。"""
    p = find_provider(cfg, name)
    if p is None:
        return False
    cfg["base_url"] = p.get("base_url", "")
    cfg["model"] = p.get("model", "")
    cfg["api_key"] = p.get("api_key", "")
    cfg["active_provider"] = name
    return True


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
