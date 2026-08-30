from flask import Flask, render_template, request, redirect, url_for, Response, flash, make_response, session, send_file, send_from_directory
import os
# Silence TensorFlow and oneDNN logs
os.environ['TF_CPP_MIN_LOG_LEVEL'] = '3' 
os.environ['TF_ENABLE_ONEDNN_OPTS'] = '0'
import logging
logging.getLogger('tensorflow').setLevel(logging.FATAL)

import warnings
# Silence FutureWarnings to keep logs clean
warnings.simplefilter(action='ignore', category=FutureWarning)

from flask_socketio import SocketIO
import cv2
import numpy as np
import json
import os
import csv
import io
import qrcode
import time
import subprocess
import sys
import logging
from logging.handlers import RotatingFileHandler
from io import BytesIO
from datetime import datetime, timedelta
from functools import wraps
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# --- Enterprise Logging Configuration ---
# Logs will be saved to both console and a rotating file
log_formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
log_file = 'verivault_enterprise.log'

# Rotating file handler (5MB per file, keep 3 backups)
file_handler = RotatingFileHandler(log_file, maxBytes=5*1024*1024, backupCount=3)
file_handler.setFormatter(log_formatter)
file_handler.setLevel(logging.INFO)

# Console handler
console_handler = logging.StreamHandler()
console_handler.setFormatter(log_formatter)
console_handler.setLevel(logging.INFO)

# Setup logger
logger = logging.getLogger('VeriVault')
logger.setLevel(logging.INFO)
logger.addHandler(file_handler)
logger.addHandler(console_handler)

from database import MEDIA_ROOT, init_db, migrate_db, add_student, add_student_face, get_attendance_report, get_all_students, delete_student, add_timetable_entry, get_timetable_entries, delete_timetable_entry, check_login, get_stats, get_recent_attendance, add_teacher, get_all_teachers, delete_user, apply_leave, get_student_leaves, get_all_leave_requests, update_leave_status, get_student_stats, get_student_attendance_history, mark_attendance, add_announcement, get_announcements, get_db_connection, add_faculty_face, get_user_by_id, update_user, log_audit_trail, get_attendance_trends, get_course_distribution
from ai_services import ask_database_ai, verify_ai_connectivity
from deepface import DeepFace
from camera import VideoCamera
import recognition
from scheduler import init_scheduler
from id_generator import generate_id_card

app = Flask(__name__)

# Sets the secret key (refusing to start on a missing one outside debug),
# cookie policy, CSRF and response headers.
from security import init_security, record_failed_login, clear_login_failures, \
    lockout_remaining, rotate_csrf_token
init_security(app)

socketio = SocketIO(app, async_mode='threading')

# Ensure private media directories exist (outside static/)
for _sub in ('attendance_captures', 'profiles'):
    os.makedirs(os.path.join(MEDIA_ROOT, _sub), exist_ok=True)

# Initialize and Migrate Database
init_db()
migrate_db()

# Presence model (class sessions, sightings, intervals, verdicts)
from presence import init_presence_schema
init_presence_schema()

# Institution details and the notification delivery record
from settings import init_settings_schema
from notification_hub import init_notification_schema
init_settings_schema()
init_notification_schema()

# Start background jobs
init_scheduler(app)

# Global camera instance
camera_instance = None

def get_camera():
    global camera_instance
    if camera_instance is None:
        try:
            camera_instance = VideoCamera(secret_key=app.secret_key)
        except Exception as e:
            logger.error(f"Camera Hardware Error: {e}. Dashboard-only mode.")
            return None
    return camera_instance

def reload_face_index():
    """Refresh identities here and signal other processes to do the same."""
    try:
        import bus
        bus.signal_face_reload()
    except Exception as e:
        logger.debug(f"Could not signal face reload: {e}")
    if camera_instance:
        try:
            camera_instance.load_embeddings()
        except Exception as e:
            logger.debug(f"Local index reload failed: {e}")


@app.context_processor
def inject_institution():
    """Campus name and coordinates, available to every template.

    The student dashboard used to carry its own copy of the campus latitude
    and longitude in JavaScript, which drifted from the one in app.py the
    moment either changed.
    """
    from settings import get_settings
    return {'institution': get_settings()}


# Login Required Decorator
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'logged_in' not in session:
            flash('Please login first.', 'error')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

# Admin Required Decorator
def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if session.get('role') != 'Admin':
            flash('Access restricted to Administrators only.', 'error')
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return login_required(decorated_function)

def role_required(*roles):
    """Restrict a route to the given roles. Replaces scattered inline checks."""
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if session.get('role') not in roles:
                flash('You do not have access to that page.', 'error')
                return redirect(url_for('index'))
            return f(*args, **kwargs)
        return login_required(decorated_function)
    return decorator


# --- AI Auto-Repair Error Catcher ---
import traceback
from database import log_error_to_db

@app.errorhandler(Exception)
def handle_exception(e):
    """Catches all unhandled exceptions, logs them for AI repair, and shows a safe error page."""
    # Ignore HTTP exceptions (like 404)
    from werkzeug.exceptions import HTTPException
    if isinstance(e, HTTPException):
        return e

    # Get the traceback
    tb = traceback.format_exc()
    route = request.path
    
    # Log to the database and enterprise logger
    logger.critical(f"CRITICAL SYSTEM FAILURE on {route}\n{tb}")
    try:
        log_error_to_db(route, tb)
        logger.info(f"AI Watchdog notified for error repair on {route}")
    except Exception as log_e:
        logger.error(f"Failed to log error to DB: {log_e}")
        
    # Return a generic 500 response to the user
    return "A critical system error occurred. The AI Auto-Repair watchdog has been notified and is analyzing the code.", 500

# --- Private media (face crops, profile photos) ---
# These used to sit under static/ where Flask served them to anyone.
@app.route('/media/<path:category>/<path:filename>')
@login_required
def private_media(category, filename):
    """Serves biometric media only to those entitled to see it."""
    if category not in ('attendance_captures', 'profiles'):
        return "Not found", 404

    # Students may only ever fetch their own imagery.
    if session.get('role') == 'Student':
        owned = (filename.startswith(f"student_{session.get('user_id')}.")
                 or filename.startswith(f"ID_Card_{session.get('user_id')}.")
                 or filename.startswith(f"verify_Student_{session.get('user_id')}_"))
        if not owned:
            return "Forbidden", 403

    safe_name = os.path.basename(filename)
    directory = os.path.abspath(os.path.join(MEDIA_ROOT, category))
    target = os.path.join(directory, safe_name)
    if not os.path.isfile(target):
        return "Not found", 404
    return send_file(target)


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']

        locked_for = lockout_remaining(username)
        if locked_for:
            flash(f'Too many failed attempts. Try again in {locked_for // 60 + 1} minute(s).', 'error')
            log_audit_trail(None, f"Login blocked by throttle for '{username}'",
                            "Auth", None, request.remote_addr)
            return render_template('login.html')

        user = check_login(username, password)
        if user:
            clear_login_failures(username)
            # New privilege level, so the pre-login token must not carry over.
            session.clear()
            rotate_csrf_token()
            session['logged_in'] = True
            session['user_id'] = user['id']
            session['username'] = user['username']
            session['role'] = user['role']
            session['full_name'] = user['full_name']
            session.permanent = True
            log_audit_trail(user['id'], f"Signed in as {user['role']}",
                            "Auth", user['id'], request.remote_addr)
            flash(f'Welcome, {user["full_name"]}!', 'success')
            return redirect(url_for('index'))

        locked_for = record_failed_login(username)
        log_audit_trail(None, f"Failed login for '{username}'", "Auth", None, request.remote_addr)
        if locked_for:
            flash(f'Too many failed attempts. This account is locked for {locked_for // 60} minutes.', 'error')
        else:
            flash('Invalid credentials.', 'error')
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    flash('Logged out.', 'success')
    return redirect(url_for('login'))

@app.route('/teachers', methods=['GET', 'POST'])
@admin_required
def manage_teachers():
    if request.method == 'POST':
        add_teacher(
            request.form['username'],
            request.form['password'],
            request.form['full_name'],
            request.form['department'],
            request.form.get('subjects')
        )
        flash('Teacher added successfully!', 'success')
        return redirect(url_for('manage_teachers'))
    
    teachers = get_all_teachers()
    return render_template('manage_teachers.html', teachers=teachers)

@app.route('/delete_teacher/<int:id>', methods=['POST'])
@admin_required
def delete_teacher_route(id):
    delete_user(id)
    flash('Teacher removed.', 'success')
    return redirect(url_for('manage_teachers'))

# Has to live at the root or the browser scopes the worker to /static/ and it
# never sees a navigation request.
@app.route('/sw.js')
def service_worker():
    response = make_response(send_from_directory('static', 'sw.js'))
    response.headers['Content-Type'] = 'application/javascript'
    response.headers['Cache-Control'] = 'no-cache'
    return response

@app.route('/')
@login_required
def index():
    role = session.get('role')
    if role == 'Student':
        return redirect(url_for('student_dashboard'))
    if role == 'Teacher':
        return redirect(url_for('teacher_dashboard'))

    stats = get_stats()
    recent = get_recent_attendance(5)
    announcements = get_announcements(limit=3)
    return render_template('index.html', stats=stats, recent=recent, announcements=announcements)

@app.route('/api/analytics')
@login_required
def api_analytics():
    if session.get('role') == 'Student':
        return {"error": "Unauthorized"}, 403
    
    trends = get_attendance_trends(7)
    distribution = get_course_distribution()
    return {"trends": trends, "distribution": distribution}

@app.route('/student_dashboard')
@login_required
def student_dashboard():
    if session.get('role') != 'Student':
        return redirect(url_for('index'))
    
    student_id = session.get('user_id')
    conn = get_db_connection()
    student = conn.execute('SELECT course FROM students WHERE id = ?', (student_id,)).fetchone()
    conn.close()
    
    stats = get_student_stats(student_id)
    history = get_student_attendance_history(student_id)[:10]
    leaves = get_student_leaves(student_id)
    announcements = get_announcements(course_filter=student['course'] if student else None, limit=3)

    import presence as P
    subjects = P.student_subject_summary(student_id)

    return render_template('student_dashboard.html', stats=stats, history=history, leaves=leaves,
                           announcements=announcements, subjects=subjects,
                           today=datetime.now().strftime('%Y-%m-%d'))

@app.route('/add_announcement', methods=['POST'])
@login_required
def post_announcement():
    if session.get('role') not in ['Admin', 'Teacher']:
        return redirect(url_for('index'))
        
    add_announcement(
        session.get('user_id'),
        request.form['title'],
        request.form['content'],
        request.form.get('target_course', 'All')
    )
    flash('Announcement published successfully!', 'success')
    return redirect(url_for('index'))

import hmac
import hashlib

@app.route('/student/qr')
@login_required
def get_student_qr():
    if session.get('role') != 'Student':
        return redirect(url_for('index'))
        
    student_id = session.get('user_id')
    conn = get_db_connection()
    student = conn.execute('SELECT qr_hash FROM students WHERE id = ?', (student_id,)).fetchone()
    conn.close()
    
    if student and student['qr_hash']:
        # Enterprise-Grade Dynamic Token (Expiring every 60 seconds)
        # Prevents "QR Photo Spoofing"
        timestamp = int(time.time() / 60) # Change token every minute
        raw_data = f"{student['qr_hash']}:{timestamp}"
        
        # Sign the token using the app's secret key
        signature = hmac.new(
            app.secret_key.encode() if isinstance(app.secret_key, str) else app.secret_key,
            raw_data.encode(),
            hashlib.sha256
        ).hexdigest()[:16]
        
        dynamic_token = f"{student['qr_hash']}:{timestamp}:{signature}"
        
        qr = qrcode.QRCode(version=1, box_size=10, border=5)
        qr.add_data(dynamic_token)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        
        img_io = BytesIO()
        img.save(img_io, 'PNG')
        img_io.seek(0)
        
        logger.info(f"Dynamic QR Token generated for student {student_id}")
        return Response(img_io.getvalue(), mimetype='image/png')
    
    return "QR Code not found", 404

@app.route('/student/id_card')
@login_required
def download_id_card():
    if session.get('role') != 'Student':
        return redirect(url_for('index'))
        
    student_id = session.get('user_id')
    conn = get_db_connection()
    # Need full data for ID card
    student = conn.execute('SELECT * FROM students WHERE id = ?', (student_id,)).fetchone()
    conn.close()
    
    if student:
        output_path = os.path.join(MEDIA_ROOT, 'profiles', f'ID_Card_{student_id}.pdf')
        generate_id_card(student, output_path)
        
        return send_file(output_path, as_attachment=True)
    
    return "Student data not found", 404

@app.route('/apply_leave', methods=['POST'])
@login_required
def apply_leave_route():
    if session.get('role') != 'Student':
        return redirect(url_for('index'))
    
    apply_leave(
        session.get('user_id'),
        request.form['start_date'],
        request.form['end_date'],
        request.form['reason']
    )
    flash('Leave application submitted successfully.', 'success')
    return redirect(url_for('student_dashboard'))

@app.route('/admin/leaves')
@login_required
def manage_leaves():
    # Both Admin and Teacher can see/approve leaves
    if session.get('role') not in ['Admin', 'Teacher']:
        return redirect(url_for('index'))
        
    requests = get_all_leave_requests()
    return render_template('manage_leaves.html', requests=requests)

@app.route('/admin/leave/update/<int:id>/<string:status>', methods=['POST'])
@login_required
def update_leave(id, status):
    if session.get('role') not in ['Admin', 'Teacher']:
        return redirect(url_for('index'))
        
    update_leave_status(id, status)
    flash(f'Leave request {status}.', 'success')
    return redirect(url_for('manage_leaves'))

@app.route('/register', methods=['GET', 'POST'])
@admin_required
def register():
    if request.method == 'POST':
        name = request.form['name']
        ou_id = request.form['ou_id']
        edc_number = request.form['edc_number']
        course = request.form['course']
        year = request.form['year']
        outlook_email = request.form.get('outlook_email')
        parent_email = request.form.get('parent_email')
        parent_phone = request.form.get('parent_phone')
        
        # Get the single uploaded file
        file = request.files.get('photo')
        
        if not file or file.filename == '':
            flash('No photo selected.', 'error')
            return redirect(url_for('register'))

        # 1. Create Student Record
        student_id = add_student(name, ou_id, edc_number, course, year, outlook_email, parent_email, parent_phone)
        
        if not student_id:
            flash('Error: OU ID or EDC Number already exists.', 'error')
            return redirect(url_for('register'))

        # Save Photo for ID Card
        photo_path = os.path.join(MEDIA_ROOT, 'profiles', f'student_{student_id}.jpg')
        file.save(photo_path)
        file.seek(0) # Reset file pointer for CV2 processing

        # 2. Process the single photo
        face_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_frontalface_default.xml')
        try:
            file_bytes = np.frombuffer(file.read(), np.uint8)
            img = cv2.imdecode(file_bytes, cv2.IMREAD_COLOR)
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            
            faces = face_cascade.detectMultiScale(gray, 1.1, 5)
            
            if len(faces) > 0:
                (x, y, w, h) = faces[0]
                roi_gray = gray[y:y+h, x:x+w]
                roi_color = img[y:y+h, x:x+w]
                roi_gray = cv2.resize(roi_gray, (200, 200))

                face_data_json = json.dumps(roi_gray.tolist())

                # Neural Embedding Generation
                try:
                    embedding = DeepFace.represent(img_path=roi_color, model_name="Facenet", enforce_detection=False)[0]["embedding"]
                    embedding_json = json.dumps(embedding)
                except Exception as e:
                    logger.error(f"DeepFace Embedding Error: {e}")
                    embedding_json = None

                add_student_face(student_id, face_data_json, embedding_json)
                flash(f'Student registered successfully to {course} - {year}!', 'success')
            else:

                flash('Student added, but NO face detected in the photo. Attendance might not work.', 'error')
        except Exception as e:
            flash(f'An error occurred during photo processing: {str(e)}', 'error')

        # Tell every vision worker to pick up the new face
        reload_face_index()

        return redirect(url_for('register'))
            
    return render_template('register.html')

@app.route('/timetable', methods=['GET', 'POST'])
@login_required
def timetable():
    if request.method == 'POST':
        # Only Admins can add timetable entries
        if session.get('role') != 'Admin':
            flash('Access denied.', 'error')
            return redirect(url_for('timetable'))
            
        teacher_id_val = request.form.get('teacher_id')
        if not teacher_id_val:
            teacher_id_val = None
            
        add_timetable_entry(
            request.form['course'],
            request.form['year'],
            request.form['day'],
            request.form['start_time'],
            request.form['end_time'],
            request.form['subject'],
            teacher_id_val
        )
        flash('Class scheduled successfully!', 'success')
        return redirect(url_for('timetable'))
    
    entries = get_timetable_entries()
    teachers = get_all_teachers() # For the dropdown
    return render_template('timetable.html', entries=entries, teachers=teachers)

@app.route('/delete_timetable/<int:id>', methods=['POST'])
@admin_required
def delete_timetable_route(id):
    delete_timetable_entry(id)
    flash('Schedule entry removed.', 'success')
    return redirect(url_for('timetable'))

@app.route('/attendance')
@role_required('Admin', 'Teacher')
def attendance():
    date_filter = request.args.get('date')
    report = get_attendance_report(date_filter)
    return render_template('attendance.html', report=report, current_date=date_filter)

@app.route('/export_csv')
@role_required('Admin', 'Teacher')
def export_csv():
    date_filter = request.args.get('date')
    report = get_attendance_report(date_filter)
    
    si = io.StringIO()
    cw = csv.writer(si)
    # Updated headers for unified report
    cw.writerow(['Date', 'Time In', 'Subject', 'Status', 'Person Name', 'Role', 'Course', 'Year', 'Outlook Email', 'Parent Email'])
    
    for row in report:
        cw.writerow([
            row['date'], 
            row['time_in'], 
            row['subject'], 
            row['status'],
            row['display_name'], 
            row['person_role'],
            row['course'] if row['course'] else 'N/A', 
            row['year'] if row['year'] else 'N/A', 
            row['outlook_email'],
            row['parent_email']
        ])
        
    output = make_response(si.getvalue())
    output.headers["Content-Disposition"] = "attachment; filename=attendance_report.csv"
    output.headers["Content-type"] = "text/csv"
    return output

@app.route('/register_face', methods=['GET', 'POST'])
@role_required('Admin', 'Teacher')
def register_face():
    if request.method == 'POST':
        file = request.files.get('photo')
        if not file or file.filename == '':
            flash('No photo selected.', 'error')
            return redirect(url_for('register_face'))

        face_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_frontalface_default.xml')
        try:
            file_bytes = np.frombuffer(file.read(), np.uint8)
            img = cv2.imdecode(file_bytes, cv2.IMREAD_COLOR)
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            faces = face_cascade.detectMultiScale(gray, 1.1, 5)
            
            if len(faces) > 0:
                (x, y, w, h) = faces[0]
                roi_gray = cv2.resize(gray[y:y+h, x:x+w], (200, 200))
                roi_color = img[y:y+h, x:x+w]
                face_data_json = json.dumps(roi_gray.tolist())

                # Neural Embedding Generation
                try:
                    embedding = DeepFace.represent(img_path=roi_color, model_name="Facenet", enforce_detection=False)[0]["embedding"]
                    embedding_json = json.dumps(embedding)
                except Exception as e:
                    logger.error(f"DeepFace Embedding Error: {e}")
                    embedding_json = None

                add_faculty_face(session.get('user_id'), face_data_json, embedding_json)

                reload_face_index()
                flash('Your face has been registered for attendance!', 'success')

            else:
                flash('No face detected. Please try a clearer photo.', 'error')
        except Exception as e:
            flash(f'Error: {str(e)}', 'error')
        return redirect(url_for('index'))
            
    return render_template('register_face.html')

@app.route('/manual_attendance', methods=['GET', 'POST'])
@role_required('Admin', 'Teacher')
def manual_attendance():
    if request.method == 'POST':
        student_id = request.form['student_id']
        status = request.form['status']
        start_date_str = request.form['start_date']
        end_date_str = request.form['end_date']
        
        start_date = datetime.strptime(start_date_str, '%Y-%m-%d')
        end_date = datetime.strptime(end_date_str, '%Y-%m-%d')
        
        # Iterate through every day in the range (inclusive)
        current_date = start_date
        count = 0
        while current_date <= end_date:
            date_to_mark = current_date.strftime('%Y-%m-%d')
            # Mark as a general "Manual Override" subject for that day
            if mark_attendance(person_id=student_id, role='Student', status=status, 
                               override_date=date_to_mark, override_subject="Manual Override"):
                count += 1
            current_date += timedelta(days=1)
            
        # Enterprise Audit Trail
        admin_id = session.get('user_id')
        ip_addr = request.remote_addr
        log_audit_trail(admin_id, f"Manual Override: {status} from {start_date_str} to {end_date_str}", "Student", student_id, ip_addr)
            
        flash(f'Successfully updated {count} records for the selected range.', 'success')
        return redirect(url_for('manual_attendance'))
        
    students = get_all_students()
    today = datetime.now().strftime('%Y-%m-%d')
    return render_template('manual_attendance.html', students=students, today=today)

@app.route('/students')
@role_required('Admin', 'Teacher')
def manage_students():
    students = get_all_students()
    return render_template('manage_students.html', students=students)

# --- Geo-Fenced Mobile Check-in ---
import math

def haversine(lat1, lon1, lat2, lon2):
    """Calculates distance between two coordinates in meters."""
    R = 6371000 # Earth radius in meters
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)

    a = math.sin(delta_phi/2.0)**2 + \
        math.cos(phi1) * math.cos(phi2) * \
        math.sin(delta_lambda/2.0)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c

@app.route('/mobile_checkin', methods=['POST'])
@login_required
def mobile_checkin():
    if session.get('role') != 'Student':
        return {"error": "Only students can use mobile check-in"}, 403
        
    from settings import get_settings
    cfg = get_settings()

    data = request.json
    student_lat = float(data.get('lat', 0))
    student_lon = float(data.get('lon', 0))

    distance = haversine(student_lat, student_lon, cfg['campus_lat'], cfg['campus_lon'])
    allowed = cfg['geofence_radius_m']

    if distance <= allowed:
        student_id = session.get('user_id')
        mark_attendance(student_id, role='Student', status='Present', override_subject="Mobile GPS Check-in")
        
        # Enterprise Audit
        log_audit_trail(student_id, f"Mobile Check-in (Distance: {int(distance)}m)", "Student", student_id, request.remote_addr)
        
        return {"success": True, "message": f"Verified! Distance from campus: {int(distance)}m"}
    else:
        # Enterprise Audit
        log_audit_trail(session.get('user_id'), f"Failed Check-in (Too far: {int(distance)}m)", "Student", session.get('user_id'), request.remote_addr)
        return {"error": f"You are {int(distance)} m away, and check-in is allowed within "
                         f"{int(allowed)} m of campus."}, 403

@app.route('/delete_student/<int:id>', methods=['POST'])
@admin_required
def delete_student_route(id):
    delete_student(id)
    flash('Student deleted successfully.', 'success')
    reload_face_index()
        
    return redirect(url_for('manage_students'))

@app.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
    user_id = session.get('user_id')
    if session.get('role') == 'Student':
        return redirect(url_for('student_profile'))


    if request.method == 'POST':
        full_name = request.form['full_name']
        username = request.form['username']
        new_password = request.form.get('password') or None
        subjects = request.form.get('subjects')
        
        update_user(user_id, full_name, username, new_password, subjects)
        
        # Update session info
        session['full_name'] = full_name
        session['username'] = username
        
        flash('Profile updated successfully!', 'success')
        return redirect(url_for('profile'))
        
    user = get_user_by_id(user_id)
    return render_template('profile.html', user=user)

@app.route('/student/profile', methods=['GET', 'POST'])
@role_required('Student')
def student_profile():
    """A student's own account page. Chiefly, the only way they can stop using
    the EDC number they were enrolled with as their password."""
    from database import get_student_by_id, set_student_password, student_password_is_default
    student_id = session.get('user_id')

    if request.method == 'POST':
        ok, message = set_student_password(
            student_id,
            request.form.get('current_password', ''),
            request.form.get('new_password', ''),
        )
        if ok:
            log_audit_trail(student_id, "Changed own password", "Student", student_id, request.remote_addr)
            flash(message, 'success')
            return redirect(url_for('student_profile'))
        flash(message, 'error')

    return render_template('student_profile.html',
                           student=get_student_by_id(student_id),
                           using_default=student_password_is_default(student_id))


@app.route('/admin/audit')
@admin_required
def audit_log():
    """An audit trail nobody can read is not an audit trail."""
    limit = min(int(request.args.get('limit', 200)), 1000)
    conn = get_db_connection()
    entries = conn.execute(
        '''SELECT a.*, COALESCE(u.full_name, s.name, 'System') AS actor
           FROM audit_logs a
           LEFT JOIN users u ON a.user_id = u.id
           LEFT JOIN students s ON a.user_id = s.id AND a.target_type = 'Student'
           ORDER BY a.timestamp DESC LIMIT ?''', (limit,)).fetchall()
    conn.close()
    return render_template('audit_log.html', entries=entries, limit=limit)


@app.route('/delete_account', methods=['POST'])
@login_required
def delete_account():
    user_id = session.get('user_id')
    if session.get('role') == 'Admin' and user_id == 1:
        flash('The primary System Administrator account cannot be removed.', 'error')
        return redirect(url_for('profile'))
        
    delete_user(user_id)
    session.clear()
    flash('Your account has been permanently removed.', 'success')
    return redirect(url_for('login'))

@app.route('/api/ai_chat', methods=['POST'])
@login_required
def api_ai_chat():
    if session.get('role') == 'Student':
        return {"error": "Unauthorized"}, 403
        
    user_query = request.json.get('query')
    if not user_query:
        return {"error": "No query provided"}, 400
        
    result = ask_database_ai(user_query)
    return result

@app.route('/api/generate_report', methods=['POST'])
@admin_required
def api_generate_report():
    try:
        from tasks import generate_weekly_department_pdf
        # Since we want to return the PDF immediately to the user clicking the button,
        # we'll execute it synchronously here instead of .delay()
        pdf_path = generate_weekly_department_pdf()
        return send_file(pdf_path, as_attachment=True, download_name=f"Enterprise_Report_{datetime.now().strftime('%Y%m%d')}.pdf")
    except Exception as e:
        logger.error(f"Failed to generate report: {e}")
        return {"error": "Generation failed"}, 500

@app.route('/api/camera/start')
@role_required('Admin', 'Teacher')
def start_camera_api():
    cam = get_camera()
    if not cam:
        return {"error": "No camera on this machine"}, 503
    cam.start_capture()
    return {"status": "started"}

@app.route('/api/camera/stop')
@role_required('Admin', 'Teacher')
def stop_camera_api():
    cam = get_camera()
    if not cam:
        return {"error": "No camera on this machine"}, 503
    cam.stop_capture()
    return {"status": "stopped"}

def gen(camera):
    while True:
        frame = camera.get_frame()
        if frame:
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n\r\n')
        else:
            # If camera is off, don't hog CPU, wait a bit
            time.sleep(0.1)

@app.route('/scanner')
@role_required('Admin', 'Teacher')
def web_scanner():
    return render_template('scanner.html')

# Every face costs one embedding, and the phone is waiting on the response.
# Past this many the frame is almost certainly a corridor, not a class.
MAX_SCAN_FACES = 12


@app.route('/api/cloud_scan', methods=['POST'])
@role_required('Admin', 'Teacher')
def cloud_scan():
    """Scanning API for any device (Mobile/Tablet/PC).

    Marks everyone in the frame. Pointing the camera at a row of students is
    the normal way this gets used, so stopping at the first recognised face -
    which is what it used to do - marked one student and quietly ignored the
    rest of the row.
    """
    data = request.json
    if not data or 'image' not in data:
        return {"error": "No image data"}, 400

    import base64
    try:
        header, encoded = data['image'].split(",", 1)
        image_bytes = base64.b64decode(encoded)
        nparr = np.frombuffer(image_bytes, np.uint8)
        frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if frame is None:
            return {"error": "Unreadable image"}, 400

        cam = get_camera()
        if not cam:
            return {"error": "AI Vision Engine offline"}, 500

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        boxes = recognition.detect_faces_group(gray, cam.face_cascade, cam.profile_cascade)

        detected = len(boxes)
        if detected > MAX_SCAN_FACES:
            # Largest first, so the people actually being scanned win over
            # whoever is walking past in the background.
            boxes = sorted(boxes, key=lambda b: b[2] * b[3], reverse=True)[:MAX_SCAN_FACES]
            logger.info(f"Cloud scan found {detected} faces; processing the {MAX_SCAN_FACES} largest.")

        marked, already, seen = [], [], set()
        unknown = 0

        for (x, y, w, h) in boxes:
            roi_color = frame[y:y + h, x:x + w]
            if roi_color.size == 0:
                continue

            person, dist = cam.recognize_face(roi_color)
            if not person:
                unknown += 1
                continue
            if person['id'] in seen:
                continue
            seen.add(person['id'])

            entry = {
                "name": person['name'],
                "role": person['role'],
                "confidence": round((1.0 - dist) * 100, 1),
            }
            if mark_attendance(person['id'], role=person['role'], captured_face=roi_color):
                marked.append(entry)
            else:
                already.append(entry)

        if marked:
            status = f"Marked {len(marked)} present"
        elif already:
            status = "Already marked"
        elif detected:
            status = "Face not recognised"
        else:
            status = "No face detected"

        return {
            "status": status,
            "faces": detected,
            "processed": len(boxes),
            "marked": marked,
            "already": already,
            "unknown": unknown,
        }
    except Exception as e:
        logger.exception("Cloud scan failed")
        return {"error": str(e)}, 500

@app.route('/video_feed')
@role_required('Admin', 'Teacher')
def video_feed():
    cam = get_camera()
    if not cam:
        return "No camera on this machine", 503
    cam.start_capture()
    return Response(gen(cam),
                    mimetype='multipart/x-mixed-replace; boundary=frame')

# =============================================================================
# Presence dashboards (Phase 5)
# =============================================================================
import presence as P


@app.route('/teacher')
@role_required('Teacher', 'Admin')
def teacher_dashboard():
    date_str = request.args.get('date') or datetime.now().strftime('%Y-%m-%d')
    teacher_id = session.get('user_id') if session.get('role') == 'Teacher' else None
    sessions = P.session_overview(date_str, teacher_id=teacher_id)

    live_id = None
    now = datetime.now()
    for s in sessions:
        if P.parse_ts(s['start_ts']) <= now < P.parse_ts(s['end_ts']):
            live_id = s['id']
            break

    return render_template('dashboard_teacher.html', sessions=sessions,
                           date=date_str, live_session_id=live_id,
                           announcements=get_announcements(limit=3))


@app.route('/admin')
@admin_required
def admin_dashboard():
    date_str = request.args.get('date') or datetime.now().strftime('%Y-%m-%d')
    return render_template('dashboard_admin.html',
                           stats=get_stats(),
                           sessions=P.session_overview(date_str),
                           cameras=P.camera_health(),
                           config=P.get_config(),
                           date=date_str,
                           announcements=get_announcements(limit=3))


@app.route('/api/session/<int:session_id>/live')
@role_required('Teacher', 'Admin')
def api_session_live(session_id):
    state = P.live_session_state(session_id)
    if not state:
        return {"error": "No such session"}, 404
    return state


@app.route('/api/session/<int:session_id>/close', methods=['POST'])
@role_required('Teacher', 'Admin')
def api_session_close(session_id):
    summary = P.close_session(session_id)
    log_audit_trail(session.get('user_id'), f"Closed session {session_id} manually",
                    "ClassSession", session_id, request.remote_addr)
    return summary


@app.route('/api/session/<int:session_id>/override', methods=['POST'])
@role_required('Teacher', 'Admin')
def api_session_override(session_id):
    """Manual correction of one student's verdict, with an audit entry."""
    data = request.json or {}
    student_id = data.get('student_id')
    status = data.get('status')
    if not student_id or status not in P.STATUS_ORDER:
        return {"error": "student_id and a valid status are required"}, 400

    sess = P.get_session(session_id)
    if not sess:
        return {"error": "No such session"}, 404

    conn = get_db_connection()
    cur = conn.cursor()
    existing = cur.execute('SELECT id FROM attendance WHERE session_id = ? AND student_id = ?',
                           (session_id, student_id)).fetchone()
    if existing:
        cur.execute('UPDATE attendance SET status = ?, marked_by_user_id = ? WHERE id = ?',
                    (status, session.get('user_id'), existing['id']))
    else:
        cur.execute('INSERT INTO attendance (student_id, session_id, date, time_in, subject, status, marked_by_user_id) '
                    'VALUES (?, ?, ?, ?, ?, ?, ?)',
                    (student_id, session_id, sess['date'], str(sess['start_ts'])[11:19],
                     sess['subject'], status, session.get('user_id')))
    conn.commit()
    conn.close()

    log_audit_trail(session.get('user_id'), f"Override: student {student_id} -> {status}",
                    "Attendance", session_id, request.remote_addr)
    return {"ok": True, "student_id": student_id, "status": status}


@app.route('/api/camera/<int:camera_id>/preview')
@role_required('Teacher', 'Admin')
def api_camera_preview(camera_id):
    import bus
    frame = bus.get_preview(camera_id)
    if not frame:
        return "No preview available", 404
    return Response(frame, mimetype='image/jpeg')


# --- Student views -----------------------------------------------------------

@app.route('/student/day')
@login_required
def student_day():
    if session.get('role') != 'Student':
        return {"error": "Unauthorized"}, 403
    date_str = request.args.get('date') or datetime.now().strftime('%Y-%m-%d')
    return {"date": date_str, "classes": P.student_day_breakdown(session.get('user_id'), date_str)}


@app.route('/student/calendar')
@login_required
def student_calendar_api():
    if session.get('role') != 'Student':
        return {"error": "Unauthorized"}, 403
    today = datetime.now()
    start = request.args.get('start') or today.replace(day=1).strftime('%Y-%m-%d')
    end = request.args.get('end') or today.strftime('%Y-%m-%d')
    return {"days": P.student_calendar(session.get('user_id'), start, end)}


@app.route('/student/export')
@login_required
def student_export():
    """Date-range CSV of the student's own record."""
    if session.get('role') != 'Student':
        return redirect(url_for('index'))
    start = request.args.get('start') or datetime.now().replace(day=1).strftime('%Y-%m-%d')
    end = request.args.get('end') or datetime.now().strftime('%Y-%m-%d')

    conn = get_db_connection()
    rows = conn.execute(
        'SELECT date, time_in, subject, status, coverage_pct, present_seconds FROM attendance '
        'WHERE student_id = ? AND date BETWEEN ? AND ? ORDER BY date DESC, time_in DESC',
        (session.get('user_id'), start, end)).fetchall()
    conn.close()

    si = io.StringIO()
    cw = csv.writer(si)
    cw.writerow(['Date', 'Time In', 'Subject', 'Status', 'Coverage %', 'Minutes Present'])
    for r in rows:
        cw.writerow([r['date'], r['time_in'], r['subject'], r['status'],
                     r['coverage_pct'] if r['coverage_pct'] is not None else '',
                     (r['present_seconds'] or 0) // 60])

    output = make_response(si.getvalue())
    output.headers["Content-Disposition"] = f"attachment; filename=my_attendance_{start}_to_{end}.csv"
    output.headers["Content-type"] = "text/csv"
    return output


# --- Admin: cameras, thresholds, sessions ------------------------------------

@app.route('/admin/cameras', methods=['GET', 'POST'])
@admin_required
def manage_cameras():
    if request.method == 'POST':
        P.add_camera(request.form['name'], request.form.get('room'),
                     request.form.get('source', '0'),
                     request.form.get('course') or None,
                     request.form.get('year') or None)
        flash('Camera added.', 'success')
        return redirect(url_for('manage_cameras'))
    return render_template('manage_cameras.html', cameras=P.camera_health())


@app.route('/admin/cameras/<int:camera_id>/delete', methods=['POST'])
@admin_required
def delete_camera_route(camera_id):
    P.delete_camera(camera_id)
    flash('Camera removed.', 'success')
    return redirect(url_for('manage_cameras'))


@app.route('/admin/config', methods=['POST'])
@admin_required
def update_config():
    for key in P.DEFAULT_CONFIG:
        if key in request.form:
            P.set_config(key, request.form[key])
    log_audit_trail(session.get('user_id'), "Updated attendance thresholds",
                    "Config", None, request.remote_addr)
    flash('Attendance rules updated.', 'success')
    return redirect(url_for('admin_dashboard'))


@app.route('/admin/settings', methods=['POST'])
@admin_required
def update_institution_settings():
    from settings import DEFAULTS, set_setting
    changed, errors = [], []
    for key in DEFAULTS:
        if key not in request.form:
            continue
        try:
            set_setting(key, request.form[key].strip())
            changed.append(key)
        except (ValueError, KeyError) as e:
            errors.append(str(e))

    if errors:
        for message in errors:
            flash(message, 'error')
    else:
        log_audit_trail(session.get('user_id'), f"Updated institution settings: {', '.join(changed)}",
                        "Settings", None, request.remote_addr)
        flash('Institution settings saved.', 'success')
    return redirect(url_for('admin_dashboard'))


@app.route('/admin/notifications')
@admin_required
def notification_log():
    """What the system actually sent, and what it failed to send."""
    from notification_hub import recent_notifications, delivery_summary, is_configured as sms_ready
    from email_service import is_configured as email_ready
    return render_template('notifications.html',
                           entries=recent_notifications(300),
                           summary=delivery_summary(7),
                           email_ready=email_ready(),
                           sms_ready=sms_ready())


@app.route('/admin/materialize', methods=['POST'])
@admin_required
def materialize_now():
    created = P.materialize_sessions()
    flash(f'Created {created} class session(s) from the timetable for today.', 'success')
    return redirect(url_for('admin_dashboard'))


# --- PWA & Advanced Classroom Analytics Routes ---

@app.route('/manifest.json')
def serve_manifest():
    return send_from_directory('static', 'manifest.json')

@app.route('/admin/seating_heatmap/<int:camera_id>')
@admin_required
def seating_heatmap(camera_id):
    camera = P.get_camera(camera_id)
    if not camera:
        flash('Camera not found.', 'danger')
        return redirect(url_for('manage_cameras'))

    # Most recent sightings, not an arbitrary all-time slice. Without ORDER BY
    # the LIMIT returned whatever the table scan hit first - in practice the
    # oldest rows - so the map never reflected where people sit now.
    conn = get_db_connection()
    sightings = conn.execute(
        "SELECT box_x, box_y, box_w, box_h FROM sightings "
        "WHERE camera_id = ? AND box_x IS NOT NULL "
        "ORDER BY id DESC LIMIT 2000",
        (camera_id,)).fetchall()
    conn.close()

    # The grid has to be derived from the frame the camera actually produces.
    # Fixed divisors assumed a ~1250x700 frame: at 640x480 every student
    # collapsed into the top-left cells, at 1920x1080 they all clipped into the
    # last one. Calibrating off the observed extent keeps the map correct at
    # any resolution without needing the frame size stored anywhere.
    GRID = 5
    points = []
    for s in sightings:
        bx, by = s['box_x'], s['box_y']
        if bx is None or by is None:
            continue
        cx = bx + (s['box_w'] or 0) / 2.0     # centre of the face, not its corner
        cy = by + (s['box_h'] or 0) / 2.0
        points.append((cx, cy))

    grid = []
    max_count = 1
    cell_counts = {}
    if points:
        span_x = max(cx for cx, _ in points) or 1.0
        span_y = max(cy for _, cy in points) or 1.0
        for cx, cy in points:
            col_idx = min(GRID - 1, max(0, int(cx / span_x * GRID)))
            row_idx = min(GRID - 1, max(0, int(cy / span_y * GRID)))
            key = (row_idx, col_idx)
            cell_counts[key] = cell_counts.get(key, 0) + 1
            if cell_counts[key] > max_count:
                max_count = cell_counts[key]

    for r in range(GRID):
        row_cells = []
        for c in range(GRID):
            cnt = cell_counts.get((r, c), 0)
            intensity = round(cnt / float(max_count), 2) if max_count > 0 else 0.0
            row_cells.append({
                'row_idx': r + 1,
                'col_idx': c + 1,
                'count': cnt,
                'intensity': intensity
            })
        grid.append(row_cells)

    return render_template('seating_heatmap.html', camera=camera, grid=grid)

@app.route('/api/voice_announcement/<int:session_id>')
@role_required('Admin', 'Teacher')
def voice_announcement(session_id):
    session_data = P.get_session(session_id)
    if not session_data:
        return {'status': 'error', 'message': 'Session not found'}, 404

    conn = get_db_connection()
    counts = conn.execute(
        "SELECT status, COUNT(*) as cnt FROM attendance WHERE session_id = ? GROUP BY status",
        (session_id,)).fetchall()
    conn.close()

    status_map = {r['status']: r['cnt'] for r in counts}
    present = status_map.get('Present', 0)
    late = status_map.get('Late', 0)
    absent = status_map.get('Absent', 0)

    text = f"Attendance report for {session_data['subject']}. {present} students present, {late} late, and {absent} absent."
    return {'status': 'success', 'subject': session_data['subject'], 'text': text}


def is_redis_running():
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(1)
        s.connect(("localhost", 6379))
        s.close()
        return True
    except:
        return False

if __name__ == '__main__':
    # --- Enterprise Multi-Process Orchestration ---
    if os.environ.get('WERKZEUG_RUN_MAIN') == 'true':
        if verify_ai_connectivity():
            logger.info("Analytics Services Engine: ONLINE")
        else:
            logger.warning("Analytics Services Engine: Standby Mode.")

        if not os.environ.get('VERCEL'):
            logger.info("Launching System Diagnostics Watchdog...")
            subprocess.Popen([sys.executable, "ai_watchdog.py"])

        if is_redis_running():
            try:
                logger.info("Launching Celery Background Task Worker...")
                subprocess.Popen([sys.executable, "-m", "celery", "-A", "tasks.celery_app", "worker", "--loglevel=info", "--pool=solo"])
            except Exception as e:
                logger.error(f"Failed to launch Celery Worker: {e}")
        else:
            logger.warning("REDIS NOT FOUND: Background tasks (PDF Reports, Bulk Emails) are DISABLED. Please start Redis server on port 6379.")

    debug_mode = os.environ.get('FLASK_DEBUG', '0') == '1'
    socketio.run(app, host='0.0.0.0', port=5000, debug=debug_mode, allow_unsafe_werkzeug=True)
