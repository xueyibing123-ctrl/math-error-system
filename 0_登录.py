import os
import streamlit as st
from dotenv import load_dotenv
from db import init_db, login_user, register_user
from ui import icon_title, icon_text,load_svg
import streamlit.components.v1 as components

load_dotenv()
init_db()

st.set_page_config(page_title="AI错因分析系统", page_icon="📘", layout="centered")

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Noto+Sans+SC:wght@400;500;700&display=swap');

html, body, [class*="css"] {
    font-family: 'Noto Sans SC', 'PingFang SC', sans-serif;
}

/* 主背景 */
.stApp {
    background: #F0F4FF !important;
}

/* 侧边栏白色 */
section[data-testid="stSidebar"] {
    background: #FFFFFF !important;
    border-right: 1px solid #E4E8F0 !important;
}
section[data-testid="stSidebar"] p,
section[data-testid="stSidebar"] label,
section[data-testid="stSidebar"] span,
section[data-testid="stSidebar"] h1,
section[data-testid="stSidebar"] h2,
section[data-testid="stSidebar"] h3 {
    color: #2D3250 !important;
}
section[data-testid="stSidebar"] .stTextInput > div > div {
    background: #F5F7FA !important;
    border-radius: 8px !important;
}
section[data-testid="stSidebar"] .stTextInput input {
    background: #F5F7FA !important;
    color: #2D3250 !important;
    -webkit-text-fill-color: #2D3250 !important;
    border-radius: 8px !important;
    caret-color: #2D3250 !important;
}
section[data-testid="stSidebar"] [data-testid="stSidebarNavLink"] {
    border-radius: 8px !important;
    color: #4A5280 !important;
    margin: 2px 0 !important;
}
section[data-testid="stSidebar"] [data-testid="stSidebarNavLink"]:hover {
    background: #EEF1FF !important;
    color: #4F6CF7 !important;
}
section[data-testid="stSidebar"] [aria-selected="true"] {
    background: linear-gradient(90deg, #EEF1FF, #F5F0FF) !important;
    color: #4F6CF7 !important;
    font-weight: 600 !important;
    border-left: 3px solid #4F6CF7 !important;
}
section[data-testid="stSidebar"] .stButton > button {
    background: linear-gradient(135deg, #EF4444, #DC2626) !important;
    color: white !important;
    border: none !important;
    border-radius: 10px !important;
    width: 100% !important;
    font-weight: 600 !important;
}
section[data-testid="stSidebar"] .stButton > button:hover {
    opacity: 0.85 !important;
}

/* 主内容区 */
.main .block-container {
    padding-top: 2rem !important;
}

/* 标题 */
h1, h2, h3 {
    color: #1A1D2E !important;
}
p, li {
    color: #4A5280 !important;
}

/* 登录卡片 */
.stTabs [data-baseweb="tab-panel"] {
    background: #FFFFFF !important;
    border-radius: 16px !important;
    border: 1px solid #E4E8F0 !important;
    padding: 1.5rem !important;
    box-shadow: 0 4px 20px rgba(79,108,247,0.08) !important;
}
..stTabs [data-baseweb="tab-list"] {
    background: #EEF1FF !important;
    border-radius: 12px !important;
    padding: 4px !important;
    gap: 8px !important;
    width: fit-content !important;
}
.stTabs [data-baseweb="tab"] {
    color: #6B7499 !important;
    border-radius: 8px !important;
    font-weight: 500 !important;
    min-width: 120px !important;
    justify-content: center !important;
}
.stTabs [aria-selected="true"] {
    background: #FFFFFF !important;
    color: #4F6CF7 !important;
    font-weight: 600 !important;
    box-shadow: 0 2px 8px rgba(79,108,247,0.15) !important;
}

/* 输入框 */
.stTextInput input, .stTextArea textarea {
    background: #F8FAFF !important;
    border: 1.5px solid #E0E6FF !important;
    color: #1A1D2E !important;
    -webkit-text-fill-color: #1A1D2E !important;
    border-radius: 10px !important;
    caret-color: #4F6CF7 !important;
}
.stTextInput input:focus, .stTextArea textarea:focus {
    border-color: #4F6CF7 !important;
    box-shadow: 0 0 0 3px rgba(79,108,247,0.12) !important;
    background: #FFFFFF !important;
}
.stTextInput input::placeholder {
    color: #A0AABF !important;
    -webkit-text-fill-color: #A0AABF !important;
}

/* 主按钮 */
.stButton > button[kind="primary"] {
    background: linear-gradient(135deg, #4F6CF7 0%, #7C3AED 100%) !important;
    color: white !important;
    border: none !important;
    border-radius: 10px !important;
    padding: 0.6rem 2rem !important;
    font-weight: 600 !important;
    font-size: 1rem !important;
    box-shadow: 0 4px 15px rgba(79,108,247,0.3) !important;
    transition: all 0.2s !important;
}
.stButton > button[kind="primary"]:hover {
    transform: translateY(-2px) !important;
    box-shadow: 0 8px 25px rgba(79,108,247,0.4) !important;
}

/* 普通按钮 */
.stButton > button {
    background: #FFFFFF !important;
    color: #4A5280 !important;
    border: 1.5px solid #E0E6FF !important;
    border-radius: 10px !important;
    transition: all 0.2s !important;
}
.stButton > button:hover {
    border-color: #4F6CF7 !important;
    color: #4F6CF7 !important;
    background: #F0F4FF !important;
}

/* 提示框 */
.stSuccess {
    background: #F0FDF4 !important;
    border: 1px solid #86EFAC !important;
    border-radius: 10px !important;
    color: #166534 !important;
}
.stWarning {
    background: #FFFBEB !important;
    border: 1px solid #FCD34D !important;
    border-radius: 10px !important;
    color: #92400E !important;
}
.stError {
    background: #FFF1F2 !important;
    border: 1px solid #FCA5A5 !important;
    border-radius: 10px !important;
    color: #991B1B !important;
}
.stInfo {
    background: #EFF6FF !important;
    border: 1px solid #93C5FD !important;
    border-radius: 10px !important;
    color: #1E40AF !important;
}

/* 卡片容器 */
[data-testid="stVerticalBlockBorderWrapper"] {
    background: #FFFFFF !important;
    border: 1px solid #E4E8F0 !important;
    border-radius: 16px !important;
    box-shadow: 0 2px 12px rgba(79,108,247,0.06) !important;
}

/* 数据表格 */
.stDataFrame {
    border-radius: 12px !important;
    overflow: hidden !important;
    border: 1px solid #E4E8F0 !important;
    box-shadow: 0 2px 8px rgba(0,0,0,0.04) !important;
}

/* selectbox */
.stSelectbox > div > div {
    background: #F8FAFF !important;
    border: 1.5px solid #E0E6FF !important;
    border-radius: 10px !important;
    color: #1A1D2E !important;
}

/* metric */
[data-testid="stMetric"] {
    background: #FFFFFF !important;
    border: 1px solid #E4E8F0 !important;
    border-radius: 12px !important;
    padding: 1rem !important;
    box-shadow: 0 2px 8px rgba(79,108,247,0.06) !important;
}
[data-testid="stMetricValue"] {
    color: #4F6CF7 !important;
    font-weight: 700 !important;
}
[data-testid="stMetricLabel"] {
    color: #6B7499 !important;
}

/* expander */
.streamlit-expanderHeader {
    background: #F5F7FF !important;
    border: 1px solid #E0E6FF !important;
    border-radius: 10px !important;
    color: #4A5280 !important;
}

/* 文件上传 */
[data-testid="stFileUploader"] {
    background: #F8FAFF !important;
    border: 2px dashed #C7D2FE !important;
    border-radius: 12px !important;
}

/* divider */
hr {
    border-color: #E4E8F0 !important;
}

/* caption */
.stCaption {
    color: #9CA3C0 !important;
}

/* 进度条 */
.stProgress > div > div {
    background: linear-gradient(90deg, #4F6CF7, #7C3AED) !important;
    border-radius: 999px !important;
}

/* slider */
.stSlider > div > div > div {
    background: #4F6CF7 !important;
}

/* 主标题渐变 */
.gradient-title {
    background: linear-gradient(135deg, #4F6CF7, #7C3AED);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    background-clip: text;
}
</style>
""", unsafe_allow_html=True)

# ── session_state 初始化 ──────────────────
if "logged_in" not in st.session_state:
    st.session_state.logged_in = False
if "user" not in st.session_state:
    st.session_state.user = None

# ── 侧边栏：登录状态显示 ──────────────────
with st.sidebar:
    if st.session_state.logged_in:
        u = st.session_state.user
        icon_text("assets/icons/user.svg", f"{u['username']}", size=18)
        st.caption(f"角色：{'教师' if u['role'] == 'teacher' else '学生'}")
        if u.get("class_name"):
            st.caption(f"班级：{u['class_name']}")
        st.markdown("---")
        st.caption("账户操作")
        if st.button("退出登录", key="btn_logout"):
            st.session_state.logged_in = False
            st.session_state.user = None
            st.session_state.student_id = "unknown"
            st.rerun()
    else:
        icon_text("assets/icons/log-in.svg", "请先登录", size=18)
    st.markdown("---")
    icon_text("assets/icons/book.svg", "小学数学错因分析系统 v2.0", size=16)

# ── 未登录：显示登录/注册页 ──────────────
if not st.session_state.logged_in:

    icon_title("assets/icons/book.svg", "小学数学应用题错因分析系统")
    st.caption("AI驱动 · 精准诊断 · 因材施教")

    # 如果想留一点间距
    st.markdown("<div style='margin-bottom: 1.5rem;'></div>", unsafe_allow_html=True)

    tab1, tab2 = st.tabs(["登录", "注册"])

    

    with tab1:
        st.subheader("登录")
        username = st.text_input("用户名", key="login_user")
        password = st.text_input("密码", type="password", key="login_pass")
        if st.button("登录", type="primary", key="btn_login"):
            if not username or not password:
                st.warning("请填写用户名和密码")
            else:
                user = login_user(username, password)
                if user:
                    st.session_state.logged_in = True
                    st.session_state.user = user
                    st.session_state.student_id = user["username"]
                    st.success(f"欢迎回来，{user['username']}！")
                    st.rerun()
                else:
                    st.error("用户名或密码错误")
        st.caption("教师默认账号：teacher / teacher123")

    with tab2:
        st.subheader("账号注册")
        
        CLASSES = [
            "三年级1班","三年级2班","三年级3班","三年级4班",
            "四年级1班","四年级2班","四年级3班","四年级4班",
            "五年级1班","五年级2班","五年级3班","五年级4班",
            "六年级1班","六年级2班","六年级3班","六年级4班",
        ]
        
        reg_name = st.text_input("姓名", key="reg_name")
        reg_class = st.selectbox("班级", CLASSES, key="reg_class")
        reg_pass = st.text_input("设置密码", type="password", key="reg_pass")
        reg_pass2 = st.text_input("确认密码", type="password", key="reg_pass2")
        teacher_code = st.text_input("教师邀请码（普通学生留空）", key="reg_teacher_code")
        TEACHER_INVITE_CODE = "teacher2024"

        if st.button("注册", type="primary", key="btn_register"):
            if not reg_name or not reg_pass:
                st.warning("请填写完整信息")
            elif reg_pass != reg_pass2:
                st.error("两次密码不一致")
            else:
                role = "teacher" if teacher_code == TEACHER_INVITE_CODE else "student"
                ok = register_user(reg_name, reg_pass, reg_class)
                if ok:
                    if role == "teacher":
                        from db import get_conn
                        conn = get_conn()
                        conn.execute("UPDATE users SET role='teacher' WHERE username=?", (reg_name,))
                        conn.commit()
                        conn.close()
                        st.success(f"✅ 教师账号注册成功！负责班级：{reg_class}")
                    else:
                        st.success(f"✅ 学生账号注册成功！班级：{reg_class}")
                else:
                    st.error("该用户名已存在，请换一个")
        st.caption("教师注册需填写邀请码，请联系管理员获取。")

# ── 已登录：显示主页 ──────────────────────
else:
    u = st.session_state.user
    role_text = "教师" if u["role"] == "teacher" else "学生"
    role_svg = load_svg("assets/icons/school.svg", size=34, color="#4F6CF7")

    st.markdown(f"""
    <div style="
        background: linear-gradient(135deg, rgba(79,108,247,0.15), rgba(124,58,237,0.10));
        border: 1px solid rgba(79,108,247,0.20);
        border-radius: 16px;
        padding: 1.5rem 2rem;
        margin-bottom: 1.5rem;
    ">
        <div style="display:flex; align-items:center; gap:1rem;">
            <div style="
                width:54px; height:54px;
                display:flex; align-items:center; justify-content:center;
                background:#FFFFFF;
                border-radius:14px;
                border:1px solid rgba(79,108,247,0.18);
                box-shadow: 0 2px 10px rgba(79,108,247,0.08);
            ">
                {role_svg}
            </div>
            <div>
                <h2 style="margin:0; color:#1A1D2E; font-size:1.4rem;">
                    欢迎回来，<span style="color:#4F6CF7;">{u['username']}</span>
                </h2>
                <p style="margin:0; color:#6B7499; font-size:0.9rem;">
                    {role_text} · {u.get('class_name','') or '系统管理员'}
                </p>
            </div>
        </div>
    </div>
    """, unsafe_allow_html=True)
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("功能模块", "8 个")
    with col2:
        st.metric("版本", "v2.0")
    with col3:
        st.metric("状态", "运行中")

    st.markdown("""
| 页面 | 功能 | 适用角色 |
|------|------|----------|
|  错因分析 | AI识别错因，生成专项训练 | 学生/教师 |
|  错题本 | 历史记录筛选导出 | 学生/教师 |
|  教师后台 | 统计图表、预警管理 | 教师 |
|  班级管理 | 学生名单、班级对比 | 教师 |
|  错因标签 | 自定义标签体系 | 教师 |
|  学生画像 | 雷达图、进步曲线 | 全部 |
|  批量分析 | 多题一次性分析 | 教师 |
""")