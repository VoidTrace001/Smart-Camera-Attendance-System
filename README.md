# 🛡️ VeriVault AI: Autonomous Smart Campus Ecosystem

[![AI Core: Gemini Pro](https://img.shields.io/badge/AI_Core-Gemini_Pro-blue?logo=google-gemini)](https://deepmind.google/technologies/gemini/)
[![Framework: Flask](https://img.shields.io/badge/Framework-Flask-lightgrey?logo=flask)](https://flask.palletsprojects.com/)
[![Computer Vision: OpenCV](https://img.shields.io/badge/Computer_Vision-OpenCV-white?logo=opencv)](https://opencv.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

**VeriVault AI** is a state-of-the-art, enterprise-grade attendance and institutional analytics platform. It replaces manual processes with **Biometric Vision**, **Geo-Fenced Mobile Check-ins**, and **Self-Healing AI Watchdogs**, creating a secure, touchless, and predictive experience for students and faculty.

---

## 🌟 Key Features

### 🤖 Autonomous AI Self-Repair (Gemini Powered)
*   **Zero-Downtime Watchdog:** An integrated AI service (`ai_watchdog.py`) that monitors backend errors.
*   **Self-Patching:** Using **Gemini Pro**, the system analyzes crash tracebacks, understands the broken code context, and autonomously applies source code patches to fix bugs in real-time.

### 👁️ Next-Gen Biometric Intelligence
*   **LBPH Recognition:** High-speed, localized face recognition.
*   **AI Anti-Spoofing:** Liveness detection requiring "Blink Verification" to prevent photo or video replay attacks.
*   **Classroom Engagement Analytics:** Real-time tracking of student eye-contact to categorize focus as *Highly Engaged*, *Attentive*, or *Distracted*.

### 🌍 Geo-Fenced Mobile Portability
*   **GPS Check-in:** Students can mark attendance via their smartphones.
*   **Radius Lock:** Uses the **Haversine formula** to strictly block attendance markers if the student is more than 300 meters from the campus building.

### 📈 Predictive Student Success
*   **AI Dropout Predictor:** Analyzes 10-day attendance patterns to flag "High Risk" students *before* they fall below the 75% threshold.
*   **Smart Parent Reports:** Generates personalized, empathetic weekly summaries for parents using LLM-based generative reporting.

### 🔒 Enterprise Security & Compliance
*   **Audit Trails:** Permanent, immutable logging of every manual override and check-in attempt (Timestamp, User ID, IP).
*   **Data Retention:** Automated annual "Batch Purge" system to securely delete student biometric data 4 years post-graduation (e.g., GDPR/FERPA compliance).

---

## 🛠️ Technical Stack

*   **Backend:** Python 3.10+, Flask (WSGI), Flask-SocketIO
*   **Frontend:** HTML5, CSS3 (Glassmorphism), JavaScript (Vanilla)
*   **Database:** SQLite (Relational)
*   **Computer Vision:** OpenCV (Haar Cascades + LBPH)
*   **AI Models:** Google Gemini Pro (Generative AI)
*   **Communication:** Twilio (WhatsApp/SMS API), SMTP (Email Service)
*   **Scheduling:** APScheduler (Background Jobs)
*   **Reporting:** ReportLab (PDF Generation), Pandas/CSV

---

## 🚀 Quick Start

### 1. Clone the Repository
```bash
git clone https://github.com/your-username/verivault-ai.git
cd verivault-ai
```

### 2. Install Dependencies
```bash
pip install -r requirements.txt
```

### 3. Configure API Keys
Add your **Gemini API Key** to `ai_watchdog.py` and `ai_services.py`. 
(Optional: Add Twilio credentials to `notification_hub.py` for real WhatsApp alerts).

### 4. Run the Application
```bash
python app.py
```
*The AI Watchdog and Database Migrations will start automatically.*

---

## 🏗️ System Architecture

1.  **Stationary Unit (PC/IoT):** Runs the continuous camera loop for biometric scanning.
2.  **Web Portal:** Multi-role dashboard for Admins (Analytics), Teachers (Management), and Students (Profile).
3.  **Watchdog Process:** Autonomous background script for self-repair and system health monitoring.
4.  **Notification Hub:** Real-time communications via Real WhatsApp API and Email.

---

## 📅 Roadmap
- [ ] **Native APK:** Deploying the student portal as a compiled Android/iOS app.
- [ ] **Cloud Migration:** Full deployment to AWS/Heroku with PostgreSQL.
- [ ] **DeepFace Integration:** Upgrading from LBPH to Neural Network-based recognition for 99.8% accuracy.

---

## 📄 License
This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

---

**Developed for the Future of Education.**
*"VeriVault AI: Securing the Future of Education through Intelligent Automation."*
