from functools import lru_cache
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


BACKEND_ROOT = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    host: str = "0.0.0.0"
    port: int = 8000
    cors_origin: str = "http://localhost:5173"

    # LLM provider: ollama (preferred) or vllm
    llm_provider: str = "ollama"
    llm_model_name: str = "qwen3.5:4b"
    vllm_base_url: str = "http://localhost:8001"
    vllm_api_key: str = "EMPTY"
    llm_fallback_enabled: bool = False
    ollama_base_url: str = "http://localhost:11434"
    ollama_model_name: str = "qwen3.5:4b"
    ollama_thinking: bool = False
    llm_request_timeout_s: float = 10.0
    llm_n_ctx: int = 1536
    llm_max_tokens: int = 256
    llm_temperature: float = 0.7

    # SenseVoice (FunASR)
    sensevoice_model_dir: str = "./models/SenseVoiceSmall"
    sensevoice_device: str = "cuda:0"

    # Piper TTS
    piper_exe_path: str = "./bin/piper/piper/piper.exe"
    piper_voices_dir: str = "./models/piper"
    piper_default_voice_id: str = "en_US-hfc_female-medium"
    piper_chinese_voice_id: str = "zh_CN-xiao_ya-medium"
    piper_chinese_fallback_voice_id: str = "zh_CN-huayan-medium"
    piper_model_path: str = "./models/piper/en_US-amy-medium.onnx"
    piper_config_path: str = "./models/piper/en_US-amy-medium.onnx.json"
    tts_sample_rate: int = 22050
    tts_min_chunk_chars: int = 28
    tts_first_chunk_chars: int = 16

    def model_post_init(self, __context: object) -> None:
        self.sensevoice_model_dir = self._resolve_backend_path(self.sensevoice_model_dir)
        self.piper_exe_path = self._resolve_backend_path(self.piper_exe_path)
        self.piper_voices_dir = self._resolve_backend_path(self.piper_voices_dir)
        self.piper_model_path = self._resolve_backend_path(self.piper_model_path)
        self.piper_config_path = self._resolve_backend_path(self.piper_config_path)

    @staticmethod
    def _resolve_backend_path(path_value: str) -> str:
        path = Path(path_value)
        if path.is_absolute():
            return str(path)
        return str((BACKEND_ROOT / path).resolve())


@lru_cache
def get_settings() -> Settings:
    return Settings()
