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
你是通用学科错因分析系统，支持小学到高中所有学科和所有题型（选择题、填空题、判断题、解答题、应用题、阅读理解等）。严格输出合法JSON，不输出任何额外文本。

【题型判断与分析规则】
- 选择题/判断题：学生答案为字母(A/B/C/D)或√×，与正确答案比对即可，若学生答案为"未作答"则判错
- 填空题：学生答案为填入内容，判断是否正确
- 解答/应用题：看步骤是否完整正确
- 未作答（学生答案为空或"未作答"）：直接判为有误，错因选C2

【执行步骤】
第一步：识别题型，自己推导正确答案
第二步：与学生答案比对（未作答直接判错）
第三步：输出JSON

错因标签：
A1抄写错误 / A2过程错误 / A3基础薄弱
B1概念错误 / B2方法误判 / B3迁移失败
C1综合困难 / C2未作答/畏难 / C3抽象不足

输出格式：
{
  "答案是否有误": true或false,
  "题型判断": "如：数学选择题 / 语文填空题",
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

答案正确时后四个数组全部为[]。
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


def analyze_one(idx, question, steps, model, subject, student_id):
    """单题分析，用于并行调用。优先使用题库答案（含按点给分）。"""
    bank = search_question_bank(question, subject=subject)
    base = f"学科：{subject}\n\n题目：\n{question}\n\n学生作答：\n{steps}"
    user_prompt = base + (_build_batch_ref(bank) if bank else "")
    try:
        result_raw = chat(model=model, system=SYSTEM_PROMPT, user=user_prompt, temperature=0.2)
        try:
            data = safe_json_loads(result_raw)
        except Exception:
            result_raw = chat(model=model, system=SYSTEM_PROMPT, user=user_prompt, temperature=0.0)
            data = safe_json_loads(result_raw)

        is_wrong = data.get("答案是否有误", False)
        tags = data.get("错因标签", [])
        main_error = (tags[0] if isinstance(tags, list) and tags else "UNKNOWN") if is_wrong else "UNKNOWN"

        save_record(student_id, question, steps, main_error, data.get("温和反馈", ""))
        error_count = count_same_error(main_error)
        if main_error != "UNKNOWN" and error_count >= DRILL_THRESHOLD:
            upsert_alert(student_id=student_id, error_code=main_error,
                         error_count=error_count, threshold=DRILL_THRESHOLD)

        scoring_str = ""
        if data.get("按点得分"):
            got = data.get("得分", 0)
            full = data.get("满分", 0)
            scoring_str = f"得分：{got}/{full}分"

        return idx, {
            "题号": idx + 1,
            "题目": question[:40] + "…" if len(question) > 40 else question,
            "步骤": steps[:30] + "…" if len(steps) > 30 else steps,
            "错因": main_error,
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
            "步骤": steps[:30] + "…" if len(steps) > 30 else steps,
            "错因": "UNKNOWN", "题型": "-",
            "反馈": f"分析失败：{e}", "得分": "", "按点得分": [],
            "状态": "❌ 失败", "是否有误": False,
        }


# ── 页面 ─────────────────────────────────────────────
icon_title("assets/icons/批量分析.svg", "批量分析")
st.caption("一次粘贴多道题目，AI批量识别错因并写入错题本。")

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
        "🧠 错因分析模型",
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

识别规则：
- 选择题：找学生圈选、填写或标注的选项字母（A/B/C/D），若空白写"未作答"
- 填空题：找学生在括号()或横线上填写的内容，若空白写"未作答"
- 判断题：找学生写的√或×，若空白写"未作答"
- 解答/应用题：找学生的计算过程和步骤，若空白写"未作答"

每道题严格按以下格式输出，题目之间用---分隔：
题目：[完整题目内容，含选项ABCD]
题型：[选择题/填空题/判断题/解答题/应用题]
学生答案：[学生所写的答案、圈选的选项或解题步骤]
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

            def _ocr_one_page(args):
                i, f = args
                b64, mime = _compress_batch(f.read())
                text = chat_with_image(model=OCR_MODEL, image_b64=b64,
                                       mime_type=mime, prompt=OCR_PROMPT_BATCH)
                return i, text.strip()

            prog = st.progress(0, text=f"并行识别 {len(uploaded_imgs)} 张图片…")
            page_results = [None] * len(uploaded_imgs)
            done = 0
            with ThreadPoolExecutor(max_workers=5) as ex:
                futs = {ex.submit(_ocr_one_page, (i, f)): i
                        for i, f in enumerate(uploaded_imgs)}
                for fut in as_completed(futs):
                    i, text = fut.result()
                    page_results[i] = text
                    done += 1
                    prog.progress(done / len(uploaded_imgs),
                                  text=f"已完成 {done}/{len(uploaded_imgs)} 张…")
            prog.empty()

            combined = "\n---\n".join(t for t in page_results if t)
            st.session_state["batch_textarea"] = combined
            st.success(f"识别完成！{len(uploaded_imgs)} 张图片已合并，请确认后点击「开始批量分析」")
            st.rerun()

batch_text = st.text_area("粘贴题目（多题用 --- 分隔）",
                          height=300, key="batch_textarea")

col1, col2 = st.columns([1, 3])
with col1:
    max_questions = st.number_input("最多分析几道", min_value=1, max_value=20, value=10)

if st.button("🚀 开始批量分析", type="primary", key="btn_batch"):
    if not batch_text.strip():
        st.warning("请先输入题目")
        st.stop()

    blocks = [b.strip() for b in batch_text.split("---") if b.strip()]
    blocks = blocks[:max_questions]

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
    st.divider()
    st.subheader("📊 分析结果")
    df = pd.DataFrame(results)
    display_cols = [c for c in ["题号", "题目", "错因", "得分", "题型", "状态"] if c in df.columns]
    st.dataframe(df[display_cols], use_container_width=True)

    st.divider()
    st.subheader("🔍 本次错因汇总")
    error_summary = df[df["错因"] != "UNKNOWN"]["错因"].value_counts().reset_index()
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

    st.divider()
    st.subheader("💬 详细反馈")
    for r in results:
        if r["状态"] == "✅ 完成":
            with st.container(border=True):
                col1, col2 = st.columns([3, 1])
                with col1:
                    wrong_mark = "❌" if r.get("是否有误") else "✅"
                    score_badge = f" · {r['得分']}" if r.get("得分") else ""
                    st.markdown(f"**第{r['题号']}题** {wrong_mark} · 错因：`{r['错因']}`{score_badge}")
                    st.caption(r["题目"])
                with col2:
                    st.markdown(f"**{r['状态']}**")
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

    csv = df.drop(columns=["是否有误", "按点得分"], errors="ignore").to_csv(index=False).encode("utf-8-sig")
    st.download_button("⬇️ 导出批量分析结果", data=csv,
                       file_name="batch_analysis.csv", mime="text/csv")
