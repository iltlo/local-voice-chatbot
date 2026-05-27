const EMOTION_EMOJI = {
  happy: "😊",
  sad: "😢",
  angry: "😠",
  fear: "😨",
  surprised: "😮",
  disgust: "🤢",
  neutral: "😐",
};
const FALLBACK_EMOJI = "🙂";

/**
 * Shows the live transcript ("You") and streaming assistant response cards.
 *
 * @param {{
 *   transcript: string,
 *   transcriptRaw: string,
 *   assistantText: string,
 *   userEmotion: string,
 * }} props
 */
export default function TranscriptCards({ transcript, transcriptRaw, assistantText, userEmotion }) {
  return (
    <>
      <section className="card">
        <h2>
          You
          <span className="emotion-emoji" title={`Detected emotion: ${userEmotion || "unknown"}`}>
            {EMOTION_EMOJI[userEmotion] || FALLBACK_EMOJI}
          </span>
        </h2>
        <p>{transcript || "Your transcript appears here."}</p>
        {transcriptRaw ? <p className="sense-raw">SenseVoice raw: {transcriptRaw}</p> : null}
      </section>

      <section className="card accent">
        <h2>Assistant</h2>
        <p>{assistantText || "Streaming response will appear here."}</p>
      </section>
    </>
  );
}
