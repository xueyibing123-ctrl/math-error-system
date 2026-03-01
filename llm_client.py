import os
import json
import urllib.request
from dotenv import load_dotenv

load_dotenv()

BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"


def _post(payload: dict) -> dict:
    api_key = os.getenv("DASHSCOPE_API_KEY")
    if not api_key:
        raise RuntimeError("未检测到 DASHSCOPE_API_KEY，请检查 .env 或 Secrets 配置")

    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        BASE_URL,
        data=body,
        method="POST",
    )
    req.add_header("Authorization", f"Bearer {api_key}")
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept-Charset", "utf-8")

    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def chat(model, system, user, temperature=0.3):
    payload = {
        "model": model,
        "temperature": temperature,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    result = _post(payload)
    return result["choices"][0]["message"]["content"]


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
    result = _post(payload)
    return result["choices"][0]["message"]["content"]