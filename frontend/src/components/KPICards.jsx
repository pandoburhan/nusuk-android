import React from 'react';
import { KeyRound, Database, CheckCircle2, Award, ShieldAlert, Timer, Zap, BarChart2 } from 'lucide-react';
import { StatusBadge, fmtSec, getTzMeta } from './common';

function KpiCard({ title, icon: Icon, tone, headline, sub, secondary, rows, emptyText }) {
  return (
    <div className="bg-slate-900 border border-slate-800 rounded-xl p-3.5 flex flex-col shadow-sm h-56 min-w-0">
      <div className="flex items-center justify-between text-slate-400 mb-1.5">
        <span className="text-xs font-medium">{title}</span>
        <div className={`w-7 h-7 rounded-lg flex items-center justify-center ${tone.bg} ${tone.text}`}>
          <Icon className="w-4 h-4" />
        </div>
      </div>
      <div className="shrink-0">
        <div className="flex items-baseline gap-1.5 flex-wrap">{headline}</div>
        {sub && <div className="text-[11px] text-slate-500 mt-0.5">{sub}</div>}
        {secondary && <div className="text-[10px] mt-0.5">{secondary}</div>}
      </div>
      <div className="mt-2 pt-2 border-t border-slate-800 flex-1 min-h-0 overflow-y-auto space-y-1 pr-0.5">
        {rows && rows.length > 0 ? (
          rows
        ) : (
          <div className="text-[10px] text-slate-600 text-center py-2">{emptyText || 'Belum ada instance'}</div>
        )}
      </div>
    </div>
  );
}

const Row = ({ name, children, title }) => (
  <div className="flex items-center justify-between gap-2 text-[11px]" title={title}>
    <span className="text-slate-300 truncate font-medium">{name}</span>
    <span className="font-mono text-slate-400 shrink-0 flex items-center gap-1.5">{children}</span>
  </div>
);

export default function KPICards({ kpiData, instances, timezone = 'wib' }) {
  const g = kpiData?.global || {};
  const tzMeta = getTzMeta(timezone);
  const live = Object.fromEntries((instances || []).map((i) => [i.uuid, i]));
  const rows = (kpiData?.instances || []).map((r) => {
    const l = live[r.uuid];
    return l
      ? { ...r, status: l.status || r.status, current_app_keys: l.current_app_keys ?? r.current_app_keys, name: l.name || r.name }
      : r;
  });

  const totalInst = instances?.length ?? g.total_instances ?? 0;
  const onlineInst = instances
    ? instances.filter((i) => i.status && i.status !== 'OFFLINE').length
    : g.online_instances ?? 0;
  const totalAppKeys = rows.reduce(
    (a, r) => a + (r.status !== 'OFFLINE' && typeof r.current_app_keys === 'number' ? r.current_app_keys : 0),
    0,
  );
  const unusedPure = g.unused_pure ?? 0;

  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3.5">
      <KpiCard
        title="Kunci Akun Nusuk"
        icon={KeyRound}
        tone={{ bg: 'bg-emerald-500/10', text: 'text-emerald-400' }}
        headline={
          <>
            <span className="text-2xl font-bold text-white">
              {onlineInst} / {totalInst}
            </span>
            <span className="text-xs text-slate-500">Online</span>
          </>
        }
        sub={
          <>
            Total kunci aktif: <strong className="text-emerald-400">{totalAppKeys}</strong>
          </>
        }
        rows={rows.map((r) => (
          <Row key={r.uuid} name={r.name} title={r.address}>
            {r.status === 'OFFLINE' ? (
              <span className="text-slate-600">OFFLINE</span>
            ) : (
              <span className="text-emerald-400">{r.current_app_keys ?? '—'}</span>
            )}
          </Row>
        ))}
      />

      <KpiCard
        title="Stok Key DB"
        icon={Database}
        tone={{ bg: 'bg-sky-500/10', text: 'text-sky-400' }}
        headline={
          <>
            <span className="text-2xl font-bold text-sky-400">{unusedPure}</span>
            <span className="text-xs text-slate-500">unused murni</span>
          </>
        }
        sub={unusedPure < 5 ? <span className="text-amber-400">Stok Tipis!</span> : 'Belum dipakai device manapun'}
        secondary={
          <span className="px-1.5 py-0.5 rounded bg-rose-500/10 text-rose-400 border border-rose-500/30">
            {g.blacklisted ?? 0} blacklist
          </span>
        }
        rows={rows.map((r) => (
          <Row key={r.uuid} name={r.name}>
            <span className="text-sky-400" title="Tersedia">{r.eligible_keys}</span>
            <span className="text-emerald-400" title="Used">{r.used_keys}</span>
            <span className="text-rose-400" title="Invalid">{r.invalid_keys}</span>
          </Row>
        ))}
      />

      <KpiCard
        title="Status Redeem"
        icon={CheckCircle2}
        tone={{ bg: 'bg-indigo-500/10', text: 'text-indigo-400' }}
        headline={
          <>
            <span className="text-xl font-bold text-emerald-400">{g.used_claims ?? 0}</span>
            <span className="text-[10px] text-slate-500">sukses</span>
            <span className="text-slate-700">|</span>
            <span className="text-xl font-bold text-rose-400">{g.blacklisted ?? 0}</span>
            <span className="text-[10px] text-slate-500">blacklist</span>
          </>
        }
        sub="Used claims vs key blacklisted"
        rows={rows.map((r) => (
          <Row key={r.uuid} name={r.name}>
            <span className="text-emerald-400">{r.used_keys}</span>
            <span className="text-slate-700">/</span>
            <span className="text-rose-400">{r.invalid_keys}</span>
          </Row>
        ))}
      />

      <KpiCard
        title="Total Quiz Terjawab"
        icon={BarChart2}
        tone={{ bg: 'bg-emerald-500/10', text: 'text-emerald-400' }}
        headline={
          <>
            <span className="text-2xl font-bold text-emerald-400">{g.today_quiz_answered ?? 0}</span>
            <span className="text-xs text-slate-400">hari ini ({tzMeta.short})</span>
          </>
        }
        sub={
          <span className="flex items-center gap-2 font-mono">
            <span>
              7H: <strong className="text-sky-400">{g.quiz_7d_answered ?? 0}</strong>
            </span>
            <span className="text-slate-700">|</span>
            <span>
              30H: <strong className="text-violet-400">{g.quiz_30d_answered ?? 0}</strong>
            </span>
          </span>
        }
        secondary={
          <span className="text-slate-500">
            Total sepanjang waktu: <strong className="text-slate-300">{g.total_quiz_answered ?? 0}</strong>
          </span>
        }
        rows={rows.map((r) => (
          <Row
            key={r.uuid}
            name={r.name}
            title={`Hari ini (${tzMeta.short}): ${r.quiz_today ?? 0} | 7 Hari: ${r.quiz_7d ?? 0} | 30 Hari: ${r.quiz_30d ?? 0} | Total: ${r.quiz_total ?? 0}`}
          >
            <span className="text-emerald-400" title="Hari Ini">{r.quiz_today ?? 0}</span>
            <span className="text-slate-700">/</span>
            <span className="text-sky-400" title="7 Hari Terakhir">{r.quiz_7d ?? 0}</span>
            <span className="text-slate-700">/</span>
            <span className="text-violet-400" title="30 Hari Terakhir">{r.quiz_30d ?? 0}</span>
          </Row>
        ))}
      />

      <KpiCard
        title="Akurasi AI Kuis"
        icon={Award}
        tone={{ bg: 'bg-teal-500/10', text: 'text-teal-400' }}
        headline={
          <>
            <span className="text-2xl font-bold text-white">{g.accuracy_percentage ?? 0}%</span>
            <span className="text-xs text-emerald-400">Global</span>
          </>
        }
        sub={
          <>
            <span className="text-emerald-400">{g.correct ?? 0} benar</span> /{' '}
            <span className="text-rose-400">{g.incorrect ?? 0} salah</span>
          </>
        }
        secondary={
          <span className="text-slate-500">
            Hari ini: <strong className="text-slate-300">{g.today_quiz_answered ?? 0}</strong> (Total{' '}
            {g.total_quiz_answered ?? 0})
          </span>
        }
        rows={rows.map((r) => (
          <Row key={r.uuid} name={r.name}>
            <span>{r.quiz_total} soal</span>
          </Row>
        ))}
      />

      <KpiCard
        title="Durasi / Soal"
        icon={Timer}
        tone={{ bg: 'bg-cyan-500/10', text: 'text-cyan-400' }}
        headline={
          <>
            <span className="text-2xl font-bold text-cyan-400">{fmtSec(g.avg_duration_sec)}</span>
            <span className="text-xs text-slate-500">/soal</span>
          </>
        }
        sub={
          <>
            Respon AI: <strong className="text-slate-300">{fmtSec(g.avg_ai_duration_sec)}</strong>
          </>
        }
        secondary={<span className="text-slate-500">Rata-rata 5 percobaan terakhir</span>}
        rows={rows.map((r) => (
          <Row key={r.uuid} name={r.name} title={`5 terakhir — AI: ${fmtSec(r.avg_ai_duration_sec)}`}>
            <span className="text-cyan-400">{fmtSec(r.avg_duration_sec)}</span>
            <span className="text-slate-600">AI {fmtSec(r.avg_ai_duration_sec)}</span>
          </Row>
        ))}
      />

      <KpiCard
        title="Durasi / Key"
        icon={Zap}
        tone={{ bg: 'bg-violet-500/10', text: 'text-violet-400' }}
        headline={
          <>
            <span className="text-2xl font-bold text-violet-400">{fmtSec(g.avg_redeem_duration_sec)}</span>
            <span className="text-xs text-slate-500">/key</span>
          </>
        }
        sub="Rata-rata 5 percobaan terakhir"
        rows={rows.map((r) => (
          <Row key={r.uuid} name={r.name} title="Rata-rata 5 percobaan terakhir">
            <span className="text-violet-400">{fmtSec(r.avg_redeem_duration_sec)}</span>
          </Row>
        ))}
      />

      <KpiCard
        title="Self-Healing"
        icon={ShieldAlert}
        tone={{ bg: 'bg-amber-500/10', text: 'text-amber-400' }}
        headline={
          <>
            <span className="text-2xl font-bold text-amber-400">{g.auto_recovery_count ?? 0}</span>
            <span className="text-xs text-slate-500">total kejadian</span>
          </>
        }
        sub="Auto-Restart / RAM Cleared"
        rows={rows.map((r) => {
          const limit = r.circuit_breaker_limit || 0;
          const cons = r.consecutive_recoveries || 0;
          const hit = limit > 0 && cons >= limit;
          const near = limit > 0 && cons >= limit - 1 && cons > 0;
          return (
            <Row key={r.uuid} name={r.name} title={`Beruntun ${cons}/${limit}`}>
              <span className={hit ? 'text-rose-400' : near ? 'text-amber-400' : 'text-slate-400'}>
                {r.recovery_count}
              </span>
              <span
                className={`text-[9px] px-1 rounded border ${hit ? 'border-rose-500/50 text-rose-400 bg-rose-500/10' : near ? 'border-amber-500/40 text-amber-400' : 'border-slate-700 text-slate-500'}`}
              >
                CB {cons}/{limit}
              </span>
            </Row>
          );
        })}
      />
    </div>
  );
}

export { StatusBadge };
