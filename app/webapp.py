"""手机网页版服务（Flask）。

在电脑上和桌面版共用同一套 EXIF 读取 / AI 点评逻辑，
手机浏览器（或"添加到主屏幕"的 PWA）通过局域网访问。

路由：
- GET  /                单页应用（app/static/index.html）
- GET  /api/ping        校验访问密码（access_token 为空时直接放行）
- POST /api/inspect     上传照片，读取 EXIF 参数返回
- POST /api/critique    上传照片 + 参数，返回 AI 点评（markdown）
- GET/POST /api/settings 查看 / 更新 API 配置
"""
from __future__ import annotations

import functools
import os
import tempfile

from flask import Flask, jsonify, request, send_from_directory
from werkzeug.utils import secure_filename

from . import exif_utils, image_utils, storage
from .ai_client import ApiError, critique

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

# 表单字段名 -> EXIF 参数字段名（与 prompt._FIELD_LABELS 对应）
_PARAM_FIELDS = ("aperture", "shutter", "iso", "focal_length", "datetime", "camera")


def create_app() -> Flask:
    app = Flask(__name__, static_folder=STATIC_DIR, static_url_path="")
    app.config["MAX_CONTENT_LENGTH"] = 80 * 1024 * 1024  # 手机原图/RAW 可能很大

    # ---- 访问密码 ----

    def _token_ok() -> bool:
        token = storage.load_config().get("access_token") or ""
        if not token:
            return True  # 未设置密码：局域网内放行
        return request.headers.get("X-Access-Token", "") == token

    def require_token(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            if not _token_ok():
                return jsonify({"error": "需要访问密码"}), 401
            return fn(*args, **kwargs)

        return wrapper

    # ---- 页面 ----

    @app.get("/")
    def index():
        return send_from_directory(STATIC_DIR, "index.html")

    # ---- API ----

    @app.get("/api/ping")
    @require_token
    def ping():
        return jsonify({"ok": True})

    @app.post("/api/inspect")
    @require_token
    def inspect():
        file = request.files.get("image")
        if file is None or not file.filename:
            return jsonify({"error": "没有收到照片"}), 400
        tmp_path = _save_upload(file)
        try:
            params = exif_utils.read_exif(tmp_path)
        finally:
            _remove(tmp_path)
        return jsonify({"params": params})

    @app.post("/api/critique")
    @require_token
    def critique_route():
        file = request.files.get("image")
        if file is None or not file.filename:
            return jsonify({"error": "没有收到照片"}), 400

        cfg = storage.load_config()
        if not cfg.get("api_key"):
            return jsonify({"error": "服务器还没配置 API Key，请先在「设置」里填写"}), 400

        form = request.form
        params = {key: form.get(key, "").strip() for key in _PARAM_FIELDS}
        params = {key: value for key, value in params.items() if value}
        extra_time = form.get("extra", "").strip()
        intent = form.get("intent", "").strip()
        try:
            angle = float(form.get("angle", "0") or 0) % 360
        except ValueError:
            angle = 0.0

        tmp_path = _save_upload(file)
        try:
            image = image_utils.open_as_pil(tmp_path)
        except Exception as e:
            return jsonify({"error": f"无法读取这张照片：{e}"}), 400
        finally:
            _remove(tmp_path)

        if angle:  # angle 是顺时针度数；PIL rotate 为逆时针
            image = image.rotate(-angle, expand=True)

        try:
            result_text = critique(image, params, extra_time, intent, cfg)
        except ApiError as e:
            return jsonify({"error": str(e)}), 502
        except Exception as e:
            return jsonify({"error": f"点评失败：{e}"}), 500

        try:
            storage.save_record(file.filename, image, params, extra_time, intent, result_text)
        except Exception:
            pass  # 历史保存失败不影响返回点评

        return jsonify({"markdown": result_text})

    @app.get("/api/settings")
    @require_token
    def get_settings():
        cfg = storage.load_config()
        api_key = cfg.get("api_key") or ""
        providers = [
            {
                "name": p.get("name", ""),
                "base_url": p.get("base_url", ""),
                "model": p.get("model", ""),
                # 不下发完整 key，只给掩码提示
                "api_key_hint": f"…{p.get('api_key', '')[-4:]}" if p.get("api_key") else "",
            }
            for p in cfg.get("providers") or []
        ]
        return jsonify(
            {
                "base_url": cfg.get("base_url", ""),
                "model": cfg.get("model", ""),
                "api_key_set": bool(api_key),
                "api_key_hint": f"…{api_key[-4:]}" if api_key else "",
                "access_token_set": bool(cfg.get("access_token")),
                "active_provider": cfg.get("active_provider", ""),
                "providers": providers,
                "presets": storage.PROVIDER_PRESETS,
            }
        )

    @app.post("/api/settings")
    @require_token
    def post_settings():
        data = request.get_json(silent=True) or {}
        cfg = storage.load_config()

        # 提取实际提交的模型相关字段
        submitted_model_fields = {}
        for key in ("base_url", "model"):
            if key in data:
                submitted_model_fields[key] = str(data[key]).strip()

        # 提交过 base_url 或 model → 手动配置模式，解除服务商关联
        if submitted_model_fields:
            cfg.update(submitted_model_fields)
            cfg["active_provider"] = ""
        else:
            # 没动模型配置 → 保留服务商关联
            pass

        # api_key / access_token
        for key in ("api_key", "access_token"):
            if key in data:
                cfg[key] = str(data[key]).strip()

        storage.save_config(cfg)
        return jsonify({"ok": True})

    # ---- 服务商管理 ----

    @app.post("/api/providers/save")
    @require_token
    def save_provider():
        data = request.get_json(silent=True) or {}
        name = str(data.get("name", "")).strip()
        if not name:
            return jsonify({"error": "服务商名称不能为空"}), 400
        cfg = storage.load_config()
        storage.upsert_provider(
            cfg,
            name,
            str(data.get("base_url", "")).strip(),
            str(data.get("model", "")).strip(),
            str(data["api_key"]).strip() if data.get("api_key") else None,  # 不传/空 = 沿用已存 key
        )
        storage.save_config(cfg)
        return jsonify({"ok": True})

    @app.post("/api/providers/activate")
    @require_token
    def activate_provider():
        data = request.get_json(silent=True) or {}
        name = str(data.get("name", "")).strip()
        cfg = storage.load_config()
        if not storage.activate_provider(cfg, name):
            return jsonify({"error": "找不到这个服务商"}), 404
        storage.save_config(cfg)
        return jsonify({"ok": True})

    @app.post("/api/providers/remove")
    @require_token
    def remove_provider():
        data = request.get_json(silent=True) or {}
        name = str(data.get("name", "")).strip()
        cfg = storage.load_config()
        if not storage.remove_provider(cfg, name):
            return jsonify({"error": "找不到这个服务商"}), 404
        storage.save_config(cfg)
        return jsonify({"ok": True})

    @app.errorhandler(413)
    def too_large(_e):
        return jsonify({"error": "照片太大了（超过 80MB）"}), 413

    return app


def _save_upload(file) -> str:
    """把上传的文件存成带原后缀的临时文件（EXIF/RAW 解析依赖后缀判断格式）。"""
    suffix = os.path.splitext(secure_filename(file.filename or ""))[1].lower() or ".jpg"
    fd, tmp_path = tempfile.mkstemp(prefix="photo_", suffix=suffix)
    with os.fdopen(fd, "wb") as f:
        file.save(f)
    return tmp_path


def _remove(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass
