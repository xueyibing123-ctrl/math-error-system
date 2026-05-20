import time
import json
import re
import streamlit as st
from concurrent.futures import ThreadPoolExecutor, as_completed
from llm_client import chat
from ui import icon_title

if not st.session_state.get("logged_in"):
    st.warning("请先登录")
    st.stop()
if st.session_state.get("user", {}).get("role") != "teacher":
    st.warning("仅教师可使用此功能")
    st.stop()

icon_title("assets/icons/批量分析.svg", "模型评测")
st.caption("一键对比各大模型的作业批改准确率与速度，选出最适合的模型。")

# ── 测试集（8道，覆盖4种题型×对错各一） ─────────────────────
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

# ── 模型列表（四大家族） ──────────────────────────────────────
MODEL_GROUPS = {
    "🧠 GLM（智谱）": {
        "glm-4-flash（免费·最快）": "glm-4-flash",
        "glm-4-air（均衡）": "glm-4-air",
        "glm-4-plus（高精度）": "glm-4-plus",
    },
    "☁️ Qwen（通义）": {
        "qwen-turbo（快速）": "qwen-turbo",
        "qwen-plus（均衡）": "qwen-plus",
        "qwen-max（最强）": "qwen-max",
    },
    "🔭 DeepSeek": {
        "deepseek-v4-pro（旗舰）": "deepseek-v4-pro",
        "deepseek-v4-flash（快速）": "deepseek-v4-flash",
        "deepseek-reasoner（R1·慢但准）": "deepseek-reasoner",
    },
    "🫧 Doubao（豆包）": {
        "doubao-seed-2.0-pro（最新旗舰）": "doubao-seed-2.0-pro",
        "doubao-1.5-pro-256k（长文本）": "doubao-1-5-pro-256k-250115",
    },
}

# 默认勾选各家代表模型
_DEFAULT = {"glm-4-flash", "qwen-max", "deepseek-v4-pro", "doubao-seed-2.0-pro"}

EVAL_SYSTEM = (
    "你是小学作业批改助手。根据题目和参考答案，判断学生答案是否正确。\n"
    "规则：选择/判断题答案一致才算对；计算题最终结果正确即可，格式不限；"
    "应用题同义表述算对；"分"可代表"分钟"等单位缩写允许。\n"
    "严格输出JSON（不加代码块）：{\"correct\": true或false, \"brief\": \"一句话原因\"}"
)

# ── ① 选择模型 ────────────────────────────────────────────
st.subheader("① 选择要评测的模型")
selected_models: dict[str, str] = {}
cols = st.columns(len(MODEL_GROUPS))
for col, (group_name, models) in zip(cols, MODEL_GROUPS.items()):
    with col:
        st.markdown(f"**{group_name}**")
        for label, mid in models.items():
            if st.checkbox(label, key=f"eval_{mid}", value=(mid in _DEFAULT)):
                selected_models[label] = mid

st.divider()

# ── ② 评测设置 & 启动 ─────────────────────────────────────
st.subheader("② 开始评测")

with st.expander("📋 查看 8 道测试题", expanded=False):
    for c in TEST_CASES:
        st.markdown(
            f"**{c['id']}. [{c['type']}]** {c['question']}  \n"
            f"参考答案：`{c['correct']}`　学生答案：`{c['student']}`　"
            f"预期：{'✅正确' if c['expected'] else '❌错误'}"
        )

n_models = len(selected_models)
st.caption(
    f"已选 **{n_models}** 个模型 · {len(TEST_CASES)} 道测试题 · "
    f"共 {n_models * len(TEST_CASES)} 个任务并行运行 · 预计 15-40 秒"
)

if st.button("🚀 开始评测", type="primary", disabled=n_models == 0):
    if n_models == 0:
        st.warning("请至少选择一个模型")
        st.stop()

    # results[model_label][case_idx] = {model_says, expected, correct_eval, elapsed, brief}
    results: dict[str, list] = {lbl: [None] * len(TEST_CASES) for lbl in selected_models}

    def _run_one(model_label, model_id, case_idx, case):
        start = time.time()
        try:
            user_msg = (
                f"题目：{case['question']}\n"
                f"参考答案：{case['correct']}\n"
                f"学生答案：{case['student']}"
            )
            raw = chat(model=model_id, system=EVAL_SYSTEM, user=user_msg, temperature=0.1)
            elapsed = round(time.time() - start, 1)
            # 鲁棒解析：直接从原始文本提取 correct 字段
            m_correct = re.search(r'"correct"\s*:\s*(true|false)', raw, re.IGNORECASE)
            m_brief   = re.search(r'"brief"\s*:\s*"([^"]*)"', raw)
            if m_correct:
                model_says = m_correct.group(1).lower() == "true"
                brief = m_brief.group(1) if m_brief else raw[:80]
            else:
                # 最后兜底：整体看是否含 true/false
                model_says = None
                brief = f"解析失败：{raw[:80]}"
        except Exception as e:
            elapsed = round(time.time() - start, 1)
            model_says = None
            brief = f"调用失败：{str(e)[:80]}"

        correct_eval = (model_says == case["expected"]) if model_says is not None else False
        return model_label, case_idx, {
            "model_says": model_says,
            "expected": case["expected"],
            "correct_eval": correct_eval,
            "elapsed": elapsed,
            "brief": brief,
        }

    prog = st.progress(0, text="正在评测，请稍候…")
    total_tasks = n_models * len(TEST_CASES)
    done = 0

    with ThreadPoolExecutor(max_workers=min(total_tasks, 16)) as ex:
        futures = [
            ex.submit(_run_one, lbl, mid, i, case)
            for lbl, mid in selected_models.items()
            for i, case in enumerate(TEST_CASES)
        ]
        for f in as_completed(futures):
            lbl, cidx, res = f.result()
            results[lbl][cidx] = res
            done += 1
            prog.progress(done / total_tasks, text=f"已完成 {done}/{total_tasks} 个任务…")

    prog.empty()
    st.success("✅ 评测完成！")

    # ── ③ 汇总排行榜 ──────────────────────────────────────
    st.subheader("③ 结果排行榜")
    st.caption("按准确率从高到低排列，准确率相同时按速度排列")

    import pandas as pd

    summary_rows = []
    for lbl, case_results in results.items():
        valid = [r for r in case_results if r is not None]
        n = len(TEST_CASES)
        acc = sum(r["correct_eval"] for r in valid) / n
        avg_t = sum(r["elapsed"] for r in valid) / len(valid) if valid else 0

        # 按题型统计
        by_type: dict[str, list] = {}
        for i, r in enumerate(case_results):
            t = TEST_CASES[i]["type"]
            by_type.setdefault(t, []).append(r["correct_eval"] if r else False)

        row = {
            "排名": "",
            "模型": lbl,
            "总准确率": f"{acc*100:.0f}%",
            "平均耗时(s)": f"{avg_t:.1f}",
            "_acc": acc,
            "_t": avg_t,
        }
        for t, vals in by_type.items():
            row[t] = f"{sum(vals)/len(vals)*100:.0f}%"
        summary_rows.append(row)

    summary_rows.sort(key=lambda x: (-x["_acc"], x["_t"]))
    for i, r in enumerate(summary_rows):
        r["排名"] = ["🥇", "🥈", "🥉"][i] if i < 3 else f"  {i+1}"

    display_cols = ["排名", "模型", "总准确率"] + list(TEST_CASES[0]["type"] and
        [t for t in dict.fromkeys(c["type"] for c in TEST_CASES)]) + ["平均耗时(s)"]
    display_cols = [c for c in display_cols if c in summary_rows[0]]

    df = pd.DataFrame(summary_rows)[display_cols]
    st.dataframe(df, hide_index=True, use_container_width=True)

    # 最佳推荐
    best = summary_rows[0]
    best_acc = best["总准确率"]
    best_t = best["平均耗时(s)"]
    st.info(f"💡 推荐：**{best['模型'].split('（')[0]}** — 准确率 {best_acc}，平均耗时 {best_t}s")

    # ── ④ 逐题详细对比 ────────────────────────────────────
    st.subheader("④ 逐题详细对比")
    for i, case in enumerate(TEST_CASES):
        exp_label = "✅正确" if case["expected"] else "❌错误"
        with st.expander(
            f"第{i+1}题 · {case['label']} · 正确答案 `{case['correct']}` · "
            f"学生答案 `{case['student']}` · 预期 {exp_label}"
        ):
            cols2 = st.columns(len(selected_models))
            for col2, lbl in zip(cols2, selected_models):
                r = results[lbl][i]
                with col2:
                    short_name = lbl.split("（")[0]
                    st.caption(f"**{short_name}**")
                    if r is None:
                        st.write("⏳ 无结果")
                    elif r["model_says"] is None:
                        st.warning("⚠️ 解析失败")
                        st.caption(r["brief"])
                    elif r["correct_eval"]:
                        st.success("✅ 判断正确")
                        verdict = "判为正确" if r["model_says"] else "判为错误"
                        st.caption(f"{verdict} · {r['elapsed']}s\n{r['brief']}")
                    else:
                        st.error("❌ 判断有误")
                        verdict = "判为正确" if r["model_says"] else "判为错误"
                        st.caption(f"{verdict} · {r['elapsed']}s\n{r['brief']}")
