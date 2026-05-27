import { useCallback, useEffect, useRef, useState } from "react";

/**
 * Manages a WebSocket connection lifecycle.
 *
 * The `onMessage` callback is stored in a ref so the latest version is always
 * called without reconnecting the socket when the handler changes.
 *
 * @param {string} url - WebSocket URL to connect to.
 * @param {function} onMessage - Async function called with each parsed message object.
 * @returns {{ socketState: string, send: function }}
 */
export function useWebSocket(url, onMessage) {
  const [socketState, setSocketState] = useState("connecting");
  const wsRef = useRef(null);
  const onMessageRef = useRef(onMessage);
  onMessageRef.current = onMessage;

  useEffect(() => {
    const ws = new WebSocket(url);
    wsRef.current = ws;

    ws.onopen = () => setSocketState("connected");
    ws.onclose = () => setSocketState("disconnected");
    ws.onerror = () => setSocketState("error");
    ws.onmessage = async (event) => {
      const msg = JSON.parse(event.data);
      await onMessageRef.current(msg);
    };

    return () => ws.close();
  }, [url]);

  const send = useCallback((data) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(typeof data === "string" ? data : JSON.stringify(data));
    }
  }, []);

  return { socketState, send };
}
