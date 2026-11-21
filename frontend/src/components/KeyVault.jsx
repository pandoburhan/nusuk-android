import React, { useState, useEffect, useCallback } from 'react';
import {
  KeyRound, Plus, Upload, Trash2, RotateCcw, Copy, Check, Download,
  Search, CheckCircle2, ChevronLeft, ChevronRight, Eye, X, ShieldBan,
} from 'lucide-react';
import { keysApi, errorDetail } from '../api/client';
import { ConfirmModal, fmtDateTime, DeviceChip } from './common';

const MASTER_STATUSES = [
  ['all', 'Semua Status'],
  ['unused_pure', 'Unused Murni'],
  ['claimed', 'Claimed (dipakai ≥1 device)'],
  ['blacklisted', 'Blacklisted'],
];
const DEVICE_STATUSES = [
  ['all', 'Semua Status'],
  ['available', 'Available'],
  ['in_progress', 'In Progress'],
  ['used', 'Used'],
  ['invalid', 'Invalid'],
];

const badge = (status) => {
  switch (status) {
    case 'unused_pure':
    case 'available':
      return 'bg-sky-500/10 text-sky-400 border-sky-500/30';
    case 'claimed':
    case 'used':
      return 'bg-emerald-500/10 text-emerald-400 border-emerald-500/30';
    case 'in_progress':
      return 'bg-amber-500/10 text-amber-400 border-amber-500/30 animate-pulse';
    case 'blacklisted':
    case 'invalid':
      return 'bg-rose-500/10 text-rose-400 border-rose-500/30';
    default:
      return 'bg-slate-800 text-slate-400 border-slate-700';
  }
};

const fmtMs = (ms) => (ms === null || ms === undefined ? '—' : `${(ms / 1000).toFixed(1)}s`);

export default function KeyVault({ onKeysUpdated, instances = [], refreshSignal, timezone = 'wib' }) {
  const [activeTab, setActiveTab] = useState('manual');
  const [singleCode, setSingleCode] = useState('');
  const [bulkText, setBulkText] = useState('');
  const [importSummary, setImportSummary] = useState(null);
  const [isImporting, setIsImporting] = useState(false);
  const [feedback, setFeedback] = useState(null);

  // Table state
  const [keysList, setKeysList] = useState([]);
  const [totalKeys, setTotalKeys] = useState(0);
  const [summary, setSummary] = useState(null);
  const [page, setPage] = useState(1);
  const [pageSize] = useState(20);
  const [perspective, setPerspective] = useState(''); // '' = master
  const [statusFilter, setStatusFilter] = useState('all');
  const [searchQuery, setSearchQuery] = useState('');
  const [loadingTable, setLoadingTable] = useState(false);
  const [copiedId, setCopiedId] = useState(null);

  const [detail, setDetail] = useState(null); // {key, claims}
  const [detailLoading, setDetailLoading] = useState(false);
  const [confirm, setConfirm] = useState(null); // {title, body, action, danger}
  const [exportOpen, setExportOpen] = useState(false);
  const [exportDevice, setExportDevice] = useState('');

  const isMaster = !perspective;
  const perspectiveName = instances.find((i) => i.uuid === perspective)?.name;

  const nonBlankLinesCount = bulkText
    .split('\n')
    .map((l) => l.trim())
    .filter(Boolean).length;

  const fetchKeys = useCallback(async () => {
    try {
      setLoadingTable(true);
      const res = await keysApi.listKeys(page, pageSize, statusFilter, searchQuery, perspective);
      setKeysList(res.data.items);
      setTotalKeys(res.data.total);
      setSummary(res.data.summary || null);
    } catch (err) {
      console.error('Error fetching keys list:', err);
    } finally {
      setLoadingTable(false);
    }
  }, [page, pageSize, statusFilter, searchQuery, perspective]);

  useEffect(() => {
    fetchKeys();
  }, [fetchKeys, refreshSignal]);

  const after = () => {
    fetchKeys();
    if (onKeysUpdated) onKeysUpdated();
  };

  const handleAddManual = async (e) => {
    e.preventDefault();
    if (!singleCode.trim()) return;
    try {
      setFeedback(null);
      const res = await keysApi.addManual(singleCode);
      setFeedback({ type: 'success', text: res.data.message });
      setSingleCode('');
      after();
    } catch (err) {
      setFeedback({ type: 'error', text: errorDetail(err, 'Gagal menambahkan key.') });
    }
  };

  const handleAddBulk = async (e) => {
    e.preventDefault();
    if (!bulkText.trim() || isImporting) return;
    try {
      setIsImporting(true);
      setImportSummary(null);
      setFeedback(null);
      const res = await keysApi.addBulk(bulkText);
      setImportSummary(res.data);
      setBulkText('');
      after();
    } catch (err) {
      setFeedback({ type: 'error', text: errorDetail(err, 'Gagal mengimpor key bulk.') });
    } finally {
      setIsImporting(false);
    }
  };

  const doReset = async (id, deviceUuid = '') => {
    try {
      await keysApi.resetKey(id, deviceUuid);
      setFeedback({
        type: 'success',
        text: deviceUuid ? 'Klaim device berhasil direset.' : 'Blacklist key berhasil direset.',
      });
      after();
      if (detail && detail.key.id === id) openDetail(id);
    } catch (err) {
      setFeedback({ type: 'error', text: errorDetail(err, 'Gagal mereset key.') });
    }
  };

  const doDelete = async (id) => {
    try {
      await keysApi.deleteKey(id);
      setDetail(null);
      after();
    } catch (err) {
      setFeedback({ type: 'error', text: errorDetail(err, 'Gagal menghapus key.') });
    }
  };

  const handleClearBlacklisted = async () => {
    try {
      await keysApi.clearInvalid();
      after();
    } catch (err) {
      setFeedback({ type: 'error', text: errorDetail(err, 'Gagal membersihkan key.') });
    }
  };

  async function openDetail(id) {
    try {
      setDetailLoading(true);
      const res = await keysApi.getClaims(id);
      setDetail(res.data);
    } catch (err) {
      setFeedback({ type: 'error', text: errorDetail(err, 'Gagal memuat detail klaim.') });
    } finally {
      setDetailLoading(false);
    }
  }

  const copyToClipboard = (text, id) => {
    navigator.clipboard.writeText(text);
    setCopiedId(id);
    setTimeout(() => setCopiedId(null), 2000);
  };

  const doExport = async () => {
    try {
      const res = await keysApi.exportUnused(exportDevice);
      const blobUrl = URL.createObjectURL(res.data);
      const a = document.createElement('a');
      a.href = blobUrl;
      a.download = exportDevice ? `unused_${instances.find((i) => i.uuid === exportDevice)?.name || 'device'}.txt` : 'unused_keys.txt';
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(blobUrl), 1000);
      setExportOpen(false);
    } catch (err) {
      toast?.({ type: 'error', message: errorDetail(err, 'Gagal mengekspor key') });
    }
  };

  const rowStatus = (k) => {
    if (!isMaster) return k.device_status || 'available';
    if (k.is_blacklisted) return 'blacklisted';
    if (k.used_count > 0) return 'claimed';
    return 'unused_pure';
  };

  const totalPages = Math.ceil(totalKeys / pageSize) || 1;
  const statuses = isMaster ? MASTER_STATUSES : DEVICE_STATUSES;

  const askReset = (k) =>
    setConfirm({
      title: 'Reset Blacklist Key',
      body: `Reset global untuk ${k.code}? Blacklist dihapus dan SEMUA klaim berstatus invalid pada key ini akan dibersihkan, sehingga key dapat dicoba lagi oleh semua device.`,
      action: () => doReset(k.id),
    });
  const askResetDevice = (k, deviceUuid, deviceName) =>
    setConfirm({
      title: 'Reset Klaim Per-Device',
      body: `Hapus klaim (used/invalid) key ${k.code} untuk device ${deviceName}? Device tersebut dapat memakai key ini lagi.`,
      action: () => doReset(k.id, deviceUuid),
    });

  return (
    <div className="space-y-5">
      {/* Top Section: Input Key Baru */}
      <div className="bg-slate-900 border border-slate-800 rounded-2xl p-5 shadow-sm">
        <div className="flex items-center justify-between border-b border-slate-800 pb-3 mb-4">
          <div className="flex items-center gap-2">
            <KeyRound className="w-5 h-5 text-sky-400" />
            <h2 className="text-sm font-semibold text-white">Input Kode Key Baru ke Database</h2>
          </div>

          <div className="flex items-center gap-1 bg-slate-950 p-1 rounded-xl text-xs border border-slate-800">
            <button
              onClick={() => setActiveTab('manual')}
              className={`px-3 py-1 rounded-lg font-medium transition ${
                activeTab === 'manual'
                  ? 'bg-sky-600 text-white'
                  : 'text-slate-400 hover:text-white'
              }`}
            >
              Input Satuan (Manual)
            </button>
            <button
              onClick={() => setActiveTab('bulk')}
              className={`px-3 py-1 rounded-lg font-medium transition ${
                activeTab === 'bulk'
                  ? 'bg-sky-600 text-white'
                  : 'text-slate-400 hover:text-white'
              }`}
            >
              Input Massal (Bulk Newline)
            </button>
          </div>
        </div>

        {feedback && (
          <div
            className={`p-3 rounded-xl text-xs flex items-center justify-between mb-4 border ${
              feedback.type === 'success'
                ? 'bg-emerald-950/40 border-emerald-800/60 text-emerald-300'
                : 'bg-rose-950/40 border-rose-800/60 text-rose-300'
            }`}
          >
            <span>{feedback.text}</span>
            <button onClick={() => setFeedback(null)} className="text-slate-400 hover:text-white">✕</button>
          </div>
        )}

        {/* Tab 1: Manual Single Input */}
        {activeTab === 'manual' && (
          <form onSubmit={handleAddManual} className="flex flex-col sm:flex-row items-center gap-3">
            <input
              type="text"
              value={singleCode}
              onChange={(e) => setSingleCode(e.target.value)}
              placeholder="Contoh: NG-GRWTCmHz"
              className="flex-1 bg-slate-950 border border-slate-800 rounded-xl px-4 py-2.5 text-xs text-white placeholder-slate-500 focus:outline-none focus:border-sky-500 font-mono"
            />
            <button
              type="submit"
              className="w-full sm:w-auto px-5 py-2.5 bg-sky-600 hover:bg-sky-500 text-white text-xs font-semibold rounded-xl flex items-center justify-center gap-2 transition active:scale-95 shadow-md shadow-sky-600/20"
            >
              <Plus className="w-4 h-4" />
              Tambah Key
            </button>
          </form>
        )}

        {/* Tab 2: Bulk Input per Newline */}
        {activeTab === 'bulk' && (
          <form onSubmit={handleAddBulk} className="space-y-3">
            <textarea
              rows={5}
              disabled={isImporting}
              value={bulkText}
              onChange={(e) => setBulkText(e.target.value)}
              placeholder={"Paste daftar kode key di sini (1 kode per baris baru, mendukung ribuan key sekaligus):\nNG-AAA111\nNG-BBB222\nNG-CCC333"}
              className="w-full bg-slate-950 border border-slate-800 rounded-xl p-3.5 text-xs text-white placeholder-slate-500 focus:outline-none focus:border-sky-500 font-mono disabled:opacity-50"
            />
            <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3">
              <span className="text-xs text-slate-400">
                Terdeteksi: <strong className="text-sky-400 font-mono">{nonBlankLinesCount}</strong> baris siap diimpor
                {isImporting && <span className="ml-2 text-amber-400 animate-pulse font-medium">(Sedang memproses batch...)</span>}
              </span>

              <button
                type="submit"
                disabled={nonBlankLinesCount === 0 || isImporting}
                className={`px-5 py-2.5 text-xs font-semibold rounded-xl flex items-center gap-2 transition ${
                  nonBlankLinesCount > 0 && !isImporting
                    ? 'bg-sky-600 hover:bg-sky-500 text-white shadow-md shadow-sky-600/20 active:scale-95'
                    : 'bg-slate-800 text-slate-500 cursor-not-allowed border border-slate-700/50'
                }`}
              >
                {isImporting ? (
                  <>
                    <div className="w-3.5 h-3.5 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                    Mengimpor {nonBlankLinesCount} Keys...
                  </>
                ) : (
                  <>
                    <Upload className="w-4 h-4" />
                    Import Bulk Keys {nonBlankLinesCount > 0 ? `(${nonBlankLinesCount})` : ''}
                  </>
                )}
              </button>
            </div>
          </form>
        )}

        {/* Import Summary Card */}
        {importSummary && (
          <div className="mt-4 p-3.5 bg-slate-950 rounded-xl border border-sky-500/30 flex items-center justify-between text-xs animate-fadeIn">
            <div className="flex items-center gap-4">
              <CheckCircle2 className="w-5 h-5 text-emerald-400 shrink-0" />
              <div>
                <span className="font-semibold text-white">Laporan Hasil Impor Massal:</span>
                <div className="text-slate-400 mt-0.5">
                  Total Baris: <strong className="text-slate-200">{importSummary.total_lines}</strong> | 
                  Disimpan (Baru): <strong className="text-emerald-400">{importSummary.saved_count}</strong> | 
                  Dilewati (Duplikat/Kosong): <strong className="text-amber-400">{importSummary.duplicates_or_skipped}</strong>
                  {importSummary.invalid_count > 0 && (
                    <> | Format Tidak Valid: <strong className="text-rose-400">{importSummary.invalid_count}</strong></>
                  )}
                </div>
              </div>
            </div>
            <button onClick={() => setImportSummary(null)} className="text-slate-500 hover:text-white">✕</button>
          </div>
        )}
      </div>


      {/* Bottom Section: Key Inventory */}
      <div className="bg-slate-900 border border-slate-800 rounded-2xl p-5 shadow-sm space-y-4">
        {/* Summary */}
        {summary && isMaster && (
          <div className="grid grid-cols-2 md:grid-cols-4 gap-2.5 text-xs">
            {[
              ['Total Master Key', summary.total, 'text-white'],
              ['Unused Murni', summary.unused_pure, 'text-sky-400'],
              ['Claimed', summary.claimed, 'text-emerald-400'],
              ['Blacklisted', summary.blacklisted, 'text-rose-400'],
            ].map(([l, v, c]) => (
              <div key={l} className="bg-slate-950/60 border border-slate-800 rounded-xl px-3 py-2">
                <div className="text-[10px] text-slate-500">{l}</div>
                <div className={`text-lg font-bold ${c}`}>{v}</div>
              </div>
            ))}
          </div>
        )}

        <div className="flex flex-col lg:flex-row items-stretch lg:items-center justify-between gap-3">
          <div className="flex flex-wrap items-center gap-2 flex-1">
            <select
              value={perspective}
              onChange={(e) => {
                setPerspective(e.target.value);
                setStatusFilter('all');
                setPage(1);
              }}
              className="bg-slate-950 border border-slate-800 rounded-xl px-3 py-2 text-xs text-slate-300 focus:outline-none focus:border-sky-500 max-w-[200px]"
              title="Perspektif tampilan"
            >
              <option value="">Master (Semua Device)</option>
              {instances.map((i) => (
                <option key={i.uuid} value={i.uuid}>
                  Device: {i.name}
                </option>
              ))}
            </select>

            <div className="relative flex-1 min-w-[160px] max-w-xs">
              <Search className="w-4 h-4 text-slate-500 absolute left-3 top-1/2 -translate-y-1/2" />
              <input
                type="text"
                value={searchQuery}
                onChange={(e) => {
                  setSearchQuery(e.target.value);
                  setPage(1);
                }}
                placeholder="Cari kode key..."
                className="w-full bg-slate-950 border border-slate-800 rounded-xl pl-9 pr-3 py-2 text-xs text-white placeholder-slate-500 focus:outline-none focus:border-sky-500 font-mono"
              />
            </div>

            <select
              value={statusFilter}
              onChange={(e) => {
                setStatusFilter(e.target.value);
                setPage(1);
              }}
              className="bg-slate-950 border border-slate-800 rounded-xl px-3 py-2 text-xs text-slate-300 focus:outline-none focus:border-sky-500"
            >
              {statuses.map(([v, l]) => (
                <option key={v} value={v}>
                  {l}
                </option>
              ))}
            </select>
          </div>

          <div className="flex items-center gap-2 text-xs">
            <button
              onClick={() =>
                setConfirm({
                  title: 'Bersihkan Key Blacklisted',
                  danger: true,
                  body: 'Hapus permanen seluruh master key yang berstatus blacklisted dari database?',
                  action: handleClearBlacklisted,
                })
              }
              className="px-3 py-2 bg-rose-950/30 hover:bg-rose-950/50 text-rose-400 border border-rose-800/40 rounded-xl transition flex items-center gap-1.5"
              title="Hapus semua master key yang ter-blacklist"
            >
              <Trash2 className="w-3.5 h-3.5" />
              Bersihkan Blacklisted
            </button>
            <button
              onClick={() => {
                setExportDevice(perspective);
                setExportOpen(true);
              }}
              className="px-3 py-2 bg-slate-800 hover:bg-slate-700 text-slate-300 border border-slate-700 rounded-xl transition flex items-center gap-1.5"
            >
              <Download className="w-3.5 h-3.5" />
              Export Unused (.txt)
            </button>
          </div>
        </div>

        {!isMaster && (
          <div className="text-[11px] text-slate-500">
            Perspektif device: <DeviceChip name={perspectiveName || '—'} />
          </div>
        )}

        <div className="border border-slate-800 rounded-xl overflow-auto max-h-[560px]">
          <table className="w-full text-left text-xs text-slate-300">
            <thead className="bg-slate-950 text-slate-400 uppercase text-[10px] tracking-wider border-b border-slate-800 sticky top-0 z-10">
              <tr>
                <th className="py-3 px-4">Kode Key</th>
                <th className="py-3 px-3">Status</th>
                <th className="py-3 px-3">Sumber</th>
                {isMaster ? (
                  <>
                    <th className="py-3 px-3">Dipakai</th>
                    <th className="py-3 px-3">Blacklist Oleh</th>
                  </>
                ) : (
                  <>
                    <th className="py-3 px-3">Durasi</th>
                    <th className="py-3 px-3">Catatan / Error</th>
                  </>
                )}
                <th className="py-3 px-3">Dibuat</th>
                <th className="py-3 px-4 text-right">Aksi</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800/60">
              {keysList.length > 0 ? (
                keysList.map((k) => {
                  const st = rowStatus(k);
                  return (
                    <tr key={k.id} className="hover:bg-slate-800/30 transition">
                      <td className="py-2.5 px-4 font-mono font-medium text-white">
                        <div className="flex items-center gap-2">
                          <span>{k.code}</span>
                          <button
                            onClick={() => copyToClipboard(k.code, k.id)}
                            className="text-slate-500 hover:text-white transition"
                            title="Copy code"
                          >
                            {copiedId === k.id ? (
                              <Check className="w-3.5 h-3.5 text-emerald-400" />
                            ) : (
                              <Copy className="w-3.5 h-3.5" />
                            )}
                          </button>
                        </div>
                      </td>
                      <td className="py-2.5 px-3">
                        <span className={`px-2 py-0.5 rounded-full text-[10px] font-semibold border ${badge(st)}`}>
                          {st}
                        </span>
                      </td>
                      <td className="py-2.5 px-3 text-slate-400 capitalize">{k.source}</td>
                      {isMaster ? (
                        <>
                          <td className="py-2.5 px-3 font-mono text-[11px] text-slate-400">
                            {k.used_count}/{k.total_devices}
                          </td>
                          <td className="py-2.5 px-3 text-[11px] text-slate-400 max-w-[200px] truncate" title={k.blacklist_reason || ''}>
                            {k.is_blacklisted ? (
                              <span className="text-rose-300">
                                {k.blacklisted_by_name || '—'}
                                {k.blacklist_reason ? ` · ${k.blacklist_reason}` : ''}
                              </span>
                            ) : (
                              '—'
                            )}
                          </td>
                        </>
                      ) : (
                        <>
                          <td className="py-2.5 px-3 font-mono text-[11px] text-slate-400">{fmtMs(k.device_duration_ms)}</td>
                          <td className="py-2.5 px-3 text-[11px] text-slate-400 max-w-[220px] truncate" title={k.device_error_note || ''}>
                            {k.device_error_note || '—'}
                          </td>
                        </>
                      )}
                      <td className="py-2.5 px-3 text-slate-500 text-[11px] whitespace-nowrap">{fmtDateTime(k.created_at, timezone)}</td>
                      <td className="py-2.5 px-4 text-right whitespace-nowrap space-x-1">
                        <button
                          onClick={() => openDetail(k.id)}
                          className="p-1 text-slate-400 hover:text-white hover:bg-slate-800 rounded transition"
                          title="Detail klaim per device"
                        >
                          <Eye className="w-3.5 h-3.5" />
                        </button>
                        {isMaster && k.is_blacklisted && (
                          <button
                            onClick={() => askReset(k)}
                            className="p-1 text-amber-400 hover:text-amber-300 hover:bg-amber-500/10 rounded transition"
                            title="Reset blacklist (global)"
                          >
                            <RotateCcw className="w-3.5 h-3.5" />
                          </button>
                        )}
                        {!isMaster && (k.device_status === 'used' || k.device_status === 'invalid') && (
                          <button
                            onClick={() => askResetDevice(k, perspective, perspectiveName)}
                            className="p-1 text-amber-400 hover:text-amber-300 hover:bg-amber-500/10 rounded transition"
                            title="Reset klaim device ini"
                          >
                            <RotateCcw className="w-3.5 h-3.5" />
                          </button>
                        )}
                        <button
                          onClick={() =>
                            setConfirm({
                              title: 'Hapus Key',
                              danger: true,
                              body: `Hapus ${k.code} dari database beserta seluruh riwayat klaimnya?`,
                              action: () => doDelete(k.id),
                            })
                          }
                          className="p-1 text-rose-400 hover:text-rose-300 hover:bg-rose-500/10 rounded transition"
                          title="Hapus key dari database"
                        >
                          <Trash2 className="w-3.5 h-3.5" />
                        </button>
                      </td>
                    </tr>
                  );
                })
              ) : (
                <tr>
                  <td colSpan={7} className="py-8 text-center text-slate-500">
                    {loadingTable ? 'Memuat data key...' : 'Tidak ada key yang sesuai filter.'}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>

        <div className="flex items-center justify-between text-xs text-slate-400 pt-2">
          <span>
            Menampilkan <strong className="text-white">{keysList.length}</strong> dari{' '}
            <strong className="text-white">{totalKeys}</strong> key
          </span>
          <div className="flex items-center gap-1">
            <button
              onClick={() => setPage((p) => Math.max(1, p - 1))}
              disabled={page === 1}
              className="p-1.5 rounded-lg border border-slate-800 bg-slate-950 hover:bg-slate-800 disabled:opacity-40 disabled:cursor-not-allowed transition"
            >
              <ChevronLeft className="w-4 h-4" />
            </button>
            <span className="px-3 font-mono text-slate-200">
              {page} / {totalPages}
            </span>
            <button
              onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
              disabled={page === totalPages}
              className="p-1.5 rounded-lg border border-slate-800 bg-slate-950 hover:bg-slate-800 disabled:opacity-40 disabled:cursor-not-allowed transition"
            >
              <ChevronRight className="w-4 h-4" />
            </button>
          </div>
        </div>
      </div>

      {/* Claim detail modal */}
      {(detail || detailLoading) && (
        <div className="fixed inset-0 z-50 bg-slate-950/75 backdrop-blur-sm flex items-center justify-center p-4">
          <div className="bg-slate-900 border border-slate-800 rounded-2xl max-w-2xl w-full p-5 shadow-2xl space-y-4 max-h-[90vh] flex flex-col">
            <div className="flex items-center justify-between border-b border-slate-800 pb-3">
              <h3 className="text-sm font-semibold text-white flex items-center gap-2">
                <KeyRound className="w-4 h-4 text-sky-400" />
                Detail Klaim — <span className="font-mono">{detail?.key?.code || '…'}</span>
              </h3>
              <button onClick={() => setDetail(null)} className="text-slate-400 hover:text-white">
                <X className="w-4 h-4" />
              </button>
            </div>
            {detail && (
              <>
                {detail.key.is_blacklisted && (
                  <div className="p-2.5 rounded-xl bg-rose-950/30 border border-rose-800/50 text-[11px] text-rose-300 flex items-center justify-between gap-2">
                    <span className="flex items-center gap-1.5">
                      <ShieldBan className="w-3.5 h-3.5" />
                      Blacklisted oleh <strong>{detail.key.blacklisted_by_name || '—'}</strong>
                      {detail.key.blacklist_reason ? ` · ${detail.key.blacklist_reason}` : ''}
                    </span>
                    <button
                      onClick={() => askReset(detail.key)}
                      className="px-2 py-1 bg-amber-600/20 hover:bg-amber-600/30 text-amber-300 border border-amber-500/30 rounded-lg shrink-0"
                    >
                      Reset Blacklist
                    </button>
                  </div>
                )}
                <div className="overflow-auto border border-slate-800 rounded-xl flex-1 min-h-0">
                  <table className="w-full text-left text-xs">
                    <thead className="bg-slate-950 text-slate-400 uppercase text-[10px] sticky top-0">
                      <tr>
                        <th className="py-2 px-3">Device</th>
                        <th className="py-2 px-3">Status</th>
                        <th className="py-2 px-3">Durasi</th>
                        <th className="py-2 px-3">Catatan</th>
                        <th className="py-2 px-3">Waktu</th>
                        <th className="py-2 px-3 text-right">Aksi</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-800/60">
                      {detail.claims.length === 0 && (
                        <tr>
                          <td colSpan={6} className="py-6 text-center text-slate-500">
                            Belum ada device yang memakai key ini.
                          </td>
                        </tr>
                      )}
                      {detail.claims.map((c) => (
                        <tr key={`${c.device_uuid}-${c.created_at}`}>
                          <td className="py-2 px-3 text-slate-200">
                            {c.device_name}
                            {c.is_blacklist_source && (
                              <span className="ml-1.5 text-[9px] px-1 rounded bg-rose-500/10 text-rose-400 border border-rose-500/30">
                                sumber blacklist
                              </span>
                            )}
                          </td>
                          <td className="py-2 px-3">
                            <span className={`px-2 py-0.5 rounded-full text-[10px] font-semibold border ${badge(c.status)}`}>
                              {c.status}
                            </span>
                          </td>
                          <td className="py-2 px-3 font-mono text-[11px] text-slate-400">{fmtMs(c.duration_ms)}</td>
                          <td className="py-2 px-3 text-[11px] text-slate-400 max-w-[180px] truncate" title={c.error_note || ''}>
                            {c.error_note || '—'}
                          </td>
                          <td className="py-2 px-3 text-[11px] text-slate-500 whitespace-nowrap">
                            {fmtDateTime(c.completed_at || c.created_at, timezone)}
                          </td>
                          <td className="py-2 px-3 text-right">
                            {c.status !== 'in_progress' && (
                              <button
                                onClick={() => askResetDevice(detail.key, c.device_uuid, c.device_name)}
                                className="p-1 text-amber-400 hover:bg-amber-500/10 rounded"
                                title="Reset klaim device ini"
                              >
                                <RotateCcw className="w-3.5 h-3.5" />
                              </button>
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </>
            )}
            {detailLoading && !detail && <div className="text-xs text-slate-500 py-6 text-center">Memuat…</div>}
          </div>
        </div>
      )}

      {/* Export choice modal */}
      {exportOpen && (
        <ConfirmModal
          title="Export Key Unused"
          confirmLabel="Download .txt"
          onCancel={() => setExportOpen(false)}
          onConfirm={doExport}
        >
          <p>Pilih jenis export:</p>
          <label className="flex items-start gap-2 p-2 rounded-lg border border-slate-800 cursor-pointer">
            <input type="radio" checked={exportDevice === ''} onChange={() => setExportDevice('')} className="mt-0.5" />
            <span>
              <strong className="text-white">Global</strong> — hanya key unused murni (belum dipakai device manapun &amp;
              tidak blacklist)
            </span>
          </label>
          <label className="flex items-start gap-2 p-2 rounded-lg border border-slate-800 cursor-pointer">
            <input
              type="radio"
              checked={exportDevice !== ''}
              onChange={() => setExportDevice(exportDevice || instances[0]?.uuid || '')}
              disabled={instances.length === 0}
              className="mt-0.5"
            />
            <span className="flex-1">
              <strong className="text-white">Per Device</strong> — key yang belum dipakai device tertentu
              <select
                value={exportDevice}
                onChange={(e) => setExportDevice(e.target.value)}
                className="mt-1.5 w-full bg-slate-950 border border-slate-800 rounded-lg px-2 py-1.5 text-slate-300"
              >
                <option value="">— pilih device —</option>
                {instances.map((i) => (
                  <option key={i.uuid} value={i.uuid}>
                    {i.name} ({i.address})
                  </option>
                ))}
              </select>
            </span>
          </label>
        </ConfirmModal>
      )}

      {confirm && (
        <ConfirmModal
          title={confirm.title}
          danger={confirm.danger}
          onCancel={() => setConfirm(null)}
          onConfirm={() => {
            const a = confirm.action;
            setConfirm(null);
            a();
          }}
        >
          <p>{confirm.body}</p>
        </ConfirmModal>
      )}
    </div>
  );
}
