import React, { useState, useEffect, useRef } from 'react';
import {
  Smartphone, Settings as Gear, Eye, EyeOff, RefreshCw, Power, Plug, Pencil, Trash2,
  WifiOff, Loader2, RotateCcw, Plus, X, Maximize2,
} from 'lucide-react';
import { instancesApi, errorDetail } from '../api/client';
import { ONLINE, StatusBadge, ConnectionBadge, statusDot, fmtUptime, ConfirmModal } from './common';

const MODE_SHORT = { answer_only: 'Answer', redeem_only: 'Redeem', auto: 'Auto' };

function PreviewArea({ inst, intervalSec, patchInstance, onEnable, onExpand }) {
  const online = ONLINE.includes(inst.status);
  const enabled = online && inst.live_preview_enabled;
  const [src, setSrc] = useState('');
  const [error, setError] = useState(null);
  const urlRef = useRef('');

  useEffect(() => {
    if (!enabled) return undefined;
    let cancelled = false;
    let timer = null;
    const controller = new AbortController();

    const tick = async () => {
      try {
        const res = await instancesApi.screenshot(inst.uuid, controller.signal);
        if (cancelled) return;
        const url = URL.createObjectURL(res.data);
        if (urlRef.current) URL.revokeObjectURL(urlRef.current);
        urlRef.current = url;
        setSrc(url);
        setError(null);
      } catch (err) {
        if (cancelled || err?.code === 'ERR_CANCELED') return;
        const status = err?.response?.status;
        if (status === 409) {
          // preview_disabled on backend: sync local state and stop polling
          patchInstance(inst.uuid, { live_preview_enabled: false });
          return;
        }
        setError(status === 503 ? 'unavailable' : 'failed');
      }
      if (!cancelled) timer = setTimeout(tick, Math.max(1, intervalSec) * 1000);
    };
    tick();

    return () => {
      cancelled = true;
      controller.abort();
      if (timer) clearTimeout(timer);
    };
  }, [enabled, inst.uuid, intervalSec, patchInstance]);

  useEffect(
    () => () => {
      if (urlRef.current) URL.revokeObjectURL(urlRef.current);
    },
    [],
  );

  let overlay = null;
  if (inst.status === 'REBOOTING') {
    overlay = (
      <div className="flex flex-col items-center gap-1.5 text-amber-400">
        <Loader2 className="w-6 h-6 animate-spin" />
        <span className="text-[11px] font-medium">Device sedang reboot…</span>
        <span className="text-[10px] text-slate-500">Menunggu emulator siap kembali</span>
      </div>
    );
  } else if (!online) {
    overlay = (
      <div className="flex flex-col items-center gap-1.5 text-slate-500">
        <WifiOff className="w-6 h-6" />
        <span className="text-[11px] font-medium">Device Offline</span>
        <span className="text-[10px]">Menunggu koneksi ADB</span>
      </div>
    );
  } else if (!inst.live_preview_enabled) {
    overlay = (
      <div className="flex flex-col items-center gap-2 text-center px-3">
        <EyeOff className="w-6 h-6 text-slate-500" />
        <span className="text-[11px] font-medium text-slate-300">
          Live Preview Nonaktif (Mode Hemat Resource)
        </span>
        <button
          onClick={onEnable}
          className="px-3 py-1 bg-emerald-600 hover:bg-emerald-500 text-white text-[11px] rounded-lg font-medium"
        >
          Aktifkan Preview
        </button>
      </div>
    );
  } else if (!src || error) {
    overlay = (
      <div className="flex flex-col items-center gap-1.5 text-slate-500">
        {error ? <WifiOff className="w-5 h-5" /> : <Loader2 className="w-5 h-5 animate-spin" />}
        <span className="text-[11px]">{error ? 'Layar belum tersedia…' : 'Memuat layar…'}</span>
      </div>
    );
  }

  return (
    <div className="relative h-60 bg-slate-950/80 rounded-lg border border-slate-800/80 flex items-center justify-center overflow-hidden group">
      {enabled && src && !error && (
        <img
          src={src}
          alt={inst.name}
          className="max-h-full max-w-full w-auto h-auto object-contain cursor-pointer transition hover:scale-[1.01]"
          onClick={onExpand}
          title="Klik untuk melihat layar penuh"
        />
      )}
      {overlay && (
        <div className={`absolute inset-0 flex items-center justify-center ${enabled && src ? 'bg-slate-950/70' : ''}`}>
          {overlay}
        </div>
      )}
      {/* Floating expand button */}
      {online && (
        <button
          onClick={onExpand}
          className="absolute top-2 right-2 p-1.5 bg-slate-950/80 hover:bg-slate-900 text-slate-400 hover:text-white rounded-lg border border-slate-700/70 shadow-md transition opacity-80 group-hover:opacity-100 z-10"
          title="Buka Layar Penuh (Expand Modal)"
        >
          <Maximize2 className="w-3.5 h-3.5" />
        </button>
      )}
    </div>
  );
}

function FullscreenPreviewModal({ inst, intervalSec, patchInstance, onClose, toast }) {
  const online = ONLINE.includes(inst.status);
  const enabled = online && inst.live_preview_enabled;
  const [src, setSrc] = useState('');
  const [error, setError] = useState(null);
  const [refreshing, setRefreshing] = useState(false);
  const urlRef = useRef('');

  // Handle ESC key press
  useEffect(() => {
    const handleKeyDown = (e) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [onClose]);

  const fetchScreenshot = async (signal) => {
    if (!enabled) return;
    try {
      const res = await instancesApi.screenshot(inst.uuid, signal);
      const url = URL.createObjectURL(res.data);
      if (urlRef.current) URL.revokeObjectURL(urlRef.current);
      urlRef.current = url;
      setSrc(url);
      setError(null);
    } catch (err) {
      if (err?.code === 'ERR_CANCELED') return;
      const status = err?.response?.status;
      if (status === 409) {
        patchInstance(inst.uuid, { live_preview_enabled: false });
        return;
      }
      setError(status === 503 ? 'unavailable' : 'failed');
    }
  };

  useEffect(() => {
    if (!enabled) return undefined;
    let cancelled = false;
    let timer = null;
    const controller = new AbortController();

    const tick = async () => {
      await fetchScreenshot(controller.signal);
      if (!cancelled) {
        timer = setTimeout(tick, Math.max(1, intervalSec || 4) * 1000);
      }
    };
    tick();

    return () => {
      cancelled = true;
      controller.abort();
      if (timer) clearTimeout(timer);
    };
  }, [enabled, inst.uuid, intervalSec, patchInstance]);

  useEffect(
    () => () => {
      if (urlRef.current) URL.revokeObjectURL(urlRef.current);
    },
    [],
  );

  const handleManualRefresh = async () => {
    if (!enabled) return;
    setRefreshing(true);
    await fetchScreenshot();
    setRefreshing(false);
  };

  const togglePreview = async (value) => {
    const prev = inst.live_preview_enabled;
    patchInstance(inst.uuid, { live_preview_enabled: value });
    try {
      await instancesApi.update(inst.uuid, { live_preview_enabled: value });
    } catch (e) {
      patchInstance(inst.uuid, { live_preview_enabled: prev });
      toast({ type: 'error', title: inst.name, message: errorDetail(e, 'Gagal mengubah preview') });
    }
  };

  const progCount = inst.session_completed_count ?? 0;
  const progLimitStr = inst.stop_mode === 'until_exhausted' ? '∞' : (inst.stop_limit || 100);
  const progUnit = inst.mode === 'redeem_only' ? 'Key' : 'Soal';

  return (
    <div
      className="fixed inset-0 z-50 bg-slate-950/80 backdrop-blur-md flex flex-col p-3 md:p-6 animate-fadeIn"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      {/* Top Header / Telemetry Bar */}
      <div className="bg-slate-900/95 border border-slate-800 rounded-2xl p-3 md:px-5 md:py-3.5 shadow-2xl flex flex-wrap items-center justify-between gap-3 mb-3 shrink-0">
        <div className="flex items-center gap-3 min-w-0">
          <span className={`w-2.5 h-2.5 rounded-full shrink-0 ${statusDot(inst.status)}`} />
          <div className="min-w-0">
            <div className="text-sm md:text-base font-bold text-white flex items-center gap-2 truncate">
              <span>{inst.name}</span>
              <ConnectionBadge type={inst.connection_type} />
              <StatusBadge status={inst.status} />
              {inst.mode && (
                <span className="text-[10px] px-2 py-0.5 rounded-md bg-sky-950 text-sky-300 border border-sky-800 font-mono">
                  {MODE_SHORT[inst.mode] || inst.mode}
                </span>
              )}
            </div>
            <div className="text-[11px] text-slate-400 font-mono truncate flex items-center gap-2 mt-0.5">
              <span>{inst.address}</span>
              <span>•</span>
              <span className="text-emerald-400 font-semibold">Keys: {online ? (inst.current_app_keys ?? '—') : '—'}</span>
              <span>•</span>
              <span className="text-sky-400">Stok: {inst.eligible_keys ?? 0}</span>
              {inst.status === 'RUNNING' && (
                <>
                  <span>•</span>
                  <span>⏱ {fmtUptime(inst.uptime_seconds)}</span>
                </>
              )}
            </div>
          </div>
        </div>

        {/* Progress & Quick Controls */}
        <div className="flex items-center gap-2.5 ml-auto">
          {ONLINE.includes(inst.status) && (
            <div className="hidden sm:flex flex-col text-right pr-2 border-r border-slate-800">
              <span className="text-[11px] text-slate-300 font-medium font-mono">
                Progress: <span className="text-emerald-400 font-bold">{progCount}</span> / {progLimitStr} {progUnit}
              </span>
              <span className="text-[10px] text-slate-500 truncate max-w-[200px]" title={inst.current_step}>
                {inst.current_step || '—'}
              </span>
            </div>
          )}

          <button
            onClick={() => togglePreview(!inst.live_preview_enabled)}
            className={`px-3 py-1.5 rounded-xl border text-xs font-medium flex items-center gap-1.5 transition ${
              inst.live_preview_enabled
                ? 'bg-slate-800 text-slate-300 border-slate-700 hover:text-white'
                : 'bg-emerald-600/20 text-emerald-300 border-emerald-500/40 hover:bg-emerald-600/30'
            }`}
            title="Toggle Live Preview"
          >
            {inst.live_preview_enabled ? <EyeOff className="w-3.5 h-3.5" /> : <Eye className="w-3.5 h-3.5" />}
            <span className="hidden md:inline">{inst.live_preview_enabled ? 'Matikan Preview' : 'Aktifkan Preview'}</span>
          </button>

          {enabled && (
            <button
              onClick={handleManualRefresh}
              disabled={refreshing}
              className="p-2 text-slate-300 hover:text-white bg-slate-800 hover:bg-slate-700 border border-slate-700 rounded-xl transition"
              title="Refresh Manual Screenshot"
            >
              <RefreshCw className={`w-4 h-4 ${refreshing ? 'animate-spin' : ''}`} />
            </button>
          )}

          <button
            onClick={onClose}
            className="p-2 text-slate-400 hover:text-white bg-slate-800/80 hover:bg-rose-950/40 border border-slate-700 hover:border-rose-500/60 rounded-xl transition"
            title="Tutup Fullscreen (ESC)"
          >
            <X className="w-4 h-4" />
          </button>
        </div>
      </div>

      {/* Main Fullscreen Image Container */}
      <div
        className="flex-1 flex flex-col items-center justify-center min-h-0 overflow-hidden relative"
        onClick={(e) => {
          if (e.target === e.currentTarget) onClose();
        }}
      >
        {enabled && src && !error ? (
          <div className="relative h-full w-full max-h-[80vh] flex items-center justify-center">
            <img
              src={src}
              alt={inst.name}
              className="max-h-[80vh] max-w-full w-auto h-auto object-contain rounded-2xl shadow-2xl border border-slate-700/80"
            />
          </div>
        ) : (
          <div className="h-full max-h-[65vh] w-full max-w-md bg-slate-900/90 border border-slate-800 rounded-2xl flex flex-col items-center justify-center p-8 text-center shadow-2xl">
            {!online ? (
              <div className="flex flex-col items-center gap-3 text-slate-500">
                <WifiOff className="w-12 h-12 text-slate-600" />
                <span className="text-sm font-semibold text-slate-400">Device Offline</span>
                <span className="text-xs text-slate-500">Perangkat tidak terhubung ke ADB</span>
              </div>
            ) : !inst.live_preview_enabled ? (
              <div className="flex flex-col items-center gap-3">
                <EyeOff className="w-12 h-12 text-slate-600" />
                <span className="text-sm font-semibold text-slate-200">
                  Live Preview Nonaktif (Mode Hemat Resource)
                </span>
                <p className="text-xs text-slate-400 max-w-xs">
                  Screenshot live emulator sedang dinonaktifkan untuk menghemat penggunaan RAM dan CPU host.
                </p>
                <button
                  onClick={() => togglePreview(true)}
                  className="mt-2 px-4 py-2 bg-emerald-600 hover:bg-emerald-500 text-white text-xs font-semibold rounded-xl shadow-lg shadow-emerald-600/30 transition"
                >
                  Aktifkan Live Preview Sekarang
                </button>
              </div>
            ) : (
              <div className="flex flex-col items-center gap-3 text-slate-500">
                {error ? <WifiOff className="w-10 h-10 text-rose-400" /> : <Loader2 className="w-10 h-10 animate-spin text-sky-400" />}
                <span className="text-xs font-medium text-slate-300">
                  {error ? 'Gagal mengambil screenshot dari emulator' : 'Memuat layar live emulator…'}
                </span>
                {error && (
                  <button
                    onClick={handleManualRefresh}
                    className="px-3 py-1 bg-slate-800 hover:bg-slate-700 text-slate-200 rounded-lg text-xs"
                  >
                    Coba Lagi
                  </button>
                )}
              </div>
            )}
          </div>
        )}
      </div>

      {/* Bottom Floating Info Pill */}
      <div className="mt-2 shrink-0 flex items-center justify-between text-[11px] text-slate-400 bg-slate-900/80 border border-slate-800/80 rounded-xl px-4 py-2 max-w-2xl mx-auto w-full shadow-lg">
        <span className="truncate">
          Layar: <strong className="text-slate-200 font-mono">{inst.current_screen || 'SCREEN_UNKNOWN_OR_LOADING'}</strong>
        </span>
        <span className="text-slate-500 text-[10px]">Tekan ESC atau klik di luar untuk menutup</span>
        <span className="truncate text-right">
          Step: <strong className="text-slate-200">{inst.current_step || '—'}</strong>
        </span>
      </div>
    </div>
  );
}

function DeviceCard({ inst, intervalSec, patchInstance, toast, onEdit, refreshInstances, onExpand }) {
  const [menuOpen, setMenuOpen] = useState(false);
  const [confirm, setConfirm] = useState(null); // 'device' | 'delete'
  const online = ONLINE.includes(inst.status);

  const run = async (fn, okMsg) => {
    setMenuOpen(false);
    try {
      const res = await fn();
      toast({ type: 'success', message: res?.data?.message || okMsg });
    } catch (e) {
      toast({ type: 'error', title: inst.name, message: errorDetail(e) });
    }
  };

  const togglePreview = async (value) => {
    setMenuOpen(false);
    const prev = inst.live_preview_enabled;
    patchInstance(inst.uuid, { live_preview_enabled: value });
    try {
      await instancesApi.update(inst.uuid, { live_preview_enabled: value });
    } catch (e) {
      patchInstance(inst.uuid, { live_preview_enabled: prev });
      toast({ type: 'error', title: inst.name, message: errorDetail(e, 'Gagal mengubah preview') });
    }
  };

  const itemCls =
    'w-full text-left px-3 py-1.5 text-[11px] hover:bg-slate-800 flex items-center gap-2 text-slate-300';

  return (
    <div className="bg-slate-900 border border-slate-800 rounded-xl p-3 space-y-2.5 min-w-0">
      {/* Header */}
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2 min-w-0">
          <span className={`w-2 h-2 rounded-full shrink-0 ${statusDot(inst.status)}`} />
          <div className="min-w-0">
            <div className="text-xs font-semibold text-white truncate flex items-center gap-1.5">
              <span className="truncate">{inst.name}</span>
              <ConnectionBadge type={inst.connection_type} compact />
            </div>
            <div className="text-[10px] text-slate-500 font-mono truncate" title={inst.address}>
              {inst.address}
            </div>
          </div>
        </div>
        <div className="flex items-center gap-1.5 shrink-0">
          <StatusBadge status={inst.status} />

          {/* Expand Fullscreen Button */}
          <button
            onClick={() => onExpand(inst)}
            className="p-1 text-slate-400 hover:text-white bg-slate-800 hover:bg-slate-700 rounded-md border border-slate-700 transition"
            title="Buka Layar Penuh (Expand Modal)"
          >
            <Maximize2 className="w-3.5 h-3.5" />
          </button>

          <div className="relative">
            <button
              onClick={() => setMenuOpen((v) => !v)}
              className="p-1 text-slate-400 hover:text-white bg-slate-800 hover:bg-slate-700 rounded-md border border-slate-700"
              title="Menu device"
            >
              <Gear className="w-3.5 h-3.5" />
            </button>
            {menuOpen && (
              <>
                <div className="fixed inset-0 z-30" onClick={() => setMenuOpen(false)} />
                <div className="absolute right-0 mt-1 w-56 bg-slate-900 border border-slate-700 rounded-xl shadow-2xl z-40 py-1">
                  <button
                    className={itemCls}
                    onClick={() => togglePreview(!inst.live_preview_enabled)}
                  >
                    {inst.live_preview_enabled ? <EyeOff className="w-3.5 h-3.5" /> : <Eye className="w-3.5 h-3.5" />}
                    Live Preview: {inst.live_preview_enabled ? 'ON → matikan' : 'OFF → aktifkan'}
                  </button>
                  <div className="my-1 border-t border-slate-800" />
                  <button
                    className={`${itemCls} text-rose-300`}
                    onClick={() => {
                      setMenuOpen(false);
                      setConfirm('device');
                    }}
                  >
                    <Power className="w-3.5 h-3.5" /> Force Restart Device
                  </button>
                  <button
                    className={itemCls}
                    onClick={() =>
                      run(() => instancesApi.forceRestartApp(inst.uuid), 'Force restart app dikirim')
                    }
                  >
                    <RotateCcw className="w-3.5 h-3.5" /> Force Restart App
                  </button>
                  <button
                    className={itemCls}
                    onClick={() => run(() => instancesApi.reconnect(inst.uuid), 'Reconnect ADB dikirim')}
                  >
                    <Plug className="w-3.5 h-3.5" /> Reconnect ADB
                  </button>
                  <div className="my-1 border-t border-slate-800" />
                  <button
                    className={itemCls}
                    onClick={() => {
                      setMenuOpen(false);
                      onEdit(inst);
                    }}
                  >
                    <Pencil className="w-3.5 h-3.5" /> Edit Alias / IP:Port
                  </button>
                  <button
                    className={`${itemCls} text-rose-400`}
                    onClick={() => {
                      setMenuOpen(false);
                      setConfirm('delete');
                    }}
                  >
                    <Trash2 className="w-3.5 h-3.5" /> Hapus (Soft-delete)
                  </button>
                </div>
              </>
            )}
          </div>
        </div>
      </div>

      <PreviewArea
        inst={inst}
        intervalSec={intervalSec}
        patchInstance={patchInstance}
        onEnable={() => togglePreview(true)}
        onExpand={() => onExpand(inst)}
      />

      {/* Footer */}
      <div className="grid grid-cols-2 gap-x-2 gap-y-0.5 text-[10px] text-slate-500">
        <span className="truncate" title={inst.current_step}>
          Step: <span className="text-slate-300">{inst.current_step || '—'}</span>
        </span>
        <span className="truncate text-right" title={inst.current_screen}>
          {inst.current_screen || '—'}
        </span>
        <span>
          Keys: <span className="text-emerald-400">{online ? (inst.current_app_keys ?? '—') : '—'}</span>
        </span>
        <span className="text-right">
          {inst.mode ? <span className="text-sky-400">{MODE_SHORT[inst.mode] || inst.mode}</span> : '—'}
          {ONLINE.includes(inst.status) && (
            <span className="ml-1 text-emerald-400 font-mono font-medium">
              ({inst.session_completed_count ?? 0}/{inst.stop_mode === 'until_exhausted' ? '∞' : (inst.stop_limit || 100)})
            </span>
          )}
          {inst.status === 'RUNNING' && <span className="ml-1.5">⏱ {fmtUptime(inst.uptime_seconds)}</span>}
        </span>
      </div>

      {confirm === 'device' && (
        <ConfirmModal
          title={`Force Restart Device — ${inst.name}`}
          danger
          confirmLabel="Ya, Restart Device"
          onCancel={() => setConfirm(null)}
          onConfirm={() => {
            setConfirm(null);
            run(() => instancesApi.forceRestartDevice(inst.uuid), 'Reboot device dikirim');
          }}
        >
          <p>
            Ini akan me-<strong>reboot seluruh emulator</strong> ({inst.address}), bukan sekadar aplikasi
            Nusuk. Proses yang sedang berjalan akan terhenti dan device butuh beberapa saat untuk online lagi.
          </p>
        </ConfirmModal>
      )}
      {confirm === 'delete' && (
        <ConfirmModal
          title={`Hapus Instance — ${inst.name}`}
          danger
          confirmLabel="Ya, Hapus"
          onCancel={() => setConfirm(null)}
          onConfirm={async () => {
            setConfirm(null);
            try {
              await instancesApi.remove(inst.uuid);
              toast({ type: 'success', message: `${inst.name} diarsipkan (soft-delete). Riwayat tetap tersimpan.` });
              refreshInstances();
            } catch (e) {
              toast({ type: 'error', title: inst.name, message: errorDetail(e) });
            }
          }}
        >
          <p>
            Instance akan dinonaktifkan dan disembunyikan dari dashboard. Riwayat kuis/key tetap aman dan
            dapat dipulihkan dengan menambahkan kembali IP:Port yang sama.
          </p>
        </ConfirmModal>
      )}
    </div>
  );
}

function EditDeviceModal({ inst, onClose, toast, refreshInstances }) {
  const isUsb = (inst.connection_type || 'tcp').toLowerCase() === 'usb';
  const [name, setName] = useState(inst.name || '');
  const [ip, setIp] = useState(inst.ip || '');
  const [port, setPort] = useState(String(inst.port ?? '5555'));
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState('');

  const save = async (e) => {
    e.preventDefault();
    setSaving(true);
    setErr('');
    try {
      const payload = isUsb
        ? { name: name.trim() }
        : { name: name.trim(), ip: ip.trim(), port: parseInt(port, 10) };
      await instancesApi.update(inst.uuid, payload);
      toast({ type: 'success', message: 'Instance diperbarui' });
      refreshInstances();
      onClose();
    } catch (ex) {
      setErr(errorDetail(ex, 'Gagal menyimpan'));
    } finally {
      setSaving(false);
    }
  };

  const input =
    'w-full bg-slate-950 border border-slate-800 rounded-xl px-3 py-2 text-white text-xs focus:outline-none focus:border-sky-500';
  return (
    <div className="fixed inset-0 z-50 bg-slate-950/75 backdrop-blur-sm flex items-center justify-center p-4">
      <form onSubmit={save} className="bg-slate-900 border border-slate-800 rounded-2xl max-w-sm w-full p-5 space-y-3 shadow-2xl">
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-semibold text-white flex items-center gap-2">
            Edit Instance <ConnectionBadge type={inst.connection_type} />
          </h3>
          <button type="button" onClick={onClose} className="text-slate-400 hover:text-white">
            <X className="w-4 h-4" />
          </button>
        </div>
        <label className="block text-xs text-slate-400">
          Alias
          <input className={`${input} mt-1`} value={name} onChange={(e) => setName(e.target.value)} />
        </label>
        {isUsb ? (
          <div className="p-2.5 rounded-xl bg-slate-950/70 border border-slate-800 text-[11px] space-y-1">
            <div className="text-slate-500">Serial USB:</div>
            <div className="font-mono text-indigo-300 break-all">{inst.adb_serial || inst.address}</div>
            <div className="text-[10px] text-slate-500">
              Untuk memindahkan HP ini ke Wireless Debugging tanpa kehilangan histori, cukup tambahkan via tab Wireless di modal Tambah Instance (UUID otomatis ditautkan).
            </div>
          </div>
        ) : (
          <div className="grid grid-cols-3 gap-2">
            <label className="col-span-2 block text-xs text-slate-400">
              IP
              <input className={`${input} mt-1 font-mono`} value={ip} onChange={(e) => setIp(e.target.value)} />
            </label>
            <label className="block text-xs text-slate-400">
              Port
              <input className={`${input} mt-1 font-mono`} value={port} onChange={(e) => setPort(e.target.value)} />
            </label>
          </div>
        )}
        {err && <div className="text-[11px] text-rose-400">{err}</div>}
        <div className="flex justify-end gap-2 text-xs">
          <button type="button" onClick={onClose} className="px-4 py-2 text-slate-400 hover:text-white rounded-xl hover:bg-slate-800">
            Batal
          </button>
          <button
            type="submit"
            disabled={saving || !name.trim() || (!isUsb && !ip.trim())}
            className="px-4 py-2 bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 text-white rounded-xl font-semibold"
          >
            {saving ? 'Menyimpan…' : 'Simpan'}
          </button>
        </div>
      </form>
    </div>
  );
}

export default function EmulatorPreview({ instances, intervalSec, patchInstance, toast, refreshInstances, onAdd }) {
  const [editing, setEditing] = useState(null);
  const [expandedUuid, setExpandedUuid] = useState(null);

  const activeExpanded = expandedUuid ? instances.find((i) => i.uuid === expandedUuid) : null;

  return (
    <div className="bg-slate-900 border border-slate-800 rounded-2xl p-4 shadow-sm flex flex-col">
      <div className="flex items-center justify-between border-b border-slate-800 pb-3 mb-3">
        <div className="flex items-center gap-2">
          <Smartphone className="w-4 h-4 text-sky-400" />
          <h3 className="text-xs font-semibold text-white">Live Emulator Preview</h3>
          <span className="text-[10px] text-slate-500">({instances.length} instance)</span>
        </div>
        <button
          onClick={refreshInstances}
          className="p-1.5 text-slate-400 hover:text-white bg-slate-800 hover:bg-slate-700 rounded-lg border border-slate-700"
          title="Muat ulang daftar device"
        >
          <RefreshCw className="w-3.5 h-3.5" />
        </button>
      </div>

      {instances.length === 0 ? (
        <div className="py-16 flex flex-col items-center gap-3 text-slate-500 text-xs">
          <Smartphone className="w-8 h-8" />
          <span>Belum ada instance terdaftar.</span>
          <button
            onClick={onAdd}
            className="px-3 py-1.5 bg-emerald-600 hover:bg-emerald-500 text-white rounded-lg font-medium flex items-center gap-1.5"
          >
            <Plus className="w-3.5 h-3.5" /> Tambah Instance
          </button>
        </div>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-3 max-h-[720px] overflow-y-auto pr-1">
          {instances.map((inst) => (
            <DeviceCard
              key={inst.uuid}
              inst={inst}
              intervalSec={intervalSec}
              patchInstance={patchInstance}
              toast={toast}
              onEdit={setEditing}
              refreshInstances={refreshInstances}
              onExpand={(d) => setExpandedUuid(d.uuid)}
            />
          ))}
        </div>
      )}

      {editing && (
        <EditDeviceModal
          inst={editing}
          onClose={() => setEditing(null)}
          toast={toast}
          refreshInstances={refreshInstances}
        />
      )}

      {activeExpanded && (
        <FullscreenPreviewModal
          inst={activeExpanded}
          intervalSec={intervalSec}
          patchInstance={patchInstance}
          onClose={() => setExpandedUuid(null)}
          toast={toast}
        />
      )}
    </div>
  );
}
