import warnings
# Silence FutureWarnings to keep logs clean
warnings.simplefilter(action='ignore', category=FutureWarning)

from google import genai
from google.genai import types
import json
import re

# Use the same API key as the Watchdog
API_KEY = "here you need to paste your gemini api key"

# Configure client to use stable v1 API to avoid 404s on v1beta
client = genai.Client(api_key=API_KEY, http_options={'api_version': 'v1'})

# Global model variable to allow fallback
CURRENT_MODEL = 'gemini-1.5-flash'

def verify_ai_connectivity():
    """Tests the Gemini AI connection on startup with automatic fallback."""
    global CURRENT_MODEL
    models_to_try = ['gemini-1.5-flash', 'gemini-1.5-pro', 'gemini-2.0-flash-exp']
    
    for model_name in models_to_try:
        try:
            response = client.models.generate_content(
                model=model_name,
                contents="Confirm online."
            )
            if response.text:
                CURRENT_MODEL = model_name
                return True
        except Exception:
            continue
    return False

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
        response = client.models.generate_content(
            model=CURRENT_MODEL,
            contents=prompt
        )
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
        response = client.models.generate_content(
            model=CURRENT_MODEL,
            contents=prompt
        )
        return response.text.strip()
    except Exception as e:
        print(f"Smart Report Generation Failed: {e}")
        return f"System Auto-Report: {student_data['name']} currently has {stats['percentage']}% attendance."

def ask_database_ai(user_query):
    """
    Translates natural language into SQL, executes it safely, 
    and returns a human-readable response.
    """
    schema_context = """
    Database: SQLite
    Tables:
    - students (id, name, ou_id, edc_number, course, year, outlook_email)
    - users (id, username, full_name, role, department)
    - attendance (id, student_id, user_id, date, time_in, subject, status)
    - leave_requests (id, student_id, start_date, end_date, status, reason)
    - announcements (title, content, target_course, created_at)
    """

    prompt = f"""
    You are a VeriVault AI Data Analyst.
    Your task:
    1. Translate this user question into a VALID SQLite query: "{user_query}"
    2. Use the following schema: {schema_context}
    
    Rules:
    - Return ONLY a JSON object with two keys: "sql" (the query string) and "explanation" (what you are looking for).
    - Do NOT perform any DELETE, DROP, or UPDATE operations. SELECT only.
    - If the query is impossible based on schema, return an error message in "explanation".
    """

    try:
        # 1. Generate SQL
        response = client.models.generate_content(model=CURRENT_MODEL, contents=prompt)
        json_str = re.search(r'\{.*\}', response.text, re.DOTALL).group()
        ai_plan = json.loads(json_str)
        sql_query = ai_plan.get('sql')

        if not sql_query:
            return {"answer": ai_plan.get('explanation', "I'm sorry, I couldn't understand that data request.")}

        # 2. Execute SQL (Read-Only)
        from database import get_db_connection
        conn = get_db_connection()
        try:
            results = conn.execute(sql_query).fetchall()
            data_rows = [dict(row) for row in results]
        except Exception as sql_err:
            conn.close()
            return {"answer": f"AI generated an invalid query: {sql_err}"}
        conn.close()

        # 3. Summarize Results
        summary_prompt = f"""
        User Question: {user_query}
        SQL Used: {sql_query}
        Raw Results: {json.dumps(data_rows[:20])} (Truncated if too long)
        
        Task: Provide a concise, professional answer to the user based on these results. 
        If results are empty, say "No matching records found."
        """
        
        final_response = client.models.generate_content(model=CURRENT_MODEL, contents=summary_prompt)
        return {"answer": final_response.text.strip(), "sql": sql_query}

    except Exception as e:
        print(f"AI Database Assistant Failed: {e}")
        return {"answer": "The AI Data Analyst is currently offline. Please try again later."}
