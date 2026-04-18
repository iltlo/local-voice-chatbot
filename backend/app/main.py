from __future__ import annotations

import base64
import logging
import asyncio
import time
import sys
from contextlib import suppress

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.models import ClientAudioMessage, ClientInterruptMessage, ServerMessage
from app.services.pipeline import VoicePipeline
from app.services.runtime_status import get_vram_status


class _DuplicateLogFilter(logging.Filter):
    def __init__(self, window_seconds: float = 0.5) -> None:
        super().__init__()
        self.window_seconds = window_seconds
        self._last_signature: tuple[str, int, str] | None = None
        self._last_time = 0.0

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        signature = (record.name, record.levelno, msg)
        now = time.monotonic()
        if self._last_signature == signature and (now - self._last_time) <= self.window_seconds:
            return False
        self._last_signature = signature
        self._last_time = now
        return True


logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s", force=True)
_root_logger = logging.getLogger()
_root_logger.addFilter(_DuplicateLogFilter())

_app_logger = logging.getLogger("app")
_app_logger.handlers.clear()
_app_handler = logging.StreamHandler(sys.stderr)
_app_handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
_app_logger.addHandler(_app_handler)
_app_logger.setLevel(logging.INFO)
_app_logger.propagate = False

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


settings = get_settings()
app = FastAPI(title="Local Realtime Voice Chatbot")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.cors_origin],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

pipeline = VoicePipeline(settings)


async def _runtime_snapshot() -> dict[str, object]:
    llm_state = await pipeline.llm.runtime_status()
    gpu_state = get_vram_status()
    tts_state = pipeline.tts.runtime_status()
    return {
        "status": "ok",
        **llm_state,
        **tts_state,
        **gpu_state,
    }


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/status")
async def runtime_status() -> dict[str, object]:
    return await _runtime_snapshot()


@app.get("/voices")
def voices() -> dict[str, object]:
    return {
        "default_voice": pipeline.tts._default_model_path,
        "chinese_voice": pipeline.tts._chinese_model_path,
        "chinese_fallback_voice": pipeline.tts._chinese_fallback_model_path,
        "voices": pipeline.tts.list_available_voices(),
    }


@app.websocket("/ws")
async def websocket_chat(ws: WebSocket) -> None:
    await ws.accept()
    await ws.send_json(ServerMessage(type="ready", runtime=await _runtime_snapshot()).model_dump(exclude_none=True))
    active_task = None
    selected_voice_model: str | None = None

    async def cancel_active(reason: str, request_id: str | None = None) -> None:
        nonlocal active_task
        if active_task and not active_task.done():
            active_task.cancel()
            with suppress(asyncio.CancelledError):
                await active_task
        active_task = None
        await ws.send_json(
            ServerMessage(type="interrupted", reason=reason, request_id=request_id).model_dump(exclude_none=True)
        )

    async def stream_audio_request(audio_bytes: bytes, suffix: str) -> None:
        async for event in pipeline.handle_audio(audio_bytes, suffix=suffix, voice_model_path=selected_voice_model):
            await ws.send_json(event.model_dump(exclude_none=True))

    try:
        while True:
            payload = await ws.receive_json()
            message_type = payload.get("type")
            if message_type == "interrupt":
                interrupt = ClientInterruptMessage.model_validate(payload)
                await cancel_active(reason="user_interrupt", request_id=interrupt.request_id)
                continue

            if message_type == "set_voice":
                requested_voice = payload.get("voice_id")
                resolved = pipeline.tts.resolve_voice_model(requested_voice)
                if not resolved:
                    await ws.send_json(
                        ServerMessage(type="error", error=f"Unknown voice: {requested_voice}").model_dump(exclude_none=True)
                    )
                    continue
                selected_voice_model = resolved
                await ws.send_json(
                    {
                        "type": "voice_selected",
                        "voice_id": requested_voice,
                        "voice_model_path": resolved,
                    }
                )
                continue

            if message_type != "user_audio":
                continue

            message = ClientAudioMessage.model_validate(payload)
            audio_bytes = base64.b64decode(message.audio_base64)
            suffix = ".webm" if "webm" in message.mime_type.lower() else ".wav"

            if active_task and not active_task.done():
                await cancel_active(reason="new_request")

            active_task = pipeline_task = asyncio.create_task(stream_audio_request(audio_bytes, suffix))

            def _done_cb(task):
                if task.cancelled():
                    return
                exc = task.exception()
                if exc is not None:
                    logging.getLogger(__name__).exception("Pipeline task failed: %s", exc)

            pipeline_task.add_done_callback(_done_cb)

    except WebSocketDisconnect:
        if active_task and not active_task.done():
            active_task.cancel()
        return
    except Exception as exc:  # noqa: BLE001
        await ws.send_json(ServerMessage(type="error", error=str(exc)).model_dump())
