# -*- coding: utf-8 -*-
"""手机端补齐功能的冒烟测试：验证 /api/test-connection、历史记录、token query 兼容。

用法：.venv/Scripts/python _test_web_parity.py
在临时目录里跑，不污染真实的 config.json / history/。
"""
import tempfile
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from app import storage

tmp = Path(tempfile.mkdtemp())
storage.CONFIG_PATH = tmp / "config.json"
storage.HISTORY_DIR = tmp / "history"

from app.webapp import create_app
c = create_app().test_client()

# ---- 1. 测试连接 ----
with patch("app.webapp.test_connection", return_value="连接正常") as m:
    r = c.post("/api/test-connection", json={"base_url": "https://x/v1", "model": "m1", "api_key": "k1"})
    assert r.status_code == 200 and r.get_json()["reply"] == "连接正常", r.get_json()
    called = m.call_args[0][0]
    assert called == {"base_url": "https://x/v1", "model": "m1", "api_key": "k1"}, called
# key 留空 → 回落到已保存的
cfg = storage.load_config()
cfg["api_key"] = "saved-key"
cfg["base_url"] = "https://saved/v1"
storage.save_config(cfg)
with patch("app.webapp.test_connection", return_value="连接正常") as m:
    r = c.post("/api/test-connection", json={"base_url": "", "model": "", "api_key": ""})
    called = m.call_args[0][0]
    assert called["api_key"] == "saved-key" and called["base_url"] == "https://saved/v1", called
# 完全没有 key → 400
cfg["api_key"] = ""
storage.save_config(cfg)
r = c.post("/api/test-connection", json={})
assert r.status_code == 400
print("1. /api/test-connection OK")

# ---- 2. 历史记录 ----
img = Image.new("RGB", (800, 600), (100, 150, 200))
storage.save_record("test.jpg", img, {"iso": "100"}, "傍晚", "人像", "## 总评\n测试点评")
storage.save_record("test2.jpg", img, {}, "", "", "## 总评\n第二条")

r = c.get("/api/history")
recs = r.get_json()["records"]
assert len(recs) == 2 and recs[0]["has_thumb"] and recs[0]["image_name"] == "test2.jpg", recs

rid = recs[1]["id"]
r = c.get(f"/api/history/{rid}")
data = r.get_json()
assert "测试点评" in data["markdown"] and data["image_name"] == "test.jpg", data

r = c.get(f"/api/history/{rid}/thumb")
assert r.status_code == 200 and r.data[:2] == b"\xff\xd8"  # JPEG
Image.open(__import__("io").BytesIO(r.data)).verify()

r = c.get("/api/history/not-exist")
assert r.status_code == 404
# 目录穿越防护
r = c.get("/api/history/..%2F..%2Fconfig")
assert r.status_code in (404, 400), r.status_code
print("2. /api/history OK")

# ---- 3. token 兼容 query 参数 ----
cfg["access_token"] = "secret123"
storage.save_config(cfg)
r = c.get("/api/history", headers={"X-Access-Token": "wrong"})
assert r.status_code == 401
r = c.get("/api/history", headers={"X-Access-Token": "secret123"})
assert r.status_code == 200
r = c.get(f"/api/history/{rid}/thumb?token=secret123")  # <img> 场景
assert r.status_code == 200
r = c.get(f"/api/history/{rid}/thumb")
assert r.status_code == 401
print("3. token query 兼容 OK")

print("ALL TESTS PASSED")
