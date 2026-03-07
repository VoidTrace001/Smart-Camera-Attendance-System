import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import threading
from datetime import datetime
from twilio.rest import Client

# --- Email Config ---
SMTP_SERVER = "smtp.office365.com"
SMTP_PORT = 587
SENDER_EMAIL = "your_email@outlook.com"
SENDER_PASSWORD = "your_password"

# --- Twilio WhatsApp Config ---
TWILIO_ACCOUNT_SID = 'your_account_sid'
TWILIO_AUTH_TOKEN = 'your_auth_token'
TWILIO_WHATSAPP_NUMBER = 'whatsapp:+14155238886' # Twilio Sandbox Number

def send_whatsapp_async(parent_phone, student_name, subject, status, time_in):
    def send():
        if not parent_phone: return
        
        # Format the number for WhatsApp (e.g. whatsapp:+919876543210)
        formatted_phone = f"whatsapp:{parent_phone}" if not parent_phone.startswith("whatsapp:") else parent_phone
        
        message_body = f"✅ Smart Attendance Alert\n\n{student_name} has been marked '{status}' for {subject} at {time_in}."
        
        print(f"Attempting to send WhatsApp to {formatted_phone}...")
        try:
            # Uncomment below to actually send when you add your real Twilio API Keys
            # client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
            # message = client.messages.create(
            #     from_=TWILIO_WHATSAPP_NUMBER,
            #     body=message_body,
            #     to=formatted_phone
            # )
            print(f"[MOCK WHATSAPP SUCCESS] Message sent to {formatted_phone}")
        except Exception as e:
            print(f"[WHATSAPP ERROR] Failed: {e}")

    threading.Thread(target=send, daemon=True).start()

def send_attendance_email_async(recipient_emails, student_name, subject, status, time_in):
    def send():
        if not recipient_emails: return
        
        if isinstance(recipient_emails, str):
            recipient_emails = [recipient_emails]
            
        print(f"Attempting to send attendance email to {recipient_emails}...")
        
        msg = MIMEMultipart()
        msg['From'] = SENDER_EMAIL
        msg['To'] = ", ".join(recipient_emails)
        msg['Subject'] = f"Attendance Update: {student_name} - {subject}"
        
        body = f"""
        Hello,
        
        Attendance for {student_name} for the subject '{subject}' has been marked as {status} on {datetime.now().strftime('%Y-%m-%d')} at {time_in}.
        
        This is an automated notification from EThames Business School Smart Attendance System.
        """
        msg.attach(MIMEText(body, 'plain'))
        
        try:
            # server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
            # server.starttls()
            # server.login(SENDER_EMAIL, SENDER_PASSWORD)
            # server.send_message(msg)
            # server.quit()
            print(f"[MOCK EMAIL SUCCESS] Email sent to {recipient_emails}")
        except Exception as e:
            print(f"[EMAIL ERROR] Failed to send email: {e}")

    threading.Thread(target=send, daemon=True).start()
