import os
import io
import re as _re
import json
import random as _random
import base64
import streamlit as st
from PIL import Image, ImageDraw
from dotenv import load_dotenv
from llm_client import chat, chat_with_image
from db import save_record, count_same_error, upsert_alert, search_question_bank, init_question_bank
try:
    init_question_bank()
except Exception:
    pass
from ui import icon_title

# 登录守卫：未登录跳回主页
if not st.session_state.get("logged_in"):
    st.warning("请先登录")
    st.stop()


load_dotenv()

# 视觉模型（支持图片输入，可用于OCR识题 或 单模式识题+分析）
VISION_MODELS = {
    "qwen-vl-plus（通义视觉·快速）": "qwen-vl-plus",
    "qwen-vl-max（通义视觉·高精度）": "qwen-vl-max",
    "Doubao-1.5-Vision-Pro（豆包视觉·旗舰）": "doubao-1-5-vision-pro-32k-250115",
    "Doubao-1.5-Vision-Lite（豆包视觉·轻量）": "doubao-1-5-vision-lite-32k-250115",
    "GLM-4.6V（智谱视觉·通用）": "glm-4.6v",
    "GLM-4.1V-Thinking（智谱视觉·深度思考）": "glm-4.1v-thinking",
}

# 分析模型（支持文本输入，用于错因判断）
ANALYSIS_MODELS = {
    "qwen-max（通义千问·推荐）": "qwen-max",
    "DeepSeek-V4-Pro（最强·深度思考）": "deepseek-v4-pro",
    "DeepSeek-V4-Flash（快速·低价）": "deepseek-v4-flash",
    "DeepSeek-R1（链式推理）": "deepseek-reasoner",
    "Doubao-Seed-2.0-Pro（豆包·最新旗舰）": "doubao-seed-2.0-pro",
    "Doubao-1.5-Pro-256k（豆包·长文本）": "doubao-1-5-pro-256k-250115",
    "GLM-4-Flash（智谱·快速免费）": "glm-4-flash",
}

# 手动输入单题时可选的分析模型（同上）
TEXT_MODELS = ANALYSIS_MODELS

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
    "full_page_results": None,
    "full_page_wrong_nums": [],
    "full_page_img_bytes": None,
    "annotated_img": None,
}.items():
    if _k not in st.session_state:
        st.session_state[_k] = _v

DRILL_THRESHOLD = 3

TEXT_SYSTEM_PROMPT = """
你是一位经验丰富的作业批改助手，适用于小学、初中、高中各年级，覆盖数学、语文、英语、物理、化学、历史、政治等所有学科。你必须严格输出合法JSON，不要输出任何额外文本。

【批改宽松原则（重要，优先执行）】
- 时间单位缩写等价：分=分钟、秒=秒钟、时=小时，不扣分
- 数字与汉字等价：1=一、0.5=二分之一，不扣分
- 语文/英语阅读理解、简答、主观题：重点看意思和要点是否准确，不强求用词与参考答案完全相同，意思对即算对
- 计算步骤有小笔误但结果正确，酌情判对
- 有参考答案时以参考答案为准，但允许合理的同义表述

【必须按以下步骤执行】
第一步：独立作答（不看学生答案，先自己得出正确答案）
第二步：宽松比对（完全正确或意思相符→"答案是否有误":false，有明显错误→true）
第三步：输出JSON

错因标签（仅答错时填写）：A1抄写错误/A2过程错误/A3基础薄弱/B1概念错误/B2方法误判/B3迁移失败/C1综合困难/C2畏难放弃/C3抽象不足

输出格式：
{"答案是否有误":true或false,"题型判断":"...","错因标签":[],"判断理由":[],"建议干预策略":[],"温和反馈":"见下方要求"}

【温和反馈要求】
- 答案有误：用苏格拉底问答法，先肯定学生思考，再用1~2个启发性问题引导学生自己发现错误，不直接给答案，语气亲切，120~200字
- 答案正确：真诚鼓励，夸具体思维亮点，50字以内

答案正确时：错因标签、判断理由、建议干预策略全部输出[]。
""".strip()

# 整页识别+分析一次完成的联合Prompt
COMBINED_PROMPT = """你是一位经验丰富的作业批改助手，请完成两步工作：

第一步：识别图片中的所有题目（数学/语文/英语/物理/化学等均适用）
第二步：对每道题独立判断学生答案是否正确，给出批改结果

【批改宽松原则（重要）】
- 时间单位缩写等价：分=分钟、秒=秒钟、时=小时，不扣分
- 数字与汉字等价：1=一、0.5=二分之一，不扣分
- 语文/英语主观题、阅读理解：重点看意思和要点是否准确，不强求用词完全一致
- 计算步骤有小笔误但结果正确，酌情判对

其他要求：
- 先自己推导出正确答案，再宽松比对学生答案
- 数学/物理公式用$...$包裹LaTeX，如$x^2$、$\\frac{1}{2}$
- 语文/英语保持原文

【温和反馈写作要求】
- 答案有误时：用苏格拉底问答法，先肯定学生的思考过程，再用1~2个启发性问题引导学生自己发现错误，不直接给出答案，语气亲切温暖，120~200字
- 答案正确时：真诚鼓励，夸具体的思维亮点，50字以内

严格输出JSON数组（只含JSON，不要其他文字）：
[
  {
    "题号": "1",
    "题型": "选择题",
    "题目": "完整题目内容含选项",
    "学生答案": "学生所写答案",
    "答案是否有误": true,
    "题型判断": "学科+题型描述",
    "错因标签": ["A2"],
    "判断理由": ["理由说明"],
    "建议干预策略": ["策略"],
    "温和反馈": "苏格拉底式引导，120~200字，不直接给答案"
  }
]

错因标签（仅答错时填写）：A1抄写/A2过程错/A3基础薄弱/B1概念错/B2方法误判/B3迁移失败/C1综合困难/C2畏难/C3抽象不足
答案正确时：答案是否有误=false，错因标签/判断理由/建议干预策略均为[]"""


_PRAISE = [
    "答对了！思路很清晰，继续保持！",
    "完全正确，做得很好！",
    "对！这道题掌握得很扎实！",
    "答对了，很棒！计算准确！",
    "正确！很好地运用了所学知识！",
]

SHORT_ANALYSIS_SYSTEM = """你是作业批改助手。已知学生答案有误，正确答案已提供。只需分析错因并写温和反馈，不必重新判断对错。严格输出合法JSON，不输出其他文字。

错因标签（选1个）：A1抄写错误/A2过程错误/A3基础薄弱/B1概念错误/B2方法误判/B3迁移失败/C1综合困难/C2畏难放弃/C3抽象不足

输出格式：
{"答案是否有误":true,"题型判断":"...","错因标签":["X"],"判断理由":["一句话说明"],"建议干预策略":["一句话"],"温和反馈":"苏格拉底式引导100-150字，不直接给答案"}""".strip()


def _direct_compare(student_ans: str, correct_ans: str):
    """保守直接比对。True=对 / False=错 / None=交AI。"""
    s = (student_ans or "").strip()
    c = (correct_ans or "").strip()
    if not s or s in ("未作答", "（未填写步骤）"):
        return False

    def _norm(x):
        x = _re.sub(r'\s+', '', x)
        return x.replace('分钟', '分').replace('秒钟', '秒').replace('小时', '时').upper()

    sn, cn = _norm(s), _norm(c)
    if sn == cn:
        return True
    if _re.fullmatch(r'[ABCD]', sn):
        m = _re.search(r'[ABCD]', cn[:20])
        if m:
            return sn == m.group()
        return None
    _pos = {'√', '✓', '对', '正确', 'T', 'TRUE'}
    _neg = {'×', '✗', '错', '错误', 'F', 'FALSE'}
    if sn in _pos or sn in _neg:
        s_pos = sn in _pos
        c_pos = any(p in cn for p in _pos)
        c_neg = any(n in cn for n in _neg)
        if c_pos and not c_neg:
            return s_pos
        if c_neg and not c_pos:
            return not s_pos
    return None


def build_scoring_ref(bank: dict) -> str:
    """从题库命中结果构建注入到 prompt 的参考文本（含评分细则）。"""
    ref = (
        f"\n\n【📚 题库参考答案（相似度 {bank['similarity']:.0%}，来源：{bank['source'] or '题库'}）】\n"
        f"{bank['correct_answer']}\n"
        f"请以此为标准答案判断学生作答是否正确，不要自行推导答案。"
    )
    criteria = bank.get("scoring_criteria", [])
    if criteria:
        total_pts = bank.get("total_points", 0)
        ref += f"\n\n【按点给分】本题共 {total_pts} 分，请严格按以下评分细则逐点打分，并在JSON中额外输出 \"得分\"、\"满分\"、\"按点得分\" 字段：\n"
        for c in criteria:
            ref += f"- {c['criterion']}：{c['points']} 分\n"
        ref += ("\"按点得分\" 格式：[{\"要点\":\"...\",\"满分\":N,\"得分\":M,\"说明\":\"一句话说明\"},...]\n"
                "\"得分\" 为整数实际总得分，\"满分\" 为整数总分。")
    return ref


def build_user_prompt(subject: str, question: str, student_answer: str) -> tuple[str, bool, list]:
    """
    构造分析用的 user_prompt。
    如果题库命中，把正确答案（含评分细则）注入提示词。
    返回 (prompt, hit_from_bank, scoring_criteria)。
    """
    bank = search_question_bank(question, subject=subject)
    if bank:
        ref = build_scoring_ref(bank)
        prompt = f"学科：{subject}\n\n题目：\n{question}\n\n学生作答：\n{student_answer}{ref}"
        return prompt, True, bank.get("scoring_criteria", [])
    return f"学科：{subject}\n\n题目：\n{question}\n\n学生作答：\n{student_answer}", False, []


def render_scoring(data: dict):
    """如果分析结果含按点得分，渲染得分明细。"""
    breakdown = data.get("按点得分")
    if not breakdown:
        return
    total = data.get("满分", 0)
    got = data.get("得分", 0)
    st.markdown(f"**📊 按点得分：{got} / {total} 分**")
    for item in breakdown:
        icon = "✅" if item.get("得分", 0) >= item.get("满分", 1) else "❌"
        st.markdown(
            f"{icon} **{item.get('要点','')}**（满分 {item.get('满分',0)} 分）"
            f"　得 **{item.get('得分',0)}** 分　— {item.get('说明','')}"
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


def compress_image(img_bytes: bytes, max_side: int = 1600) -> tuple[bytes, str]:
    """压缩图片，减少上传体积，返回(压缩后bytes, mime_type)。"""
    img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
    w, h = img.size
    if max(w, h) > max_side:
        ratio = max_side / max(w, h)
        img = img.resize((int(w * ratio), int(h * ratio)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=82, optimize=True)
    return buf.getvalue(), "image/jpeg"


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

# ── 学科 & 模式选择 ───────────────────────────────────
SUBJECT = st.selectbox(
    "📚 学科",
    ["数学", "语文", "英语", "物理", "化学", "历史", "政治", "生物", "地理", "其他"],
    key="subject_select"
)

page_mode = st.radio(
    "📋 模式选择",
    ["📷 拍照整页·单模型（识题+分析一次完成，最快）",
     "📷 拍照整页·双模型（OCR模型+分析模型，可自由搭配）",
     "✏️ 手动输入单题"],
    key="page_mode_radio",
)

st.divider()

# ══════════════════════════════════════════════════════
# 整页拍照模式 · 单模型（单次API调用）
# ══════════════════════════════════════════════════════
if page_mode == "📷 拍照整页·单模型（识题+分析一次完成，最快）":

    vision_label = st.selectbox(
        "🤖 视觉分析模型（识题+分析一次完成）",
        list(VISION_MODELS.keys()),
        key="vision_model_select",
        help="视觉模型直接读图判断对错给出分析，一次API调用搞定"
    )
    VISION_MODEL = VISION_MODELS[vision_label]
    st.caption(f"⚡ 单次调用 · **{vision_label.split('（')[0]}** · 学科：**{SUBJECT}**")

    uploaded_img = st.file_uploader(
        "上传整页试卷照片",
        type=["jpg", "jpeg", "png"],
        key="full_page_img"
    )

    if uploaded_img is not None:
        img_bytes_raw = uploaded_img.read()
        st.image(img_bytes_raw, use_container_width=True)

        if st.button("🚀 一键识别并分析所有题目", key="btn_combined", type="primary"):
            st.session_state.full_page_results = None
            st.session_state.full_page_wrong_nums = []
            st.session_state.annotated_img = None
            st.session_state.full_page_img_bytes = img_bytes_raw

            with st.spinner("正在识别并分析中，请稍候（单次调用，比之前快很多）…"):
                try:
                    # 压缩图片减少上传时间
                    compressed, mime = compress_image(img_bytes_raw)
                    img_b64 = base64.b64encode(compressed).decode("utf-8")

                    combined_prompt = f"学科：{SUBJECT}\n\n" + COMBINED_PROMPT
                    raw = chat_with_image(
                        image_b64=img_b64,
                        mime_type=mime,
                        prompt=combined_prompt,
                        model=VISION_MODEL,
                        temperature=0.1,
                    )
                    results = safe_json_loads(raw)
                    if not isinstance(results, list) or not results:
                        st.error("返回结果为空或格式异常，请重试或换一张清晰图片")
                        st.stop()

                    wrong_nums = []
                    for r in results:
                        is_wrong = r.get("答案是否有误", False)
                        tags = r.get("错因标签", [])
                        if is_wrong and tags:
                            wrong_nums.append(str(r.get("题号", "")))
                            save_record(
                                st.session_state.get("student_id", "unknown"),
                                r.get("题目", ""),
                                r.get("学生答案", ""),
                                tags[0],
                                r.get("温和反馈", "")
                            )

                    st.session_state.full_page_results = results
                    st.session_state.full_page_wrong_nums = wrong_nums
                    st.rerun()

                except Exception as e:
                    st.error(f"识别失败：{e}")

    # ── 展示分析结果 ──────────────────────────────────
    if st.session_state.get("full_page_results"):
        results = st.session_state.full_page_results
        wrong_nums = st.session_state.get("full_page_wrong_nums", [])

        ERROR_DESC = {
            "A1": "抄写/转录错误", "A2": "解题过程错误", "A3": "基础知识薄弱",
            "B1": "关键概念识别错误", "B2": "解题方法误判", "B3": "知识迁移失败",
            "C1": "综合理解困难", "C2": "畏难情绪放弃", "C3": "抽象思维不足",
        }

        st.divider()
        if wrong_nums:
            st.warning(f"⚠️ 共发现 **{len(wrong_nums)}** 道错题：第 **{', '.join(wrong_nums)}** 题")
        else:
            st.success("未发现明显错误，做得不错！")

        st.markdown("### 📋 逐题分析报告")
        for r in results:
            num = str(r.get("题号", "?"))
            orig_q = r.get("题目", "")
            orig_a = r.get("学生答案", "")
            ques_type = r.get("题型", "")
            is_wrong = r.get("答案是否有误", False)
            tags = r.get("错因标签", [])

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
                    render_scoring(r)
                    st.markdown(f"**📌 题型判断**：{r.get('题型判断', '-')}")
                    st.markdown("**🏷️ 错因标签**")
                    for tag in tags:
                        st.error(f"**{tag}** — {ERROR_DESC.get(tag, '')}")
                    st.markdown("**🔍 判断理由**")
                    for reason in r.get("判断理由", []):
                        st.markdown(f"• {reason}")
                    st.markdown("**💡 建议干预策略**")
                    for s in r.get("建议干预策略", []):
                        st.markdown(f"• {s}")
                    st.markdown("**💬 温和反馈**")
                    st.info(r.get("温和反馈", ""))
            else:
                with st.expander(f"✅ 第 {num} 题{' · ' + ques_type if ques_type else ''}（正确）"):
                    st.markdown(orig_q)
                    st.caption(r.get("题型判断", ""))
                    if r.get("温和反馈"):
                        st.info(r.get("温和反馈", ""))

    st.stop()

# ══════════════════════════════════════════════════════
# 整页拍照模式 · 双模型（OCR + 分析分开选）
# ══════════════════════════════════════════════════════
elif page_mode == "📷 拍照整页·双模型（OCR模型+分析模型，可自由搭配）":

    col_ocr, col_ana = st.columns(2)
    with col_ocr:
        ocr_label = st.selectbox(
            "📷 OCR识题模型",
            list(VISION_MODELS.keys()),
            key="dual_ocr_select",
            help="负责从图片提取题目文字"
        )
    with col_ana:
        ana_label = st.selectbox(
            "🧠 批改分析模型",
            list(ANALYSIS_MODELS.keys()),
            key="dual_ana_select",
            help="负责判断对错、分析错因，推理能力强的模型更准"
        )
    OCR_MODEL = VISION_MODELS[ocr_label]
    ANA_MODEL = ANALYSIS_MODELS[ana_label]
    st.caption(f"🔗 双模型方案：**{ocr_label.split('（')[0]}** 识题 → **{ana_label.split('（')[0]}** 分析 · 学科：**{SUBJECT}**")

    uploaded_img2 = st.file_uploader(
        "上传整页试卷照片",
        type=["jpg", "jpeg", "png"],
        key="dual_full_page_img"
    )

    if uploaded_img2 is not None:
        img_bytes_raw2 = uploaded_img2.read()
        st.image(img_bytes_raw2, use_container_width=True)

        if st.button("🔍 识别整页题目", key="btn_dual_ocr", type="primary"):
            st.session_state.full_page_results = None
            st.session_state.full_page_wrong_nums = []
            st.session_state.annotated_img = None
            st.session_state["dual_problems"] = None

            with st.spinner("正在识别题目…"):
                try:
                    compressed2, mime2 = compress_image(img_bytes_raw2)
                    img_b64_2 = base64.b64encode(compressed2).decode("utf-8")
                    OCR_PROMPT = (
                        f"学科：{SUBJECT}\n"
                        "请识别图片中所有题目。对每道题提取：题号、题型、完整题目内容（含选项）、学生作答内容。\n"
                        "数学/物理公式用$...$包裹LaTeX，语文/英语保持原文。\n"
                        "严格输出JSON数组：\n"
                        "[{\"题号\":\"1\",\"题型\":\"选择题\",\"题目\":\"...\",\"学生答案\":\"...\"}]\n"
                        "只输出JSON，不要其他文字。"
                    )
                    ocr_raw = chat_with_image(image_b64=img_b64_2, mime_type=mime2,
                                              model=OCR_MODEL, prompt=OCR_PROMPT)
                    problems = safe_json_loads(ocr_raw)
                    if isinstance(problems, list) and problems:
                        st.session_state["dual_problems"] = problems
                        st.session_state["dual_img_bytes"] = img_bytes_raw2
                        st.success(f"识别完成，共发现 **{len(problems)}** 道题目")
                    else:
                        st.error("识别结果为空，请重试或换清晰图片")
                except Exception as e:
                    st.error(f"识别失败：{e}")

    if st.session_state.get("dual_problems"):
        problems = st.session_state["dual_problems"]
        st.markdown(f"**已识别 {len(problems)} 道题目：**")
        for p in problems:
            with st.expander(f"第 {p.get('题号','?')} 题 · {p.get('题型','')}"):
                st.write(f"**题目：** {p.get('题目','')}")
                st.write(f"**学生答案：** {p.get('学生答案','')}")

        if st.button("📊 分析所有题目错因", key="btn_dual_analyze", type="primary"):
            from concurrent.futures import ThreadPoolExecutor, as_completed

            def _analyze(prob, idx):
                question = prob.get("题目", "")
                student_ans = prob.get("学生答案", "")
                bank = search_question_bank(question, subject=SUBJECT)
                direct = _direct_compare(student_ans, bank["correct_answer"]) if bank else None

                if direct is True:
                    data = {"答案是否有误": False, "题型判断": "客观题（题库直判）",
                            "错因标签": [], "判断理由": [], "建议干预策略": [],
                            "温和反馈": _random.choice(_PRAISE)}
                elif direct is False:
                    user_prompt = (f"学科：{SUBJECT}\n题目：{question}\n"
                                   f"正确答案：{bank['correct_answer']}\n学生答案：{student_ans}")
                    if bank.get("scoring_criteria"):
                        user_prompt += "\n" + build_scoring_ref(bank)
                    result_raw = chat(model=ANA_MODEL, system=SHORT_ANALYSIS_SYSTEM,
                                      user=user_prompt, temperature=0.2)
                    data = safe_json_loads(result_raw)
                    data["答案是否有误"] = True
                else:
                    base = f"学科：{SUBJECT}\n\n题目：\n{question}\n\n学生作答：\n{student_ans}"
                    user_prompt = base + (build_scoring_ref(bank) if bank else "")
                    result_raw = chat(model=ANA_MODEL, system=TEXT_SYSTEM_PROMPT,
                                      user=user_prompt, temperature=0.2)
                    data = safe_json_loads(result_raw)
                return idx, data

            all_results = [None] * len(problems)
            wrong_nums = []
            prog = st.progress(0, text="分析中…")
            done = 0

            with ThreadPoolExecutor(max_workers=5) as ex:
                futs = {ex.submit(_analyze, p, i): i for i, p in enumerate(problems)}
                for f in as_completed(futs):
                    try:
                        idx, data = f.result()
                        prob = problems[idx]
                        is_wrong = data.get("答案是否有误", False)
                        tags = data.get("错因标签", [])
                        if is_wrong and tags:
                            wrong_nums.append(str(prob.get("题号", str(idx+1))))
                            save_record(st.session_state.get("student_id", "unknown"),
                                        prob.get("题目",""), prob.get("学生答案",""),
                                        tags[0], data.get("温和反馈",""))
                        all_results[idx] = {**prob, "data": data}
                    except Exception as e:
                        all_results[futs[f]] = {**problems[futs[f]], "data": None, "error": str(e)}
                    done += 1
                    prog.progress(done / len(problems), text=f"已完成 {done}/{len(problems)} 道")

            prog.empty()
            st.session_state.full_page_results = all_results
            st.session_state.full_page_wrong_nums = wrong_nums
            st.rerun()

    if st.session_state.get("full_page_results") and page_mode == "📷 拍照整页·双模型（OCR模型+分析模型，可自由搭配）":
        results = st.session_state.full_page_results
        wrong_nums = st.session_state.get("full_page_wrong_nums", [])
        ERROR_DESC2 = {
            "A1":"抄写/转录错误","A2":"解题过程错误","A3":"基础知识薄弱",
            "B1":"关键概念识别错误","B2":"解题方法误判","B3":"知识迁移失败",
            "C1":"综合理解困难","C2":"畏难情绪放弃","C3":"抽象思维不足",
        }
        st.divider()
        if wrong_nums:
            st.warning(f"⚠️ 共发现 **{len(wrong_nums)}** 道错题：第 **{', '.join(wrong_nums)}** 题")
        else:
            st.success("未发现明显错误，做得不错！")
        st.markdown("### 📋 逐题分析报告")
        for r in results:
            if r is None:
                continue
            d = r.get("data")
            num = str(r.get("题号","?"))
            orig_q = r.get("题目","")
            orig_a = r.get("学生答案","")
            ques_type = r.get("题型","")
            if d:
                is_wrong = d.get("答案是否有误", False)
                tags = d.get("错因标签", [])
                if is_wrong:
                    st.markdown(
                        f"<div style='background:#FFF1F0;border-left:4px solid #FF4D4F;"
                        f"border-radius:8px;padding:1rem 1.2rem;margin-bottom:1rem;'>"
                        f"<b style='color:#CF1322;'>❌ 第 {num} 题{' · '+ques_type if ques_type else ''}（有误）</b>"
                        f"</div>", unsafe_allow_html=True)
                    with st.container(border=True):
                        st.markdown(f"**📝 原题**\n\n{orig_q}")
                        st.markdown(f"**✏️ 学生答案**\n\n{orig_a}")
                        st.divider()
                        render_scoring(d)
                        st.markdown(f"**📌 题型判断**：{d.get('题型判断','-')}")
                        for tag in tags:
                            st.error(f"**{tag}** — {ERROR_DESC2.get(tag,'')}")
                        for reason in d.get("判断理由",[]):
                            st.markdown(f"• {reason}")
                        for s in d.get("建议干预策略",[]):
                            st.markdown(f"• {s}")
                        st.info(d.get("温和反馈",""))
                else:
                    with st.expander(f"✅ 第 {num} 题{' · '+ques_type if ques_type else ''}（正确）"):
                        st.markdown(orig_q)
                        if d.get("温和反馈"):
                            st.info(d.get("温和反馈",""))
            elif r.get("error"):
                st.error(f"第 {num} 题分析失败：{r['error']}")

    st.stop()

# ══════════════════════════════════════════════════════
# 手动输入单题模式
# ══════════════════════════════════════════════════════
model_label = st.selectbox(
    "🧠 分析模型",
    list(TEXT_MODELS.keys()),
    key="text_model_select",
)
MODEL = TEXT_MODELS[model_label]

# 单题图片识别（可选）
with st.expander("📷 拍照识别单题（可选）"):
    single_img = st.file_uploader("上传单题图片，AI自动识别转文字",
                                  type=["jpg", "jpeg", "png"], key="single_img")
    if single_img:
        img_bytes_raw = single_img.read()
        st.image(img_bytes_raw, width=300)
        vis_label = st.selectbox("识别模型", list(VISION_MODELS.keys()), key="single_ocr_model")
        if st.button("识别图片内容", key="btn_ocr"):
            with st.spinner("正在识别..."):
                try:
                    compressed, mime = compress_image(img_bytes_raw)
                    img_b64 = base64.b64encode(compressed).decode("utf-8")
                    ocr_text = chat_with_image(
                        image_b64=img_b64, mime_type=mime,
                        model=VISION_MODELS[vis_label],
                        prompt='请识别图片内容，分两部分：1）题目（含选项）2）学生解题步骤或答案。数学符号用$...$包裹LaTeX格式。严格按JSON输出：{"题目": "...", "步骤": "..."}'
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
        bank = search_question_bank(question.strip(), subject=SUBJECT)
        direct = _direct_compare(student_answer.strip(), bank["correct_answer"]) if bank else None

        with st.spinner("AI正在分析中..."):
            try:
                if direct is True:
                    st.info("📚 已命中题库，直接比对正确")
                    data = {"答案是否有误": False, "题型判断": "客观题（题库直判）",
                            "错因标签": [], "判断理由": [], "建议干预策略": [],
                            "温和反馈": _random.choice(_PRAISE)}
                elif direct is False:
                    st.info("📚 已命中题库，答案有误，AI分析错因中…")
                    user_prompt = (f"学科：{SUBJECT}\n题目：{question.strip()}\n"
                                   f"正确答案：{bank['correct_answer']}\n学生答案：{student_answer.strip()}")
                    if bank.get("scoring_criteria"):
                        user_prompt += "\n" + build_scoring_ref(bank)
                    try:
                        result_raw = chat(model=MODEL, system=SHORT_ANALYSIS_SYSTEM, user=user_prompt, temperature=0.2)
                        data = safe_json_loads(result_raw)
                    except Exception:
                        result_raw = chat(model=MODEL, system=SHORT_ANALYSIS_SYSTEM, user=user_prompt, temperature=0.0)
                        data = safe_json_loads(result_raw)
                    data["答案是否有误"] = True
                else:
                    if bank:
                        st.info("📚 已命中题库（主观题），使用题库答案辅助判断")
                    user_prompt, _, _ = build_user_prompt(SUBJECT, question.strip(), student_answer.strip())
                    try:
                        result_raw = chat(model=MODEL, system=TEXT_SYSTEM_PROMPT, user=user_prompt, temperature=0.2)
                        data = safe_json_loads(result_raw)
                    except Exception:
                        result_raw = chat(model=MODEL, system=TEXT_SYSTEM_PROMPT, user=user_prompt, temperature=0.0)
                        data = safe_json_loads(result_raw)

                tags = data.get("错因标签", [])
                is_wrong = data.get("答案是否有误", False)
                main_error = (tags[0] if isinstance(tags, list) and tags else "UNKNOWN") if is_wrong else "UNKNOWN"
                save_record(st.session_state.get("student_id", "unknown"),
                            question.strip(), student_answer.strip(),
                            main_error, data.get("温和反馈", ""))
                error_count = count_same_error(main_error)
                if main_error != "UNKNOWN" and error_count >= DRILL_THRESHOLD:
                    upsert_alert(student_id=st.session_state.get("student_id"),
                                 error_code=main_error, error_count=error_count,
                                 threshold=DRILL_THRESHOLD)
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
    is_wrong = data.get("答案是否有误", False)

    ERROR_DESC = {
        "A1": "抄写/转录错误", "A2": "解题过程错误", "A3": "基础知识薄弱",
        "B1": "关键概念识别错误", "B2": "解题方法误判", "B3": "知识迁移失败",
        "C1": "综合理解困难", "C2": "畏难情绪放弃", "C3": "抽象思维不足",
    }

    if not is_wrong:
        # 答案正确：只显示✅和鼓励
        st.success(f"✅ 批改完成 · **{data.get('题型判断', '')}**")
        st.info(data.get("温和反馈", ""))
    else:
        # 答案有误：显示完整错因分析
        st.error(f"❌ 批改完成 · **{data.get('题型判断', '')}**")
        with st.container(border=True):
            st.markdown("**🏷️ 错因标签**")
            for tag in data.get("错因标签", []):
                st.error(f"**{tag}** — {ERROR_DESC.get(tag, '')}")
            st.markdown("**🔍 判断理由**")
            for reason in data.get("判断理由", []):
                st.write(f"• {reason}")
            st.markdown("**💡 建议干预策略**")
            for strategy in data.get("建议干预策略", []):
                st.write(f"• {strategy}")
        render_scoring(data)
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
