# Local Realtime Voice Chatbot

Local voice-to-voice chatbot with low latency:
- STT: SenseVoice
- LLM: `gemma4:e2b` via Ollama
- TTS: Piper
- Backend: FastAPI + WebSocket
- Frontend: React + Vite

<img width="2846" height="1546" alt="Screenshot 2026-05-27 142352" src="https://github.com/user-attachments/assets/61bb5797-7fa6-46d2-9055-b59361122b1f" />

## Quick Flow

1. Hold `Space` to record.
2. Release to send audio to backend.
3. Backend transcribes, streams LLM tokens, and streams TTS audio chunks.

## Prerequisites

- Ollama installed and running
- Docker Desktop (for Docker setup)
- Local model assets available:
  - `backend/models/SenseVoiceSmall`
  - `backend/models/piper/en_US-amy-medium.onnx`
  - `backend/models/piper/en_US-amy-medium.onnx.json`
  - `backend/bin/piper/piper/piper` (Linux) or `piper.exe` (Windows)

Pull the LLM once:

```bash
ollama pull gemma4:e2b
```

## Run With Docker (Recommended)

```bash
docker compose up --build
```

Endpoints:
- Frontend: `http://localhost:3000`
- Backend: `http://localhost:8000`
- Status: `http://localhost:8000/status`

## Run Locally (Without Docker)

Backend — **Linux/macOS:**

```bash
cd backend
chmod +x setup_backend.sh
./setup_backend.sh
.venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Backend — **Windows:**

```powershell
cd backend
.\setup_backend.ps1
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Frontend (both platforms):

```bash
cd frontend
npm install
npm run dev
```

## Full Local Setup (models + runtime)

Linux/macOS:

```bash
cd backend
chmod +x setup_all.sh
./setup_all.sh
```

Windows:

```powershell
cd backend
.\setup_all.ps1
```

## Useful Commands

```bash
docker compose up -d --build
docker compose up -d --build frontend
docker compose ps
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/status
```
