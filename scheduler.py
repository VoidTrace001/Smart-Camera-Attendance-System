import os
from datetime import datetime
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
from database import get_all_students, get_student_stats, get_student_attendance_history, delete_student
from email_service import send_attendance_email_async
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
    c.drawString(50, height - 50, "EThames Business School - Attendance Warning")
    
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
    print("[SCHEDULER] Running weekly defaulter check...")
    students = get_all_students()
    
    for s in students:
        stats = get_student_stats(s['id'])
        
        # Only warn if they have actually missed classes (don't warn on day 1)
        if stats['percentage'] < 75.0 and (stats['present'] + stats['absent']) > 5:
            print(f"Warning triggered for {s['name']} ({stats['percentage']}%)")
            
            pdf_filename = f"reports/Warning_{s['name'].replace(' ', '_')}_{datetime.now().strftime('%Y%m%d')}.pdf"
            generate_warning_pdf(s['name'], s['course'], stats['percentage'], pdf_filename)
            
            # Send Email Alert
            if s['parent_email']:
                from ai_services import generate_smart_parent_report
                from email_service import send_attendance_email_async
                
                # Use Gemini AI to write a personalized parent report
                student_data = {"name": s['name']}
                ai_email_body = generate_smart_parent_report(student_data, stats)
                
                send_attendance_email_async(
                    s['parent_email'], 
                    s['name'], 
                    "OVERALL ATTENDANCE", 
                    f"CRITICAL WARNING (Below 75%).\n\nAI Advisor Note:\n{ai_email_body}", 
                    "N/A"
                )
    print("[SCHEDULER] Check complete.")

def init_scheduler(app):
    from apscheduler.schedulers.background import BackgroundScheduler
    scheduler = BackgroundScheduler()
    
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
