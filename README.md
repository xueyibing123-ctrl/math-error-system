# 📘 小学数学应用题错因分析系统 v2.0

AI驱动 · 精准诊断 · 因材施教

基于阿里云通义千问大模型，帮助教师快速识别学生数学错因，生成专项训练，形成学生画像。

---

## ✨ 功能模块

| 页面 | 功能 | 适用角色 |
|------|------|----------|
| 错因分析 | AI识别错因，生成专项训练 | 学生/教师 |
| 错题本 | 历史记录筛选导出 | 学生/教师 |
| 教师后台 | 统计图表、预警管理 | 教师 |
| 班级管理 | 学生名单、班级对比 | 教师 |
| 错因标签 | 自定义标签体系 | 教师 |
| 学生画像 | 雷达图、进步曲线 | 全部 |
| 批量分析 | 多题一次性分析 | 教师 |

---

## 🚀 本地运行

### 1. 克隆项目

```bash
git clone https://github.com/你的用户名/你的仓库名.git
cd 你的仓库名
```

### 2. 安装依赖

```bash
pip install -r requirements.txt
```

### 3. 配置 API Key

复制示例文件并填入你自己的阿里云 API Key：

```bash
cp .env.example .env
```

打开 `.env` 文件，填入你的 Key：

```
DASHSCOPE_API_KEY=你的阿里云API Key
DASHSCOPE_MODEL=qwen-plus
```

> 阿里云 API Key 获取地址：https://bailian.console.aliyun.com → 右上角「API Key 管理」

### 4. 启动系统

```bash
streamlit run 0_登录.py
```

浏览器访问 http://localhost:8501 即可使用。

---

## 👤 默认账号

| 账号 | 密码 | 角色 |
|------|------|------|
| teacher | teacher123 | 教师 |

学生账号需自行注册，教师注册需填写邀请码（默认：`teacher2024`）。

---

## 🛠 技术栈

- **前端框架**：Streamlit
- **AI 模型**：阿里云通义千问（qwen-plus / qwen-vl-plus）
- **数据库**：SQLite
- **图表**：Plotly

---

## 📁 项目结构

```
├── 0_登录.py            # 主入口，登录/注册
├── pages/
│   ├── 1_错因分析.py
│   ├── 2_错题本.py
│   ├── 3_教师后台.py
│   ├── 4_班级管理.py
│   ├── 5_错因标签管理.py
│   ├── 6_学生画像.py
│   └── 7_批量分析.py
├── assets/icons/        # SVG 图标
├── db.py                # 数据库操作
├── llm_client.py        # AI 模型调用
├── ui.py                # 公共 UI 组件
├── .env.example         # 环境变量示例
└── requirements.txt     # 依赖列表
```

---

## 📄 License

MIT License · 欢迎二次开发
