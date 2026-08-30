"""
VeriVault attendance engine.

Consumes sightings, keeps presence intervals current for live classes, and
finalises each session shortly after it ends - writing one verdict per enrolled
student, including the ones never seen.

This replaces the old end-of-day job, which wrote a single day-level "Absent"
row per student regardless of how many classes they had.

Usage:
    python attendance_engine.py
"""
import logging
import os
import signal
import time
from datetime import datetime

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - Engine - %(levelname)s - %(message)s')
logger = logging.getLogger('VeriVaultAI')

import bus
import presence

INTERVAL_REBUILD_S = float(os.environ.get('INTERVAL_REBUILD_S', '60'))
TICK_S = float(os.environ.get('ENGINE_TICK_S', '2'))

_running = True


def _stop(signum, frame):
    global _running
    logger.info("Shutdown signal received.")
    _running = False


def _emit(event, payload):
    """Best-effort realtime push to open dashboards.

    A no-op in this process - see realtime.py for why importing the web app
    from here was actively harmful.
    """
    from realtime import emit
    emit(event, payload)


def drain_sightings():
    """Move sightings off the bus into the database."""
    if not bus.available():
        return 0
    rows, ack_ids = bus.consume_sightings(block_ms=int(TICK_S * 1000))
    if rows:
        presence.record_sightings(rows)
    if ack_ids:
        bus.ack(ack_ids)
    return len(rows)


def refresh_live_sessions():
    """Rebuild intervals for in-progress classes so dashboards stay live."""
    today = datetime.now().strftime("%Y-%m-%d")
    now = datetime.now()
    refreshed = []

    for session in presence.get_sessions_for_date(today):
        if session['status'] == 'closed':
            continue
        start, end = presence.parse_ts(session['start_ts']), presence.parse_ts(session['end_ts'])
        if not (start <= now < end):
            continue

        presence.build_intervals(session['id'])
        results = presence.compute_session_results(session['id'])
        in_room = [r for r in results if r['last_seen'] and
                   (now - presence.parse_ts(r['last_seen'])).total_seconds() < 180]

        _emit('session_progress', {
            'session_id': session['id'],
            'subject': session['subject'],
            'course': session['course'],
            'year': session['year'],
            'present_now': len(in_room),
            'roster': len(results),
            'students': results,
        })
        refreshed.append(session['id'])

    return refreshed


def close_due_sessions():
    closed = []
    for session_id in presence.sessions_due_for_close():
        summary = presence.close_session(session_id)
        logger.info(
            f"Session {session_id} ({summary.get('subject')}) closed: "
            f"{summary.get('Present', 0)} present, {summary.get('Late', 0)} late, "
            f"{summary.get('Partial', 0)} partial, {summary.get('Absent', 0)} absent, "
            f"{summary.get('On Leave', 0)} on leave"
        )
        _emit('session_closed', summary)
        closed.append(summary)
    return closed


def run():
    logger.info(f"Attendance engine starting (bus mode: {bus.mode()})")
    bus.ensure_group()

    # Make sure today's classes exist even if the nightly job never ran.
    created = presence.materialize_sessions()
    if created:
        logger.info(f"Materialised {created} session(s) for today.")

    last_rebuild = 0.0
    last_materialise_day = datetime.now().date()

    while _running:
        try:
            drain_sightings()

            now = time.time()
            if now - last_rebuild >= INTERVAL_REBUILD_S:
                refresh_live_sessions()
                close_due_sessions()
                last_rebuild = now

            # Roll into the next day without a restart.
            today = datetime.now().date()
            if today != last_materialise_day:
                presence.materialize_sessions(today)
                presence.purge_old_sightings()
                last_materialise_day = today
                logger.info(f"Rolled over to {today}.")

            if not bus.available():
                time.sleep(TICK_S)

        except Exception as e:
            logger.exception(f"Engine tick failed: {e}")
            time.sleep(5)

    logger.info("Attendance engine stopped.")


if __name__ == '__main__':
    signal.signal(signal.SIGINT, _stop)
    try:
        signal.signal(signal.SIGTERM, _stop)
    except (AttributeError, ValueError):
        pass

    print("=" * 58)
    print("  VERIVAULT AI - ATTENDANCE ENGINE")
    print("=" * 58)
    run()
