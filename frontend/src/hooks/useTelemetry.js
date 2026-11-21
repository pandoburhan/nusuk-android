import { useState, useEffect, useRef, useCallback } from 'react';
import { instancesApi } from '../api/client';

export const ONLINE_STATUSES = ['IDLE', 'RUNNING', 'RECOVERING', 'STOPPING'];

let alertSeq = 0;
let redroidSeq = 0;

/**
 * Multi-device telemetry. Everything is keyed by device_uuid.
 * Exposes `instances` (array, ordered as server list), helper `patchInstance`
 * for optimistic local updates, and event streams.
 */
export function useTelemetry(authToken, onUnauthorized) {
  const [isConnected, setIsConnected] = useState(false);
  const [instances, setInstances] = useState([]);
  const [liveLogs, setLiveLogs] = useState([]);
  const [latestAlert, setLatestAlert] = useState(null);
  const [lastQuizEvent, setLastQuizEvent] = useState(null);
  const [lastKeyEvent, setLastKeyEvent] = useState(null);
  const [lastRedroidEvent, setLastRedroidEvent] = useState(null);

  const wsRef = useRef(null);
  const reconnectTimeoutRef = useRef(null);
  const closedRef = useRef(false);

  const patchInstance = useCallback((uuid, patch) => {
    setInstances((prev) => prev.map((i) => (i.uuid === uuid ? { ...i, ...patch } : i)));
  }, []);

  const refreshInstances = useCallback(async () => {
    if (!authToken) return;
    try {
      const res = await instancesApi.list();
      setInstances(res.data.items || []);
    } catch (err) {
      console.error('[Telemetry] list instances failed', err);
    }
  }, [authToken]);

  const connect = useCallback(() => {
    if (closedRef.current || !authToken) return;
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.host}/ws/telemetry?token=${encodeURIComponent(authToken)}`;

    const ws = new WebSocket(wsUrl);
    wsRef.current = ws;

    ws.onopen = () => setIsConnected(true);

    ws.onmessage = (event) => {
      try {
        const { type, data } = JSON.parse(event.data);
        if (!data) return;
        switch (type) {
          case 'state_sync':
          case 'device_list_update':
            setInstances(data.instances || []);
            break;

          case 'status_update': {
            const { device_uuid, ...rest } = data;
            patchInstance(device_uuid, rest);
            break;
          }

          case 'app_keys_update':
            patchInstance(data.device_uuid, { current_app_keys: data.current_app_keys });
            break;

          case 'preview_setting_update':
            patchInstance(data.device_uuid, { live_preview_enabled: data.live_preview_enabled });
            break;

          case 'log_event':
            setLiveLogs((prev) => [data, ...prev.slice(0, 299)]);
            break;

          case 'alert_event':
            alertSeq += 1;
            setLatestAlert({ ...data, _seq: alertSeq });
            break;

          case 'quiz_event':
            setLastQuizEvent({ ...data, _t: Date.now() });
            break;

          case 'key_event':
            setLastKeyEvent({ ...data, _t: Date.now() });
            break;

          case 'redroid_provisioning_event':
            redroidSeq += 1;
            setLastRedroidEvent({ ...data, _seq: redroidSeq });
            if (data.status === 'ready' || data.stage === 'ready') {
              refreshInstances();
            }
            break;

          default:
            break;
        }
      } catch (err) {
        console.error('[Telemetry] Error parsing message:', err);
      }
    };

    ws.onclose = (event) => {
      setIsConnected(false);
      if (event?.code === 4401) {
        closedRef.current = true;
        onUnauthorized?.();
        return;
      }
      if (closedRef.current) return;
      reconnectTimeoutRef.current = setTimeout(connect, 3000);
    };

    ws.onerror = () => {
      ws.close();
    };
  }, [authToken, patchInstance, refreshInstances, onUnauthorized]);

  useEffect(() => {
    if (!authToken) {
      closedRef.current = true;
      setIsConnected(false);
      setInstances([]);
      if (reconnectTimeoutRef.current) clearTimeout(reconnectTimeoutRef.current);
      if (wsRef.current) {
        wsRef.current.close();
        wsRef.current = null;
      }
      return undefined;
    }

    closedRef.current = false;
    refreshInstances();
    connect();
    return () => {
      closedRef.current = true;
      if (reconnectTimeoutRef.current) clearTimeout(reconnectTimeoutRef.current);
      if (wsRef.current) wsRef.current.close();
    };
  }, [authToken, connect, refreshInstances]);

  const clearAlert = useCallback(() => setLatestAlert(null), []);
  const clearRedroidEvent = useCallback(() => setLastRedroidEvent(null), []);

  return {
    isConnected,
    instances,
    patchInstance,
    refreshInstances,
    liveLogs,
    latestAlert,
    clearAlert,
    lastQuizEvent,
    lastKeyEvent,
    lastRedroidEvent,
    clearRedroidEvent,
  };
}
