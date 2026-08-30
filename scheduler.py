import os
from datetime import datetime
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
from database import get_all_students, get_student_stats, get_student_attendance_history, delete_student

def materialize_tomorrow():
    """Creates concrete class_sessions from the timetable for the coming day.

    Per-class absence is decided by attendance_engine.py when each session
    closes, so there is no end-of-day sweep any more - the old one wrote a
    single day-level Absent row per student regardless of their timetable.
    """
    from presence import materialize_sessions
    from datetime import timedelta
    tomorrow = (datetime.now() + timedelta(days=1)).date()
    created = materialize_sessions(tomorrow)
    print(f"[SCHEDULER] Materialised {created} class session(s) for {tomorrow}.")


def purge_expired_sightings():
    """Retention: raw sightings are evidence, not a permanent record."""
    from presence import purge_old_sightings
    removed = purge_old_sightings()
    print(f"[SCHEDULER] Purged {removed} expired sighting(s).")


def close_stale_sessions():
    """Safety net: finalise any session the engine missed (e.g. it was down)."""
    from presence import sessions_due_for_close, close_session
    for session_id in sessions_due_for_close():
        summary = close_session(session_id)
        print(f"[SCHEDULER] Recovered unclosed session {session_id}: {summary}")


from ai_services import predict_dropout_risk
from notification_hub import send_whatsapp_alert

# Create a folder to store PDFs if it doesn't exist
if not os.path.exists("reports"):
    os.makedirs("reports")

def purge_graduated_batches():
    """Enterprise Data Retention: Securely deletes student records 4 years after their batch year."""
    print("[SCHEDULER] Running Automated Data Retention Check...")
    students = get_all_students()
    current_year = datetime.now().year
    
    purged_count = 0
    for s in students:
        batch_year_str = s['year'].replace(' Batch', '')
        try:
            batch_year = int(batch_year_str)
            if current_year >= batch_year + 4:
                print(f"[DATA RETENTION] Purging graduated student: {s['name']} (Batch {batch_year})")
                delete_student(s['id'])
                purged_count += 1
        except ValueError:
            pass # Ignore if year isn't strictly a number
    print(f"[SCHEDULER] Retention complete. Purged {purged_count} outdated records.")

def run_ai_dropout_predictor():
    """Uses Gemini AI to predict dropout risks before they hit critical levels."""
    print("[SCHEDULER] Running AI Dropout Predictor...")
    students = get_all_students()
    
    for s in students:
        stats = get_student_stats(s['id'])
        if stats['percentage'] > 0 and stats['percentage'] <= 85.0: # Only analyze those slipping
            history = get_student_attendance_history(s['id'])
            # Convert SQLite Row objects to dicts for JSON serialization
            history_dicts = [dict(row) for row in history]
            student_profile = {"name": s['name'], "course": s['course'], "current_attendance": stats['percentage']}
            
            prediction = predict_dropout_risk(student_profile, history_dicts)
            
            if prediction.get('risk_level') == 'High':
                print(f"[AI ALERT] High Dropout Risk detected for {s['name']}: {prediction.get('ai_analysis')}")
                if s['parent_phone']:
                    send_whatsapp_alert(s['parent_phone'], f"URGENT: VeriVault AI has detected a high risk of attendance failure for {s['name']}. Please log in to the portal immediately. Reason: {prediction.get('ai_analysis')}")
    print("[SCHEDULER] AI Prediction cycle complete.")

def generate_warning_pdf(student_name, course, percentage, filename):
    c = canvas.Canvas(filename, pagesize=letter)
    width, height = letter

    # Header
    c.setFont("Helvetica-Bold", 16)
    from settings import get_settings
    institution = get_settings()['institution_name']
    c.drawString(50, height - 50, f"{institution} - Attendance Warning")
    
    c.setLineWidth(2)
    c.line(50, height - 60, width - 50, height - 60)

    # Body
    c.setFont("Helvetica", 12)
    c.drawString(50, height - 100, f"Date: {datetime.now().strftime('%Y-%m-%d')}")
    c.drawString(50, height - 130, f"To the Parents/Guardians of {student_name},")
    
    body_text = [
        "This is an official notification regarding the attendance record",
        f"of your ward enrolled in the {course} program.",
        "",
        "As per college regulations, students are required to maintain",
        "a minimum attendance of 75%.",
        "",
        f"Currently, the attendance percentage is: {percentage}%",
        "",
        "Please ensure regular attendance to avoid academic penalties."
    ]

    y_pos = height - 170
    for line in body_text:
        c.drawString(50, y_pos, line)
        y_pos -= 20

    # Signature
    c.drawString(50, y_pos - 40, "Sincerely,")
    c.drawString(50, y_pos - 60, "Principal / Head of Department")
    
    c.save()

def check_defaulters_and_warn():
    print("[SCHEDULER] Triggering Enterprise Background Tasks...")
    try:
        from tasks import generate_weekly_department_pdf, send_bulk_warning_emails
        generate_weekly_department_pdf.delay()
        send_bulk_warning_emails.delay()
        print("[SCHEDULER] Tasks successfully queued in Redis/Celery.")
    except Exception as e:
        print(f"[SCHEDULER ERROR] Failed to dispatch Celery tasks: {e}")


def init_scheduler(app):
    from apscheduler.schedulers.background import BackgroundScheduler
    scheduler = BackgroundScheduler()
    
    # Build tomorrow's class sessions just after midnight
    scheduler.add_job(func=materialize_tomorrow, trigger="cron", hour=0, minute=5)

    # Safety net for sessions the engine did not finalise
    scheduler.add_job(func=close_stale_sessions, trigger="cron", minute='*/30')

    # Retention sweep for raw sightings
    scheduler.add_job(func=purge_expired_sightings, trigger="cron", hour=3, minute=30)
    
    # Run every Friday at 17:00 (5 PM)
    scheduler.add_job(func=check_defaulters_and_warn, trigger="cron", day_of_week='fri', hour=17, minute=0)
    
    # Run AI Predictor every Wednesday at 18:00
    scheduler.add_job(func=run_ai_dropout_predictor, trigger="cron", day_of_week='wed', hour=18, minute=0)

    # Run Data Retention Purge once a year on July 1st
    scheduler.add_job(func=purge_graduated_batches, trigger="cron", month=7, day=1, hour=2, minute=0)
    
    scheduler.start()
    
    # Shut down the scheduler when exiting the app
    import atexit
    atexit.register(lambda: scheduler.shutdown())
