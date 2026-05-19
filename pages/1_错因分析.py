import os
import io
import json
import base64
import streamlit as st
from PIL import Image, ImageDraw
from dotenv import load_dotenv
from llm_client import chat, chat_with_image
from db import save_record, count_same_error, upsert_alert
from ui import icon_title

# 登录守卫：未登录跳回主页
if not st.session_state.get("logged_in"):
    st.warning("请先登录")
    st.stop()


load_dotenv()

# 可供切换的模型列表（DashScope 兼容接口）
# OCR 视觉识别模型（负责从图片提取题目文字）
OCR_MODELS = {
    "qwen-vl-plus（通义视觉·默认）": "qwen-vl-plus",
    "qwen-vl-max（通义视觉·高精度）": "qwen-vl-max",
    "glm-4v-flash（智谱视觉·快速）": "glm-4v-flash",
    "glm-4v-plus（智谱视觉·高精度）": "glm-4v-plus",
}

# 分析模型（负责错因判断、错误分析）
AVAILABLE_MODELS = {
    "qwen-max（通义千问·默认）": "qwen-max",
    "qwen-plus（通义千问·快速便宜）": "qwen-plus",
    "qwen2.5-math-72b（通义数学专项）": "qwen2.5-math-72b-instruct",
    "deepseek-v4（DeepSeek·最新版）": "deepseek-chat",
    "deepseek-r1（DeepSeek·链式推理）": "deepseek-reasoner",
    "deepseek-v3（DeepSeek·上一代）": "deepseek-v3",
    "glm-4-plus（智谱·通用）": "glm-4-plus",
    "glm-4-flash（智谱·快速免费）": "glm-4-flash",
    "doubao-pro-32k（豆包·通用）": "doubao-pro-32k",
    "doubao-1.5-pro（豆包·最新）": "doubao-1.5-pro-32k",
}

DEFAULT_MODEL = os.getenv("DASHSCOPE_MODEL", "qwen-max")

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
    "full_page_problems": None,
    "full_page_results": None,
    "full_page_wrong_nums": [],
    "full_page_img_b64": "",
    "full_page_mime": "",
    "full_page_img_bytes": None,
    "annotated_img": None,
}.items():
    if _k not in st.session_state:
        st.session_state[_k] = _v

DRILL_THRESHOLD = 3

SYSTEM_PROMPT = """
你是通用学科错因分析系统，适用于小学、初中、高中各年级，覆盖数学、语文、英语、物理、化学、历史、政治等所有学科，支持选择题、填空题、判断题、解析题、应用题、阅读理解、写作、翻译等所有题型。你必须严格输出合法JSON，不要输出任何额外文本。

【必须按以下步骤执行，不得跳过】

第一步：独立作答（不看学生答案，先自己得出正确答案）
- 选择题：推导后确定正确选项
- 填空/计算/翻译题：完整作答得出标准答案
- 判断题：判断命题真假
- 阅读理解/简答题：给出要点
- 写作类：判断学生表达是否符合要求

第二步：比对
- 将学生答案与第一步的正确答案比较
- 完全正确 → "答案是否有误": false
- 有错误或明显不足 → "答案是否有误": true

第三步：输出JSON（不含推理过程）

错因标签体系（适用所有学科，答案正确时必须为[]）：
A1 抄写/转录错误 / A2 解题过程错误 / A3 基础知识薄弱
B1 关键概念识别错误 / B2 解题方法误判 / B3 知识迁移失败
C1 综合理解困难 / C2 畏难情绪放弃 / C3 抽象思维能力不足

输出格式：
{
  "答案是否有误": true或false,
  "题型判断": "学科+题型一句话",
  "错因标签": [],
  "判断理由": [],
  "建议干预策略": [],
  "温和反馈": "针对该学科该题型给学生的引导（120~200字）"
}

严格规定：答案正确时"答案是否有误"=false，后四个数组全部为[]，温和反馈给予鼓励。
""".strip()

FULL_PAGE_OCR_PROMPT = (
    "请仔细识别这张试卷图片中的所有题目（适用于数学、语文、英语、物理、化学等各学科）。\n"
    "对每道题提取：题号、题型、完整题目内容（选择题须包含所有选项）、学生的作答内容。\n"
    "数学/物理/化学公式用LaTeX格式并用$...$包裹，如$x^2$、$\\sqrt{2}$、$\\frac{1}{2}$。\n"
    "语文/英语题目保持原文，不要修改。\n"
    "严格输出JSON数组，包含所有题目，不得遗漏：\n"
    "[\n"
    "  {\"题号\": \"1\", \"题型\": \"选择题\", \"题目\": \"完整题目（含选项）...\", \"学生答案\": \"学生所选或所写的答案...\"},\n"
    "  {\"题号\": \"2\", \"题型\": \"阅读理解\", \"题目\": \"...\", \"学生答案\": \"...\"}\n"
    "]\n"
    "只输出JSON，不要输出其他任何文字。"
)


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
    start = s.find("[")
    end = s.rfind("]")
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


def get_wrong_positions(img_b64: str, mime_type: str, wrong_nums: list) -> list:
    """Ask the vision model to locate wrong answers on the image; returns list of bounding boxes."""
    nums_str = "、".join(f"第{n}题" for n in wrong_nums)
    prompt = (
        f"图片中{nums_str}的答案有误。"
        "请找出每道错题的答案书写区域在图片中的位置，用边界框标出。"
        "坐标系：图片左上角为(0,0)，右下角为(1000,1000)。"
        "严格输出JSON数组（只含错题）："
        "[{\"题号\": \"1\", \"x1\": 50, \"y1\": 100, \"x2\": 400, \"y2\": 200}, ...]"
        "只输出JSON，不要输出其他文字。"
    )
    response = chat_with_image(image_b64=img_b64, mime_type=mime_type, prompt=prompt)
    return safe_json_loads(response)


def annotate_image(img_bytes: bytes, boxes: list) -> bytes:
    """Draw red ellipses on the image at the given bounding box locations."""
    img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
    draw = ImageDraw.Draw(img)
    w, h = img.size
    for box in boxes:
        x1 = int(box.get("x1", 0) / 1000 * w)
        y1 = int(box.get("y1", 0) / 1000 * h)
        x2 = int(box.get("x2", 0) / 1000 * w)
        y2 = int(box.get("y2", 0) / 1000 * h)
        pad = 18
        draw.ellipse([x1 - pad, y1 - pad, x2 + pad, y2 + pad], outline="red", width=5)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# ── 页面 ─────────────────────────────────────────────
icon_title("assets/icons/错因分析.svg", "错因分析")
st.markdown("上传题目与学生解题步骤，AI识别错因并温和引导。")

# ── 科目 & 模型选择 ───────────────────────────────────
col_subj, col_ocr, col_model = st.columns([1, 1.5, 1.5])
with col_subj:
    SUBJECT = st.selectbox(
        "📚 学科",
        ["数学", "语文", "英语", "物理", "化学", "历史", "政治", "生物", "地理", "其他"],
        key="subject_select"
    )
with col_ocr:
    ocr_label = st.selectbox(
        "📷 OCR识题模型",
        list(OCR_MODELS.keys()),
        key="ocr_model_select",
        help="负责从图片中提取题目文字，专业视觉模型更准确"
    )
with col_model:
    model_label = st.selectbox(
        "🧠 错因分析模型",
        list(AVAILABLE_MODELS.keys()),
        key="model_select",
        help="负责判断对错、分析错因，推理能力强的模型更准"
    )

OCR_MODEL = OCR_MODELS[ocr_label]
MODEL = AVAILABLE_MODELS[model_label]

# 显示当前流水线
st.caption(f"当前方案：**{ocr_label.split('（')[0]}** 识题 → **{model_label.split('（')[0]}** 分析")
st.divider()

# ── 图片上传区域 ──────────────────────────────────────
st.markdown("**📷 拍照识题（可选）**")

page_mode = st.radio(
    "识题模式",
    ["单题模式", "整页识别（多道题）"],
    horizontal=True,
    key="page_mode",
    help="单题模式：识别单道题目；整页识别：识别整张试卷所有题目并标注错题"
)

uploaded_img = st.file_uploader(
    "上传题目图片，AI自动识别转文字" if page_mode == "单题模式" else "上传整页试卷照片，AI识别所有题目",
    type=["jpg", "jpeg", "png"]
)

if uploaded_img is not None:
    img_bytes_raw = uploaded_img.read()
    img_b64 = base64.b64encode(img_bytes_raw).decode("utf-8")
    suffix = uploaded_img.name.split(".")[-1].lower()
    mime = "image/jpeg" if suffix in ("jpg", "jpeg") else "image/png"

    if page_mode == "单题模式":
        st.image(img_bytes_raw, width=300)
        if st.button("识别图片内容", key="btn_ocr"):
            with st.spinner("正在识别图片..."):
                try:
                    ocr_text = chat_with_image(
                        image_b64=img_b64,
                        mime_type=mime,
                        model=OCR_MODEL,
                        prompt='请识别图片内容，分两部分：1）题目（含选项）2）学生解题步骤或答案。数学符号用$...$包裹LaTeX格式，如$x^2$、$\\sqrt{2}$。严格按JSON输出：{"题目": "...", "步骤": "..."}'
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

    else:
        # ── 整页识别模式 ──────────────────────────────
        st.image(img_bytes_raw, use_container_width=True)
        if st.button("识别整页所有题目", key="btn_full_ocr", type="primary"):
            # reset previous results
            st.session_state.full_page_problems = None
            st.session_state.full_page_results = None
            st.session_state.full_page_wrong_nums = []
            st.session_state.annotated_img = None
            with st.spinner("正在识别整页题目，请稍候…"):
                try:
                    ocr_raw = chat_with_image(
                        image_b64=img_b64,
                        mime_type=mime,
                        model=OCR_MODEL,
                        prompt=FULL_PAGE_OCR_PROMPT
                    )
                    problems = safe_json_loads(ocr_raw)
                    if isinstance(problems, list) and problems:
                        st.session_state.full_page_problems = problems
                        st.session_state.full_page_img_b64 = img_b64
                        st.session_state.full_page_mime = mime
                        st.session_state.full_page_img_bytes = img_bytes_raw
                        st.success(f"识别完成，共发现 **{len(problems)}** 道题目")
                    else:
                        st.error("识别结果为空或格式异常，请重试或换一张清晰度更高的图片")
                except Exception as e:
                    st.error(f"识别失败：{e}")

        # ── 展示识别到的题目列表 ──────────────────────
        if st.session_state.get("full_page_problems"):
            problems = st.session_state.full_page_problems
            st.markdown(f"**已识别 {len(problems)} 道题目：**")
            for p in problems:
                with st.expander(f"第 {p.get('题号', '?')} 题"):
                    st.write(f"**题目：** {p.get('题目', '')}")
                    st.write(f"**学生答案：** {p.get('学生答案', '')}")

            if st.button("分析所有题目错因", key="btn_full_analyze", type="primary"):
                st.session_state.full_page_results = None
                st.session_state.full_page_wrong_nums = []
                st.session_state.annotated_img = None
                wrong_nums = []
                all_results = []
                progress = st.progress(0, text="分析中…")
                for i, prob in enumerate(problems):
                    user_prompt = f"学科：{SUBJECT}\n\n题目：\n{prob.get('题目', '')}\n\n学生作答：\n{prob.get('学生答案', '')}"
                    try:
                        result_raw = chat(model=MODEL, system=SYSTEM_PROMPT, user=user_prompt, temperature=0.2)
                        try:
                            data = safe_json_loads(result_raw)
                        except Exception:
                            result_raw = chat(model=MODEL, system=SYSTEM_PROMPT, user=user_prompt, temperature=0.0)
                            data = safe_json_loads(result_raw)
                        all_results.append({
                            "题号": prob.get("题号", str(i + 1)),
                            "题型": prob.get("题型", ""),
                            "题目": prob.get("题目", ""),
                            "学生答案": prob.get("学生答案", ""),
                            "data": data,
                        })
                        is_wrong = data.get("答案是否有误", False)
                        tags = data.get("错因标签", [])
                        if is_wrong and tags:
                            wrong_nums.append(str(prob.get("题号", str(i + 1))))
                            save_record(
                                st.session_state.get("student_id", "unknown"),
                                prob.get("题目", ""),
                                prob.get("学生答案", ""),
                                tags[0],
                                data.get("温和反馈", "")
                            )
                    except Exception as e:
                        all_results.append({
                            "题号": prob.get("题号", str(i + 1)),
                            "题目": prob.get("题目", ""),
                            "学生答案": prob.get("学生答案", ""),
                            "data": None,
                            "error": str(e),
                        })
                    progress.progress((i + 1) / len(problems), text=f"已分析 {i+1}/{len(problems)} 道")

                # 分析完成后自动标注错题
                if wrong_nums:
                    progress.progress(1.0, text="正在标注错题位置…")
                    try:
                        boxes = get_wrong_positions(
                            img_b64, mime, wrong_nums
                        )
                        if boxes:
                            st.session_state.annotated_img = annotate_image(img_bytes_raw, boxes)
                    except Exception:
                        pass  # 标注失败不阻断主流程

                st.session_state.full_page_results = all_results
                st.session_state.full_page_wrong_nums = wrong_nums
                st.rerun()

            # ── 展示分析结果 ──────────────────────────
            if st.session_state.get("full_page_results"):
                results = st.session_state.full_page_results
                wrong_nums = st.session_state.get("full_page_wrong_nums", [])

                ERROR_DESC = {
                    "A1": "数字抄写错误", "A2": "计算过程错误", "A3": "基础技能薄弱",
                    "B1": "关键概念识别错误", "B2": "运算类型误判", "B3": "变式迁移失败",
                    "C1": "综合结构理解困难", "C2": "畏难情绪放弃", "C3": "抽象关系建模能力不足",
                }

                # ── 标注图优先展示 ────────────────────
                if st.session_state.get("annotated_img"):
                    st.divider()
                    st.markdown("### 🔴 错题标注试卷")
                    st.image(st.session_state.annotated_img,
                             caption="红色圆圈 = 错题区域",
                             use_container_width=True)
                    st.download_button(
                        "⬇️ 下载标注图片",
                        data=st.session_state.annotated_img,
                        file_name="错题标注.png",
                        mime="image/png",
                        key="dl_annotated"
                    )

                # ── 汇总提示 ─────────────────────────
                st.divider()
                if wrong_nums:
                    st.warning(f"共发现 **{len(wrong_nums)}** 道错题：第 {', '.join(wrong_nums)} 题")
                else:
                    st.success("未发现明显错误，做得不错！")

                # ── 逐题展示（错题展开，正确题折叠）────
                st.markdown("### 📋 逐题分析报告")
                for r in results:
                    d = r.get("data")
                    num = r.get("题号", "?")
                    orig_q = r.get("题目", "")
                    orig_a = r.get("学生答案", "")
                    ques_type = r.get("题型", "")

                    if d:
                        tags = d.get("错因标签", [])
                        is_wrong = d.get("答案是否有误", False)

                        if is_wrong:
                            st.markdown(
                                f"<div style='background:#FFF1F0;border-left:4px solid #FF4D4F;"
                                f"border-radius:8px;padding:1rem 1.2rem;margin-bottom:1rem;'>"
                                f"<b style='font-size:1.05rem;color:#CF1322;'>❌ 第 {num} 题"
                                f"{' · ' + ques_type if ques_type else ''}（有误）</b>"
                                f"</div>",
                                unsafe_allow_html=True
                            )
                            with st.container(border=True):
                                st.markdown("**📝 原题**")
                                st.markdown(orig_q)
                                st.markdown("**✏️ 学生答案**")
                                st.markdown(orig_a)
                                st.divider()
                                st.markdown(f"**📌 题型判断**：{d.get('题型判断', '-')}")

                                st.markdown("**🏷️ 错因标签**")
                                for tag in tags:
                                    st.error(f"**{tag}** — {ERROR_DESC.get(tag, '')}")

                                st.markdown("**🔍 判断理由**")
                                for reason in d.get("判断理由", []):
                                    st.markdown(f"• {reason}")

                                st.markdown("**💡 建议干预策略**")
                                for s in d.get("建议干预策略", []):
                                    st.markdown(f"• {s}")

                                st.markdown("**💬 温和反馈**")
                                st.info(d.get("温和反馈", ""))
                        else:
                            with st.expander(f"✅ 第 {num} 题{' · ' + ques_type if ques_type else ''}（正确）"):
                                st.markdown(orig_q)
                                st.caption(d.get("题型判断", ""))
                                if d.get("温和反馈"):
                                    st.info(d.get("温和反馈", ""))
                    elif r.get("error"):
                        st.error(f"第 {num} 题分析失败：{r['error']}")

        # 整页模式下不显示单题输入框
        st.stop()

# ── 单题模式：手动输入区域 ────────────────────────────
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
        user_prompt = f"学科：{SUBJECT}\n\n题目：\n{question.strip()}\n\n学生作答：\n{student_answer.strip()}"
        with st.spinner("AI正在分析中..."):
            try:
                result_raw = chat(model=MODEL, system=SYSTEM_PROMPT, user=user_prompt, temperature=0.2)
                try:
                    data = safe_json_loads(result_raw)
                except Exception:
                    result_raw = chat(model=MODEL, system=SYSTEM_PROMPT, user=user_prompt, temperature=0.0)
                    data = safe_json_loads(result_raw)
                tags = data.get("错因标签", [])
                is_wrong = data.get("答案是否有误", False)
                main_error = (tags[0] if isinstance(tags, list) and tags else "UNKNOWN") if is_wrong else "UNKNOWN"
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
                st.exception(e)

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
        # derive grade hint from student's class name
        class_name = st.session_state.get("user", {}).get("class_name", "")
        if any(k in class_name for k in ("高一", "高二", "高三")):
            grade_hint = "高中"
        elif any(k in class_name for k in ("七年级", "八年级", "九年级")):
            grade_hint = "初中"
        else:
            grade_hint = "小学中高年级"
        drill_system = f"你是{SUBJECT}专项训练题生成器。严格输出JSON：{{\"训练题\":[{{\"题目\":\"\",\"提示\":\"\",\"提醒\":\"\"}}]}}"
        drill_user = f"学科：{SUBJECT}。错因标签：{st.session_state.main_error}。生成5道由浅入深的{grade_hint}{SUBJECT}练习题。"
        try:
            with st.spinner("正在生成专项训练题..."):
                drill_raw = chat(model=MODEL, system=drill_system, user=drill_user, temperature=0.4)
            drill_data = safe_json_loads(drill_raw)
            st.session_state.drill_items = normalize_drill_items(drill_data)
        except Exception as e:
            st.session_state.drill_error = str(e)
            st.exception(e)

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
