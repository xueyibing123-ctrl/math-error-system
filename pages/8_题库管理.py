import os
import base64
import streamlit as st
from dotenv import load_dotenv
from db import init_question_bank, add_question, get_all_questions, delete_question, count_questions
from llm_client import chat_with_image
from ui import icon_title

load_dotenv()

# 登录守卫（仅教师可访问）
if not st.session_state.get("logged_in"):
    st.warning("请先登录")
    st.stop()
if st.session_state.get("user", {}).get("role") != "teacher":
    st.warning("仅教师可管理题库")
    st.stop()

# 初始化题库表
try:
    init_question_bank()
except Exception:
    pass

SUBJECTS = ["数学", "语文", "英语", "物理", "化学", "历史", "政治", "生物", "地理", "其他"]
GRADES = ["小学一年级", "小学二年级", "小学三年级", "小学四年级", "小学五年级", "小学六年级",
          "初一", "初二", "初三", "高一", "高二", "高三", "通用"]

VISION_MODELS = {
    "qwen-vl-plus（通义视觉·快速）": "qwen-vl-plus",
    "qwen-vl-max（通义视觉·高精度）": "qwen-vl-max",
    "GLM-4.6V（智谱视觉）": "glm-4.6v",
}

icon_title("assets/icons/批量分析.svg", "题库管理")
st.caption("教师上传教辅资料和答案，分析时自动检索提升准确率。")

uploaded_by = st.session_state.get("user", {}).get("username", "teacher")

# ── 统计信息 ─────────────────────────────────────────
total = count_questions()
col1, col2, col3 = st.columns(3)
col1.metric("题库总量", f"{total} 题")
col2.metric("覆盖功能", "错因分析 + 批量分析")
col3.metric("检索方式", "模糊匹配自动命中")

st.divider()

tab1, tab2 = st.tabs(["➕ 录入题目", "📚 题库列表"])

# ══════════════════════════════════════════════════════
# Tab1: 录入题目
# ══════════════════════════════════════════════════════
with tab1:
    st.markdown("### 录入方式")
    input_mode = st.radio("", ["✏️ 手动输入", "📷 拍照/上传图片（OCR识别）"], horizontal=True, key="input_mode")

    col_s, col_g = st.columns(2)
    with col_s:
        subject = st.selectbox("学科", SUBJECTS, key="qb_subject")
    with col_g:
        grade = st.selectbox("年级", GRADES, key="qb_grade")
    source = st.text_input("来源（教材/试卷名称，如：人教版八年级数学上册期中卷）", key="qb_source")

    if input_mode == "✏️ 手动输入":
        st.markdown("**单题录入**")
        question_text = st.text_area("题目内容（含选项）", height=120, key="qb_q")
        correct_answer = st.text_area("正确答案（含解题过程）", height=100, key="qb_a")
        if st.button("✅ 录入到题库", type="primary", key="btn_add_single"):
            if not question_text.strip() or not correct_answer.strip():
                st.warning("题目和答案不能为空")
            else:
                add_question(subject, grade, source, question_text.strip(),
                             correct_answer.strip(), uploaded_by)
                st.success("✅ 录入成功！")
                st.rerun()

        st.divider()
        st.markdown("**批量录入（多题用 `---` 分隔，每题格式：题目在前，答案用「答：」开头）**")
        st.code("题目：小明有5个苹果……\n答：5×3=15，答案是15个\n---\n题目：……\n答：……", language="text")
        batch_text = st.text_area("批量粘贴", height=200, key="qb_batch")
        if st.button("批量录入", key="btn_batch_add"):
            blocks = [b.strip() for b in batch_text.split("---") if b.strip()]
            ok, fail = 0, 0
            for block in blocks:
                lines = block.split("\n")
                q_lines, a_lines = [], []
                in_answer = False
                for line in lines:
                    stripped = line.strip()
                    if stripped.startswith("答：") or stripped.startswith("答:"):
                        in_answer = True
                        a_lines.append(stripped[2:].strip())
                    elif in_answer:
                        a_lines.append(stripped)
                    else:
                        q_lines.append(stripped)
                q = "\n".join(q_lines).replace("题目：", "").replace("题目:", "").strip()
                a = "\n".join(a_lines).strip()
                if q and a:
                    add_question(subject, grade, source, q, a, uploaded_by)
                    ok += 1
                else:
                    fail += 1
            st.success(f"✅ 批量录入完成：{ok} 题成功，{fail} 题格式有误跳过")
            if ok:
                st.rerun()

    else:
        st.markdown("**上传试卷/教辅图片，AI自动识别题目和答案**")
        ocr_model_label = st.selectbox("识别模型", list(VISION_MODELS.keys()), key="qb_ocr_model")
        OCR_MODEL = VISION_MODELS[ocr_model_label]

        uploaded_file = st.file_uploader("上传图片（支持JPG/PNG）", type=["jpg", "jpeg", "png"], key="qb_img")
        if uploaded_file:
            img_bytes = uploaded_file.read()
            st.image(img_bytes, use_container_width=True)

            if st.button("🔍 OCR识别并提取题目和答案", type="primary", key="btn_qb_ocr"):
                suffix = uploaded_file.name.split(".")[-1].lower()
                mime = "image/jpeg" if suffix in ("jpg", "jpeg") else "image/png"

                # 压缩图片
                from PIL import Image
                import io
                img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
                w, h = img.size
                if max(w, h) > 1600:
                    ratio = 1600 / max(w, h)
                    img = img.resize((int(w*ratio), int(h*ratio)), Image.LANCZOS)
                buf = io.BytesIO()
                img.save(buf, format="JPEG", quality=82)
                compressed = buf.getvalue()
                img_b64 = base64.b64encode(compressed).decode()

                with st.spinner("正在识别题目和答案…"):
                    try:
                        prompt = f"""请识别这张图片中的所有题目和对应的正确答案。学科：{subject}。

严格输出JSON数组，每道题一个对象：
[
  {{
    "题目": "完整题目内容（含选项）",
    "答案": "正确答案和解析过程"
  }}
]

只输出JSON，不要任何其他文字。"""
                        raw = chat_with_image(image_b64=img_b64, mime_type="image/jpeg",
                                              model=OCR_MODEL, prompt=prompt)
                        import json, re
                        # 解析JSON
                        raw = raw.strip()
                        match = re.search(r"\[[\s\S]*\]", raw)
                        if match:
                            items = json.loads(match.group())
                        else:
                            items = json.loads(raw)

                        st.session_state["qb_ocr_items"] = items
                        st.success(f"识别完成，共发现 {len(items)} 道题目，请确认后录入")
                    except Exception as e:
                        st.error(f"识别失败：{e}")

            # 展示识别结果并确认录入
            if st.session_state.get("qb_ocr_items"):
                items = st.session_state["qb_ocr_items"]
                st.markdown(f"**识别到 {len(items)} 道题，可逐题编辑后录入：**")
                for i, item in enumerate(items):
                    with st.expander(f"第 {i+1} 题", expanded=True):
                        q_val = st.text_area("题目", value=item.get("题目", ""), key=f"ocr_q_{i}", height=100)
                        a_val = st.text_area("答案", value=item.get("答案", ""), key=f"ocr_a_{i}", height=80)
                        items[i]["题目"] = q_val
                        items[i]["答案"] = a_val

                if st.button("✅ 全部录入题库", type="primary", key="btn_ocr_import"):
                    ok = 0
                    for item in items:
                        q = item.get("题目", "").strip()
                        a = item.get("答案", "").strip()
                        if q and a:
                            add_question(subject, grade, source, q, a, uploaded_by)
                            ok += 1
                    st.success(f"✅ 成功录入 {ok} 道题目！")
                    st.session_state["qb_ocr_items"] = None
                    st.rerun()

# ══════════════════════════════════════════════════════
# Tab2: 题库列表
# ══════════════════════════════════════════════════════
with tab2:
    col_f1, col_f2 = st.columns([1, 2])
    with col_f1:
        filter_subject = st.selectbox("筛选学科", ["全部"] + SUBJECTS, key="qb_filter_sub")
    with col_f2:
        keyword = st.text_input("关键词搜索", placeholder="输入题目关键词...", key="qb_keyword")

    subject_filter = None if filter_subject == "全部" else filter_subject
    questions = get_all_questions(subject=subject_filter, keyword=keyword or None, limit=200)

    st.markdown(f"共 **{len(questions)}** 条记录")

    if not questions:
        st.info("题库暂无数据，请先在「录入题目」标签页添加题目。")
    else:
        for q in questions:
            with st.container(border=True):
                col_info, col_del = st.columns([5, 1])
                with col_info:
                    st.markdown(f"**[{q['subject']}·{q['grade']}]** {q['source'] or '无来源'} "
                                f"<span style='color:#9CA3C0;font-size:0.8rem;'>by {q['uploaded_by']} · "
                                f"{str(q['created_at'])[:10]}</span>", unsafe_allow_html=True)
                    with st.expander("查看题目与答案"):
                        st.markdown(f"**📝 题目：**\n\n{q['question_text']}")
                        st.markdown(f"**✅ 答案：**\n\n{q['correct_answer']}")
                with col_del:
                    if st.button("🗑️ 删除", key=f"del_{q['id']}"):
                        delete_question(q["id"])
                        st.rerun()

    if questions:
        import pandas as pd
        df = pd.DataFrame(questions)[["subject", "grade", "source", "question_text", "correct_answer", "uploaded_by", "created_at"]]
        df.columns = ["学科", "年级", "来源", "题目", "答案", "录入人", "时间"]
        csv = df.to_csv(index=False).encode("utf-8-sig")
        st.download_button("⬇️ 导出题库CSV", data=csv, file_name="题库.csv", mime="text/csv")
