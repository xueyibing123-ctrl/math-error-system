import os
import json
import base64
import streamlit as st
import pandas as pd
import plotly.express as px
from dotenv import load_dotenv
from llm_client import chat, chat_with_image
from db import save_record, count_same_error, upsert_alert
from ui import icon_title, icon_text

load_dotenv()
MODEL = os.getenv("DASHSCOPE_MODEL", "qwen-plus")

# 登录守卫
if not st.session_state.get("logged_in"):
    st.warning("请先登录")
    st.stop()

DRILL_THRESHOLD = 3

SYSTEM_PROMPT = """
你是小学数学错因分析系统。你必须严格输出合法JSON，不要输出任何额外文本。

错因标签体系（只允许从中选择）：
A1 数字抄写错误 / A2 计算过程错误 / A3 基础技能薄弱
B1 单位一/关键概念识别错误 / B2 运算类型误判 / B3 变式迁移失败
C1 综合结构理解困难 / C2 畏难情绪放弃 / C3 抽象关系建模能力不足

请输出：
{
  "题型判断": "一句话",
  "错因标签": ["标签1"],
  "判断理由": ["理由1"],
  "建议干预策略": ["策略1"],
  "温和反馈": "给学生的引导文字（50字以内，批量模式简短即可）"
}
""".strip()


def safe_json_loads(s: str):
    if not s:
        raise ValueError("empty")
    s = s.strip()
    try:
        return json.loads(s)
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


icon_title("assets/icons/批量分析.svg", "批量分析")
st.caption("一次粘贴多道题目，AI批量识别错因并写入错题本。")

user = st.session_state.get("user", {})
student_id = user.get("username", "unknown")

icon_text("assets/icons/pencil-line.svg", "输入格式说明",size=22)
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
                                    type=["jpg","jpeg","png"], key="batch_img")
    if uploaded_img:
        st.image(uploaded_img, width=400)
        if st.button("识别图片中的所有题目", key="btn_batch_ocr"):
            img_bytes = uploaded_img.read()
            img_b64 = base64.b64encode(img_bytes).decode("utf-8")
            suffix = uploaded_img.name.split(".")[-1].lower()
            mime = "image/jpeg" if suffix in ("jpg","jpeg") else "image/png"
            with st.spinner("正在识别图片中的题目..."):
                try:
                    ocr_result = chat_with_image(
                        model="qwen-vl-plus", image_b64=img_b64, mime_type=mime,
                        prompt="""请识别图片中所有数学题目和学生解题步骤。
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

    st.info(f"共识别到 {len(blocks)} 道题，开始分析...")
    results = []
    progress = st.progress(0)
    status_text = st.empty()

    for idx, block in enumerate(blocks):
        status_text.text(f"正在分析第 {idx+1}/{len(blocks)} 题...")
        progress.progress((idx + 1) / len(blocks))

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

        if not question:
            results.append({"题号": idx+1, "题目": "(格式错误，跳过)", "步骤": "",
                            "错因": "UNKNOWN", "题型": "-", "反馈": "-", "状态": "❌ 跳过"})
            continue

        user_prompt = f"题目：\n{question}\n\n学生解题步骤：\n{steps}"
        try:
            result_raw = chat(model=MODEL, system=SYSTEM_PROMPT, user=user_prompt, temperature=0.2)
            data = safe_json_loads(result_raw)
            tags = data.get("错因标签", [])
            main_error = tags[0] if isinstance(tags, list) and tags else "UNKNOWN"

            save_record(student_id, question, steps, main_error, data.get("温和反馈", ""))
            error_count = count_same_error(main_error)
            if main_error != "UNKNOWN" and error_count >= DRILL_THRESHOLD:
                upsert_alert(student_id=student_id, error_code=main_error,
                             error_count=error_count, threshold=DRILL_THRESHOLD)

            results.append({
                "题号": idx+1,
                "题目": question[:40] + "…" if len(question) > 40 else question,
                "步骤": steps[:30] + "…" if len(steps) > 30 else steps,
                "错因": main_error, "题型": data.get("题型判断", "-"),
                "反馈": data.get("温和反馈", "-"), "状态": "✅ 完成"
            })
        except Exception as e:
            results.append({
                "题号": idx+1,
                "题目": question[:40] + "…" if len(question) > 40 else question,
                "步骤": steps[:30] + "…" if len(steps) > 30 else steps,
                "错因": "UNKNOWN", "题型": "-", "反馈": f"分析失败：{e}", "状态": "❌ 失败"
            })

    progress.empty()
    status_text.empty()
    st.session_state["batch_results"] = results
    ok = len([r for r in results if r["状态"] == "✅ 完成"])
    fail = len(results) - ok
    st.success(f"✅ 批量分析完成！{ok} 道成功，{fail} 道失败/跳过。")

if st.session_state.get("batch_results"):
    results = st.session_state["batch_results"]
    st.divider()
    st.subheader("📊 分析结果")
    df = pd.DataFrame(results)
    st.dataframe(df[["题号","题目","错因","题型","状态"]], use_container_width=True)

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
            fig.update_layout(height=250, margin=dict(t=10,b=10,l=10,r=10))
            st.plotly_chart(fig, use_container_width=True)

    st.divider()
    st.subheader("💬 详细反馈")
    for r in results:
        if r["状态"] == "✅ 完成":
            with st.container(border=True):
                col1, col2 = st.columns([3, 1])
                with col1:
                    st.markdown(f"**第{r['题号']}题** · 错因：`{r['错因']}`")
                    st.caption(r["题目"])
                with col2:
                    st.markdown(f"**{r['状态']}**")
                st.write(r["反馈"])

    csv = df.to_csv(index=False).encode("utf-8-sig")
    st.download_button("⬇️ 导出批量分析结果", data=csv,
                       file_name="batch_analysis.csv", mime="text/csv")