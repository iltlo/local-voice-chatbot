/**
 * Displays LLM/TTS runtime status (model, voices, VRAM).
 *
 * @param {{ runtime: object }} props
 */
export default function StatusPanel({ runtime }) {
  return (
    <div className="ptt-status">
      <p><strong>Status:</strong> {runtime.models_loaded ? "Ready" : "Loading models..."}</p>
      <p><strong>Model:</strong> {runtime.configured_model || "unknown"}</p>
      <p><strong>EN Voice:</strong> {runtime.tts_default_voice_id || "unknown"}</p>
      <p><strong>ZH Voice:</strong> {runtime.tts_chinese_voice_id || "unknown"}</p>
      <p><strong>YUE Voice:</strong> {runtime.tts_cantonese_voice_id || "browser fallback"}</p>
      {runtime.tts_last_voice_id !== "unknown" && (
        <p>
          <strong>Current:</strong> {runtime.tts_last_voice_id} ({runtime.tts_last_voice_reason})
        </p>
      )}
      {runtime.vram_used_mb !== null && (
        <p>
          <strong>VRAM:</strong> {runtime.vram_used_mb} / {runtime.vram_total_mb} MB ({runtime.vram_percent}%)
        </p>
      )}
    </div>
  );
}
