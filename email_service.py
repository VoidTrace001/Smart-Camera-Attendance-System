"""
Outbound email.

This used to print "[MOCK EMAIL SUCCESS]" and return, with the SMTP calls
commented out and a placeholder password in the source. Nothing was ever sent,
and every caller was told it had been.

Now it sends. If SMTP is not configured it says so and records the attempt as
skipped rather than claiming success — a notification system that lies about
delivery is worse than one that does nothing.

Configure in .env:

    SMTP_HOST=smtp.office365.com
    SMTP_PORT=587
    SMTP_USER=attendance@yourcollege.edu
    SMTP_PASSWORD=an-app-password
    SMTP_FROM="College Attendance <attendance@yourcollege.edu>"
    SMTP_SECURITY=starttls        # starttls | ssl | none
"""
import logging
import os
import smtplib
import threading
from email.message import EmailMessage
from email.utils import formataddr, formatdate

logger = logging.getLogger('VeriVaultAI')

SMTP_HOST = os.environ.get('SMTP_HOST', '')
SMTP_PORT = int(os.environ.get('SMTP_PORT', '587'))
SMTP_USER = os.environ.get('SMTP_USER', '')
SMTP_PASSWORD = os.environ.get('SMTP_PASSWORD', '')
SMTP_SECURITY = os.environ.get('SMTP_SECURITY', 'starttls').lower()
SMTP_TIMEOUT = int(os.environ.get('SMTP_TIMEOUT', '20'))

_DEFAULT_FROM = SMTP_FROM = os.environ.get('SMTP_FROM') or SMTP_USER


def is_configured():
    return bool(SMTP_HOST and SMTP_USER and SMTP_PASSWORD)


def _normalise(recipients):
    if isinstance(recipients, str):
        recipients = [recipients]
    seen, out = set(), []
    for address in recipients or []:
        address = (address or '').strip()
        if address and '@' in address and address.lower() not in seen:
            seen.add(address.lower())
            out.append(address)
    return out


def send_email(recipients, subject, body, html=None):
    """Sends one message. Returns (sent, detail). Blocking - see the _async wrappers."""
    from notification_hub import record_notification

    recipients = _normalise(recipients)
    if not recipients:
        return False, "No valid recipient address"

    if not is_configured():
        detail = "SMTP is not configured (set SMTP_HOST, SMTP_USER, SMTP_PASSWORD)"
        logger.warning(f"Email to {recipients} skipped: {detail}")
        record_notification('email', ', '.join(recipients), subject, 'skipped', detail)
        return False, detail

    message = EmailMessage()
    message['Subject'] = subject
    message['From'] = _DEFAULT_FROM if '<' in _DEFAULT_FROM else formataddr(('Attendance', _DEFAULT_FROM))
    message['To'] = ', '.join(recipients)
    message['Date'] = formatdate(localtime=True)
    message.set_content(body)
    if html:
        message.add_alternative(html, subtype='html')

    try:
        if SMTP_SECURITY == 'ssl':
            server = smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=SMTP_TIMEOUT)
        else:
            server = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=SMTP_TIMEOUT)

        with server:
            if SMTP_SECURITY == 'starttls':
                server.starttls()
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.send_message(message)

        logger.info(f"Email sent to {recipients}: {subject}")
        record_notification('email', ', '.join(recipients), subject, 'sent', None)
        return True, "sent"

    except smtplib.SMTPAuthenticationError as e:
        detail = f"SMTP rejected the login ({e.smtp_code}). App passwords are usually required."
    except smtplib.SMTPRecipientsRefused:
        detail = "Every recipient address was refused by the server"
    except Exception as e:
        detail = f"{type(e).__name__}: {e}"

    logger.error(f"Email to {recipients} failed: {detail}")
    record_notification('email', ', '.join(recipients), subject, 'failed', detail)
    return False, detail


def _in_background(fn, *args):
    threading.Thread(target=fn, args=args, daemon=True).start()


def send_attendance_email_async(recipient_emails, student_name, subject, status, time_in):
    """Tells a student and their parent that attendance was recorded."""
    from settings import get_settings
    institution = get_settings()['institution_name']

    line = f"{student_name} was marked {status} for {subject}"
    if time_in and time_in != 'N/A':
        line += f" at {time_in}"

    body = (
        f"Hello,\n\n{line}.\n\n"
        f"You can see the full record, class by class, by signing in to the "
        f"{institution} attendance portal.\n\n"
        f"This message was sent automatically. Please do not reply to it.\n"
    )
    _in_background(send_email, recipient_emails,
                   f"Attendance update: {student_name} - {subject}", body)


def send_whatsapp_async(parent_phone, student_name, subject, status, time_in):
    """Kept for callers that predate notification_hub; delegates to the real sender."""
    from notification_hub import send_whatsapp_alert
    if not parent_phone:
        return
    message = f"{student_name} was marked {status} for {subject}"
    if time_in and time_in != 'N/A':
        message += f" at {time_in}"
    _in_background(send_whatsapp_alert, parent_phone, message + ".")
