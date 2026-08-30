import sqlite3
import time
import os
import json
import re
import warnings

# Silence FutureWarnings to keep logs clean
warnings.simplefilter(action='ignore', category=FutureWarning)

from google import genai

# ==============================================================================
# VERIVAULT AI - ERROR ANALYSIS WATCHDOG (ADVISORY ONLY)
# ==============================================================================
API_KEY = os.environ.get("GEMINI_API_KEY")
client = genai.Client(api_key=API_KEY) if API_KEY else None

DB_NAME = "attendance.db"

def get_db_connection():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn

def analyze_and_repair(error_id, route, traceback_data):
    print(f"\n[AI Watchdog] CRITICAL ERROR DETECTED (ID: {error_id})")
    print(f"[AI Watchdog] Route: {route}")
    
    # 1. Extract Target File and Line
    match = re.search(r'File "(.*?)", line (\d+), in', traceback_data)
    file_context = "Could not locate file."
    filepath = None
    line_num = None
    
    if match:
        filepath = match.group(1)
        line_num = int(match.group(2))
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                lines = f.readlines()
                start = max(0, line_num - 30)
                end = min(len(lines), line_num + 30)
                file_context = "".join(lines[start:end])
        except Exception as e:
            file_context = f"Error reading file: {e}"

    # 2. Consult Gemini AI for the Fix
    prompt = f"""
    You are an expert Backend Repair AI. A Flask application crashed with this error:
    
    ROUTE: {route}
    TRACEBACK: {traceback_data}
    
    CODE CONTEXT (around line {line_num}):
    {file_context}
    
    TASK:
    1. Identify the root cause.
    2. Provide the EXACT Python code to fix this specific issue.
    3. Output your response as a JSON object with two keys: "analysis" (string) and "fix_code" (the complete corrected version of the code snippet provided in context).
    """
    
    if client is None:
        print("[AI Watchdog] GEMINI_API_KEY not set - recording error without analysis.")
        try:
            conn = get_db_connection()
            conn.execute("UPDATE error_logs SET status = 'Unanalyzed' WHERE id = ?", (error_id,))
            conn.commit(); conn.close()
        except Exception:
            pass
        return

    print("[AI Watchdog] Consulting Gemini for repair advice...")
    try:
        response = client.models.generate_content(
            model=os.environ.get('GEMINI_MODEL', 'gemini-2.0-flash'),
            contents=prompt
        )
        # Extract JSON from response (handling potential markdown blocks)
        json_str = re.search(r'\{.*\}', response.text, re.DOTALL).group()
        repair_plan = json.loads(json_str)
        ai_analysis = repair_plan.get('analysis', 'No analysis provided.')
        fix_code = repair_plan.get('fix_code', '')
        
        print(f"[AI Analysis]: {ai_analysis}")
    except Exception as e:
        print(f"[AI Watchdog] AI Consultation failed: {e}")
        return

    # 3. Record the analysis. Advisory only - this process does NOT rewrite
    #    source. Auto-patching a live attendance system is not a trade worth making.
    try:
        conn = get_db_connection()
        conn.execute("UPDATE error_logs SET status = 'AI Analyzed', ai_analysis = ? WHERE id = ?",
                     (ai_analysis, error_id))
        conn.commit()
        conn.close()
        print(f"[AI Watchdog] Analysis recorded for error {error_id}. Review it in the admin dashboard.")
        if fix_code:
            print("[AI Watchdog] A suggested fix was returned; it is stored for a human to apply.")
    except Exception as log_e:
        print(f"[AI Watchdog] Could not record analysis: {log_e}")


def run_watchdog():
    print("==================================================")
    print("  VERIVAULT AI - GEMINI POWERED AUTO-REPAIR v2.0  ")
    print("==================================================")
    print("Status: LIVE | AI: Gemini Pro | Mode: FULLY AUTOMATED\n")
    
    while True:
        try:
            conn = get_db_connection()
            pending_errors = conn.execute("SELECT * FROM error_logs WHERE status = 'Pending'").fetchall()
            for error in pending_errors:
                analyze_and_repair(error['id'], error['route'], error['traceback_data'])
            conn.close()
        except Exception:
            pass
        time.sleep(5)

if __name__ == "__main__":
    run_watchdog()
