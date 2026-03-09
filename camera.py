import cv2
import numpy as np
import json
import os
import threading
import pyttsx3
import hmac
import hashlib
import time
from pathlib import Path
from database import mark_attendance, get_student_by_qr, get_all_people_with_embeddings
from deepface import DeepFace
import logging

logger = logging.getLogger('VeriVaultAI')

def speak_text(text):
    """Runs TTS in a separate thread to prevent freezing the camera feed."""
    def run_speech():
        try:
            engine = pyttsx3.init()
            engine.say(text)
            engine.runAndWait()
        except Exception as e:
            logger.error(f"TTS Error: {e}")
    threading.Thread(target=run_speech, daemon=True).start()

class VideoCamera(object):
    def __init__(self, secret_key=None):
        self.secret_key = secret_key
        self.video = None 
        
        # Load Haar Cascades for fast detection (DeepFace will handle recognition)
        cascade_path = cv2.data.haarcascades + 'haarcascade_frontalface_default.xml' if hasattr(cv2, 'data') else str(Path(cv2.__file__).parent / 'data' / 'haarcascade_frontalface_default.xml')
        self.face_cascade = cv2.CascadeClassifier(cascade_path)
        
        eye_cascade_path = cv2.data.haarcascades + 'haarcascade_eye.xml' if hasattr(cv2, 'data') else str(Path(cv2.__file__).parent / 'data' / 'haarcascade_eye.xml')
        self.eye_cascade = cv2.CascadeClassifier(eye_cascade_path)
        
        # Initialize OpenCV QR Detector
        self.qr_detector = cv2.QRCodeDetector()
        
        # DeepFace Settings
        self.model_name = "Facenet" # Fast and accurate (128d)
        self.detector_backend = "opencv" # Fast detection
        self.distance_metric = "cosine"
        self.threshold = 0.40 # Adjust for sensitivity
        
        self.known_people = [] # List of dicts {id, name, role, embedding}
        self.load_embeddings()
        
        # Multi-Face Tracking State
        self.face_states = {} # Maps tracking keys to state
        self.last_qr_keys = set()
        
        logger.info("DeepFace Neural Vision Engine Initialized.")

    def __del__(self):
        if self.video:
            self.video.release()

    def start_capture(self):
        if self.video is None:
            self.video = cv2.VideoCapture(0)
            logger.info("Camera hardware initialized.")

    def stop_capture(self):
        if self.video:
            self.video.release()
            self.video = None
            logger.info("Camera hardware released.")

    def load_embeddings(self):
        """Loads all neural embeddings from the database."""
        try:
            self.known_people = get_all_people_with_embeddings()
            logger.info(f"Loaded {len(self.known_people)} neural embeddings for recognition.")
        except Exception as e:
            logger.error(f"Failed to load embeddings: {e}")

    def recognize_face(self, face_img):
        """Compares a face image against known embeddings using Cosine Similarity."""
        if not self.known_people:
            return None, 1.0

        try:
            # Generate embedding for the current face
            target_embedding = DeepFace.represent(
                img_path=face_img, 
                model_name=self.model_name, 
                enforce_detection=False,
                detector_backend=self.detector_backend
            )[0]["embedding"]
            
            best_match = None
            min_dist = 1.0
            
            for person in self.known_people:
                # Cosine distance calculation
                dist = self.calculate_distance(target_embedding, person['embedding'])
                if dist < min_dist:
                    min_dist = dist
                    best_match = person
            
            if min_dist <= self.threshold:
                return best_match, min_dist
        except Exception as e:
            logger.error(f"Recognition Error: {e}")
            
        return None, 1.0

    def calculate_distance(self, embed1, embed2):
        a = np.array(embed1)
        b = np.array(embed2)
        return 1 - (np.dot(a, b) / (np.linalg.norm(a) * np.dot(b, b)**0.5))

    def analyze_engagement(self, face_img, track_key):
        """Asynchronously analyzes emotion and engagement."""
        def run_analysis():
            try:
                objs = DeepFace.analyze(
                    img_path=face_img, 
                    actions=['emotion'], 
                    enforce_detection=False,
                    detector_backend=self.detector_backend,
                    silent=True
                )
                if objs and track_key in self.face_states:
                    emotion = objs[0]['dominant_emotion']
                    # Map emotion to engagement score
                    engagement_map = {
                        'happy': 'Positive / Engaged',
                        'neutral': 'Focused',
                        'surprise': 'Highly Interested',
                        'sad': 'Bored / Disengaged',
                        'angry': 'Frustrated',
                        'fear': 'Confused',
                        'disgust': 'Averse'
                    }
                    self.face_states[track_key]['emotion'] = engagement_map.get(emotion, "Analyzing...")
            except: pass

        threading.Thread(target=run_analysis, daemon=True).start()

    def get_frame(self):
        if self.video is None: return None
        success, image = self.video.read()
        if not success: return None

        current_frame_qr_keys = set()
        current_frame_face_keys = set()

        # 1. QR AUTH (Enterprise HMAC)
        try:
            data, bbox, _ = self.qr_detector.detectAndDecode(image)
            if data:
                parts = data.split(':')
                if len(parts) == 3:
                    qr_hash, ts_str, signature = parts
                    if abs(int(time.time() / 60) - int(ts_str)) <= 2:
                        raw_data = f"{qr_hash}:{ts_str}"
                        expected_sig = hmac.new(self.secret_key.encode() if isinstance(self.secret_key, str) else self.secret_key, raw_data.encode(), hashlib.sha256).hexdigest()[:16]
                        if hmac.compare_digest(signature, expected_sig):
                            student = get_student_by_qr(qr_hash)
                            if student:
                                cv2.putText(image, f"Secure Auth: {student['name']}", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)
                                qr_key = f"Student_QR_{student['id']}"
                                current_frame_qr_keys.add(qr_key)
                                if qr_key not in self.last_qr_keys:
                                    if mark_attendance(student['id'], role='Student', override_subject="Secure QR"):
                                        speak_text(f"QR Attendance marked for {student['name']}")
        except: pass
        self.last_qr_keys = current_frame_qr_keys

        # 2. NEURAL FACE RECOGNITION
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        faces = self.face_cascade.detectMultiScale(gray, 1.1, 5)

        for (x, y, w, h) in faces:
            roi_color = image[y:y+h, x:x+w]
            roi_gray = gray[y:y+h, x:x+w]
            
            # Tracking and State
            track_key = f"face_{x//20}_{y//20}"
            current_frame_face_keys.add(track_key)
            if track_key not in self.face_states:
                self.face_states[track_key] = {'blink_count': 0, 'verified': False, 'name': 'Scanning...', 'role': '', 'id': None, 'emotion': 'Analyzing...'}
            
            state = self.face_states[track_key]

            # Recognition (Every 30 frames to save CPU)
            if state['name'] == 'Scanning...':
                person, dist = self.recognize_face(roi_color)
                if person:
                    state['name'] = person['name']
                    state['role'] = person['role']
                    state['id'] = person['id']
                    # Start emotion analysis once recognized
                    self.analyze_engagement(roi_color, track_key)

            # Blink Detection (Liveness)
            eyes = self.eye_cascade.detectMultiScale(roi_gray, 1.1, 3)
            if len(eyes) == 0: 
                state['blink_count'] += 1
                if state['blink_count'] >= 1: state['verified'] = True

            # UI Feedback
            color = (0, 255, 0) if state['verified'] else (0, 165, 255)
            cv2.rectangle(image, (x, y), (x+w, y+h), color, 2)
            cv2.putText(image, f"{state['name']} ({state['role']})", (x, y-10), cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)
            
            # Engagement Overlay
            cv2.putText(image, f"Mood: {state['emotion']}", (x, y+h+20), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
            
            if not state['verified']:
                cv2.putText(image, "Blink to Verify", (x, y+h+40), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 165, 255), 1)

            # Mark Attendance
            if state['verified'] and state['id']:
                if mark_attendance(state['id'], role=state['role'], captured_face=roi_color):
                    speak_text(f"Welcome {state['name']}")

        # Cleanup
        keys_to_remove = [k for k in self.face_states.keys() if k not in current_frame_face_keys]
        for k in keys_to_remove: del self.face_states[k]

        ret, jpeg = cv2.imencode('.jpg', image)
        return jpeg.tobytes()
