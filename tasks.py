import os
from celery import Celery
from datetime import datetime
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
from database import get_all_students, get_student_stats, get_course_distribution
import logging

# Configure Celery
# We use Redis as the message broker. Ensure Redis is running locally on port 6379.
celery_app = Celery('verivault_tasks', broker='redis://localhost:6379/0')
celery_app.conf.update(
    result_backend='redis://localhost:6379/0',
    task_serializer='json',
    accept_content=['json'],
    result_serializer='json',
    timezone='UTC',
    enable_utc=True,
)

logger = logging.getLogger('VeriVaultAI')

@celery_app.task
def generate_weekly_department_pdf():
    """Generates a highly visual PDF report for department heads."""
    logger.info("Starting background task: Generating Weekly Department Report")
    
    if not os.path.exists("reports"):
        os.makedirs("reports")
        
    filename = f"reports/Weekly_Enterprise_Report_{datetime.now().strftime('%Y%m%d')}.pdf"
    
    # 1. Gather Data
    distribution = get_course_distribution()
    students = get_all_students()
    
    # 2. Build PDF Document
    c = canvas.Canvas(filename, pagesize=letter)
    width, height = letter

    # Header
    c.setFillColorRGB(0.31, 0.27, 0.90) # Primary Indio Color
    c.rect(0, height - 80, width, 80, fill=1)
    
    c.setFillColorRGB(1, 1, 1)
    c.setFont("Helvetica-Bold", 24)
    c.drawString(40, height - 50, "VERIVAULT AI - ENTERPRISE SYSTEMS")
    
    c.setFont("Helvetica", 12)
    c.drawString(40, height - 70, f"Weekly Institutional Health Report | Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}")

    # Body - Departmental Health
    c.setFillColorRGB(0, 0, 0)
    c.setFont("Helvetica-Bold", 16)
    y_pos = height - 120
    c.drawString(40, y_pos, "Departmental Health Overview")
    y_pos -= 30
    
    c.setFont("Helvetica", 12)
    for dept in distribution:
        health = dept['health_percentage']
        color = (0.06, 0.72, 0.50) if health >= 75 else (0.93, 0.26, 0.26) # Green or Red
        c.setFillColorRGB(*color)
        c.drawString(60, y_pos, f"• {dept['course']}: {health}% Attendance Compliance")
        y_pos -= 20

    # Body - Critical Defaulters
    y_pos -= 20
    c.setFillColorRGB(0, 0, 0)
    c.setFont("Helvetica-Bold", 16)
    c.drawString(40, y_pos, "Critical Defaulter Watchlist (< 75%)")
    y_pos -= 30
    
    c.setFont("Helvetica", 10)
    c.setFillColorRGB(0.4, 0.4, 0.4)
    defaulter_count = 0
    
    for s in students:
        stats = get_student_stats(s['id'])
        if 0 < stats['percentage'] < 75.0:
            c.drawString(60, y_pos, f"- {s['name']} ({s['course']}): {stats['percentage']}% [Missing: {stats['absent']} days]")
            y_pos -= 15
            defaulter_count += 1
            if y_pos < 50: # Page break
                c.showPage()
                y_pos = height - 50
                c.setFont("Helvetica", 10)
                
    if defaulter_count == 0:
         c.drawString(60, y_pos, "Zero critical defaulters found. Optimal health.")

    # Footer
    c.setFont("Helvetica-Oblique", 8)
    c.setFillColorRGB(0.5, 0.5, 0.5)
    c.drawString(40, 30, "Automated via VeriVault AI Smart Queue (Celery/Redis)")

    c.save()
    logger.info(f"Weekly report generated successfully: {filename}")
    
    return filename

@celery_app.task
def send_bulk_warning_emails():
    """Asynchronously sends warning emails to parents of all defaulters."""
    logger.info("Starting background task: Bulk Warning Emails")
    from ai_services import generate_smart_parent_report
    from email_service import send_attendance_email_async
    
    students = get_all_students()
    sent_count = 0
    
    for s in students:
        if s['parent_email']:
            stats = get_student_stats(s['id'])
            if 0 < stats['percentage'] < 75.0 and (stats['present'] + stats['absent']) > 5:
                # Use AI to generate email content
                student_data = {"name": s['name']}
                ai_email_body = generate_smart_parent_report(student_data, stats)
                
                # Send email (this function itself should ideally not block, but we are already in a celery worker)
                send_attendance_email_async(
                    s['parent_email'], 
                    s['name'], 
                    "OVERALL ATTENDANCE", 
                    f"CRITICAL WARNING (Below 75%).\n\nAI Advisor Note:\n{ai_email_body}", 
                    "N/A"
                )
                sent_count += 1
                
    logger.info(f"Bulk email dispatch complete. Sent {sent_count} warnings.")
    return sent_count
