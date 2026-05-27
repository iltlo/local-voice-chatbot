# Local Realtime Voice Chatbot

Local voice-to-voice chatbot with low latency:
- STT: SenseVoice
- LLM: `gemma4:e2b` via Ollama
- TTS: Piper
- Backend: FastAPI + WebSocket
- Frontend: React + Vite

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
  - `backend/bin/piper/piper/piper.exe`

Pull the LLM once:

```powershell
ollama pull gemma4:e2b
```

## Run With Docker (Recommended)

```powershell
docker compose up --build
```

Endpoints:
- Frontend: `http://localhost:5173`
- Backend: `http://localhost:8000`
- Status: `http://localhost:8000/status`

## Run Locally (Without Docker)

Backend:

```powershell
cd backend
.\setup_backend.ps1
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Frontend:

```powershell
cd frontend
npm install
npm run dev
```

## Useful Commands

```powershell
docker compose up -d --build
docker compose up -d --build frontend
docker compose ps
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/status
```
