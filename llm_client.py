import os
import traceback
import httpx
from dotenv import load_dotenv

load_dotenv()

# 每个提供商的接口地址和对应的环境变量名
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

# 模型名前缀 → 提供商映射
_MODEL_PROVIDER = {
    "qwen": "dashscope",
    "deepseek": "deepseek",
    "glm": "zhipu",
    "doubao": "volc",
    "ep-": "volc",   # 火山引擎 endpoint ID 格式
}


def _get_provider(model: str) -> dict:
    """根据模型名自动选择提供商配置。"""
    for prefix, provider in _MODEL_PROVIDER.items():
        if model.lower().startswith(prefix):
            return _PROVIDERS[provider]
    return _PROVIDERS["dashscope"]  # 默认走 DashScope


def _get_headers(model: str) -> dict:
    provider = _get_provider(model)
    api_key = os.getenv(provider["api_key_env"])
    if not api_key:
        raise RuntimeError(
            f"未检测到 {provider['api_key_env']}，请在 Streamlit Secrets 或 .env 中配置"
        )
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }


def chat(model, system, user, temperature=0.3):
    provider = _get_provider(model)
    try:
        with httpx.Client(timeout=60) as client:
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
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]
    except Exception as e:
        raise RuntimeError(f"详细错误：\n{traceback.format_exc()}") from e


def chat_with_image(image_b64: str, mime_type: str, prompt: str, model="qwen-vl-plus", temperature=0.1):
    # 图片识别固定走 DashScope（其他平台暂不支持）
    provider = _PROVIDERS["dashscope"]
    api_key = os.getenv(provider["api_key_env"])
    if not api_key:
        raise RuntimeError("未检测到 DASHSCOPE_API_KEY")
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    try:
        with httpx.Client(timeout=60) as client:
            resp = client.post(
                provider["base_url"],
                headers=headers,
                json={
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
                },
            )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]
    except Exception as e:
        raise RuntimeError(f"详细错误：\n{traceback.format_exc()}") from e
