from __future__ import annotations

import io
import logging
import os
import re
import shutil
import subprocess
import tempfile
import wave
from pathlib import Path
from typing import Any, Iterator

from app.config import Settings

try:
    import pysbd  # type: ignore
except Exception:  # pragma: no cover - optional dependency fallback
    pysbd = None


logger = logging.getLogger(__name__)


class PiperTTS:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._fallback = False
        self._piper_exe = self._resolve_piper_executable()
        self._default_model_path = self._resolve_preferred_model(
            self.settings.piper_default_voice_id,
            self.settings.piper_model_path,
        )
        chinese_model = self.resolve_voice_model(self.settings.piper_chinese_voice_id)
        self._chinese_model_path = chinese_model if chinese_model else None
        cantonese_model = self.resolve_voice_model(self.settings.piper_cantonese_voice_id)
        self._cantonese_model_path = cantonese_model if cantonese_model else None
        chinese_fallback_model = self.resolve_voice_model(self.settings.piper_chinese_fallback_voice_id)
        self._chinese_fallback_model_path = chinese_fallback_model if chinese_fallback_model else None
        self._last_model_path = self._default_model_path
        self._last_voice_reason = "default"
        self._last_text_language = "unknown"
        self._python_piper_voice_cache: dict[str, Any] = {}
        self._sentence_segmenters: dict[str, Any] = {}
        self._logged_xiaoya_cli_incompat = False
        self._ensure_persistent_chinese_resources()
        self._validate_paths()

    @staticmethod
    def _voice_id_from_path(model_path: str | None) -> str | None:
        if not model_path:
            return None
        return Path(model_path).stem

    @property
    def default_model_path(self) -> str:
        return self._default_model_path

    @property
    def chinese_model_path(self) -> str | None:
        return self._chinese_model_path

    @property
    def chinese_fallback_model_path(self) -> str | None:
        return self._chinese_fallback_model_path

    def runtime_status(self) -> dict[str, str | bool | None]:
        return {
            "tts_available": not self._fallback,
            "tts_default_voice_id": self._voice_id_from_path(self._default_model_path),
            "tts_chinese_voice_id": self._voice_id_from_path(self._chinese_model_path),
            "tts_cantonese_voice_id": self._voice_id_from_path(self._cantonese_model_path),
            "tts_last_voice_id": self._voice_id_from_path(self._last_model_path),
            "tts_last_voice_reason": self._last_voice_reason,
            "tts_last_text_language": self._last_text_language,
        }

    def _ensure_persistent_chinese_resources(self) -> None:
        """Persist g2pW resources in mounted models path to avoid repeated downloads."""
        runtime_path = Path("/app/g2pW")
        persisted_path = Path(self.settings.piper_voices_dir).parent / "g2pW"
        try:
            persisted_path.mkdir(parents=True, exist_ok=True)

            if runtime_path.is_symlink():
                return

            if runtime_path.exists() and not runtime_path.is_symlink():
                # Keep existing files but ensure future downloads are stored on mounted path.
                if not any(persisted_path.iterdir()):
                    shutil.copytree(runtime_path, persisted_path, dirs_exist_ok=True)
                return

            runtime_path.symlink_to(persisted_path, target_is_directory=True)
            logger.info("Linked Chinese phoneme cache to persistent path: %s -> %s", runtime_path, persisted_path)
        except Exception as exc:  # noqa: BLE001
            logger.info("Persistent Chinese cache setup skipped: %s", exc)

    def preload_voices(self) -> dict[str, bool]:
        """Preload default English and Chinese voices."""
        results = {}
        try:
            logger.info("Preloading English voice: %s", self._voice_id_from_path(self._default_model_path))
            chunks = list(self.stream_synthesize("Hi", self._default_model_path, "english"))
            results["english"] = len(chunks) > 0
            logger.info("English voice preloaded: %s", results["english"])
        except Exception as exc:
            logger.warning("English voice preload failed: %s", exc)
            results["english"] = False

        try:
            logger.info("Preloading Chinese voice: %s", self._voice_id_from_path(self._chinese_model_path))
            chunks = list(self.stream_synthesize("你好", self._chinese_model_path, "chinese"))
            results["chinese"] = len(chunks) > 0
            logger.info("Chinese voice preloaded: %s", results["chinese"])
        except Exception as exc:
            logger.warning("Chinese voice preload failed: %s", exc)
            results["chinese"] = False

        return results

    def _candidate_models(self, voice_model_path: str | None, language: str) -> list[tuple[str | None, str]]:
        preferred_model = voice_model_path or self._default_model_path
        candidates: list[tuple[str | None, str]] = []

        if voice_model_path:
            candidates.append((voice_model_path, "selected"))
        elif language == "cantonese":
            # Prefer explicit Cantonese model only. If absent, let pipeline trigger basic TTS fallback.
            candidates.append((self._cantonese_model_path, "cantonese_primary"))
        elif language == "chinese":
            candidates.append((self._chinese_model_path, "chinese_primary"))
            candidates.append((self._chinese_fallback_model_path, "chinese_fallback"))
            candidates.append((self._default_model_path, "chinese_default_fallback"))
        else:
            candidates.append((preferred_model, "default"))
            if preferred_model != self._default_model_path:
                candidates.append((self._default_model_path, "default_fallback"))

        return candidates

    def _resolve_piper_executable(self) -> str | None:
        configured = Path(self.settings.piper_exe_path)
        candidates: list[Path] = [configured]

        # In containers we may receive a Windows .exe path from old settings.
        if configured.suffix.lower() == ".exe":
            candidates.append(configured.with_suffix(""))

        candidates.append(Path("/opt/piper/piper/piper"))
        candidates.append(Path("/opt/piper/piper"))

        discovered = shutil.which("piper")
        if discovered:
            candidates.append(Path(discovered))

        seen: set[str] = set()
        for candidate in candidates:
            candidate_str = str(candidate)
            if candidate_str in seen:
                continue
            seen.add(candidate_str)

            if not candidate.exists():
                continue

            if os.name == "nt" or os.access(candidate, os.X_OK):
                return candidate_str

        return None

    def _validate_paths(self) -> None:
        exe_exists = bool(self._piper_exe and Path(self._piper_exe).exists())
        model = Path(self._default_model_path)
        if not exe_exists or not model.exists():
            self._fallback = True
            logger.warning(
                "Piper fallback enabled: exe_exists=%s model_exists=%s configured_exe=%s resolved_exe=%s",
                exe_exists,
                model.exists(),
                self.settings.piper_exe_path,
                self._piper_exe,
            )

    def _resolve_preferred_model(self, voice_id: str | None, fallback: str | None = None) -> str:
        resolved = self.resolve_voice_model(voice_id)
        if resolved:
            return resolved
        if fallback:
            return fallback
        return ""

    @staticmethod
    def _contains_cjk(text: str) -> bool:
        return any("\u4e00" <= ch <= "\u9fff" for ch in text)

    @staticmethod
    def _language_from_tag(language_tag: str | None) -> str | None:
        if not language_tag:
            return None

        normalized = language_tag.lower().strip()
        if not normalized:
            return None

        if normalized in {"cantonese", "yue", "zh-hk", "zh-yue", "zh-yue-hant"}:
            return "cantonese"
        if normalized in {"chinese", "zh", "cmn", "mandarin"}:
            return "chinese"
        if normalized in {"english", "en"}:
            return "english"

        if normalized.startswith(("yue", "zh-hk", "zh-yue")):
            return "cantonese"
        if normalized.startswith(("zh", "cn", "cmn")):
            return "chinese"
        return "english"

    def _detect_language(self, text: str) -> str | None:
        cjk_count = len(re.findall(r"[\u4e00-\u9fff]", text))
        latin_count = len(re.findall(r"[A-Za-z]", text))

        if cjk_count == 0 and latin_count == 0:
            return None
        if cjk_count == 0:
            return "english"
        if latin_count == 0:
            return "chinese"

        # For mixed output, choose the dominant script in the assistant text.
        return "chinese" if cjk_count >= latin_count else "english"

    def resolve_tts_language(self, text: str, stt_language_tag: str | None) -> str:
        detected_language = self._detect_language(text)
        tagged_language = self._language_from_tag(stt_language_tag)

        # Cantonese users often receive Chinese-script output. Keep Cantonese voice path in that case.
        if tagged_language == "cantonese" and detected_language == "chinese":
            return "cantonese"

        return detected_language or tagged_language or "english"

    def list_available_voices(self) -> list[dict[str, str]]:
        voices_dir = Path(self.settings.piper_voices_dir)
        if not voices_dir.exists():
            return []

        voices: list[dict[str, str]] = []
        for model_path in sorted(voices_dir.glob("*.onnx")):
            config_path = model_path.with_suffix(model_path.suffix + ".json")
            voices.append(
                {
                    "id": model_path.stem,
                    "label": model_path.stem.replace("_", " "),
                    "model_path": str(model_path),
                    "config_path": str(config_path) if config_path.exists() else "",
                }
            )
        return voices

    def resolve_voice_model(self, voice_id: str | None) -> str | None:
        if not voice_id:
            return None

        candidate = Path(voice_id)
        if candidate.is_absolute() and candidate.exists():
            return str(candidate)

        voices_dir = Path(self.settings.piper_voices_dir)
        if not voice_id.endswith(".onnx"):
            candidate = voices_dir / f"{voice_id}.onnx"
            if candidate.exists():
                return str(candidate)

        candidate = voices_dir / voice_id
        if candidate.exists():
            return str(candidate)

        return None

    def _synthesize_with_model(self, text: str, model_path: str, expected_failure: bool = False) -> bytes:
        with tempfile.TemporaryDirectory() as tmpdir:
            out_path = Path(tmpdir) / "chunk.wav"
            cmd = [
                self._piper_exe or self.settings.piper_exe_path,
                "--model",
                model_path,
                "--output_file",
                str(out_path),
            ]

            config_path = Path(model_path).with_suffix(Path(model_path).suffix + ".json")
            if config_path.exists():
                cmd.extend(["--config", str(config_path)])

            process = subprocess.run(
                cmd,
                input=text.encode("utf-8"),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            if process.returncode != 0 or not out_path.exists():
                stderr_msg = process.stderr.decode("utf-8", errors="ignore").strip()
                is_xiaoya_codepoint_error = (
                    "zh_CN-xiao_ya-medium" in model_path
                    and "not a single codepoint" in stderr_msg
                )
                if expected_failure and is_xiaoya_codepoint_error:
                    if not self._logged_xiaoya_cli_incompat:
                        logger.info(
                            "Piper CLI incompatibility for zh_CN-xiao_ya-medium detected; using Python Piper retry."
                        )
                        self._logged_xiaoya_cli_incompat = True
                else:
                    logger.warning(
                        "TTS synth failed: model=%s returncode=%s stderr=%r",
                        model_path,
                        process.returncode,
                        stderr_msg[:240],
                    )
                return b""

            return out_path.read_bytes()

    @staticmethod
    def _wav_from_pcm(pcm: bytes, sample_rate: int, channels: int, sample_width: int) -> bytes:
        if not pcm:
            return b""

        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as wav:
            wav.setnchannels(channels)
            wav.setsampwidth(sample_width)
            wav.setframerate(sample_rate)
            wav.writeframes(pcm)
        return buffer.getvalue()

    def _stream_with_python_piper(self, text: str, model_path: str) -> Iterator[bytes]:
        from piper.voice import PiperVoice  # type: ignore

        voice = self._python_piper_voice_cache.get(model_path)
        if voice is None:
            config_path = f"{model_path}.json"
            voice = PiperVoice.load(model_path, config_path=config_path, use_cuda=False)
            self._python_piper_voice_cache[model_path] = voice

        for chunk in voice.synthesize(text):
            pcm = getattr(chunk, "audio_int16_bytes", b"")
            if not pcm:
                continue

            sample_rate = int(getattr(chunk, "sample_rate", self.settings.tts_sample_rate) or self.settings.tts_sample_rate)
            channels = int(getattr(chunk, "sample_channels", 1) or 1)
            sample_width = int(getattr(chunk, "sample_width", 2) or 2)
            wav_chunk = self._wav_from_pcm(pcm, sample_rate, channels, sample_width)
            if wav_chunk:
                yield wav_chunk

    @staticmethod
    def _sanitize_for_chinese_voice(text: str) -> str:
        # Keep Chinese characters/punctuation and drop Latin tokens that break some Chinese Piper voices.
        sanitized = re.sub(r"[A-Za-z]+", " ", text)
        sanitized = re.sub(r"\s+", " ", sanitized).strip()
        return sanitized or "你好"

    @staticmethod
    def _split_text_for_streaming(text: str) -> list[str]:
        parts = re.split(r"(?<=[。！？!?;；.])\s+", text.strip())
        return [part for part in parts if part]

    def _split_text_with_pysbd(self, text: str, language: str) -> list[str]:
        if pysbd is None:
            return self._split_text_for_streaming(text)

        lang_code = "zh" if language == "chinese" else "en"
        segmenter = self._sentence_segmenters.get(lang_code)
        if segmenter is None:
            segmenter = pysbd.Segmenter(language=lang_code, clean=False)
            self._sentence_segmenters[lang_code] = segmenter

        segments = [segment.strip() for segment in segmenter.segment(text) if segment and segment.strip()]
        if segments:
            return segments
        return self._split_text_for_streaming(text)

    @staticmethod
    def _script_type(ch: str) -> str:
        if "\u4e00" <= ch <= "\u9fff":
            return "cjk"
        if ch.isascii() and ch.isalpha():
            return "latin"
        return "neutral"

    def _split_mixed_language_runs(self, text: str) -> list[str]:
        stripped = text.strip()
        if not stripped:
            return []

        has_cjk = any(self._script_type(ch) == "cjk" for ch in stripped)
        has_latin = any(self._script_type(ch) == "latin" for ch in stripped)
        if not (has_cjk and has_latin):
            return [stripped]

        runs: list[str] = []
        current = ""
        current_type: str | None = None

        for ch in stripped:
            ch_type = self._script_type(ch)

            if current_type is None:
                current = ch
                current_type = ch_type
                continue

            # Keep punctuation/spacing attached to the active script run.
            if ch_type == "neutral" or current_type == "neutral" or ch_type == current_type:
                current += ch
                if current_type == "neutral" and ch_type != "neutral":
                    current_type = ch_type
                continue

            if current.strip():
                runs.append(current.strip())
            current = ch
            current_type = ch_type

        if current.strip():
            runs.append(current.strip())

        return runs or [stripped]

    def _stream_single_language_synthesis(
        self,
        text: str,
        voice_model_path: str | None,
        stt_language_tag: str | None,
    ) -> Iterator[bytes]:
        tagged_language = self._language_from_tag(stt_language_tag)
        detected_language = self._detect_language(text)
        language = self.resolve_tts_language(text, stt_language_tag)

        if detected_language and tagged_language and detected_language != tagged_language:
            logger.info(
                "TTS language resolved with override: detected=%s stt_tag=%s final=%s",
                detected_language,
                tagged_language,
                language,
            )

        candidates = self._candidate_models(voice_model_path, language)

        seen: set[str] = set()
        for model_path, reason in candidates:
            if not model_path or model_path in seen:
                continue
            seen.add(model_path)

            if not Path(model_path).exists():
                logger.warning("TTS model missing: %s", model_path)
                continue

            synthesis_text = text
            if language == "chinese" and reason.startswith("chinese"):
                synthesis_text = self._sanitize_for_chinese_voice(text)

            yielded = False
            try:
                segments = self._split_text_with_pysbd(synthesis_text, language)
                for segment in segments:
                    for wav_chunk in self._stream_with_python_piper(segment, model_path):
                        yielded = True
                        yield wav_chunk

                if yielded:
                    self._last_model_path = model_path
                    self._last_voice_reason = reason
                    self._last_text_language = language
                    logger.info(
                        "TTS model selected: language=%s voice=%s reason=%s",
                        language,
                        self._voice_id_from_path(model_path),
                        reason,
                    )
                    return
            except Exception as exc:
                logger.warning("Python piper stream failed: model=%s error=%s", model_path, exc)

            expected_cli_failure = language == "chinese" and reason == "chinese_primary"
            audio = self._synthesize_with_model(synthesis_text, model_path, expected_failure=expected_cli_failure)
            if audio:
                self._last_model_path = model_path
                self._last_voice_reason = reason
                self._last_text_language = language
                logger.info(
                    "TTS model selected: language=%s voice=%s reason=%s",
                    language,
                    self._voice_id_from_path(model_path),
                    reason,
                )
                yield audio
                return

    def stream_synthesize(
        self,
        text: str,
        voice_model_path: str | None = None,
        stt_language_tag: str | None = None,
    ) -> Iterator[bytes]:
        if self._fallback:
            logger.info("TTS stream skipped (fallback enabled)")
            return

        run_texts = self._split_mixed_language_runs(text)
        if len(run_texts) > 1 and not voice_model_path:
            logger.info("TTS mixed-language split applied: runs=%d", len(run_texts))

        tagged_language = self._language_from_tag(stt_language_tag)
        has_cantonese_model = bool(self._cantonese_model_path and Path(self._cantonese_model_path).exists())
        is_mixed_runs = len(run_texts) > 1

        for run_text in run_texts:
            run_language_tag = stt_language_tag
            # For mixed-script replies, keep Chinese runs audible when no Cantonese Piper model is configured.
            if (
                is_mixed_runs
                and tagged_language == "cantonese"
                and not has_cantonese_model
                and self._detect_language(run_text) == "chinese"
            ):
                run_language_tag = "chinese"

            for audio in self._stream_single_language_synthesis(run_text, voice_model_path, run_language_tag):
                yield audio

    def synthesize(
        self,
        text: str,
        voice_model_path: str | None = None,
        stt_language_tag: str | None = None,
    ) -> bytes:
        chunks = list(self.stream_synthesize(text, voice_model_path, stt_language_tag))
        if not chunks:
            return b""
        if len(chunks) == 1:
            return chunks[0]
        return b"".join(chunks)
