import os
import json
import base64
import streamlit as st
from dotenv import load_dotenv
from llm_client import chat, chat_with_image
from db import save_record, count_same_error, upsert_alert
from ui import icon_title

# 登录守卫：未登录跳回主页
if not st.session_state.get("logged_in"):
    st.warning("请先登录")
    st.stop()


load_dotenv()
MODEL = os.getenv("DASHSCOPE_MODEL", "qwen-plus")

# ── session_state 初始化 ──────────────────────────────
for _k, _v in {
    "analysis_result": None,
    "main_error": "UNKNOWN",
    "error_count": 0,
    "drill_requested": False,
    "drill_items": None,
    "drill_error": "",
    "drill_raw_debug": "",
    "drill_mastery": {},
    "ocr_text": "",
    "ocr_steps": "",
}.items():
    if _k not in st.session_state:
        st.session_state[_k] = _v

DRILL_THRESHOLD = 3

SYSTEM_PROMPT = """
你是小学数学错因分析系统（识别层）。你必须严格输出合法JSON，不要输出任何额外文本。

错因标签体系（只允许从中选择）：
A1 数字抄写错误 / A2 计算过程错误 / A3 基础技能薄弱
B1 单位一/关键概念识别错误 / B2 运算类型误判 / B3 变式迁移失败
C1 综合结构理解困难 / C2 畏难情绪放弃 / C3 抽象关系建模能力不足

请输出：
{
  "题型判断": "一句话",
  "错因标签": ["标签1","标签2（可选）"],
  "判断理由": ["理由1","理由2（可选）"],
  "建议干预策略": ["策略1","策略2"],
  "温和反馈": "给学生的引导文字（120~200字）"
}
""".strip()


def safe_json_loads(s: str):
    if not s:
        raise ValueError("empty response")
    s = s.strip()
    try:
        return json.loads(s)
    except Exception:
        pass
    if "```" in s:
        import re
        match = re.search(r"```(?:json)?\s*([\s\S]*?)```", s)
        if match:
            try:
                return json.loads(match.group(1).strip())
            except Exception:
                pass
    start = s.find("{")
    end = s.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(s[start:end+1])
        except Exception:
            pass
    raise ValueError("JSON parse failed")


def normalize_drill_items(drill_data):
    if isinstance(drill_data, dict):
        items = drill_data.get("训练题") or drill_data.get("items") or drill_data.get("questions")
    elif isinstance(drill_data, list):
        items = drill_data
    else:
        items = None
    if not items:
        raise ValueError("训练题结构无法识别")
    norm = []
    for x in items[:5]:
        if not isinstance(x, dict):
            x = {"question": str(x)}
        norm.append({
            "question": x.get("题目") or x.get("question") or "",
            "hint": x.get("提示") or x.get("hint") or "",
            "reminder": x.get("提醒") or x.get("reminder") or "",
        })
    while len(norm) < 5:
        norm.append({"question": "(暂无)", "hint": "", "reminder": ""})
    return norm


# ── 页面 ─────────────────────────────────────────────
icon_title("assets/icons/错因分析.svg", "错因分析")
st.markdown("上传题目与学生解题步骤，AI识别错因并温和引导。")

# 图片上传
st.markdown("**📷 拍照识题（可选）**")
uploaded_img = st.file_uploader("上传题目图片，AI自动识别转文字", type=["jpg","jpeg","png"])
if uploaded_img is not None:
    st.image(uploaded_img, width=300)
    if st.button("识别图片内容", key="btn_ocr"):
        img_bytes = uploaded_img.read()
        img_b64 = base64.b64encode(img_bytes).decode("utf-8")
        suffix = uploaded_img.name.split(".")[-1].lower()
        mime = "image/jpeg" if suffix in ("jpg", "jpeg") else "image/png"
        with st.spinner("正在识别图片..."):
            try:
                ocr_text = chat_with_image(
                    model="qwen-vl-plus",
                    image_b64=img_b64,
                    mime_type=mime,
                    prompt='请识别图片内容，分两部分：1）题目 2）学生解题步骤。严格按JSON输出：{"题目": "...", "步骤": "..."}'
                )
                try:
                    ocr_json = safe_json_loads(ocr_text)
                    st.session_state.ocr_text = ocr_json.get("题目", "")
                    st.session_state.ocr_steps = ocr_json.get("步骤", "")
                except Exception:
                    st.session_state.ocr_text = ocr_text
                    st.session_state.ocr_steps = ""
                st.success("识别完成，已自动填入")
            except Exception as e:
                st.error(f"识别失败：{e}")

question = st.text_area("请输入原题：",
                        value=st.session_state.get("ocr_text", ""),
                        height=100)
student_answer = st.text_area("请输入学生解题步骤：",
                               value=st.session_state.get("ocr_steps", ""),
                               height=100)

if st.button("开始分析", type="primary"):
    if not question.strip() or not student_answer.strip():
        st.warning("请填写完整信息")
    else:
        user_prompt = f"题目：\n{question.strip()}\n\n学生解题步骤：\n{student_answer.strip()}"
        with st.spinner("AI正在分析中..."):
            try:
                result_raw = chat(model=MODEL, system=SYSTEM_PROMPT, user=user_prompt, temperature=0.2)
                try:
                    data = safe_json_loads(result_raw)
                except Exception:
                    result_raw = chat(model=MODEL, system=SYSTEM_PROMPT, user=user_prompt, temperature=0.0)
                    data = safe_json_loads(result_raw)
                tags = data.get("错因标签", [])
                main_error = tags[0] if isinstance(tags, list) and tags else "UNKNOWN"
                save_record(
                    st.session_state.get("student_id", "unknown"),
                    question.strip(), student_answer.strip(),
                    main_error, data.get("温和反馈", "")
                )
                error_count = count_same_error(main_error)
                if main_error != "UNKNOWN" and error_count >= DRILL_THRESHOLD:
                    upsert_alert(
                        student_id=st.session_state.get("student_id"),
                        error_code=main_error,
                        error_count=error_count,
                        threshold=DRILL_THRESHOLD
                    )
                st.session_state.analysis_result = data
                st.session_state.main_error = main_error
                st.session_state.error_count = error_count
                st.session_state.drill_items = None
                st.session_state.drill_mastery = {}
            except Exception as e:
                st.error(f"发生错误：{e}")

# 展示分析结果
if st.session_state.analysis_result:
    data = st.session_state.analysis_result
    st.success("分析完成")

    ERROR_DESC = {
        "A1": "数字抄写错误",
        "A2": "计算过程错误",
        "A3": "基础技能薄弱",
        "B1": "关键概念识别错误",
        "B2": "运算类型误判",
        "B3": "变式迁移失败",
        "C1": "综合结构理解困难",
        "C2": "畏难情绪放弃",
        "C3": "抽象关系建模能力不足",
    }

    with st.container(border=True):
        st.markdown(f"**📌 题型判断**：{data.get('题型判断', '-')}")

        st.markdown("**🏷️ 错因标签**")
        for tag in data.get("错因标签", []):
            desc = ERROR_DESC.get(tag, "")
            st.error(f"**{tag}** — {desc}")

        st.markdown("**🔍 判断理由**")
        for reason in data.get("判断理由", []):
            st.write(f"• {reason}")

        st.markdown("**💡 建议干预策略**")
        for strategy in data.get("建议干预策略", []):
            st.write(f"• {strategy}")

    st.markdown("### 💬 温和反馈")
    st.info(data.get("温和反馈", ""))
    if st.session_state.main_error != "UNKNOWN" and st.session_state.error_count >= DRILL_THRESHOLD:
        st.warning(f"⚠ 错因 **{st.session_state.main_error}** 累计 **{st.session_state.error_count}** 次，建议专项训练。")

# 专项训练区域
if st.session_state.main_error != "UNKNOWN" and st.session_state.error_count >= DRILL_THRESHOLD:
    st.divider()
    st.subheader("🎯 专项训练（自动触发）")
    col1, col2 = st.columns([1, 1])
    with col1:
        clicked = st.button("生成专项训练题（5道）", key="btn_drill")
    with col2:
        if st.button("清空训练题", key="btn_drill_clear"):
            st.session_state.drill_items = None
            st.session_state.drill_mastery = {}
            st.rerun()

    if clicked:
        st.session_state.drill_items = None
        st.session_state.drill_mastery = {}
        st.session_state.drill_requested = True

    if st.session_state.get("drill_requested"):
        st.session_state.drill_requested = False
        drill_system = "你是小学数学专项训练题生成器。严格输出JSON：{\"训练题\":[{\"题目\":\"\",\"提示\":\"\",\"提醒\":\"\"}]}"
        drill_user = f"错因标签：{st.session_state.main_error}。生成5道由浅入深的小学3-6年级应用题。"
        try:
            with st.spinner("正在生成专项训练题..."):
                drill_raw = chat(model=MODEL, system=drill_system, user=drill_user, temperature=0.4)
            drill_data = safe_json_loads(drill_raw)
            st.session_state.drill_items = normalize_drill_items(drill_data)
        except Exception as e:
            st.session_state.drill_error = str(e)
            st.error(f"生成失败：{e}")

    if st.session_state.drill_items:
        st.markdown("### 📚 训练题列表（打卡）")
        for i, q in enumerate(st.session_state.drill_items, start=1):
            status = st.session_state.drill_mastery.get(i, "未标记")
            with st.container(border=True):
                st.markdown(f"**第 {i} 题** · 状态：`{status}`")
                st.write(q.get("question", ""))
                if q.get("hint"):
                    st.caption(f"提示：{q['hint']}")
                if q.get("reminder"):
                    st.caption(f"提醒：{q['reminder']}")
                b1, b2, _ = st.columns([1, 1, 2])
                with b1:
                    if st.button("✅ 我已掌握", key=f"mastered_{i}"):
                        st.session_state.drill_mastery[i] = "已掌握"
                with b2:
                    if st.button("🧠 我还不会", key=f"notyet_{i}"):
                        st.session_state.drill_mastery[i] = "还不会"