from __future__ import annotations

import asyncio
import base64
import logging
import uuid
from typing import AsyncGenerator

from app.config import Settings
from app.models import ServerMessage
from app.services.llm_service import LocalLLM
from app.services.stt_service import STTResult, SenseVoiceSTT
from app.services.tts_service import PiperTTS

logger = logging.getLogger(__name__)


class VoicePipeline:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.stt = SenseVoiceSTT(settings)
        self.llm = LocalLLM(settings)
        self.tts = PiperTTS(settings)

    async def handle_audio(
        self,
        audio_bytes: bytes,
        suffix: str = ".webm",
        voice_model_path: str | None = None,
    ) -> AsyncGenerator[ServerMessage, None]:
        request_id = str(uuid.uuid4())
        logger.info("Pipeline start: request_id=%s audio_bytes=%d suffix=%s", request_id, len(audio_bytes), suffix)

        stt_result: STTResult = await asyncio.to_thread(self.stt.transcribe_with_metadata, audio_bytes, suffix)
        transcript = stt_result.text
        logger.info("STT transcript: %r language_tag=%s", transcript, stt_result.language_tag)
        yield ServerMessage(type="transcript", transcript=transcript, request_id=request_id)
        full_reply = ""
        llm_token_count = 0
        tts_chunk_count = 0

        try:
            async for token in self.llm.stream_reply(transcript):
                full_reply += token
                llm_token_count += 1
                if llm_token_count <= 5 or llm_token_count % 50 == 0:
                    logger.info(
                        "LLM token stream: request_id=%s count=%d token=%r",
                        request_id,
                        llm_token_count,
                        token[:80],
                    )
                yield ServerMessage(type="llm_token", token=token, request_id=request_id)

            final_text = full_reply.strip()
            if final_text:
                audio_chunk = await asyncio.to_thread(
                    self.tts.synthesize,
                    final_text,
                    voice_model_path,
                    stt_result.language_tag,
                )
                if audio_chunk:
                    tts_status = self.tts.runtime_status()
                    tts_chunk_count = 1
                    logger.info(
                        "Streaming audio chunk: request_id=%s count=%d final=%s",
                        request_id,
                        tts_chunk_count,
                        True,
                    )
                    yield ServerMessage(
                        type="tts_audio_chunk",
                        audio_base64=base64.b64encode(audio_chunk).decode("ascii"),
                        sample_rate=self.settings.tts_sample_rate,
                        request_id=request_id,
                        is_final_chunk=True,
                        tts_voice_id=tts_status.get("tts_last_voice_id"),
                        tts_voice_reason=tts_status.get("tts_last_voice_reason"),
                        tts_text_language=tts_status.get("tts_last_text_language"),
                    )
                    logger.info("TTS audio ready: request_id=%s bytes=%d final=%s", request_id, len(audio_chunk), True)
                else:
                    logger.info("TTS audio not generated: request_id=%s chars=%d", request_id, len(final_text))

            logger.info(
                "Pipeline done: request_id=%s llm_tokens=%d tts_chunks=%d reply_chars=%d",
                request_id,
                llm_token_count,
                tts_chunk_count,
                len(final_text),
            )
            yield ServerMessage(type="llm_done", text=final_text, request_id=request_id)
        except asyncio.CancelledError:
            logger.info("Pipeline cancelled: request_id=%s", request_id)
            raise
