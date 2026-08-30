"""
Request-level protections: CSRF, session cookie policy, login throttling and
response headers.

None of this existed while the app only ever ran on a classroom PC. It matters
now that the plan is to reach it from a phone over a public hostname, where the
browser will happily send session cookies on a request some other page started.

Wire it up once, in app.py:

    from security import init_security
    init_security(app)
"""
import hmac
import logging
import os
import secrets
import threading
import time
from collections import defaultdict

from flask import abort, request, session

logger = logging.getLogger('VeriVaultAI')

CSRF_SESSION_KEY = '_csrf_token'
CSRF_FORM_FIELD = '_csrf'
CSRF_HEADER = 'X-CSRF-Token'
SAFE_METHODS = {'GET', 'HEAD', 'OPTIONS', 'TRACE'}

# Routes that legitimately cannot carry a token. Keep this list short and
# justify every entry - it is the obvious place for a hole to appear.
CSRF_EXEMPT = set()

# Everything the templates pull from a CDN. A page that tries to load a script
# from anywhere else is doing something we did not ask for.
SCRIPT_SOURCES = "'self' 'unsafe-inline' https://cdn.jsdelivr.net https://cdn.datatables.net " \
                 "https://code.jquery.com https://unpkg.com https://cdn.socket.io"
STYLE_SOURCES = "'self' 'unsafe-inline' https://cdn.datatables.net https://unpkg.com " \
                "https://fonts.googleapis.com"
IMAGE_SOURCES = "'self' data: blob: https://*.tile.openstreetmap.org"
FONT_SOURCES = "'self' https://fonts.gstatic.com data:"

# Login throttling. In-process, which is correct for the single eventlet worker
# in the Procfile; a multi-worker deployment would need this in Redis.
MAX_ATTEMPTS = 5
ATTEMPT_WINDOW_S = 300
LOCKOUT_S = 900

_failures = defaultdict(list)
_lockouts = {}
_throttle_lock = threading.Lock()


# ----------------------------------------------------------------------------
# CSRF
# ----------------------------------------------------------------------------

def csrf_token():
    """Token for the current session, minted on first use."""
    token = session.get(CSRF_SESSION_KEY)
    if not token:
        token = secrets.token_urlsafe(32)
        session[CSRF_SESSION_KEY] = token
    return token


def rotate_csrf_token():
    """Call after a privilege change so a pre-login token cannot be reused."""
    session[CSRF_SESSION_KEY] = secrets.token_urlsafe(32)


def _submitted_token():
    if request.form.get(CSRF_FORM_FIELD):
        return request.form[CSRF_FORM_FIELD]
    if request.headers.get(CSRF_HEADER):
        return request.headers[CSRF_HEADER]
    # fetch() calls that send JSON rather than a form body
    if request.is_json:
        payload = request.get_json(silent=True) or {}
        return payload.get(CSRF_FORM_FIELD)
    return None


def _check_csrf():
    if request.method in SAFE_METHODS:
        return
    if request.endpoint in CSRF_EXEMPT:
        return

    expected = session.get(CSRF_SESSION_KEY)
    submitted = _submitted_token()

    if not expected or not submitted or not hmac.compare_digest(expected, submitted):
        logger.warning(f"CSRF rejection on {request.path} from {request.remote_addr}")
        abort(400, description="Your session expired or the form was not submitted from this site. "
                               "Reload the page and try again.")


# ----------------------------------------------------------------------------
# Login throttling
# ----------------------------------------------------------------------------

def _throttle_key(username):
    return (str(username).lower().strip(), request.remote_addr or 'unknown')


def lockout_remaining(username):
    """Seconds left on a lockout, or 0 if the caller may try again."""
    key = _throttle_key(username)
    with _throttle_lock:
        until = _lockouts.get(key, 0)
        remaining = int(until - time.time())
        if remaining <= 0:
            _lockouts.pop(key, None)
            return 0
        return remaining


def record_failed_login(username):
    """Returns the lockout length in seconds if this attempt triggered one."""
    key = _throttle_key(username)
    now = time.time()
    with _throttle_lock:
        recent = [t for t in _failures[key] if now - t < ATTEMPT_WINDOW_S]
        recent.append(now)
        _failures[key] = recent

        if len(recent) >= MAX_ATTEMPTS:
            _lockouts[key] = now + LOCKOUT_S
            _failures[key] = []
            logger.warning(f"Locked out '{key[0]}' from {key[1]} after {MAX_ATTEMPTS} failed attempts")
            return LOCKOUT_S
    return 0


def clear_login_failures(username):
    key = _throttle_key(username)
    with _throttle_lock:
        _failures.pop(key, None)
        _lockouts.pop(key, None)


# ----------------------------------------------------------------------------
# Wiring
# ----------------------------------------------------------------------------

def _resolve_secret_key(app):
    key = os.environ.get('SECRET_KEY')
    if key:
        return key

    # A predictable key forges sessions AND the rotating QR token, which is the
    # one thing standing between a screenshot and someone else's attendance.
    if app.debug or os.environ.get('FLASK_DEBUG') == '1':
        logger.warning("SECRET_KEY not set - generating a temporary one. "
                       "Sessions will not survive a restart. Set it in .env before deploying.")
        return secrets.token_hex(32)

    raise RuntimeError(
        "SECRET_KEY is not set. Refusing to start: without it, session cookies and "
        "the rotating QR tokens can both be forged. Add SECRET_KEY to your .env."
    )


def init_security(app):
    app.secret_key = _resolve_secret_key(app)

    https_only = os.environ.get('SESSION_COOKIE_SECURE', '0') == '1'
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE='Lax',
        SESSION_COOKIE_SECURE=https_only,
        PERMANENT_SESSION_LIFETIME=60 * 60 * 12,
        MAX_CONTENT_LENGTH=16 * 1024 * 1024,   # a face photo, not a video file
    )
    if not https_only:
        logger.warning("SESSION_COOKIE_SECURE is off. Set it to 1 once the app is served over HTTPS.")

    app.jinja_env.globals['csrf_token'] = csrf_token
    app.before_request(_check_csrf)

    @app.after_request
    def _headers(response):
        response.headers.setdefault('X-Content-Type-Options', 'nosniff')
        response.headers.setdefault('X-Frame-Options', 'DENY')
        response.headers.setdefault('Referrer-Policy', 'strict-origin-when-cross-origin')
        response.headers.setdefault('Permissions-Policy', 'geolocation=(self), camera=(self), microphone=()')
        response.headers.setdefault('Content-Security-Policy', '; '.join([
            "default-src 'self'",
            f"script-src {SCRIPT_SOURCES}",
            f"style-src {STYLE_SOURCES}",
            f"img-src {IMAGE_SOURCES}",
            f"font-src {FONT_SOURCES}",
            "connect-src 'self' ws: wss:",
            "frame-ancestors 'none'",
            "base-uri 'self'",
            "form-action 'self'",
        ]))
        if request.is_secure:
            response.headers.setdefault('Strict-Transport-Security', 'max-age=31536000; includeSubDomains')
        return response

    logger.info("Security layer active: CSRF, cookie policy, login throttling, response headers.")
