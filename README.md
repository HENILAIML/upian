# UPIAN AI Gateway & Multimodal Platform

An enterprise-ready AI Gateway and Multimodal Orchestration platform built with **Flask**, **SQLAlchemy**, and **PostgreSQL**. Features multi-provider LLM routing, vision classification, dynamic tiered plans, Google OAuth authentication, and resilient failover.

---

## 🌟 Key Features

* **Intelligent Routing**: Semantic classification (Fast / Medium / Complex) routing prompts dynamically between Gemini, OpenAI, and Anthropic.
* **Multimodal Vision Intelligence**: Image classification using MobileNetV3 and SigLIP2 zero-shot classification with uncertainty estimation.
* **Document & Image Extraction**: Universal drag-and-drop parsing for 9 formats (PDF, DOCX, XLSX, CSV, TXT, PNG, JPG, JPEG, WEBP).
* **AI Image Generation**: Text-to-image and image editing powered by DALL-E and Google Imagen 3 with automated quota tracking.
* **Tiered Subscription Plans**: Dynamic plan customization (Free, Pro, Pro+) with real-time fee calculation and UPI verification.
* **Enterprise Security**: Google Identity Services OAuth, PBKDF2/scrypt password hashing, CSRF protection, and IDOR prevention.
* **Resilient Infrastructure**: Automated PostgreSQL migrations, connection pre-pinging, and scheduled backup/restore scripts.

---

## 🚀 Quick Start

### 1. Prerequisites
* Python 3.11+ or 3.12+
* PostgreSQL 14+ (or SQLite fallback for testing)

### 2. Installation
```bash
git clone https://github.com/HENILAIML/upian.git
cd upian
python -m venv venv
# Windows:
venv\Scripts\activate
# Linux/macOS:
source venv/bin/activate

pip install -r requirements.txt
```

### 3. Environment Configuration
Copy the example environment file and update your credentials:
```bash
cp .env.example .env
```
Key environment variables:
```ini
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/upian_full_project
SECRET_KEY=your_strong_secret_key_here
GEMINI_API_KEY=your_gemini_api_key_here
GOOGLE_CLIENT_ID=your_google_client_id.apps.googleusercontent.com
```

### 4. Database Setup & Run
Run the automated migration and start the development server:
```bash
python migrations/migrate_v1.py
python app.py
```
Or run with production WSGI:
```bash
waitress-serve --host=0.0.0.0 --port=5000 app:app
```

---

## 🐳 Docker Deployment

Build and run using Docker Compose:
```bash
docker compose up -d --build
```
Or build the image directly:
```bash
docker build -t upian-ai-gateway:latest .
docker run -p 5000:5000 --env-file .env upian-ai-gateway:latest
```

---

## 🧪 Testing

Run the comprehensive 84-test suite:
```bash
pytest tests
```

---

## 📄 License
This project is proprietary and confidential. All rights reserved.
