import time
import re
import base64
import streamlit as st
from concurrent.futures import ThreadPoolExecutor, as_completed
from difflib import SequenceMatcher
from llm_client import chat, chat_with_image
from ui import icon_title

if not st.session_state.get("logged_in"):
    st.warning("请先登录")
    st.stop()
if st.session_state.get("user", {}).get("role") != "teacher":
    st.warning("仅教师可使用此功能")
    st.stop()

icon_title("assets/icons/批量分析.svg", "模型评测")
st.caption("分别评测 OCR 识别能力 和 文字批改能力，找出最适合的模型组合。")

# ══════════════════════════════════════════════════════════
# 模型列表
# ══════════════════════════════════════════════════════════
VISION_MODELS = {
    "qwen-vl-plus（通义·快速）": "qwen-vl-plus",
    "qwen-vl-max（通义·高精度）": "qwen-vl-max",
    "doubao-1.5-vision-pro（豆包·旗舰）": "doubao-1-5-vision-pro-32k-250115",
    "doubao-1.5-vision-lite（豆包·轻量）": "doubao-1-5-vision-lite-32k-250115",
    "glm-4.6v（智谱·通用）": "glm-4.6v",
    "glm-4.1v-thinking（智谱·深度思考）": "glm-4.1v-thinking",
    "glm-ocr（智谱·专用OCR）": "glm-ocr",
}

TEXT_MODELS = {
    "qwen-max（通义·最强）": "qwen-max",
    "qwen-plus（通义·均衡）": "qwen-plus",
    "qwen-turbo（通义·快速）": "qwen-turbo",
    "deepseek-v4-pro（DeepSeek·旗舰）": "deepseek-v4-pro",
    "deepseek-v4-flash（DeepSeek·快速）": "deepseek-v4-flash",
    "deepseek-reasoner（DeepSeek·R1）": "deepseek-reasoner",
    "doubao-seed-2.0-pro（豆包·旗舰）": "doubao-seed-2.0-pro",
    "doubao-1.5-pro-256k（豆包·长文本）": "doubao-1-5-pro-256k-250115",
    "glm-4-plus（智谱·高精度）": "glm-4-plus",
    "glm-4-air（智谱·均衡）": "glm-4-air",
    "glm-4-flash（智谱·免费快速）": "glm-4-flash",
}

_DEFAULT_VISION = {"qwen-vl-plus", "doubao-1-5-vision-pro-32k-250115", "glm-4.6v"}
_DEFAULT_TEXT   = {"qwen-max", "deepseek-v4-pro", "doubao-seed-2.0-pro", "glm-4-flash"}

# ══════════════════════════════════════════════════════════
tab1, tab2 = st.tabs(["📷 OCR 识别测试", "✏️ 文字批改测试"])

# ──────────────────────────────────────────────────────────
# Tab1: OCR 测试
# ──────────────────────────────────────────────────────────
with tab1:
    st.markdown("### 📷 OCR 视觉模型评测")
    st.caption("上传作业图片并填写正确文字，测试各视觉模型的识别准确率。")

    # 模型选择
    st.markdown("**① 选择视觉模型**")
    ocr_selected: dict = {}
    vcols = st.columns(2)
    items = list(VISION_MODELS.items())
    half = (len(items) + 1) // 2
    for ci, chunk in enumerate([items[:half], items[half:]]):
        with vcols[ci]:
            for label, mid in chunk:
                if st.checkbox(label, key=f"ocr_eval_{mid}", value=(mid in _DEFAULT_VISION)):
                    ocr_selected[label] = mid

    st.divider()

    # 上传测试图片
    st.markdown("**② 上传测试图片并填写标准答案**")
    st.caption("建议上传 2-3 张有代表性的作业图片（含文字、数字、符号），填写图片中应识别出的完整文字。")

    if "ocr_test_cases" not in st.session_state:
        st.session_state["ocr_test_cases"] = [{"img": None, "ground_truth": "", "name": ""}]

    cases = st.session_state["ocr_test_cases"]

    to_delete = None
    for i, tc in enumerate(cases):
        with st.container(border=True):
            hc1, hc2 = st.columns([5, 1])
            with hc2:
                if len(cases) > 1 and st.button("删除", key=f"ocr_del_{i}"):
                    to_delete = i
            with hc1:
                st.markdown(f"**测试图片 {i+1}**")
            img_col, txt_col = st.columns([1, 1])
            with img_col:
                f = st.file_uploader(f"上传图片", type=["jpg","jpeg","png"],
                                     key=f"ocr_img_{i}", label_visibility="collapsed")
                if f:
                    cases[i]["img"] = f.read()
                    cases[i]["name"] = f.name
                    st.image(cases[i]["img"], use_container_width=True)
                elif cases[i].get("img"):
                    st.image(cases[i]["img"], use_container_width=True)
            with txt_col:
                cases[i]["ground_truth"] = st.text_area(
                    "图片中的标准文字（OCR 应识别出的内容）",
                    value=cases[i].get("ground_truth", ""),
                    height=150, key=f"ocr_gt_{i}",
                    placeholder="把图片里所有文字手动抄写在这里，用作评分基准"
                )

    if to_delete is not None:
        st.session_state["ocr_test_cases"].pop(to_delete)
        st.rerun()

    if st.button("＋ 再加一张测试图片", key="ocr_add_case"):
        st.session_state["ocr_test_cases"].append({"img": None, "ground_truth": "", "name": ""})
        st.rerun()

    st.divider()

    valid_cases = [c for c in cases if c.get("img") and c.get("ground_truth","").strip()]
    st.caption(f"已选 **{len(ocr_selected)}** 个模型 · **{len(valid_cases)}** 张有效测试图片")

    OCR_PROMPT = (
        "请将图片中所有文字原样识别输出，不要遗漏任何内容，"
        "包括题号、题目、选项、答案、数字、符号等。保持原始排版顺序。"
    )

    def _compress_for_eval(img_bytes):
        from PIL import Image
        import io
        img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
        w, h = img.size
        if max(w, h) > 2000:
            r = 2000 / max(w, h)
            img = img.resize((int(w*r), int(h*r)), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=88)
        return base64.b64encode(buf.getvalue()).decode(), "image/jpeg"

    if st.button("🚀 开始OCR评测", type="primary",
                 disabled=(not ocr_selected or not valid_cases)):
        if not ocr_selected:
            st.warning("请至少选择一个视觉模型")
            st.stop()
        if not valid_cases:
            st.warning("请上传图片并填写标准文字")
            st.stop()

        # 预压缩所有图片（主线程）
        compressed = [_compress_for_eval(c["img"]) for c in valid_cases]

        ocr_results: dict = {lbl: [None]*len(valid_cases) for lbl in ocr_selected}

        def _ocr_one(model_label, model_id, case_idx, b64, mime, ground_truth):
            start = time.time()
            try:
                text = chat_with_image(image_b64=b64, mime_type=mime,
                                       model=model_id, prompt=OCR_PROMPT, temperature=0.1)
                elapsed = round(time.time() - start, 1)
                sim = SequenceMatcher(None,
                    ground_truth.replace(" ","").replace("\n",""),
                    text.replace(" ","").replace("\n","")
                ).ratio()
                return model_label, case_idx, {
                    "text": text, "sim": sim, "elapsed": elapsed, "error": None
                }
            except Exception as e:
                return model_label, case_idx, {
                    "text": "", "sim": 0.0,
                    "elapsed": round(time.time()-start, 1),
                    "error": str(e)[:100]
                }

        total = len(ocr_selected) * len(valid_cases)
        prog = st.progress(0, text="OCR 评测中…")
        done = 0

        with ThreadPoolExecutor(max_workers=min(total, 12)) as ex:
            futures = [
                ex.submit(_ocr_one, lbl, mid, i,
                          compressed[i][0], compressed[i][1],
                          valid_cases[i]["ground_truth"])
                for lbl, mid in ocr_selected.items()
                for i in range(len(valid_cases))
            ]
            for f in as_completed(futures):
                lbl, ci, res = f.result()
                ocr_results[lbl][ci] = res
                done += 1
                prog.progress(done/total, text=f"已完成 {done}/{total}…")

        prog.empty()
        st.success("✅ OCR 评测完成！")

        # 排行榜
        st.subheader("③ 排行榜")
        import pandas as pd
        rows = []
        for lbl, res_list in ocr_results.items():
            valid = [r for r in res_list if r]
            avg_sim = sum(r["sim"] for r in valid) / len(valid) if valid else 0
            avg_t   = sum(r["elapsed"] for r in valid) / len(valid) if valid else 0
            per_img = {f"图{i+1}相似度": f"{r['sim']*100:.0f}%" if r else "—"
                       for i, r in enumerate(res_list)}
            rows.append({"排名":"","模型":lbl,
                         "平均相似度":f"{avg_sim*100:.0f}%",
                         **per_img,
                         "平均耗时(s)":f"{avg_t:.1f}",
                         "_s":avg_sim,"_t":avg_t})
        rows.sort(key=lambda x: (-x["_s"], x["_t"]))
        top2 = rows[:2]
        for i, r in enumerate(top2):
            r["排名"] = ["🥇","🥈"][i]
        display = ["排名","模型","平均相似度"] + \
                  [f"图{i+1}相似度" for i in range(len(valid_cases))] + ["平均耗时(s)"]
        st.dataframe(pd.DataFrame(top2)[display], hide_index=True, use_container_width=True)
        best_ocr = rows[0]
        st.info(f"💡 推荐：**{best_ocr['模型'].split('（')[0]}** — 相似度 {best_ocr['平均相似度']}，耗时 {best_ocr['平均耗时(s)']}s")

        # 逐图详细
        st.subheader("④ 逐图识别对比")
        for i, tc in enumerate(valid_cases):
            with st.expander(f"图片 {i+1}：{tc.get('name','') or f'测试图{i+1}'}"):
                st.markdown(f"**标准文字：**\n```\n{tc['ground_truth']}\n```")
                cols3 = st.columns(len(ocr_selected))
                for col3, lbl in zip(cols3, ocr_selected):
                    r = ocr_results[lbl][i]
                    with col3:
                        st.caption(f"**{lbl.split('（')[0]}**")
                        if r is None:
                            st.write("⏳")
                        elif r["error"]:
                            st.error(f"失败：{r['error']}")
                        else:
                            sim_pct = r["sim"] * 100
                            color = "✅" if sim_pct >= 80 else ("⚠️" if sim_pct >= 50 else "❌")
                            st.markdown(f"{color} 相似度 **{sim_pct:.0f}%** · {r['elapsed']}s")
                            st.text_area("识别结果", value=r["text"], height=120,
                                         key=f"ocr_out_{lbl}_{i}", disabled=True,
                                         label_visibility="collapsed")


# ──────────────────────────────────────────────────────────
# Tab2: 文字批改测试
# ──────────────────────────────────────────────────────────
with tab2:
    st.markdown("### ✏️ 文字批改模型评测")
    st.caption("用 8 道预置题目测试各文字模型的批改准确率，覆盖选择、判断、计算、应用四种题型。")

    # 模型选择
    st.markdown("**① 选择文字模型**")
    txt_selected: dict = {}
    tcols = st.columns(3)
    titems = list(TEXT_MODELS.items())
    chunk_size = (len(titems) + 2) // 3
    for ci in range(3):
        with tcols[ci]:
            for label, mid in titems[ci*chunk_size:(ci+1)*chunk_size]:
                if st.checkbox(label, key=f"txt_eval_{mid}", value=(mid in _DEFAULT_TEXT)):
                    txt_selected[label] = mid

    st.divider()

    TEST_CASES = [
        dict(id=1, type="选择题", label="选择题·答对",
             question="3的倍数是哪个？A.10  B.21  C.32  D.43",
             correct="B", student="B", expected=True),
        dict(id=2, type="选择题", label="选择题·答错",
             question="3的倍数是哪个？A.10  B.21  C.32  D.43",
             correct="B", student="C", expected=False),
        dict(id=3, type="判断题", label="判断题·答对",
             question="两个奇数的和一定是偶数。（判断题）",
             correct="√", student="√", expected=True),
        dict(id=4, type="判断题", label="判断题·答错",
             question="两个奇数的和一定是偶数。（判断题）",
             correct="√", student="×", expected=False),
        dict(id=5, type="计算题", label="计算题·答对（格式不同）",
             question="计算：24÷6×3",
             correct="24÷6×3=4×3=12", student="=12", expected=True),
        dict(id=6, type="计算题", label="计算题·答错（运算顺序）",
             question="计算：24÷6×3",
             correct="24÷6×3=4×3=12", student="24÷(6×3)≈1.3", expected=False),
        dict(id=7, type="应用题", label="应用题·答对（同义表述）",
             question="一辆汽车3小时行驶了180千米，平均每小时行驶多少千米？",
             correct="180÷3=60（千米），平均每小时60千米",
             student="60千米每小时", expected=True),
        dict(id=8, type="应用题", label="应用题·答错（用了乘法）",
             question="一辆汽车3小时行驶了180千米，平均每小时行驶多少千米？",
             correct="180÷3=60（千米），平均每小时60千米",
             student="180×3=540千米", expected=False),
    ]
    CASE_TYPES = list(dict.fromkeys(c["type"] for c in TEST_CASES))

    EVAL_SYSTEM = '''你是小学作业批改助手。根据题目和参考答案，判断学生答案是否正确。
规则：选择/判断题答案一致才算对；计算题最终结果正确即可，格式不限；
应用题同义表述算对；"分"可代表"分钟"等单位缩写允许。
严格输出JSON（不加代码块）：{"correct": true或false, "brief": "一句话原因"}'''

    with st.expander("📋 查看 8 道测试题", expanded=False):
        for c in TEST_CASES:
            st.markdown(
                f"**{c['id']}. [{c['type']}]** {c['question']}  \n"
                f"参考答案：`{c['correct']}`　学生答案：`{c['student']}`　"
                f"预期：{'✅正确' if c['expected'] else '❌错误'}"
            )

    n_txt = len(txt_selected)
    st.caption(f"已选 **{n_txt}** 个模型 · {len(TEST_CASES)} 道题 · 共 {n_txt*len(TEST_CASES)} 个任务并行")

    if st.button("🚀 开始批改评测", type="primary", disabled=(n_txt == 0)):
        txt_results: dict = {lbl: [None]*len(TEST_CASES) for lbl in txt_selected}

        def _txt_one(model_label, model_id, case_idx, case):
            start = time.time()
            try:
                user_msg = (f"题目：{case['question']}\n"
                            f"参考答案：{case['correct']}\n"
                            f"学生答案：{case['student']}")
                raw = chat(model=model_id, system=EVAL_SYSTEM, user=user_msg, temperature=0.1)
                elapsed = round(time.time()-start, 1)
                m_c = re.search(r'"correct"\s*:\s*(true|false)', raw, re.IGNORECASE)
                m_b = re.search(r'"brief"\s*:\s*"([^"]*)"', raw)
                model_says = m_c.group(1).lower() == "true" if m_c else None
                brief = m_b.group(1) if m_b else raw[:80]
            except Exception as e:
                elapsed = round(time.time()-start, 1)
                model_says = None
                brief = f"调用失败：{str(e)[:80]}"
            correct_eval = (model_says == case["expected"]) if model_says is not None else False
            return model_label, case_idx, {
                "model_says": model_says, "expected": case["expected"],
                "correct_eval": correct_eval, "elapsed": elapsed, "brief": brief,
            }

        total2 = n_txt * len(TEST_CASES)
        prog2 = st.progress(0, text="批改评测中…")
        done2 = 0

        with ThreadPoolExecutor(max_workers=min(total2, 16)) as ex:
            futures2 = [
                ex.submit(_txt_one, lbl, mid, i, case)
                for lbl, mid in txt_selected.items()
                for i, case in enumerate(TEST_CASES)
            ]
            for f in as_completed(futures2):
                lbl, ci, res = f.result()
                txt_results[lbl][ci] = res
                done2 += 1
                prog2.progress(done2/total2, text=f"已完成 {done2}/{total2}…")

        prog2.empty()
        st.success("✅ 批改评测完成！")

        import pandas as pd
        st.subheader("③ 排行榜")
        rows2 = []
        for lbl, res_list in txt_results.items():
            valid = [r for r in res_list if r]
            acc = sum(r["correct_eval"] for r in valid) / len(TEST_CASES)
            avg_t = sum(r["elapsed"] for r in valid) / len(valid) if valid else 0
            by_type: dict = {}
            for i, r in enumerate(res_list):
                t = TEST_CASES[i]["type"]
                by_type.setdefault(t, []).append(r["correct_eval"] if r else False)
            row2 = {"排名":"","模型":lbl,
                    "总准确率":f"{acc*100:.0f}%",
                    "平均耗时(s)":f"{avg_t:.1f}",
                    "_acc":acc,"_t":avg_t}
            for t in CASE_TYPES:
                vals = by_type.get(t, [False, False])
                row2[t] = f"{sum(vals)/len(vals)*100:.0f}%"
            rows2.append(row2)
        rows2.sort(key=lambda x: (-x["_acc"], x["_t"]))
        top2b = rows2[:2]
        for i, r in enumerate(top2b):
            r["排名"] = ["🥇","🥈"][i]
        dcols = ["排名","模型","总准确率"] + CASE_TYPES + ["平均耗时(s)"]
        st.dataframe(pd.DataFrame(top2b)[dcols], hide_index=True, use_container_width=True)
        best2 = rows2[0]
        st.info(f"💡 推荐：**{best2['模型'].split('（')[0]}** — 准确率 {best2['总准确率']}，耗时 {best2['平均耗时(s)']}s")

        st.subheader("④ 逐题详细对比")
        for i, case in enumerate(TEST_CASES):
            exp_label = "✅正确" if case["expected"] else "❌错误"
            with st.expander(
                f"第{i+1}题 · {case['label']} · 答案`{case['correct']}` · 学生`{case['student']}` · 预期{exp_label}"
            ):
                cols4 = st.columns(len(txt_selected))
                for col4, lbl in zip(cols4, txt_selected):
                    r = txt_results[lbl][i]
                    with col4:
                        st.caption(f"**{lbl.split('（')[0]}**")
                        if r is None:
                            st.write("⏳")
                        elif r["model_says"] is None:
                            st.warning("⚠️ 解析失败")
                            st.caption(r["brief"])
                        elif r["correct_eval"]:
                            st.success("✅ 判断正确")
                            st.caption(f"{'判为正确' if r['model_says'] else '判为错误'} · {r['elapsed']}s  \n{r['brief']}")
                        else:
                            st.error("❌ 判断有误")
                            st.caption(f"{'判为正确' if r['model_says'] else '判为错误'} · {r['elapsed']}s  \n{r['brief']}")
