import os
from reportlab.lib.pagesizes import A6
from reportlab.pdfgen import canvas
from reportlab.lib.units import mm
import qrcode

def generate_id_card(student_data, output_path):
    # ID Card Size (A6)
    width, height = A6
    c = canvas.Canvas(output_path, pagesize=A6)
    
    # Background Color (Subtle)
    c.setFillColorRGB(0.97, 0.98, 1.0)
    c.rect(0, 0, width, height, fill=1)
    
    # Header Accent
    c.setFillColorRGB(0.31, 0.27, 0.9) # VeriVault Indigo
    c.rect(0, height - 25*mm, width, 25*mm, fill=1)
    
    # College Logo Placeholder or Real Logo
    logo_path = os.path.join('static', 'images', 'college_logo.png')
    if os.path.exists(logo_path):
        c.drawImage(logo_path, 5*mm, height - 20*mm, width=40*mm, preserveAspectRatio=True, mask='auto')
    else:
        c.setFillColorRGB(1, 1, 1)
        c.setFont("Helvetica-Bold", 14)
        c.drawString(10*mm, height - 15*mm, "ETHAMES")
        c.setFont("Helvetica", 8)
        c.drawString(10*mm, height - 20*mm, "BUSINESS SCHOOL")

    # Student Photo
    photo_path = os.path.join('static', 'profiles', f"student_{student_data['id']}.jpg")
    if os.path.exists(photo_path):
        c.setStrokeColorRGB(1, 1, 1)
        c.setLineWidth(2)
        c.rect(width/2 - 20*mm, height/2 + 5*mm, 40*mm, 40*mm, stroke=1)
        c.drawImage(photo_path, width/2 - 20*mm, height/2 + 5*mm, width=40*mm, height=40*mm)

    # Student Details
    c.setFillColorRGB(0.06, 0.09, 0.16) # Dark Slate
    c.setFont("Helvetica-Bold", 16)
    c.drawCentredString(width/2, height/2 - 5*mm, student_data['name'].upper())
    
    c.setFont("Helvetica-Bold", 10)
    c.setFillColorRGB(0.39, 0.45, 0.55) # Gray
    c.drawCentredString(width/2, height/2 - 12*mm, f"{student_data['course']} | {student_data['year']}")
    
    # ID Details
    c.setFont("Helvetica", 9)
    c.setFillColorRGB(0.1, 0.1, 0.1)
    c.drawString(10*mm, 35*mm, f"OU ID: {student_data['ou_id']}")
    c.drawString(10*mm, 30*mm, f"EDC: {student_data['edc_number']}")
    
    # QR Code
    qr = qrcode.QRCode(box_size=2, border=0)
    qr.add_data(student_data['qr_hash'])
    qr.make(fit=True)
    qr_img = qr.make_image(fill_color="black", back_color="white")
    
    qr_temp_path = f"static/profiles/temp_qr_{student_data['id']}.png"
    qr_img.save(qr_temp_path)
    
    c.drawImage(qr_temp_path, width - 35*mm, 10*mm, width=25*mm, height=25*mm)
    os.remove(qr_temp_path)
    
    # Footer
    c.setFont("Helvetica-BoldOblique", 8)
    c.setFillColorRGB(0.31, 0.27, 0.9)
    c.drawCentredString(width/2, 5*mm, "VERIVAULT SECURE ID SYSTEM")
    
    c.save()
