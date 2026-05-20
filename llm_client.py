import os
import traceback
import httpx
from dotenv import load_dotenv

load_dotenv()

_PROVIDERS = {
    "dashscope": {
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
        "api_key_env": "DASHSCOPE_API_KEY",
    },
    "deepseek": {
        "base_url": "https://api.deepseek.com/v1/chat/completions",
        "api_key_env": "DEEPSEEK_API_KEY",
    },
    "zhipu": {
        "base_url": "https://open.bigmodel.cn/api/paas/v4/chat/completions",
        "api_key_env": "ZHIPU_API_KEY",
    },
    "volc": {
        "base_url": "https://ark.cn-beijing.volces.com/api/v3/chat/completions",
        "api_key_env": "VOLC_API_KEY",
    },
}

_MODEL_PROVIDER = {
    "qwen": "dashscope",
    "deepseek": "deepseek",
    "glm": "zhipu",
    "doubao": "volc",
    "ep-": "volc",
    "doubao-seed": "volc",
}


def _get_provider(model: str) -> dict:
    for prefix, provider in _MODEL_PROVIDER.items():
        if model.lower().startswith(prefix):
            return _PROVIDERS[provider]
    return _PROVIDERS["dashscope"]


def _get_headers(model: str) -> dict:
    provider = _get_provider(model)
    api_key = os.getenv(provider["api_key_env"])
    if not api_key:
        raise RuntimeError(f"未检测到 {provider['api_key_env']}，请在 Streamlit Secrets 中配置")
    return {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}


def _raise_with_body(resp):
    """报错时把 API 返回的具体原因一并抛出。"""
    if not resp.is_success:
        try:
            detail = resp.json()
        except Exception:
            detail = resp.text
        raise RuntimeError(f"API错误 {resp.status_code}：{detail}")


def chat(model, system, user, temperature=0.3):
    provider = _get_provider(model)
    try:
        with httpx.Client(timeout=120) as client:
            resp = client.post(
                provider["base_url"],
                headers=_get_headers(model),
                json={
                    "model": model,
                    "temperature": temperature,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                },
            )
        _raise_with_body(resp)
        return resp.json()["choices"][0]["message"]["content"]
    except RuntimeError:
        raise
    except Exception as e:
        raise RuntimeError(f"详细错误：\n{traceback.format_exc()}") from e


def chat_with_image(image_b64: str, mime_type: str, prompt: str, model="qwen-vl-plus", temperature=0.1):
    """支持多平台视觉模型。GLM-OCR 使用专用格式，其余走 OpenAI 兼容格式。"""
    provider = _get_provider(model)
    api_key = os.getenv(provider["api_key_env"])
    if not api_key:
        raise RuntimeError(f"未检测到 {provider['api_key_env']}，请在 Streamlit Secrets 中配置")
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    model_lower = model.lower()
    is_zhipu = model_lower.startswith("glm")

    # 智谱 GLM 系列只接受 PNG，JPEG 会返回 400；自动转换
    if is_zhipu and mime_type in ("image/jpeg", "image/jpg"):
        try:
            from PIL import Image
            import io, base64 as _b64
            img = Image.open(io.BytesIO(_b64.b64decode(image_b64)))
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            image_b64 = _b64.b64encode(buf.getvalue()).decode()
            mime_type = "image/png"
        except Exception:
            pass  # 转换失败就原样发送，让 API 报错给用户看

    # GLM-OCR 使用智谱专用的 OCR 接口格式
    if model_lower == "glm-ocr":
        payload = {
            "model": model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{mime_type};base64,{image_b64}"},
                        },
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
        }
    else:
        # 其他模型统一用 OpenAI 兼容格式（DashScope / 智谱 GLM-4V / DeepSeek 均支持）
        payload = {
            "model": model,
            "temperature": temperature,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{mime_type};base64,{image_b64}"},
                        },
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
        }

    try:
        with httpx.Client(timeout=180) as client:
            resp = client.post(provider["base_url"], headers=headers, json=payload)
        _raise_with_body(resp)
        return resp.json()["choices"][0]["message"]["content"]
    except RuntimeError:
        raise
    except Exception as e:
        raise RuntimeError(f"详细错误：\n{traceback.format_exc()}") from e


def embed(text: str, model: str = "text-embedding-v3") -> list | None:
    """
    生成文本向量（DashScope 兼容接口）。
    返回 list[float] 或 None（失败时静默返回 None，不抛异常）。
    """
    api_key = os.getenv("DASHSCOPE_API_KEY")
    if not api_key:
        return None
    try:
        with httpx.Client(timeout=15) as client:
            resp = client.post(
                "https://dashscope.aliyuncs.com/compatible-mode/v1/embeddings",
                headers={"Authorization": f"Bearer {api_key}",
                         "Content-Type": "application/json"},
                json={"model": model, "input": text, "encoding_format": "float"},
            )
        if resp.is_success:
            return resp.json()["data"][0]["embedding"]
        return None
    except Exception:
        return None


def chat_with_images(images: list, prompt: str, model="qwen-vl-max", temperature=0.1):
    """
    多图输入视觉模型调用。
    images: [{"b64": "...", "mime": "image/jpeg"}, ...]
    GLM 系列自动将 JPEG 转 PNG。
    """
    provider = _get_provider(model)
    api_key = os.getenv(provider["api_key_env"])
    if not api_key:
        raise RuntimeError(f"未检测到 {provider['api_key_env']}，请在 Streamlit Secrets 中配置")
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    is_zhipu = model.lower().startswith("glm")

    content = []
    for img in images:
        b64, mime = img["b64"], img["mime"]
        if is_zhipu and mime in ("image/jpeg", "image/jpg"):
            try:
                from PIL import Image as _Img
                import io as _io, base64 as _b64mod
                pil = _Img.open(_io.BytesIO(_b64mod.b64decode(b64)))
                buf = _io.BytesIO()
                pil.save(buf, format="PNG")
                b64 = _b64mod.b64encode(buf.getvalue()).decode()
                mime = "image/png"
            except Exception:
                pass
        content.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}})
    content.append({"type": "text", "text": prompt})

    payload = {
        "model": model,
        "temperature": temperature,
        "messages": [{"role": "user", "content": content}],
    }
    try:
        with httpx.Client(timeout=240) as client:
            resp = client.post(provider["base_url"], headers=headers, json=payload)
        _raise_with_body(resp)
        return resp.json()["choices"][0]["message"]["content"]
    except RuntimeError:
        raise
    except Exception as e:
        raise RuntimeError(f"详细错误：\n{traceback.format_exc()}") from e
