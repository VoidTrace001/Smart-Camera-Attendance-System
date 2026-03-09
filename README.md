# VeriVault AI: Enterprise Biometric Ecosystem 🛡️🤖

[![Python](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://www.python.org/)
[![Flask](https://img.shields.io/badge/Framework-Flask-lightgrey.svg)](https://flask.palletsprojects.com/)
[![AI](https://img.shields.io/badge/AI-DeepFace%20%26%20Gemini-orange.svg)](https://ai.google.dev/)
[![Security](https://img.shields.io/badge/Security-Zero--Trust-green.svg)](#)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

**VeriVault AI** is a state-of-the-art, autonomous institutional management system. It transcends traditional attendance tools by combining **Deep Learning Computer Vision**, **Generative AI Analytics**, and **Microservices Architecture**.

---

## 💎 Signature Features

### 🧠 Neural Vision Engine
*   **Facenet Integration:** High-accuracy (99.38%) facial recognition using 128-dimensional mathematical embeddings.
*   **Emotion & Engagement AI:** Real-time analysis of classroom sentiment (Happy, Focused, Bored, Confused).
*   **Liveness Verification:** Anti-spoofing logic via mandatory blink-detection to prevent photo-based fraud.

### 🤖 Generative AI Assistant
*   **Neural Database Analyst:** A floating AI Assistant that allows administrators to query institutional data using Natural Language.
*   **Text-to-SQL:** Converts human questions (e.g., *"Which students missed BCA classes last week?"*) into secure SQLite queries instantly.

### 📍 Geo-Spatial Security
*   **Satellite Geo-Fencing:** Interactive **Leaflet.js** map for mobile check-ins.
*   **Secure Perimeter:** Authenticates attendance only within a 300m radius of the campus.

### 🏗️ Enterprise Infrastructure
*   **Asynchronous Processing:** Powered by **Celery & Redis** for non-blocking report generation and bulk communications.
*   **Self-Healing Watchdog:** An autonomous background process that monitors system health and suggests AI-driven code repairs.
*   **Docker Ready:** Containerized environment for frictionless 1-click deployment.

---

## 📖 Full System Documentation

### 1. CORE ARCHITECTURE (APEX STACK)
- **Backend Framework:** Flask (Python 3.11+)
- **Vision Engine:** DeepFace Neural Network (FaceNet Model)
- **Database Layer:** Hybrid (SQLite for local / PostgreSQL for Cloud)
- **Task Orchestration:** Celery + Redis (Asynchronous Processing)
- **Intelligence:** Google Gemini 1.5 Flash (Generative AI)
- **Frontend:** Vanilla JS, Chart.js (Analytics), Leaflet.js (Geo-Spatial)
- **DevOps:** Docker & Docker-Compose (Containerization)

### 2. COMPREHENSIVE FEATURE BREAKDOWN

#### PHASE 1: PREDICTIVE ANALYTICS & GEO-SPATIAL UI
- **Visual Business Intelligence:** Animated trendlines and doughnut charts displaying institutional attendance health in real-time.
- **Satellite Geo-Fencing:** Interactive GPS tracking requiring students to be within a secure campus radius to authorize check-ins.

#### PHASE 2: NEURAL NETWORK VISION
- **128D Mathematical Embeddings:** Replaced pixel-matching with high-dimensional vector comparison (Cosine Similarity).
- **Emotion AI:** Real-time detection of student mood to generate classroom engagement metrics.
- **Liveness Detection:** Enforced blink-detection to secure the biometric gateway.

#### PHASE 3: ENTERPRISE ARCHITECTURE
- **Asynchronous Workers:** Heavy operations (PDF generation, bulk emails) are offloaded to background workers.
- **Self-Healing Watchdog:** monitors crashes and suggests real-time code repairs.
- **Automated Business Logic:** 
    *   Daily automated marking of absentees at EOD.
    *   Weekly AI Dropout Prediction analysis.
    *   Annual data retention purge of graduated batches.

#### PHASE 4: GENERATIVE AI DATABASE ASSISTANT
- **VeriVault AI Analyst:** A floating natural language interface integrated into the dashboard.
- **Text-to-SQL Engine:** Translates human questions into complex SQL queries safely.
- **Data Summarization:** Analyzes database results and provides professional summaries.

---

## 🚀 Getting Started

### 1. Prerequisites
- Python 3.11+
- Redis Server (Running on port 6379)
- Google Gemini API Key

### 2. Installation
```bash
git clone https://github.com/VoidTrace001/Smart-Camera-Attendance-System.git
cd Smart-Camera-Attendance-System
pip install -r requirements.txt
```

### 3. Configuration
Create a `.env` file in the root directory:
```env
SECRET_KEY=your_secure_random_key
GEMINI_API_KEY=your_google_ai_key

# Initial Admin Setup
INITIAL_ADMIN_USER=your_preferred_admin_username
INITIAL_ADMIN_PASS=your_secure_password
```

### 4. Launch
```bash
python app.py
```
*The system will automatically orchestrate the Web Server, AI Watchdog, and Celery Worker.*

---

## 🛡️ Security Standards
*   **Zero-Trust Architecture:** Every password is cryptographically hashed (PBKDF2-SHA256).
*   **Anti-Replay Protection:** QR codes use **Dynamic HMAC Tokens** that expire every 60 seconds.
*   **Privacy First:** Biometric data is stored as mathematical vectors, not raw images.

---

## ⚖️ License
Distributed under the MIT License. See `LICENSE` for more information.

**Built for the Next Generation of Institutional Intelligence.**
