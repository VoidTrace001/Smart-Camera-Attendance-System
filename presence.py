"""
Presence model: class sessions, sightings, presence intervals and verdicts.

The original attendance table recorded one row per student per subject per day,
written on first sighting and never revisited. That cannot express "was this
student actually in the room for the length of the class", so this module adds
the data model that can, and the rules that turn raw sightings into a verdict.
"""
from datetime import datetime, timedelta

from database import get_db_connection, DB_TYPE

TS_FMT = "%Y-%m-%d %H:%M:%S"

# Tunable rules. Seeded into attendance_config so an admin can change them
# without a deploy; every read goes through get_config().
DEFAULT_CONFIG = {
    "sample_interval_s": "2",     # how often the vision worker looks
    "bucket_s": "30",             # granularity of "seen"
    "gap_tolerance_s": "180",     # absence shorter than this doesn't break an interval
    "grace_minutes": "10",        # after this, arrival counts as Late
    "present_pct": "75",          # coverage at or above this is Present
    "partial_pct": "40",          # coverage at or above this is Partial, below is Absent
    "end_grace_minutes": "10",    # gone this long before the bell = left and didn't return
    "close_delay_s": "120",       # wait after end_ts before finalising
    "sighting_retention_days": "30",
}


def _now():
    return datetime.now()


def parse_ts(value):
    if isinstance(value, datetime):
        return value
    return datetime.strptime(str(value)[:19], TS_FMT)


def fmt_ts(dt):
    return dt.strftime(TS_FMT)


def _is_number(v):
    try:
        float(v)
        return True
    except (TypeError, ValueError):
        return False


# ----------------------------------------------------------------------------
# Schema
# ----------------------------------------------------------------------------

def init_presence_schema():
    conn = get_db_connection()
    cursor = conn.cursor()
    SERIAL = "SERIAL PRIMARY KEY" if DB_TYPE == "postgres" else "INTEGER PRIMARY KEY AUTOINCREMENT"

    cursor.execute(f'''
        CREATE TABLE IF NOT EXISTS cameras (
            id {SERIAL},
            name TEXT NOT NULL,
            room TEXT,
            source TEXT NOT NULL DEFAULT '0',
            course TEXT,
            year TEXT,
            active INTEGER DEFAULT 1
        )
    ''')

    # A timetable row is a recurring rule; a class_session is that rule
    # materialised for one specific date. Verdicts attach to sessions.
    cursor.execute(f'''
        CREATE TABLE IF NOT EXISTS class_sessions (
            id {SERIAL},
            timetable_id INTEGER,
            camera_id INTEGER,
            course TEXT NOT NULL,
            year TEXT NOT NULL,
            subject TEXT NOT NULL,
            teacher_id INTEGER,
            date TEXT NOT NULL,
            start_ts TEXT NOT NULL,
            end_ts TEXT NOT NULL,
            status TEXT DEFAULT 'scheduled',
            UNIQUE(timetable_id, date)
        )
    ''')

    cursor.execute(f'''
        CREATE TABLE IF NOT EXISTS sightings (
            id {SERIAL},
            session_id INTEGER NOT NULL,
            student_id INTEGER NOT NULL,
            camera_id INTEGER,
            ts TEXT NOT NULL,
            confidence REAL,
            box_x INTEGER,
            box_y INTEGER,
            box_w INTEGER,
            box_h INTEGER
        )
    ''')

    cursor.execute(f'''
        CREATE TABLE IF NOT EXISTS presence_intervals (
            id {SERIAL},
            session_id INTEGER NOT NULL,
            student_id INTEGER NOT NULL,
            entered_at TEXT NOT NULL,
            exited_at TEXT NOT NULL,
            duration_s INTEGER NOT NULL
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS attendance_config (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    ''')

    for stmt in (
        'CREATE INDEX IF NOT EXISTS idx_sightings_lookup ON sightings (session_id, student_id, ts)',
        'CREATE INDEX IF NOT EXISTS idx_intervals_lookup ON presence_intervals (session_id, student_id)',
        'CREATE INDEX IF NOT EXISTS idx_sessions_date ON class_sessions (date, start_ts)',
        'CREATE INDEX IF NOT EXISTS idx_sessions_status ON class_sessions (status)',
    ):
        try:
            cursor.execute(stmt)
        except Exception:
            pass

    # Add spatial seating box columns if missing
    for col, coltype in (('session_id', 'INTEGER'),
                         ('present_seconds', 'INTEGER'),
                         ('coverage_pct', 'REAL')):
        try:
            if DB_TYPE == "postgres":
                cursor.execute(f'ALTER TABLE attendance ADD COLUMN IF NOT EXISTS {col} {coltype}')
            else:
                cursor.execute(f'ALTER TABLE attendance ADD COLUMN {col} {coltype}')
        except Exception:
            if DB_TYPE == "postgres":
                conn.rollback()

    for col, coltype in (('box_x', 'INTEGER'), ('box_y', 'INTEGER'), ('box_w', 'INTEGER'), ('box_h', 'INTEGER')):
        try:
            if DB_TYPE == "postgres":
                cursor.execute(f'ALTER TABLE sightings ADD COLUMN IF NOT EXISTS {col} {coltype}')
            else:
                cursor.execute(f'ALTER TABLE sightings ADD COLUMN {col} {coltype}')
        except Exception:
            if DB_TYPE == "postgres":
                conn.rollback()

    for key, value in DEFAULT_CONFIG.items():
        try:
            if DB_TYPE == "postgres":
                cursor.execute('INSERT INTO attendance_config (key, value) VALUES (?, ?) '
                               'ON CONFLICT (key) DO NOTHING', (key, value))
            else:
                cursor.execute('INSERT OR IGNORE INTO attendance_config (key, value) VALUES (?, ?)',
                               (key, value))
        except Exception:
            pass

    conn.commit()
    conn.close()


# ----------------------------------------------------------------------------
# Config
# ----------------------------------------------------------------------------

def get_config():
    conn = get_db_connection()
    try:
        rows = conn.execute('SELECT key, value FROM attendance_config').fetchall()
        cfg = dict(DEFAULT_CONFIG)
        cfg.update({r['key']: r['value'] for r in rows})
    except Exception:
        cfg = dict(DEFAULT_CONFIG)
    finally:
        conn.close()
    return {k: (float(v) if _is_number(v) else v) for k, v in cfg.items()}


def set_config(key, value):
    conn = get_db_connection()
    if DB_TYPE == "postgres":
        conn.execute('INSERT INTO attendance_config (key, value) VALUES (?, ?) '
                     'ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value', (key, str(value)))
    else:
        conn.execute('INSERT OR REPLACE INTO attendance_config (key, value) VALUES (?, ?)',
                     (key, str(value)))
    conn.commit()
    conn.close()


# ----------------------------------------------------------------------------
# Cameras
# ----------------------------------------------------------------------------

def add_camera(name, room, source, course=None, year=None):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('INSERT INTO cameras (name, room, source, course, year, active) VALUES (?, ?, ?, ?, ?, 1)',
                (name, room, str(source), course, year))
    new_id = cur.lastrowid
    conn.commit()
    conn.close()
    return new_id


def get_cameras(active_only=False):
    conn = get_db_connection()
    q = 'SELECT * FROM cameras'
    if active_only:
        q += ' WHERE active = 1'
    rows = conn.execute(q + ' ORDER BY name').fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_camera(camera_id):
    conn = get_db_connection()
    row = conn.execute('SELECT * FROM cameras WHERE id = ?', (camera_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def delete_camera(camera_id):
    conn = get_db_connection()
    conn.execute('DELETE FROM cameras WHERE id = ?', (camera_id,))
    conn.commit()
    conn.close()


# ----------------------------------------------------------------------------
# Session materialisation
# ----------------------------------------------------------------------------

def materialize_sessions(target_date=None):
    """Expands timetable rules into concrete class_sessions for one date.

    Idempotent: the UNIQUE(timetable_id, date) constraint means re-running
    changes nothing. Without these rows there is nothing to mark someone
    absent against.
    """
    day = target_date or _now().date()
    date_str = day.strftime("%Y-%m-%d")
    weekday = day.strftime("%A")

    conn = get_db_connection()
    cursor = conn.cursor()
    entries = cursor.execute(
        'SELECT * FROM timetable WHERE day_of_week = ?', (weekday,)).fetchall()
    cameras = cursor.execute('SELECT * FROM cameras WHERE active = 1').fetchall()

    created = 0
    for e in entries:
        keys = e.keys() if hasattr(e, 'keys') else []
        camera_id = None
        for cam in cameras:
            cam_course, cam_year = cam['course'], cam['year']
            if cam_course in (None, '', e['course']) and cam_year in (None, '', e['year']):
                camera_id = cam['id']
                break

        start_ts = f"{date_str} {str(e['start_time'])[:5]}:00"
        end_ts = f"{date_str} {str(e['end_time'])[:5]}:00"
        try:
            cursor.execute(
                'INSERT INTO class_sessions (timetable_id, camera_id, course, year, subject, '
                'teacher_id, date, start_ts, end_ts, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                (e['id'], camera_id, e['course'], e['year'], e['subject'],
                 e['teacher_id'] if 'teacher_id' in keys else None,
                 date_str, start_ts, end_ts, 'scheduled'))
            created += 1
        except Exception:
            pass  # already materialised for this date

    conn.commit()
    conn.close()
    return created


def get_live_session_for_camera(camera_id, at=None):
    """The session this camera should be watching right now, if any."""
    now = at or _now()
    conn = get_db_connection()
    row = conn.execute(
        'SELECT * FROM class_sessions WHERE camera_id = ? AND start_ts <= ? AND end_ts > ? '
        "AND status != 'closed' ORDER BY start_ts LIMIT 1",
        (camera_id, fmt_ts(now), fmt_ts(now))).fetchone()
    conn.close()
    return dict(row) if row else None


def get_sessions_for_date(date_str, course=None, year=None, teacher_id=None):
    conn = get_db_connection()
    q = 'SELECT * FROM class_sessions WHERE date = ?'
    params = [date_str]
    if course:
        q += ' AND course = ?'
        params.append(course)
    if year:
        q += ' AND year = ?'
        params.append(year)
    if teacher_id:
        q += ' AND teacher_id = ?'
        params.append(teacher_id)
    rows = conn.execute(q + ' ORDER BY start_ts', params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_session(session_id):
    conn = get_db_connection()
    row = conn.execute('SELECT * FROM class_sessions WHERE id = ?', (session_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def set_session_status(session_id, status):
    conn = get_db_connection()
    conn.execute('UPDATE class_sessions SET status = ? WHERE id = ?', (status, session_id))
    conn.commit()
    conn.close()


def roster_for_session(session_id):
    """Everyone expected in the room - the basis for marking no-shows absent."""
    s = get_session(session_id)
    if not s:
        return []
    conn = get_db_connection()
    rows = conn.execute(
        'SELECT id, name, ou_id, edc_number, outlook_email, parent_email, parent_phone '
        'FROM students WHERE course = ? AND year = ? ORDER BY name',
        (s['course'], s['year'])).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ----------------------------------------------------------------------------
# Sightings
# ----------------------------------------------------------------------------

def record_sightings(rows):
    """Bulk-insert sightings. rows: (session_id, student_id, camera_id, ts, confidence, [box_x, box_y, box_w, box_h])"""
    if not rows:
        return 0
    conn = get_db_connection()
    cur = conn.cursor()
    norm_rows = []
    for r in rows:
        if len(r) == 9:
            norm_rows.append(r)
        else:
            norm_rows.append((r[0], r[1], r[2], r[3], r[4], None, None, None, None))
    cur.executemany(
        'INSERT INTO sightings (session_id, student_id, camera_id, ts, confidence, box_x, box_y, box_w, box_h) '
        'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)', norm_rows)
    conn.commit()
    conn.close()
    return len(norm_rows)


def purge_old_sightings():
    cfg = get_config()
    cutoff = _now() - timedelta(days=int(cfg['sighting_retention_days']))
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('DELETE FROM sightings WHERE ts < ?', (fmt_ts(cutoff),))
    removed = cur.rowcount
    conn.commit()
    conn.close()
    return removed


# ----------------------------------------------------------------------------
# Interval building - the core of the presence rule
# ----------------------------------------------------------------------------

def build_intervals(session_id, cfg=None):
    """Collapses raw sightings into presence intervals.

    Consecutive sightings merge into one interval as long as the gap between
    them stays under gap_tolerance_s. A gap longer than that ends the interval -
    that is precisely how "left the room and didn't come back" gets detected.
    """
    cfg = cfg or get_config()
    gap = int(cfg['gap_tolerance_s'])
    bucket = int(cfg['bucket_s'])

    conn = get_db_connection()
    rows = conn.execute(
        'SELECT student_id, ts FROM sightings WHERE session_id = ? ORDER BY student_id, ts',
        (session_id,)).fetchall()

    by_student = {}
    for r in rows:
        by_student.setdefault(r['student_id'], []).append(parse_ts(r['ts']))

    intervals = []
    for student_id, stamps in by_student.items():
        start = last = stamps[0]
        for ts in stamps[1:]:
            if (ts - last).total_seconds() <= gap:
                last = ts
            else:
                intervals.append((session_id, student_id, fmt_ts(start), fmt_ts(last),
                                  int((last - start).total_seconds()) + bucket))
                start = last = ts
        intervals.append((session_id, student_id, fmt_ts(start), fmt_ts(last),
                          int((last - start).total_seconds()) + bucket))

    cur = conn.cursor()
    cur.execute('DELETE FROM presence_intervals WHERE session_id = ?', (session_id,))
    if intervals:
        cur.executemany(
            'INSERT INTO presence_intervals (session_id, student_id, entered_at, exited_at, duration_s) '
            'VALUES (?, ?, ?, ?, ?)', intervals)
    conn.commit()
    conn.close()
    return len(intervals)


def get_intervals(session_id, student_id=None):
    conn = get_db_connection()
    q = 'SELECT * FROM presence_intervals WHERE session_id = ?'
    params = [session_id]
    if student_id:
        q += ' AND student_id = ?'
        params.append(student_id)
    rows = conn.execute(q + ' ORDER BY student_id, entered_at', params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def classify(coverage_pct, first_seen, last_seen, session_start, session_end, cfg):
    """coverage + arrival + departure -> Present / Late / Partial / Absent.

    Coverage on its own cannot tell "arrived late but stayed" apart from
    "turned up then walked out", and those are not the same thing. A student
    last seen well before the bell is Absent for that class however much of the
    earlier part they sat through - the went-out-and-never-came-back case,
    handled explicitly rather than left to the coverage bands.
    """
    if first_seen is None:
        return 'Absent'

    left_early = last_seen is not None and \
        (session_end - last_seen).total_seconds() > float(cfg['end_grace_minutes']) * 60
    if left_early:
        return 'Absent'

    if coverage_pct >= float(cfg['present_pct']):
        if (first_seen - session_start).total_seconds() > float(cfg['grace_minutes']) * 60:
            return 'Late'
        return 'Present'
    if coverage_pct >= float(cfg['partial_pct']):
        return 'Partial'
    return 'Absent'


def _approved_leave(conn, student_id, date_str):
    row = conn.execute(
        "SELECT id FROM leave_requests WHERE student_id = ? AND status = 'Approved' "
        "AND start_date <= ? AND end_date >= ?", (student_id, date_str, date_str)).fetchone()
    return row is not None


def compute_session_results(session_id, cfg=None):
    """Coverage and verdict for every enrolled student. Read-only."""
    cfg = cfg or get_config()
    s = get_session(session_id)
    if not s:
        return []

    start = parse_ts(s['start_ts'])
    end = parse_ts(s['end_ts'])
    duration = max((end - start).total_seconds(), 1)

    per_student = {}
    for iv in get_intervals(session_id):
        acc = per_student.setdefault(iv['student_id'], {'seconds': 0, 'first': None, 'last': None})
        acc['seconds'] += iv['duration_s']
        entered, exited = parse_ts(iv['entered_at']), parse_ts(iv['exited_at'])
        acc['first'] = entered if acc['first'] is None else min(acc['first'], entered)
        acc['last'] = exited if acc['last'] is None else max(acc['last'], exited)

    conn = get_db_connection()
    results = []
    for student in roster_for_session(session_id):
        acc = per_student.get(student['id'], {'seconds': 0, 'first': None, 'last': None})
        seconds = min(acc['seconds'], duration)
        coverage = round(seconds / duration * 100, 1)

        if _approved_leave(conn, student['id'], s['date']):
            status = 'On Leave'
        else:
            status = classify(coverage, acc['first'], acc['last'], start, end, cfg)

        results.append({
            'student_id': student['id'],
            'name': student['name'],
            'present_seconds': int(seconds),
            'coverage_pct': coverage,
            'status': status,
            'first_seen': fmt_ts(acc['first']) if acc['first'] else None,
            'last_seen': fmt_ts(acc['last']) if acc['last'] else None,
        })
    conn.close()
    return results


def close_session(session_id, cfg=None):
    """Finalise a session: write one attendance verdict per enrolled student."""
    cfg = cfg or get_config()
    s = get_session(session_id)
    if not s:
        return {'written': 0}

    build_intervals(session_id, cfg)
    results = compute_session_results(session_id, cfg)

    conn = get_db_connection()
    cur = conn.cursor()
    written = 0
    for r in results:
        existing = cur.execute(
            'SELECT id FROM attendance WHERE session_id = ? AND student_id = ?',
            (session_id, r['student_id'])).fetchone()
        if existing:
            cur.execute(
                'UPDATE attendance SET status = ?, present_seconds = ?, coverage_pct = ?, '
                'subject = ? WHERE id = ?',
                (r['status'], r['present_seconds'], r['coverage_pct'], s['subject'], existing['id']))
        else:
            time_in = r['first_seen'][11:19] if r['first_seen'] else str(s['start_ts'])[11:19]
            cur.execute(
                'INSERT INTO attendance (student_id, session_id, date, time_in, subject, status, '
                'marked_by_user_id, present_seconds, coverage_pct) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
                (r['student_id'], session_id, s['date'], time_in, s['subject'], r['status'],
                 s['teacher_id'], r['present_seconds'], r['coverage_pct']))
        written += 1

    cur.execute("UPDATE class_sessions SET status = 'closed' WHERE id = ?", (session_id,))
    conn.commit()
    conn.close()

    summary = {'written': written, 'session_id': session_id, 'subject': s['subject']}
    for st in ('Present', 'Late', 'Partial', 'Absent', 'On Leave'):
        summary[st] = sum(1 for r in results if r['status'] == st)
    return summary


def sessions_due_for_close(at=None):
    cfg = get_config()
    now = at or _now()
    cutoff = now - timedelta(seconds=int(cfg['close_delay_s']))
    conn = get_db_connection()
    rows = conn.execute(
        "SELECT id FROM class_sessions WHERE status != 'closed' AND end_ts <= ?",
        (fmt_ts(cutoff),)).fetchall()
    conn.close()
    return [r['id'] for r in rows]


# ----------------------------------------------------------------------------
# Dashboard queries
# ----------------------------------------------------------------------------

STATUS_ORDER = ['Present', 'Late', 'Partial', 'Absent', 'On Leave']


def student_day_breakdown(student_id, date_str):
    """Every scheduled class for a student on one day, with the verdict."""
    conn = get_db_connection()
    student = conn.execute('SELECT course, year FROM students WHERE id = ?', (student_id,)).fetchone()
    if not student:
        conn.close()
        return []

    rows = conn.execute(
        '''SELECT cs.id AS session_id, cs.subject, cs.start_ts, cs.end_ts, cs.status AS session_status,
                  a.status, a.coverage_pct, a.present_seconds, a.time_in
           FROM class_sessions cs
           LEFT JOIN attendance a ON a.session_id = cs.id AND a.student_id = ?
           WHERE cs.course = ? AND cs.year = ? AND cs.date = ?
           ORDER BY cs.start_ts''',
        (student_id, student['course'], student['year'], date_str)).fetchall()
    conn.close()

    out = []
    for r in rows:
        d = dict(r)
        d['start'] = str(d['start_ts'])[11:16]
        d['end'] = str(d['end_ts'])[11:16]
        if d['status'] is None:
            d['status'] = 'Scheduled' if d['session_status'] != 'closed' else 'Absent'
        d['coverage_pct'] = d['coverage_pct'] if d['coverage_pct'] is not None else 0
        out.append(d)
    return out


def student_calendar(student_id, start_date, end_date):
    """Per-day roll-up for the calendar view."""
    conn = get_db_connection()
    rows = conn.execute(
        '''SELECT date, status, COUNT(*) AS n FROM attendance
           WHERE student_id = ? AND date BETWEEN ? AND ?
           GROUP BY date, status''',
        (student_id, start_date, end_date)).fetchall()
    conn.close()

    days = {}
    for r in rows:
        day = days.setdefault(str(r['date']), {s: 0 for s in STATUS_ORDER})
        if r['status'] in day:
            day[r['status']] = r['n']

    for day, counts in days.items():
        held = sum(counts.values())
        credited = counts['Present'] + counts['Late'] + counts['On Leave']
        counts['total'] = held
        counts['pct'] = round(credited / held * 100, 1) if held else 0
    return days


REQUIRED_PCT = 75.0


def attendance_budget(held, credited, required=REQUIRED_PCT):
    """How much room a student has left against the attendance requirement.

    A percentage tells someone they are at 78% but not what to do about it.
    This answers the question they actually have: how many more can I miss,
    or how many in a row do I need to claw this back?

    Returns (can_miss, must_attend) - one of them is always zero.
    """
    if not held:
        return 0, 0

    ratio = required / 100.0
    if credited >= held * ratio:
        # credited / (held + m) >= ratio
        return int(credited / ratio) - held, 0

    # (credited + n) / (held + n) >= ratio
    needed = (ratio * held - credited) / (1 - ratio)
    return 0, int(-(-needed // 1))  # ceil, without importing math for one call


def student_subject_summary(student_id):
    """Attendance percentage per subject, against the 75% requirement."""
    conn = get_db_connection()
    rows = conn.execute(
        '''SELECT subject,
                  COUNT(*) AS held,
                  SUM(CASE WHEN status IN ('Present','Late','On Leave') THEN 1 ELSE 0 END) AS credited
           FROM attendance
           WHERE student_id = ? AND subject IS NOT NULL
           GROUP BY subject ORDER BY subject''',
        (student_id,)).fetchall()
    conn.close()

    out = []
    for r in rows:
        held = r['held'] or 0
        credited = r['credited'] or 0
        pct = round(credited / held * 100, 1) if held else 0
        can_miss, must_attend = attendance_budget(held, credited)
        out.append({'subject': r['subject'], 'held': held, 'credited': credited,
                    'percentage': pct, 'at_risk': pct < REQUIRED_PCT,
                    'can_miss': can_miss, 'must_attend': must_attend})
    return out


def live_session_state(session_id, seen_within_s=180):
    """Who is in the room right now, for the teacher's live roster."""
    session = get_session(session_id)
    if not session:
        return None

    build_intervals(session_id)
    results = compute_session_results(session_id)
    now = _now()

    for r in results:
        last = parse_ts(r['last_seen']) if r['last_seen'] else None
        r['in_room'] = bool(last and (now - last).total_seconds() < seen_within_s)
        r['last_seen_ago'] = int((now - last).total_seconds()) if last else None

    results.sort(key=lambda r: (not r['in_room'], -r['coverage_pct'], r['name']))
    return {
        'session': session,
        'students': results,
        'present_now': sum(1 for r in results if r['in_room']),
        'roster': len(results),
    }


def session_overview(date_str, teacher_id=None):
    """Sessions for a date with a headline count per status."""
    sessions = get_sessions_for_date(date_str, teacher_id=teacher_id)
    conn = get_db_connection()
    for s in sessions:
        rows = conn.execute(
            'SELECT status, COUNT(*) AS n FROM attendance WHERE session_id = ? GROUP BY status',
            (s['id'],)).fetchall()
        counts = {st: 0 for st in STATUS_ORDER}
        for r in rows:
            if r['status'] in counts:
                counts[r['status']] = r['n']
        s['counts'] = counts
        s['start'] = str(s['start_ts'])[11:16]
        s['end'] = str(s['end_ts'])[11:16]
    conn.close()
    return sessions


def camera_health():
    """Camera rows joined with their last heartbeat."""
    import bus
    out = []
    for cam in get_cameras():
        beat = bus.get_heartbeat(cam['id'])
        cam['heartbeat'] = beat
        cam['online'] = beat is not None
        out.append(cam)
    return out
