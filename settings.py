"""
Institution settings — the things that differ between one college and the next.

The campus coordinates were written into app.py and again into the student
dashboard's JavaScript; the college name appeared in the ID card, the warning
PDF, the parent emails and the registration page. Six copies of two facts, so
deploying this anywhere else meant editing source.

They live in the database now, and an administrator edits them in the control
room.
"""
import logging

from database import get_db_connection, DB_TYPE

logger = logging.getLogger('VeriVaultAI')

DEFAULTS = {
    "institution_name": "EThames Business School",
    "institution_short": "EThames",
    "institution_affiliation": "Osmania University Affiliated",
    "campus_lat": "17.4300",
    "campus_lon": "78.4480",
    "geofence_radius_m": "300",
    "attendance_required_pct": "75",
}

NUMERIC = {"campus_lat", "campus_lon", "geofence_radius_m", "attendance_required_pct"}


def init_settings_schema():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('''
        CREATE TABLE IF NOT EXISTS institution_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    ''')
    for key, value in DEFAULTS.items():
        try:
            if DB_TYPE == "postgres":
                cur.execute('INSERT INTO institution_settings (key, value) VALUES (?, ?) '
                            'ON CONFLICT (key) DO NOTHING', (key, value))
            else:
                cur.execute('INSERT OR IGNORE INTO institution_settings (key, value) VALUES (?, ?)',
                            (key, value))
        except Exception:
            pass
    conn.commit()
    conn.close()


def get_settings():
    """Every setting, numbers already converted."""
    values = dict(DEFAULTS)
    try:
        conn = get_db_connection()
        rows = conn.execute('SELECT key, value FROM institution_settings').fetchall()
        conn.close()
        values.update({r['key']: r['value'] for r in rows})
    except Exception as e:
        logger.debug(f"Falling back to default institution settings: {e}")

    out = {}
    for key, value in values.items():
        if key in NUMERIC:
            try:
                out[key] = float(value)
            except (TypeError, ValueError):
                out[key] = float(DEFAULTS[key])
        else:
            out[key] = value
    return out


def set_setting(key, value):
    if key not in DEFAULTS:
        raise KeyError(f"Unknown setting: {key}")

    if key in NUMERIC:
        value = float(value)
        if key == 'campus_lat' and not -90 <= value <= 90:
            raise ValueError("Latitude must be between -90 and 90.")
        if key == 'campus_lon' and not -180 <= value <= 180:
            raise ValueError("Longitude must be between -180 and 180.")
        if key == 'geofence_radius_m' and not 10 <= value <= 20000:
            raise ValueError("The check-in radius must be between 10 m and 20 km.")
        if key == 'attendance_required_pct' and not 1 <= value <= 100:
            raise ValueError("The attendance requirement must be between 1% and 100%.")

    conn = get_db_connection()
    if DB_TYPE == "postgres":
        conn.execute('INSERT INTO institution_settings (key, value) VALUES (?, ?) '
                     'ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value', (key, str(value)))
    else:
        conn.execute('INSERT OR REPLACE INTO institution_settings (key, value) VALUES (?, ?)',
                     (key, str(value)))
    conn.commit()
    conn.close()
