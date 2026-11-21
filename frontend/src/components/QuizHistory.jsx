import React, { useState, useEffect, useCallback } from 'react';
import { Award, CheckCircle2, XCircle, ChevronLeft, ChevronRight, Eye, RefreshCw } from 'lucide-react';
import { logsApi } from '../api/client';
import { DeviceChip, fmtDateTime } from './common';

const fmtDuration = (ms) => {
  if (ms === null || ms === undefined) return '—';
  if (ms < 1000) return `${ms} ms`;
  return `${(ms / 1000).toFixed(2)} dtk`;
};

export default function QuizHistory({ instances = [], refreshSignal, timezone = 'wib' }) {
  const [logs, setLogs] = useState([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [pageSize] = useState(20);
  const [resultFilter, setResultFilter] = useState('all');
  const [doorFilter, setDoorFilter] = useState('all');
  const [deviceFilter, setDeviceFilter] = useState('all');
  const [devices, setDevices] = useState([]);
  const [loading, setLoading] = useState(false);
  const [selectedQuiz, setSelectedQuiz] = useState(null);

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

  const fetchLogs = useCallback(async () => {
    try {
      setLoading(true);
      const res = await logsApi.getQuizLogs(page, pageSize, resultFilter, doorFilter, deviceFilter);
      setLogs(res.data.items || []);
      setTotal(res.data.total || 0);
    } catch (err) {
      console.error('Fetch quiz logs error:', err);
    } finally {
      setLoading(false);
    }
  }, [page, pageSize, resultFilter, doorFilter, deviceFilter]);

  useEffect(() => {
    fetchLogs();
  }, [fetchLogs, refreshSignal]);

  const totalPages = Math.ceil(total / pageSize) || 1;

  const parseOptions = (optionsJson) => {
    try {
      return JSON.parse(optionsJson);
    } catch {
      return [];
    }
  };

  return (
    <div className="bg-slate-900 border border-slate-800 rounded-2xl p-5 shadow-sm space-y-4">
      <div className="flex flex-col md:flex-row items-stretch md:items-center justify-between gap-3 border-b border-slate-800 pb-3">
        <div className="flex items-center gap-2">
          <Award className="w-5 h-5 text-teal-400" />
          <h2 className="text-sm font-semibold text-white">Riwayat Pertanyaan Kuis &amp; Jawaban AI</h2>
        </div>

        {/* Filter controls */}
        <div className="flex flex-wrap items-center gap-2 text-xs">
          <select
            value={deviceFilter}
            onChange={(e) => {
              setDeviceFilter(e.target.value);
              setPage(1);
            }}
            className="bg-slate-950 border border-slate-800 rounded-xl px-3 py-1.5 text-slate-300 focus:outline-none focus:border-teal-500 max-w-[210px]"
          >
            <option value="all">Semua Device</option>
            {devices.map((d) => (
              <option key={d.uuid} value={d.uuid}>
                {d.name}{d.is_deleted ? ' (Arsip)' : ''}
              </option>
            ))}
          </select>

          <select
            value={resultFilter}
            onChange={(e) => {
              setResultFilter(e.target.value);
              setPage(1);
            }}
            className="bg-slate-950 border border-slate-800 rounded-xl px-3 py-1.5 text-slate-300 focus:outline-none focus:border-teal-500"
          >
            <option value="all">Semua Hasil</option>
            <option value="correct">Hanya Benar (Correct)</option>
            <option value="incorrect">Hanya Salah (Incorrect)</option>
          </select>

          <select
            value={doorFilter}
            onChange={(e) => {
              setDoorFilter(e.target.value);
              setPage(1);
            }}
            className="bg-slate-950 border border-slate-800 rounded-xl px-3 py-1.5 text-slate-300 focus:outline-none focus:border-teal-500"
          >
            <option value="all">Semua Pintu</option>
            <option value="Prophet">The Prophet&apos;s</option>
            <option value="Quran">The Quran</option>
            <option value="Makkah">Makkah</option>
            <option value="Madinah">Madinah</option>
          </select>

          <button
            onClick={fetchLogs}
            className="p-1.5 rounded-xl bg-slate-950 border border-slate-800 text-slate-400 hover:text-white"
            title="Muat ulang"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
          </button>
        </div>
      </div>

      {/* Table */}
      <div className="border border-slate-800 rounded-xl overflow-x-auto">
        <table className="w-full text-left text-xs text-slate-300">
          <thead className="bg-slate-950/80 text-slate-400 uppercase text-[10px] tracking-wider border-b border-slate-800">
            <tr>
              <th className="py-3 px-4">Waktu</th>
              <th className="py-3 px-3">Device / Instance</th>
              <th className="py-3 px-3">Kategori Pintu</th>
              <th className="py-3 px-4">Pertanyaan</th>
              <th className="py-3 px-3">Pilihan Jawaban AI</th>
              <th className="py-3 px-3">Hasil</th>
              <th className="py-3 px-3 text-right">Total Durasi</th>
              <th className="py-3 px-3 text-right">Respon AI</th>
              <th className="py-3 px-3 text-center">Detail</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-800/60 font-sans">
            {logs.length > 0 ? (
              logs.map((log) => (
                <tr key={log.id} className="hover:bg-slate-800/30 transition">
                  <td className="py-3 px-4 text-slate-500 text-[11px] whitespace-nowrap">
                    {fmtDateTime(log.created_at, timezone)}
                  </td>
                  <td className="py-3 px-3 whitespace-nowrap">
                    <DeviceChip
                      name={log.device_name || log.device_id}
                      archived={!!log.device_archived}
                    />
                  </td>
                  <td className="py-3 px-3 font-medium text-slate-200 whitespace-nowrap">
                    {log.door_category}
                  </td>
                  <td className="py-3 px-4 max-w-xs truncate text-white" title={log.question_text}>
                    {log.question_text}
                  </td>
                  <td className="py-3 px-3 font-medium text-sky-400 max-w-[180px] truncate" title={log.ai_selected_answer || ''}>
                    {log.ai_selected_answer || '-'}
                  </td>
                  <td className="py-3 px-3">
                    {log.result_status === 'correct' ? (
                      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-semibold bg-emerald-500/10 text-emerald-400 border border-emerald-500/30">
                        <CheckCircle2 className="w-3 h-3" /> Correct
                      </span>
                    ) : (
                      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-semibold bg-rose-500/10 text-rose-400 border border-rose-500/30">
                        <XCircle className="w-3 h-3" /> Incorrect
                      </span>
                    )}
                  </td>
                  <td className="py-3 px-3 text-right font-mono text-cyan-300 text-[11px] whitespace-nowrap">
                    {fmtDuration(log.duration_ms)}
                  </td>
                  <td className="py-3 px-3 text-right font-mono text-[11px] whitespace-nowrap">
                    {log.ai_duration_ms === null || log.ai_duration_ms === undefined ? (
                      <span className="text-slate-600" title="Data v1 (tidak diukur)">—</span>
                    ) : (
                      <span className="text-violet-300">{fmtDuration(log.ai_duration_ms)}</span>
                    )}
                  </td>
                  <td className="py-3 px-3 text-center">
                    <button
                      onClick={() => setSelectedQuiz(log)}
                      className="p-1 text-slate-400 hover:text-white hover:bg-slate-800 rounded transition"
                      title="Lihat detail soal & opsi"
                    >
                      <Eye className="w-3.5 h-3.5" />
                    </button>
                  </td>
                </tr>
              ))
            ) : (
              <tr>
                <td colSpan={9} className="py-8 text-center text-slate-500">
                  {loading ? 'Memuat riwayat kuis...' : 'Belum ada riwayat kuis.'}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {/* Pagination */}
      <div className="flex items-center justify-between text-xs text-slate-400 pt-2">
        <span>Total: <strong className="text-white">{total}</strong> soal</span>
        <div className="flex items-center gap-1">
          <button
            onClick={() => setPage((p) => Math.max(1, p - 1))}
            disabled={page === 1}
            className="p-1.5 rounded-lg border border-slate-800 bg-slate-950 hover:bg-slate-800 disabled:opacity-40"
          >
            <ChevronLeft className="w-4 h-4" />
          </button>
          <span className="px-3 font-mono">{page} / {totalPages}</span>
          <button
            onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
            disabled={page === totalPages}
            className="p-1.5 rounded-lg border border-slate-800 bg-slate-950 hover:bg-slate-800 disabled:opacity-40"
          >
            <ChevronRight className="w-4 h-4" />
          </button>
        </div>
      </div>

      {/* Modal Detail Soal */}
      {selectedQuiz && (
        <div className="fixed inset-0 z-50 bg-slate-950/70 backdrop-blur-sm flex items-center justify-center p-4">
          <div className="bg-slate-900 border border-slate-800 rounded-2xl max-w-lg w-full p-6 shadow-2xl space-y-4 animate-fadeIn max-h-[90vh] overflow-y-auto">
            <div className="flex items-center justify-between border-b border-slate-800 pb-3">
              <span className="text-xs font-semibold text-teal-400 uppercase tracking-wider">
                Detail Soal Kuis — {selectedQuiz.door_category}
              </span>
              <button onClick={() => setSelectedQuiz(null)} className="text-slate-400 hover:text-white">✕</button>
            </div>

            <div className="grid grid-cols-2 gap-2 text-[11px] bg-slate-950/70 border border-slate-800 rounded-xl p-3">
              <div>
                <div className="text-slate-500">Instance / Device</div>
                <div className="text-white font-medium">
                  {selectedQuiz.device_name || selectedQuiz.device_id}
                  {selectedQuiz.device_archived ? ' (Arsip)' : ''}
                </div>
                {selectedQuiz.device_uuid && (
                  <div className="text-[10px] text-slate-500 font-mono truncate" title={selectedQuiz.device_uuid}>
                    {selectedQuiz.device_uuid}
                  </div>
                )}
              </div>
              <div>
                <div className="text-slate-500">Waktu</div>
                <div className="text-slate-200">{fmtDateTime(selectedQuiz.created_at, timezone)}</div>
                <div className="text-[10px] text-slate-500">Mode: {selectedQuiz.mode}</div>
              </div>
              <div>
                <div className="text-slate-500">Total Durasi Siklus</div>
                <div className="text-cyan-300 font-mono font-semibold">
                  {fmtDuration(selectedQuiz.duration_ms)} ({selectedQuiz.duration_ms} ms)
                </div>
              </div>
              <div>
                <div className="text-slate-500">Durasi Respon API AI</div>
                <div className="text-violet-300 font-mono font-semibold">
                  {selectedQuiz.ai_duration_ms === null || selectedQuiz.ai_duration_ms === undefined
                    ? '— (Legacy v1)'
                    : `${fmtDuration(selectedQuiz.ai_duration_ms)} (${selectedQuiz.ai_duration_ms} ms)`}
                </div>
              </div>
            </div>

            <div>
              <label className="text-[11px] font-medium text-slate-400">Pertanyaan Lengkap:</label>
              <p className="text-sm font-semibold text-white mt-1 leading-relaxed">
                {selectedQuiz.question_text}
              </p>
            </div>

            <div>
              <label className="text-[11px] font-medium text-slate-400 mb-2 block">Daftar Opsi Jawaban:</label>
              <div className="space-y-2">
                {parseOptions(selectedQuiz.options_json).map((opt, idx) => {
                  const isSelected = selectedQuiz.ai_selected_answer === opt;
                  return (
                    <div
                      key={idx}
                      className={`p-2.5 rounded-xl text-xs border flex items-center justify-between ${
                        isSelected
                          ? selectedQuiz.result_status === 'correct'
                            ? 'bg-emerald-950/30 border-emerald-500/50 text-emerald-200'
                            : 'bg-rose-950/30 border-rose-500/50 text-rose-200'
                          : 'bg-slate-950/50 border-slate-800 text-slate-300'
                      }`}
                    >
                      <span>{opt}</span>
                      {isSelected && (
                        <span className="text-[10px] font-bold uppercase tracking-wider">
                          Pilihan AI ({selectedQuiz.result_status})
                        </span>
                      )}
                    </div>
                  );
                })}
              </div>
            </div>

            <div className="pt-3 border-t border-slate-800 text-right">
              <button
                onClick={() => setSelectedQuiz(null)}
                className="px-4 py-2 bg-slate-800 hover:bg-slate-700 text-white text-xs font-medium rounded-xl transition"
              >
                Tutup
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
