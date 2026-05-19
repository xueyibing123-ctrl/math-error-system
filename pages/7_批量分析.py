import os
import json
import base64
import streamlit as st
import pandas as pd
import plotly.express as px
from concurrent.futures import ThreadPoolExecutor, as_completed
from dotenv import load_dotenv
from llm_client import chat, chat_with_image
from db import save_record, count_same_error, upsert_alert
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
    "qwen-max（通义千问·默认）": "qwen-max",
    "qwen-plus（通义千问·快速便宜）": "qwen-plus",
    "qwen2.5-math-72b（通义数学专项）": "qwen2.5-math-72b-instruct",
    "DeepSeek-V4-Pro（最强·深度思考）": "deepseek-v4-pro",
    "DeepSeek-V4-Flash（快速·低价）": "deepseek-v4-flash",
    "DeepSeek-V3.2（均衡）": "deepseek-chat",
    "DeepSeek-R1（链式推理）": "deepseek-reasoner",
    "Doubao-1.5-Pro-256k（豆包·旗舰）": "doubao-1-5-pro-256k-250115",
    "GLM-4.7（智谱·多步推理）": "glm-4.7",
    "GLM-4-Flash（智谱·快速免费）": "glm-4-flash",
}

SYSTEM_PROMPT = """
你是通用学科错因分析系统，适用于小学、初中、高中各年级，覆盖数学、语文、英语、物理、化学、历史、政治等所有学科，支持选择题、填空题、判断题、解析题、应用题、阅读理解、写作、翻译等所有题型。你必须严格输出合法JSON，不要输出任何额外文本。

【必须按以下步骤执行，不得跳过】

第一步：独立作答（不看学生答案，先自己得出正确答案）
第二步：比对（将学生答案与正确答案比较）
- 完全正确 → "答案是否有误": false
- 有错误或明显不足 → "答案是否有误": true

第三步：输出JSON

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
  "温和反馈": "见下方要求"
}

【温和反馈要求】
- 答案有误：用苏格拉底问答法，先肯定学生思考，再用1~2个启发性问题引导学生自己发现错误，不直接给答案，语气亲切，100~150字
- 答案正确：真诚鼓励，夸具体思维亮点，30字以内

严格规定：答案正确时"答案是否有误"=false，后四个数组全部为[]。
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


def analyze_one(idx, question, steps, model, subject, student_id):
    """单题分析，用于并行调用。"""
    user_prompt = f"学科：{subject}\n\n题目：\n{question}\n\n学生作答：\n{steps}"
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

        return idx, {
            "题号": idx + 1,
            "题目": question[:40] + "…" if len(question) > 40 else question,
            "步骤": steps[:30] + "…" if len(steps) > 30 else steps,
            "错因": main_error,
            "题型": data.get("题型判断", "-"),
            "反馈": data.get("温和反馈", "-"),
            "状态": "✅ 完成",
            "是否有误": is_wrong,
        }
    except Exception as e:
        return idx, {
            "题号": idx + 1,
            "题目": question[:40] + "…" if len(question) > 40 else question,
            "步骤": steps[:30] + "…" if len(steps) > 30 else steps,
            "错因": "UNKNOWN", "题型": "-",
            "反馈": f"分析失败：{e}", "状态": "❌ 失败", "是否有误": False,
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
    st.session_state["batch_input"] = """题目：小明有5个苹果，小红的苹果是小明的8倍少4个，小红有几个苹果？
步骤：5×8=40，40-4=36，小红有36个苹果
---
题目：一根绳子长12米，剪去全长的1/3，还剩多少米？
步骤：12÷3=4，还剩4米
---
题目：学校买了8箱铅笔，每箱24支，一共多少支？
步骤：8+24=32，一共32支"""

with st.expander("📷 拍照上传（自动识别题目和步骤）"):
    uploaded_img = st.file_uploader("上传试卷/作业照片，AI自动提取所有题目",
                                    type=["jpg", "jpeg", "png"], key="batch_img")
    if uploaded_img:
        st.image(uploaded_img, width=400)
        if st.button("识别图片中的所有题目", key="btn_batch_ocr"):
            img_bytes = uploaded_img.read()
            img_b64 = base64.b64encode(img_bytes).decode("utf-8")
            suffix = uploaded_img.name.split(".")[-1].lower()
            mime = "image/jpeg" if suffix in ("jpg", "jpeg") else "image/png"
            with st.spinner("正在识别图片中的题目..."):
                try:
                    ocr_result = chat_with_image(
                        model=OCR_MODEL, image_b64=img_b64, mime_type=mime,
                        prompt="""请识别图片中所有题目和学生解题步骤。
每道题按以下格式输出，题目之间用---分隔：
题目：[题目内容]
步骤：[学生写的解题步骤]
---
如果某题没有解题步骤，步骤写"未作答"。只输出题目内容，不要其他说明。"""
                    )
                    st.session_state["batch_input"] = ocr_result
                    st.success("识别完成！已自动填入下方输入框。")
                except Exception as e:
                    st.error(f"识别失败：{e}")

batch_text = st.text_area("粘贴题目（多题用 --- 分隔）",
                          value=st.session_state.get("batch_input", ""),
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

    # 解析每个 block 的题目和步骤
    parsed = []
    for idx, block in enumerate(blocks):
        question, steps = "", ""
        for line in block.split("\n"):
            line = line.strip()
            if line.startswith("题目：") or line.startswith("题目:"):
                question = line[3:].strip()
            elif line.startswith("步骤：") or line.startswith("步骤:"):
                steps = line[3:].strip()
        if not question and not steps:
            lines = [l.strip() for l in block.split("\n") if l.strip()]
            if lines:
                question = lines[0]
                steps = " ".join(lines[1:]) if len(lines) > 1 else "（未填写步骤）"
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
    st.dataframe(df[["题号", "题目", "错因", "题型", "状态"]], use_container_width=True)

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
                    st.markdown(f"**第{r['题号']}题** {wrong_mark} · 错因：`{r['错因']}`")
                    st.caption(r["题目"])
                with col2:
                    st.markdown(f"**{r['状态']}**")
                st.write(r["反馈"])

    csv = df.drop(columns=["是否有误"], errors="ignore").to_csv(index=False).encode("utf-8-sig")
    st.download_button("⬇️ 导出批量分析结果", data=csv,
                       file_name="batch_analysis.csv", mime="text/csv")
