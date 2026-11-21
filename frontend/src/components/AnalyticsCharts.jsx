import React, { useState, useEffect } from 'react';
import {
  AreaChart, Area, BarChart, Bar, PieChart, Pie, Cell,
  XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Legend
} from 'recharts';
import { BarChart3, TrendingUp, PieChart as PieIcon, Layers } from 'lucide-react';
import { analyticsApi } from '../api/client';
import { getTzMeta } from './common';

const STATUS_COLORS = {
  unused_pure: '#38bdf8',
  used_partial: '#22c55e',
  blacklisted: '#f43f5e',
  available: '#38bdf8',
  used: '#22c55e',
  invalid: '#f43f5e',
  in_progress: '#f59e0b',
};

export default function AnalyticsCharts({ instances = [], refreshSignal, timezone = 'wib' }) {
  const [timeframe, setTimeframe] = useState('24h');
  const [deviceUuid, setDeviceUuid] = useState('all');
  const [chartData, setChartData] = useState(null);
  const [loading, setLoading] = useState(false);
  const tzMeta = getTzMeta(timezone);

  const fetchCharts = async (tf, dev, tz) => {
    try {
      setLoading(true);
      const res = await analyticsApi.getCharts(tf, dev, tz);
      setChartData(res.data);
    } catch (err) {
      console.error('Fetch charts error:', err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchCharts(timeframe, deviceUuid, timezone);
  }, [timeframe, deviceUuid, timezone, refreshSignal]);

  const vol = chartData?.quiz_volume;

  return (
    <div className="space-y-4">
      {/* Timeframe Filter Bar */}
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-800 pb-3">
        <div className="flex items-center gap-2">
          <BarChart3 className="w-5 h-5 text-emerald-400" />
          <h2 className="text-sm font-semibold text-white">Analitik &amp; Visualisasi Performa</h2>
          <span className="text-[10px] px-2 py-0.5 rounded-full bg-slate-900 border border-slate-700 text-emerald-400 font-mono">
            {tzMeta.label}
          </span>
        </div>

        <div className="flex flex-wrap items-center gap-2">
        <select
          value={deviceUuid}
          onChange={(e) => setDeviceUuid(e.target.value)}
          className="bg-slate-900 border border-slate-800 rounded-xl px-2.5 py-1.5 text-xs text-slate-300 focus:outline-none focus:border-emerald-500 max-w-[160px]"
        >
          <option value="all">Semua Device</option>
          {instances.map((i) => (
            <option key={i.uuid} value={i.uuid}>{i.name}</option>
          ))}
        </select>
        <div className="flex items-center gap-1 bg-slate-900 border border-slate-800 p-1 rounded-xl text-xs">
          {[
            ['today', 'HARI INI'],
            ['24h', '24H'],
            ['7d', '7D'],
            ['30d', '30D'],
            ['all', 'ALL'],
          ].map(([tf, lbl]) => (
            <button
              key={tf}
              onClick={() => setTimeframe(tf)}
              className={`px-2.5 py-1 rounded-lg font-medium transition ${
                timeframe === tf
                  ? 'bg-emerald-600 text-white shadow'
                  : 'text-slate-400 hover:text-white'
              }`}
            >
              {lbl}
            </button>
          ))}
        </div>
        </div>
      </div>

      {/* Volume Summary Strip (Today / 7d / 30d / All for selected scope & timezone) */}
      {vol && (
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-2.5 text-xs">
          <div className="bg-slate-900 border border-slate-800 rounded-xl px-3.5 py-2.5 flex items-center justify-between">
            <span className="text-slate-400">Quiz Hari Ini ({tzMeta.short})</span>
            <strong className="text-base font-bold text-emerald-400 font-mono">{vol.today}</strong>
          </div>
          <div className="bg-slate-900 border border-slate-800 rounded-xl px-3.5 py-2.5 flex items-center justify-between">
            <span className="text-slate-400">Quiz 7 Hari ({tzMeta.short})</span>
            <strong className="text-base font-bold text-sky-400 font-mono">{vol.d7}</strong>
          </div>
          <div className="bg-slate-900 border border-slate-800 rounded-xl px-3.5 py-2.5 flex items-center justify-between">
            <span className="text-slate-400">Quiz 30 Hari ({tzMeta.short})</span>
            <strong className="text-base font-bold text-violet-400 font-mono">{vol.d30}</strong>
          </div>
          <div className="bg-slate-900 border border-slate-800 rounded-xl px-3.5 py-2.5 flex items-center justify-between">
            <span className="text-slate-400">Total Sepanjang Waktu</span>
            <strong className="text-base font-bold text-white font-mono">{vol.all}</strong>
          </div>
        </div>
      )}

      {/* Grid of Charts */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {/* 1. Quiz Trend Chart */}
        <div className="bg-slate-900 border border-slate-800 rounded-2xl p-4 shadow-sm flex flex-col justify-between">
          <div className="flex items-center justify-between mb-3">
            <span className="text-xs font-semibold text-white flex items-center gap-2">
              <TrendingUp className="w-4 h-4 text-emerald-400" />
              Tren Soal Kuis & Akurasi
            </span>
            <span className="text-[11px] text-slate-500">Total vs Correct</span>
          </div>

          <div className="h-64 min-h-[260px] w-full min-w-0">
            {chartData?.quiz_trend?.length > 0 ? (
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={chartData.quiz_trend}>
                  <defs>
                    <linearGradient id="colorTotal" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor="#38bdf8" stopOpacity={0.3}/>
                      <stop offset="95%" stopColor="#38bdf8" stopOpacity={0}/>
                    </linearGradient>
                    <linearGradient id="colorCorrect" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor="#22c55e" stopOpacity={0.4}/>
                      <stop offset="95%" stopColor="#22c55e" stopOpacity={0}/>
                    </linearGradient>
                  </defs>
                  <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
                  <XAxis dataKey="time" stroke="#64748b" fontSize={11} />
                  <YAxis stroke="#64748b" fontSize={11} />
                  <Tooltip
                    contentStyle={{ backgroundColor: '#0f172a', borderColor: '#334155', borderRadius: '8px', fontSize: '12px' }}
                    formatter={(value, name, item) => {
                      if (name === 'Total Soal') {
                        const p = item.payload;
                        const dur = p.avg_duration_sec
                          ? ` (Avg: ${p.avg_duration_sec}s${p.avg_ai_duration_sec != null ? `, AI: ${p.avg_ai_duration_sec}s` : ''})`
                          : '';
                        return [`${value}${dur}`, name];
                      }
                      return [value, name];
                    }}
                  />
                  <Legend wrapperStyle={{ fontSize: '11px', paddingTop: '10px' }} />
                  <Area type="monotone" dataKey="total" stroke="#38bdf8" name="Total Soal" fillOpacity={1} fill="url(#colorTotal)" />
                  <Area type="monotone" dataKey="correct" stroke="#22c55e" name="Jawaban Benar" fillOpacity={1} fill="url(#colorCorrect)" />
                </AreaChart>
              </ResponsiveContainer>
            ) : (
              <div className="h-full flex items-center justify-center text-xs text-slate-500">
                Belum ada data kuis pada rentang ini
              </div>
            )}
          </div>
        </div>

        {/* 2. Redeem Activity Chart */}
        <div className="bg-slate-900 border border-slate-800 rounded-2xl p-4 shadow-sm flex flex-col justify-between">
          <div className="flex items-center justify-between mb-3">
            <span className="text-xs font-semibold text-white flex items-center gap-2">
              <Layers className="w-4 h-4 text-sky-400" />
              Laju Pengisian Key (Used vs Invalid)
            </span>
            <span className="text-[11px] text-slate-500">Stacked Bar</span>
          </div>

          <div className="h-64 min-h-[260px] w-full min-w-0">
            {chartData?.redeem_trend?.length > 0 ? (
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={chartData.redeem_trend}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
                  <XAxis dataKey="time" stroke="#64748b" fontSize={11} />
                  <YAxis stroke="#64748b" fontSize={11} />
                  <Tooltip contentStyle={{ backgroundColor: '#0f172a', borderColor: '#334155', borderRadius: '8px', fontSize: '12px' }} />
                  <Legend wrapperStyle={{ fontSize: '11px', paddingTop: '10px' }} />
                  <Bar dataKey="used" name="Sukses (Used)" fill="#22c55e" stackId="a" radius={[0, 0, 4, 4]} />
                  <Bar dataKey="invalid" name="Gagal (Invalid)" fill="#f43f5e" stackId="a" radius={[4, 4, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            ) : (
              <div className="h-full flex items-center justify-center text-xs text-slate-500">
                Belum ada aktivitas penukaran key pada rentang ini
              </div>
            )}
          </div>
        </div>

        {/* 3. Key Stock Donut Chart */}
        <div className="bg-slate-900 border border-slate-800 rounded-2xl p-4 shadow-sm flex flex-col justify-between">
          <div className="flex items-center justify-between mb-3">
            <span className="text-xs font-semibold text-white flex items-center gap-2">
              <PieIcon className="w-4 h-4 text-teal-400" />
              Proporsi Persediaan Key Database
            </span>
            <span className="text-[11px] text-slate-500">Status Breakdown</span>
          </div>

          <div className="h-64 min-h-[260px] w-full min-w-0 flex items-center justify-center">
            {chartData?.key_distribution?.some((d) => d.count > 0) ? (
              <ResponsiveContainer width="100%" height="100%">
                <PieChart>
                  <Pie
                    data={chartData.key_distribution}
                    dataKey="count"
                    nameKey="status"
                    cx="50%"
                    cy="50%"
                    innerRadius={55}
                    outerRadius={85}
                    paddingAngle={3}
                  >
                    {chartData.key_distribution.map((entry, index) => (
                      <Cell key={`cell-${index}`} fill={STATUS_COLORS[entry.status] || '#64748b'} />
                    ))}
                  </Pie>
                  <Tooltip contentStyle={{ backgroundColor: '#0f172a', borderColor: '#334155', borderRadius: '8px', fontSize: '12px' }} />
                  <Legend wrapperStyle={{ fontSize: '11px' }} />
                </PieChart>
              </ResponsiveContainer>
            ) : (
              <div className="h-full flex items-center justify-center text-xs text-slate-500">
                Belum ada key di database
              </div>
            )}
          </div>
        </div>

        {/* 4. Door Performance Chart */}
        <div className="bg-slate-900 border border-slate-800 rounded-2xl p-4 shadow-sm flex flex-col justify-between">
          <div className="flex items-center justify-between mb-3">
            <span className="text-xs font-semibold text-white flex items-center gap-2">
              <BarChart3 className="w-4 h-4 text-amber-400" />
              Performa Per Kategori Pintu
            </span>
            <span className="text-[11px] text-slate-500">Total vs Correct</span>
          </div>

          <div className="h-64 min-h-[260px] w-full min-w-0">
            {chartData?.door_performance?.length > 0 ? (
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={chartData.door_performance} layout="vertical">
                  <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
                  <XAxis type="number" stroke="#64748b" fontSize={11} />
                  <YAxis dataKey="door" type="category" stroke="#64748b" fontSize={10} width={110} />
                  <Tooltip contentStyle={{ backgroundColor: '#0f172a', borderColor: '#334155', borderRadius: '8px', fontSize: '12px' }} />
                  <Legend wrapperStyle={{ fontSize: '11px', paddingTop: '10px' }} />
                  <Bar dataKey="total" name="Total Soal" fill="#38bdf8" radius={[0, 4, 4, 0]} />
                  <Bar dataKey="correct" name="Benar" fill="#22c55e" radius={[0, 4, 4, 0]} />
                </BarChart>
              </ResponsiveContainer>
            ) : (
              <div className="h-full flex items-center justify-center text-xs text-slate-500">
                Belum ada data riwayat pintu
              </div>
            )}
          </div>
        </div>

        {/* 5. Instance comparison (global view only) */}
        {deviceUuid === 'all' && (
          <div className="md:col-span-2 bg-slate-900 border border-slate-800 rounded-2xl p-4 shadow-sm">
            <div className="flex items-center justify-between mb-3">
              <span className="text-xs font-semibold text-white flex items-center gap-2">
                <Layers className="w-4 h-4 text-violet-400" />
                Perbandingan Antar Instance
              </span>
              <span className="text-[11px] text-slate-500">Soal vs Key terpakai</span>
            </div>
            <div className="h-64 w-full min-w-0">
              {chartData?.instance_comparison?.length > 0 ? (
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={chartData.instance_comparison}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
                    <XAxis dataKey="name" stroke="#64748b" fontSize={11} />
                    <YAxis stroke="#64748b" fontSize={11} />
                    <Tooltip
                      contentStyle={{ backgroundColor: '#0f172a', borderColor: '#334155', borderRadius: '8px', fontSize: '12px' }}
                      formatter={(value, name, item) => {
                        const p = item.payload;
                        if (name === 'Total Soal') {
                          const extra = p.avg_duration_sec ? ` (Avg ${p.avg_duration_sec}s${p.avg_ai_duration_sec != null ? `, AI ${p.avg_ai_duration_sec}s` : ''})` : '';
                          return [`${value}${extra}`, name];
                        }
                        return [value, name];
                      }}
                    />
                    <Legend wrapperStyle={{ fontSize: '11px', paddingTop: '10px' }} />
                    <Bar dataKey="quiz_total" name="Total Soal" fill="#38bdf8" radius={[4, 4, 0, 0]} />
                    <Bar dataKey="keys_used" name="Key Terpakai" fill="#a78bfa" radius={[4, 4, 0, 0]} />
                  </BarChart>
                </ResponsiveContainer>
              ) : (
                <div className="h-full flex items-center justify-center text-xs text-slate-500">
                  Belum ada data perbandingan
                </div>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

