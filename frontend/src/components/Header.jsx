import React, { useState, useEffect } from 'react';
import { Activity, Database, Radio, Settings as SettingsIcon, RefreshCw, Server, Globe, Clock } from 'lucide-react';
import { controlApi, errorDetail } from '../api/client';
import { TIMEZONES, fmtTime, fmtCountdown } from './common';

export default function Header({
  statusData,
  instances,
  isConnected,
  onOpenSettings,
  toast,
  timezone = 'wib',
  onChangeTimezone,
  serverTimeAnchor,
}) {
  const [reconnecting, setReconnecting] = useState(false);
  const [nowMs, setNowMs] = useState(() => Date.now());

  useEffect(() => {
    const tick = () => {
      if (serverTimeAnchor?.serverUnixMs && serverTimeAnchor?.clientPerfMs != null) {
        const elapsed = performance.now() - serverTimeAnchor.clientPerfMs;
        setNowMs(serverTimeAnchor.serverUnixMs + elapsed);
      } else {
        setNowMs(Date.now());
      }
    };
    tick();
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, [serverTimeAnchor]);

  const count = (s) => instances.filter((i) => i.status === s).length;
  const running = count('RUNNING');
  const idle = count('IDLE');
  const recovering = count('RECOVERING') + count('REBOOTING');
  const offline = count('OFFLINE');
  const err = count('ERROR');
  const online = instances.filter((i) => i.status && i.status !== 'OFFLINE' && i.status !== 'ERROR').length;

  const handleReconnectAll = async () => {
    try {
      setReconnecting(true);
      const res = await controlApi.reconnectAll();
      toast({ type: 'success', message: res.data?.message || 'Reconnect ADB dikirim ke semua device' });
    } catch (e) {
      toast({ type: 'error', message: errorDetail(e, 'Gagal reconnect ADB') });
    } finally {
      setReconnecting(false);
    }
  };

  const dbOk = statusData ? statusData.database_connected !== false : true;
  const otherTz = timezone === 'wib' ? 'ksa' : 'wib';
  const nextResetMs = serverTimeAnchor?.nextKsaResetUnixMs;
  const resetRemain = nextResetMs ? Math.max(0, nextResetMs - nowMs) : null;
  const isNetSynced = Boolean(serverTimeAnchor?.synced);

  return (
    <header className="bg-slate-900/90 backdrop-blur border-b border-slate-800 sticky top-0 z-40 px-6 py-3.5">
      <div className="max-w-[1600px] mx-auto flex flex-col md:flex-row items-center justify-between gap-4">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-xl bg-gradient-to-tr from-emerald-600 to-teal-500 flex items-center justify-center shadow-lg shadow-emerald-500/20">
            <Activity className="w-5 h-5 text-white" />
          </div>
          <div>
            <h1 className="text-lg font-bold tracking-tight text-white flex items-center gap-2">
              Nusuk Automation Hub
              <span className="text-xs font-medium px-2 py-0.5 rounded-full bg-emerald-950 text-emerald-400 border border-emerald-800">
                v2.0 Multi-Instance
              </span>
            </h1>
            <p className="text-xs text-slate-400">AI Challenge Solver &amp; Distributed Key Vault</p>
          </div>
        </div>

        <div className="flex flex-wrap items-center justify-center gap-2.5 text-xs">
          {/* Timezone Selector + Internet-Synced Live Clock */}
          <div
            className="flex items-center gap-2 px-2.5 py-1 rounded-lg bg-slate-800/90 border border-emerald-700/40 text-slate-200"
            title={
              `Sumber waktu: ${isNetSynced ? `Internet (${serverTimeAnchor?.source})` : 'Fallback lokal'}\n` +
              `${TIMEZONES[otherTz].label}: ${fmtTime(nowMs, otherTz)}\n` +
              (resetRemain != null ? `Reset limit 00:00 Arab Saudi dalam: ${fmtCountdown(resetRemain)}` : '')
            }
          >
            <Globe className="w-3.5 h-3.5 text-emerald-400 shrink-0" />
            <select
              value={timezone}
              onChange={(e) => onChangeTimezone?.(e.target.value)}
              aria-label="Pilih Zona Waktu"
              className="bg-slate-900 border border-slate-700 rounded-md px-2 py-0.5 text-[11px] font-medium text-white focus:outline-none focus:border-emerald-500 cursor-pointer"
            >
              <option value="wib">WIB (UTC+7 • Indonesia)</option>
              <option value="ksa">Arab Saudi (UTC+3 • Riyadh)</option>
            </select>
            <span className="flex items-center gap-1 font-mono text-[11px] text-emerald-300">
              <Clock className="w-3 h-3 text-slate-400" />
              {fmtTime(nowMs, timezone)}
            </span>
            <span
              className={`w-1.5 h-1.5 rounded-full shrink-0 ${isNetSynced ? 'bg-emerald-400' : 'bg-amber-400'}`}
              title={isNetSynced ? `Tersinkronisasi internet (${serverTimeAnchor?.source})` : 'Menggunakan waktu lokal'}
            />
            {resetRemain != null && (
              <span className="hidden xl:inline text-[10px] text-slate-400 border-l border-slate-700 pl-2">
                Reset KSA: <strong className="text-slate-200 font-mono">{fmtCountdown(resetRemain)}</strong>
              </span>
            )}
          </div>

          <div className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-slate-800/80 border border-slate-700/60 text-slate-300">
            <Database className="w-3.5 h-3.5 text-emerald-400" />
            <span>
              MySQL: <strong className="text-slate-100">nusuk_key</strong>
            </span>
            <span className={`w-2 h-2 rounded-full ${dbOk ? 'bg-emerald-500' : 'bg-rose-500'}`} />
          </div>

          {/* Cluster pill */}
          <div className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-slate-800/80 border border-slate-700/60 text-slate-300">
            <Server className="w-3.5 h-3.5 text-sky-400" />
            <span className="font-medium text-slate-100">
              {online}/{instances.length} Online
            </span>
            <span className="text-slate-500">|</span>
            <span className="text-emerald-400">{running} Running</span>
            <span className="text-slate-600">•</span>
            <span className="text-sky-400">{idle} Idle</span>
            {recovering > 0 && (
              <>
                <span className="text-slate-600">•</span>
                <span className="text-amber-400">{recovering} Recovering</span>
              </>
            )}
            {err > 0 && (
              <>
                <span className="text-slate-600">•</span>
                <span className="text-rose-400">{err} Error</span>
              </>
            )}
            <span className="text-slate-600">•</span>
            <span className="text-slate-400">{offline} Offline</span>
            <button
              onClick={handleReconnectAll}
              disabled={reconnecting}
              title="Reconnect ADB semua device"
              className="ml-1 text-[10px] px-1.5 py-0.5 bg-sky-600 hover:bg-sky-500 text-white rounded transition flex items-center gap-1"
            >
              <RefreshCw className={`w-2.5 h-2.5 ${reconnecting ? 'animate-spin' : ''}`} />
              Reconnect
            </button>
          </div>

          <div className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-slate-800/80 border border-slate-700/60 text-slate-300">
            <Radio className="w-3.5 h-3.5 text-teal-400" />
            <span>WebSocket:</span>
            <span
              className={`w-2 h-2 rounded-full ${isConnected ? 'bg-emerald-500' : 'bg-amber-500 animate-pulse'}`}
            />
            <span className="text-[11px] font-medium text-slate-400">
              {isConnected ? 'Live' : 'Connecting'}
            </span>
          </div>

          <button
            onClick={onOpenSettings}
            className="p-1.5 text-slate-400 hover:text-white bg-slate-800 hover:bg-slate-700 rounded-lg border border-slate-700 transition"
            title="System Settings"
          >
            <SettingsIcon className="w-4 h-4" />
          </button>
        </div>
      </div>
    </header>
  );
}
