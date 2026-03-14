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
  timestamp: string;
}

interface PollResponse {
  count: number;
  total: number;
  since: number;
  messages: MCPMessage[];
}

export function usePolling(backendUrl: string, intervalMs: number = 2000) {
  const [messages, setMessages] = useState<MCPMessage[]>([]);
  const [connected, setConnected] = useState(false);
  const sinceRef = useRef(0);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const poll = useCallback(async () => {
    try {
      const resp = await fetch(
        backendUrl + "/messages?since=" + sinceRef.current
      );
      if (resp.ok) {
        const data: PollResponse = await resp.json();
        if (data.messages && data.messages.length > 0) {
          setMessages((prev) => [...prev, ...data.messages]);
          sinceRef.current = data.total;
        }
        setConnected(true);
      } else {
        setConnected(false);
      }
    } catch (e) {
      setConnected(false);
    }
  }, [backendUrl]);

  useEffect(() => {
    poll();
    intervalRef.current = setInterval(poll, intervalMs);
    return () => {
      if (intervalRef.current) {
        clearInterval(intervalRef.current);
      }
    };
  }, [poll, intervalMs]);

  const clearMessages = useCallback(() => {
    setMessages([]);
    sinceRef.current = 0;
  }, []);

  const resetToLatest = useCallback(async () => {
    try {
      const resp = await fetch(backendUrl + "/messages?since=0");
      if (resp.ok) {
        const data: PollResponse = await resp.json();
        setMessages([]);
        sinceRef.current = data.total || 0;
      } else {
        setMessages([]);
        sinceRef.current = 0;
      }
    } catch {
      setMessages([]);
      sinceRef.current = 0;
    }
  }, [backendUrl]);

  return { messages, connected, clearMessages, resetToLatest };
}