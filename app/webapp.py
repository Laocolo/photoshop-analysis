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

from flask import Flask, jsonify, request, send_file, send_from_directory
from werkzeug.utils import secure_filename

from . import exif_utils, image_edit, image_utils, prompt, storage
from .ai_client import ApiError, critique, test_connection

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
        # <img> 标签无法带请求头，缩略图等场景允许用 query 参数传密码
        return (request.headers.get("X-Access-Token", "") == token
                or request.args.get("token", "") == token)

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
        resp = send_from_directory(STATIC_DIR, "index.html")
        # 禁止缓存首页：手机端（尤其 iOS「添加到主屏幕」的 PWA）容易缓存旧页面，
        # 导致功能更新后看不到新界面。API 与桌面端逻辑不受影响。
        resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        resp.headers["Pragma"] = "no-cache"
        resp.headers["Expires"] = "0"
        return resp

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

    @app.post("/api/optimize")
    @require_token
    def optimize_route():
        file = request.files.get("image")
        if file is None or not file.filename:
            return jsonify({"error": "没有收到照片"}), 400
        critique_text = request.form.get("critique", "").strip()
        # 前端编辑过的建议文本优先；否则从点评 markdown 中提取
        suggestions = request.form.get("suggestions", "").strip()
        if not suggestions and not critique_text:
            return jsonify({"error": "缺少优化建议，请先完成一次点评"}), 400
        try:
            angle = float(request.form.get("angle", "0") or 0) % 360
        except ValueError:
            angle = 0.0

        tmp_path = _save_upload(file)
        try:
            image = image_utils.open_as_pil(tmp_path)
        except Exception as e:
            return jsonify({"error": f"无法读取这张照片：{e}"}), 400
        finally:
            _remove(tmp_path)

        if angle:
            image = image.rotate(-angle, expand=True)

        cfg = storage.load_config()
        if not suggestions:
            suggestions = image_edit.extract_suggestions(critique_text)
        try:
            optimized = image_edit.optimize(image, suggestions, cfg)
        except ApiError as e:
            return jsonify({"error": str(e)}), 502
        except Exception as e:
            return jsonify({"error": f"优化失败：{e}"}), 500

        return jsonify({"image": image_edit.encode_data_uri(optimized)})

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
                "image_model": p.get("image_model", ""),
                # 完整 key 一并下发，供界面回显（与桌面端一致）；另附掩码提示便于列表展示
                "api_key": p.get("api_key", ""),
                "api_key_hint": f"…{p.get('api_key', '')[-4:]}" if p.get("api_key") else "",
            }
            for p in cfg.get("providers") or []
        ]
        return jsonify(
            {
                "base_url": cfg.get("base_url", ""),
                "model": cfg.get("model", ""),
                "api_key": api_key,
                "api_key_set": bool(api_key),
                "api_key_hint": f"…{api_key[-4:]}" if api_key else "",
                "access_token_set": bool(cfg.get("access_token")),
                "active_provider": cfg.get("active_provider", ""),
                "providers": providers,
                "presets": storage.PROVIDER_PRESETS,
                "image_provider": cfg.get("image_provider", ""),
                "image_model": cfg.get("image_model", ""),
                "image_model_presets": storage.IMAGE_MODEL_PRESETS,
                "critique_role": cfg.get("critique_role", ""),
                # 内置预设 + 用户自建合并；custom=该名存在自定义版本（可编辑/删除），
                # prompt=当前生效的描述（自定义优先），供界面编辑预填
                "role_presets": _merged_roles(cfg),
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

        # 图片优化配置 + 点评角色（与点评服务商关联无关）
        for key in ("image_provider", "image_model", "critique_role"):
            if key in data:
                cfg[key] = str(data[key]).strip()

        # 切图片优化服务商时，把当前填的图片模型同步存回该服务商。
        # 否则服务商里残留的旧模型名会在运行时覆盖新配置（「切到哪家用哪家」）。
        if "image_provider" in data and "image_model" in data and cfg.get("image_provider"):
            p = storage.find_provider(cfg, cfg["image_provider"])
            if p is not None:
                p["image_model"] = cfg["image_model"]

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
            str(data["image_model"]).strip() if "image_model" in data else None,  # 不传 = 沿用已存
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

    # ---- 自定义点评角色 ----

    @app.post("/api/roles/save")
    @require_token
    def save_role():
        data = request.get_json(silent=True) or {}
        name = str(data.get("name", "")).strip()
        prompt_text = str(data.get("prompt", "")).strip()
        if not name or not prompt_text:
            return jsonify({"error": "角色名称和角色描述都不能为空"}), 400
        cfg = storage.load_config()
        storage.upsert_custom_role(cfg, name, prompt_text)
        cfg["critique_role"] = name  # 保存后直接使用新角色
        storage.save_config(cfg)
        return jsonify({"ok": True})

    @app.post("/api/roles/remove")
    @require_token
    def remove_role():
        data = request.get_json(silent=True) or {}
        name = str(data.get("name", "")).strip()
        cfg = storage.load_config()
        if not storage.remove_custom_role(cfg, name):
            return jsonify({"error": "找不到这个自定义角色"}), 404
        if any(r["name"] == name for r in prompt.ROLE_PRESETS):
            cfg["critique_role"] = name  # 删的是内置角色的覆盖版 → 回落到内置版
        storage.save_config(cfg)
        return jsonify({"ok": True})

    # ---- 测试连接（与桌面端设置里的「测试连接」一致） ----

    @app.post("/api/test-connection")
    @require_token
    def test_conn():
        data = request.get_json(silent=True) or {}
        cfg = storage.load_config()
        test_cfg = {
            "base_url": str(data.get("base_url", "")).strip() or cfg.get("base_url", ""),
            "model": str(data.get("model", "")).strip() or cfg.get("model", ""),
            # 没填 key 用已保存的（网页端不回传完整 key）
            "api_key": str(data.get("api_key", "")).strip() or cfg.get("api_key", ""),
        }
        if not test_cfg["api_key"]:
            return jsonify({"error": "请先填写 API Key"}), 400
        try:
            reply = test_connection(test_cfg)
        except ApiError as e:
            return jsonify({"error": str(e)}), 502
        return jsonify({"ok": True, "reply": reply})

    # ---- 历史记录（与桌面端历史面板一致） ----

    @app.get("/api/history")
    @require_token
    def history_list():
        items = [
            {
                "id": d.name,
                "time": meta.get("time", ""),
                "image_name": meta.get("image_name", ""),
                "has_thumb": (d / "thumb.jpg").exists(),
            }
            for d, meta in storage.list_records()
        ]
        return jsonify({"records": items})

    @app.get("/api/history/<rid>")
    @require_token
    def history_get(rid):
        rec = storage.load_record(_record_dir(rid))
        if rec is None:
            return jsonify({"error": "记录不存在"}), 404
        meta, text, thumb = rec
        return jsonify({
            "markdown": text,
            "time": meta.get("time", ""),
            "image_name": meta.get("image_name", ""),
            "has_thumb": thumb.exists(),
        })

    @app.get("/api/history/<rid>/thumb")
    @require_token
    def history_thumb(rid):
        thumb = _record_dir(rid) / "thumb.jpg"
        if not thumb.exists():
            return jsonify({"error": "没有缩略图"}), 404
        return send_file(thumb)

    @app.errorhandler(413)
    def too_large(_e):
        return jsonify({"error": "照片太大了（超过 80MB）"}), 413

    return app


def _merged_roles(cfg: dict) -> list:
    """内置角色 + 自定义角色合并列表：custom=该名存在自定义版本，prompt=生效描述（自定义优先）。"""
    custom = {r.get("name", ""): r.get("prompt", "") for r in cfg.get("custom_roles") or []}
    builtin_names = {r["name"] for r in prompt.ROLE_PRESETS}
    roles = [
        {
            "name": r["name"],
            "builtin": True,
            "custom": r["name"] in custom,
            "prompt": custom.get(r["name"], r["prompt"]),
        }
        for r in prompt.ROLE_PRESETS
    ]
    roles.extend(
        {"name": name, "builtin": False, "custom": True, "prompt": p}
        for name, p in custom.items()
        if name not in builtin_names
    )
    return roles


def _record_dir(rid: str):
    """历史记录目录；secure_filename 过滤路径分隔符，防目录穿越。"""
    return storage.HISTORY_DIR / secure_filename(rid)


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
