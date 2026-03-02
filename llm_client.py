import os
import traceback
import httpx
from dotenv import load_dotenv

load_dotenv()

BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"


def _get_headers():
    api_key = os.getenv("DASHSCOPE_API_KEY")
    if not api_key:
        raise RuntimeError("未检测到 DASHSCOPE_API_KEY")
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }


def chat(model, system, user, temperature=0.3):
    try:
        with httpx.Client(timeout=60) as client:
            resp = client.post(
                BASE_URL,
                headers=_get_headers(),
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
        # 输出完整堆栈，找到真正出错位置
        raise RuntimeError(f"详细错误：\n{traceback.format_exc()}") from e


def chat_with_image(image_b64: str, mime_type: str, prompt: str, model="qwen-vl-plus", temperature=0.1):
    try:
        with httpx.Client(timeout=60) as client:
            resp = client.post(
                BASE_URL,
                headers=_get_headers(),
                json={
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
                },
            )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]
    except Exception as e:
        raise RuntimeError(f"详细错误：\n{traceback.format_exc()}") from e
