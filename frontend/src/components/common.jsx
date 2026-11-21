import React from 'react';
import { CheckCircle2, AlertCircle, Info, X, Usb, Wifi, Monitor } from 'lucide-react';

export const ONLINE = ['IDLE', 'RUNNING', 'RECOVERING', 'STOPPING'];

export const statusStyle = (status) => {
  switch (status) {
    case 'RUNNING':
      return 'bg-emerald-500/15 text-emerald-400 border-emerald-500/40';
    case 'IDLE':
      return 'bg-sky-500/10 text-sky-400 border-sky-500/30';
    case 'RECOVERING':
    case 'REBOOTING':
      return 'bg-amber-500/15 text-amber-400 border-amber-500/40';
    case 'STOPPING':
      return 'bg-orange-500/15 text-orange-400 border-orange-500/40';
    case 'ERROR':
      return 'bg-rose-600/20 text-rose-300 border-rose-500/50';
    default:
      return 'bg-slate-800 text-slate-400 border-slate-700';
  }
};

export const statusDot = (status) => {
  switch (status) {
    case 'RUNNING':
      return 'bg-emerald-500 animate-pulse';
    case 'IDLE':
      return 'bg-sky-400';
    case 'RECOVERING':
    case 'REBOOTING':
      return 'bg-amber-400 animate-pulse';
    case 'STOPPING':
      return 'bg-orange-400 animate-pulse';
    case 'ERROR':
      return 'bg-rose-500';
    default:
      return 'bg-slate-600';
  }
};

export function StatusBadge({ status }) {
  return (
    <span
      className={`px-1.5 py-0.5 rounded border text-[9px] font-semibold uppercase tracking-wider ${statusStyle(status)}`}
    >
      {status || 'OFFLINE'}
    </span>
  );
}

export function ConnectionBadge({ type = 'tcp', compact = false }) {
  const t = String(type || 'tcp').toLowerCase();
  if (t === 'usb') {
    return (
      <span
        className="inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded border text-[9px] font-semibold uppercase tracking-wider bg-indigo-500/15 text-indigo-300 border-indigo-500/40 shrink-0"
        title="Koneksi Fisik via USB Debugging"
      >
        <Usb className="w-2.5 h-2.5" />
        {!compact && 'USB'}
      </span>
    );
  }
  if (t === 'wireless') {
    return (
      <span
        className="inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded border text-[9px] font-semibold uppercase tracking-wider bg-teal-500/15 text-teal-300 border-teal-500/40 shrink-0"
        title="Koneksi Android Wireless Debugging (Wi-Fi)"
      >
        <Wifi className="w-2.5 h-2.5" />
        {!compact && 'Wi-Fi'}
      </span>
    );
  }
  return (
    <span
      className="inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded border text-[9px] font-semibold uppercase tracking-wider bg-slate-800 text-slate-400 border-slate-700 shrink-0"
      title="Koneksi Emulator / TCP IP:Port"
    >
      <Monitor className="w-2.5 h-2.5" />
      {!compact && 'TCP'}
    </span>
  );
}

export function DeviceChip({ name, address, archived }) {
  return (
    <span
      className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded bg-slate-800 border border-slate-700 text-[10px] ${archived ? 'opacity-60' : ''}`}
    >
      <strong className="text-slate-200">{name}</strong>
      {address && <span className="text-slate-500 font-mono">{address}</span>}
      {archived && <span className="text-slate-500">(arsip)</span>}
    </span>
  );
}

export const fmtSec = (v) => (v === null || v === undefined || v === 0 ? '—' : `${v}s`);

export const fmtUptime = (seconds = 0) => {
  const s = Math.max(0, Math.floor(seconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  if (h > 0) return `${h}h ${m}m`;
  if (m > 0) return `${m}m ${sec}s`;
  return `${sec}s`;
};

export const TIMEZONES = {
  wib: {
    key: 'wib',
    short: 'WIB',
    label: 'WIB (UTC+7)',
    full: 'WIB (UTC+7 — Indonesia)',
    iana: 'Asia/Jakarta',
  },
  ksa: {
    key: 'ksa',
    short: 'KSA',
    label: 'Arab Saudi (UTC+3)',
    full: 'Arab Saudi (UTC+3 — Riyadh)',
    iana: 'Asia/Riyadh',
  },
};

export const getTzMeta = (tz = 'wib') => TIMEZONES[tz === 'ksa' ? 'ksa' : 'wib'];

// Backend timestamps are UTC+0 (naive ISO without 'Z' or aware ISO).
export const parseUtc = (iso) => {
  if (!iso) return null;
  if (typeof iso === 'number') return new Date(iso > 1e12 ? iso : iso * 1000);
  const s = String(iso);
  return new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(s) ? s : `${s}Z`);
};

export const fmtDateTime = (iso, tz = 'wib') => {
  const d = parseUtc(iso);
  if (!d || Number.isNaN(d.getTime())) return '—';
  const meta = getTzMeta(tz);
  try {
    return new Intl.DateTimeFormat('id-ID', {
      timeZone: meta.iana,
      day: '2-digit',
      month: '2-digit',
      year: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      hour12: false,
    }).format(d);
  } catch {
    return d.toLocaleString();
  }
};

export const fmtTime = (iso, tz = 'wib') => {
  const d = parseUtc(iso);
  if (!d || Number.isNaN(d.getTime())) return '—';
  const meta = getTzMeta(tz);
  try {
    return new Intl.DateTimeFormat('id-ID', {
      timeZone: meta.iana,
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      hour12: false,
    }).format(d);
  } catch {
    return d.toLocaleTimeString();
  }
};

export const fmtCountdown = (ms = 0) => {
  const totalSec = Math.max(0, Math.floor(ms / 1000));
  const h = Math.floor(totalSec / 3600);
  const m = Math.floor((totalSec % 3600) / 60);
  return `${h}j ${String(m).padStart(2, '0')}m`;
};

export function Toasts({ toasts, onDismiss }) {
  return (
    <div className="fixed bottom-4 right-4 z-[70] space-y-2 w-80 max-h-[70vh] overflow-y-auto">
      {toasts.map((t) => {
        const cls =
          t.type === 'success'
            ? 'bg-emerald-950/95 border-emerald-700/60 text-emerald-200'
            : t.type === 'error'
              ? 'bg-rose-950/95 border-rose-700/60 text-rose-200'
              : t.type === 'warning'
                ? 'bg-amber-950/95 border-amber-700/60 text-amber-200'
                : 'bg-slate-900/95 border-sky-700/60 text-sky-200';
        const Icon =
          t.type === 'success' ? CheckCircle2 : t.type === 'info' ? Info : AlertCircle;
        return (
          <div
            key={t.id}
            className={`border rounded-xl p-3 text-xs shadow-xl flex items-start gap-2 ${cls}`}
          >
            <Icon className="w-4 h-4 shrink-0 mt-0.5" />
            <div className="flex-1 min-w-0 break-words space-y-1">
              {t.title && <div className="font-semibold">{t.title}</div>}
              <div>{t.message}</div>
              {t.lines?.length > 0 && (
                <ul className="list-disc pl-4 text-[11px] opacity-90 max-h-24 overflow-y-auto">
                  {t.lines.map((l, i) => (
                    <li key={i}>{l}</li>
                  ))}
                </ul>
              )}
            </div>
            <button onClick={() => onDismiss(t.id)} className="opacity-60 hover:opacity-100">
              <X className="w-3.5 h-3.5" />
            </button>
          </div>
        );
      })}
    </div>
  );
}

export function ConfirmModal({ title, children, confirmLabel = 'Ya, Lanjutkan', danger, onConfirm, onCancel, extra }) {
  return (
    <div className="fixed inset-0 z-[60] bg-slate-950/75 backdrop-blur-sm flex items-center justify-center p-4">
      <div className="bg-slate-900 border border-slate-800 rounded-2xl max-w-md w-full p-5 shadow-2xl space-y-4 max-h-[90vh] overflow-y-auto">
        <h3 className="text-sm font-semibold text-white">{title}</h3>
        <div className="text-xs text-slate-300 space-y-2">{children}</div>
        {extra}
        <div className="flex justify-end gap-2 text-xs">
          <button
            onClick={onCancel}
            className="px-4 py-2 rounded-xl text-slate-400 hover:text-white hover:bg-slate-800"
          >
            Batal
          </button>
          <button
            onClick={onConfirm}
            className={`px-4 py-2 rounded-xl text-white font-semibold ${danger ? 'bg-rose-600 hover:bg-rose-500' : 'bg-emerald-600 hover:bg-emerald-500'}`}
          >
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
