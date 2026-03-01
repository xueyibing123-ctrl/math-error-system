import os
import json
import requests
from dotenv import load_dotenv

load_dotenv()

BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"


def get_headers():
    api_key = os.getenv("DASHSCOPE_API_KEY")
    if not api_key:
        raise RuntimeError("未检测到 DASHSCOPE_API_KEY，请检查 .env 或 Secrets 配置")
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json; charset=utf-8",
    }


def chat(model, system, user, temperature=0.3):
    payload = {
        "model": model,
        "temperature": temperature,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    response = requests.post(
        BASE_URL,
        headers=get_headers(),
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        timeout=60,
    )
    response.raise_for_status()
    return response.json()["choices"][0]["message"]["content"]


def chat_with_image(image_b64: str, mime_type: str, prompt: str, model="qwen-vl-plus", temperature=0.1):
    payload = {
        "model": model,
        "temperature": temperature,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{mime_type};base64,{image_b64}"
                        },
                    },
                    {
                        "type": "text",
                        "text": prompt,
                    },
                ],
            }
        ],
    }
    response = requests.post(
        BASE_URL,
        headers=get_headers(),
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        timeout=60,
    )
    response.raise_for_status()
    return response.json()["choices"][0]["message"]["content"]