"""
Starts and babysits the background half of VeriVault.

The web app serves pages; it does not watch classrooms. That work lives in
attendance_engine.py and one vision_worker.py per camera, and until now nothing
launched them - the modules were written but had to be started by hand, one
terminal each. This does it in one command and restarts anything that dies.

    python supervisor.py

Cameras come from the database, so adding one in the admin dashboard and
restarting here is enough. There is no separate process list to maintain.
"""
import logging
import os
import signal
import subprocess
import sys
import time

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - Supervisor - %(levelname)s - %(message)s')
logger = logging.getLogger('VeriVault')

# A worker that dies immediately keeps dying - usually a camera that isn't
# plugged in. Back off rather than spinning on it.
FIRST_RETRY_S = 5
MAX_RETRY_S = 120
HEALTHY_AFTER_S = 60
POLL_S = 3

_running = True


def _stop(signum, frame):
    global _running
    logger.info("Shutting down. Waiting for children to finish.")
    _running = False


class Child:
    def __init__(self, name, args, env=None):
        self.name = name
        self.args = args
        self.env = env or {}
        self.process = None
        self.started_at = 0
        self.backoff = FIRST_RETRY_S
        self.retry_at = 0
        self.failures = 0

    def start(self):
        environment = dict(os.environ, **self.env)
        try:
            self.process = subprocess.Popen([sys.executable, *self.args], env=environment)
        except Exception as e:
            logger.error(f"{self.name}: could not start ({e})")
            self.schedule_retry()
            return
        self.started_at = time.time()
        logger.info(f"{self.name}: started (pid {self.process.pid})")

    def schedule_retry(self):
        self.failures += 1
        self.retry_at = time.time() + self.backoff
        logger.warning(f"{self.name}: retrying in {int(self.backoff)}s")
        self.backoff = min(self.backoff * 2, MAX_RETRY_S)

    def check(self):
        """Restart if it has exited. Called on every supervisor tick."""
        if self.process is None:
            if time.time() >= self.retry_at:
                self.start()
            return

        code = self.process.poll()
        if code is None:
            # Survived long enough to count as healthy - forgive the backoff.
            if self.backoff > FIRST_RETRY_S and time.time() - self.started_at > HEALTHY_AFTER_S:
                self.backoff = FIRST_RETRY_S
            return

        lived = int(time.time() - self.started_at)
        logger.warning(f"{self.name}: exited with code {code} after {lived}s")
        self.process = None
        self.schedule_retry()

    def terminate(self):
        if self.process is None or self.process.poll() is not None:
            return
        logger.info(f"{self.name}: stopping")
        self.process.terminate()
        try:
            self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            logger.warning(f"{self.name}: did not stop in time, killing")
            self.process.kill()


def active_cameras():
    from presence import get_cameras
    return get_cameras(active_only=True)


def build_children():
    children = [Child('engine', ['attendance_engine.py'])]

    cameras = active_cameras()
    if not cameras:
        logger.warning("No active cameras registered. Add one under Admin -> Cameras; "
                       "the engine will run on its own until then.")

    for cam in cameras:
        children.append(Child(
            f"camera:{cam['id']} {cam['name']}",
            ['vision_worker.py'],
            {'CAMERA_ID': str(cam['id'])},
        ))
    return children


def main():
    signal.signal(signal.SIGINT, _stop)
    try:
        signal.signal(signal.SIGTERM, _stop)
    except (AttributeError, ValueError):
        pass

    print("=" * 58)
    print("  VERIVAULT AI - SUPERVISOR")
    print("=" * 58)

    # The engine also does this on boot, but doing it here means a fresh
    # install has sessions before the first worker ever looks for one.
    try:
        from presence import materialize_sessions
        created = materialize_sessions()
        if created:
            logger.info(f"Built {created} class session(s) from today's timetable.")
    except Exception as e:
        logger.error(f"Could not build today's sessions: {e}")

    children = build_children()
    logger.info(f"Supervising {len(children)} process(es).")
    for child in children:
        child.start()

    while _running:
        for child in children:
            child.check()
        time.sleep(POLL_S)

    for child in children:
        child.terminate()
    logger.info("Supervisor stopped.")


if __name__ == '__main__':
    main()
