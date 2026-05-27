import { useCallback, useRef, useState } from "react";

function bytesToBase64(bytes) {
  let binary = "";
  const chunk = 0x8000;
  for (let i = 0; i < bytes.length; i += chunk) {
    binary += String.fromCharCode(...bytes.subarray(i, i + chunk));
  }
  return btoa(binary);
}

/**
 * Manages microphone recording via MediaRecorder.
 *
 * The `onReady` callback is stored in a ref so it always has the latest
 * version without restarting effects.
 *
 * @param {object} opts
 * @param {function} opts.onReady - Called with `{ base64, mimeType }` when recording stops.
 * @param {boolean} opts.enabled  - When false, `startRecording` is a no-op.
 * @returns {{ isRecording: boolean, startRecording: function, stopRecording: function }}
 */
export function useRecorder({ onReady, enabled }) {
  const [isRecording, setIsRecording] = useState(false);
  const mediaStreamRef = useRef(null);
  const mediaRecorderRef = useRef(null);
  const chunksRef = useRef([]);
  const onReadyRef = useRef(onReady);
  onReadyRef.current = onReady;

  const ensureMicStream = useCallback(async () => {
    if (mediaStreamRef.current) {
      return mediaStreamRef.current;
    }
    mediaStreamRef.current = await navigator.mediaDevices.getUserMedia({ audio: true });
    return mediaStreamRef.current;
  }, []);

  const startRecording = useCallback(
    async () => {
      if (!enabled || isRecording) {
        return;
      }
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
    },
    [enabled, isRecording, ensureMicStream],
  );

  const stopRecording = useCallback(async () => {
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
    onReadyRef.current({ base64, mimeType: "audio/webm" });
    setIsRecording(false);
  }, [isRecording]);

  return { isRecording, startRecording, stopRecording };
}
