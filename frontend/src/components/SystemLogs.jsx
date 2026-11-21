import React, { useState, useEffect, useRef, useCallback } from 'react';
import { Terminal, ArrowDown, Trash2 } from 'lucide-react';
import { logsApi } from '../api/client';
import { parseUtc, fmtTime } from './common';

export default function SystemLogs({ liveLogs, instances = [], timezone = 'wib' }) {
  const [dbLogs, setDbLogs] = useState([]);
  const [devices, setDevices] = useState([]);
  const [deviceFilter, setDeviceFilter] = useState('all'); // all | GLOBAL | <uuid>
  const [severityFilter, setSeverityFilter] = useState('ALL');
  const [autoScroll, setAutoScroll] = useState(true);
  const [clearedAt, setClearedAt] = useState(0);
  const consoleEndRef = useRef(null);

  const loadDevices = useCallback(async () => {
    try {
      const res = await logsApi.getDevices();
      setDevices(res.data?.items || []);
    } catch {
      setDevices(instances.map((i) => ({ uuid: i.uuid, name: i.name, is_deleted: false })));
    }
  }, [instances]);

  useEffect(() => {
    loadDevices();
  }, [loadDevices]);

  const fetchDbLogs = useCallback(async () => {
    try {
      const res = await logsApi.getSystemLogs(1, 100, severityFilter, deviceFilter);
      setDbLogs(res.data.items || []);
    } catch (err) {
      console.error('Fetch system logs error:', err);
    }
  }, [severityFilter, deviceFilter]);

  useEffect(() => {
    fetchDbLogs();
  }, [fetchDbLogs]);

  const allLogs = React.useMemo(() => {
    const combined = [...liveLogs, ...dbLogs];
    const seen = new Set();
    const unique = [];
    for (const item of combined) {
      const key = `${item.id}-${item.created_at}`;
      if (seen.has(key)) continue;
      seen.add(key);

      if (severityFilter !== 'ALL' && item.severity !== severityFilter) continue;
      if (deviceFilter === 'GLOBAL' && item.device_uuid) continue;
      if (deviceFilter !== 'all' && deviceFilter !== 'GLOBAL' && item.device_uuid !== deviceFilter) continue;

      const ts = parseUtc(item.created_at)?.getTime() || 0;
      if (clearedAt && ts <= clearedAt) continue;

      unique.push(item);
    }
    return unique.sort((a, b) => (parseUtc(a.created_at)?.getTime() || 0) - (parseUtc(b.created_at)?.getTime() || 0));
  }, [liveLogs, dbLogs, severityFilter, deviceFilter, clearedAt]);

  useEffect(() => {
    if (autoScroll && consoleEndRef.current) {
      consoleEndRef.current.scrollIntoView({ behavior: 'smooth' });
    }
  }, [allLogs, autoScroll]);

  const getSeverityStyle = (sev) => {
    switch (sev) {
      case 'INFO':
        return 'text-sky-400 bg-sky-950/40 border-sky-800/40';
      case 'WARNING':
        return 'text-amber-400 bg-amber-950/40 border-amber-800/40';
      case 'ERROR':
      case 'CRITICAL':
        return 'text-rose-400 bg-rose-950/40 border-rose-800/40 font-bold';
      default:
        return 'text-slate-400 bg-slate-800 border-slate-700';
    }
  };

  return (
    <div className="bg-slate-900 border border-slate-800 rounded-2xl p-5 shadow-sm space-y-3 flex flex-col h-[560px]">
      {/* Header Bar */}
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-2 border-b border-slate-800 pb-3">
        <div className="flex items-center gap-2">
          <Terminal className="w-5 h-5 text-emerald-400" />
          <h2 className="text-sm font-semibold text-white">Konsol Log Sistem &amp; Recovery</h2>
        </div>

        <div className="flex flex-wrap items-center gap-2 text-xs">
          {/* Device Filter */}
          <select
            value={deviceFilter}
            onChange={(e) => setDeviceFilter(e.target.value)}
            className="bg-slate-950 border border-slate-800 rounded-xl px-2.5 py-1 text-slate-300 focus:outline-none focus:border-emerald-500 text-[11px] max-w-[210px]"
          >
            <option value="all">Semua Log (Global + Device)</option>
            <option value="GLOBAL">GLOBAL (Sistem &amp; Server)</option>
            {devices.map((d) => (
              <option key={d.uuid} value={d.uuid}>
                {d.name}{d.is_deleted ? ' (Arsip)' : ''}
              </option>
            ))}
          </select>

          {/* Severity Filter */}
          <select
            value={severityFilter}
            onChange={(e) => setSeverityFilter(e.target.value)}
            className="bg-slate-950 border border-slate-800 rounded-xl px-2.5 py-1 text-slate-300 focus:outline-none focus:border-emerald-500 text-[11px]"
          >
            <option value="ALL">Semua Level</option>
            <option value="INFO">INFO</option>
            <option value="WARNING">WARNING</option>
            <option value="ERROR">ERROR</option>
            <option value="CRITICAL">CRITICAL</option>
          </select>

          {/* Auto Scroll Switch */}
          <button
            onClick={() => setAutoScroll(!autoScroll)}
            className={`px-2.5 py-1 rounded-lg border text-[11px] font-medium flex items-center gap-1 transition ${
              autoScroll
                ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/30'
                : 'bg-slate-800 text-slate-400 border-slate-700'
            }`}
          >
            <ArrowDown className="w-3 h-3" />
            Auto-Scroll
          </button>

          <button
            onClick={() => setClearedAt(Date.now())}
            className="px-2.5 py-1 rounded-lg border border-slate-700 bg-slate-800 hover:bg-slate-700 text-slate-300 text-[11px] font-medium flex items-center gap-1 transition"
            title="Bersihkan tampilan layar konsol (tidak menghapus database)"
          >
            <Trash2 className="w-3 h-3" />
            Bersihkan
          </button>
        </div>
      </div>

      {/* Terminal Output Area */}
      <div className="flex-1 bg-slate-950 rounded-xl p-4 border border-slate-800/90 font-mono text-[11px] overflow-y-auto space-y-2">
        {allLogs.length > 0 ? (
          allLogs.map((log, idx) => (
            <div key={`${log.id}-${idx}`} className="flex items-start gap-2 leading-relaxed hover:bg-slate-900/50 p-1 rounded transition">
              <span className="text-slate-500 select-none shrink-0 text-[10px]">
                {fmtTime(log.created_at, timezone)}
              </span>

              <span className={`px-1.5 py-0.2 rounded border text-[9px] uppercase tracking-wider shrink-0 font-semibold ${getSeverityStyle(log.severity)}`}>
                {log.severity}
              </span>

              <span
                className={`px-1.5 py-0.2 rounded border text-[9px] shrink-0 font-semibold ${
                  log.device_uuid
                    ? 'bg-indigo-950/50 text-indigo-300 border-indigo-700/40'
                    : 'bg-slate-800 text-slate-300 border-slate-700'
                }`}
              >
                {log.device_name || (log.device_uuid ? log.device_uuid.slice(0, 8) : 'GLOBAL')}
              </span>

              <span className="text-teal-400 shrink-0 font-semibold">
                [{log.category}]
              </span>

              <span className="text-slate-300 flex-1 break-all">
                {log.message}
                {log.last_screen_state && (
                  <span className="text-slate-500 ml-2 text-[10px]">
                    (state: {log.last_screen_state})
                  </span>
                )}
              </span>
            </div>
          ))
        ) : (
          <div className="h-full flex items-center justify-center text-slate-600">
            Menunggu aliran aktivitas log sistem...
          </div>
        )}
        <div ref={consoleEndRef} />
      </div>

      <div className="flex items-center justify-between text-[11px] text-slate-500 pt-1">
        <span>Aliran log otomatis tersinkronisasi via WebSocket</span>
        <span>Total baris tampil: {allLogs.length}</span>
      </div>
    </div>
  );
}
