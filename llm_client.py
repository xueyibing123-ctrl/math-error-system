import os
from dotenv import load_dotenv
import dashscope
from dashscope import Generation, MultiModalConversation

load_dotenv()


def _get_api_key():
    api_key = os.getenv("DASHSCOPE_API_KEY")
    if not api_key:
        raise RuntimeError("未检测到 DASHSCOPE_API_KEY，请检查 .env 或 Secrets 配置")
    return api_key


def chat(model, system, user, temperature=0.3):
    dashscope.api_key = _get_api_key()
    response = Generation.call(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=temperature,
        result_format="message",
    )
    if response.status_code != 200:
        raise RuntimeError(f"API错误：{response.code} - {response.message}")
    return response.output.choices[0].message.content


def chat_with_image(image_b64: str, mime_type: str, prompt: str, model="qwen-vl-plus", temperature=0.1):
    dashscope.api_key = _get_api_key()
    response = MultiModalConversation.call(
        model=model,
        messages=[{
            "role": "user",
            "content": [
                {"image": f"data:{mime_type};base64,{image_b64}"},
                {"text": prompt},
            ],
        }],
    )
    if response.status_code != 200:
        raise RuntimeError(f"API错误：{response.code} - {response.message}")
    return response.output.choices[0].message.content[0]["text"]