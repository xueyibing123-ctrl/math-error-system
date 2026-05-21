import os
import json
import base64
import streamlit as st
from dotenv import load_dotenv
from db import (init_question_bank, add_question, get_all_questions,
                delete_question, count_questions, backfill_embeddings)
from llm_client import chat, chat_with_image, chat_with_images
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
    "Doubao-1.5-Vision-Pro（豆包视觉）": "doubao-1-5-vision-pro-32k-250115",
    "GLM-4.6V（智谱视觉）": "glm-4.6v",
    "GLM-4.1V-Thinking（智谱·深度思考）": "glm-4.1v-thinking",
}

TEXT_MODELS = {
    "qwen-max（通义·最强）": "qwen-max",
    "qwen-plus（通义·均衡）": "qwen-plus",
    "DeepSeek-V4-Pro（DeepSeek·旗舰）": "deepseek-v4-pro",
    "DeepSeek-V4-Flash（DeepSeek·快速）": "deepseek-v4-flash",
    "Doubao-Seed-2.0-Pro（豆包·旗舰）": "doubao-seed-2.0-pro",
    "GLM-4-Plus（智谱·高精度）": "glm-4-plus",
    "GLM-4-Air（智谱·均衡）": "glm-4-air",
    "GLM-4-Flash（智谱·免费快速）": "glm-4-flash",
}

icon_title("assets/icons/批量分析.svg", "题库管理")
st.caption("录入题目、答案与评分细则，分析时自动检索并按点给分。")

uploaded_by = st.session_state.get("user", {}).get("username", "teacher")

# ── 顶部筛选器（学科 + 年级，影响统计和列表）────────────
f_col1, f_col2, f_col3 = st.columns([2, 2, 4])
with f_col1:
    filter_subject = st.selectbox(
        "学科筛选", ["全部"] + SUBJECTS, key="qb_filter_subject"
    )
with f_col2:
    filter_grade = st.selectbox(
        "年级筛选", ["全部"] + GRADES, key="qb_filter_grade"
    )
with f_col3:
    st.write("")  # 占位

_fs = filter_subject if filter_subject != "全部" else None
_fg = filter_grade if filter_grade != "全部" else None

total_all  = count_questions()
try:
    total_filt = count_questions(subject=_fs, grade=_fg)
except TypeError:
    # 兼容旧版 db.py（不支持 grade 参数时降级）
    total_filt = count_questions(subject=_fs)

# ── 统计指标（根据筛选实时更新）──────────────────────────
col1, col2, col3, col4 = st.columns(4)
if _fs or _fg:
    label = " · ".join(x for x in [filter_subject, filter_grade] if x != "全部")
    col1.metric("筛选结果", f"{total_filt} 题", delta=f"共{total_all}题", delta_color="off")
    col1.caption(f"📌 {label}")
else:
    col1.metric("题库总量", f"{total_all} 题")
col2.metric("支持功能", "按点给分")
with col3:
    st.markdown("**检索方式**")
    st.markdown("语义向量 · 文字匹配 · 图像指纹")
with col4:
    if st.button("⚡ 补全题目向量", help="为尚未生成语义向量的题目批量生成，提升检索准确率"):
        try:
            with st.spinner("正在生成向量，请稍候…"):
                ok, fail = backfill_embeddings(limit=50)
            if ok + fail == 0:
                st.info("所有题目已有向量，无需补全")
            elif fail == 0:
                st.success(f"✅ 完成：{ok} 道生成成功")
            else:
                st.warning(f"完成：{ok} 道成功，{fail} 道失败（可能是 API Key 未配置或题库无文字）")
        except Exception as _e:
            st.error(f"补全向量失败：{_e}")

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
        _subj_idx = SUBJECTS.index(_fs) if _fs and _fs in SUBJECTS else 0
        subject = st.selectbox("学科", SUBJECTS, index=_subj_idx, key="qb_subject")
    with col_g:
        _grade_idx = GRADES.index(_fg) if _fg and _fg in GRADES else 0
        grade = st.selectbox("年级", GRADES, index=_grade_idx, key="qb_grade")
    source = st.text_input("来源（如：人教版八年级数学上册期中卷）", key="qb_source")

    if input_mode == "✏️ 手动输入":
        question_text = st.text_area("题目内容（含题干和选项）", height=120, key="qb_q",
                                     placeholder="例：小明买了3本书，每本12元，一共花了多少钱？")
        correct_answer = st.text_area("参考答案（含解题过程）", height=100, key="qb_a",
                                      placeholder="例：12×3=36（元），答：一共花了36元。")
        manual_img = st.file_uploader("题目图片（可选，上传后存为截图并建立图像指纹）",
                                      type=["jpg", "jpeg", "png"], key="qb_manual_img")

        criteria, total_pts = criteria_editor("manual")

        if st.button("✅ 录入到题库", type="primary", key="btn_add_single"):
            if not question_text.strip() or not correct_answer.strip():
                st.warning("题目和答案不能为空")
            else:
                img_bytes = manual_img.read() if manual_img else None
                add_question(subject, grade, source,
                             question_text.strip(), correct_answer.strip(),
                             total_points=total_pts,
                             scoring_criteria=criteria,
                             uploaded_by=uploaded_by,
                             image_bytes=img_bytes)
                st.success(f"✅ 录入成功！{'（含 ' + str(len(criteria)) + ' 个评分细则，共 ' + str(total_pts) + ' 分）' if criteria else ''}")
                st.session_state["manual_criteria"] = []
                st.rerun()

    else:
        st.info("📌 两步识别：第①步视觉模型只读取文字，第②步文字模型负责理解配对，准确率更高")
        col_m1, col_m2 = st.columns(2)
        with col_m1:
            ocr_label = st.selectbox("① 视觉模型（读文字）", list(VISION_MODELS.keys()), key="qb_ocr_model")
            OCR_MODEL = VISION_MODELS[ocr_label]
        with col_m2:
            txt_label = st.selectbox("② 文字模型（配对整理）", list(TEXT_MODELS.keys()), key="qb_txt_model")
            TXT_MODEL = TEXT_MODELS[txt_label]

        ocr_mode = st.radio(
            "图片模式",
            ["📄 题目答案同页（可多张）",
             "📄+📄 题目页 + 答案页分开（各可多张）"],
            horizontal=True, key="qb_ocr_mode"
        )

        def _compress(raw_bytes):
            from PIL import Image
            import io
            img = Image.open(io.BytesIO(raw_bytes)).convert("RGB")
            w, h = img.size
            if max(w, h) > 2000:
                ratio = 2000 / max(w, h)
                img = img.resize((int(w * ratio), int(h * ratio)), Image.LANCZOS)
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=88)
            return base64.b64encode(buf.getvalue()).decode(), "image/jpeg"

        OCR_PROMPT = (
            "请将图片中所有文字原样识别输出，不要遗漏任何内容，"
            "包括题号、题目、选项、答案、分值标注（如括号内的分数）、评分说明等。"
            "保持原始排版顺序，每道题之间空一行。"
        )

        _SCORING_RULE = """
评分细则生成规则：
- 若题目/答案中有明确分值（如"(3分)"、"共5分"），必须填入"总分"
- 若总分>0，请根据答案内容将分值拆分为合理的得分点，例如：
  * 总分2分 → [{"criterion":"方法正确","points":1},{"criterion":"结果正确","points":1}]
  * 总分3分 → 可按步骤或要点拆分
  * 总分4分 → 通常2个得分点各2分，或4个各1分
- 若确实无法拆分（如选择题），"评分细则"填[]
"""

        _JSON_SCHEMA = """[
  {
    "题号": "1",
    "题目": "完整题目内容（含题号、题干、选项）",
    "答案": "参考答案和解析过程",
    "评分细则": [{"criterion": "得分点描述", "points": 分值}],
    "总分": 整数
  }
]"""

        def _do_ocr_single(raw_bytes_list, label="", progress_cb=None):
            """并行 OCR 多张图片（接受 bytes 列表，线程安全），返回按页序拼接的原始文字。"""
            from concurrent.futures import ThreadPoolExecutor, as_completed

            # 压缩在主线程完成，线程只做网络请求
            compressed = [(i, _compress(raw)) for i, raw in enumerate(raw_bytes_list)]

            def _ocr_one(args):
                i, (b64, mime) = args
                t = chat_with_image(image_b64=b64, mime_type=mime,
                                    model=OCR_MODEL, prompt=OCR_PROMPT)
                return i, t.strip()

            results = [None] * len(raw_bytes_list)
            done = 0
            with ThreadPoolExecutor(max_workers=5) as ex:
                futs = {ex.submit(_ocr_one, item): item[0] for item in compressed}
                for f in as_completed(futs):
                    i, text = f.result()
                    results[i] = text
                    done += 1
                    if progress_cb:
                        progress_cb(done, len(raw_bytes_list), label)

            return "\n\n".join(f"--- {label}第{i+1}页 ---\n{t}" for i, t in enumerate(results))

        _FILTER_RULE = """
提取规则：
- 只提取题目正文和对应答案，忽略以下内容：
  页眉、页脚、页码、"仅供教师"/"教师用书"/"附加题"等标注、装订线、印刷日期、版权声明
- 大题标题（如"一、选择题"）不单独作为一道题，并入其下各小题
- 竖式计算题（如加减法竖式）识别为"计算：___"格式，答案填竖式结果
"""

        def _structure_single(raw_text):
            sys_p = "你是题目整理助手，严格输出合法JSON，不输出任何其他文字。"
            usr_p = f"""以下是试卷的原始识别文字（学科：{subject}）：

{raw_text}

请提取所有题目和对应答案，整理成JSON数组。
重要：不能遗漏任何一道题，若题号不连续请仔细检查原文再确认。
{_FILTER_RULE}
{_SCORING_RULE}
输出格式：
{_JSON_SCHEMA}
只输出JSON数组，不输出其他文字。"""
            return chat(model=TXT_MODEL, system=sys_p, user=usr_p, temperature=0.1)

        def _structure_dual(q_text, a_text):
            sys_p = "你是题目整理助手，严格输出合法JSON，不输出任何其他文字。"
            usr_p = f"""学科：{subject}。以下是分别从题目页和答案页识别的原始文字。

【题目页原始文字】
{q_text}

【答案页原始文字】
{a_text}

请按题号将题目与答案一一配对。
重要：不能遗漏任何一道题，若题号不连续请仔细检查原文再确认。
{_FILTER_RULE}
{_SCORING_RULE}
输出格式：
{_JSON_SCHEMA}
只输出JSON数组，不输出其他文字。"""
            return chat(model=TXT_MODEL, system=sys_p, user=usr_p, temperature=0.1)

        def _parse_items(raw):
            raw = raw.strip()
            m = __import__("re").search(r"\[[\s\S]*\]", raw)
            return json.loads(m.group() if m else raw)

        def _check_missing(items):
            """检测题号是否有缺失，返回缺失题号列表。"""
            import re
            nums = []
            for item in items:
                # 优先用题号字段，其次从题目文字里提取
                n = item.get("题号", "")
                if not n:
                    m = re.match(r"^(\d+)[.、．\s]", item.get("题目", "").strip())
                    n = m.group(1) if m else ""
                if n and str(n).isdigit():
                    nums.append(int(n))
            if len(nums) < 2:
                return []
            full = set(range(min(nums), max(nums) + 1))
            return sorted(full - set(nums))

        # ── 同页模式 ──────────────────────────────────────
        if ocr_mode == "📄 题目答案同页（可多张）":
            st.caption("题目和答案在同一页，可一次上传多张（如试卷第1页、第2页）")
            files = st.file_uploader("上传图片（可多选）", type=["jpg", "jpeg", "png"],
                                     key="qb_img", accept_multiple_files=True)
            if files:
                cols = st.columns(min(len(files), 3))
                for i, f in enumerate(files):
                    cols[i % 3].image(f.read(), use_container_width=True)
                    f.seek(0)

                if st.button("🔍 开始识别", type="primary", key="btn_qb_ocr"):
                    try:
                        n = len(files)
                        # 主线程预读所有字节（线程安全）
                        all_raw = [f.read() for f in files]
                        st.session_state["qb_ocr_img_bytes"] = all_raw[0] if all_raw else None

                        prog = st.progress(0, text=f"第①步：并行识别 {n} 张图片…")

                        def _cb(done, total, _label):
                            prog.progress(int(done / total * 55),
                                          text=f"第①步：已完成 {done}/{total} 张…")

                        raw_text = _do_ocr_single(all_raw, progress_cb=_cb)
                        prog.progress(60, text="第②步：文字模型整理题目与答案…")
                        raw_json = _structure_single(raw_text)
                        prog.progress(95, text="解析结果…")
                        new_items = _parse_items(raw_json)
                        prog.empty()
                        existing = st.session_state.get("qb_ocr_items") or []
                        st.session_state["qb_ocr_items"] = existing + new_items
                        missing = _check_missing(new_items)
                        st.success(f"识别完成，新增 {len(new_items)} 道题（已累计 {len(existing) + len(new_items)} 道），可继续上传或直接录入")
                        if missing:
                            st.warning(f"⚠️ 检测到可能缺少第 **{', '.join(map(str, missing))}** 题，请检查原图并点击「＋ 手动补充一题」补录")
                    except Exception as e:
                        import traceback
                        st.error(f"识别失败：{e or repr(e)}\n\n```\n{traceback.format_exc()}\n```")

        # ── 分页模式 ──────────────────────────────────────
        else:
            st.caption("题目和答案分开，各自可上传多张（如题目2页 + 答案2页）")
            col_q, col_a = st.columns(2)
            with col_q:
                files_q = st.file_uploader("📄 题目页（可多选）", type=["jpg", "jpeg", "png"],
                                           key="qb_img_q", accept_multiple_files=True)
                if files_q:
                    for f in files_q:
                        st.image(f.read(), use_container_width=True)
                        f.seek(0)
            with col_a:
                files_a = st.file_uploader("📄 答案页（可多选）", type=["jpg", "jpeg", "png"],
                                           key="qb_img_a", accept_multiple_files=True)
                if files_a:
                    for f in files_a:
                        st.image(f.read(), use_container_width=True)
                        f.seek(0)

            if files_q and files_a:
                st.caption(f"已上传：题目页 {len(files_q)} 张 · 答案页 {len(files_a)} 张")
                if st.button("🔍 开始识别并配对", type="primary", key="btn_qb_dual_ocr"):
                    try:
                        nq, na = len(files_q), len(files_a)
                        total_imgs = nq + na
                        # 主线程预读所有字节（线程安全，避免跨线程读 UploadedFile）
                        q_raw = [f.read() for f in files_q]
                        a_raw = [f.read() for f in files_a]
                        st.session_state["qb_ocr_img_bytes"] = q_raw[0] if q_raw else None

                        prog = st.progress(0, text=f"第①步：并行识别全部 {total_imgs} 张图片…")

                        # 双线程 OCR，不传 progress_cb（后台线程不能调 Streamlit UI）
                        from concurrent.futures import ThreadPoolExecutor
                        with ThreadPoolExecutor(max_workers=2) as ex:
                            fq = ex.submit(_do_ocr_single, q_raw, "题目页")
                            fa = ex.submit(_do_ocr_single, a_raw, "答案页")
                            prog.progress(30, text="第①步：正在识别题目页…")
                            q_text = fq.result()
                            prog.progress(60, text="第①步：正在识别答案页…")
                            a_text = fa.result()

                        prog.progress(65, text="第②步：文字模型按题号配对…")
                        raw_json = _structure_dual(q_text, a_text)
                        prog.progress(95, text="解析结果…")
                        new_items = _parse_items(raw_json)
                        prog.empty()
                        existing = st.session_state.get("qb_ocr_items") or []
                        st.session_state["qb_ocr_items"] = existing + new_items
                        missing = _check_missing(new_items)
                        st.success(f"配对完成，新增 {len(new_items)} 道题（已累计 {len(existing) + len(new_items)} 道），可继续上传或直接录入")
                        if missing:
                            st.warning(f"⚠️ 检测到可能缺少第 **{', '.join(map(str, missing))}** 题，请检查原图并点击「＋ 手动补充一题」补录")
                    except Exception as e:
                        import traceback
                        st.error(f"识别失败：{e or repr(e)}\n\n```\n{traceback.format_exc()}\n```")

        if st.session_state.get("qb_ocr_items") is not None:
            items = st.session_state["qb_ocr_items"]

            # 顶部操作栏
            st.divider()
            hcol1, hcol2, hcol3 = st.columns([3, 1, 1])
            with hcol1:
                st.markdown(f"**📋 待录入题目：共 {len(items)} 道**（可继续上传图片追加）")
            with hcol2:
                if st.button("＋ 手动补充一题", key="btn_add_blank"):
                    st.session_state["qb_ocr_items"].append({"题目": "", "答案": "", "评分细则": [], "总分": 0})
                    st.rerun()
            with hcol3:
                if st.button("🗑️ 清空重来", key="btn_ocr_clear"):
                    st.session_state["qb_ocr_items"] = None
                    st.rerun()

            # 逐题编辑
            to_delete = None
            for i, item in enumerate(items):
                with st.expander(f"第 {i+1} 题{'（空题，请填写）' if not item.get('题目') else ''}", expanded=not item.get("题目")):
                    dcol, _ = st.columns([5, 1])
                    with _:
                        if st.button("删除", key=f"ocr_del_{i}"):
                            to_delete = i
                    q_val = st.text_area("题目", value=item.get("题目", ""),
                                         key=f"ocr_q_{i}", height=100)
                    a_val = st.text_area("答案", value=item.get("答案", ""),
                                         key=f"ocr_a_{i}", height=80)
                    items[i]["题目"] = q_val
                    items[i]["答案"] = a_val

                    raw_criteria = item.get("评分细则", [])
                    if f"ocr_criteria_{i}" not in st.session_state:
                        st.session_state[f"ocr_criteria_{i}"] = raw_criteria
                    ocr_criteria, ocr_pts = criteria_editor(f"ocr_{i}",
                                                            st.session_state[f"ocr_criteria_{i}"])
                    items[i]["评分细则"] = ocr_criteria
                    items[i]["总分"] = ocr_pts

            if to_delete is not None:
                st.session_state["qb_ocr_items"].pop(to_delete)
                st.rerun()

            st.divider()
            if st.button("✅ 全部录入题库", type="primary", key="btn_ocr_import"):
                ok = 0
                # 使用 OCR 时保存的代表图（题目页第一张）为所有题建立图像指纹
                ocr_img_bytes = st.session_state.get("qb_ocr_img_bytes")
                for item in items:
                    q = item.get("题目", "").strip()
                    a = item.get("答案", "").strip()
                    if q and a:
                        add_question(subject, grade, source, q, a,
                                     total_points=item.get("总分", 0),
                                     scoring_criteria=item.get("评分细则", []),
                                     uploaded_by=uploaded_by,
                                     image_bytes=ocr_img_bytes)
                        ok += 1
                st.success(f"✅ 成功录入 {ok} 道题目！{'（含图像指纹）' if ocr_img_bytes else ''}")
                st.session_state["qb_ocr_items"] = None
                st.session_state.pop("qb_ocr_img_bytes", None)
                st.rerun()

# ══════════════════════════════════════════════════════
# Tab2: 题库列表
# ══════════════════════════════════════════════════════
with tab2:
    # 顶部已有学科/年级筛选，这里只加关键词搜索
    if _fs or _fg:
        label_hint = " · ".join(x for x in [filter_subject, filter_grade] if x != "全部")
        st.caption(f"📌 当前筛选：{label_hint}（可在顶部修改）")
    keyword = st.text_input("关键词搜索", placeholder="题目内容、来源关键词…", key="qb_keyword")

    questions = get_all_questions(
        subject=_fs,
        grade=_fg,
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
                        if q.get("image_data"):
                            try:
                                img_col, _ = st.columns([1, 2])
                                img_col.image(
                                    base64.b64decode(q["image_data"]),
                                    caption="题目截图", use_container_width=True
                                )
                            except Exception:
                                pass
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
