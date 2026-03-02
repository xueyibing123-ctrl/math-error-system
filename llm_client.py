import os
from dotenv import load_dotenv
import dashscope
from dashscope import Generation, MultiModalConversation

load_dotenv()


def _get_api_key():
    api_key = os.getenv("DASHSCOPE_API_KEY")
    if not api_key:
        raise RuntimeError("未检测到 DASHSCOPE_API_KEY，请检查 .env 或 Secrets 配置")
<<<<<<< HEAD
    return api_key
=======

    body = json.dumps(payload, ensure_ascii=True).encode("utf-8")
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
>>>>>>> efc2d0c018a9a7f78686b2d7e468df815ae353c5


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
<<<<<<< HEAD
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
=======
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
>>>>>>> efc2d0c018a9a7f78686b2d7e468df815ae353c5
