"""
Interactive camera for the web UI (live preview and the browser scanner).

Continuous classroom monitoring is NOT done here - that belongs to
vision_worker.py, which runs as its own process and does not depend on a
browser being open. This class exists for the operator-facing preview and for
the on-demand scan endpoint, and shares its recognition code with the worker
via recognition.py.
"""
import hashlib
import hmac
import logging
import threading
import time

import cv2

import recognition
from database import get_student_by_qr, mark_attendance

logger = logging.getLogger('VeriVaultAI')

_tts_lock = threading.Lock()


def speak_text(text):
    """Announce a name. Silently does nothing where TTS is unavailable."""
    def run_speech():
        try:
            import pyttsx3
            with _tts_lock:
                engine = pyttsx3.init()
                engine.say(text)
                engine.runAndWait()
        except Exception as e:
            logger.debug(f"TTS unavailable: {e}")
    threading.Thread(target=run_speech, daemon=True).start()


class VideoCamera(object):
    def __init__(self, secret_key=None):
        self.secret_key = secret_key
        self.video = None

        self.face_cascade = recognition.get_face_cascade()
        self.profile_cascade = recognition.get_profile_cascade()
        self.eye_cascade = recognition.get_eye_cascade()
        self.qr_detector = cv2.QRCodeDetector()

        self.index = recognition.FaceIndex()
        self.tracker = recognition.FaceTracker()
        self.load_embeddings()

        self.last_qr_keys = set()
        self.announced = {}
        logger.info("Neural vision engine initialised.")

    def __del__(self):
        try:
            if self.video:
                self.video.release()
        except Exception:
            pass

    # -- hardware -----------------------------------------------------------

    def start_capture(self):
        if self.video is None:
            self.video = cv2.VideoCapture(0)
            logger.info("Camera hardware initialised.")

    def stop_capture(self):
        if self.video:
            self.video.release()
            self.video = None
            self.tracker = recognition.FaceTracker()
            logger.info("Camera hardware released.")

    # -- identities ---------------------------------------------------------

    def load_embeddings(self):
        try:
            count = self.index.load()
            logger.info(f"Loaded {count} identities for recognition.")
        except Exception as e:
            logger.error(f"Failed to load identities: {e}")

    @property
    def known_people(self):
        return self.index.people

    def recognize_face(self, face_img):
        """Returns (person, distance). Kept for the /api/cloud_scan endpoint."""
        embedding = recognition.embed_face(face_img)
        if embedding is None:
            return None, 1.0
        return self.index.match(embedding)

    # -- QR -----------------------------------------------------------------

    def _check_qr(self, image):
        """Verifies the rotating HMAC token and marks attendance on a match."""
        current = set()
        try:
            data, _bbox, _ = self.qr_detector.detectAndDecode(image)
            if not data:
                self.last_qr_keys = current
                return

            parts = data.split(':')
            if len(parts) != 3:
                return
            qr_hash, ts_str, signature = parts
            if abs(int(time.time() / 60) - int(ts_str)) > 2:
                return

            key = self.secret_key.encode() if isinstance(self.secret_key, str) else self.secret_key
            expected = hmac.new(key, f"{qr_hash}:{ts_str}".encode(), hashlib.sha256).hexdigest()[:16]
            if not hmac.compare_digest(signature, expected):
                return

            student = get_student_by_qr(qr_hash)
            if not student:
                return

            cv2.putText(image, f"Secure Auth: {student['name']}", (10, 30),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 200, 0), 2)
            marker = f"qr_{student['id']}"
            current.add(marker)
            if marker not in self.last_qr_keys:
                if mark_attendance(student['id'], role='Student', override_subject="Secure QR"):
                    speak_text(f"QR attendance marked for {student['name']}")
        except Exception as e:
            logger.debug(f"QR decode skipped: {e}")
        finally:
            self.last_qr_keys = current

    # -- frames -------------------------------------------------------------

    def get_frame(self):
        if self.video is None:
            return None
        success, image = self.video.read()
        if not success:
            return None

        self._check_qr(image)

        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        boxes = recognition.detect_faces_group(gray, self.face_cascade, self.profile_cascade)
        tracks = self.tracker.update(boxes)

        for track in tracks:
            x, y, w, h = track.box
            roi_colour = image[y:y + h, x:x + w]
            roi_gray = gray[y:y + h, x:x + w]
            if roi_colour.size == 0:
                continue

            if track.needs_identify:
                embedding = recognition.embed_face(roi_colour)
                if embedding is not None:
                    person, distance = self.index.match(embedding)
                    track.person, track.distance = person, distance
                    track.last_verified = time.time()

            live = recognition.update_liveness(track, roi_gray, self.eye_cascade)

            colour = (0, 200, 0) if live and track.person else (0, 165, 255)
            cv2.rectangle(image, (x, y), (x + w, y + h), colour, 2)
            label = f"{track.name}" + (f" ({track.person['role']})" if track.person else "")
            cv2.putText(image, label, (x, max(y - 10, 12)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, colour, 2)
            if not live:
                cv2.putText(image, "Blink to verify", (x, y + h + 20),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 165, 255), 1)

            if live and track.person:
                person = track.person
                if mark_attendance(person['id'], role=person['role'], captured_face=roi_colour):
                    last = self.announced.get(person['id'], 0)
                    if time.time() - last > 30:
                        speak_text(f"Welcome {person['name']}")
                        self.announced[person['id']] = time.time()

        ret, jpeg = cv2.imencode('.jpg', image)
        return jpeg.tobytes() if ret else None
