import google.generativeai as genai
import json
import re

# Use the same API key as the Watchdog
API_KEY = "AIzaSyAbkKURTHZoKU1gj4nhKTEzzBhn8Uzv53Y"
genai.configure(api_key=API_KEY)
model = genai.GenerativeModel('gemini-1.5-flash')

def predict_dropout_risk(student_data, attendance_history):
    """
    Analyzes a student's attendance pattern to predict dropout risk 
    before they hit the critical 75% threshold.
    """
    prompt = f"""
    You are an AI Academic Advisor. Analyze this student's data and recent attendance history.
    
    Student Profile: {json.dumps(student_data)}
    Recent Attendance (Last 10 days): {json.dumps(attendance_history)}
    
    Task:
    1. Determine the "risk_level" (Low, Medium, High) of this student falling below the 75% attendance requirement or dropping out.
    2. Provide a 2-sentence "ai_analysis" explaining the reasoning (e.g., "Trending downwards on Mondays").
    
    Return ONLY a JSON object with keys "risk_level" and "ai_analysis".
    """
    
    try:
        response = model.generate_content(prompt)
        json_str = re.search(r'\{.*\}', response.text, re.DOTALL).group()
        return json.loads(json_str)
    except Exception as e:
        print(f"Dropout Prediction Failed: {e}")
        return {"risk_level": "Unknown", "ai_analysis": "AI prediction unavailable at this time."}

def generate_smart_parent_report(student_data, stats):
    """
    Generates an empathetic, customized email body for parents.
    """
    prompt = f"""
    You are an Academic Liaison. Write a short, professional, and empathetic email to a parent 
    about their child's current attendance status.
    
    Student Name: {student_data['name']}
    Attendance Percentage: {stats['percentage']}%
    Present Days: {stats['present']}
    Absent Days: {stats['absent']}
    
    Guidelines:
    - If attendance is >85%, be highly encouraging and brief.
    - If 75-85%, be polite but remind them of the importance of consistency.
    - If <75%, express urgent concern regarding university regulations.
    - Do not include subject/greeting blocks, just the email body text.
    """
    
    try:
        response = model.generate_content(prompt)
        return response.text.strip()
    except Exception as e:
        print(f"Smart Report Generation Failed: {e}")
        return f"System Auto-Report: {student_data['name']} currently has {stats['percentage']}% attendance."
