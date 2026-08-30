"""
VeriVault vision worker - one process per classroom camera.

Runs independently of the web app. Nothing here is driven by an HTTP request,
so monitoring continues whether or not anyone has a browser open. That was the
central limitation of the original design: frames were only pulled while a
client was consuming the /video_feed generator.

Each tick the worker asks whether a class is scheduled in this room right now.
If none is, it sleeps - no frames are captured and nothing is written. The
camera is only ever looking during a class it is supposed to be watching.

Usage:
    CAMERA_ID=1 python vision_worker.py
"""
import logging
import os
import signal
import time
from datetime import datetime

os.environ.setdefault('TF_CPP_MIN_LOG_LEVEL', '3')
os.environ.setdefault('TF_ENABLE_ONEDNN_OPTS', '0')

import cv2
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - VisionWorker - %(levelname)s - %(message)s')
logger = logging.getLogger('VeriVault')

import bus
import presence
import recognition

CAMERA_ID = int(os.environ.get('CAMERA_ID', '1'))
IDLE_SLEEP = float(os.environ.get('IDLE_SLEEP_S', '15'))
REQUIRE_LIVENESS = os.environ.get('REQUIRE_LIVENESS', '0') == '1'

_running = True


def _stop(signum, frame):
    global _running
    logger.info("Shutdown signal received; finishing current frame.")
    _running = False


class VisionWorker:
    def __init__(self, camera_id):
        self.camera_id = camera_id
        self.camera = presence.get_camera(camera_id)
        if not self.camera:
            raise SystemExit(f"No camera row with id={camera_id}. Add one in the admin dashboard first.")

        self.face_cascade = recognition.get_face_cascade()
        self.profile_cascade = recognition.get_profile_cascade()
        self.eye_cascade = recognition.get_eye_cascade()
        self.index = recognition.FaceIndex()
        self.index.load()
        self.tracker = recognition.FaceTracker()

        self.video = None
        self.session = None
        self.pending = []
        self.last_bucket = {}          # student_id -> last bucket index emitted
        self.last_flush = time.time()
        self.face_version = bus.face_version()
        self.frames = 0

        cfg = presence.get_config()
        self.sample_interval = float(cfg['sample_interval_s'])
        self.bucket_s = int(cfg['bucket_s'])

        logger.info(f"Camera {camera_id} '{self.camera['name']}' in {self.camera['room']}: "
                    f"{len(self.index)} identities, sampling every {self.sample_interval}s")

    # -- capture ------------------------------------------------------------

    def open_capture(self):
        if self.video is not None:
            return
        source = self.camera['source']
        source = int(source) if str(source).isdigit() else source
        self.video = cv2.VideoCapture(source)
        if not self.video.isOpened():
            logger.error(f"Could not open camera source {source!r}")
            self.video = None
        else:
            logger.info(f"Capture opened on source {source!r}")

    def close_capture(self):
        if self.video is not None:
            self.video.release()
            self.video = None
            self.tracker = recognition.FaceTracker()
            logger.info("Capture released (no class in session).")

    # -- identity index -----------------------------------------------------

    def refresh_index_if_stale(self):
        version = bus.face_version()
        if version and version != self.face_version:
            logger.info("Enrolment changed; reloading face index.")
            self.index.load()
            self.face_version = version

    # -- per-frame work -----------------------------------------------------

    def process_frame(self, frame, session):
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        boxes = recognition.detect_faces_group(gray, self.face_cascade, self.profile_cascade)
        tracks = self.tracker.update(boxes)
        now = datetime.now()

        for track in tracks:
            x, y, w, h = track.box
            roi_colour = frame[y:y + h, x:x + w]
            roi_gray = gray[y:y + h, x:x + w]
            if roi_colour.size == 0:
                continue

            # Embed only when the track is new or due for re-verification,
            # not on every frame.
            if track.needs_identify:
                embedding = recognition.embed_face(roi_colour)
                if embedding is not None:
                    person, distance = self.index.match(embedding)
                    track.person, track.distance = person, distance
                    track.last_verified = time.time()

            if REQUIRE_LIVENESS:
                recognition.update_liveness(track, roi_gray, self.eye_cascade)

            self.record(track, session, now)
            self.annotate(frame, track)

        return len(tracks)

    def record(self, track, session, now):
        """Emit at most one sighting per student per bucket."""
        person = track.person
        if not person or person.get('role') != 'Student':
            return
        if REQUIRE_LIVENESS and not track.live:
            return

        bucket = int(now.timestamp() // self.bucket_s)
        if self.last_bucket.get(person['id']) == bucket:
            return
        self.last_bucket[person['id']] = bucket

        bx, by, bw, bh = track.box
        self.pending.append((session['id'], person['id'], self.camera_id,
                             presence.fmt_ts(now), round(track.distance, 4),
                             int(bx), int(by), int(bw), int(bh)))

    def annotate(self, frame, track):
        x, y, w, h = track.box
        identified = track.person is not None
        colour = (0, 200, 0) if identified else (0, 165, 255)
        cv2.rectangle(frame, (x, y), (x + w, y + h), colour, 2)
        label = track.name if identified else 'Unknown'
        cv2.putText(frame, label, (x, max(y - 8, 12)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, colour, 2)

    def flush(self, force=False):
        if not self.pending:
            return
        if force or len(self.pending) >= 50 or (time.time() - self.last_flush) > 10:
            count = bus.publish_sightings(self.pending)
            logger.debug(f"Published {count} sightings")
            self.pending.clear()
            self.last_flush = time.time()

    # -- main loop ----------------------------------------------------------

    def run(self):
        logger.info("Vision worker running. Monitoring is continuous and independent of the web UI.")
        while _running:
            session = presence.get_live_session_for_camera(self.camera_id)

            if not session:
                if self.session is not None:
                    self.flush(force=True)
                    logger.info(f"Session {self.session['id']} window ended.")
                    self.session = None
                    self.last_bucket.clear()
                self.close_capture()
                bus.heartbeat(self.camera_id, {'state': 'idle', 'at': presence.fmt_ts(datetime.now())})
                time.sleep(IDLE_SLEEP)
                continue

            if self.session is None or self.session['id'] != session['id']:
                logger.info(f"Session {session['id']} live: {session['subject']} "
                            f"({session['course']} {session['year']}) "
                            f"{session['start_ts'][11:16]}-{session['end_ts'][11:16]}")
                self.session = session
                self.last_bucket.clear()
                presence.set_session_status(session['id'], 'live')

            self.open_capture()
            if self.video is None:
                bus.heartbeat(self.camera_id, {'state': 'camera_error',
                                               'at': presence.fmt_ts(datetime.now())})
                time.sleep(IDLE_SLEEP)
                continue

            ok, frame = self.video.read()
            if not ok:
                logger.warning("Frame grab failed; reopening capture.")
                self.close_capture()
                time.sleep(1)
                continue

            self.refresh_index_if_stale()
            faces = self.process_frame(frame, session)
            self.frames += 1
            self.flush()

            ok, jpeg = cv2.imencode('.jpg', frame)
            if ok:
                bus.publish_preview(self.camera_id, jpeg.tobytes())

            bus.heartbeat(self.camera_id, {
                'state': 'monitoring',
                'session_id': session['id'],
                'subject': session['subject'],
                'faces': faces,
                'identities': len(self.index),
                'frames': self.frames,
                'at': presence.fmt_ts(datetime.now()),
            })

            time.sleep(self.sample_interval)

        self.flush(force=True)
        self.close_capture()
        logger.info("Vision worker stopped.")


if __name__ == '__main__':
    signal.signal(signal.SIGINT, _stop)
    try:
        signal.signal(signal.SIGTERM, _stop)
    except (AttributeError, ValueError):
        pass

    print("=" * 58)
    print("  VERIVAULT AI - VISION WORKER")
    print("=" * 58)
    VisionWorker(CAMERA_ID).run()
