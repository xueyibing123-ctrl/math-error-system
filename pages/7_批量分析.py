import os
import json
import base64
import streamlit as st
import pandas as pd
import plotly.express as px
from concurrent.futures import ThreadPoolExecutor, as_completed
from dotenv import load_dotenv
from llm_client import chat, chat_with_image
from PIL import Image
import io
from db import save_record, count_same_error, upsert_alert, search_question_bank, init_question_bank
try:
    init_question_bank()
except Exception:
    pass
from ui import icon_title, icon_text

load_dotenv()

# 登录守卫
if not st.session_state.get("logged_in"):
    st.warning("请先登录")
    st.stop()

DRILL_THRESHOLD = 3

OCR_MODELS = {
    "qwen-vl-plus（通义视觉·默认）": "qwen-vl-plus",
    "qwen-vl-max（通义视觉·高精度）": "qwen-vl-max",
    "Doubao-1.5-Vision-Pro（豆包视觉·旗舰）": "doubao-1-5-vision-pro-32k-250115",
    "Doubao-1.5-Vision-Lite（豆包视觉·轻量）": "doubao-1-5-vision-lite-32k-250115",
    "GLM-4.6V（智谱视觉·通用）": "glm-4.6v",
    "GLM-4.1V-Thinking（智谱视觉·深度思考）": "glm-4.1v-thinking",
}

AVAILABLE_MODELS = {
    "qwen-max（通义千问·推荐）": "qwen-max",
    "DeepSeek-V4-Pro（最强·深度思考）": "deepseek-v4-pro",
    "DeepSeek-V4-Flash（快速·低价）": "deepseek-v4-flash",
    "DeepSeek-R1（链式推理）": "deepseek-reasoner",
    "Doubao-Seed-2.0-Pro（豆包·最新旗舰）": "doubao-seed-2.0-pro",
    "Doubao-1.5-Pro-256k（豆包·长文本）": "doubao-1-5-pro-256k-250115",
    "GLM-4-Flash（智谱·快速免费）": "glm-4-flash",
}

SYSTEM_PROMPT = """
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
- 钟表/画图题：学生答案为指针位置的文字描述，对比题目要求时刻
- 表格补全题：逐格判断内容是否符合规律或题意
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
  "温和反馈": "见下方要求"
}

【温和反馈要求】
- 未作答：温柔询问是否遇到困难，给出一个方向性提示，60字以内
- 答案有误：苏格拉底问答法，肯定思考后用1~2个启发问题引导，不直接给答案，100~150字
- 答案正确：真诚鼓励具体亮点，30字以内

答案正确时，错因标签、判断理由、建议干预策略全部输出[]。
""".strip()


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
    for start_char, end_char in [("{", "}"), ("[", "]")]:
        start = s.find(start_char)
        end = s.rfind(end_char)
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(s[start:end+1])
            except Exception:
                continue
    raise ValueError("JSON parse failed")


import re as _re, random as _random

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
    """
    保守的直接比对。返回 True（对）/ False（错）/ None（无法确定，交给AI）。
    只对把握十足的题型直判，主观题/解答题一律返回 None。
    """
    s = (student_ans or "").strip()
    c = (correct_ans or "").strip()

    if not s or s in ("未作答", "（未填写步骤）"):
        return False  # 空白=错

    def _norm(x):
        x = _re.sub(r'\s+', '', x)
        x = x.replace('分钟', '分').replace('秒钟', '秒').replace('小时', '时')
        return x.upper()

    sn, cn = _norm(s), _norm(c)

    # 完全匹配（归一化后）
    if sn == cn:
        return True

    # 选择题：学生答案是单个字母 A-D
    if _re.fullmatch(r'[ABCD]', sn):
        # 从正确答案里找最先出现的选项字母
        m = _re.search(r'[ABCD]', cn[:20])  # 只看前20字，防止误匹配
        if m:
            return sn == m.group()
        return None

    # 判断题：学生答案是 √/× 或对/错
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

    # 其余（解答题/主观题）交给 AI
    return None


def _build_batch_ref(bank: dict) -> str:
    """构建题库参考注入文本（含评分细则）。"""
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


ERROR_DESC = {
    "A1": "抄写/转录错误", "A2": "解题过程错误", "A3": "基础知识薄弱",
    "B1": "关键概念错误", "B2": "解题方法误判", "B3": "知识迁移失败",
    "C1": "综合理解困难", "C2": "畏难放弃", "C3": "抽象思维不足",
}


def analyze_one(idx, question, steps, model, subject, student_id):
    """单题分析。题库命中时优先直判，减少 AI 调用次数。"""
    bank = search_question_bank(question, subject=subject)
    direct = _direct_compare(steps, bank["correct_answer"]) if bank else None

    try:
        if direct is True:
            # ✅ 题库直判正确，0 次 AI 调用
            data = {
                "答案是否有误": False,
                "题型判断": "客观题（题库直判）",
                "错因标签": [], "判断理由": [], "建议干预策略": [],
                "温和反馈": _random.choice(_PRAISE),
            }
        elif direct is False:
            # ❌ 题库直判错误，只让 AI 分析错因+反馈，不再判断对错
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
            data["答案是否有误"] = True  # 确保字段正确
        else:
            # 题库未命中 or 主观题，全量 AI 推理
            base = f"学科：{subject}\n\n题目：\n{question}\n\n学生作答：\n{steps}"
            user_prompt = base + (_build_batch_ref(bank) if bank else "")
            try:
                result_raw = chat(model=model, system=SYSTEM_PROMPT,
                                  user=user_prompt, temperature=0.2)
                data = safe_json_loads(result_raw)
            except Exception:
                result_raw = chat(model=model, system=SYSTEM_PROMPT,
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
            "错因": "", "题型": "-",
            "反馈": f"分析失败：{e}", "得分": "", "按点得分": [],
            "状态": "❌ 失败", "是否有误": False,
        }


# ── 页面 ─────────────────────────────────────────────
icon_title("assets/icons/批量分析.svg", "批量分析")
st.caption("拍照或粘贴作业，AI批量批改并记录错题。")

user = st.session_state.get("user", {})
student_id = user.get("username", "unknown")

# ── 学科 & 模型选择 ───────────────────────────────────
col_subj, col_ocr, col_model = st.columns([1, 1.5, 1.5])
with col_subj:
    SUBJECT = st.selectbox(
        "📚 学科",
        ["数学", "语文", "英语", "物理", "化学", "历史", "政治", "生物", "地理", "其他"],
        key="batch_subject"
    )
with col_ocr:
    ocr_label = st.selectbox(
        "📷 OCR识题模型",
        list(OCR_MODELS.keys()),
        key="batch_ocr_model"
    )
with col_model:
    model_label = st.selectbox(
        "🧠 批改分析模型",
        list(AVAILABLE_MODELS.keys()),
        key="batch_analysis_model"
    )

OCR_MODEL = OCR_MODELS[ocr_label]
MODEL = AVAILABLE_MODELS[model_label]
st.caption(f"当前方案：**{ocr_label.split('（')[0]}** 识题 → **{model_label.split('（')[0]}** 分析（并行加速）")
st.divider()

icon_text("assets/icons/pencil-line.svg", "输入格式说明", size=22)
st.info("""
每道题用「---」分隔，每道题内部格式：
```
题目：小明有5个苹果，小红比小明多3个，小红有几个？
步骤：5-3=2，小红有2个苹果
```
""")

if st.button("填入示例", key="btn_example"):
    st.session_state["batch_textarea"] = """题目：小明有5个苹果，小红的苹果是小明的8倍少4个，小红有几个苹果？
步骤：5×8=40，40-4=36，小红有36个苹果
---
题目：一根绳子长12米，剪去全长的1/3，还剩多少米？
步骤：12÷3=4，还剩4米
---
题目：学校买了8箱铅笔，每箱24支，一共多少支？
步骤：8+24=32，一共32支"""
    st.rerun()

with st.expander("📷 拍照上传（自动识别题目和步骤）"):
    uploaded_imgs = st.file_uploader(
        "上传试卷/作业照片（可多选，支持多张）",
        type=["jpg", "jpeg", "png"], key="batch_img",
        accept_multiple_files=True
    )
    if uploaded_imgs:
        cols = st.columns(min(len(uploaded_imgs), 3))
        for i, f in enumerate(uploaded_imgs):
            cols[i % 3].image(f.read(), use_container_width=True)
            f.seek(0)
        st.caption(f"已上传 {len(uploaded_imgs)} 张图片")

        if st.button("识别图片中的所有题目", key="btn_batch_ocr"):
            OCR_PROMPT_BATCH = """请识别图片中所有题目和学生的作答内容。

识别规则（按题型分类）：
- 选择题：找学生圈选、填写或标注的选项字母（A/B/C/D），若空白写"未作答"
- 填空题：找学生在括号()或横线上填写的内容，若空白写"未作答"
- 比较大小/填符号题（如"在○里填上>、<或="）：仔细观察每个圆圈内学生写的符号，严格区分>（大于，开口朝左）和<（小于，开口朝右）和=，逐个读出每题的符号；若空白写"未作答"
- 判断题：找学生写的√或×，若空白写"未作答"
- 解答/应用题：找学生的计算过程和步骤，若空白写"未作答"
- 钟表/画图题（如"在钟表上画出时刻""画一画"等）：仔细观察钟面上是否有学生手绘的指针线条，描述时针和分针各自指向的数字（如"时针指向6，分针指向12，表示6:00"），若钟面上看不到任何手绘指针写"未作答"
- 时间轴标记题（如"用☆标出时刻"）：观察数轴上是否有学生标的符号（☆、×、点等），描述该符号在数轴上的大致位置（如"☆在6:30处"），若无任何标记写"未作答"
- 表格补全题：逐行读出学生在每个空格里填写的内容，格式为"第N格：[内容]"，若某格为空写"空白"
- 连线题：描述学生连了哪些线，若未连写"未作答"

每道题严格按以下格式输出，题目之间用---分隔：
题目：[完整题目内容，含选项ABCD或小题序号]
题型：[选择题/填空题/判断题/解答题/应用题/画图题/表格题/连线题]
学生答案：[学生所写/所画/所填的答案，用文字详细描述]
---
只输出以上格式，不要其他说明文字。"""

            def _compress_batch(raw_bytes):
                img = Image.open(io.BytesIO(raw_bytes)).convert("RGB")
                w, h = img.size
                if max(w, h) > 2000:
                    ratio = 2000 / max(w, h)
                    img = img.resize((int(w * ratio), int(h * ratio)), Image.LANCZOS)
                buf = io.BytesIO()
                img.save(buf, format="JPEG", quality=88)
                return base64.b64encode(buf.getvalue()).decode(), "image/jpeg"

            # 先在主线程读取所有文件内容（UploadedFile不是线程安全的）
            all_bytes = [f.read() for f in uploaded_imgs]

            def _ocr_one_page(args):
                i, raw_bytes = args
                b64, mime = _compress_batch(raw_bytes)
                text = chat_with_image(model=OCR_MODEL, image_b64=b64,
                                       mime_type=mime, prompt=OCR_PROMPT_BATCH)
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
            st.session_state["batch_textarea"] = combined
            st.success(f"识别完成！{len(uploaded_imgs)} 张图片已合并，请确认后点击「开始批量分析」")
            st.rerun()

batch_text = st.text_area("粘贴题目（多题用 --- 分隔）",
                          height=300, key="batch_textarea")

if st.button("🚀 开始批量分析", type="primary", key="btn_batch"):
    if not batch_text.strip():
        st.warning("请先输入题目")
        st.stop()

    blocks = [b.strip() for b in batch_text.split("---") if b.strip()]

    if not blocks:
        st.warning("未识别到题目，请检查格式")
        st.stop()

    # 解析每个 block 的题目、题型和学生答案
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
        # 把题型信息附加到题目里，让分析模型知道是什么类型
        if q_type and q_type not in question:
            question = f"【{q_type}】{question}"
        parsed.append((idx, question, steps))

    st.info(f"共识别到 {len(parsed)} 道题，并行分析中…")
    results_map = {}
    progress = st.progress(0, text="分析中…")
    done_count = 0

    # 并行调用，最多 5 个线程同时跑
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {}
        for idx, question, steps in parsed:
            if not question:
                results_map[idx] = {
                    "题号": idx + 1, "题目": "(格式错误，跳过)", "步骤": "",
                    "错因": "UNKNOWN", "题型": "-", "反馈": "-", "状态": "❌ 跳过", "是否有误": False,
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
    st.session_state["batch_results"] = results
    ok = len([r for r in results if r["状态"] == "✅ 完成"])
    fail = len(results) - ok
    st.success(f"✅ 批量分析完成！{ok} 道成功，{fail} 道失败/跳过。")

if st.session_state.get("batch_results"):
    results = st.session_state["batch_results"]
    df = pd.DataFrame(results)

    # ── 汇总数字 ───────────────────────────────────────
    st.divider()
    total = len(results)
    correct_n = len([r for r in results if not r.get("是否有误") and r["状态"] == "✅ 完成"])
    wrong_n = len([r for r in results if r.get("是否有误")])
    mc1, mc2, mc3 = st.columns(3)
    mc1.metric("共批改", f"{total} 题")
    mc2.metric("✅ 正确", f"{correct_n} 题")
    mc3.metric("❌ 错误", f"{wrong_n} 题")

    st.subheader("📋 批改结果")
    display_cols = [c for c in ["题号", "题目", "批改结果", "来源", "得分", "题型"] if c in df.columns]
    st.dataframe(df[display_cols], use_container_width=True)

    # ── 错题分析（只统计错题）──────────────────────────
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

    # ── 详细反馈 ────────────────────────────────────────
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
                    # 展示完整题目
                    full_q = r.get("题目_全文") or r.get("题目", "")
                    st.markdown(f"> {full_q}")
                with col2:
                    st.caption(r.get("题型", "-"))
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
    st.download_button("⬇️ 导出批改结果", data=csv,
                       file_name="批改结果.csv", mime="text/csv")
