"""
Message bus between the vision workers and the attendance engine.

Redis is the intended transport when the system runs as separate containers.
On a single classroom machine - the Windows build - running a Redis server is
an unnecessary burden, so the bus falls back to writing sightings straight to
the database. Callers use the same API either way.
"""
import json
import logging
import os
import time

logger = logging.getLogger('VeriVault')

REDIS_URL = os.environ.get('REDIS_URL', 'redis://localhost:6379/0')
SIGHTINGS_STREAM = 'verivault:sightings'
CONSUMER_GROUP = 'engine'
RELOAD_CHANNEL = 'verivault:reload_faces'

PREVIEW_TTL = 5
HEARTBEAT_TTL = 30

_client = None
_mode = None


def _connect():
    global _client, _mode
    if _mode is not None:
        return _client

    try:
        import redis
        client = redis.from_url(REDIS_URL, socket_connect_timeout=1.5)
        client.ping()
        _client, _mode = client, 'redis'
        logger.info(f"Bus: connected to Redis at {REDIS_URL}")
    except Exception as e:
        _client, _mode = None, 'direct'
        logger.warning(f"Bus: Redis unavailable ({e}). Falling back to direct database writes.")
    return _client


def mode():
    _connect()
    return _mode


def available():
    return mode() == 'redis'


# ----------------------------------------------------------------------------
# Sightings
# ----------------------------------------------------------------------------

def publish_sightings(rows):
    """rows: iterable of (session_id, student_id, camera_id, ts, confidence, [box_x, box_y, box_w, box_h])"""
    rows = list(rows)
    if not rows:
        return 0

    client = _connect()
    if client is None:
        from presence import record_sightings
        return record_sightings(rows)

    try:
        pipe = client.pipeline()
        for item in rows:
            session_id, student_id, camera_id, ts, confidence = item[:5]
            box_x = item[5] if len(item) > 5 else ''
            box_y = item[6] if len(item) > 6 else ''
            box_w = item[7] if len(item) > 7 else ''
            box_h = item[8] if len(item) > 8 else ''
            pipe.xadd(SIGHTINGS_STREAM, {
                'session_id': str(session_id),
                'student_id': str(student_id),
                'camera_id': str(camera_id if camera_id is not None else ''),
                'ts': str(ts),
                'confidence': str(confidence if confidence is not None else ''),
                'box_x': str(box_x if box_x is not None else ''),
                'box_y': str(box_y if box_y is not None else ''),
                'box_w': str(box_w if box_w is not None else ''),
                'box_h': str(box_h if box_h is not None else ''),
            })
        pipe.execute()
        return len(rows)
    except Exception as e:
        logger.error(f"Bus: publish failed ({e}); writing directly instead.")
        from presence import record_sightings
        return record_sightings(rows)


def ensure_group():
    client = _connect()
    if client is None:
        return False
    try:
        client.xgroup_create(SIGHTINGS_STREAM, CONSUMER_GROUP, id='0', mkstream=True)
    except Exception:
        pass  # already exists
    return True


def consume_sightings(consumer='engine-1', count=500, block_ms=2000):
    """Drains pending sightings. Returns (rows, ack_ids)."""
    client = _connect()
    if client is None:
        return [], []

    try:
        response = client.xreadgroup(CONSUMER_GROUP, consumer,
                                     {SIGHTINGS_STREAM: '>'}, count=count, block=block_ms)
    except Exception as e:
        logger.error(f"Bus: consume failed: {e}")
        return [], []

    rows, ack_ids = [], []
    for _stream, entries in response or []:
        for entry_id, fields in entries:
            get = lambda k: fields.get(k.encode(), b'').decode()
            box = lambda k: int(get(k)) if get(k) else None
            try:
                # publish_sightings writes nine fields; reading back only five
                # silently dropped every seat coordinate on the way through
                # Redis, so the heatmap stayed empty on any Redis deployment
                # while working fine on the direct-write fallback.
                rows.append((
                    int(get('session_id')),
                    int(get('student_id')),
                    int(get('camera_id')) if get('camera_id') else None,
                    get('ts'),
                    float(get('confidence')) if get('confidence') else None,
                    box('box_x'), box('box_y'), box('box_w'), box('box_h'),
                ))
                ack_ids.append(entry_id)
            except (ValueError, TypeError):
                ack_ids.append(entry_id)  # malformed, drop it
    return rows, ack_ids


def ack(ids):
    client = _connect()
    if client is None or not ids:
        return
    try:
        client.xack(SIGHTINGS_STREAM, CONSUMER_GROUP, *ids)
    except Exception as e:
        logger.error(f"Bus: ack failed: {e}")


# ----------------------------------------------------------------------------
# Camera preview and health
# ----------------------------------------------------------------------------

def publish_preview(camera_id, jpeg_bytes):
    client = _connect()
    if client is None:
        return
    try:
        client.setex(f'verivault:camera:{camera_id}:preview', PREVIEW_TTL, jpeg_bytes)
    except Exception:
        pass


def get_preview(camera_id):
    client = _connect()
    if client is None:
        return None
    try:
        return client.get(f'verivault:camera:{camera_id}:preview')
    except Exception:
        return None


def heartbeat(camera_id, payload):
    client = _connect()
    if client is None:
        return
    try:
        client.setex(f'verivault:camera:{camera_id}:heartbeat', HEARTBEAT_TTL, json.dumps(payload))
    except Exception:
        pass


def get_heartbeat(camera_id):
    client = _connect()
    if client is None:
        return None
    try:
        raw = client.get(f'verivault:camera:{camera_id}:heartbeat')
        return json.loads(raw) if raw else None
    except Exception:
        return None


# ----------------------------------------------------------------------------
# Face index invalidation
# ----------------------------------------------------------------------------

def signal_face_reload():
    """Tell every worker to reload embeddings after an enrolment change."""
    client = _connect()
    if client is None:
        return
    try:
        client.publish(RELOAD_CHANNEL, str(time.time()))
        client.set('verivault:faces:version', str(time.time()))
    except Exception:
        pass


def face_version():
    client = _connect()
    if client is None:
        return None
    try:
        raw = client.get('verivault:faces:version')
        return raw.decode() if raw else None
    except Exception:
        return None
