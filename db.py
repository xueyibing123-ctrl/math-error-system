import sqlite3
from datetime import datetime

DB_NAME = "wrongbook.db"


def get_conn():
    return sqlite3.connect(DB_NAME, check_same_thread=False)


def init_db():
    conn = get_conn()
    cur = conn.cursor()
    init_users_table() 

    # 错题记录表
    cur.execute("""
        CREATE TABLE IF NOT EXISTS wrong_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id TEXT,
            question TEXT NOT NULL,
            student_answer TEXT NOT NULL,
            error_tag TEXT NOT NULL,
            feedback TEXT,
            created_at TEXT NOT NULL
        )
    """)

    # 预警表（统一版本，只建一次）
    cur.execute("""
        CREATE TABLE IF NOT EXISTS alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id TEXT,
            error_code TEXT NOT NULL,
            error_count INTEGER NOT NULL,
            threshold INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'OPEN',
            triggered_at TEXT NOT NULL
        )
    """)

    cur.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_alerts_unique_open
        ON alerts(student_id, error_code, status)
    """)

    conn.commit()
    conn.close()


def save_record(student_id: str,question: str, student_answer: str, error_tag: str, feedback: str):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO wrong_records (student_id, question, student_answer, error_tag, feedback, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (student_id, question, student_answer, error_tag, feedback, datetime.now().isoformat()))
    conn.commit()
    conn.close()


def count_same_error(error_tag: str) -> int:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM wrong_records WHERE error_tag = ?", (error_tag,))
    count = cur.fetchone()[0]
    conn.close()
    return count


def get_recent_records(limit: int = 50):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, student_id, question, student_answer, error_tag, feedback, created_at
        FROM wrong_records
        ORDER BY id DESC
        LIMIT ?
    """, (limit,))
    rows = cur.fetchall()
    conn.close()
    return rows


def get_error_stats(limit: int = 20):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT error_tag, COUNT(*) as cnt
        FROM wrong_records
        GROUP BY error_tag
        ORDER BY cnt DESC
        LIMIT ?
    """, (limit,))
    rows = cur.fetchall()
    conn.close()
    return rows


def upsert_alert(student_id: str, error_code: str, error_count: int, threshold: int):
    """达到阈值时写入 OPEN 预警；若已存在则更新次数与时间。"""
    conn = get_conn()
    cur = conn.cursor()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cur.execute("""
        SELECT id FROM alerts
        WHERE student_id IS ? AND error_code=? AND status='OPEN'
        LIMIT 1
    """, (student_id, error_code))
    row = cur.fetchone()
    if row:
        cur.execute("""
            UPDATE alerts SET error_count=?, threshold=?, triggered_at=?
            WHERE id=?
        """, (error_count, threshold, now, row[0]))
    else:
        cur.execute("""
            INSERT INTO alerts(student_id, error_code, error_count, threshold, status, triggered_at)
            VALUES(?, ?, ?, ?, 'OPEN', ?)
        """, (student_id, error_code, error_count, threshold, now))
    conn.commit()
    conn.close()


def list_alerts(status: str = "OPEN", limit: int = 200):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT student_id, error_code, error_count, threshold, status, triggered_at
        FROM alerts
        WHERE status=?
        ORDER BY triggered_at DESC
        LIMIT ?
    """, (status, limit))
    rows = cur.fetchall()
    conn.close()
    return rows


def resolve_alert(student_id: str, error_code: str):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        UPDATE alerts SET status='RESOLVED'
        WHERE student_id IS ? AND error_code=? AND status='OPEN'
    """, (student_id, error_code))
    conn.commit()
    conn.close()
def query_records(student_id=None, error_code=None, start_date=None, end_date=None, limit=500):
    conn = get_conn()
    cur = conn.cursor()
    sql = "SELECT id, student_id, question, student_answer, error_tag, created_at FROM wrong_records WHERE 1=1"
    params = []
    if student_id:
        sql += " AND student_id=?"
        params.append(student_id)
    if error_code and error_code != "ALL":
        sql += " AND error_tag=?"
        params.append(error_code)
    if start_date:
        sql += " AND substr(created_at,1,10) >= ?"
        params.append(start_date)
    if end_date:
        sql += " AND substr(created_at,1,10) <= ?"
        params.append(end_date)
    sql += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    cur.execute(sql, params)
    rows = cur.fetchall()
    conn.close()
    return rows
def init_users_table():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'student',
            class_name TEXT,
            created_at TEXT NOT NULL
        )
    """)
    # 默认创建一个教师账号
    cur.execute("""
        INSERT OR IGNORE INTO users (username, password, role, created_at)
        VALUES ('teacher', 'teacher123', 'teacher', ?)
    """, (datetime.now().isoformat(),))
    conn.commit()
    conn.close()

def register_user(username: str, password: str, class_name: str = "") -> bool:
    """注册学生，用户名重复返回 False"""
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO users (username, password, role, class_name, created_at)
            VALUES (?, ?, 'student', ?, ?)
        """, (username, password, class_name, datetime.now().isoformat()))
        conn.commit()
        conn.close()
        return True
    except sqlite3.IntegrityError:
        return False

def login_user(username: str, password: str):
    """登录成功返回用户信息 dict，失败返回 None"""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, username, role, class_name FROM users
        WHERE username=? AND password=?
    """, (username, password))
    row = cur.fetchone()
    conn.close()
    if row:
        return {"id": row[0], "username": row[1], "role": row[2], "class_name": row[3]}
    return None
def get_all_students():
    """获取所有学生账号列表"""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, username, class_name, created_at
        FROM users WHERE role='student'
        ORDER BY class_name, username
    """)
    rows = cur.fetchall()
    conn.close()
    return rows

def get_records_by_class(class_name: str, limit: int = 500):
    """按班级查询错题记录"""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT r.id, r.student_id, r.question, r.student_answer,
               r.error_tag, r.created_at, u.class_name
        FROM wrong_records r
        LEFT JOIN users u ON r.student_id = u.username
        WHERE u.class_name = ?
        ORDER BY r.created_at DESC
        LIMIT ?
    """, (class_name, limit))
    rows = cur.fetchall()
    conn.close()
    return rows

def get_all_classes():
    """获取所有班级名称"""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT DISTINCT class_name FROM users
        WHERE role='student' AND class_name IS NOT NULL AND class_name != ''
        ORDER BY class_name
    """)
    rows = [r[0] for r in cur.fetchall()]
    conn.close()
    return rows
def get_all_error_tags():
    """获取所有错因标签"""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS error_tags (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            description TEXT,
            drill_threshold INTEGER NOT NULL DEFAULT 3,
            enable_drill INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL
        )
    """)
    # 插入默认标签（已存在则忽略）
    defaults = [
        ("A1", "数字抄写错误", "抄题时数字写错", 3, 1),
        ("A2", "计算过程错误", "运算步骤出错", 3, 1),
        ("A3", "基础技能薄弱", "基础运算能力不足", 3, 1),
        ("B1", "关键概念识别错误", "单位或关键词理解偏差", 3, 1),
        ("B2", "运算类型误判", "加减乘除选择错误", 3, 1),
        ("B3", "变式迁移失败", "换一种说法就不会了", 3, 1),
        ("C1", "综合结构理解困难", "多步骤题目结构混乱", 3, 1),
        ("C2", "畏难情绪放弃", "遇难直接放弃不尝试", 5, 0),
        ("C3", "抽象关系建模能力不足", "无法建立数量关系", 3, 1),
    ]
    for code, name, desc, threshold, enable in defaults:
        cur.execute("""
            INSERT OR IGNORE INTO error_tags (code, name, description, drill_threshold, enable_drill, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (code, name, desc, threshold, enable, datetime.now().isoformat()))
    conn.commit()
    rows = cur.fetchall()
    cur.execute("SELECT id, code, name, description, drill_threshold, enable_drill FROM error_tags ORDER BY code")
    rows = cur.fetchall()
    conn.close()
    return rows

def upsert_error_tag(code: str, name: str, description: str, drill_threshold: int, enable_drill: bool):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO error_tags (code, name, description, drill_threshold, enable_drill, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(code) DO UPDATE SET
            name=excluded.name,
            description=excluded.description,
            drill_threshold=excluded.drill_threshold,
            enable_drill=excluded.enable_drill
    """, (code, name, description, drill_threshold, 1 if enable_drill else 0, datetime.now().isoformat()))
    conn.commit()
    conn.close()

def delete_error_tag(code: str):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM error_tags WHERE code=?", (code,))
    conn.commit()
    conn.close()
def get_student_error_stats(student_id: str):
    """获取某学生的错因分布"""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT error_tag, COUNT(*) as cnt
        FROM wrong_records
        WHERE student_id=?
        GROUP BY error_tag
        ORDER BY cnt DESC
    """, (student_id,))
    rows = cur.fetchall()
    conn.close()
    return rows

def get_student_trend(student_id: str, days: int = 30):
    """获取某学生近N天每天错因记录"""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT substr(created_at, 1, 10) as date, error_tag, COUNT(*) as cnt
        FROM wrong_records
        WHERE student_id=?
        AND created_at >= date('now', ?)
        GROUP BY date, error_tag
        ORDER BY date
    """, (student_id, f'-{days} days'))
    rows = cur.fetchall()
    conn.close()
    return rows