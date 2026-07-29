"""按点评的「后期调整建议」调用图片编辑模型（Agnes / 豆包 SeedEdit / 魔搭 Qwen-Image-Edit 等）优化照片。

接口为 OpenAI 风格的 POST {base_url}/images/generations，各家参数风格略有差异：
- 豆包 SeedEdit：顶层 image 字段收 data-uri，response_format="b64_json"
- Agnes：输入图放 extra_body.image 数组，size 必填
- 魔搭 ModelScope：异步任务模式，image_url 收 data-uri/base64，返回 task_id 后轮询
先按豆包/OpenAI 风格请求，400 时自动换 Agnes 风格重试；魔搭地址走独立分支。
"""
from __future__ import annotations

import base64
import io
import re
import time

import requests
from PIL import Image

from . import storage
from .ai_client import ApiError

MAX_INPUT_SIDE = 1536   # 发给图片模型的原图长边上限
OUTPUT_LONG_SIDE = 1024  # 输出图长边（等比）
TIMEOUT = 240           # 图片生成较慢
RETRY_DELAYS = (15, 30)  # 队列满/限流（503/429）时的自动重试等待秒数，共 3 次尝试

_EDIT_INSTRUCTION = (
    "你是一名照片后期专家。请严格根据以下后期调整建议处理这张照片，"
    "保持画面主体、内容和构图不变，只做建议中的影调、色彩、裁剪、锐化等调整，"
    "不要添加或删除元素，不要改变拍摄场景：\n\n"
)


def extract_suggestions(markdown: str) -> str:
    """从点评 markdown 中提取「## 后期调整建议」小节；提取不到返回全文。"""
    match = re.search(r"^#{1,4}\s*后期调整建议\s*$\n(.*?)(?=^#{1,4}\s|\Z)", markdown, re.M | re.S)
    if match and match.group(1).strip():
        return match.group(1).strip()
    return markdown.strip()


def _resolve_credentials(cfg: dict) -> tuple[str, str, str]:
    """返回 (base_url, api_key, model)。image_provider 非空时用对应服务商的凭证，
    且该服务商若存了 image_model 则优先于全局 image_model。"""
    name = cfg.get("image_provider") or ""
    if name:
        p = storage.find_provider(cfg, name)
        if p is None:
            raise ApiError(f"图片优化配置的服务商「{name}」不存在，请在设置中重新选择。")
        model = p.get("image_model") or cfg.get("image_model") or "agnes-image-2.1-flash"
        return p.get("base_url", ""), p.get("api_key", ""), model
    model = cfg.get("image_model") or "agnes-image-2.1-flash"
    return cfg.get("base_url", ""), cfg.get("api_key", ""), model


def _size_for(image: Image.Image) -> str:
    """输出尺寸 "WxH"：长边压到 OUTPUT_LONG_SIDE、等比、取 16 的倍数。"""
    w, h = image.size
    scale = OUTPUT_LONG_SIDE / max(w, h)
    if scale > 1:
        scale = 1.0
    w16 = max(16, round(w * scale / 16) * 16)
    h16 = max(16, round(h * scale / 16) * 16)
    return f"{w16}x{h16}"


def encode_data_uri(image: Image.Image) -> str:
    img = image.copy()
    if img.mode != "RGB":
        img = img.convert("RGB")
    img.thumbnail((MAX_INPUT_SIDE, MAX_INPUT_SIDE))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=85)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def _decode_image(data: dict) -> Image.Image:
    item = (data.get("data") or [{}])[0]
    b64 = item.get("b64_json")
    if b64:
        return Image.open(io.BytesIO(base64.b64decode(b64)))
    url = item.get("url")
    if url:
        try:
            resp = requests.get(url, timeout=120)
            resp.raise_for_status()
        except requests.RequestException as e:
            raise ApiError(f"下载优化结果失败：{e}") from e
        return Image.open(io.BytesIO(resp.content))
    raise ApiError("图片接口没有返回图像数据。")


def _post_images(url: str, headers: dict, payload: dict) -> dict:
    last_err: ApiError | None = None
    for delay in (0, *RETRY_DELAYS):
        if delay:
            time.sleep(delay)  # 队列满/限流，等一会儿再试
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=TIMEOUT)
        except requests.RequestException as e:
            # 网络/代理抖动（如代理客户端瞬间断连），稍后自动重试
            last_err = ApiError(
                f"网络错误，请检查网络后重试：{e}"
                "（已自动重试仍失败；如果开了代理，请确认代理客户端运行正常）"
            )
            continue
        if resp.status_code == 401:
            raise ApiError("图片模型的 API Key 无效（401），请在设置中检查图片优化配置。")
        if resp.status_code in (429, 503):
            # 限流 / 队列满（如 Agnes 免费档高峰期），稍后自动重试
            last_err = ApiError(
                f"图片接口返回错误 {resp.status_code}：{resp.text[:300]}"
                "（模型队列繁忙，已自动重试仍失败，请稍后再点一次）"
            )
            last_err.status = resp.status_code
            continue
        if resp.status_code != 200:
            err = ApiError(f"图片接口返回错误 {resp.status_code}：{resp.text[:300]}")
            err.status = resp.status_code
            raise err
        try:
            return resp.json()
        except ValueError as e:
            raise ApiError(f"无法解析图片接口响应：{e}") from e
    raise last_err


def _optimize_modelscope(base_url: str, api_key: str, model: str, prompt: str, data_uri: str, size: str) -> Image.Image:
    """魔搭 API-Inference 异步图片编辑：提交任务 → 轮询 → 下载结果图。"""
    base = base_url.rstrip("/")
    headers = {
        "Authorization": "Bearer " + api_key,
        "Content-Type": "application/json",
        "X-ModelScope-Async-Mode": "true",  # 魔搭图片模型要求异步模式
    }
    b64 = data_uri.split(",", 1)[-1]
    # image_url 官方文档称支持 url 或 base64；先带尺寸 data-uri，400 依次降级
    payloads = [
        {"model": model, "prompt": prompt, "image_url": [data_uri], "size": size},
        {"model": model, "prompt": prompt, "image_url": [data_uri]},
        {"model": model, "prompt": prompt, "image_url": [b64]},
    ]
    task_id = None
    last_err: ApiError | None = None
    for payload in payloads:
        try:
            resp = requests.post(base + "/images/generations", headers=headers, json=payload, timeout=TIMEOUT)
        except requests.RequestException as e:
            raise ApiError(f"网络错误，请检查网络后重试：{e}")
        if resp.status_code == 401:
            raise ApiError("图片模型的 API Key 无效（401），请在设置中检查图片优化配置。")
        if resp.status_code == 400:
            last_err = ApiError(f"图片接口返回错误 400：{resp.text[:300]}")
            continue  # 参数风格不匹配，换下一种
        if resp.status_code != 200:
            err = ApiError(f"图片接口返回错误 {resp.status_code}：{resp.text[:300]}")
            err.status = resp.status_code
            raise err
        task_id = resp.json().get("task_id")
        if task_id:
            break
    if not task_id:
        raise last_err or ApiError("魔搭接口没有返回 task_id，请检查图片模型名是否正确。")

    poll_headers = {**headers, "X-ModelScope-Task-Type": "image_generation"}
    for _ in range(60):  # 每 5 秒查一次，最长约 5 分钟
        time.sleep(5)
        try:
            resp = requests.get(base + "/tasks/" + task_id, headers=poll_headers, timeout=60)
        except requests.RequestException:
            continue  # 轮询抖动，下次再查
        data = resp.json()
        status = data.get("task_status")
        if status == "SUCCEED":
            img_url = (data.get("output_images") or [None])[0]
            if not img_url:
                raise ApiError("魔搭任务完成但没有返回结果图片。")
            try:
                r = requests.get(img_url, timeout=120)
                r.raise_for_status()
            except requests.RequestException as e:
                raise ApiError(f"下载优化结果失败：{e}") from e
            return Image.open(io.BytesIO(r.content))
        if status == "FAILED":
            raise ApiError(f"魔搭图片任务失败：{str(data)[:300]}")
    raise ApiError("魔搭图片任务超时（约 5 分钟未完成），请稍后重试。")


def optimize(image: Image.Image, suggestions: str, cfg: dict) -> Image.Image:
    """按建议优化照片，返回优化后的 PIL Image。"""
    base_url, api_key, model = _resolve_credentials(cfg)
    if not api_key:
        raise ApiError("还没配置图片优化模型的 API Key，请在设置中配置「图片优化」。")
    if not base_url:
        raise ApiError("还没配置图片优化模型的接口地址，请在设置中配置「图片优化」。")

    prompt = _EDIT_INSTRUCTION + suggestions
    data_uri = encode_data_uri(image)

    if "modelscope" in base_url:  # 魔搭走异步任务分支
        return _optimize_modelscope(base_url, api_key, model, prompt, data_uri, _size_for(image))

    url = base_url.rstrip("/") + "/images/generations"
    headers = {"Authorization": "Bearer " + api_key, "Content-Type": "application/json"}
    size = _size_for(image)

    # 豆包/OpenAI 风格：顶层 image + response_format
    payload_a = {
        "model": model,
        "prompt": prompt,
        "size": size,
        "response_format": "b64_json",
        "image": data_uri,
    }
    try:
        return _decode_image(_post_images(url, headers, payload_a))
    except ApiError as e:
        # 400 类错误可能是参数风格不匹配，换 Agnes 风格重试一次
        if getattr(e, "status", None) != 400:
            raise
    payload_b = {
        "model": model,
        "prompt": prompt,
        "size": size,
        "extra_body": {"image": [data_uri], "response_format": "b64_json"},
    }
    return _decode_image(_post_images(url, headers, payload_b))
