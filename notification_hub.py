"""
WhatsApp and SMS delivery, and the record of everything the system sent.

Two things changed here. The Twilio call is real and env-driven rather than
guarded by an `if 'placeholder' in SID` simulation branch. And every message —
email included — is written to a notifications table, because "did the parent
actually get told" is a question staff will be asked and could not previously
answer.

Configure in .env:

    TWILIO_ACCOUNT_SID=ACxxxxxxxx
    TWILIO_AUTH_TOKEN=xxxxxxxx
    TWILIO_WHATSAPP_FROM=whatsapp:+14155238886   # sandbox number, or your own
    TWILIO_SMS_FROM=+1xxxxxxxxxx                 # optional, enables SMS fallback
"""
import logging
import os
import re
from datetime import datetime

logger = logging.getLogger('VeriVaultAI')

ACCOUNT_SID = os.environ.get('TWILIO_ACCOUNT_SID', '')
AUTH_TOKEN = os.environ.get('TWILIO_AUTH_TOKEN', '')
WHATSAPP_FROM = os.environ.get('TWILIO_WHATSAPP_FROM', 'whatsapp:+14155238886')
SMS_FROM = os.environ.get('TWILIO_SMS_FROM', '')
DEFAULT_COUNTRY_CODE = os.environ.get('DEFAULT_COUNTRY_CODE', '+91')

_client = None


def is_configured():
    return ACCOUNT_SID.startswith('AC') and bool(AUTH_TOKEN)


def _get_client():
    global _client
    if _client is None and is_configured():
        from twilio.rest import Client
        _client = Client(ACCOUNT_SID, AUTH_TOKEN)
    return _client


def normalise_phone(number):
    """Twilio needs E.164. Indian numbers are usually stored as ten digits."""
    if not number:
        return None
    cleaned = re.sub(r'[^\d+]', '', str(number))
    if cleaned.startswith('+'):
        return cleaned
    if len(cleaned) == 10:
        return f"{DEFAULT_COUNTRY_CODE}{cleaned}"
    if cleaned.startswith('00'):
        return f"+{cleaned[2:]}"
    return f"+{cleaned}" if cleaned else None


# ----------------------------------------------------------------------------
# Delivery record
# ----------------------------------------------------------------------------

def init_notification_schema():
    from database import get_db_connection, DB_TYPE
    serial = "SERIAL PRIMARY KEY" if DB_TYPE == "postgres" else "INTEGER PRIMARY KEY AUTOINCREMENT"
    conn = get_db_connection()
    conn.cursor().execute(f'''
        CREATE TABLE IF NOT EXISTS notifications (
            id {serial},
            channel TEXT NOT NULL,
            recipient TEXT,
            summary TEXT,
            status TEXT NOT NULL,
            detail TEXT,
            sent_at TEXT NOT NULL
        )
    ''')
    conn.commit()
    conn.close()


def record_notification(channel, recipient, summary, status, detail=None):
    """status: sent | failed | skipped. Never raises - logging must not break sending."""
    try:
        from database import get_db_connection
        conn = get_db_connection()
        conn.execute(
            'INSERT INTO notifications (channel, recipient, summary, status, detail, sent_at) '
            'VALUES (?, ?, ?, ?, ?, ?)',
            (channel, str(recipient)[:200], str(summary)[:300], status,
             str(detail)[:500] if detail else None,
             datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.debug(f"Could not record notification: {e}")


def recent_notifications(limit=200):
    from database import get_db_connection
    conn = get_db_connection()
    rows = conn.execute('SELECT * FROM notifications ORDER BY sent_at DESC, id DESC LIMIT ?',
                        (limit,)).fetchall()
    conn.close()
    return rows


def delivery_summary(days=7):
    """Counts per channel and status, for the control room."""
    from database import get_db_connection
    conn = get_db_connection()
    rows = conn.execute(
        "SELECT channel, status, COUNT(*) AS n FROM notifications "
        "WHERE sent_at >= datetime('now', ?) GROUP BY channel, status", (f'-{days} days',)).fetchall()
    conn.close()
    out = {}
    for r in rows:
        out.setdefault(r['channel'], {}).setdefault(r['status'], 0)
        out[r['channel']][r['status']] += r['n']
    return out


# ----------------------------------------------------------------------------
# Sending
# ----------------------------------------------------------------------------

def send_whatsapp_alert(to_number, message_body):
    """Returns (sent, detail). Falls back to SMS if WhatsApp is refused."""
    number = normalise_phone(to_number)
    if not number:
        record_notification('whatsapp', to_number, message_body, 'skipped', 'No phone number')
        return False, "No phone number"

    if not is_configured():
        detail = "Twilio is not configured (set TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN)"
        logger.warning(f"WhatsApp to {number} skipped: {detail}")
        record_notification('whatsapp', number, message_body, 'skipped', detail)
        return False, detail

    try:
        message = _get_client().messages.create(
            from_=WHATSAPP_FROM, body=message_body, to=f'whatsapp:{number}')
        logger.info(f"WhatsApp sent to {number} (SID {message.sid})")
        record_notification('whatsapp', number, message_body, 'sent', message.sid)
        return True, message.sid
    except Exception as e:
        detail = f"{type(e).__name__}: {e}"
        logger.error(f"WhatsApp to {number} failed: {detail}")
        record_notification('whatsapp', number, message_body, 'failed', detail)

        # A parent who never joined the WhatsApp sandbox can still get an SMS.
        if SMS_FROM:
            return send_sms_alert(number, message_body)
        return False, detail


def send_sms_alert(to_number, message_body):
    number = normalise_phone(to_number)
    if not (number and SMS_FROM and is_configured()):
        record_notification('sms', number, message_body, 'skipped', 'SMS sender not configured')
        return False, "SMS not configured"

    try:
        message = _get_client().messages.create(from_=SMS_FROM, body=message_body, to=number)
        logger.info(f"SMS sent to {number} (SID {message.sid})")
        record_notification('sms', number, message_body, 'sent', message.sid)
        return True, message.sid
    except Exception as e:
        detail = f"{type(e).__name__}: {e}"
        logger.error(f"SMS to {number} failed: {detail}")
        record_notification('sms', number, message_body, 'failed', detail)
        return False, detail
