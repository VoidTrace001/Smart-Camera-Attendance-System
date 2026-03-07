import cv2
import numpy as np
import json
import os
import threading
import pyttsx3
from pathlib import Path
from database import mark_attendance, get_student_by_qr

def speak_text(text):
    """Runs TTS in a separate thread to prevent freezing the camera feed."""
    def run_speech():
        try:
            engine = pyttsx3.init()
            engine.say(text)
            engine.runAndWait()
        except Exception as e:
            print(f"TTS Error: {e}")
    threading.Thread(target=run_speech, daemon=True).start()

class VideoCamera(object):
    def __init__(self):
        self.video = None # Do NOT open hardware yet
        
        # Load Haar Cascades
        cascade_path = cv2.data.haarcascades + 'haarcascade_frontalface_default.xml' if hasattr(cv2, 'data') else str(Path(cv2.__file__).parent / 'data' / 'haarcascade_frontalface_default.xml')
        self.face_cascade = cv2.CascadeClassifier(cascade_path)
        
        eye_cascade_path = cv2.data.haarcascades + 'haarcascade_eye.xml' if hasattr(cv2, 'data') else str(Path(cv2.__file__).parent / 'data' / 'haarcascade_eye.xml')
        self.eye_cascade = cv2.CascadeClassifier(eye_cascade_path)
        
        # Create LBPH Face Recognizer
        self.recognizer = cv2.face.LBPHFaceRecognizer_create() if hasattr(cv2.face, 'LBPHFaceRecognizer_create') else cv2.face.createLBPHFaceRecognizer()
        
        # Initialize OpenCV QR Detector
        self.qr_detector = cv2.QRCodeDetector()
        
        self.people_mapping = {}
        self.is_trained = False
        self.load_and_train()
        
        # Multi-Face Tracking State
        self.face_states = {} # Maps person_key to their blink/liveness state
        self.last_qr_keys = set() # Keep track of recently scanned QRs to prevent spamming

    def __del__(self):
        if self.video:
            self.video.release()

    def start_capture(self):
        """Explicitly turn on the camera hardware."""
        if self.video is None:
            self.video = cv2.VideoCapture(0)
            print("Camera hardware initialized.")

    def stop_capture(self):
        """Explicitly turn off the camera hardware."""
        if self.video:
            self.video.release()
            self.video = None
            print("Camera hardware released.")

    def load_and_train(self):
        """Loads all faces (Students + Faculty) and trains the recognizer."""
        from database import get_all_people_with_faces
        people_data, mapping = get_all_people_with_faces()
        
        faces = []
        labels = []
        self.people_mapping = mapping
        
        print(f"Loading {len(people_data)} faces for training...")
        
        for p in people_data:
            try:
                face_img = np.array(json.loads(p['face_encoding']), dtype='uint8')
                faces.append(face_img)
                labels.append(p['label'])
            except Exception as e:
                print(f"Error loading face: {e}")

        if len(faces) > 0:
            self.recognizer.train(faces, np.array(labels))
            self.is_trained = True
            print("Unified model trained successfully.")
        else:
            self.is_trained = False

    def get_frame(self):
        if self.video is None:
            return None
            
        success, image = self.video.read()
        if not success: return None

        current_frame_qr_keys = set()
        current_frame_face_keys = set()

        # 1. Check for QR Codes (Fallback System using OpenCV)
        try:
            data, bbox, _ = self.qr_detector.detectAndDecode(image)
            if data:
                student = get_student_by_qr(data)
                if student:
                    cv2.putText(image, f"QR Auth: {student['name']}", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 0, 0), 2)
                    
                    qr_key = f"Student_QR_{student['id']}"
                    current_frame_qr_keys.add(qr_key)
                    
                    if qr_key not in self.last_qr_keys:
                        if mark_attendance(student['id'], role='Student'):
                            speak_text(f"QR Attendance marked for {student['name']}")
        except: pass

        # Update last QR keys for next frame
        self.last_qr_keys = current_frame_qr_keys

        # 2. Check for Faces
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        faces = self.face_cascade.detectMultiScale(gray, 1.1, 5)

        for (x, y, w, h) in faces:
            roi_gray = gray[y:y+h, x:x+w]
            label_text = "Unknown"
            color = (0, 0, 255)
            
            # Predict early to get the ID for tracking state
            predicted_key = None
            person_name = "Unknown"
            person_role = ""
            confidence = 100
            person_db_id = None
            
            if self.is_trained:
                try:
                    label, confidence = self.recognizer.predict(roi_gray)
                    if confidence < 80:
                        person = self.people_mapping.get(label)
                        if person:
                            person_name = person['name']
                            person_role = person['role']
                            person_db_id = person['id']
                            predicted_key = f"{person_role}_{person_db_id}"
                            label_text = f"{person_name} ({person_role})"
                            color = (0, 255, 0)
                except: pass

            # Fallback key if unknown (track by coordinates loosely, but mainly we care about recognized people)
            track_key = predicted_key if predicted_key else f"unknown_{x//20}_{y//20}"
            current_frame_face_keys.add(track_key)
            
            # Initialize state for new faces
            if track_key not in self.face_states:
                self.face_states[track_key] = {'blink_counter': 0, 'blink_detected': False}

            state = self.face_states[track_key]

            # Engagement & Blink AI
            eyes = self.eye_cascade.detectMultiScale(roi_gray, 1.1, 3)
            engagement_text = "Distracted"
            engagement_color = (0, 0, 255)
            
            if len(eyes) >= 2:
                engagement_text = "Highly Engaged"
                engagement_color = (0, 255, 0)
                state['blink_counter'] = 0
            elif len(eyes) == 1:
                engagement_text = "Attentive"
                engagement_color = (255, 255, 0)
                state['blink_counter'] = 0
            else:
                state['blink_counter'] += 1
                if state['blink_counter'] >= 1: 
                    state['blink_detected'] = True

            # Display Liveness and Engagement (Next-Gen Analytics)
            is_live = state['blink_detected']
            
            # FUTURE EXPANSION: DeepFace Integration Point
            # if USE_DEEPFACE:
            #     emotion = DeepFace.analyze(roi_gray, actions=['emotion'], enforce_detection=False)[0]['dominant_emotion']
            #     engagement_text = f"Emotion: {emotion}"
            
            if is_live:
                cv2.putText(image, f"Live | {engagement_text}", (x, y+h+20), cv2.FONT_HERSHEY_SIMPLEX, 0.6, engagement_color, 2)
            else:
                cv2.putText(image, "Blink to verify (AI Anti-Spoof)", (x, y+h+20), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 165, 255), 2)

            # Mark Attendance for recognized and live faces
            if self.is_trained and is_live and predicted_key:
                if mark_attendance(person_db_id, role=person_role):
                    speak_text(f"Attendance marked for {person_name}")

            cv2.rectangle(image, (x, y), (x+w, y+h), color, 2)
            cv2.putText(image, label_text, (x, y-10), cv2.FONT_HERSHEY_SIMPLEX, 0.8, color, 2)

        # Cleanup old states for faces that left the frame
        keys_to_remove = [k for k in self.face_states.keys() if k not in current_frame_face_keys]
        for k in keys_to_remove:
            del self.face_states[k]

        ret, jpeg = cv2.imencode('.jpg', image)
        return jpeg.tobytes()
