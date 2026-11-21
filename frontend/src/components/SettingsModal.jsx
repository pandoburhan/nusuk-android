import React, { useState, useEffect } from 'react';
import {
  Settings, Save, Sparkles, CheckCircle2, AlertCircle, Eye, EyeOff,
  Cpu, Globe, Zap, Archive, ChevronDown, ChevronUp, Server,
} from 'lucide-react';
import { settingsApi, instancesApi } from '../api/client';
import { fmtDateTime } from './common';

export default function SettingsModal({ isOpen, onClose, onSaved, timezone = 'wib' }) {
  const [formData, setFormData] = useState({
    ai_base_url: 'https://api.openai.com/v1',
    ai_api_key: '',
    ai_model: 'gpt-4o-mini',
    loading_timeout_seconds: 25,
    circuit_breaker_limit: 5,
    action_delay_seconds: 0.0,
    watchdog_interval_seconds: 7,
    preview_refresh_seconds: 4,
  });
  const [maskedKey, setMaskedKey] = useState('');
  const [hasKey, setHasKey] = useState(false);
  const [showKey, setShowKey] = useState(false);
  const [feedback, setFeedback] = useState(null);
  const [saving, setSaving] = useState(false);
  const [testingAi, setTestingAi] = useState(false);

  const [showArchived, setShowArchived] = useState(false);
  const [archivedList, setArchivedList] = useState([]);
  const [loadingArchived, setLoadingArchived] = useState(false);

  const presets = [
    { name: 'OpenAI', url: 'https://api.openai.com/v1', model: 'gpt-4o-mini', desc: 'Official OpenAI GPT models' },
    { name: 'Gemini (OpenAI-Compat)', url: 'https://generativelanguage.googleapis.com/v1beta/openai/', model: 'gemini-2.5-flash', desc: 'Google Gemini OpenAI-compatible' },
    { name: 'Gemini (Interactions)', url: 'https://generativelanguage.googleapis.com/v1beta/interactions', model: 'gemini-2.5-flash', desc: 'Google Gemini native Interactions API' },
    { name: 'DeepSeek', url: 'https://api.deepseek.com/v1', model: 'deepseek-chat', desc: 'DeepSeek official API' },
    { name: 'Groq', url: 'https://api.groq.com/openai/v1', model: 'llama-3.3-70b-versatile', desc: 'Ultra-fast inference' },
    { name: 'OpenRouter', url: 'https://openrouter.ai/api/v1', model: 'google/gemini-2.0-flash-001', desc: 'Unified multi-provider' },
    { name: 'Ollama (Local)', url: 'http://localhost:11434/v1', model: 'llama3.2', desc: 'Local offline LLM' },
  ];

  const applyPreset = (p) => {
    setFormData((prev) => ({ ...prev, ai_base_url: p.url, ai_model: p.model }));
    setFeedback({ type: 'info', text: `Preset '${p.name}' diterapkan: URL diatur ke ${p.url} dan model ${p.model}.` });
  };

  useEffect(() => {
    if (isOpen) {
      setShowArchived(false);
      settingsApi.getSettings().then((res) => {
        const d = res.data;
        setFormData({
          ai_base_url: d.ai_base_url || 'https://api.openai.com/v1',
          ai_api_key: '',
          ai_model: d.ai_model || 'gpt-4o-mini',
          loading_timeout_seconds: d.loading_timeout_seconds ?? 25,
          circuit_breaker_limit: d.circuit_breaker_limit ?? 5,
          action_delay_seconds: d.action_delay_seconds ?? 0.0,
          watchdog_interval_seconds: d.watchdog_interval_seconds ?? 7,
          preview_refresh_seconds: d.preview_refresh_seconds ?? 4,
        });
        setMaskedKey(d.ai_api_key_masked || '');
        setHasKey(d.has_ai_key);
        setShowKey(false);
        setFeedback(null);
      }).catch((err) => console.error('Load settings error:', err));
    }
  }, [isOpen]);

  const toggleArchived = async () => {
    const next = !showArchived;
    setShowArchived(next);
    if (next) {
      try {
        setLoadingArchived(true);
        const res = await instancesApi.archived();
        setArchivedList(res.data?.items || []);
      } catch {
        setArchivedList([]);
      } finally {
        setLoadingArchived(false);
      }
    }
  };

  if (!isOpen) return null;

  const handleSubmit = async (e) => {
    e.preventDefault();
    try {
      setSaving(true);
      setFeedback(null);
      const payload = { ...formData };
      if (!payload.ai_api_key) delete payload.ai_api_key;
      await settingsApi.updateSettings(payload);
      if (onSaved) onSaved();
      setFeedback({ type: 'success', text: 'Pengaturan AI dan sistem berhasil disimpan.' });
      setTimeout(() => onClose(), 900);
    } catch (err) {
      setFeedback({
        type: 'error',
        text: err.response?.data?.detail || 'Gagal menyimpan pengaturan.',
      });
    } finally {
      setSaving(false);
    }
  };

  const handleTestAi = async () => {
    try {
      setTestingAi(true);
      setFeedback(null);
      const res = await settingsApi.testAI({
        ai_base_url: formData.ai_base_url,
        ai_api_key: formData.ai_api_key || undefined,
        ai_model: formData.ai_model,
      });
      setFeedback({ type: 'success', text: res.data.message || 'Koneksi AI berhasil dan siap digunakan!' });
    } catch (err) {
      setFeedback({ type: 'error', text: err.response?.data?.detail || 'Uji coba koneksi AI gagal.' });
    } finally {
      setTestingAi(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 bg-slate-950/75 backdrop-blur-sm flex items-center justify-center p-4">
      <div className="bg-slate-900 border border-slate-800 rounded-2xl max-w-xl w-full p-6 shadow-2xl space-y-5 animate-fadeIn max-h-[90vh] overflow-y-auto">
        <div className="flex items-center justify-between border-b border-slate-800 pb-3">
          <div className="flex items-center gap-2">
            <Settings className="w-5 h-5 text-emerald-400" />
            <h2 className="text-sm font-semibold text-white">Konfigurasi Global &amp; Parameter Otomasi</h2>
          </div>
          <button onClick={onClose} className="text-slate-400 hover:text-white">✕</button>
        </div>

        {feedback && (
          <div
            className={`p-3 rounded-xl text-xs flex items-center gap-2 border ${
              feedback.type === 'success'
                ? 'bg-emerald-950/40 border-emerald-800/60 text-emerald-300'
                : feedback.type === 'error'
                  ? 'bg-rose-950/40 border-rose-800/60 text-rose-300'
                  : 'bg-sky-950/40 border-sky-800/60 text-sky-300'
            }`}
          >
            {feedback.type === 'success' ? (
              <CheckCircle2 className="w-4 h-4 shrink-0" />
            ) : feedback.type === 'error' ? (
              <AlertCircle className="w-4 h-4 shrink-0" />
            ) : (
              <Zap className="w-4 h-4 shrink-0 text-sky-400" />
            )}
            <span className="break-all">{feedback.text}</span>
          </div>
        )}

        <form onSubmit={handleSubmit} className="space-y-4 text-xs">
          {/* AI Provider Section */}
          <div className="bg-slate-950/70 border border-slate-800/90 rounded-xl p-4 space-y-3">
            <div className="flex items-center justify-between">
              <span className="font-semibold text-white flex items-center gap-1.5 text-xs">
                <Cpu className="w-4 h-4 text-sky-400" />
                AI Provider Settings
              </span>
              <span className="text-[10px] text-slate-500 font-mono">OpenAI, Gemini, Groq, DeepSeek, Ollama</span>
            </div>

            <div>
              <label className="block text-slate-400 text-[11px] font-medium mb-1.5">
                Quick Presets (Klik untuk mengisi URL &amp; Model):
              </label>
              <div className="flex flex-wrap gap-1.5">
                {presets.map((p) => (
                  <button
                    key={p.name}
                    type="button"
                    onClick={() => applyPreset(p)}
                    className="px-2.5 py-1 rounded-lg text-[11px] font-medium bg-slate-800 hover:bg-slate-700 text-slate-300 hover:text-white border border-slate-700/60 active:scale-95 transition"
                    title={p.desc}
                  >
                    {p.name}
                  </button>
                ))}
              </div>
            </div>

            <div>
              <label className="block text-slate-300 font-medium mb-1">AI API Base URL:</label>
              <div className="relative">
                <input
                  type="text"
                  value={formData.ai_base_url}
                  onChange={(e) => setFormData({ ...formData, ai_base_url: e.target.value })}
                  className="w-full bg-slate-900 border border-slate-800 rounded-xl pl-3.5 pr-8 py-2 text-white font-mono focus:outline-none focus:border-sky-500"
                />
                <Globe className="w-3.5 h-3.5 text-slate-500 absolute right-3 top-1/2 -translate-y-1/2" />
              </div>
            </div>

            <div>
              <div className="flex items-center justify-between mb-1">
                <label className="text-slate-300 font-medium">AI API Key:</label>
                {hasKey && <span className="text-[10px] text-emerald-400 font-mono">Tersimpan: {maskedKey}</span>}
              </div>
              <div className="relative">
                <input
                  type={showKey ? 'text' : 'password'}
                  value={formData.ai_api_key}
                  onChange={(e) => setFormData({ ...formData, ai_api_key: e.target.value })}
                  placeholder={hasKey ? 'Biarkan kosong untuk tetap menggunakan key tersimpan' : 'Masukkan API key provider Anda'}
                  className="w-full bg-slate-900 border border-slate-800 rounded-xl pl-3.5 pr-9 py-2 text-white font-mono focus:outline-none focus:border-sky-500"
                />
                <button
                  type="button"
                  onClick={() => setShowKey(!showKey)}
                  className="p-1 text-slate-500 hover:text-white absolute right-2.5 top-1/2 -translate-y-1/2"
                >
                  {showKey ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                </button>
              </div>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-3 gap-2.5 items-end">
              <div className="sm:col-span-2">
                <label className="block text-slate-300 font-medium mb-1">AI Model Name:</label>
                <input
                  type="text"
                  value={formData.ai_model}
                  onChange={(e) => setFormData({ ...formData, ai_model: e.target.value })}
                  className="w-full bg-slate-900 border border-slate-800 rounded-xl px-3.5 py-2 text-white font-mono focus:outline-none focus:border-sky-500"
                />
              </div>

              <div>
                <button
                  type="button"
                  onClick={handleTestAi}
                  disabled={testingAi}
                  className="w-full px-3 py-2 bg-sky-600/20 hover:bg-sky-600/30 text-sky-400 border border-sky-500/30 rounded-xl font-medium transition flex items-center justify-center gap-1.5 active:scale-95"
                >
                  <Sparkles className={`w-3.5 h-3.5 ${testingAi ? 'animate-spin' : ''}`} />
                  {testingAi ? 'Menguji...' : 'Test AI'}
                </button>
              </div>
            </div>
          </div>

          {/* Timing & Resilience Parameters */}
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
            <div>
              <label className="block text-slate-300 font-medium mb-1">Loading Timeout (s):</label>
              <input
                type="number"
                min="5"
                max="60"
                value={formData.loading_timeout_seconds}
                onChange={(e) => setFormData({ ...formData, loading_timeout_seconds: parseInt(e.target.value, 10) || 25 })}
                className="w-full bg-slate-950/70 border border-slate-800 rounded-xl px-3.5 py-2 text-white font-mono focus:outline-none focus:border-sky-500"
              />
            </div>

            <div>
              <label className="block text-slate-300 font-medium mb-1" title="Berlaku independen per instance">
                Circuit Breaker (per device):
              </label>
              <input
                type="number"
                min="1"
                max="20"
                value={formData.circuit_breaker_limit}
                onChange={(e) => setFormData({ ...formData, circuit_breaker_limit: parseInt(e.target.value, 10) || 5 })}
                className="w-full bg-slate-950/70 border border-slate-800 rounded-xl px-3.5 py-2 text-white font-mono focus:outline-none focus:border-sky-500"
              />
            </div>

            <div>
              <label
                className="block text-slate-300 font-medium mb-1"
                title="0 = Smart Wait penuh (tercepat). Naikkan ke 0.2–1.0s hanya jika emulator lambat."
              >
                Action Delay (s): <span className="text-[10px] text-emerald-400 font-normal">(0 = Smart Wait)</span>
              </label>
              <input
                type="number"
                step="0.1"
                min="0"
                max="5.0"
                value={formData.action_delay_seconds}
                onChange={(e) => {
                  const v = parseFloat(e.target.value);
                  setFormData({ ...formData, action_delay_seconds: Number.isNaN(v) ? 0 : Math.max(0, v) });
                }}
                className="w-full bg-slate-950/70 border border-slate-800 rounded-xl px-3.5 py-2 text-white font-mono focus:outline-none focus:border-sky-500"
              />
            </div>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div>
              <label className="block text-slate-300 font-medium mb-1">Watchdog Reconnect Interval (s):</label>
              <input
                type="number"
                min="3"
                max="60"
                value={formData.watchdog_interval_seconds}
                onChange={(e) => setFormData({ ...formData, watchdog_interval_seconds: parseInt(e.target.value, 10) || 7 })}
                className="w-full bg-slate-950/70 border border-slate-800 rounded-xl px-3.5 py-2 text-white font-mono focus:outline-none focus:border-sky-500"
              />
            </div>
            <div>
              <label className="block text-slate-300 font-medium mb-1">Live Preview Refresh Interval (s):</label>
              <input
                type="number"
                min="2"
                max="30"
                value={formData.preview_refresh_seconds}
                onChange={(e) => setFormData({ ...formData, preview_refresh_seconds: parseInt(e.target.value, 10) || 4 })}
                className="w-full bg-slate-950/70 border border-slate-800 rounded-xl px-3.5 py-2 text-white font-mono focus:outline-none focus:border-sky-500"
              />
            </div>
          </div>

          {/* Multi-Instance Info & Archived Devices */}
          <div className="bg-slate-950/60 border border-slate-800 rounded-xl p-3 space-y-2">
            <div className="flex items-start gap-2 text-[11px] text-slate-400">
              <Server className="w-4 h-4 text-emerald-400 shrink-0 mt-0.5" />
              <span>
                Perangkat emulator kini dikelola secara <strong>Multi-Instance</strong> melalui panel Command Dashboard
                (tombol <em>+ Tambah Instance</em> dan menu gear pada tiap kartu device).
              </span>
            </div>

            <button
              type="button"
              onClick={toggleArchived}
              className="flex items-center justify-between w-full pt-2 border-t border-slate-800/80 text-[11px] text-slate-300 hover:text-white"
            >
              <span className="flex items-center gap-1.5">
                <Archive className="w-3.5 h-3.5 text-slate-400" />
                Riwayat Perangkat Terhapus (Soft-Deleted / Arsip)
              </span>
              {showArchived ? <ChevronUp className="w-3.5 h-3.5" /> : <ChevronDown className="w-3.5 h-3.5" />}
            </button>

            {showArchived && (
              <div className="max-h-36 overflow-y-auto space-y-1.5 pt-1">
                {loadingArchived ? (
                  <div className="text-[11px] text-slate-500 py-2 text-center">Memuat arsip…</div>
                ) : archivedList.length === 0 ? (
                  <div className="text-[11px] text-slate-600 py-2 text-center">Tidak ada perangkat di arsip.</div>
                ) : (
                  archivedList.map((d) => (
                    <div key={d.uuid} className="bg-slate-900 border border-slate-800 rounded-lg px-2.5 py-1.5 text-[11px] flex items-center justify-between gap-2">
                      <div className="min-w-0">
                        <div className="text-slate-200 font-medium truncate">
                          {d.name} ({d.address || d.adb_serial || (d.ip ? `${d.ip}:${d.port}` : 'USB')})
                        </div>
                        <div className="text-[10px] text-slate-500 font-mono truncate">{d.uuid}</div>
                      </div>
                      <span className="text-[10px] text-slate-500 shrink-0">{fmtDateTime(d.deleted_at, timezone)}</span>
                    </div>
                  ))
                )}
              </div>
            )}
          </div>

          <div className="border-t border-slate-800 pt-4 flex justify-end gap-2.5">
            <button
              type="button"
              onClick={onClose}
              className="px-4 py-2 rounded-xl text-slate-400 hover:text-white hover:bg-slate-800 font-medium transition"
            >
              Batal
            </button>
            <button
              type="submit"
              disabled={saving}
              className="px-5 py-2 rounded-xl bg-emerald-600 hover:bg-emerald-500 text-white font-semibold transition flex items-center gap-1.5 shadow-lg shadow-emerald-600/20 active:scale-95"
            >
              <Save className="w-4 h-4" />
              {saving ? 'Menyimpan...' : 'Simpan Perubahan'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
