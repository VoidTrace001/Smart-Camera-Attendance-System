"""
Pushes live updates to open dashboards, safe to call from any process.

The web app, the attendance engine and every vision worker are separate
processes, and only the web app holds the Socket.IO server and the browser
connections. An emit from anywhere else could never have reached a client.

What it did instead was `from app import socketio`, which imports the whole web
application into the calling process: TensorFlow, the database migrations, and
a second copy of the scheduler with all six cron jobs. With the supervisor
running an engine plus a worker per camera, that meant several copies of the
weekly defaulter emails and the dropout-risk WhatsApp alerts to parents, and a
34-second stall on the first emit.

So the rule is: emit only if this process is already the one running the web
app. Everywhere else it is a no-op, which costs nothing and loses nothing.

Genuine cross-process realtime would need Socket.IO started with a message
queue (`SocketIO(app, message_queue=REDIS_URL)`), at which point this can
publish to that queue instead.
"""
import logging
import sys

logger = logging.getLogger('VeriVaultAI')


def emit(event, payload):
    """Returns True if the event actually went to a browser."""
    app_module = sys.modules.get('app')
    if app_module is None:
        return False

    socketio = getattr(app_module, 'socketio', None)
    if socketio is None:
        return False

    try:
        socketio.emit(event, payload)
        return True
    except Exception as e:
        logger.debug(f"Realtime emit for '{event}' failed: {e}")
        return False
