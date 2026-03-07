import os
from twilio.rest import Client

# Twilio Configuration (Placeholders - need to be set in environment or replaced with actuals)
TWILIO_ACCOUNT_SID = os.environ.get('TWILIO_ACCOUNT_SID', 'AC_placeholder_sid')
TWILIO_AUTH_TOKEN = os.environ.get('TWILIO_AUTH_TOKEN', 'placeholder_token')
TWILIO_WHATSAPP_SENDER = 'whatsapp:+14155238886' # Default Twilio Sandbox number

def send_whatsapp_alert(to_number, message_body):
    """
    Sends a real WhatsApp message using Twilio API.
    Note: to_number must be in E.164 format, e.g., '+919876543210'
    """
    if 'placeholder' in TWILIO_ACCOUNT_SID:
        print(f"[Simulation] Twilio WhatsApp to {to_number}: {message_body}")
        return True

    try:
        client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        message = client.messages.create(
            from_=TWILIO_WHATSAPP_SENDER,
            body=message_body,
            to=f'whatsapp:{to_number}'
        )
        print(f"[Twilio] WhatsApp sent to {to_number}. SID: {message.sid}")
        return True
    except Exception as e:
        print(f"[Twilio Error] Failed to send WhatsApp: {e}")
        return False
