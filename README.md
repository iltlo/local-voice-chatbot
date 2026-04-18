# Local Realtime Voice Chatbot

A local, low-latency voice-to-voice chatbot using:
- STT: SenseVoice (FunASR)
- LLM: qwen3:4b (served locally via Ollama; closest available tag to Qwen3-4B-Inst-2507)
- TTS: Piper (CPU)
- Backend: FastAPI + WebSocket
- Frontend: React + Vite + MediaRecorder

## Architecture

1. Hold Space in the browser to record voice.
2. Release Space to send the audio blob to FastAPI over WebSocket.
3. Backend transcribes with SenseVoice and sends transcript text to UI.
4. Transcript is passed to Qwen; tokens stream to UI in real time.
5. Backend pipelines LLM->TTS so synthesis runs concurrently while tokens continue streaming, reducing time-to-first-audio.
6. Frontend shows runtime telemetry: LLM running/idle/offline and VRAM usage when available.

## Why this fits 12GB VRAM

- qwen3:4b via Ollama keeps the LLM local and streams tokens with low latency.
- SenseVoice typically uses ~1-2GB VRAM.
- Piper is CPU-based, preserving VRAM and reducing OOM risk.

## Folder Layout

- backend/app/main.py: FastAPI app and WebSocket endpoint
- backend/app/services/stt_service.py: SenseVoice adapter
- backend/app/services/llm_service.py: Gemma streaming adapter (Ollama)
- backend/app/services/tts_service.py: Piper adapter
- backend/app/services/pipeline.py: end-to-end streaming orchestration
- frontend/src/App.jsx: push-to-talk UX + websocket + audio queue

## Setup

## 1) Backend

```powershell
cd backend
py -3.10 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip setuptools wheel
pip install -r requirements.txt
pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu124
copy .env.example .env
```

Recommended one-command setup:

```powershell
cd backend
.\setup_backend.ps1
```

Place your local assets:
- Gemma model in Ollama (`ollama pull gemma4:e2b`)
 - Qwen model in Ollama (`ollama pull qwen3:4b`)
- SenseVoice model dir at `backend/models/SenseVoiceSmall`
- Piper binary at `backend/bin/piper/piper/piper.exe`
- Piper model at `backend/models/piper/en_US-amy-medium.onnx`

Install Ollama (Windows) and start it, then pull model:

```powershell
ollama pull qwen3:4b
```

Run backend:

```powershell
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

## 2) Frontend

```powershell
cd frontend
npm install
copy .env.example .env
npm run dev
```

Open `http://localhost:5173`.

## 3) Docker (Recommended For Cross-Platform)

This repo includes containerized backend/frontend for consistent Windows/Linux/macOS setup.

### Prerequisites

- Docker Desktop (or Docker Engine + Compose)
- Ollama running on host machine (for Gemma model serving)
- Local model assets mounted into backend:
	- `backend/models/SenseVoiceSmall`
	- `backend/models/piper/en_US-amy-medium.onnx`
	- `backend/models/piper/en_US-amy-medium.onnx.json`
	- `backend/bin/piper/piper/piper.exe`

### Run

```powershell
docker compose up --build
```

### Endpoints

- Frontend: `http://localhost:5173`
- Backend: `http://localhost:8000`
- Runtime status API: `http://localhost:8000/status`

### Notes

- Docker backend defaults to `SENSEVOICE_DEVICE=cpu` for broad compatibility.
- Ollama URL is set to `http://host.docker.internal:11434` in `docker-compose.yml`.
- If you want GPU SenseVoice in Docker on Linux/NVIDIA, add proper NVIDIA container runtime and set `SENSEVOICE_DEVICE=cuda:0`.

## Useful Commands After Code Changes

Rebuild and restart only frontend:

```powershell
docker compose up -d --build frontend
```

Rebuild and restart backend + frontend:

```powershell
docker compose up -d --build
```

Check container status:

```powershell
docker compose ps
```

Check health + runtime status:

```powershell
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/status
```

## WebSocket Message Protocol

Client -> server:
- `{ "type": "user_audio", "mime_type": "audio/webm", "audio_base64": "..." }`

Server -> client:
- `ready`
- `transcript` with transcript text
- `llm_token` streamed token text
- `tts_audio_chunk` with `audio_base64` WAV bytes
- `llm_done` final assistant text
- `error`

## Notes

- If model dependencies are missing, backend adapters return safe fallback responses so the app still boots for wiring checks.
- For best latency, keep Ollama running and tune context/token settings in `backend/.env`.
- You can swap Piper voice models by editing `PIPER_MODEL_PATH`.
