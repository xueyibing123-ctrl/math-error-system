import os
from datetime import datetime
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

load_dotenv()


def get_conn():
    url = os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("未检测到 DATABASE_URL，请检查 Secrets 配置")
    return psycopg2.connect(url, cursor_factory=RealDictCursor)


def init_db():
    pass
 


def save_record(student_id, question, student_answer, error_tag, feedback):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO wrong_records (student_id, question, student_answer, error_tag, feedback, created_at)
        VALUES (%s, %s, %s, %s, %s, %s)
    """, (student_id, question, student_answer, error_tag, feedback, datetime.now().isoformat()))
    conn.commit()
    cur.close()
    conn.close()


def count_same_error(error_tag):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) as cnt FROM wrong_records WHERE error_tag = %s", (error_tag,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row["cnt"] if row else 0


def get_recent_records(limit=50):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, student_id, question, student_answer, error_tag, feedback, created_at
        FROM wrong_records ORDER BY id DESC LIMIT %s
    """, (limit,))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [tuple(r.values()) for r in rows]


def get_error_stats(limit=20):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT error_tag, COUNT(*) as cnt FROM wrong_records
        GROUP BY error_tag ORDER BY cnt DESC LIMIT %s
    """, (limit,))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [(r["error_tag"], r["cnt"]) for r in rows]


def upsert_alert(student_id, error_code, error_count, threshold):
    conn = get_conn()
    cur = conn.cursor()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cur.execute("""
        SELECT id FROM alerts
        WHERE student_id = %s AND error_code = %s AND status = 'OPEN'
        LIMIT 1
    """, (student_id, error_code))
    row = cur.fetchone()
    if row:
        cur.execute("""
            UPDATE alerts SET error_count=%s, threshold=%s, triggered_at=%s WHERE id=%s
        """, (error_count, threshold, now, row["id"]))
    else:
        cur.execute("""
            INSERT INTO alerts (student_id, error_code, error_count, threshold, status, triggered_at)
            VALUES (%s, %s, %s, %s, 'OPEN', %s)
        """, (student_id, error_code, error_count, threshold, now))
    conn.commit()
    cur.close()
    conn.close()


def list_alerts(status="OPEN", limit=200):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT student_id, error_code, error_count, threshold, status, triggered_at
        FROM alerts WHERE status=%s ORDER BY triggered_at DESC LIMIT %s
    """, (status, limit))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [tuple(r.values()) for r in rows]


def resolve_alert(student_id, error_code):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        UPDATE alerts SET status='RESOLVED'
        WHERE student_id=%s AND error_code=%s AND status='OPEN'
    """, (student_id, error_code))
    conn.commit()
    cur.close()
    conn.close()


def query_records(student_id=None, error_code=None, start_date=None, end_date=None, limit=500):
    conn = get_conn()
    cur = conn.cursor()
    sql = "SELECT id, student_id, question, student_answer, error_tag, created_at FROM wrong_records WHERE 1=1"
    params = []
    if student_id:
        sql += " AND student_id=%s"
        params.append(student_id)
    if error_code and error_code != "ALL":
        sql += " AND error_tag=%s"
        params.append(error_code)
    if start_date:
        sql += " AND substr(created_at,1,10) >= %s"
        params.append(start_date)
    if end_date:
        sql += " AND substr(created_at,1,10) <= %s"
        params.append(end_date)
    sql += " ORDER BY created_at DESC LIMIT %s"
    params.append(limit)
    cur.execute(sql, params)
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [tuple(r.values()) for r in rows]


def register_user(username, password, class_name=""):
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO users (username, password, role, class_name, created_at)
            VALUES (%s, %s, 'student', %s, %s)
        """, (username, password, class_name, datetime.now().isoformat()))
        conn.commit()
        cur.close()
        conn.close()
        return True
    except Exception:
        return False


def login_user(username, password):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, username, role, class_name FROM users
        WHERE username=%s AND password=%s
    """, (username, password))
    row = cur.fetchone()
    cur.close()
    conn.close()
    if row:
        return {"id": row["id"], "username": row["username"], "role": row["role"], "class_name": row["class_name"]}
    return None


def get_all_students():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, username, class_name, created_at FROM users
        WHERE role='student' ORDER BY class_name, username
    """)
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [tuple(r.values()) for r in rows]


def get_records_by_class(class_name, limit=500):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT r.id, r.student_id, r.question, r.student_answer,
               r.error_tag, r.created_at, u.class_name
        FROM wrong_records r
        LEFT JOIN users u ON r.student_id = u.username
        WHERE u.class_name = %s
        ORDER BY r.created_at DESC LIMIT %s
    """, (class_name, limit))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [tuple(r.values()) for r in rows]


def get_all_classes():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT DISTINCT class_name FROM users
        WHERE role='student' AND class_name IS NOT NULL AND class_name != ''
        ORDER BY class_name
    """)
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [r["class_name"] for r in rows]


def get_all_error_tags():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT id, code, name, description, drill_threshold, enable_drill FROM error_tags ORDER BY code")
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [tuple(r.values()) for r in rows]


def upsert_error_tag(code, name, description, drill_threshold, enable_drill):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO error_tags (code, name, description, drill_threshold, enable_drill, created_at)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (code) DO UPDATE SET
            name=EXCLUDED.name,
            description=EXCLUDED.description,
            drill_threshold=EXCLUDED.drill_threshold,
            enable_drill=EXCLUDED.enable_drill
    """, (code, name, description, drill_threshold, 1 if enable_drill else 0, datetime.now().isoformat()))
    conn.commit()
    cur.close()
    conn.close()


def delete_error_tag(code):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM error_tags WHERE code=%s", (code,))
    conn.commit()
    cur.close()
    conn.close()


def get_student_error_stats(student_id):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT error_tag, COUNT(*) as cnt FROM wrong_records
        WHERE student_id=%s GROUP BY error_tag ORDER BY cnt DESC
    """, (student_id,))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [(r["error_tag"], r["cnt"]) for r in rows]


def get_student_trend(student_id, days=30):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT substr(created_at, 1, 10) as date, error_tag, COUNT(*) as cnt
        FROM wrong_records
        WHERE student_id=%s AND created_at >= (NOW() - INTERVAL '%s days')::TEXT
        GROUP BY date, error_tag ORDER BY date
    """, (student_id, days))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [tuple(r.values()) for r in rows]


def init_users_table():
    pass


# ═══════════════════════════════════════════════════════
# 题库管理
# ═══════════════════════════════════════════════════════

def init_question_bank():
    """创建题库表，并为旧表补充新字段。"""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS question_bank (
            id SERIAL PRIMARY KEY,
            subject VARCHAR(50) DEFAULT '数学',
            grade VARCHAR(50) DEFAULT '',
            source VARCHAR(200) DEFAULT '',
            question_text TEXT NOT NULL,
            correct_answer TEXT NOT NULL,
            total_points INT DEFAULT 0,
            scoring_criteria TEXT DEFAULT '[]',
            uploaded_by VARCHAR(100) DEFAULT '',
            created_at TIMESTAMP DEFAULT NOW()
        )
    """)
    # 兼容旧表：按点给分字段
    for col, defn in [("total_points", "INT DEFAULT 0"),
                      ("scoring_criteria", "TEXT DEFAULT '[]'")]:
        try:
            cur.execute(f"ALTER TABLE question_bank ADD COLUMN IF NOT EXISTS {col} {defn}")
        except Exception:
            pass
    conn.commit()
    cur.close()
    conn.close()


def add_question(subject, grade, source, question_text, correct_answer,
                 total_points=0, scoring_criteria=None, uploaded_by=""):
    """
    scoring_criteria: list of {"criterion": str, "points": int}
    """
    import json
    criteria_json = json.dumps(scoring_criteria or [], ensure_ascii=False)
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO question_bank
            (subject, grade, source, question_text, correct_answer,
             total_points, scoring_criteria, uploaded_by, created_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id
    """, (subject, grade, source, question_text, correct_answer,
          total_points, criteria_json, uploaded_by, datetime.now()))
    row = cur.fetchone()
    conn.commit()
    cur.close()
    conn.close()
    return row["id"] if row else None


def search_question_bank(question_text: str, subject: str = None, threshold: float = 0.65):
    """
    在题库中检索最相似的题目。
    返回 {'question_text','correct_answer','source','similarity',
           'total_points','scoring_criteria'} 或 None。
    """
    import json
    from difflib import SequenceMatcher
    conn = get_conn()
    cur = conn.cursor()
    if subject:
        cur.execute(
            "SELECT id, question_text, correct_answer, source, total_points, scoring_criteria "
            "FROM question_bank WHERE subject=%s", (subject,)
        )
    else:
        cur.execute(
            "SELECT id, question_text, correct_answer, source, total_points, scoring_criteria "
            "FROM question_bank"
        )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    if not rows:
        return None
    q_norm = question_text.replace(" ", "").replace("\n", "")
    best, best_score = None, 0.0
    for row in rows:
        db_norm = row["question_text"].replace(" ", "").replace("\n", "")
        score = SequenceMatcher(None, q_norm, db_norm).ratio()
        if score > best_score:
            best_score = score
            best = row
    if best_score >= threshold:
        try:
            criteria = json.loads(best["scoring_criteria"] or "[]")
        except Exception:
            criteria = []
        return {
            "question_text": best["question_text"],
            "correct_answer": best["correct_answer"],
            "source": best["source"],
            "similarity": round(best_score, 3),
            "total_points": best["total_points"] or 0,
            "scoring_criteria": criteria,
        }
    return None


def get_all_questions(subject=None, keyword=None, limit=200):
    conn = get_conn()
    cur = conn.cursor()
    sql = ("SELECT id, subject, grade, source, question_text, correct_answer, "
           "total_points, scoring_criteria, uploaded_by, created_at "
           "FROM question_bank WHERE 1=1")
    params = []
    if subject:
        sql += " AND subject=%s"
        params.append(subject)
    if keyword:
        sql += " AND (question_text ILIKE %s OR correct_answer ILIKE %s OR source ILIKE %s)"
        params.extend([f"%{keyword}%", f"%{keyword}%", f"%{keyword}%"])
    sql += " ORDER BY created_at DESC LIMIT %s"
    params.append(limit)
    cur.execute(sql, params)
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [dict(r) for r in rows]


def delete_question(question_id: int):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM question_bank WHERE id=%s", (question_id,))
    conn.commit()
    cur.close()
    conn.close()


def count_questions(subject=None):
    conn = get_conn()
    cur = conn.cursor()
    if subject:
        cur.execute("SELECT COUNT(*) as cnt FROM question_bank WHERE subject=%s", (subject,))
    else:
        cur.execute("SELECT COUNT(*) as cnt FROM question_bank")
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row["cnt"] if row else 0
