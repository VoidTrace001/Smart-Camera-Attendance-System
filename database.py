import sqlite3
import os
import json
import cv2
from datetime import datetime
import hashlib
from werkzeug.security import generate_password_hash, check_password_hash

# Use SQLite locally, but allow PostgreSQL (Supabase) in production
DB_TYPE = "postgres" if os.environ.get('DATABASE_URL') else "sqlite"
DB_NAME = "attendance.db"

# Face crops and profile photos live OUTSIDE static/ so Flask will not serve
# them unauthenticated. They are read back through an authorised route.
MEDIA_ROOT = os.environ.get('MEDIA_ROOT', 'private_media')

class PgCursorWrapper:
    def __init__(self, cursor):
        self._cursor = cursor
    def execute(self, sql, params=None):
        if params is not None:
            sql = sql.replace('?', '%s')
            return self._cursor.execute(sql, params)
        return self._cursor.execute(sql)
    def executemany(self, sql, params_list):
        sql = sql.replace('?', '%s')
        return self._cursor.executemany(sql, params_list)
    def fetchone(self):
        res = self._cursor.fetchone()
        return dict(res) if res is not None and not isinstance(res, dict) else res
    def fetchall(self):
        rows = self._cursor.fetchall()
        return [dict(r) if not isinstance(r, dict) else r for r in rows] if rows else []
    @property
    def rowcount(self):
        return self._cursor.rowcount
    @property
    def lastrowid(self):
        return getattr(self._cursor, 'lastrowid', None)

class PgConnWrapper:
    def __init__(self, conn):
        self._conn = conn
    def cursor(self):
        return PgCursorWrapper(self._conn.cursor())
    def execute(self, sql, params=None):
        cur = self.cursor()
        cur.execute(sql, params)
        return cur
    def executemany(self, sql, params_list):
        cur = self.cursor()
        cur.executemany(sql, params_list)
        return cur
    def commit(self):
        return self._conn.commit()
    def rollback(self):
        return self._conn.rollback()
    def close(self):
        return self._conn.close()

def get_db_connection():
    if DB_TYPE == "postgres":
        import psycopg2
        from psycopg2.extras import RealDictCursor
        conn = psycopg2.connect(os.environ.get('DATABASE_URL'))
        conn.cursor_factory = RealDictCursor 
        return PgConnWrapper(conn)
    else:
        # The web app, the attendance engine and one vision worker per camera
        # are separate processes all writing this one file. On the default
        # rollback journal a writer blocks every reader, and the default 5s
        # timeout is short enough that a busy classroom raises
        # "database is locked" mid-class. WAL lets readers run during a write;
        # busy_timeout makes the writers queue instead of failing.
        conn = sqlite3.connect(DB_NAME, timeout=30.0)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute('PRAGMA journal_mode = WAL')
            conn.execute('PRAGMA busy_timeout = 30000')
            conn.execute('PRAGMA synchronous = NORMAL')
        except sqlite3.Error:
            pass  # a read-only or locked file must still hand back a connection
        return conn

def migrate_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Define placeholder for SQL syntax differences
    if DB_TYPE == "postgres":
        # Migration for Postgres (Supabase)
        tables = {
            "students": [
                ('course', 'TEXT'), ('year', 'TEXT'), ('outlook_email', 'TEXT'),
                ('parent_email', 'TEXT'), ('parent_phone', 'TEXT'), ('qr_hash', 'TEXT'),
                ('ou_id', 'TEXT'), ('edc_number', 'TEXT'), ('face_embedding', 'TEXT'),
                ('password_hash', 'TEXT')
            ],
            "users": [('face_encoding', 'TEXT'), ('subjects', 'TEXT'), ('face_embedding', 'TEXT')],
            "attendance": [
                ('user_id', 'INTEGER'), ('subject', 'TEXT'), 
                ('status', 'TEXT DEFAULT \'Present\''), ('marked_by_user_id', 'INTEGER'),
                ('verification_photo', 'TEXT')
            ],
            "timetable": [('teacher_id', 'INTEGER')]
        }
        for table, cols in tables.items():
            for col, col_type in cols:
                try:
                    cursor.execute(f'ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {col} {col_type}')
                except: conn.rollback()
    else:
        # Existing SQLite Migration
        migrations = [
            ('students', 'course', 'TEXT'),
            ('students', 'year', 'TEXT'),
            ('students', 'outlook_email', 'TEXT'),
            ('students', 'parent_email', 'TEXT'),
            ('students', 'parent_phone', 'TEXT'),
            ('students', 'qr_hash', 'TEXT'),
            ('students', 'ou_id', 'TEXT'),
            ('students', 'edc_number', 'TEXT'),
            ('students', 'face_embedding', 'TEXT'),
            ('students', 'password_hash', 'TEXT'),
            ('users', 'face_encoding', 'TEXT'),
            ('users', 'subjects', 'TEXT'),
            ('users', 'face_embedding', 'TEXT'),
            ('attendance', 'user_id', 'INTEGER'),
            ('attendance', 'subject', 'TEXT'),
            ('attendance', 'status', 'TEXT DEFAULT "Present"'),
            ('attendance', 'marked_by_user_id', 'INTEGER'),
            ('attendance', 'verification_photo', 'TEXT'),
            ('timetable', 'teacher_id', 'INTEGER')
        ]
        for table, column, col_type in migrations:
            try:
                cursor.execute(f'ALTER TABLE {table} ADD COLUMN {column} {col_type}')
            except sqlite3.OperationalError: pass
            
    # Backfill hashes for students registered before hashing existed.
    try:
        legacy = cursor.execute(
            'SELECT id, edc_number FROM students WHERE password_hash IS NULL AND edc_number IS NOT NULL').fetchall()
        for row in legacy:
            cursor.execute('UPDATE students SET password_hash = ? WHERE id = ?',
                           (generate_password_hash(row['edc_number']), row['id']))
        if legacy:
            print(f"[MIGRATION] Hashed {len(legacy)} legacy student credential(s).")
    except Exception as e:
        print(f"[MIGRATION] Student credential backfill skipped: {e}")

    conn.commit()
    conn.close()

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Auto-increment keyword differs between SQLite and Postgres
    SERIAL_KEY = "SERIAL PRIMARY KEY" if DB_TYPE == "postgres" else "INTEGER PRIMARY KEY AUTOINCREMENT"

    # 1. Students Table
    cursor.execute(f'''
        CREATE TABLE IF NOT EXISTS students (
            id {SERIAL_KEY},
            name TEXT NOT NULL,
            ou_id TEXT,
            edc_number TEXT,
            course TEXT NOT NULL,
            year TEXT NOT NULL,
            outlook_email TEXT,
            parent_email TEXT,
            parent_phone TEXT,
            qr_hash TEXT,
            password_hash TEXT,
            UNIQUE(ou_id),
            UNIQUE(edc_number),
            UNIQUE(outlook_email)
        )
    ''')
    
    # 2. Student Faces Table
    cursor.execute(f'''
        CREATE TABLE IF NOT EXISTS student_faces (
            id {SERIAL_KEY},
            student_id INTEGER NOT NULL,
            face_encoding TEXT NOT NULL,
            face_embedding TEXT,
            FOREIGN KEY (student_id) REFERENCES students (id) ON DELETE CASCADE
        )
    ''')

    # 3. Users Table (Faculty/Admins)
    cursor.execute(f'''
        CREATE TABLE IF NOT EXISTS users (
            id {SERIAL_KEY},
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            full_name TEXT NOT NULL,
            role TEXT NOT NULL, 
            department TEXT,
            subjects TEXT,
            face_encoding TEXT,
            face_embedding TEXT
        )
    ''')
    
    # 4. Timetable Table
    cursor.execute(f'''
        CREATE TABLE IF NOT EXISTS timetable (
            id {SERIAL_KEY},
            course TEXT NOT NULL,
            year TEXT NOT NULL,
            day_of_week TEXT NOT NULL,
            start_time TEXT NOT NULL,
            end_time TEXT NOT NULL,
            subject TEXT NOT NULL,
            teacher_id INTEGER,
            FOREIGN KEY (teacher_id) REFERENCES users (id)
        )
    ''')
    
    # 5. Attendance Table
    cursor.execute(f'''
        CREATE TABLE IF NOT EXISTS attendance (
            id {SERIAL_KEY},
            student_id INTEGER,
            user_id INTEGER,
            date TEXT NOT NULL,
            time_in TEXT NOT NULL,
            subject TEXT,
            status TEXT DEFAULT 'Present',
            marked_by_user_id INTEGER,
            verification_photo TEXT,
            FOREIGN KEY (student_id) REFERENCES students (id) ON DELETE CASCADE,
            FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE,
            FOREIGN KEY (marked_by_user_id) REFERENCES users (id)
        )
    ''')

    # 6. Leave Requests Table
    cursor.execute(f'''
        CREATE TABLE IF NOT EXISTS leave_requests (
            id {SERIAL_KEY},
            student_id INTEGER NOT NULL,
            start_date TEXT NOT NULL,
            end_date TEXT NOT NULL,
            reason TEXT NOT NULL,
            status TEXT DEFAULT 'Pending',
            applied_on TEXT NOT NULL,
            FOREIGN KEY (student_id) REFERENCES students (id) ON DELETE CASCADE
        )
    ''')

    # 7. Announcements Table
    cursor.execute(f'''
        CREATE TABLE IF NOT EXISTS announcements (
            id {SERIAL_KEY},
            user_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            target_course TEXT DEFAULT 'All',
            created_at TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
    ''')

    # 8. Error Logs Table (For AI Auto-Repair System)
    cursor.execute(f'''
        CREATE TABLE IF NOT EXISTS error_logs (
            id {SERIAL_KEY},
            timestamp TEXT NOT NULL,
            route TEXT,
            traceback_data TEXT NOT NULL,
            status TEXT DEFAULT 'Pending',
            ai_analysis TEXT
        )
    ''')

    # 9. Audit Logs Table (Enterprise Security)
    cursor.execute(f'''
        CREATE TABLE IF NOT EXISTS audit_logs (
            id {SERIAL_KEY},
            user_id INTEGER,
            action TEXT NOT NULL,
            target_type TEXT,
            target_id INTEGER,
            timestamp TEXT NOT NULL,
            ip_address TEXT
        )
    ''')

    # 10. AI Predictions Table
    cursor.execute(f'''
        CREATE TABLE IF NOT EXISTS ai_predictions (
            id {SERIAL_KEY},
            student_id INTEGER,
            prediction_type TEXT,
            risk_level TEXT,
            ai_analysis TEXT,
            generated_at TEXT
        )
    ''')

    # Default admin - Use environment variables for security
    admin_user = os.environ.get('INITIAL_ADMIN_USER', 'admin')
    admin_pass = os.environ.get('INITIAL_ADMIN_PASS', 'admin123')
    
    admin_password_hashed = generate_password_hash(admin_pass)
    
    if DB_TYPE == "sqlite":
        cursor.execute("INSERT OR IGNORE INTO users (username, password, full_name, role) VALUES (?, ?, 'System Administrator', 'Admin')", (admin_user, admin_password_hashed))
    else:
        cursor.execute("INSERT INTO users (username, password, full_name, role) VALUES (?, ?, 'System Administrator', 'Admin') ON CONFLICT (username) DO NOTHING", (admin_user, admin_password_hashed))
    
    conn.commit()
    conn.close()

# --- User/Auth Functions ---
def check_login(username, password):
    conn = get_db_connection()
    user = conn.execute('SELECT * FROM users WHERE username = ?', (username,)).fetchone()
    if user and check_password_hash(user['password'], password):
        conn.close()
        return {'id': user['id'], 'username': user['username'], 'role': user['role'], 'full_name': user['full_name']}
    
    # Students authenticate against a PBKDF2 hash, seeded from their EDC number
    # at registration. Never compared in plaintext.
    student = conn.execute('SELECT * FROM students WHERE outlook_email = ?', (username,)).fetchone()
    conn.close()
    if student and student['password_hash'] and check_password_hash(student['password_hash'], password):
        return {'id': student['id'], 'username': student['outlook_email'], 'role': 'Student', 'full_name': student['name']}

    return None

def add_teacher(username, password, full_name, department, subjects=None):
    conn = get_db_connection()
    hashed_password = generate_password_hash(password)
    try:
        conn.execute('INSERT INTO users (username, password, full_name, role, department, subjects) VALUES (?, ?, ?, ?, ?, ?)',
                     (username, hashed_password, full_name, 'Teacher', department, subjects))
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()

def get_all_teachers():
    conn = get_db_connection()
    teachers = conn.execute("SELECT * FROM users WHERE role = 'Teacher'").fetchall()
    conn.close()
    return teachers

def delete_user(user_id):
    conn = get_db_connection()
    conn.execute('DELETE FROM users WHERE id = ?', (user_id,))
    conn.commit()
    conn.close()

def get_user_by_id(user_id):
    conn = get_db_connection()
    user = conn.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()
    conn.close()
    return user

def update_user(user_id, full_name, username, password=None, subjects=None):
    conn = get_db_connection()
    if password:
        hashed_password = generate_password_hash(password)
        conn.execute('UPDATE users SET full_name = ?, username = ?, password = ?, subjects = ? WHERE id = ?',
                     (full_name, username, hashed_password, subjects, user_id))
    else:
        conn.execute('UPDATE users SET full_name = ?, username = ? , subjects = ? WHERE id = ?',
                     (full_name, username, subjects, user_id))
    conn.commit()
    conn.close()


# --- Student Functions ---
def add_student(name, ou_id, edc_number, course, year, outlook_email=None, parent_email=None, parent_phone=None):
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Generate unique QR hash based on EDC number
    qr_data = f"STUDENT_QR_{edc_number}_{datetime.now().timestamp()}"
    qr_hash = hashlib.sha256(qr_data.encode()).hexdigest()[:20]

    # Initial credential is the EDC number, stored hashed. Students change it later.
    password_hash = generate_password_hash(edc_number)

    try:
        cursor.execute('INSERT INTO students (name, ou_id, edc_number, course, year, outlook_email, parent_email, parent_phone, qr_hash, password_hash) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                       (name, ou_id, edc_number, course, year, outlook_email, parent_email, parent_phone, qr_hash, password_hash))
        student_id = cursor.lastrowid
        conn.commit()
        return student_id
    except sqlite3.IntegrityError:
        return None
    finally:
        conn.close()

def get_student_by_id(student_id):
    conn = get_db_connection()
    student = conn.execute('SELECT * FROM students WHERE id = ?', (student_id,)).fetchone()
    conn.close()
    return student


def student_password_is_default(student_id):
    """True while a student is still signing in with their EDC number.

    EDC numbers are printed on ID cards and circulate freely inside a college,
    so an account still on its seeded credential is effectively unprotected.
    """
    conn = get_db_connection()
    row = conn.execute('SELECT edc_number, password_hash FROM students WHERE id = ?',
                       (student_id,)).fetchone()
    conn.close()
    if not row or not row['password_hash'] or not row['edc_number']:
        return False
    return check_password_hash(row['password_hash'], row['edc_number'])


def set_student_password(student_id, current_password, new_password):
    """Returns (ok, message). Verifies the old password before changing it."""
    conn = get_db_connection()
    row = conn.execute('SELECT password_hash FROM students WHERE id = ?', (student_id,)).fetchone()
    if not row:
        conn.close()
        return False, "Account not found."

    if not row['password_hash'] or not check_password_hash(row['password_hash'], current_password):
        conn.close()
        return False, "Your current password is not correct."

    if len(new_password or '') < 8:
        conn.close()
        return False, "Choose a password of at least 8 characters."

    if new_password == current_password:
        conn.close()
        return False, "That is the password you are already using."

    conn.execute('UPDATE students SET password_hash = ? WHERE id = ?',
                 (generate_password_hash(new_password), student_id))
    conn.commit()
    conn.close()
    return True, "Password updated."


def add_student_face(student_id, face_encoding_json, face_embedding_json=None):
    conn = get_db_connection()
    conn.execute('INSERT INTO student_faces (student_id, face_encoding, face_embedding) VALUES (?, ?, ?)', (student_id, face_encoding_json, face_embedding_json))
    conn.commit()
    conn.close()

def add_faculty_face(user_id, face_encoding_json, face_embedding_json=None):
    conn = get_db_connection()
    conn.execute('UPDATE users SET face_encoding = ?, face_embedding = ? WHERE id = ?', (face_encoding_json, face_embedding_json, user_id))
    conn.commit()
    conn.close()

def get_all_people_with_faces():
    conn = get_db_connection()
    students = conn.execute('SELECT s.id, s.name, f.face_encoding, f.face_embedding FROM students s JOIN student_faces f ON s.id = f.student_id').fetchall()
    faculty = conn.execute('SELECT id, full_name as name, face_encoding, face_embedding, role FROM users WHERE face_encoding IS NOT NULL').fetchall()
    conn.close()
    
    people = []
    mapping = {}
    label_counter = 1
    
    for r in students:
        people.append({'label': label_counter, 'face_encoding': r['face_encoding'], 'face_embedding': r['face_embedding']})
        mapping[label_counter] = {'id': r['id'], 'name': r['name'], 'role': 'Student'}
        label_counter += 1
        
    for r in faculty:
        people.append({'label': label_counter, 'face_encoding': r['face_encoding'], 'face_embedding': r['face_embedding']})
        mapping[label_counter] = {'id': r['id'], 'name': r['name'], 'role': r['role']}
        label_counter += 1
        
    return people, mapping

def get_all_people_with_embeddings():
    """Returns all users and students who have neural embeddings registered."""
    conn = get_db_connection()
    students = conn.execute('SELECT s.id, s.name, sf.face_embedding FROM students s JOIN student_faces sf ON s.id = sf.student_id WHERE sf.face_embedding IS NOT NULL').fetchall()
    faculty = conn.execute('SELECT id, full_name as name, face_embedding, role FROM users WHERE face_embedding IS NOT NULL').fetchall()
    conn.close()
    
    people = []
    for s in students:
        people.append({'id': s['id'], 'name': s['name'], 'role': 'Student', 'embedding': json.loads(s['face_embedding']) if s['face_embedding'] else None})
    for f in faculty:
        people.append({'id': f['id'], 'name': f['name'], 'role': f['role'], 'embedding': json.loads(f['face_embedding']) if f['face_embedding'] else None})
    return [p for p in people if p['embedding'] is not None]

def get_all_students():
    conn = get_db_connection()
    res = conn.execute('SELECT * FROM students ORDER BY course, year, name').fetchall()
    conn.close()
    return res

def delete_student(student_id):
    conn = get_db_connection()
    conn.execute('PRAGMA foreign_keys = ON')
    # sightings and presence_intervals carry no foreign key, so the cascade
    # does not reach them. Removing a student has to clear their biometric
    # trail too, not just the row that names them.
    for table in ('sightings', 'presence_intervals'):
        try:
            conn.execute(f'DELETE FROM {table} WHERE student_id = ?', (student_id,))
        except Exception:
            pass  # table absent on an install predating the presence model
    conn.execute('DELETE FROM students WHERE id = ?', (student_id,))
    conn.commit()
    conn.close()

def get_student_by_qr(qr_hash):
    conn = get_db_connection()
    student = conn.execute('SELECT id, name FROM students WHERE qr_hash = ?', (qr_hash,)).fetchone()
    conn.close()
    return student

# --- Attendance Functions ---
def save_verification_photo(captured_face, role, person_id, date_str, now):
    if captured_face is None:
        return None
    try:
        folder = os.path.join(MEDIA_ROOT, 'attendance_captures')
        if not os.path.exists(folder):
            os.makedirs(folder)

        filename = f"verify_{role}_{person_id}_{date_str}_{now.strftime('%H%M%S')}.jpg"
        cv2.imwrite(os.path.join(folder, filename), captured_face)
        return filename          # relative, so the web layer can serve it
    except Exception as e:
        print(f"Error saving verification photo: {e}")
        return None


def mark_attendance(person_id, role='Student', status='Present', override_date=None, override_subject=None, captured_face=None):
    conn = get_db_connection()
    cursor = conn.cursor()
    now = datetime.now()
    date_str = override_date if override_date else now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H:%M:%S")
    
    subject = "Faculty Duty" if role != 'Student' else "Unknown"
    teacher_id = None

    if role == 'Student':
        if override_subject:
            subject = override_subject
        else:
            current_day = now.strftime("%A")
            current_time_hm = now.strftime("%H:%M")
            # Scope the lookup to THIS student's course/year, otherwise anyone
            # walking past during any period gets marked into that class.
            t_query = '''SELECT t.subject, t.teacher_id
                         FROM timetable t
                         JOIN students s ON s.course = t.course AND s.year = t.year
                         WHERE s.id = ? AND t.day_of_week = ?
                           AND t.start_time <= ? AND t.end_time > ?'''
            t_row = cursor.execute(t_query, (person_id, current_day, current_time_hm, current_time_hm)).fetchone()
            subject = t_row['subject'] if t_row else "Entry / Free Period"
            teacher_id = t_row['teacher_id'] if t_row else None

    # Check existing
    if role == 'Student':
        cursor.execute('SELECT id FROM attendance WHERE student_id = ? AND date = ? AND subject = ?', (person_id, date_str, subject))
    else:
        cursor.execute('SELECT id FROM attendance WHERE user_id = ? AND date = ?', (person_id, date_str))
        
    existing = cursor.fetchone()
    if existing and not (override_date or override_subject):
        conn.close()
        return False

    verification_photo_path = save_verification_photo(captured_face, role, person_id, date_str, now)

    if existing:
        cursor.execute('UPDATE attendance SET status = ?, verification_photo = ? WHERE id = ?', (status, verification_photo_path, existing['id']))
        conn.commit()
        conn.close()
        return True

    if role == 'Student':
        cursor.execute('INSERT INTO attendance (student_id, date, time_in, subject, marked_by_user_id, status, verification_photo) VALUES (?, ?, ?, ?, ?, ?, ?)',
                       (person_id, date_str, time_str, subject, teacher_id, status, verification_photo_path))
    else:
        cursor.execute('INSERT INTO attendance (user_id, date, time_in, subject, status, verification_photo) VALUES (?, ?, ?, ?, ?, ?)',
                       (person_id, date_str, time_str, "Faculty Attendance", status, verification_photo_path))
                       
    conn.commit()
    
    # Real-time update for anyone with a dashboard open. This is called from
    # the vision workers too, where there is no Socket.IO server and the emit
    # is skipped - see realtime.py.
    try:
        from realtime import emit
        person_name = cursor.execute('SELECT name FROM students WHERE id = ?', (person_id,)).fetchone() if role == 'Student' else cursor.execute('SELECT full_name as name FROM users WHERE id = ?', (person_id,)).fetchone()
        if emit('attendance_marked', {
            'name': person_name['name'] if person_name else "Unknown",
            'time': time_str,
            'subject': subject if role == 'Student' else "Faculty Duty",
            'status': status
        }):
            emit('update_stats', get_stats())
    except Exception as e:
        print(f"Realtime notify failed: {e}")
    
    # Send Emails & WhatsApp
    if role == 'Student':
        student = cursor.execute('SELECT name, outlook_email, parent_email, parent_phone FROM students WHERE id = ?', (person_id,)).fetchone()
        if student:
            try:
                from email_service import send_attendance_email_async, send_whatsapp_async
                emails = [student['outlook_email']]
                if student['parent_email']: emails.append(student['parent_email'])
                send_attendance_email_async(emails, student['name'], subject, status, time_str)
                send_whatsapp_async(student['parent_phone'], student['name'], subject, status, time_str)
            except: pass
            
    conn.close()
    return True

def get_attendance_report(date_filter=None):
    conn = get_db_connection()
    query = '''
        SELECT COALESCE(s.name, u.full_name) as display_name, 
               s.id as student_db_id, u.id as user_db_id,
               s.course, s.year, s.outlook_email, s.parent_email,
               a.date, a.time_in, a.subject, a.status, a.verification_photo,
               CASE WHEN s.id IS NOT NULL THEN 'Student' ELSE u.role END as person_role
        FROM attendance a
        LEFT JOIN students s ON a.student_id = s.id
        LEFT JOIN users u ON a.user_id = u.id
    '''
    params = []
    if date_filter:
        query += ' WHERE a.date = ?'
        params.append(date_filter)
    query += ' ORDER BY a.date DESC, a.time_in DESC'
    res = conn.execute(query, params).fetchall()
    conn.close()
    return res

# --- Leave Management ---
def apply_leave(student_id, start_date, end_date, reason):
    conn = get_db_connection()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn.execute('INSERT INTO leave_requests (student_id, start_date, end_date, reason, applied_on) VALUES (?, ?, ?, ?, ?)',
                 (student_id, start_date, end_date, reason, now))
    conn.commit()
    conn.close()

def get_student_leaves(student_id):
    conn = get_db_connection()
    res = conn.execute('SELECT * FROM leave_requests WHERE student_id = ? ORDER BY applied_on DESC', (student_id,)).fetchall()
    conn.close()
    return res

def get_all_leave_requests():
    conn = get_db_connection()
    res = conn.execute('SELECT lr.*, s.name as student_name FROM leave_requests lr JOIN students s ON lr.student_id = s.id ORDER BY lr.applied_on DESC').fetchall()
    conn.close()
    return res

def update_leave_status(request_id, status):
    conn = get_db_connection()
    conn.execute('UPDATE leave_requests SET status = ? WHERE id = ?', (status, request_id))
    conn.commit()
    conn.close()

# --- Announcement Functions ---
def add_announcement(user_id, title, content, target_course='All'):
    conn = get_db_connection()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cursor = conn.cursor()
    cursor.execute('INSERT INTO announcements (user_id, title, content, target_course, created_at) VALUES (?, ?, ?, ?, ?)',
                   (user_id, title, content, target_course, now))
    conn.commit()
    
    # Real-time popup for anyone with a dashboard open
    try:
        from realtime import emit
        user_name = cursor.execute('SELECT full_name FROM users WHERE id = ?', (user_id,)).fetchone()
        emit('new_announcement', {
            'title': title,
            'content': content,
            'author': user_name['full_name'] if user_name else "System",
            'course': target_course,
            'time': now
        })
    except: pass
    
    conn.close()

def get_announcements(course_filter=None, limit=5):
    conn = get_db_connection()
    query = 'SELECT a.*, u.full_name as author FROM announcements a JOIN users u ON a.user_id = u.id'
    params = []
    
    if course_filter:
        query += " WHERE a.target_course = 'All' OR a.target_course = ?"
        params.append(course_filter)
        
    query += ' ORDER BY a.created_at DESC LIMIT ?'
    params.append(limit)
    
    res = conn.execute(query, params).fetchall()
    conn.close()
    return res

# --- Stats ---
def get_stats():
    conn = get_db_connection()
    total = conn.execute('SELECT COUNT(*) FROM students').fetchone()[0]
    now = datetime.now().strftime("%Y-%m-%d")
    # Late still means they turned up, so it belongs in the headline count -
    # otherwise a student who was late to everything reads as absent all day.
    present = conn.execute("SELECT COUNT(DISTINCT student_id) FROM attendance WHERE date = ? AND status IN ('Present', 'Late')", (now,)).fetchone()[0]
    leave = conn.execute("SELECT COUNT(DISTINCT student_id) FROM attendance WHERE date = ? AND status = 'On Leave'", (now,)).fetchone()[0]
    conn.close()
    return {'total': total, 'present': present, 'leave': leave}

def get_student_stats(student_id):
    """Headline figures for one student.

    Counted the same way presence.student_subject_summary does it: Present,
    Late and On Leave all credit the student. Two different attendance
    percentages in one application is a support call waiting to happen.
    """
    conn = get_db_connection()
    counts = conn.execute(
        'SELECT status, COUNT(*) AS n FROM attendance WHERE student_id = ? GROUP BY status',
        (student_id,)).fetchall()
    conn.close()

    tally = {row['status']: row['n'] for row in counts}
    present = tally.get('Present', 0) + tally.get('Late', 0)
    leave = tally.get('On Leave', 0)
    absent = tally.get('Absent', 0) + tally.get('Partial', 0)

    held = present + leave + absent
    credited = present + leave
    return {'present': present, 'absent': absent, 'leave': leave,
            'percentage': round(credited / held * 100, 1) if held else 0}

def get_recent_attendance(limit=10):
    conn = get_db_connection()
    res = conn.execute('SELECT COALESCE(s.name, u.full_name) as name, a.time_in, a.subject, a.status FROM attendance a LEFT JOIN students s ON a.student_id = s.id LEFT JOIN users u ON a.user_id = u.id ORDER BY a.date DESC, a.time_in DESC LIMIT ?', (limit,)).fetchall()
    conn.close()
    return res

def get_student_attendance_history(student_id):
    conn = get_db_connection()
    res = conn.execute('SELECT * FROM attendance WHERE student_id = ? ORDER BY date DESC LIMIT 10', (student_id,)).fetchall()
    conn.close()
    return res

# --- Enterprise Analytics Functions ---
def get_attendance_trends(days=7):
    """Aggregates attendance data for the past N days for Chart.js trendlines."""
    conn = get_db_connection()
    # Using a simple approach compatible with both SQLite and Postgres
    query = '''
        SELECT date, 
               SUM(CASE WHEN status = 'Present' THEN 1 ELSE 0 END) as present_count,
               SUM(CASE WHEN status = 'Absent' THEN 1 ELSE 0 END) as absent_count,
               SUM(CASE WHEN status = 'On Leave' THEN 1 ELSE 0 END) as leave_count
        FROM attendance
        WHERE date >= date('now', ?)
        GROUP BY date
        ORDER BY date ASC
    '''
    res = conn.execute(query, (f'-{days} days',)).fetchall()
    conn.close()
    return [dict(row) for row in res]

def get_course_distribution():
    """Calculates attendance health per course for Chart.js Radar/Doughnut charts."""
    conn = get_db_connection()
    query = '''
        SELECT s.course, 
               COUNT(a.id) as total_records,
               SUM(CASE WHEN a.status = 'Present' THEN 1 ELSE 0 END) as present_records
        FROM students s
        LEFT JOIN attendance a ON s.id = a.student_id
        GROUP BY s.course
    '''
    res = conn.execute(query).fetchall()
    conn.close()
    
    distribution = []
    for row in res:
        health = (row['present_records'] / row['total_records'] * 100) if row['total_records'] > 0 else 0
        distribution.append({
            'course': row['course'],
            'health_percentage': round(health, 1)
        })
    return distribution

# --- Timetable ---
def add_timetable_entry(course, year, day, start, end, subject, teacher_id=None):
    conn = get_db_connection()
    conn.execute('INSERT INTO timetable (course, year, day_of_week, start_time, end_time, subject, teacher_id) VALUES (?, ?, ?, ?, ?, ?, ?)', (course, year, day, start, end, subject, teacher_id))
    conn.commit()
    conn.close()

def get_timetable_entries():
    conn = get_db_connection()
    res = conn.execute('SELECT t.*, u.full_name as teacher_name FROM timetable t LEFT JOIN users u ON t.teacher_id = u.id ORDER BY t.day_of_week, t.start_time').fetchall()
    conn.close()
    return res

def delete_timetable_entry(entry_id):
    conn = get_db_connection()
    conn.execute('DELETE FROM timetable WHERE id = ?', (entry_id,))
    conn.commit()
    conn.close()

# --- AI Auto-Repair System ---
def log_error_to_db(route, traceback_data):
    """Logs an unhandled exception for the AI watchdog to analyze."""
    conn = get_db_connection()
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn.execute('INSERT INTO error_logs (timestamp, route, traceback_data) VALUES (?, ?, ?)', (timestamp, route, traceback_data))
    conn.commit()
    conn.close()

def log_audit_trail(user_id, action, target_type, target_id, ip_address):
    """Enterprise Security: Logs sensitive actions for compliance."""
    conn = get_db_connection()
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn.execute('INSERT INTO audit_logs (user_id, action, target_type, target_id, timestamp, ip_address) VALUES (?, ?, ?, ?, ?, ?)',
                 (user_id, action, target_type, target_id, timestamp, ip_address))
    conn.commit()
    conn.close()

if __name__ == "__main__":
    init_db()
