import { useState, useEffect, useRef, useCallback } from "react";

export interface MCPMessage {
  message_id: string;
  incident_id: string;
  sender: string;
  recipient: string;
  message_type: string;
  channel: string;
  payload: any;
  confidence: number;
  evidence: string[];
  parent_message_id: string | null;
  timestamp: string;
}

interface UseWebSocketReturn {
  messages: MCPMessage[];
  connected: boolean;
  send: (data: string) => void;
  clearMessages: () => void;
}

export function useWebSocket(url: string): UseWebSocketReturn {
  const [messages, setMessages] = useState<MCPMessage[]>([]);
  const [connected, setConnected] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pingIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const reconnectAttempts = useRef(0);
  const maxReconnectAttempts = 50;
  const mountedRef = useRef(true);

  const clearMessages = useCallback(() => {
    setMessages([]);
  }, []);

  const send = useCallback((data: string) => {
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
      wsRef.current.send(data);
    }
  }, []);

  const startPingInterval = useCallback(() => {
    if (pingIntervalRef.current) clearInterval(pingIntervalRef.current);
    pingIntervalRef.current = setInterval(() => {
      if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
        wsRef.current.send("ping");
      }
    }, 25000);
  }, []);

  const stopPingInterval = useCallback(() => {
    if (pingIntervalRef.current) {
      clearInterval(pingIntervalRef.current);
      pingIntervalRef.current = null;
    }
  }, []);

  const connect = useCallback(() => {
    if (!mountedRef.current) return;
    if (wsRef.current) {
      try { wsRef.current.close(); } catch { /* ignore */ }
    }

    try {
      const ws = new WebSocket(url);
      wsRef.current = ws;

      ws.onopen = () => {
        if (!mountedRef.current) return;
        setConnected(true);
        reconnectAttempts.current = 0;
        startPingInterval();
        console.log("[WS] Connected to " + url);
      };

      ws.onmessage = (event) => {
        if (!mountedRef.current) return;
        try {
          const data = event.data;

          // Ignore ping/pong
          if (data === "pong") return;

          const parsed = JSON.parse(data);

          // Ignore keepalive pings from server
          if (parsed.type === "ping") return;

          // Validate it looks like an MCP message
          if (parsed.sender && parsed.channel && parsed.message_type) {
            const msg: MCPMessage = {
              message_id: parsed.message_id || "",
              incident_id: parsed.incident_id || "",
              sender: parsed.sender || "unknown",
              recipient: parsed.recipient || "unknown",
              message_type: parsed.message_type || "status",
              channel: parsed.channel || "unknown",
              payload: parsed.payload || {},
              confidence: parsed.confidence || 0,
              evidence: parsed.evidence || [],
              parent_message_id: parsed.parent_message_id || null,
              timestamp: parsed.timestamp || new Date().toISOString(),
            };
            setMessages((prev) => [...prev, msg]);
          }
        } catch {
          // Non-JSON message — ignore
        }
      };

      ws.onclose = (event) => {
        if (!mountedRef.current) return;
        setConnected(false);
        stopPingInterval();
        console.log("[WS] Disconnected (code: " + event.code + ")");

        // Auto-reconnect with exponential backoff
        if (reconnectAttempts.current < maxReconnectAttempts) {
          const delay = Math.min(1000 * Math.pow(1.5, reconnectAttempts.current), 30000);
          reconnectAttempts.current += 1;
          console.log("[WS] Reconnecting in " + Math.round(delay) + "ms (attempt " + reconnectAttempts.current + ")");
          reconnectTimeoutRef.current = setTimeout(connect, delay);
        }
      };

      ws.onerror = () => {
        if (!mountedRef.current) return;
        setConnected(false);
      };
    } catch (err) {
      console.error("[WS] Connection error:", err);
      setConnected(false);
      if (reconnectAttempts.current < maxReconnectAttempts) {
        const delay = Math.min(2000 * Math.pow(1.5, reconnectAttempts.current), 30000);
        reconnectAttempts.current += 1;
        reconnectTimeoutRef.current = setTimeout(connect, delay);
      }
    }
  }, [url, startPingInterval, stopPingInterval]);

  useEffect(() => {
    mountedRef.current = true;
    connect();
    return () => {
      mountedRef.current = false;
      stopPingInterval();
      if (reconnectTimeoutRef.current) clearTimeout(reconnectTimeoutRef.current);
      if (wsRef.current) {
        try { wsRef.current.close(); } catch { /* ignore */ }
      }
    };
  }, [connect, stopPingInterval]);

  return { messages, connected, send, clearMessages };
}