from flask import Flask, render_template, request, redirect, url_for, Response, flash, make_response, session, send_file
from flask_socketio import SocketIO, emit
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
from io import BytesIO
from datetime import datetime, timedelta
from functools import wraps
from database import init_db, migrate_db, add_student, add_student_face, get_attendance_report, get_all_students, delete_student, add_timetable_entry, get_timetable_entries, delete_timetable_entry, check_login, get_stats, get_recent_attendance, add_teacher, get_all_teachers, delete_user, apply_leave, get_student_leaves, get_all_leave_requests, update_leave_status, get_student_stats, get_student_attendance_history, mark_attendance, add_announcement, get_announcements, get_db_connection, add_faculty_face, get_user_by_id, update_user, log_audit_trail
from camera import VideoCamera
from scheduler import init_scheduler
from id_generator import generate_id_card

app = Flask(__name__)
app.secret_key = 'super_secret_key_for_demo_purposes'
socketio = SocketIO(app, async_mode='threading')

# Initialize and Migrate Database
init_db()
migrate_db()

# Start background jobs
init_scheduler(app)

# Global camera instance
camera_instance = None

def get_camera():
    global camera_instance
    if camera_instance is None:
        try:
            camera_instance = VideoCamera()
        except Exception as e:
            print(f"[System] Camera Hardware Error: {e}. Running in Dashboard-only mode.")
            return None
    return camera_instance

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
    
    # Log to the database for the AI watchdog
    try:
        log_error_to_db(route, tb)
        print(f"CRITICAL ERROR CAUGHT: Logged for AI Auto-Repair on route {route}")
    except Exception as log_e:
        print(f"Failed to log error to DB: {log_e}")
        
    # Return a generic 500 response to the user
    return "A critical system error occurred. The AI Auto-Repair watchdog has been notified and is analyzing the code.", 500

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        user = check_login(username, password)
        if user:
            session['logged_in'] = True
            session['user_id'] = user['id']
            session['username'] = user['username']
            session['role'] = user['role']
            session['full_name'] = user['full_name']
            flash(f'Welcome, {user["full_name"]}!', 'success')
            return redirect(url_for('index'))
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

@app.route('/')
@login_required
def index():
    if session.get('role') == 'Student':
        return redirect(url_for('student_dashboard'))
        
    stats = get_stats()
    recent = get_recent_attendance(5)
    announcements = get_announcements(limit=3)
    return render_template('index.html', stats=stats, recent=recent, announcements=announcements)

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
    
    return render_template('student_dashboard.html', stats=stats, history=history, leaves=leaves, announcements=announcements)

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
        qr = qrcode.QRCode(version=1, box_size=10, border=5)
        qr.add_data(student['qr_hash'])
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        
        img_io = BytesIO()
        img.save(img_io, 'PNG')
        img_io.seek(0)
        
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
        output_path = os.path.join('static', 'profiles', f'ID_Card_{student_id}.pdf')
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
        photo_path = os.path.join('static', 'profiles', f'student_{student_id}.jpg')
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
                roi_gray = cv2.resize(roi_gray, (200, 200))
                
                face_data_json = json.dumps(roi_gray.tolist())
                add_student_face(student_id, face_data_json)
                flash(f'Student registered successfully to {course} - {year}!', 'success')
            else:
                flash('Student added, but NO face detected in the photo. Attendance might not work.', 'error')
        except Exception as e:
            flash(f'An error occurred during photo processing: {str(e)}', 'error')

        # Reload camera training
        if camera_instance:
            camera_instance.load_and_train()
        else:
            get_camera().load_and_train()

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
def attendance():
    date_filter = request.args.get('date')
    report = get_attendance_report(date_filter)
    return render_template('attendance.html', report=report, current_date=date_filter)

@app.route('/export_csv')
@login_required
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
@login_required
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
                face_data_json = json.dumps(roi_gray.tolist())
                
                add_faculty_face(session.get('user_id'), face_data_json)
                
                if camera_instance: camera_instance.load_and_train()
                flash('Your face has been registered for attendance!', 'success')
            else:
                flash('No face detected. Please try a clearer photo.', 'error')
        except Exception as e:
            flash(f'Error: {str(e)}', 'error')
        return redirect(url_for('index'))
            
    return render_template('register_face.html')

@app.route('/manual_attendance', methods=['GET', 'POST'])
@login_required
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
@login_required
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
        
    data = request.json
    student_lat = float(data.get('lat', 0))
    student_lon = float(data.get('lon', 0))
    
    # Campus Coordinates (e.g., EThames Hyderabad)
    CAMPUS_LAT = 17.4300
    CAMPUS_LON = 78.4480
    ALLOWED_RADIUS = 300 # meters
    
    distance = haversine(student_lat, student_lon, CAMPUS_LAT, CAMPUS_LON)
    
    if distance <= ALLOWED_RADIUS:
        student_id = session.get('user_id')
        mark_attendance(student_id, role='Student', status='Present', override_subject="Mobile GPS Check-in")
        
        # Enterprise Audit
        log_audit_trail(student_id, f"Mobile Check-in (Distance: {int(distance)}m)", "Student", student_id, request.remote_addr)
        
        return {"success": True, "message": f"Verified! Distance from campus: {int(distance)}m"}
    else:
        # Enterprise Audit
        log_audit_trail(session.get('user_id'), f"Failed Check-in (Too far: {int(distance)}m)", "Student", session.get('user_id'), request.remote_addr)
        return {"error": f"You are {int(distance)} meters away. You must be on campus to check in."}, 403

@app.route('/delete_student/<int:id>', methods=['POST'])
@login_required
def delete_student_route(id):
    delete_student(id)
    flash('Student deleted successfully.', 'success')
    if camera_instance:
        camera_instance.load_and_train()
    else:
        get_camera().load_and_train()
        
    return redirect(url_for('manage_students'))

@app.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
    user_id = session.get('user_id')
    if session.get('role') == 'Student':
        flash('Student profile management is coming soon.', 'info')
        return redirect(url_for('student_dashboard'))
        
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

@app.route('/api/camera/start')
@login_required
def start_camera_api():
    cam = get_camera()
    cam.start_capture()
    return {"status": "started"}

@app.route('/api/camera/stop')
@login_required
def stop_camera_api():
    cam = get_camera()
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
@login_required
def web_scanner():
    return render_template('scanner.html')

@app.route('/api/cloud_scan', methods=['POST'])
@login_required
def cloud_scan():
    """Universal Scanning API for any device (Mobile/Tablet/PC)"""
    data = request.json
    if not data or 'image' not in data:
        return {"error": "No image data"}, 400
    
    # Decode base64 image from browser
    import base64
    try:
        header, encoded = data['image'].split(",", 1)
        image_bytes = base64.b64decode(encoded)
        nparr = np.frombuffer(image_bytes, np.uint8)
        frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        
        cam = get_camera()
        if not cam:
            return {"error": "AI Vision Engine offline"}, 500
            
        # Process the frame using our existing AI logic
        # We temporarily inject this frame into the camera's processing logic
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        faces = cam.face_cascade.detectMultiScale(gray, 1.1, 5)
        
        result = {"status": "No face detected", "match": None}
        
        for (x, y, w, h) in faces:
            roi_gray = gray[y:y+h, x:x+w]
            if cam.is_trained:
                label, confidence = cam.recognizer.predict(roi_gray)
                if confidence < 80:
                    person = cam.people_mapping.get(label)
                    if person:
                        # Mark attendance instantly in cloud
                        mark_attendance(person['id'], role=person['role'])
                        result = {
                            "status": "Success",
                            "match": person['name'],
                            "role": person['role'],
                            "confidence": round(100 - confidence, 1)
                        }
                        break
        return result
    except Exception as e:
        return {"error": str(e)}, 500

@app.route('/video_feed')
def video_feed():
    cam = get_camera()
    # Ensure capture is started if this route is called
    cam.start_capture()
    return Response(gen(cam),
                    mimetype='multipart/x-mixed-replace; boundary=frame')

if __name__ == '__main__':
    # Start the AI Auto-Repair Watchdog as a background process ONLY in local dev
    # We check for 'VERCEL' environment variable to disable it on cloud
    if os.environ.get('WERKZEUG_RUN_MAIN') == 'true' and not os.environ.get('VERCEL'):
        print("[System] Starting AI Auto-Repair Watchdog autonomously...")
        subprocess.Popen([sys.executable, "ai_watchdog.py"])

    # allow_unsafe_werkzeug=True is required for certain cloud environments
    socketio.run(app, host='0.0.0.0', port=5000, debug=True, allow_unsafe_werkzeug=True)
