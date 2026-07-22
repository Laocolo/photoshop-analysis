"""调用 OpenAI 兼容的视觉大模型 API（默认 Kimi / Moonshot）。"""
from __future__ import annotations

import base64
import io

import requests
from PIL import Image

from .prompt import SYSTEM_PROMPT, build_user_prompt

MAX_IMAGE_SIDE = 1568  # 发给 API 的图片长边上限
JPEG_QUALITY = 85


class ApiError(Exception):
    """对用户友好的 API 错误。"""


def _encode_image(img: Image.Image) -> str:
    """压缩图片并转成 data-uri（base64）。"""
    img = img.copy()  # 不改动调用方的图
    if img.mode != "RGB":
        img = img.convert("RGB")
    img.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=JPEG_QUALITY)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def _post(cfg: dict, messages: list, timeout: int = 120) -> str:
    url = cfg["base_url"].rstrip("/") + "/chat/completions"
    headers = {
        "Authorization": "Bearer " + cfg["api_key"],
        "Content-Type": "application/json",
    }
    # 不传 temperature：Kimi Code 编码接口不接受该参数（会报 400 invalid temperature）
    payload = {"model": cfg["model"], "messages": messages}
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=timeout)
    except requests.RequestException as e:
        raise ApiError(f"网络错误，请检查网络后重试：{e}") from e

    if resp.status_code == 401:
        raise ApiError("API Key 无效（401），请在设置中检查。")
    if resp.status_code == 429:
        raise ApiError("请求过于频繁（429），请稍后再试。")
    if resp.status_code != 200:
        raise ApiError(f"API 返回错误 {resp.status_code}：{resp.text[:300]}")

    try:
        return resp.json()["choices"][0]["message"]["content"]
    except (KeyError, IndexError, ValueError) as e:
        raise ApiError(f"无法解析 API 响应：{e}") from e


def critique(image: Image.Image, params: dict, extra_time: str, intent: str, cfg: dict) -> str:
    """把照片（PIL Image，已按用户调整转正）+ 参数发给视觉模型，返回 markdown 点评选文。"""
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": build_user_prompt(params, extra_time, intent)},
                {"type": "image_url", "image_url": {"url": _encode_image(image)}},
            ],
        },
    ]
    return _post(cfg, messages)


def test_connection(cfg: dict) -> str:
    """发一条纯文本短消息验证 key / base_url / model 可用。"""
    return _post(cfg, [{"role": "user", "content": "你好，请只回复“连接正常”四个字。"}], timeout=30)
