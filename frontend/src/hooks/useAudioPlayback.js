import { useCallback, useRef, useState } from "react";

function base64ToArrayBuffer(base64) {
  const binary = atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) {
    bytes[i] = binary.charCodeAt(i);
  }
  return bytes.buffer;
}

/**
 * Manages Web Audio API sequential playback with generation-based cancellation.
 *
 * @returns {{
 *   ttsStreamActive: boolean,
 *   setTtsStreamActive: function,
 *   ensureAudioContext: function,
 *   enqueueChunk: function,
 *   stopAll: function,
 *   hasPendingPlayback: function,
 * }}
 */
export function useAudioPlayback() {
  const audioContextRef = useRef(null);
  const playbackChainRef = useRef(Promise.resolve());
  const playbackGenerationRef = useRef(0);
  const playbackPendingRef = useRef(0);
  const activeSourceRef = useRef(null);
  const [ttsStreamActive, setTtsStreamActive] = useState(false);

  const ensureAudioContext = useCallback(async () => {
    if (!audioContextRef.current) {
      audioContextRef.current = new AudioContext();
    }
    if (audioContextRef.current.state === "suspended") {
      await audioContextRef.current.resume();
    }
  }, []);

  const stopAll = useCallback(() => {
    playbackGenerationRef.current += 1;
    playbackPendingRef.current = 0;
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
    setTtsStreamActive(false);
  }, []);

  const enqueueChunk = useCallback(
    async (base64) => {
      await ensureAudioContext();
      const generation = playbackGenerationRef.current;
      playbackPendingRef.current += 1;

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
        })
        .finally(() => {
          playbackPendingRef.current = Math.max(0, playbackPendingRef.current - 1);
        });
    },
    [ensureAudioContext],
  );

  const hasPendingPlayback = useCallback(
    () => playbackPendingRef.current > 0 || !!activeSourceRef.current,
    [],
  );

  return { ttsStreamActive, setTtsStreamActive, ensureAudioContext, enqueueChunk, stopAll, hasPendingPlayback };
}
