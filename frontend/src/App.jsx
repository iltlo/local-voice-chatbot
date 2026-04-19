import { useEffect, useMemo, useRef, useState } from "react";

const WS_URL = import.meta.env.VITE_WS_URL || "ws://localhost:8000/ws";
const STATUS_URL = WS_URL.replace("ws://", "http://").replace("wss://", "https://").replace(/\/ws$/, "/status");
const ENABLE_BROWSER_TTS_FALLBACK = false;

function bytesToBase64(bytes) {
  let binary = "";
  const chunk = 0x8000;
  for (let i = 0; i < bytes.length; i += chunk) {
    const slice = bytes.subarray(i, i + chunk);
    binary += String.fromCharCode(...slice);
  }
  return btoa(binary);
}

function base64ToArrayBuffer(base64) {
  const binary = atob(base64);
  const len = binary.length;
  const bytes = new Uint8Array(len);
  for (let i = 0; i < len; i += 1) {
    bytes[i] = binary.charCodeAt(i);
  }
  return bytes.buffer;
}

export default function App() {
  const [socketState, setSocketState] = useState("connecting");
  const [isRecording, setIsRecording] = useState(false);
  const [transcript, setTranscript] = useState("");
  const [assistantText, setAssistantText] = useState("");
  const [chatHistory, setChatHistory] = useState([]);
  const [status, setStatus] = useState("Hold Space to talk");
  const [runtime, setRuntime] = useState({
    configured_model: "unknown",
    models_loaded: false,
    llm_reachable: false,
    llm_running: false,
    tts_available: false,
    tts_default_voice_id: "unknown",
    tts_chinese_voice_id: "unknown",
    tts_chinese_fallback_voice_id: "unknown",
    tts_last_voice_id: "unknown",
    tts_last_voice_reason: "unknown",
    tts_last_text_language: "unknown",
    vram_used_mb: null,
    vram_total_mb: null,
    vram_percent: null,
    gpu_available: false,
  });
  const [ttsStreamActive, setTtsStreamActive] = useState(false);

  const wsRef = useRef(null);
  const mediaStreamRef = useRef(null);
  const mediaRecorderRef = useRef(null);
  const chunksRef = useRef([]);
  const isSpacePressedRef = useRef(false);

  const audioContextRef = useRef(null);
  const playbackChainRef = useRef(Promise.resolve());
  const playbackGenerationRef = useRef(0);
  const activeSourceRef = useRef(null);
  const hasPlayedAudioRef = useRef(false);
  const pendingSpeechRef = useRef("");
  const currentRequestIdRef = useRef(null);
  const interruptedRequestIdsRef = useRef(new Set());
  const isAssistantStreamingRef = useRef(false);

  const canTalk = useMemo(() => socketState === "connected", [socketState]);

  useEffect(() => {
    const ws = new WebSocket(WS_URL);
    wsRef.current = ws;

    ws.onopen = () => setSocketState("connected");
    ws.onclose = () => setSocketState("disconnected");
    ws.onerror = () => setSocketState("error");

    ws.onmessage = async (event) => {
      const msg = JSON.parse(event.data);

      if (msg.type === "ready") {
        if (msg.runtime) {
          setRuntime((prev) => ({ ...prev, ...msg.runtime }));
        }
        setStatus(msg.runtime?.models_loaded ? "Ready - Hold Space to talk" : "Loading models...");
        return;
      }

      if (msg.type === "runtime_update") {
        if (msg.runtime) {
          setRuntime((prev) => ({ ...prev, ...msg.runtime }));
          if (msg.runtime.models_loaded) {
            setStatus("Ready - Hold Space to talk");
          }
        }
        return;
      }

      if (msg.type === "interrupted") {
        const requestId = msg.request_id || currentRequestIdRef.current;
        if (requestId) {
          interruptedRequestIdsRef.current.add(requestId);
          setChatHistory((prev) => prev.map((item) => (item.id === requestId ? { ...item, interrupted: true } : item)));
        }
        setStatus("Interrupted. Hold Space to talk");
        setTtsStreamActive(false);
        isAssistantStreamingRef.current = false;
        return;
      }

      if (msg.type === "transcript") {
        const requestId = msg.request_id || `${Date.now()}`;
        currentRequestIdRef.current = requestId;
        interruptedRequestIdsRef.current.delete(requestId);
        isAssistantStreamingRef.current = true;

        setTranscript(msg.transcript || "");
        setAssistantText("");
        hasPlayedAudioRef.current = false;
        pendingSpeechRef.current = "";
        stopAllAudioOutput();
        setTtsStreamActive(false);
        setChatHistory((prev) => [
          ...prev,
          {
            id: requestId,
            userText: msg.transcript || "",
            assistantText: "",
            interrupted: false,
          },
        ]);
        setStatus("Generating response...");
        return;
      }

      if (msg.type === "llm_token") {
        const requestId = msg.request_id || currentRequestIdRef.current;
        if (requestId && interruptedRequestIdsRef.current.has(requestId)) {
          return;
        }
        const token = msg.token || "";
        setAssistantText((prev) => prev + token);
        if (requestId && token) {
          setChatHistory((prev) =>
            prev.map((item) => (item.id === requestId ? { ...item, assistantText: item.assistantText + token } : item))
          );
        }
        return;
      }

      if (msg.type === "tts_audio_chunk" && msg.audio_base64) {
        const requestId = msg.request_id || currentRequestIdRef.current;
        if (requestId && interruptedRequestIdsRef.current.has(requestId)) {
          return;
        }
        if (msg.tts_voice_id) {
          setRuntime((prev) => ({
            ...prev,
            tts_last_voice_id: msg.tts_voice_id,
            tts_last_voice_reason: msg.tts_voice_reason || prev.tts_last_voice_reason,
            tts_last_text_language: msg.tts_text_language || prev.tts_last_text_language,
          }));
        }
        hasPlayedAudioRef.current = true;
        pendingSpeechRef.current = "";
        if (window.speechSynthesis) {
          window.speechSynthesis.cancel();
        }
        setTtsStreamActive(true);
        await enqueueAudioChunk(msg.audio_base64);
        return;
      }

      if (msg.type === "llm_done") {
        const requestId = msg.request_id || currentRequestIdRef.current;
        if (requestId && interruptedRequestIdsRef.current.has(requestId)) {
          setStatus("Interrupted. Hold Space to talk");
          setTtsStreamActive(false);
          isAssistantStreamingRef.current = false;
          return;
        }
        if (ENABLE_BROWSER_TTS_FALLBACK && !hasPlayedAudioRef.current) {
          if (msg.text && !pendingSpeechRef.current.trim()) {
            pendingSpeechRef.current = msg.text;
          }
          streamFallbackSpeech(true);
        }
        if (requestId && msg.text) {
          setChatHistory((prev) =>
            prev.map((item) => (item.id === requestId ? { ...item, assistantText: msg.text } : item))
          );
        }
        setStatus("Ready - Hold Space to talk");
        setTtsStreamActive(false);
        isAssistantStreamingRef.current = false;
        return;
      }

      if (msg.type === "error") {
        setStatus(`Error: ${msg.error || "Unknown error"}`);
      }

    };

    return () => {
      ws.close();
    };
  }, []);

  useEffect(() => {
    const onKeyDown = async (event) => {
      if (event.code !== "Space") {
        return;
      }
      event.preventDefault();
      if (isSpacePressedRef.current) {
        return;
      }
      isSpacePressedRef.current = true;

      if (isAssistantStreamingRef.current || ttsStreamActive || window.speechSynthesis?.speaking) {
        await interruptActiveResponse();
        return;
      }

      await startRecording();
    };

    const onKeyUp = async (event) => {
      if (event.code !== "Space") {
        return;
      }
      event.preventDefault();
      isSpacePressedRef.current = false;
      await stopRecording();
    };

    window.addEventListener("keydown", onKeyDown);
    window.addEventListener("keyup", onKeyUp);
    return () => {
      window.removeEventListener("keydown", onKeyDown);
      window.removeEventListener("keyup", onKeyUp);
    };
  });

  useEffect(() => {
    let cancelled = false;

    const fetchStatus = async () => {
      try {
        const res = await fetch(STATUS_URL);
        if (!res.ok) {
          return;
        }
        const data = await res.json();
        if (!cancelled) {
          setRuntime((prev) => ({ ...prev, ...data }));
        }
      } catch {
        // Ignore transient polling errors.
      }
    };

    fetchStatus();
    const id = setInterval(fetchStatus, 5000);

    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  async function ensureAudioContext() {
    if (!audioContextRef.current) {
      audioContextRef.current = new AudioContext();
    }
    if (audioContextRef.current.state === "suspended") {
      await audioContextRef.current.resume();
    }
  }

  function speakFallback(text) {
    const synth = window.speechSynthesis;
    if (!synth || !text?.trim()) {
      return;
    }

    const utterance = new SpeechSynthesisUtterance(text);
    utterance.rate = 1;
    utterance.pitch = 1;
    synth.speak(utterance);
  }

  function stopAllAudioOutput() {
    playbackGenerationRef.current += 1;
    pendingSpeechRef.current = "";
    if (window.speechSynthesis) {
      window.speechSynthesis.cancel();
    }
    if (activeSourceRef.current) {
      try {
        activeSourceRef.current.stop();
      } catch {
        // Ignore stop errors for already-ended sources.
      }
      activeSourceRef.current = null;
    }
    playbackChainRef.current = Promise.resolve();
  }

  async function interruptActiveResponse() {
    const requestId = currentRequestIdRef.current;
    if (!requestId) {
      stopAllAudioOutput();
      return;
    }

    interruptedRequestIdsRef.current.add(requestId);
    wsRef.current?.send(
      JSON.stringify({
        type: "interrupt",
        request_id: requestId,
      })
    );

    setChatHistory((prev) => prev.map((item) => (item.id === requestId ? { ...item, interrupted: true } : item)));
    isAssistantStreamingRef.current = false;
    setTtsStreamActive(false);
    setStatus("Interrupted. Hold Space to talk");
    stopAllAudioOutput();
  }

  function streamFallbackSpeech(forceFlush) {
    if (!ENABLE_BROWSER_TTS_FALLBACK) {
      return;
    }

    const text = pendingSpeechRef.current;
    if (!text.trim()) {
      return;
    }

    const hasBoundary = /[.!?。！？]\s*$/.test(text);
    const minChunkReached = text.length >= 80;
    if (!forceFlush && !hasBoundary && !minChunkReached) {
      return;
    }

    speakFallback(text.trim());
    pendingSpeechRef.current = "";
  }

  async function ensureMicStream() {
    if (mediaStreamRef.current) {
      return mediaStreamRef.current;
    }
    mediaStreamRef.current = await navigator.mediaDevices.getUserMedia({ audio: true });
    return mediaStreamRef.current;
  }

  async function startRecording() {
    if (!canTalk || isRecording) {
      return;
    }

    await ensureAudioContext();
    const stream = await ensureMicStream();

    chunksRef.current = [];
    const recorder = new MediaRecorder(stream, { mimeType: "audio/webm" });
    mediaRecorderRef.current = recorder;

    recorder.ondataavailable = (e) => {
      if (e.data.size > 0) {
        chunksRef.current.push(e.data);
      }
    };

    recorder.start();
    setIsRecording(true);
    setStatus("Listening...");
  }

  async function stopRecording() {
    if (!isRecording || !mediaRecorderRef.current) {
      return;
    }

    const recorder = mediaRecorderRef.current;
    await new Promise((resolve) => {
      recorder.onstop = resolve;
      recorder.stop();
    });

    const blob = new Blob(chunksRef.current, { type: "audio/webm" });
    const buffer = await blob.arrayBuffer();
    const base64 = bytesToBase64(new Uint8Array(buffer));

    wsRef.current?.send(
      JSON.stringify({
        type: "user_audio",
        mime_type: "audio/webm",
        audio_base64: base64,
      })
    );

    setIsRecording(false);
    setStatus("Transcribing...");
  }

  async function enqueueAudioChunk(base64) {
    await ensureAudioContext();
    const generation = playbackGenerationRef.current;

    playbackChainRef.current = playbackChainRef.current
      .then(async () => {
        if (generation !== playbackGenerationRef.current) {
          return;
        }
        const context = audioContextRef.current;
        const arrayBuffer = base64ToArrayBuffer(base64);
        const audioBuffer = await context.decodeAudioData(arrayBuffer.slice(0));
        await new Promise((resolve) => {
          if (generation !== playbackGenerationRef.current) {
            resolve();
            return;
          }
          const source = context.createBufferSource();
          activeSourceRef.current = source;
          source.buffer = audioBuffer;
          source.connect(context.destination);
          source.onended = () => {
            if (activeSourceRef.current === source) {
              activeSourceRef.current = null;
            }
            resolve();
          };
          source.start();
        });
      })
      .catch(() => {
        // Keep playback chain alive after decoding/playback errors.
      });
  }

  return (
    <div className="app-shell">
      <header className="hero">
        <div className="hero-top">
          <p className="eyebrow">Local Realtime Voice Chatbot</p>
        </div>
        <h1>{`SenseVoice + ${runtime.configured_model || "LLM"} + Piper`}</h1>
        <p className="status">{status}</p>
        <div className={`ptt ${isRecording ? "active" : ""}`}>
          <div className="ptt-button">
            <span>SPACE</span>
            <small>{isRecording ? "Release to send" : "Hold to talk / press again to interrupt"}</small>
          </div>
          <div className="ptt-status">
            <p><strong>Status:</strong> {runtime.models_loaded ? "Ready" : "Loading models..."}</p>
            <p><strong>Model:</strong> {runtime.configured_model || "unknown"}</p>
            <p><strong>EN Voice:</strong> {runtime.tts_default_voice_id || "unknown"}</p>
            <p><strong>ZH Voice:</strong> {runtime.tts_chinese_voice_id || "unknown"}</p>
            {runtime.tts_last_voice_id !== "unknown" && (
              <p><strong>Current:</strong> {runtime.tts_last_voice_id} ({runtime.tts_last_voice_reason})</p>
            )}
            {runtime.vram_used_mb !== null && (
              <p><strong>VRAM:</strong> {runtime.vram_used_mb} / {runtime.vram_total_mb} MB ({runtime.vram_percent}%)</p>
            )}
          </div>
        </div>
      </header>

      <main className="grid">
        <section className="card">
          <h2>You</h2>
          <p>{transcript || "Your transcript appears here."}</p>
        </section>

        <section className="card accent">
          <h2>Assistant</h2>
          <p>{assistantText || "Streaming response will appear here."}</p>
        </section>

        <section className="card history">
          <h2>Chat History</h2>
          {chatHistory.length === 0 ? (
            <p>No previous turns yet.</p>
          ) : (
            <div className="history-list">
              {chatHistory.map((turn, idx) => (
                <article key={turn.id || idx} className="history-turn">
                  <p className="history-you">You: {turn.userText || "..."}</p>
                  <p className="history-assistant">Assistant: {turn.assistantText || (turn.interrupted ? "[interrupted]" : "...")}</p>
                </article>
              ))}
            </div>
          )}
        </section>
      </main>

      <footer className="footer">WebSocket: {socketState}</footer>
    </div>
  );
}
