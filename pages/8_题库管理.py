import os
import json
import base64
import streamlit as st
from dotenv import load_dotenv
from db import (init_question_bank, add_question, get_all_questions,
                delete_question, count_questions)
from llm_client import chat_with_image, chat_with_images
from ui import icon_title

load_dotenv()

if not st.session_state.get("logged_in"):
    st.warning("请先登录")
    st.stop()
if st.session_state.get("user", {}).get("role") != "teacher":
    st.warning("仅教师可管理题库")
    st.stop()

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
st.caption("录入题目、答案与评分细则，分析时自动检索并按点给分。")

uploaded_by = st.session_state.get("user", {}).get("username", "teacher")
total = count_questions()

col1, col2, col3 = st.columns(3)
col1.metric("题库总量", f"{total} 题")
col2.metric("支持功能", "按点给分")
col3.metric("检索方式", "模糊匹配")

st.divider()


def criteria_editor(key_prefix: str, init_criteria: list = None):
    """评分细则编辑器，返回 (criteria_list, total_points)"""
    if f"{key_prefix}_criteria" not in st.session_state:
        st.session_state[f"{key_prefix}_criteria"] = init_criteria or []

    criteria = st.session_state[f"{key_prefix}_criteria"]

    st.markdown("**📊 评分细则（可选，适用于应用题/阅读理解等主观题）**")
    st.caption("每行填一个得分点和对应分值，模型会逐点判断学生是否得分")

    updated = []
    for i, item in enumerate(criteria):
        c1, c2, c3 = st.columns([5, 1, 0.5])
        with c1:
            crit = st.text_input(f"得分点 {i+1}", value=item.get("criterion", ""),
                                 key=f"{key_prefix}_crit_{i}", label_visibility="collapsed",
                                 placeholder=f"得分点{i+1}，如：列式正确 / 答出主旨大意")
        with c2:
            pts = st.number_input(f"分值{i+1}", min_value=0, max_value=20,
                                  value=item.get("points", 1),
                                  key=f"{key_prefix}_pts_{i}", label_visibility="collapsed")
        with c3:
            if st.button("✕", key=f"{key_prefix}_del_{i}"):
                criteria.pop(i)
                st.rerun()
        if crit.strip():
            updated.append({"criterion": crit.strip(), "points": int(pts)})

    st.session_state[f"{key_prefix}_criteria"] = updated

    if st.button("＋ 添加得分点", key=f"{key_prefix}_add"):
        st.session_state[f"{key_prefix}_criteria"].append({"criterion": "", "points": 1})
        st.rerun()

    total_pts = sum(c["points"] for c in updated)
    if updated:
        st.caption(f"共 **{total_pts}** 分，{len(updated)} 个得分点")
    return updated, total_pts


tab1, tab2 = st.tabs(["➕ 录入题目", "📚 题库列表"])

# ══════════════════════════════════════════════════════
# Tab1: 录入题目
# ══════════════════════════════════════════════════════
with tab1:
    input_mode = st.radio("录入方式", ["✏️ 手动输入", "📷 拍照/上传图片（OCR识别）"],
                          horizontal=True, key="input_mode")

    col_s, col_g = st.columns(2)
    with col_s:
        subject = st.selectbox("学科", SUBJECTS, key="qb_subject")
    with col_g:
        grade = st.selectbox("年级", GRADES, key="qb_grade")
    source = st.text_input("来源（如：人教版八年级数学上册期中卷）", key="qb_source")

    if input_mode == "✏️ 手动输入":
        question_text = st.text_area("题目内容（含题干和选项）", height=120, key="qb_q",
                                     placeholder="例：小明买了3本书，每本12元，一共花了多少钱？")
        correct_answer = st.text_area("参考答案（含解题过程）", height=100, key="qb_a",
                                      placeholder="例：12×3=36（元），答：一共花了36元。")

        criteria, total_pts = criteria_editor("manual")

        if st.button("✅ 录入到题库", type="primary", key="btn_add_single"):
            if not question_text.strip() or not correct_answer.strip():
                st.warning("题目和答案不能为空")
            else:
                add_question(subject, grade, source,
                             question_text.strip(), correct_answer.strip(),
                             total_points=total_pts,
                             scoring_criteria=criteria,
                             uploaded_by=uploaded_by)
                st.success(f"✅ 录入成功！{'（含 ' + str(len(criteria)) + ' 个评分细则，共 ' + str(total_pts) + ' 分）' if criteria else ''}")
                st.session_state["manual_criteria"] = []
                st.rerun()

    else:
        ocr_label = st.selectbox("识别模型", list(VISION_MODELS.keys()), key="qb_ocr_model")
        OCR_MODEL = VISION_MODELS[ocr_label]

        ocr_mode = st.radio(
            "图片模式",
            ["📄 单图（题目与答案在同一张图）",
             "📄+📄 双图（题目页 + 答案页分开）"],
            horizontal=True, key="qb_ocr_mode"
        )

        def _compress(raw_bytes):
            from PIL import Image
            import io
            img = Image.open(io.BytesIO(raw_bytes)).convert("RGB")
            w, h = img.size
            if max(w, h) > 1600:
                ratio = 1600 / max(w, h)
                img = img.resize((int(w * ratio), int(h * ratio)), Image.LANCZOS)
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=82)
            return base64.b64encode(buf.getvalue()).decode(), "image/jpeg"

        _SINGLE_PROMPT = f"""识别图片中所有题目、参考答案和评分细则。学科：{subject}。

严格输出JSON数组：
[
  {{
    "题目": "完整题目内容",
    "答案": "参考答案和解析过程",
    "评分细则": [
      {{"criterion": "得分点描述", "points": 分值}}
    ],
    "总分": 整数
  }}
]

若没有明确评分细则，"评分细则"填[]，"总分"填0。只输出JSON。"""

        _DUAL_PROMPT = f"""图一是题目页，图二是答案页。学科：{subject}。

请完成以下工作：
1. 从图一识别所有题目（含题号、完整题干和选项）
2. 从图二识别对应答案和评分细则
3. 按题号将题目与答案一一配对

严格输出JSON数组：
[
  {{
    "题目": "完整题目内容（来自图一）",
    "答案": "参考答案和解析过程（来自图二）",
    "评分细则": [
      {{"criterion": "得分点描述", "points": 分值}}
    ],
    "总分": 整数
  }}
]

若答案页没有明确评分细则，"评分细则"填[]，"总分"填0。只输出JSON。"""

        # ── 单图模式 ──────────────────────────────────────
        if ocr_mode == "📄 单图（题目与答案在同一张图）":
            uploaded_file = st.file_uploader("上传图片（JPG/PNG）", type=["jpg", "jpeg", "png"], key="qb_img")

            if uploaded_file:
                img_bytes = uploaded_file.read()
                st.image(img_bytes, use_container_width=True)

                if st.button("🔍 OCR识别题目、答案和评分细则", type="primary", key="btn_qb_ocr"):
                    with st.spinner("正在识别…"):
                        try:
                            img_b64, mime = _compress(img_bytes)
                            raw = chat_with_image(image_b64=img_b64, mime_type=mime,
                                                  model=OCR_MODEL, prompt=_SINGLE_PROMPT)
                            raw = raw.strip()
                            match = __import__("re").search(r"\[[\s\S]*\]", raw)
                            items = json.loads(match.group() if match else raw)
                            st.session_state["qb_ocr_items"] = items
                            st.success(f"识别完成，共 {len(items)} 道题，请确认后录入")
                        except Exception as e:
                            st.error(f"识别失败：{e}")

        # ── 双图模式 ──────────────────────────────────────
        else:
            st.caption("分别上传题目页和答案页，AI 自动按题号配对合并")
            col_q, col_a = st.columns(2)
            with col_q:
                file_q = st.file_uploader("📄 题目页图片", type=["jpg", "jpeg", "png"], key="qb_img_q")
                if file_q:
                    st.image(file_q.read(), use_container_width=True)
                    file_q.seek(0)
            with col_a:
                file_a = st.file_uploader("📄 答案页图片", type=["jpg", "jpeg", "png"], key="qb_img_a")
                if file_a:
                    st.image(file_a.read(), use_container_width=True)
                    file_a.seek(0)

            if file_q and file_a:
                if st.button("🔍 双图识别并配对题目与答案", type="primary", key="btn_qb_dual_ocr"):
                    with st.spinner("正在识别并配对，请稍候（两张图，可能需要30秒）…"):
                        try:
                            b64_q, mime_q = _compress(file_q.read())
                            b64_a, mime_a = _compress(file_a.read())
                            images = [{"b64": b64_q, "mime": mime_q},
                                      {"b64": b64_a, "mime": mime_a}]
                            raw = chat_with_images(images=images, model=OCR_MODEL, prompt=_DUAL_PROMPT)
                            raw = raw.strip()
                            match = __import__("re").search(r"\[[\s\S]*\]", raw)
                            items = json.loads(match.group() if match else raw)
                            st.session_state["qb_ocr_items"] = items
                            st.success(f"识别完成，共配对 {len(items)} 道题，请逐题确认后录入")
                        except Exception as e:
                            st.error(f"识别失败：{e}")

        if st.session_state.get("qb_ocr_items"):
            items = st.session_state["qb_ocr_items"]
            for i, item in enumerate(items):
                with st.expander(f"第 {i+1} 题（点击编辑）", expanded=True):
                    q_val = st.text_area("题目", value=item.get("题目", ""),
                                         key=f"ocr_q_{i}", height=100)
                    a_val = st.text_area("答案", value=item.get("答案", ""),
                                         key=f"ocr_a_{i}", height=80)
                    items[i]["题目"] = q_val
                    items[i]["答案"] = a_val

                    # 显示并可编辑评分细则
                    raw_criteria = item.get("评分细则", [])
                    if f"ocr_criteria_{i}" not in st.session_state:
                        st.session_state[f"ocr_criteria_{i}"] = raw_criteria
                    ocr_criteria, ocr_pts = criteria_editor(f"ocr_{i}",
                                                            st.session_state[f"ocr_criteria_{i}"])
                    items[i]["评分细则"] = ocr_criteria
                    items[i]["总分"] = ocr_pts

            if st.button("✅ 全部录入题库", type="primary", key="btn_ocr_import"):
                ok = 0
                for item in items:
                    q = item.get("题目", "").strip()
                    a = item.get("答案", "").strip()
                    if q and a:
                        add_question(subject, grade, source, q, a,
                                     total_points=item.get("总分", 0),
                                     scoring_criteria=item.get("评分细则", []),
                                     uploaded_by=uploaded_by)
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
        keyword = st.text_input("关键词搜索", placeholder="题目关键词...", key="qb_keyword")

    questions = get_all_questions(
        subject=None if filter_subject == "全部" else filter_subject,
        keyword=keyword or None
    )
    st.markdown(f"共 **{len(questions)}** 条记录")

    if not questions:
        st.info("题库暂无数据，请先在「录入题目」标签页添加题目。")
    else:
        for q in questions:
            with st.container(border=True):
                col_info, col_del = st.columns([5, 1])
                with col_info:
                    pts_badge = f" · **{q['total_points']}分**" if q.get("total_points") else ""
                    st.markdown(
                        f"**[{q['subject']}·{q['grade']}]**{pts_badge} {q['source'] or '无来源'} "
                        f"<span style='color:#9CA3C0;font-size:0.8rem;'>· {str(q['created_at'])[:10]}</span>",
                        unsafe_allow_html=True)
                    with st.expander("查看题目、答案与评分细则"):
                        st.markdown(f"**📝 题目：**\n\n{q['question_text']}")
                        st.markdown(f"**✅ 答案：**\n\n{q['correct_answer']}")
                        try:
                            criteria = json.loads(q.get("scoring_criteria") or "[]")
                        except Exception:
                            criteria = []
                        if criteria:
                            st.markdown("**📊 评分细则：**")
                            for c in criteria:
                                st.markdown(f"- {c['criterion']}（**{c['points']}分**）")
                with col_del:
                    if st.button("🗑️", key=f"del_{q['id']}", help="删除"):
                        delete_question(q["id"])
                        st.rerun()

        import pandas as pd
        df = pd.DataFrame(questions)
        df["scoring_criteria"] = df["scoring_criteria"].fillna("[]")
        export_cols = ["subject", "grade", "source", "question_text",
                       "correct_answer", "total_points", "scoring_criteria"]
        df = df[[c for c in export_cols if c in df.columns]]
        df.columns = ["学科", "年级", "来源", "题目", "答案", "总分", "评分细则"][:len(df.columns)]
        csv = df.to_csv(index=False).encode("utf-8-sig")
        st.download_button("⬇️ 导出题库CSV", data=csv, file_name="题库.csv", mime="text/csv")
