import os
import io
import re as _re
import json
import random as _random
import base64
import streamlit as st
import pandas as pd
import plotly.express as px
from concurrent.futures import ThreadPoolExecutor, as_completed
from PIL import Image, ImageDraw
from dotenv import load_dotenv
from llm_client import chat, chat_with_image
from db import save_record, count_same_error, upsert_alert, search_question_bank, init_question_bank
try:
    init_question_bank()
except Exception:
    pass
from ui import icon_title

load_dotenv()

# ── 登录守卫 ──────────────────────────────────────────
if not st.session_state.get("logged_in"):
    st.warning("请先登录")
    st.stop()

DRILL_THRESHOLD = 3

# ── 模型字典 ──────────────────────────────────────────
OCR_MODELS = {
    "qwen-vl-plus（通义视觉·默认）": "qwen-vl-plus",
    "qwen-vl-max（通义视觉·高精度）": "qwen-vl-max",
    "Doubao-1.5-Vision-Pro（豆包视觉·旗舰）": "doubao-1-5-vision-pro-32k-250115",
    "Doubao-1.5-Vision-Lite（豆包视觉·轻量）": "doubao-1-5-vision-lite-32k-250115",
    "GLM-4.6V（智谱视觉·通用）": "glm-4.6v",
    "GLM-4.1V-Thinking（智谱视觉·深度思考）": "glm-4.1v-thinking",
}

TEXT_MODELS = {
    "qwen-max（通义·最强）": "qwen-max",
    "qwen-plus（通义·均衡）": "qwen-plus",
    "qwen-turbo（通义·快速）": "qwen-turbo",
    "DeepSeek-V4-Pro（DeepSeek·旗舰）": "deepseek-v4-pro",
    "DeepSeek-V4-Flash（DeepSeek·快速）": "deepseek-v4-flash",
    "DeepSeek-R1（DeepSeek·推理）": "deepseek-reasoner",
    "Doubao-Seed-2.0-Pro（豆包·旗舰）": "doubao-seed-2.0-pro",
    "Doubao-1.5-Pro-256k（豆包·长文本）": "doubao-1-5-pro-256k-250115",
    "GLM-4-Plus（智谱·高精度）": "glm-4-plus",
    "GLM-4-Air（智谱·均衡）": "glm-4-air",
    "GLM-4-Flash（智谱·免费快速）": "glm-4-flash",
}

# ── Prompt 常量 ───────────────────────────────────────
BATCH_SYSTEM_PROMPT = """
你是一位经验丰富的作业批改助手，支持小学到高中所有学科和所有题型。严格输出合法JSON，不输出任何额外文本。

【批改宽松原则（重要，优先执行）】
- 时间单位缩写等价：分=分钟、秒=秒钟、时=小时，不扣分
- 数字与汉字等价：1=一、0.5=二分之一，不扣分
- 语文/英语阅读理解、简答、主观题：重点看意思和要点是否准确，不强求用词与参考答案完全相同，意思对即算对
- 计算步骤有小笔误但结果正确，酌情判对
- 有参考答案时以参考答案为准，但允许合理的同义表述

【题型判断与批改规则】
- 选择题/判断题：学生答案为字母(A/B/C/D)或√×，对比正确答案
- 填空题：学生答案与正确意思相符即可，允许合理缩写
- 解答/应用题：看步骤思路和结果是否正确
- 含画图要求的解答题（题目含"先画""画线段图""画示意图""画图分析"等）：
  * 学生答案若包含"[已画...]"说明已完成画图，画图部分视为完成，只批改列式计算
  * 学生答案若包含"[未画图]"，属于步骤不完整，错因选A2，判断理由中注明"未完成画图要求"
  * 无论画图情况如何，输出字段"需人工核对画图"均为true，提醒老师人工确认图示质量
- 未作答（学生答案为"未作答"或空白）：判为有误，错因选C2

【执行步骤】
第一步：识别题型，确定正确答案（有题库参考答案时以其为准）
第二步：宽松比对学生作答（未作答直接判错）
第三步：输出JSON

错因标签（仅答错时填写）：
A1抄写错误 / A2过程错误 / A3基础薄弱
B1概念错误 / B2方法误判 / B3迁移失败
C1综合困难 / C2未作答/畏难 / C3抽象不足

输出格式：
{
  "答案是否有误": true或false,
  "题型判断": "如：数学选择题 / 语文阅读简答",
  "正确答案": "正确答案是什么",
  "错因标签": [],
  "判断理由": [],
  "建议干预策略": [],
  "温和反馈": "见下方要求",
  "需人工核对画图": false
}

【温和反馈要求】
- 未作答：温柔询问是否遇到困难，给出一个方向性提示，60字以内
- 答案有误：苏格拉底问答法，肯定思考后用1~2个启发问题引导，不直接给答案，100~150字
- 答案正确：真诚鼓励具体亮点，30字以内

答案正确时，错因标签、判断理由、建议干预策略全部输出[]。
""".strip()

SINGLE_SYSTEM_PROMPT = """
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

SHORT_ANALYSIS_SYSTEM = """你是作业批改助手。已知学生答案有误，正确答案已提供。只需分析错因并写温和反馈，不必重新判断对错。严格输出合法JSON，不输出其他文字。

错因标签（选1个）：A1抄写错误/A2过程错误/A3基础薄弱/B1概念错误/B2方法误判/B3迁移失败/C1综合困难/C2畏难放弃/C3抽象不足

输出格式：
{"答案是否有误":true,"题型判断":"...","错因标签":["X"],"判断理由":["一句话说明"],"建议干预策略":["一句话"],"温和反馈":"苏格拉底式引导100-150字，不直接给答案"}""".strip()

COMBINED_PROMPT = """你是一位经验丰富的作业批改助手，请完成两步工作：

第一步：识别图片中的所有题目（数学/语文/英语/物理/化学等均适用）
第二步：对每道题独立判断学生答案是否正确，给出批改结果

【批改宽松原则（重要）】
- 时间单位缩写等价：分=分钟、秒=秒钟、时=小时，不扣分
- 数字与汉字等价：1=一、0.5=二分之一，不扣分
- 语文/英语主观题、阅读理解：重点看意思和要点是否准确，不强求用词完全一致
- 计算步骤有小笔误但结果正确，酌情判对

【学生答案识别规则】
- 学生答案只来自学生的手写内容，题目印刷内容不算学生答案
- 若答题区空白写"未作答"，不得推断

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

OCR_PROMPT_BATCH = """请识别图片中所有题目和学生的作答内容。

【最重要的原则】
学生答案只来自学生的手写内容。题目中印刷的插图、对话框、已知条件、例题等均属于题目本身，不是学生的作答。
如果某道题学生答题区域内没有任何手写文字或符号，必须写"未作答"，不得根据题目已知信息推断或补全答案。

识别规则（按题型分类）：
- 选择题：找学生圈选、填写或标注的选项字母（A/B/C/D），若空白写"未作答"
- 填空题：找学生在括号()或横线上填写的内容，若空白写"未作答"
- 比较大小/填符号题：仔细观察每个圆圈内学生写的符号，严格区分>（大于）和<（小于）和=，逐个读出；若空白写"未作答"
- 判断题：找学生写的√或×，若空白写"未作答"
- 解答/应用题：找学生在答题区域内手写的计算过程和步骤；若该题答题区域空白（即使题目插图中有数字或条件），写"未作答"
- 解答/应用题（含画图要求，如题目含"先画""画线段图""画示意图""用图表示""画一画再列式"等）：分两部分描述：①图示部分——学生是否在答题区画了线段图/示意图等，若画了简要描述图的内容（如"[已画线段图：明明78颗，聪聪60颗]"），若没画写"[未画图]"；②列式部分——学生的计算步骤和结论。两部分用空格连接输出
- 钟表/画图题：仔细观察钟面上是否有学生手绘的指针线条，描述时针和分针各自指向的数字；若看不到任何手绘指针写"未作答"
- 表格补全题：逐行读出学生在每个空格里填写的内容；若某格为空写"空白"

每道题严格按以下格式输出，题目之间用---分隔：
题目：[完整题目内容，含选项ABCD或小题序号]
题型：[选择题/填空题/判断题/解答题/应用题/画图题/表格题/连线题]
学生答案：[学生所写/所画/所填的答案，用文字详细描述]
---
只输出以上格式，不要其他说明文字。"""

# ── 辅助函数 ──────────────────────────────────────────
ERROR_DESC = {
    "A1": "抄写/转录错误", "A2": "解题过程错误", "A3": "基础知识薄弱",
    "B1": "关键概念错误", "B2": "解题方法误判", "B3": "知识迁移失败",
    "C1": "综合理解困难", "C2": "畏难放弃", "C3": "抽象思维不足",
}

_PRAISE = [
    "答对了！思路很清晰，继续保持！",
    "完全正确，做得很好！",
    "对！这道题掌握得很扎实！",
    "答对了，很棒！计算准确！",
    "正确！很好地运用了所学知识！",
]


def safe_json_loads(s: str):
    if not s:
        raise ValueError("empty")
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
    for sc, ec in [("{", "}"), ("[", "]")]:
        start = s.find(sc)
        end = s.rfind(ec)
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(s[start:end+1])
            except Exception:
                continue
    raise ValueError("JSON parse failed")


def compress_image(img_bytes: bytes, max_side: int = 2000) -> tuple[bytes, str]:
    img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
    w, h = img.size
    if max(w, h) > max_side:
        ratio = max_side / max(w, h)
        img = img.resize((int(w * ratio), int(h * ratio)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=88)
    return buf.getvalue(), "image/jpeg"


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
    _pos = {'√', '✓', '对', '正确', 'T', 'TRUE', 'Y', 'YES'}
    _neg = {'×', '✗', '错', '错误', 'F', 'FALSE', 'N', 'NO'}
    if sn in _pos or sn in _neg:
        s_pos = sn in _pos
        c_pos = any(p in cn for p in _pos)
        c_neg = any(n in cn for n in _neg)
        if c_pos and not c_neg:
            return s_pos
        if c_neg and not c_pos:
            return not s_pos
        return None
    return None


_OCR_VERIFY_SYSTEM = """你是OCR识别结果的审核专家。你会收到一段从学生作业图片中识别出来的文字，请逐题检查识别结果是否合理。

检查重点：
1. 数字是否合理（常见误读：8↔3、1↔7、0↔6、5↔6）
2. 符号是否正确（+、-、×、÷、=、>、<、√、×）
3. 学生答案部分是否有明显不合逻辑的内容（如"5+3=19"这种基本不可能的情况）
4. 填空/选择题的答案字符是否合理

处理规则：
- 若某题识别结果明显异常，在该题"学生答案"后加上【⚠️可能误读，建议核对】
- 若整体识别结果合理，原样输出，不做修改
- 不要修改题目内容本身，只在可疑处添加标注
- 保持原有格式（题目/题型/学生答案 的分隔结构）不变

直接输出处理后的识别结果，不要加任何说明。""".strip()


def verify_ocr(ocr_text: str, model: str = None) -> str:
    """对OCR识别结果进行自我复核，在可疑处添加⚠️标注。"""
    if not ocr_text or not ocr_text.strip():
        return ocr_text
    _model = model or os.getenv("DASHSCOPE_MODEL", "qwen-max")
    try:
        verified = chat(
            model=_model,
            system=_OCR_VERIFY_SYSTEM,
            user=f"以下是OCR识别结果，请审核：\n\n{ocr_text}",
            temperature=0.1,
        )
        return verified.strip() if verified.strip() else ocr_text
    except Exception:
        return ocr_text  # 复检失败时静默降级，不影响主流程


def _build_batch_ref(bank: dict) -> str:
    ref = (f"\n\n【📚 题库参考答案（来源：{bank['source'] or '题库'}）】\n{bank['correct_answer']}\n"
           f"请以此为标准答案判断学生作答，不要自行推导答案。")
    criteria = bank.get("scoring_criteria", [])
    if criteria:
        total_pts = bank.get("total_points", 0)
        ref += f"\n\n【按点给分】本题共 {total_pts} 分，请严格按以下评分细则逐点打分，并在JSON中额外输出 \"得分\"、\"满分\"、\"按点得分\" 字段：\n"
        for c in criteria:
            ref += f"- {c['criterion']}：{c['points']} 分\n"
        ref += ("\"按点得分\" 格式：[{\"要点\":\"...\",\"满分\":N,\"得分\":M,\"说明\":\"一句话说明\"},...]\n"
                "\"得分\" 为整数实际总得分，\"满分\" 为整数总分。")
    return ref


def render_scoring(data: dict):
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


def analyze_one(idx, question, steps, model, subject, student_id):
    """单题分析。题库命中时优先直判，减少AI调用次数。"""
    bank = search_question_bank(question, subject=subject)
    direct = _direct_compare(steps, bank["correct_answer"]) if bank else None

    try:
        if direct is True:
            data = {
                "答案是否有误": False,
                "题型判断": "客观题（题库直判）",
                "错因标签": [], "判断理由": [], "建议干预策略": [],
                "温和反馈": _random.choice(_PRAISE),
            }
        elif direct is False:
            user_prompt = (f"学科：{subject}\n题目：{question}\n"
                           f"正确答案：{bank['correct_answer']}\n学生答案：{steps}")
            if bank.get("scoring_criteria"):
                user_prompt += "\n" + _build_batch_ref(bank)
            try:
                result_raw = chat(model=model, system=SHORT_ANALYSIS_SYSTEM,
                                  user=user_prompt, temperature=0.2)
                data = safe_json_loads(result_raw)
            except Exception:
                result_raw = chat(model=model, system=SHORT_ANALYSIS_SYSTEM,
                                  user=user_prompt, temperature=0.0)
                data = safe_json_loads(result_raw)
            data["答案是否有误"] = True
        else:
            base = f"学科：{subject}\n\n题目：\n{question}\n\n学生作答：\n{steps}"
            user_prompt = base + (_build_batch_ref(bank) if bank else "")
            try:
                result_raw = chat(model=model, system=BATCH_SYSTEM_PROMPT,
                                  user=user_prompt, temperature=0.2)
                data = safe_json_loads(result_raw)
            except Exception:
                result_raw = chat(model=model, system=BATCH_SYSTEM_PROMPT,
                                  user=user_prompt, temperature=0.0)
                data = safe_json_loads(result_raw)

        is_wrong = data.get("答案是否有误", False)
        tags = data.get("错因标签", [])
        main_error = (tags[0] if isinstance(tags, list) and tags else "") if is_wrong else ""
        error_label = f"{main_error}·{ERROR_DESC[main_error]}" if main_error and main_error in ERROR_DESC else main_error

        save_record(student_id, question, steps, main_error or "UNKNOWN", data.get("温和反馈", ""))
        if main_error:
            error_count = count_same_error(main_error)
            if error_count >= DRILL_THRESHOLD:
                upsert_alert(student_id=student_id, error_code=main_error,
                             error_count=error_count, threshold=DRILL_THRESHOLD)

        scoring_str = ""
        if data.get("按点得分"):
            got = data.get("得分", 0)
            full = data.get("满分", 0)
            scoring_str = f"{got}/{full}分"

        批改结果 = "✅ 正确" if not is_wrong else f"❌ {error_label}"
        来源标记 = ("📚直判" if direct is not None else ("📚+AI" if bank else "🤖AI"))

        return idx, {
            "题号": idx + 1,
            "题目": question[:40] + "…" if len(question) > 40 else question,
            "题目_全文": question,
            "步骤": steps[:30] + "…" if len(steps) > 30 else steps,
            "批改结果": 批改结果,
            "来源": 来源标记,
            "错因": main_error,
            "错因描述": ERROR_DESC.get(main_error, ""),
            "题型": data.get("题型判断", "-"),
            "反馈": data.get("温和反馈", "-"),
            "得分": scoring_str,
            "按点得分": data.get("按点得分", []),
            "需人工核对画图": data.get("需人工核对画图", False),
            "状态": "✅ 完成",
            "是否有误": is_wrong,
        }
    except Exception as e:
        return idx, {
            "题号": idx + 1,
            "题目": question[:40] + "…" if len(question) > 40 else question,
            "题目_全文": question,
            "步骤": steps[:30] + "…" if len(steps) > 30 else steps,
            "批改结果": "⚠️ 失败", "来源": "-",
            "错因": "", "错因描述": "", "题型": "-",
            "反馈": f"分析失败：{e}", "得分": "", "按点得分": [],
            "需人工核对画图": False,
            "状态": "❌ 失败", "是否有误": False,
        }


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


def render_batch_results(results, key_prefix=""):
    """渲染批量批改结果（彩色格子 + 表格 + 详细反馈）。"""
    df = pd.DataFrame(results)

    total = len(results)
    correct_n = len([r for r in results if not r.get("是否有误") and r["状态"] == "✅ 完成"])
    wrong_n = len([r for r in results if r.get("是否有误")])

    mc1, mc2, mc3, mc4 = st.columns(4)
    mc1.metric("共批改", f"{total} 题")
    mc2.metric("✅ 正确", f"{correct_n} 题")
    mc3.metric("❌ 错误", f"{wrong_n} 题")
    with mc4:
        if wrong_n > 0:
            st.markdown(
                '<a href="/错题本" target="_self">'
                '<button style="width:100%;padding:8px;background:#FF4B4B;color:white;'
                'border:none;border-radius:6px;cursor:pointer;font-size:14px;">'
                f'📖 查看错题本（{wrong_n}题）</button></a>',
                unsafe_allow_html=True
            )

    # 彩色题号格子
    st.markdown("**题目概览：**")
    badge_html = ""
    for r in results:
        num = r["题号"]
        if r["状态"] != "✅ 完成":
            color, text_color = "#cccccc", "#333"
        elif r.get("是否有误"):
            color, text_color = "#FF4B4B", "white"
        else:
            color, text_color = "#21c45d", "white"
        badge_html += (
            f'<span title="{r.get("题目","")}" '
            f'style="display:inline-block;width:36px;height:36px;line-height:36px;'
            f'text-align:center;border-radius:6px;margin:3px;font-weight:bold;'
            f'font-size:13px;background:{color};color:{text_color};">{num}</span>'
        )
    st.markdown(f'<div style="line-height:1;">{badge_html}</div>', unsafe_allow_html=True)
    st.caption("🟢 正确　🔴 错误　⬜ 分析失败")

    st.subheader("📋 批改结果")
    display_cols = [c for c in ["题号", "题目", "批改结果", "来源", "得分", "题型"] if c in df.columns]
    st.dataframe(df[display_cols], use_container_width=True)

    # 错题分析饼图
    wrong_df = df[df["是否有误"] == True]
    if not wrong_df.empty and wrong_df["错因"].str.strip().any():
        st.divider()
        st.subheader("🔍 错题分析")
        error_summary = (wrong_df[wrong_df["错因"].str.strip() != ""]["错因"]
                         .value_counts().reset_index())
        error_summary.columns = ["错因", "次数"]
        if not error_summary.empty:
            col1, col2 = st.columns(2)
            with col1:
                st.dataframe(error_summary, use_container_width=True)
            with col2:
                fig = px.pie(error_summary, names="错因", values="次数", hole=0.35,
                             color_discrete_sequence=px.colors.qualitative.Set3)
                fig.update_layout(height=250, margin=dict(t=10, b=10, l=10, r=10))
                st.plotly_chart(fig, use_container_width=True)

    # 详细反馈
    st.divider()
    st.subheader("💬 详细反馈")
    for r in results:
        if r["状态"] == "✅ 完成":
            with st.container(border=True):
                col1, col2 = st.columns([4, 1])
                with col1:
                    score_badge = f" · **{r['得分']}**" if r.get("得分") else ""
                    if r.get("是否有误"):
                        code = r.get("错因", "")
                        desc = r.get("错因描述", ERROR_DESC.get(code, ""))
                        error_badge = f" · 错因：`{code}` {desc}" if code else ""
                        st.markdown(f"**第{r['题号']}题** ❌{error_badge}{score_badge}")
                    else:
                        st.markdown(f"**第{r['题号']}题** ✅{score_badge}")
                    full_q = r.get("题目_全文") or r.get("题目", "")
                    st.markdown(f"> {full_q}")
                with col2:
                    st.caption(r.get("题型", "-"))
                    if r.get("需人工核对画图"):
                        st.warning("⚠️ 含画图要求，请人工核对图示")
                breakdown = r.get("按点得分", [])
                if breakdown:
                    for item in breakdown:
                        icon = "✅" if item.get("得分", 0) >= item.get("满分", 1) else "❌"
                        st.markdown(
                            f"{icon} **{item.get('要点','')}**（满分{item.get('满分',0)}分）"
                            f"　得 **{item.get('得分',0)}** 分　— {item.get('说明','')}"
                        )
                    st.divider()
                st.write(r["反馈"])

    csv = df.drop(columns=["是否有误", "按点得分", "状态"], errors="ignore").to_csv(index=False).encode("utf-8-sig")
    st.download_button("⬇️ 导出批改结果", data=csv, key=f"dl_{key_prefix}",
                       file_name="批改结果.csv", mime="text/csv")


# ── Session State 初始化 ──────────────────────────────
for _k, _v in {
    "hw_batch_results": None,
    "hw_single_result": None,
    "hw_single_main_error": "UNKNOWN",
    "hw_single_error_count": 0,
    "hw_drill_items": None,
    "hw_drill_mastery": {},
    "hw_drill_requested": False,
    "hw_ocr_text": "",
    "hw_ocr_steps": "",
    "hw_photo_results": None,
    "hw_photo_wrong_nums": [],
    "hw_dual_problems": None,
}.items():
    if _k not in st.session_state:
        st.session_state[_k] = _v

# ── 页面标题 ─────────────────────────────────────────
icon_title("assets/icons/批量分析.svg", "作业批改")
st.caption("拍照或手动输入，AI批量批改并自动记录错题。")

user = st.session_state.get("user", {})
student_id = user.get("username", "unknown")

# ── 学科 & 模型选择（顶部共享） ────────────────────────
col_subj, col_ocr, col_model = st.columns([1, 1.5, 1.5])
with col_subj:
    SUBJECT = st.selectbox(
        "📚 学科",
        ["数学", "语文", "英语", "物理", "化学", "历史", "政治", "生物", "地理", "其他"],
        key="hw_subject"
    )
with col_ocr:
    ocr_label = st.selectbox(
        "📷 OCR识题模型",
        list(OCR_MODELS.keys()),
        key="hw_ocr_model"
    )
with col_model:
    model_label = st.selectbox(
        "🧠 批改分析模型",
        list(TEXT_MODELS.keys()),
        key="hw_analysis_model"
    )

OCR_MODEL = OCR_MODELS[ocr_label]
MODEL = TEXT_MODELS[model_label]
st.caption(f"当前方案：**{ocr_label.split('（')[0]}** 识题 → **{model_label.split('（')[0]}** 分析")
st.divider()

# ── 三个主功能 Tab ────────────────────────────────────
tab_photo, tab_batch, tab_single = st.tabs([
    "📷 拍照批改",
    "📋 批量文字输入",
    "🔍 单题精析",
])

# ══════════════════════════════════════════════════════
# Tab 1：拍照批改
# ══════════════════════════════════════════════════════
with tab_photo:
    photo_mode = st.radio(
        "识别方式",
        ["🚀 单模型（识题+批改一次完成，最快）",
         "🔗 双模型（OCR识题 + 分析模型，可自由搭配）",
         "📚 多图批量上传（多张试卷并行识别）"],
        key="hw_photo_mode",
        horizontal=True,
    )
    st.divider()

    # ── 单模型一键批改 ────────────────────────────────
    if photo_mode == "🚀 单模型（识题+批改一次完成，最快）":
        st.caption(f"⚡ 使用 **{ocr_label.split('（')[0]}** 视觉模型，一次调用完成识题+批改")
        uploaded_img = st.file_uploader(
            "上传整页试卷照片",
            type=["jpg", "jpeg", "png"],
            key="hw_single_photo"
        )
        if uploaded_img is not None:
            img_bytes_raw = uploaded_img.read()
            st.image(img_bytes_raw, use_container_width=True)

            if st.button("🚀 一键识别并批改所有题目", key="btn_combined", type="primary"):
                st.session_state.hw_photo_results = None
                st.session_state.hw_photo_wrong_nums = []
                with st.spinner("正在识别并分析中…"):
                    try:
                        compressed, mime = compress_image(img_bytes_raw)
                        img_b64 = base64.b64encode(compressed).decode("utf-8")
                        combined_prompt = f"学科：{SUBJECT}\n\n" + COMBINED_PROMPT
                        raw = chat_with_image(
                            image_b64=img_b64, mime_type=mime,
                            prompt=combined_prompt, model=OCR_MODEL, temperature=0.1,
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
                                save_record(student_id, r.get("题目", ""),
                                            r.get("学生答案", ""), tags[0],
                                            r.get("温和反馈", ""))
                        st.session_state.hw_photo_results = results
                        st.session_state.hw_photo_wrong_nums = wrong_nums
                        st.rerun()
                    except Exception as e:
                        st.error(f"识别失败：{e}")

        if st.session_state.get("hw_photo_results") and photo_mode == "🚀 单模型（识题+批改一次完成，最快）":
            results = st.session_state.hw_photo_results
            wrong_nums = st.session_state.get("hw_photo_wrong_nums", [])
            st.divider()
            if wrong_nums:
                st.warning(f"⚠️ 共发现 **{len(wrong_nums)}** 道错题：第 **{', '.join(wrong_nums)}** 题")
                st.markdown(
                    '<a href="/错题本" target="_self">'
                    '<button style="padding:6px 18px;background:#FF4B4B;color:white;'
                    'border:none;border-radius:6px;cursor:pointer;font-size:14px;">'
                    f'📖 查看错题本（{len(wrong_nums)}题）</button></a>',
                    unsafe_allow_html=True
                )
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
                        f"border-radius:8px;padding:1rem 1.2rem;margin-bottom:0.5rem;'>"
                        f"<b style='font-size:1.05rem;color:#CF1322;'>❌ 第 {num} 题"
                        f"{' · ' + ques_type if ques_type else ''}（有误）</b></div>",
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
                        for tag in tags:
                            st.error(f"**{tag}** — {ERROR_DESC.get(tag, '')}")
                        for reason in r.get("判断理由", []):
                            st.markdown(f"• {reason}")
                        for s in r.get("建议干预策略", []):
                            st.markdown(f"• {s}")
                        st.info(r.get("温和反馈", ""))
                else:
                    with st.expander(f"✅ 第 {num} 题{' · ' + ques_type if ques_type else ''}（正确）"):
                        st.markdown(orig_q)
                        st.caption(r.get("题型判断", ""))
                        if r.get("温和反馈"):
                            st.info(r.get("温和反馈", ""))

    # ── 双模型分步批改 ────────────────────────────────
    elif photo_mode == "🔗 双模型（OCR识题 + 分析模型，可自由搭配）":
        st.caption(f"🔗 **{ocr_label.split('（')[0]}** 识题 → **{model_label.split('（')[0]}** 分析")
        uploaded_img2 = st.file_uploader(
            "上传整页试卷照片",
            type=["jpg", "jpeg", "png"],
            key="hw_dual_photo"
        )
        if uploaded_img2 is not None:
            img_bytes_raw2 = uploaded_img2.read()
            st.image(img_bytes_raw2, use_container_width=True)

            if st.button("🔍 识别整页题目", key="btn_dual_ocr", type="primary"):
                st.session_state.hw_photo_results = None
                st.session_state.hw_photo_wrong_nums = []
                st.session_state.hw_dual_problems = None
                with st.spinner("正在识别题目…"):
                    try:
                        compressed2, mime2 = compress_image(img_bytes_raw2)
                        img_b64_2 = base64.b64encode(compressed2).decode("utf-8")
                        OCR_PROMPT = (
                            f"学科：{SUBJECT}\n"
                            "请识别图片中所有题目。对每道题提取：题号、题型、完整题目内容（含选项）、学生作答内容。\n"
                            "【重要】学生答案只来自学生的手写内容。题目中印刷的插图、对话框、已知条件均属于题目本身，不是学生作答。"
                            "若某题答题区域没有任何手写内容，学生答案填『未作答』，不得根据题目信息推断。\n"
                            "数学/物理公式用$...$包裹LaTeX，语文/英语保持原文。\n"
                            "严格输出JSON数组：\n"
                            "[{\"题号\":\"1\",\"题型\":\"选择题\",\"题目\":\"...\",\"学生答案\":\"...\"}]\n"
                            "只输出JSON，不要其他文字。"
                        )
                        ocr_raw = chat_with_image(image_b64=img_b64_2, mime_type=mime2,
                                                  model=OCR_MODEL, prompt=OCR_PROMPT)
                        # 🔍 OCR复核：让文本模型自我检查识别结果
                        with st.spinner("复核识别结果…"):
                            ocr_raw = verify_ocr(ocr_raw, model=MODEL)
                        problems = safe_json_loads(ocr_raw)
                        if isinstance(problems, list) and problems:
                            st.session_state.hw_dual_problems = problems
                            st.success(f"识别完成，共发现 **{len(problems)}** 道题目")
                        else:
                            st.error("识别结果为空，请重试或换清晰图片")
                    except Exception as e:
                        st.error(f"识别失败：{e}")

        if st.session_state.get("hw_dual_problems"):
            problems = st.session_state.hw_dual_problems
            st.markdown(f"**已识别 {len(problems)} 道题目：**")
            for p in problems:
                with st.expander(f"第 {p.get('题号','?')} 题 · {p.get('题型','')}"):
                    st.write(f"**题目：** {p.get('题目','')}")
                    st.write(f"**学生答案：** {p.get('学生答案','')}")

            if st.button("📊 分析所有题目错因", key="btn_dual_analyze", type="primary"):
                def _analyze_dual(prob, idx):
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
                            user_prompt += "\n" + _build_batch_ref(bank)
                        result_raw = chat(model=MODEL, system=SHORT_ANALYSIS_SYSTEM,
                                          user=user_prompt, temperature=0.2)
                        data = safe_json_loads(result_raw)
                        data["答案是否有误"] = True
                    else:
                        base = f"学科：{SUBJECT}\n\n题目：\n{question}\n\n学生作答：\n{student_ans}"
                        user_prompt = base + (_build_batch_ref(bank) if bank else "")
                        result_raw = chat(model=MODEL, system=SINGLE_SYSTEM_PROMPT,
                                          user=user_prompt, temperature=0.2)
                        data = safe_json_loads(result_raw)
                    return idx, data

                all_results = [None] * len(problems)
                wrong_nums = []
                prog = st.progress(0, text="分析中…")
                done = 0
                with ThreadPoolExecutor(max_workers=5) as ex:
                    futs = {ex.submit(_analyze_dual, p, i): i for i, p in enumerate(problems)}
                    for f in as_completed(futs):
                        try:
                            idx, data = f.result()
                            prob = problems[idx]
                            is_wrong = data.get("答案是否有误", False)
                            tags = data.get("错因标签", [])
                            if is_wrong and tags:
                                wrong_nums.append(str(prob.get("题号", str(idx+1))))
                                save_record(student_id, prob.get("题目",""),
                                            prob.get("学生答案",""), tags[0],
                                            data.get("温和反馈",""))
                            all_results[idx] = {**prob, "data": data}
                        except Exception as e:
                            all_results[futs[f]] = {**problems[futs[f]], "data": None, "error": str(e)}
                        done += 1
                        prog.progress(done / len(problems), text=f"已完成 {done}/{len(problems)} 道")
                prog.empty()
                st.session_state.hw_photo_results = all_results
                st.session_state.hw_photo_wrong_nums = wrong_nums
                st.rerun()

        if st.session_state.get("hw_photo_results") and photo_mode == "🔗 双模型（OCR识题 + 分析模型，可自由搭配）":
            results = st.session_state.hw_photo_results
            wrong_nums = st.session_state.get("hw_photo_wrong_nums", [])
            st.divider()
            if wrong_nums:
                st.warning(f"⚠️ 共发现 **{len(wrong_nums)}** 道错题：第 **{', '.join(wrong_nums)}** 题")
                st.markdown(
                    '<a href="/错题本" target="_self">'
                    '<button style="padding:6px 18px;background:#FF4B4B;color:white;'
                    'border:none;border-radius:6px;cursor:pointer;font-size:14px;">'
                    f'📖 查看错题本（{len(wrong_nums)}题）</button></a>',
                    unsafe_allow_html=True
                )
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
                            f"border-radius:8px;padding:1rem 1.2rem;margin-bottom:0.5rem;'>"
                            f"<b style='color:#CF1322;'>❌ 第 {num} 题{' · '+ques_type if ques_type else ''}（有误）</b>"
                            f"</div>", unsafe_allow_html=True)
                        with st.container(border=True):
                            st.markdown(f"**📝 原题**\n\n{orig_q}")
                            st.markdown(f"**✏️ 学生答案**\n\n{orig_a}")
                            st.divider()
                            render_scoring(d)
                            st.markdown(f"**📌 题型判断**：{d.get('题型判断','-')}")
                            for tag in tags:
                                st.error(f"**{tag}** — {ERROR_DESC.get(tag,'')}")
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

    # ── 多图批量上传 ──────────────────────────────────
    else:
        st.caption("上传多张试卷图片，并行OCR识别后批量分析")
        uploaded_imgs = st.file_uploader(
            "上传试卷/作业照片（可多选）",
            type=["jpg", "jpeg", "png"], key="hw_multi_img",
            accept_multiple_files=True
        )
        if uploaded_imgs:
            cols = st.columns(min(len(uploaded_imgs), 3))
            # 先读取bytes，展示缩略图
            all_bytes_preview = []
            for f in uploaded_imgs:
                b = f.read()
                all_bytes_preview.append(b)
                f.seek(0)
            for i, b in enumerate(all_bytes_preview):
                cols[i % 3].image(b, use_container_width=True)
            st.caption(f"已上传 {len(uploaded_imgs)} 张图片")

            if st.button("识别图片中的所有题目", key="btn_multi_ocr"):
                # 读取所有bytes（主线程）
                all_bytes = [f.read() for f in uploaded_imgs]

                def _ocr_one_page(args):
                    i, raw_bytes = args
                    compressed, mime = compress_image(raw_bytes)
                    b64 = base64.b64encode(compressed).decode()
                    text = chat_with_image(model=OCR_MODEL, image_b64=b64,
                                           mime_type=mime, prompt=OCR_PROMPT_BATCH)
                    # 🔍 OCR复核（线程内执行，不阻塞其他页面）
                    text = verify_ocr(text.strip(), model=MODEL)
                    return i, text.strip()

                prog = st.progress(0, text=f"并行识别 {len(all_bytes)} 张图片…")
                page_results = [None] * len(all_bytes)
                done = 0
                with ThreadPoolExecutor(max_workers=5) as ex:
                    futs = {ex.submit(_ocr_one_page, (i, b)): i
                            for i, b in enumerate(all_bytes)}
                    for fut in as_completed(futs):
                        i, text = fut.result()
                        page_results[i] = text
                        done += 1
                        prog.progress(done / len(all_bytes),
                                      text=f"已完成 {done}/{len(all_bytes)} 张…")
                prog.empty()
                combined = "\n---\n".join(t for t in page_results if t)
                st.session_state["hw_batch_textarea"] = combined
                st.success(f"识别完成！{len(uploaded_imgs)} 张图片已合并，请切换到「批量文字输入」标签确认后开始分析")
                st.rerun()

# ══════════════════════════════════════════════════════
# Tab 2：批量文字输入
# ══════════════════════════════════════════════════════
with tab_batch:
    st.info("""
每道题用「---」分隔，每道题内部格式：
```
题目：小明有5个苹果，小红比小明多3个，小红有几个？
步骤：5-3=2，小红有2个苹果
```
""")

    if st.button("填入示例", key="btn_example"):
        st.session_state["hw_batch_textarea"] = """题目：小明有5个苹果，小红的苹果是小明的8倍少4个，小红有几个苹果？
步骤：5×8=40，40-4=36，小红有36个苹果
---
题目：一根绳子长12米，剪去全长的1/3，还剩多少米？
步骤：12÷3=4，还剩4米
---
题目：学校买了8箱铅笔，每箱24支，一共多少支？
步骤：8+24=32，一共32支"""
        st.rerun()

    batch_text = st.text_area("粘贴题目（多题用 --- 分隔）",
                              height=300, key="hw_batch_textarea")

    if st.button("🚀 开始批量分析", type="primary", key="btn_batch"):
        if not batch_text.strip():
            st.warning("请先输入题目")
            st.stop()
        blocks = [b.strip() for b in batch_text.split("---") if b.strip()]
        if not blocks:
            st.warning("未识别到题目，请检查格式")
            st.stop()

        parsed = []
        for idx, block in enumerate(blocks):
            question, steps, q_type = "", "", ""
            for line in block.split("\n"):
                line = line.strip()
                if line.startswith("题目：") or line.startswith("题目:"):
                    question = line[3:].strip()
                elif line.startswith("题型：") or line.startswith("题型:"):
                    q_type = line[3:].strip()
                elif line.startswith("学生答案：") or line.startswith("学生答案:"):
                    steps = line[5:].strip()
                elif line.startswith("步骤：") or line.startswith("步骤:"):
                    steps = line[3:].strip()
            if not question and not steps:
                lines = [l.strip() for l in block.split("\n") if l.strip()]
                if lines:
                    question = lines[0]
                    steps = " ".join(lines[1:]) if len(lines) > 1 else "（未填写步骤）"
            if q_type and q_type not in question:
                question = f"【{q_type}】{question}"
            parsed.append((idx, question, steps))

        st.info(f"共识别到 {len(parsed)} 道题，并行分析中…")
        results_map = {}
        progress = st.progress(0, text="分析中…")
        done_count = 0

        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = {}
            for idx, question, steps in parsed:
                if not question:
                    results_map[idx] = {
                        "题号": idx + 1, "题目": "(格式错误，跳过)",
                        "题目_全文": "", "步骤": "",
                        "批改结果": "⚠️ 跳过", "来源": "-",
                        "错因": "UNKNOWN", "错因描述": "", "题型": "-",
                        "反馈": "-", "得分": "", "按点得分": [],
                        "需人工核对画图": False,
                        "状态": "❌ 跳过", "是否有误": False,
                    }
                    done_count += 1
                    progress.progress(done_count / len(parsed), text=f"已完成 {done_count}/{len(parsed)} 道")
                    continue
                f = executor.submit(analyze_one, idx, question, steps, MODEL, SUBJECT, student_id)
                futures[f] = idx

            for f in as_completed(futures):
                idx, row = f.result()
                results_map[idx] = row
                done_count += 1
                progress.progress(done_count / len(parsed), text=f"已完成 {done_count}/{len(parsed)} 道")

        progress.empty()
        results = [results_map[i] for i in range(len(parsed))]
        st.session_state["hw_batch_results"] = results
        ok = len([r for r in results if r["状态"] == "✅ 完成"])
        fail = len(results) - ok
        st.success(f"✅ 批量分析完成！{ok} 道成功，{fail} 道失败/跳过。")

    if st.session_state.get("hw_batch_results"):
        st.divider()
        render_batch_results(st.session_state["hw_batch_results"], key_prefix="batch")

# ══════════════════════════════════════════════════════
# Tab 3：单题精析（含专项训练）
# ══════════════════════════════════════════════════════
with tab_single:
    st.caption("单道题目深度分析，AI识别错因并温和引导，可生成专项训练。")

    # 可选：拍照识别单题
    with st.expander("📷 拍照识别单题（可选）"):
        single_img = st.file_uploader("上传单题图片，AI自动识别转文字",
                                      type=["jpg", "jpeg", "png"], key="hw_single_img")
        if single_img:
            img_bytes_raw_s = single_img.read()
            st.image(img_bytes_raw_s, width=300)
            if st.button("识别图片内容", key="btn_single_ocr"):
                with st.spinner("正在识别..."):
                    try:
                        compressed_s, mime_s = compress_image(img_bytes_raw_s)
                        img_b64_s = base64.b64encode(compressed_s).decode("utf-8")
                        ocr_text = chat_with_image(
                            image_b64=img_b64_s, mime_type=mime_s,
                            model=OCR_MODEL,
                            prompt='请识别图片内容，分两部分：1）题目（含选项）2）学生解题步骤或答案。数学符号用$...$包裹LaTeX格式。严格按JSON输出：{"题目": "...", "步骤": "..."}'
                        )
                        # 🔍 OCR复核
                        ocr_text = verify_ocr(ocr_text, model=MODEL)
                        try:
                            ocr_json = safe_json_loads(ocr_text)
                            st.session_state.hw_ocr_text = ocr_json.get("题目", "")
                            st.session_state.hw_ocr_steps = ocr_json.get("步骤", "")
                        except Exception:
                            st.session_state.hw_ocr_text = ocr_text
                            st.session_state.hw_ocr_steps = ""
                        # 若有⚠️标注，提示用户
                        if "⚠️" in ocr_text:
                            st.warning("识别完成，部分内容可能有误读，已标注⚠️，请核对后再提交")
                        else:
                            st.success("识别完成，已自动填入")
                    except Exception as e:
                        st.error(f"识别失败：{e}")

    question_s = st.text_area("请输入原题：",
                               value=st.session_state.get("hw_ocr_text", ""),
                               height=100, key="hw_single_question")
    student_answer_s = st.text_area("请输入学生解题步骤：",
                                    value=st.session_state.get("hw_ocr_steps", ""),
                                    height=100, key="hw_single_answer")

    if st.button("开始分析", type="primary", key="btn_single_analyze"):
        if not question_s.strip() or not student_answer_s.strip():
            st.warning("请填写完整信息")
        else:
            bank = search_question_bank(question_s.strip(), subject=SUBJECT)
            direct = _direct_compare(student_answer_s.strip(), bank["correct_answer"]) if bank else None

            with st.spinner("AI正在分析中..."):
                try:
                    if direct is True:
                        st.info("📚 已命中题库，直接比对正确")
                        data = {"答案是否有误": False, "题型判断": "客观题（题库直判）",
                                "错因标签": [], "判断理由": [], "建议干预策略": [],
                                "温和反馈": _random.choice(_PRAISE)}
                    elif direct is False:
                        st.info("📚 已命中题库，答案有误，AI分析错因中…")
                        user_prompt = (f"学科：{SUBJECT}\n题目：{question_s.strip()}\n"
                                       f"正确答案：{bank['correct_answer']}\n学生答案：{student_answer_s.strip()}")
                        if bank.get("scoring_criteria"):
                            user_prompt += "\n" + _build_batch_ref(bank)
                        try:
                            result_raw = chat(model=MODEL, system=SHORT_ANALYSIS_SYSTEM,
                                              user=user_prompt, temperature=0.2)
                            data = safe_json_loads(result_raw)
                        except Exception:
                            result_raw = chat(model=MODEL, system=SHORT_ANALYSIS_SYSTEM,
                                              user=user_prompt, temperature=0.0)
                            data = safe_json_loads(result_raw)
                        data["答案是否有误"] = True
                    else:
                        if bank:
                            st.info("📚 已命中题库（主观题），使用题库答案辅助判断")
                        base = f"学科：{SUBJECT}\n\n题目：\n{question_s.strip()}\n\n学生作答：\n{student_answer_s.strip()}"
                        user_prompt = base + (_build_batch_ref(bank) if bank else "")
                        try:
                            result_raw = chat(model=MODEL, system=SINGLE_SYSTEM_PROMPT,
                                              user=user_prompt, temperature=0.2)
                            data = safe_json_loads(result_raw)
                        except Exception:
                            result_raw = chat(model=MODEL, system=SINGLE_SYSTEM_PROMPT,
                                              user=user_prompt, temperature=0.0)
                            data = safe_json_loads(result_raw)

                    tags = data.get("错因标签", [])
                    is_wrong = data.get("答案是否有误", False)
                    main_error = (tags[0] if isinstance(tags, list) and tags else "UNKNOWN") if is_wrong else "UNKNOWN"
                    save_record(student_id, question_s.strip(), student_answer_s.strip(),
                                main_error, data.get("温和反馈", ""))
                    error_count = count_same_error(main_error)
                    if main_error != "UNKNOWN" and error_count >= DRILL_THRESHOLD:
                        upsert_alert(student_id=student_id, error_code=main_error,
                                     error_count=error_count, threshold=DRILL_THRESHOLD)
                    st.session_state.hw_single_result = data
                    st.session_state.hw_single_main_error = main_error
                    st.session_state.hw_single_error_count = error_count
                    st.session_state.hw_drill_items = None
                    st.session_state.hw_drill_mastery = {}
                except Exception as e:
                    st.exception(e)

    # 展示单题分析结果
    if st.session_state.hw_single_result:
        data = st.session_state.hw_single_result
        is_wrong = data.get("答案是否有误", False)

        if not is_wrong:
            st.success(f"✅ 批改完成 · **{data.get('题型判断', '')}**")
            st.info(data.get("温和反馈", ""))
        else:
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
            main_err = st.session_state.hw_single_main_error
            err_cnt = st.session_state.hw_single_error_count
            if main_err != "UNKNOWN" and err_cnt >= DRILL_THRESHOLD:
                st.warning(f"⚠ 错因 **{main_err}** 累计 **{err_cnt}** 次，建议专项训练。")

    # 专项训练区域
    main_err = st.session_state.hw_single_main_error
    err_cnt = st.session_state.hw_single_error_count
    if main_err != "UNKNOWN" and err_cnt >= DRILL_THRESHOLD:
        st.divider()
        st.subheader("🎯 专项训练（自动触发）")
        col1, col2 = st.columns([1, 1])
        with col1:
            clicked = st.button("生成专项训练题（5道）", key="btn_drill")
        with col2:
            if st.button("清空训练题", key="btn_drill_clear"):
                st.session_state.hw_drill_items = None
                st.session_state.hw_drill_mastery = {}
                st.rerun()

        if clicked:
            st.session_state.hw_drill_items = None
            st.session_state.hw_drill_mastery = {}
            st.session_state.hw_drill_requested = True

        if st.session_state.get("hw_drill_requested"):
            st.session_state.hw_drill_requested = False
            class_name = user.get("class_name", "")
            if any(k in class_name for k in ("高一", "高二", "高三")):
                grade_hint = "高中"
            elif any(k in class_name for k in ("七年级", "八年级", "九年级")):
                grade_hint = "初中"
            else:
                grade_hint = "小学中高年级"
            drill_system = f"你是{SUBJECT}专项训练题生成器。严格输出JSON：{{\"训练题\":[{{\"题目\":\"\",\"提示\":\"\",\"提醒\":\"\"}}]}}"
            drill_user = f"学科：{SUBJECT}。错因标签：{main_err}。生成5道由浅入深的{grade_hint}{SUBJECT}练习题。"
            try:
                with st.spinner("正在生成专项训练题..."):
                    drill_raw = chat(model=MODEL, system=drill_system,
                                     user=drill_user, temperature=0.4)
                drill_data = safe_json_loads(drill_raw)
                st.session_state.hw_drill_items = normalize_drill_items(drill_data)
            except Exception as e:
                st.error(f"生成失败：{e}")

        if st.session_state.hw_drill_items:
            st.markdown("### 📚 训练题列表（打卡）")
            for i, q in enumerate(st.session_state.hw_drill_items, start=1):
                status = st.session_state.hw_drill_mastery.get(i, "未标记")
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
                            st.session_state.hw_drill_mastery[i] = "已掌握"
                    with b2:
                        if st.button("🧠 我还不会", key=f"notyet_{i}"):
                            st.session_state.hw_drill_mastery[i] = "还不会"
