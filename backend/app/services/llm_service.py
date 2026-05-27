from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Sequence
from typing import AsyncGenerator

import httpx

from app.config import Settings

logger = logging.getLogger(__name__)


class LocalLLM:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    @staticmethod
    def _system_prompt() -> str:
        return (
            "You are a concise and helpful local voice assistant. "
            "Keep answers brief and conversational. "
            # "Keep the first sentence short (about 8-12 words) so speech can start quickly. "
            "Do not output emojis."
        )

    def _build_messages(
        self,
        prompt: str,
        chat_history: Sequence[tuple[str, str]] | None,
    ) -> list[dict[str, str]]:
        messages: list[dict[str, str]] = [{"role": "system", "content": self._system_prompt()}]

        if chat_history:
            recent_turns = list(chat_history)[-self.settings.llm_history_turns :]
            accumulated = 0
            kept: list[tuple[str, str]] = []
            for user_text, assistant_text in reversed(recent_turns):
                turn_size = len(user_text) + len(assistant_text)
                if kept and accumulated + turn_size > self.settings.llm_history_char_budget:
                    break
                kept.append((user_text, assistant_text))
                accumulated += turn_size

            for user_text, assistant_text in reversed(kept):
                if user_text.strip():
                    messages.append({"role": "user", "content": user_text})
                if assistant_text.strip():
                    messages.append({"role": "assistant", "content": assistant_text})

        messages.append({"role": "user", "content": prompt})
        return messages

    async def stream_reply(
        self,
        prompt: str,
        chat_history: Sequence[tuple[str, str]] | None = None,
    ) -> AsyncGenerator[str, None]:
        logger.info("LLM request: model=%s prompt=%r", self.settings.llm_model_name, prompt)
        messages = self._build_messages(prompt, chat_history)

        provider = self.settings.llm_provider.lower().strip()

        if provider == "ollama":
            try:
                async for token in self._stream_ollama(messages, model_name=self.settings.llm_model_name):
                    yield token
                return
            except Exception as exc:  # noqa: BLE001
                logger.warning("Ollama stream failed: %s", exc)
                reason = f"Ollama: {exc}"
        else:
            try:
                async for token in self._stream_vllm_with_retries(messages):
                    yield token
                return
            except Exception as exc:  # noqa: BLE001
                logger.warning("vLLM stream failed: %s", exc)

                if self.settings.llm_fallback_enabled:
                    try:
                        async for token in self._stream_ollama(messages, model_name=self.settings.ollama_model_name):
                            yield token
                        return
                    except Exception as fallback_exc:  # noqa: BLE001
                        logger.exception("Ollama fallback stream failed: %s", fallback_exc)
                        reason = f"vLLM: {exc} | Ollama: {fallback_exc}"
                else:
                    reason = f"vLLM: {exc}"

        fallback = f"[LLM request failed] {reason}. You said: {prompt}"
        for token in fallback.split(" "):
            yield token + " "
            await asyncio.sleep(0.01)

    async def _stream_vllm_with_retries(self, messages: list[dict[str, str]]) -> AsyncGenerator[str, None]:
        attempts = 6
        last_exc: Exception | None = None

        for attempt in range(1, attempts + 1):
            try:
                async for token in self._stream_vllm(messages):
                    yield token
                return
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                if attempt == attempts:
                    break
                delay = min(2 * attempt, 10)
                logger.info("vLLM not ready (attempt %d/%d): %s; retrying in %ss", attempt, attempts, exc, delay)
                await asyncio.sleep(delay)

        if last_exc is not None:
            raise last_exc
        raise RuntimeError("vLLM request failed without exception")

    async def _stream_vllm(self, messages: list[dict[str, str]]) -> AsyncGenerator[str, None]:
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.settings.vllm_api_key}",
        }

        timeout = httpx.Timeout(connect=10.0, read=None, write=30.0, pool=30.0)
        async with httpx.AsyncClient(
            base_url=self.settings.vllm_base_url.rstrip("/"),
            timeout=timeout,
        ) as client:
            request_candidates: list[tuple[str, int]] = [
                (self.settings.llm_model_name, self.settings.llm_max_tokens),
            ]
            attempted_requests: set[tuple[str, int]] = set()

            while request_candidates:
                model_name, max_tokens = request_candidates.pop(0)
                attempted_requests.add((model_name.lower(), max_tokens))
                token_count = 0
                raw_buffer = ""

                payload = {
                    "model": model_name,
                    "messages": messages,
                    "stream": True,
                    "temperature": self.settings.llm_temperature,
                    "max_tokens": max_tokens,
                }

                async with client.stream("POST", "/v1/chat/completions", headers=headers, json=payload) as resp:
                    if resp.status_code >= 400:
                        body_bytes = await resp.aread()
                        body = body_bytes.decode("utf-8", errors="ignore")[:500]
                        status_code = resp.status_code
                        logger.warning("vLLM HTTP error: status=%s model=%s body=%r", status_code, model_name, body)

                        if status_code in {400, 404}:
                            discovered_model = await self._discover_vllm_model(client, headers)
                            if discovered_model and (discovered_model.lower(), max_tokens) not in attempted_requests and (
                                discovered_model,
                                max_tokens,
                            ) not in request_candidates:
                                logger.warning(
                                    "Retrying vLLM stream with discovered model=%s (configured=%s)",
                                    discovered_model,
                                    self.settings.llm_model_name,
                                )
                                request_candidates.append((discovered_model, max_tokens))
                                continue

                        # If prompt + output budget exceeds model context, reduce output tokens and retry.
                        if status_code == 400 and (
                            "maximum context length" in body.lower() or '"param":"input_text"' in body.lower()
                        ):
                            reduced = max(64, max_tokens // 2)
                            if reduced < max_tokens and (model_name.lower(), reduced) not in attempted_requests and (
                                model_name,
                                reduced,
                            ) not in request_candidates:
                                logger.warning(
                                    "Retrying vLLM stream with reduced max_tokens=%s (previous=%s)",
                                    reduced,
                                    max_tokens,
                                )
                                request_candidates.append((model_name, reduced))
                                continue

                        detail = f"status={status_code} model={model_name}"
                        if body:
                            detail += f" body={body}"
                        raise RuntimeError(f"vLLM chat failed: {detail}")

                    async for raw_line in resp.aiter_lines():
                        if not raw_line or not raw_line.startswith("data:"):
                            continue

                        data = raw_line[5:].strip()
                        if data == "[DONE]":
                            break

                        try:
                            chunk = json.loads(data)
                        except json.JSONDecodeError:
                            logger.debug("vLLM non-json stream chunk: %r", data)
                            continue

                        choices = chunk.get("choices") or []
                        if not choices:
                            continue

                        delta = choices[0].get("delta") or {}
                        text = delta.get("content") or ""
                        if not text:
                            continue

                        if len(raw_buffer) < 300:
                            raw_buffer += text
                            if len(raw_buffer) >= 300:
                                logger.info("vLLM raw output (first 300): %r", raw_buffer[:300])

                        if token_count == 0:
                            logger.info("vLLM first token received: %r", text[:80])
                        token_count += 1
                        yield text

                logger.info(
                    "vLLM stream done: model=%s max_tokens=%s tokens=%d | raw_start=%r",
                    model_name,
                    max_tokens,
                    token_count,
                    raw_buffer[:200],
                )
                return

        raise RuntimeError("vLLM chat failed: no model candidates left")

    async def _discover_vllm_model(self, client: httpx.AsyncClient, headers: dict[str, str]) -> str | None:
        try:
            models_resp = await client.get("/v1/models", headers=headers)
            if models_resp.status_code >= 400:
                return None

            payload = models_resp.json()
            models = payload.get("data") or []
            for model in models:
                model_id = model.get("id")
                if isinstance(model_id, str) and model_id:
                    return model_id
        except Exception:
            return None

        return None

    async def _stream_ollama(
        self,
        messages: list[dict[str, str]],
        model_name: str | None = None,
    ) -> AsyncGenerator[str, None]:
        token_count = 0

        payload = {
            "model": model_name or self.settings.ollama_model_name,
            "messages": messages,
            "stream": True,
            "think": self.settings.ollama_thinking,
            "options": {
                "temperature": self.settings.llm_temperature,
                "num_ctx": self.settings.llm_n_ctx,
            },
        }

        timeout = httpx.Timeout(connect=10.0, read=None, write=30.0, pool=30.0)
        async with httpx.AsyncClient(
            base_url=self.settings.ollama_base_url.rstrip("/"),
            timeout=timeout,
        ) as client:
            async with client.stream("POST", "/api/chat", json=payload) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line:
                        continue
                    try:
                        chunk = json.loads(line)
                    except json.JSONDecodeError:
                        logger.debug("Ollama non-json stream chunk: %r", line)
                        continue

                    msg = chunk.get("message") or {}
                    text = msg.get("content") or ""
                    if not text:
                        continue

                    if token_count == 0:
                        logger.info("Ollama first token received: %r", text[:80])
                    token_count += 1
                    yield text

        logger.info("Ollama stream done: %d tokens", token_count)

    async def runtime_status(self) -> dict[str, object]:
        provider = self.settings.llm_provider.lower().strip()
        timeout = self.settings.llm_request_timeout_s

        if provider == "ollama":
            ollama_models: list[str] = []
            ollama_ok = False
            try:
                async with httpx.AsyncClient(base_url=self.settings.ollama_base_url.rstrip("/"), timeout=timeout) as client:
                    tags_resp = await client.get("/api/tags")
                    ollama_ok = tags_resp.status_code < 400
                    if ollama_ok:
                        payload = tags_resp.json()
                        for model in payload.get("models", []):
                            name = model.get("name")
                            if isinstance(name, str):
                                ollama_models.append(name)
            except Exception:
                ollama_ok = False

            target = self.settings.llm_model_name.lower()
            llm_running = any(name.lower() == target for name in ollama_models)
            return {
                "configured_model": self.settings.llm_model_name,
                "active_backend": "ollama" if ollama_ok else "none",
                "llm_reachable": ollama_ok,
                "llm_running": llm_running,
                "running_models": ollama_models,
                "ollama_reachable": ollama_ok,
            }

        vllm_models: list[str] = []
        vllm_ok = False

        try:
            headers = {"Authorization": f"Bearer {self.settings.vllm_api_key}"}
            async with httpx.AsyncClient(base_url=self.settings.vllm_base_url.rstrip("/"), timeout=timeout) as client:
                health_resp = await client.get("/health")
                vllm_ok = health_resp.status_code < 400
                if vllm_ok:
                    models_resp = await client.get("/v1/models", headers=headers)
                    if models_resp.status_code < 400:
                        payload = models_resp.json()
                        for model in payload.get("data", []):
                            model_id = model.get("id")
                            if isinstance(model_id, str):
                                vllm_models.append(model_id)
        except Exception:
            vllm_ok = False

        if vllm_ok:
            target = self.settings.llm_model_name.lower()
            llm_running = any(name.lower() == target for name in vllm_models)
            return {
                "configured_model": self.settings.llm_model_name,
                "active_backend": "vllm",
                "llm_reachable": True,
                "llm_running": llm_running,
                "running_models": vllm_models,
                "ollama_reachable": False,
            }

        return {
            "configured_model": self.settings.llm_model_name,
            "active_backend": "none",
            "llm_reachable": False,
            "llm_running": False,
            "running_models": [],
            "ollama_reachable": False,
        }

    async def preload_model(self) -> bool:
        """Preload the LLM model by sending a minimal request to warm it up."""
        try:
            logger.info("Preloading LLM model: %s", self.settings.llm_model_name)
            # Send a minimal prompt to trigger model loading
            token_count = 0
            preload_messages = [
                {"role": "system", "content": self._system_prompt()},
                {"role": "user", "content": "Hi"},
            ]
            async for _ in self._stream_ollama(preload_messages, model_name=self.settings.llm_model_name):
                token_count += 1
                if token_count > 3:
                    break
            logger.info("LLM model preloaded successfully")
            return True
        except Exception as exc:
            logger.warning("LLM model preload failed: %s", exc)
            return False
