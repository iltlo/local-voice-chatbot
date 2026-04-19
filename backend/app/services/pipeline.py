from __future__ import annotations

import asyncio
import base64
import logging
import re
import uuid
from typing import AsyncGenerator

from app.config import Settings
from app.models import ServerMessage
from app.services.llm_service import LocalLLM
from app.services.stt_service import STTResult, SenseVoiceSTT
from app.services.tts_service import PiperTTS

logger = logging.getLogger(__name__)


class VoicePipeline:
    _SENTENCE_ENDERS = set(".。！？!?;；\n")

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.stt = SenseVoiceSTT(settings)
        self.llm = LocalLLM(settings)
        self.tts = PiperTTS(settings)

    def _extract_flushable_segments(self, text: str, keep_last_complete: bool) -> tuple[list[str], str]:
        segments: list[str] = []
        start = 0
        for idx, ch in enumerate(text):
            if ch in self._SENTENCE_ENDERS:
                segment = text[start : idx + 1].strip()
                if segment:
                    segments.append(segment)
                start = idx + 1

        remainder = text[start:]
        if keep_last_complete and segments:
            # Keep one completed sentence buffered to avoid wrongly marking a non-final chunk as final.
            remainder = segments.pop() + remainder

        return segments, remainder

    async def handle_audio(
        self,
        audio_bytes: bytes,
        suffix: str = ".webm",
        voice_model_path: str | None = None,
        chat_history: list[tuple[str, str]] | None = None,
    ) -> AsyncGenerator[ServerMessage, None]:
        request_id = str(uuid.uuid4())
        logger.info("Pipeline start: request_id=%s audio_bytes=%d suffix=%s", request_id, len(audio_bytes), suffix)

        stt_result: STTResult = await asyncio.to_thread(self.stt.transcribe_with_metadata, audio_bytes, suffix)
        transcript = stt_result.text
        logger.info("STT transcript: %r language_tag=%s", transcript, stt_result.language_tag)
        yield ServerMessage(type="transcript", transcript=transcript, request_id=request_id)
        full_reply = ""
        tts_pending_text = ""
        llm_token_count = 0
        tts_chunk_count = 0

        try:
            async for token in self.llm.stream_reply(transcript, chat_history=chat_history):
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

                tts_pending_text += token
                flushable_segments, tts_pending_text = self._extract_flushable_segments(
                    tts_pending_text,
                    keep_last_complete=True,
                )

                for segment in flushable_segments:
                    stream_chunks = await asyncio.to_thread(
                        lambda seg=segment: list(self.tts.stream_synthesize(seg, voice_model_path, stt_result.language_tag))
                    )
                    if not stream_chunks:
                        continue

                    tts_status = self.tts.runtime_status()
                    for audio_chunk in stream_chunks:
                        tts_chunk_count += 1
                        logger.info(
                            "Streaming audio chunk: request_id=%s count=%d final=%s",
                            request_id,
                            tts_chunk_count,
                            False,
                        )
                        yield ServerMessage(
                            type="tts_audio_chunk",
                            audio_base64=base64.b64encode(audio_chunk).decode("ascii"),
                            sample_rate=self.settings.tts_sample_rate,
                            request_id=request_id,
                            is_final_chunk=False,
                            tts_voice_id=tts_status.get("tts_last_voice_id"),
                            tts_voice_reason=tts_status.get("tts_last_voice_reason"),
                            tts_text_language=tts_status.get("tts_last_text_language"),
                        )
                        logger.info(
                            "TTS audio ready: request_id=%s bytes=%d final=%s",
                            request_id,
                            len(audio_chunk),
                            False,
                        )

            final_text = full_reply.strip()
            if final_text:
                final_segments, _ = self._extract_flushable_segments(tts_pending_text, keep_last_complete=False)
                if not final_segments and tts_pending_text.strip():
                    final_segments = [tts_pending_text.strip()]

                if final_segments:
                    for seg_idx, segment in enumerate(final_segments, start=1):
                        stream_chunks = await asyncio.to_thread(
                            lambda seg=segment: list(self.tts.stream_synthesize(seg, voice_model_path, stt_result.language_tag))
                        )
                        if not stream_chunks:
                            continue

                        tts_status = self.tts.runtime_status()
                        is_last_segment = seg_idx == len(final_segments)
                        for chunk_idx, audio_chunk in enumerate(stream_chunks, start=1):
                            is_final = is_last_segment and chunk_idx == len(stream_chunks)
                            tts_chunk_count += 1
                            logger.info(
                                "Streaming audio chunk: request_id=%s count=%d final=%s",
                                request_id,
                                tts_chunk_count,
                                is_final,
                            )
                            yield ServerMessage(
                                type="tts_audio_chunk",
                                audio_base64=base64.b64encode(audio_chunk).decode("ascii"),
                                sample_rate=self.settings.tts_sample_rate,
                                request_id=request_id,
                                is_final_chunk=is_final,
                                tts_voice_id=tts_status.get("tts_last_voice_id"),
                                tts_voice_reason=tts_status.get("tts_last_voice_reason"),
                                tts_text_language=tts_status.get("tts_last_text_language"),
                            )
                            logger.info(
                                "TTS audio ready: request_id=%s bytes=%d final=%s",
                                request_id,
                                len(audio_chunk),
                                is_final,
                            )
                elif tts_chunk_count == 0:
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
