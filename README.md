# VeriVault

Camera-based attendance for a college timetable. Cameras watch the room for the
length of a class, and attendance is decided from how much of that class each
student was actually present for — not from whether they were spotted once on
the way in.

Built for EThames Business School. Flask, OpenCV, DeepFace, SQLite.

---

## How attendance is decided

This is the part worth understanding, because it drives everything else.

A row in the timetable is a recurring rule. Just after midnight it is expanded
into a **class session** — that rule pinned to one date, with a start and end.
Verdicts attach to sessions, so "absent" always means absent from a specific
class rather than absent from a day.

While a session is running, the vision worker for that room records a
**sighting** at most once per student per 30-second bucket. Sightings are cheap
and disposable; they are the evidence, not the record.

When the class ends, sightings are collapsed into **presence intervals**. Two
sightings join into one interval if the gap between them is under the tolerance
(three minutes by default) — that covers someone turning away from the camera.
A longer gap ends the interval, which is how "left the room" is detected at all.

Coverage is the share of the class the student was in the room for. That, plus
when they arrived and when they were last seen, gives the verdict:

| Verdict | Condition |
|---|---|
| Present | Coverage ≥ 75%, arrived within the grace period |
| Late | Coverage ≥ 75%, arrived after the grace period |
| Partial | Coverage ≥ 40% |
| Absent | Coverage below 40%, **or** last seen well before the end and never again |
| On Leave | An approved leave request covers the date |

The left-early case is checked before the coverage bands on purpose. A student
who sat through most of a class and then walked out for good is not the same as
one who arrived late and stayed, and coverage alone cannot tell them apart.

Every threshold above lives in the `attendance_config` table and is editable at
**Admin → Control Room** without a redeploy.

One consequence worth knowing: with the default 75% band and a 10-minute grace,
`Late` covers arrivals roughly 11–15 minutes into a one-hour class. Arrive later
than that and you have missed enough of the class to land in `Partial`. Widen
`present_pct` or `grace_minutes` if you want that to behave differently.

---

## Running it

### Requirements

- Python 3.11+
- A camera per monitored room (USB, or an IP camera reachable over RTSP)
- Redis — optional, see below

```bash
pip install -r requirements.txt
```

Create a `.env`:

```env
SECRET_KEY=some_long_random_string
INITIAL_ADMIN_USER=admin
INITIAL_ADMIN_PASS=change_this

# Set to 1 once the app is served over HTTPS (behind the tunnel, or any
# real deployment). Leave at 0 for plain-http local development only.
SESSION_COOKIE_SECURE=0

# Optional. Without it the AI assistant and dropout predictor stay off;
# everything else works.
GEMINI_API_KEY=
```

### Two processes

The web app serves pages. It does **not** watch classrooms — that is deliberate,
because monitoring that only runs while someone has a browser open is not
monitoring.

```bash
python app.py          # web interface, port 5000
python supervisor.py   # attendance engine + one vision worker per camera
```

Start the web app first, add a camera under **Admin → Cameras**, then start the
supervisor. It reads the camera list from the database, launches a worker for
each, and restarts anything that dies (backing off, so an unplugged camera does
not spin).

To run a single camera by hand instead:

```bash
CAMERA_ID=1 python vision_worker.py
```

#---

## Newly Added Features & System Upgrades 🚀

The platform has been enhanced with enterprise-grade capabilities:

### 1. 📲 Instant Parent WhatsApp & SMS Notifications
- **Automated Alerts:** Integrated Twilio message dispatch ([notification_hub.py](file:///D:/work/Smart-Camera-Attendance-System/notification_hub.py)) sending real-time SMS or WhatsApp messages to parent contact numbers upon absence or tardiness.
- **Delivery Log:** Every notification is logged with timestamp, channel, and message status in the `notifications` table.

### 2. 🎭 Anti-Spoofing & Liveness Detection
- **Multi-Factor Verification:** Face recognition ([recognition.py](file:///D:/work/Smart-Camera-Attendance-System/recognition.py)) now measures high-frequency Laplacian texture surface variance (`check_texture_liveness`) and eye-blink transitions to block printed photos or digital phone screens from spoofing classroom cameras.

### 3. 🐘 Full PostgreSQL & SQLite Dual Engine Support
- **Universal Compatibility:** Solved previous SQLite limitations with `PgConnWrapper` and `PgCursorWrapper` in [database.py](file:///D:/work/Smart-Camera-Attendance-System/database.py). Automatically translates `?` SQL parameter placeholders to PostgreSQL `%s` syntax while standardizing dictionary row returns. Works on both SQLite and cloud PostgreSQL (Supabase).

### 4. 🗺️ Classroom Spatial Seating Heatmaps
- **Bounding Box Tracking:** Vision workers ([vision_worker.py](file:///D:/work/Smart-Camera-Attendance-System/vision_worker.py)) capture spatial bounding boxes (`box_x`, `box_y`, `box_w`, `box_h`) for every sighting.
- **Interactive Inspector:** Route `/admin/seating_heatmap/<camera_id>` renders an interactive 5x5 occupancy density grid ([templates/seating_heatmap.html](file:///D:/work/Smart-Camera-Attendance-System/templates/seating_heatmap.html)) with click-to-inspect desk activity stats.

### 5. 🔍 Interactive Command Palette (`Ctrl + K`) & Quick Action Dock
- **Instant Search:** Pressing **`Ctrl + K`** opens a global command palette modal ([templates/layout.html](file:///D:/work/Smart-Camera-Attendance-System/templates/layout.html)) to jump to any page or tool.
- **Action Dock:** A floating bottom-right dock provides instant access to theme switching, quick search, and voice roll-call speech announcements (`/api/voice_announcement/<session_id>`).

### 6. 📱 Progressive Web App (PWA)
- **Installable Application:** Wired `static/manifest.json` and `static/sw.js` service worker for PWA support across mobile and desktop browsers.

---

## 🔮 In-Development & Upcoming Roadmap Features 🛠️

Features currently being developed for future releases:

1. 🎙️ **Hands-Free Natural Language Voice Control**: Extending Web Speech recognition for voice command roll calls (*"Mark Room 101 excused for afternoon session"*).
2. 🔔 **Native WebPush Mobile Notifications**: Direct WebPush / Firebase push notification integration for real-time parent and student smartphone pop-ups.
3. ⚡ **CUDA & TensorRT Hardware Acceleration Engine**: Hardware-level GPU pipeline tuning for 60+ FPS processing across multi-camera streams.
4. 📈 **Predictive Dropout & Risk Early-Warning Analytics**: Automated statistical modeling identifying declining attendance trends before threshold limits are breached.
5. 🏫 **Multi-Campus Enterprise Hierarchy**: Multi-tenant institutional management allowing single-dashboard operations across geographically separate campuses.

---

## What each module does

| File | Responsibility |
|---|---|
| `app.py` | Routes, sessions, auth, PWA routes, seating heatmaps, speech API |
| `presence.py` | Sessions, sightings (with bounding box geometry), presence intervals, verdict rules |
| `vision_worker.py` | One process per camera. Detects, identifies, anti-spoofing check, records spatial sightings |
| `attendance_engine.py` | Drains sightings, keeps live rosters current, finalises sessions |
| `supervisor.py` | Starts and babysits workers and engine |
| `recognition.py` | Vectorized matrix matching, facial embeddings, Laplacian texture anti-spoofing |
| `notification_hub.py` | Twilio SMS and WhatsApp alert delivery hub and audit logs |
| `bus.py` | Redis stream transport with direct-to-database fallback |
| `database.py` | Dual SQLite / PostgreSQL schema migrations and connection wrappers |
| `scheduler.py` | Nightly session building, retention, automated notifications |

---

## Running the system

To run the complete platform, start the two core processes in separate terminals:

```bash
# Terminal 1: Web Interface & Portals (port 5000)
python app.py

# Terminal 2: Camera Supervisor & Vision Workers
python supervisor.py
```

---

## Licence

MIT. See `LICENSE`.
