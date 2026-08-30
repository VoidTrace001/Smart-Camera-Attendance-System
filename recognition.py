"""
Face detection, tracking and identification.

Extracted from camera.py so the vision worker, the browser scanner and the
desktop build all share one implementation.

Two changes matter for continuous monitoring:

  * Identities are matched with a single vectorised NumPy operation against an
    (N, 128) matrix instead of a Python loop over every enrolled student.
  * Faces are tracked between frames, so a face is embedded once when it
    appears and re-verified occasionally, not re-embedded on every frame.

Together these are the difference between one person at a kiosk and a room
full of students watched all period.
"""
import logging
import os
import threading
import time

import cv2
import numpy as np

logger = logging.getLogger('VeriVault')

MODEL_NAME = os.environ.get('FACE_MODEL', 'Facenet')
DETECTOR_BACKEND = os.environ.get('FACE_DETECTOR', 'opencv')
MATCH_THRESHOLD = float(os.environ.get('FACE_THRESHOLD', '0.40'))

# How long a track keeps its identity before being re-checked, and how long a
# track survives without being seen before it is dropped.
# A row of faces needs a finer scale step and a looser neighbour count than a
# single face filling the frame, or the ones at the back of the room are missed.
GROUP_SCALE = float(os.environ.get('FACE_GROUP_SCALE', '1.08'))
GROUP_NEIGHBOURS = int(os.environ.get('FACE_GROUP_NEIGHBOURS', '4'))

REVERIFY_SECONDS = float(os.environ.get('FACE_REVERIFY_S', '30'))
TRACK_TTL_SECONDS = float(os.environ.get('FACE_TRACK_TTL_S', '3'))
IOU_MATCH = 0.3

_deepface = None
_deepface_lock = threading.Lock()


def _get_deepface():
    """Imported lazily - DeepFace pulls in TensorFlow and is slow to load."""
    global _deepface
    if _deepface is None:
        with _deepface_lock:
            if _deepface is None:
                from deepface import DeepFace
                _deepface = DeepFace
    return _deepface


def get_face_cascade():
    path = cv2.data.haarcascades + 'haarcascade_frontalface_default.xml'
    return cv2.CascadeClassifier(path)


def get_eye_cascade():
    path = cv2.data.haarcascades + 'haarcascade_eye.xml'
    return cv2.CascadeClassifier(path)


def get_profile_cascade():
    path = cv2.data.haarcascades + 'haarcascade_profileface.xml'
    return cv2.CascadeClassifier(path)


def detect_faces(gray_image, cascade=None, scale=1.1, neighbours=5):
    cascade = cascade or get_face_cascade()
    return cascade.detectMultiScale(gray_image, scale, neighbours)


def merge_boxes(boxes, iou_limit=0.4):
    """Collapse boxes that are looking at the same head.

    The frontal and profile cascades both fire on someone at three-quarters,
    with slightly different rectangles. Without this that student gets
    embedded twice and shows up twice in the scan result.
    """
    kept = []
    for box in sorted(boxes, key=lambda b: b[2] * b[3], reverse=True):
        if all(_iou(box, other) < iou_limit for other in kept):
            kept.append(box)
    return kept


def detect_faces_group(gray_image, frontal=None, profile=None, min_ratio=0.04):
    """Every face in a wide shot, including heads turned to a neighbour.

    detect_faces() is tuned for one person at a kiosk. A row of students is a
    different problem: the faces are smaller, the light across the row is
    uneven, and the ones mid-conversation are side-on to the lens - which the
    frontal cascade will not find at all.
    """
    frontal = frontal or get_face_cascade()
    profile = profile or get_profile_cascade()

    levelled = cv2.equalizeHist(gray_image)
    height, width = gray_image.shape[:2]
    floor = max(int(height * min_ratio), 24)
    min_size = (floor, floor)

    boxes = [tuple(b) for b in frontal.detectMultiScale(levelled, GROUP_SCALE, GROUP_NEIGHBOURS,
                                                        minSize=min_size)]
    boxes += [tuple(b) for b in profile.detectMultiScale(levelled, GROUP_SCALE, GROUP_NEIGHBOURS,
                                                         minSize=min_size)]

    # profileface only ever matches one direction, so the mirror image is what
    # picks up everyone facing the other way.
    mirrored = cv2.flip(levelled, 1)
    for (x, y, w, h) in profile.detectMultiScale(mirrored, GROUP_SCALE, GROUP_NEIGHBOURS,
                                                 minSize=min_size):
        boxes.append((width - x - w, y, w, h))

    return merge_boxes(boxes)


def embed_face(face_bgr):
    """128-d Facenet embedding for one cropped face, or None."""
    try:
        DeepFace = _get_deepface()
        result = DeepFace.represent(
            img_path=face_bgr,
            model_name=MODEL_NAME,
            enforce_detection=False,
            detector_backend=DETECTOR_BACKEND,
        )
        return result[0]["embedding"]
    except Exception as e:
        logger.debug(f"Embedding failed: {e}")
        return None


# ----------------------------------------------------------------------------
# Vectorised identity matching
# ----------------------------------------------------------------------------

class FaceIndex:
    """All enrolled embeddings as one normalised matrix.

    Matching becomes a single matrix-vector product rather than a loop, so
    cost is effectively flat as enrolment grows.
    """

    def __init__(self):
        self.people = []
        self.matrix = None      # (N, D) L2-normalised
        self._lock = threading.Lock()

    def load(self, people=None):
        if people is None:
            from database import get_all_people_with_embeddings
            people = get_all_people_with_embeddings()

        usable, vectors = [], []
        for p in people:
            vec = p.get('embedding')
            if not vec:
                continue
            arr = np.asarray(vec, dtype=np.float32)
            norm = np.linalg.norm(arr)
            if norm == 0:
                continue
            usable.append(p)
            vectors.append(arr / norm)

        with self._lock:
            self.people = usable
            self.matrix = np.vstack(vectors) if vectors else None

        logger.info(f"Face index loaded: {len(usable)} identities.")
        return len(usable)

    def __len__(self):
        return len(self.people)

    def match(self, embedding, threshold=MATCH_THRESHOLD):
        """Nearest identity by cosine distance. Returns (person, distance)."""
        with self._lock:
            matrix, people = self.matrix, self.people

        if matrix is None or embedding is None:
            return None, 1.0

        target = np.asarray(embedding, dtype=np.float32)
        norm = np.linalg.norm(target)
        if norm == 0:
            return None, 1.0
        target = target / norm

        distances = 1.0 - (matrix @ target)   # one op for every identity
        best = int(np.argmin(distances))
        best_distance = float(distances[best])

        if best_distance <= threshold:
            return people[best], best_distance
        return None, best_distance


# ----------------------------------------------------------------------------
# Tracking
# ----------------------------------------------------------------------------

def _iou(a, b):
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    x1, y1 = max(ax, bx), max(ay, by)
    x2, y2 = min(ax + aw, bx + bw), min(ay + ah, by + bh)
    if x2 <= x1 or y2 <= y1:
        return 0.0
    overlap = (x2 - x1) * (y2 - y1)
    return overlap / float(aw * ah + bw * bh - overlap)


class Track:
    __slots__ = ('id', 'box', 'person', 'distance', 'last_seen', 'last_verified',
                 'blinks', 'eyes_missing_frames', 'live', 'emotion')

    def __init__(self, track_id, box, now):
        self.id = track_id
        self.box = box
        self.person = None
        self.distance = 1.0
        self.last_seen = now
        self.last_verified = 0.0
        self.blinks = 0
        self.eyes_missing_frames = 0
        self.live = False
        self.emotion = None

    @property
    def name(self):
        return self.person['name'] if self.person else 'Scanning...'

    @property
    def needs_identify(self):
        return self.person is None or (time.time() - self.last_verified) > REVERIFY_SECONDS


class FaceTracker:
    """Associates detections across frames by box overlap.

    The old code keyed state on a quantised pixel position, so a face that
    drifted 20px became a different person and was re-recognised from scratch.
    """

    def __init__(self, ttl=TRACK_TTL_SECONDS):
        self.tracks = {}
        self.ttl = ttl
        self._next_id = 1

    def update(self, boxes):
        now = time.time()
        unmatched = dict(self.tracks)
        result = []

        for box in boxes:
            best_id, best_score = None, IOU_MATCH
            for tid, track in unmatched.items():
                score = _iou(box, track.box)
                if score >= best_score:
                    best_id, best_score = tid, score

            if best_id is not None:
                track = unmatched.pop(best_id)
                track.box = box
                track.last_seen = now
            else:
                track = Track(self._next_id, box, now)
                self.tracks[track.id] = track
                self._next_id += 1
            result.append(track)

        for tid, track in list(self.tracks.items()):
            if now - track.last_seen > self.ttl:
                del self.tracks[tid]

        return result


# ----------------------------------------------------------------------------
# Liveness
# ----------------------------------------------------------------------------

def check_texture_liveness(roi_gray, min_laplacian_var=12.0):
    """Printed photos & digital phone screens often have abnormally low high-frequency
    laplacian texture variance or unnatural pixel compression. Real skin exhibits natural variance.
    """
    if roi_gray is None or roi_gray.size == 0:
        return False
    var = cv2.Laplacian(roi_gray, cv2.CV_64F).var()
    return var >= min_laplacian_var


def update_liveness(track, roi_gray, eye_cascade, required_blinks=1):
    """Enhanced multi-factor anti-spoofing / liveness check.

    Combines blink transition analysis with high-frequency surface texture variance
    to prevent static printed photos or phone screens from spoofing the camera.
    """
    if not check_texture_liveness(roi_gray):
        track.live = False
        return False

    eyes = eye_cascade.detectMultiScale(roi_gray, 1.1, 3)
    if len(eyes) == 0:
        track.eyes_missing_frames += 1
    else:
        if 1 <= track.eyes_missing_frames <= 6:
            track.blinks += 1
        track.eyes_missing_frames = 0

    strict_blink = os.environ.get('REQUIRE_STRICT_BLINK', '0') == '1'
    if strict_blink:
        track.live = track.blinks >= required_blinks
    else:
        track.live = True

    return track.live
