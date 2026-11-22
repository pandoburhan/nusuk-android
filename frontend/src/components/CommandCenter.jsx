import React, { useState } from 'react';
import { Play, Square, RotateCcw, Bot, Plus, Power, Smartphone, ArrowRightLeft, AlertTriangle, Sliders, Check, LogIn, KeyRound, Loader2 } from 'lucide-react';
import { controlApi, errorDetail } from '../api/client';
import { ONLINE, StatusBadge, ConnectionBadge, ConfirmModal } from './common';

const ACTIVE_RUN = new Set(['RUNNING', 'RECOVERING']);
const MODE_SHORT = { answer_only: 'Answer', redeem_only: 'Redeem', auto: 'Auto' };

const MODES = [
  {
    id: 'answer_only',
    title: 'Mode Jawab Soal',
    desc: 'Menjawab kuis otomatis hingga target soal tercapai atau kunci akun habis (0).',
  },
  {
    id: 'redeem_only',
    title: 'Mode Isi Key Saja',
    desc: 'Mengisi key dari database hingga target key sukses tercapai atau stok habis.',
  },
  {
    id: 'auto',
    title: 'Mode Auto Penuh',
    desc: 'Siklus penuh: main kuis & auto redeem key hingga target tercapai atau sumber daya habis.',
    recommended: true,
  },
];

export default function CommandCenter({ instances, toast, onAdd, onActionComplete, onOpenAutoLogin }) {
  const [mode, setMode] = useState('auto');
  const [selected, setSelected] = useState(() => new Set());
  const [busy, setBusy] = useState(false);
  const [restartOpen, setRestartOpen] = useState(false);
  const [restartKind, setRestartKind] = useState('app');

  // Per-device stop conditions: { [uuid]: { stop_mode: 'limit' | 'until_exhausted', stop_limit: 100 } }
  const [stopConditions, setStopConditions] = useState({});
  const [bulkApplyOpen, setBulkApplyOpen] = useState(false);
  const [bulkApplyLimit, setBulkApplyLimit] = useState(100);
  const [bulkApplyMode, setBulkApplyMode] = useState('limit');

  // Drop selections of devices that no longer exist.
  const uuids = new Set(instances.map((i) => i.uuid));
  const sel = [...selected].filter((u) => uuids.has(u));
  const allSelected = instances.length > 0 && sel.length === instances.length;

  const isRedeem = mode === 'redeem_only';
  const targetUnit = isRedeem ? 'Key Sukses' : 'Soal';

  const getStopConfig = (uuid) => {
    return stopConditions[uuid] || { stop_mode: 'limit', stop_limit: 100 };
  };

  const updateStopConfig = (uuid, patch) => {
    setStopConditions((prev) => ({
      ...prev,
      [uuid]: { ...(prev[uuid] || { stop_mode: 'limit', stop_limit: 100 }), ...patch },
    }));
  };

  const applyToAllSelected = () => {
    const next = { ...stopConditions };
    const lim = Math.max(1, parseInt(bulkApplyLimit, 10) || 100);
    sel.forEach((u) => {
      next[u] = { stop_mode: bulkApplyMode, stop_limit: lim };
    });
    setStopConditions(next);
    setBulkApplyOpen(false);
    toast({
      type: 'success',
      message: `Stop Condition diterapkan ke ${sel.length} device terpilih (${bulkApplyMode === 'limit' ? `${lim} ${targetUnit}` : 'Sampai Habis'}).`,
    });
  };

  const toggle = (uuid) =>
    setSelected((prev) => {
      const n = new Set(prev);
      if (n.has(uuid)) n.delete(uuid);
      else n.add(uuid);
      return n;
    });

  const toggleAll = () => setSelected(allSelected ? new Set() : new Set(instances.map((i) => i.uuid)));

  // Calculate warning for a device
  const getDeviceWarning = (inst) => {
    const cfg = getStopConfig(inst.uuid);
    if (cfg.stop_mode !== 'limit') return null;
    const lim = Number(cfg.stop_limit) || 0;
    if (lim <= 0) return null;

    if (mode === 'answer_only') {
      const appKeys = inst.current_app_keys ?? 0;
      if (lim > appKeys) {
        return `Target ${lim} soal melebihi nyawa akun saat ini (${appKeys} key). Bot akan berhenti lebih awal saat nyawa habis.`;
      }
    } else if (mode === 'redeem_only') {
      const elig = inst.eligible_keys ?? 0;
      if (lim > elig) {
        return `Target ${lim} key melebihi stok unused tersedia untuk device ini (${elig} key). Bot akan berhenti lebih awal saat stok habis.`;
      }
    } else if (mode === 'auto') {
      const appKeys = inst.current_app_keys ?? 0;
      const elig = inst.eligible_keys ?? 0;
      const total = appKeys + elig;
      if (lim > total) {
        return `Target ${lim} soal melebihi total nyawa & stok unused (${total} total: ${appKeys} nyawa + ${elig} stok). Bot akan berhenti lebih awal saat stok habis.`;
      }
    }
    return null;
  };

  const report = (title, res, warningNotices = []) => {
    const d = res.data || {};
    const started = d.started || [];
    const switched = d.switched || [];
    const already = d.already_running || [];
    const skipped = d.skipped || [];
    const okCount = started.length + switched.length + already.length;
    const type = okCount === 0 ? 'error' : skipped.length > 0 || warningNotices.length > 0 ? 'warning' : 'success';
    const lines = [];
    if (started.length > 0 && title.startsWith('START')) {
      lines.push(`Mulai baru (${started.length}): ${started.map((x) => x.name).join(', ')}`);
    }
    if (switched.length > 0) {
      lines.push(
        `Switch mode (${switched.length}): ${switched.map((x) => `${x.name} (${x.from_mode || '?'} → ${x.to_mode})`).join(', ')}`,
      );
    }
    if (already.length > 0) {
      lines.push(`Tetap berjalan / mode sama (${already.length}): ${already.map((x) => x.name).join(', ')}`);
    }
    for (const s of skipped) {
      lines.push(`Dilewati — ${s.name}: ${s.reason}`);
    }
    for (const w of warningNotices) {
      lines.push(`⚠️ ${w}`);
    }
    toast({
      type,
      title,
      message: d.message || `${started.length} berhasil${skipped.length ? `, ${skipped.length} dilewati` : ''}.`,
      lines,
      sticky: skipped.length > 0 || switched.length > 0 || warningNotices.length > 0,
    });
  };

  const handleStart = async () => {
    if (sel.length === 0) {
      toast({ type: 'warning', message: 'Pilih minimal satu device.' });
      return;
    }

    // Validate inputs
    const invalidDevs = [];
    const warnings = [];
    const conditionsPayload = {};

    sel.forEach((uuid) => {
      const inst = instances.find((i) => i.uuid === uuid);
      const name = inst ? inst.name : uuid.slice(0, 8);
      const cfg = getStopConfig(uuid);

      if (cfg.stop_mode === 'limit') {
        const val = parseInt(cfg.stop_limit, 10);
        if (isNaN(val) || val < 1) {
          invalidDevs.push(`${name} (target harus berupa angka >= 1)`);
        } else {
          conditionsPayload[uuid] = { stop_mode: 'limit', stop_limit: val };
        }
      } else {
        conditionsPayload[uuid] = { stop_mode: 'until_exhausted', stop_limit: 100 };
      }

      if (inst) {
        const warn = getDeviceWarning(inst);
        if (warn) {
          warnings.push(`[${name}] ${warn}`);
        }
      }
    });

    if (invalidDevs.length > 0) {
      toast({
        type: 'error',
        title: 'Validasi Stop Condition Gagal',
        message: 'Perbaiki target angka pada device berikut sebelum memulai:',
        lines: invalidDevs,
      });
      return;
    }

    setBusy(true);
    const title = switchableCount > 0 ? 'START / SWITCH MODE' : 'START';
    try {
      const res = await controlApi.bulkStart(sel, mode, conditionsPayload);
      report(title, res, warnings);
    } catch (e) {
      toast({ type: 'error', title, message: errorDetail(e, 'Perintah gagal') });
    } finally {
      setBusy(false);
      if (onActionComplete) onActionComplete();
    }
  };

  const exec = async (title, fn) => {
    if (sel.length === 0) {
      toast({ type: 'warning', message: 'Pilih minimal satu device.' });
      return;
    }
    setBusy(true);
    try {
      const res = await fn();
      report(title, res);
    } catch (e) {
      toast({ type: 'error', title, message: errorDetail(e, 'Perintah gagal') });
    } finally {
      setBusy(false);
      if (onActionComplete) onActionComplete();
    }
  };

  const noSel = sel.length === 0;
  const selDevices = instances.filter((i) => selected.has(i.uuid));
  const switchableCount = selDevices.filter((i) => ACTIVE_RUN.has(i.status) && i.mode && i.mode !== mode).length;
  const sameModeCount = selDevices.filter((i) => ACTIVE_RUN.has(i.status) && i.mode === mode).length;
  const btn = 'flex-1 px-3 py-2.5 rounded-xl font-semibold text-xs flex items-center justify-center gap-1.5 transition active:scale-95 disabled:opacity-40 disabled:cursor-not-allowed disabled:active:scale-100';

  return (
    <div className="bg-slate-900 border border-slate-800 rounded-2xl p-4 shadow-sm space-y-4">
      <div className="flex items-center justify-between border-b border-slate-800/80 pb-3">
        <div>
          <h2 className="text-sm font-semibold text-white flex items-center gap-2">
            <Bot className="w-4 h-4 text-emerald-400" />
            Bulk Command Center
          </h2>
          <p className="text-[11px] text-slate-500">Kendalikan banyak device sekaligus</p>
        </div>
        <button
          onClick={onAdd}
          className="px-2.5 py-1.5 bg-emerald-600 hover:bg-emerald-500 text-white text-[11px] font-semibold rounded-lg flex items-center gap-1 shadow shadow-emerald-600/20"
        >
          <Plus className="w-3.5 h-3.5" /> Tambah Instance
        </button>
      </div>

      {/* Mode cards */}
      <div className="space-y-2">
        {MODES.map((m) => {
          const on = mode === m.id;
          return (
            <button
              key={m.id}
              type="button"
              onClick={() => setMode(m.id)}
              className={`w-full text-left p-3 rounded-xl border transition ${
                on
                  ? 'bg-emerald-950/20 border-emerald-500/60 shadow-sm shadow-emerald-500/10'
                  : 'bg-slate-800/40 border-slate-800 hover:border-slate-700'
              }`}
            >
              <div className="flex items-center justify-between">
                <span className="font-semibold text-xs text-white flex items-center gap-1.5">
                  {m.title}
                  {m.recommended && (
                    <span className="text-[9px] px-1.5 rounded bg-emerald-500/20 text-emerald-300 border border-emerald-500/30">
                      Rekomendasi
                    </span>
                  )}
                </span>
                <span
                  className={`w-3.5 h-3.5 rounded-full border flex items-center justify-center ${on ? 'border-emerald-500 bg-emerald-500' : 'border-slate-600'}`}
                >
                  {on && <span className="w-1.5 h-1.5 rounded-full bg-white" />}
                </span>
              </div>
              <p className="text-[10px] text-slate-400 mt-1 leading-relaxed">{m.desc}</p>
            </button>
          );
        })}
      </div>

      {/* Device list */}
      <div>
        <div className="flex items-center justify-between mb-1.5">
          <label className="flex items-center gap-2 text-[11px] text-slate-300 cursor-pointer select-none">
            <input
              type="checkbox"
              checked={allSelected}
              onChange={toggleAll}
              disabled={instances.length === 0}
              className="accent-emerald-500"
            />
            Select All
          </label>
          <div className="flex items-center gap-2">
            {sel.length > 0 && (
              <button
                type="button"
                onClick={() => setBulkApplyOpen((v) => !v)}
                className="text-[10px] text-sky-400 hover:text-sky-300 flex items-center gap-1 bg-sky-950/40 border border-sky-800/60 px-2 py-0.5 rounded-md"
              >
                <Sliders className="w-3 h-3" />
                Terapkan ke {sel.length} terpilih
              </button>
            )}
            <span className="text-[10px] text-slate-500">
              {sel.length}/{instances.length} dipilih
            </span>
          </div>
        </div>

        {/* Batch apply bar */}
        {bulkApplyOpen && sel.length > 0 && (
          <div className="mb-2 p-2 bg-slate-800/80 border border-sky-500/40 rounded-xl space-y-2 animate-fadeIn">
            <div className="flex items-center justify-between text-[11px] text-sky-300 font-medium">
              <span>Set Stop Condition untuk {sel.length} Device Terpilih:</span>
              <button
                type="button"
                onClick={() => setBulkApplyOpen(false)}
                className="text-slate-400 hover:text-white"
              >
                ✕
              </button>
            </div>
            <div className="flex items-center gap-2 flex-wrap text-xs">
              <label className="flex items-center gap-1.5 text-slate-300 cursor-pointer text-[11px]">
                <input
                  type="radio"
                  name="bulk_stop_mode"
                  checked={bulkApplyMode === 'limit'}
                  onChange={() => setBulkApplyMode('limit')}
                  className="accent-sky-500"
                />
                Target:
              </label>
              {bulkApplyMode === 'limit' && (
                <div className="flex items-center gap-1">
                  <input
                    type="number"
                    min="1"
                    value={bulkApplyLimit}
                    onChange={(e) => setBulkApplyLimit(e.target.value)}
                    className="w-20 px-2 py-1 bg-slate-950 border border-slate-700 rounded-lg text-white text-xs font-mono focus:outline-none focus:border-sky-500"
                  />
                  <span className="text-[11px] text-slate-400">{targetUnit}</span>
                </div>
              )}
              <label className="flex items-center gap-1.5 text-slate-300 cursor-pointer text-[11px] ml-2">
                <input
                  type="radio"
                  name="bulk_stop_mode"
                  checked={bulkApplyMode === 'until_exhausted'}
                  onChange={() => setBulkApplyMode('until_exhausted')}
                  className="accent-sky-500"
                />
                Sampai Habis
              </label>
              <button
                type="button"
                onClick={applyToAllSelected}
                className="ml-auto px-2.5 py-1 bg-sky-600 hover:bg-sky-500 text-white font-medium text-[11px] rounded-lg flex items-center gap-1 shadow-sm"
              >
                <Check className="w-3 h-3" /> Terapkan
              </button>
            </div>
          </div>
        )}

        {/* Device item cards */}
        <div className="max-h-72 overflow-y-auto space-y-1.5 border border-slate-800 rounded-xl p-1.5 bg-slate-950/50">
          {instances.length === 0 && (
            <div className="text-[11px] text-slate-600 text-center py-4 flex flex-col items-center gap-1">
              <Smartphone className="w-4 h-4" />
              Belum ada instance
            </div>
          )}
          {instances.map((i) => {
            const on = selected.has(i.uuid);
            const isAct = ACTIVE_RUN.has(i.status);
            const willSwitch = on && isAct && i.mode && i.mode !== mode;
            const sameMode = on && isAct && i.mode === mode;
            const stopCfg = getStopConfig(i.uuid);
            const warningMsg = getDeviceWarning(i);
            const isLimitInvalid = stopCfg.stop_mode === 'limit' && (!stopCfg.stop_limit || Number(stopCfg.stop_limit) < 1);

            // Live session progress
            const progCount = i.session_completed_count ?? 0;
            const progLimitStr = i.stop_mode === 'until_exhausted' ? '∞' : (i.stop_limit || 100);
            const progUnit = i.mode === 'redeem_only' ? 'Key' : 'Soal';

            return (
              <div
                key={i.uuid}
                className={`p-2 rounded-xl border transition text-[11px] ${
                  willSwitch
                    ? 'bg-amber-950/20 border-amber-600/50'
                    : on
                      ? 'bg-slate-850/90 border-emerald-600/50 shadow-sm'
                      : 'border-slate-800/80 bg-slate-900/60 hover:bg-slate-850'
                }`}
              >
                {/* Main device row */}
                <div className="flex items-center gap-2">
                  <input
                    type="checkbox"
                    checked={on}
                    onChange={() => toggle(i.uuid)}
                    className="accent-emerald-500 rounded"
                  />
                  <div className="min-w-0 flex-1">
                    <div className="text-slate-200 font-medium truncate flex items-center gap-1.5">
                      <span className="truncate">{i.name}</span>
                      <ConnectionBadge type={i.connection_type} compact />
                      {isAct && i.mode && (
                        <span className="text-[9px] px-1 rounded bg-slate-800 text-slate-300 border border-slate-700 font-mono shrink-0">
                          {MODE_SHORT[i.mode] || i.mode}
                        </span>
                      )}
                      {willSwitch && (
                        <span className="text-[9px] px-1 rounded bg-amber-500/15 text-amber-300 border border-amber-500/30 shrink-0">
                          → switch
                        </span>
                      )}
                      {sameMode && (
                        <span className="text-[9px] px-1 rounded bg-emerald-500/10 text-emerald-400 border border-emerald-500/30 shrink-0">
                          sama
                        </span>
                      )}
                      {isAct && (
                        <span className="text-[9px] px-1.5 py-0.2 rounded bg-sky-950/60 text-sky-300 border border-sky-800/50 font-mono shrink-0">
                          Progress: {progCount} / {progLimitStr} {progUnit}
                        </span>
                      )}
                      {!isAct && i.has_pending_task && (
                        <span className="text-[9px] px-1.5 py-0.2 rounded bg-amber-950/60 text-amber-300 border border-amber-800/50 font-mono shrink-0" title="Tugas aktif tersimpan: otomatis dilanjutkan saat terhubung kembali">
                          Auto-Resume: {MODE_SHORT[i.pending_task_mode] || i.pending_task_mode} ({i.pending_task_completed || 0}/{i.pending_task_stop_mode === 'until_exhausted' ? '∞' : (i.pending_task_stop_limit || 100)})
                        </span>
                      )}
                    </div>
                    <div className="text-[10px] text-slate-500 font-mono flex items-center gap-2 mt-0.5">
                      <span>{i.address}</span>
                      <span>•</span>
                      <span className="text-emerald-400/90">Keys: {i.current_app_keys ?? '—'}</span>
                      <span>•</span>
                      <span className="text-sky-400/90">Stok: {i.eligible_keys ?? 0}</span>
                    </div>
                  </div>
                  <StatusBadge status={i.status} />
                </div>

                {/* Inline Auto-Login quick button when device needs credentials/OTP */}
                {i.login_status === 'AWAITING_CREDENTIALS' && onOpenAutoLogin && (
                  <button
                    type="button"
                    onClick={() => onOpenAutoLogin(i)}
                    className="mt-2 w-full py-1.5 px-2.5 rounded-lg bg-gradient-to-r from-orange-600 to-rose-600 hover:from-orange-500 hover:to-rose-500 text-white text-[10px] font-bold flex items-center justify-center gap-1.5 shadow shadow-orange-600/20 transition"
                  >
                    <LogIn className="w-3 h-3" /> Auto Login Required
                  </button>
                )}
                {i.login_status === 'AWAITING_OTP' && onOpenAutoLogin && (
                  <button
                    type="button"
                    onClick={() => onOpenAutoLogin(i)}
                    className="mt-2 w-full py-1.5 px-2.5 rounded-lg bg-amber-500 hover:bg-amber-400 text-slate-950 text-[10px] font-bold flex items-center justify-center gap-1.5 shadow shadow-amber-500/20 transition"
                  >
                    <KeyRound className="w-3 h-3" /> Input OTP Required (4 Digit)
                  </button>
                )}
                {i.login_status === 'PROCESSING_LOGIN' && onOpenAutoLogin && (
                  <button
                    type="button"
                    onClick={() => onOpenAutoLogin(i)}
                    className="mt-2 w-full py-1 px-2.5 rounded-lg bg-sky-950/60 hover:bg-sky-950 text-sky-300 border border-sky-600/40 text-[10px] font-semibold flex items-center justify-center gap-1.5 transition"
                  >
                    <Loader2 className="w-3 h-3 animate-spin" /> Processing Auto-Login…
                  </button>
                )}

                {/* Per-Device Stop Condition Control (visible on all cards, highlighted when selected) */}
                <div className={`mt-2 pt-2 border-t flex flex-wrap items-center justify-between gap-2 text-[10px] ${on ? 'border-slate-700/80' : 'border-slate-800/40 text-slate-400'}`}>
                  <div className="flex items-center gap-1.5 font-medium">
                    <span className="text-slate-400">Stop:</span>
                    <button
                      type="button"
                      onClick={() => updateStopConfig(i.uuid, { stop_mode: 'limit' })}
                      className={`px-1.5 py-0.5 rounded text-[10px] transition font-medium ${
                        stopCfg.stop_mode === 'limit'
                          ? 'bg-emerald-600 text-white shadow-xs'
                          : 'bg-slate-800 text-slate-400 hover:text-white'
                      }`}
                    >
                      Target
                    </button>
                    <button
                      type="button"
                      onClick={() => updateStopConfig(i.uuid, { stop_mode: 'until_exhausted' })}
                      className={`px-1.5 py-0.5 rounded text-[10px] transition font-medium ${
                        stopCfg.stop_mode === 'until_exhausted'
                          ? 'bg-sky-600 text-white shadow-xs'
                          : 'bg-slate-800 text-slate-400 hover:text-white'
                      }`}
                    >
                      Sampai Habis
                    </button>
                  </div>

                  {stopCfg.stop_mode === 'limit' ? (
                    <div className="flex items-center gap-1">
                      <span className="text-slate-500">Maks:</span>
                      <input
                        type="number"
                        min="1"
                        value={stopCfg.stop_limit ?? 100}
                        onChange={(e) => updateStopConfig(i.uuid, { stop_limit: e.target.value })}
                        className={`w-16 px-1.5 py-0.5 bg-slate-950 border rounded text-[11px] font-mono text-center focus:outline-none ${
                          isLimitInvalid
                            ? 'border-rose-500 text-rose-300 bg-rose-950/20'
                            : 'border-slate-700 text-white focus:border-emerald-500'
                        }`}
                      />
                      <span className="text-slate-400">{targetUnit}</span>
                    </div>
                  ) : (
                    <span className="text-slate-400 italic">Berjalan hingga sumber daya habis</span>
                  )}
                </div>

                {/* Inline error for invalid target */}
                {isLimitInvalid && (
                  <div className="mt-1 text-[10px] text-rose-400 font-medium">
                    Target {targetUnit} harus berupa bilangan positif &gt;= 1.
                  </div>
                )}

                {/* Inline warning for insufficient resources */}
                {warningMsg && (
                  <div className="mt-1.5 p-1.5 rounded-lg bg-amber-950/30 border border-amber-600/40 text-amber-300 text-[10px] flex items-start gap-1.5 leading-snug">
                    <AlertTriangle className="w-3.5 h-3.5 shrink-0 text-amber-400 mt-0.5" />
                    <span>{warningMsg}</span>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </div>

      {/* Actions */}
      <div className="flex gap-2">
        <button
          disabled={noSel || busy}
          onClick={handleStart}
          className={`${btn} ${
            switchableCount > 0
              ? 'bg-teal-600 hover:bg-teal-500 text-white shadow-lg shadow-teal-600/20'
              : 'bg-emerald-600 hover:bg-emerald-500 text-white shadow-lg shadow-emerald-600/20'
          }`}
        >
          {switchableCount > 0 ? (
            <ArrowRightLeft className="w-3.5 h-3.5 shrink-0" />
          ) : (
            <Play className="w-3.5 h-3.5 fill-current shrink-0" />
          )}
          <span className="truncate">{switchableCount > 0 ? 'START / SWITCH MODE' : 'START'}</span>
        </button>
        <button
          disabled={noSel || busy}
          onClick={() => exec('STOP', () => controlApi.bulkStop(sel))}
          className={`${btn} bg-rose-600 hover:bg-rose-500 text-white shadow-lg shadow-rose-600/20`}
        >
          <Square className="w-3.5 h-3.5 fill-current" /> STOP
        </button>
        <button
          disabled={noSel || busy}
          onClick={() => {
            setRestartKind('app');
            setRestartOpen(true);
          }}
          className={`${btn} bg-amber-600/20 hover:bg-amber-600/30 text-amber-400 border border-amber-500/30`}
        >
          <RotateCcw className="w-3.5 h-3.5" /> RESTART
        </button>
      </div>
      <p className="text-[10px] text-slate-500 leading-relaxed">
        {switchableCount > 0 ? (
          <span className="text-amber-300">
            {switchableCount} device aktif pada mode lain akan otomatis me-restart aplikasi &amp; berpindah ke mode{' '}
            <strong>{MODE_SHORT[mode]}</strong>
            {sameModeCount > 0 ? ` (${sameModeCount} device bermode sama tetap jalan)` : ''}.
          </span>
        ) : (
          'Setiap device menjalankan Stop Condition-nya masing-masing secara independen. Device dengan mode berbeda akan otomatis berpindah mode.'
        )}
      </p>

      {restartOpen && (
        <ConfirmModal
          title={`Force Restart — ${sel.length} device`}
          danger={restartKind === 'device'}
          confirmLabel={restartKind === 'device' ? 'Ya, Reboot Device' : 'Ya, Restart Aplikasi'}
          onCancel={() => setRestartOpen(false)}
          onConfirm={() => {
            const kind = restartKind;
            setRestartOpen(false);
            exec(kind === 'device' ? 'FORCE RESTART DEVICE' : 'FORCE RESTART APP', () =>
              controlApi.bulkForceRestart(sel, kind),
            );
          }}
        >
          <p>Pilih jenis restart untuk device terpilih:</p>
          <div className="space-y-2">
            {[
              {
                id: 'app',
                icon: RotateCcw,
                t: 'Restart Aplikasi Nusuk',
                d: 'Hanya menutup paksa & membuka ulang aplikasi (bersihkan RAM app). Emulator tetap menyala.',
              },
              {
                id: 'device',
                icon: Power,
                t: 'Reboot Device (Emulator)',
                d: 'Mem-reboot seluruh emulator. Lebih lama dan menghentikan semua proses di device.',
              },
            ].map((o) => (
              <label
                key={o.id}
                className={`flex items-start gap-2 p-2.5 rounded-xl border cursor-pointer ${
                  restartKind === o.id
                    ? o.id === 'device'
                      ? 'border-rose-500/60 bg-rose-950/20'
                      : 'border-amber-500/60 bg-amber-950/20'
                    : 'border-slate-800 hover:border-slate-700'
                }`}
              >
                <input
                  type="radio"
                  name="rk"
                  checked={restartKind === o.id}
                  onChange={() => setRestartKind(o.id)}
                  className="mt-0.5"
                />
                <div>
                  <div className="font-semibold text-white flex items-center gap-1.5">
                    <o.icon className="w-3.5 h-3.5" /> {o.t}
                  </div>
                  <div className="text-[11px] text-slate-400">{o.d}</div>
                </div>
              </label>
            ))}
          </div>
        </ConfirmModal>
      )}
    </div>
  );
}

export { ONLINE };
