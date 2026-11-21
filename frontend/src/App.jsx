import React, { useState, useEffect, useCallback } from 'react';
import { LayoutDashboard, KeyRound, Award, Terminal, RefreshCw } from 'lucide-react';
import Header from './components/Header';
import KPICards from './components/KPICards';
import CommandCenter from './components/CommandCenter';
import EmulatorPreview from './components/EmulatorPreview';
import AnalyticsCharts from './components/AnalyticsCharts';
import KeyVault from './components/KeyVault';
import QuizHistory from './components/QuizHistory';
import SystemLogs from './components/SystemLogs';
import SettingsModal from './components/SettingsModal';
import AddDeviceModal from './components/AddDeviceModal';
import LockScreen from './components/LockScreen';
import { Toasts } from './components/common';

import { controlApi, analyticsApi, settingsApi, setAuthToken, setUnauthorizedHandler } from './api/client';
import { useTelemetry } from './hooks/useTelemetry';

let toastSeq = 0;

export default function App() {
  const [authToken, setAuthTokenState] = useState(null);
  const [lockNotice, setLockNotice] = useState('');
  const [activeMainTab, setActiveMainTab] = useState('dashboard');
  const [statusData, setStatusData] = useState(null);
  const [kpiData, setKpiData] = useState(null);
  const [previewRefreshSec, setPreviewRefreshSec] = useState(4);
  const [isSettingsOpen, setIsSettingsOpen] = useState(false);
  const [isAddDeviceOpen, setIsAddDeviceOpen] = useState(false);
  const [toasts, setToasts] = useState([]);
  const [refreshTick, setRefreshTick] = useState(0);
  const [timezone, setTimezone] = useState(() => {
    try {
      return window.localStorage.getItem('nusuk_tz') === 'ksa' ? 'ksa' : 'wib';
    } catch {
      return 'wib';
    }
  });
  const [serverTimeAnchor, setServerTimeAnchor] = useState(null);

  const handleChangeTimezone = useCallback((nextTz) => {
    const clean = nextTz === 'ksa' ? 'ksa' : 'wib';
    setTimezone(clean);
    try {
      window.localStorage.setItem('nusuk_tz', clean);
    } catch {
      // ignore storage errors
    }
  }, []);

  const handleUnauthorized = useCallback(() => {
    setAuthToken(null);
    setAuthTokenState(null);
    setStatusData(null);
    setKpiData(null);
    setIsSettingsOpen(false);
    setIsAddDeviceOpen(false);
    setLockNotice('Sesi akses tidak valid atau server baru saja dimulai ulang. Silakan masukkan password kembali.');
  }, []);

  useEffect(() => {
    setUnauthorizedHandler(handleUnauthorized);
    return () => setUnauthorizedHandler(null);
  }, [handleUnauthorized]);

  const handleUnlock = useCallback((token) => {
    setAuthToken(token);
    setAuthTokenState(token);
    setLockNotice('');
  }, []);

  const telemetry = useTelemetry(authToken, handleUnauthorized);

  const dismissToast = useCallback((id) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  const addToast = useCallback((t) => {
    toastSeq += 1;
    const id = toastSeq;
    setToasts((prev) => [...prev.slice(-4), { id, ...t }]);
    if (!t.sticky) {
      setTimeout(() => dismissToast(id), 5000);
    }
  }, [dismissToast]);

  const fetchSettings = useCallback(async () => {
    if (!authToken) return;
    try {
      const res = await settingsApi.getSettings();
      if (res.data?.preview_refresh_seconds) {
        setPreviewRefreshSec(res.data.preview_refresh_seconds);
      }
    } catch {
      // keep default 4s
    }
  }, [authToken]);

  const fetchOverallData = useCallback(async () => {
    if (!authToken) return;
    try {
      const [statusRes, kpiRes] = await Promise.allSettled([
        controlApi.getStatus(),
        analyticsApi.getKPI(timezone),
      ]);
      if (statusRes.status === 'fulfilled') setStatusData(statusRes.value.data);
      if (kpiRes.status === 'fulfilled') {
        const kd = kpiRes.value.data;
        setKpiData(kd);
        if (kd?.server_time?.unix_ms) {
          setServerTimeAnchor({
            serverUnixMs: kd.server_time.unix_ms,
            clientPerfMs: performance.now(),
            synced: Boolean(kd.server_time.synced),
            source: kd.server_time.source || 'system_local',
            nextKsaResetUnixMs: kd.server_time.next_ksa_reset_unix_ms || null,
          });
        }
      }
    } catch (err) {
      console.error('Fetch status/kpi error:', err);
    }
  }, [authToken, timezone]);

  useEffect(() => {
    if (!authToken) return undefined;
    fetchOverallData();
    fetchSettings();
    const interval = setInterval(fetchOverallData, 8000);
    return () => clearInterval(interval);
  }, [authToken, fetchOverallData, fetchSettings]);

  // Refresh KPI & charts when quiz or key events stream in
  useEffect(() => {
    if (!authToken) return;
    if (telemetry.lastQuizEvent || telemetry.lastKeyEvent) {
      fetchOverallData();
      setRefreshTick((n) => n + 1);
    }
  }, [authToken, telemetry.lastQuizEvent, telemetry.lastKeyEvent, fetchOverallData]);

  // Surface backend WebSocket alerts as toasts
  const { latestAlert, clearAlert } = telemetry;
  useEffect(() => {
    if (latestAlert) {
      addToast({
        type: latestAlert.type || 'info',
        message: latestAlert.message,
      });
      clearAlert();
    }
  }, [latestAlert, addToast, clearAlert]);

  if (!authToken) {
    return <LockScreen onUnlock={handleUnlock} noticeMessage={lockNotice} />;
  }

  const unusedPure = kpiData?.global?.unused_pure;

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100 flex flex-col font-sans">
      {/* Global Header */}
      <Header
        statusData={statusData}
        instances={telemetry.instances}
        isConnected={telemetry.isConnected}
        onOpenSettings={() => setIsSettingsOpen(true)}
        toast={addToast}
        timezone={timezone}
        onChangeTimezone={handleChangeTimezone}
        serverTimeAnchor={serverTimeAnchor}
      />

      {/* Main Navigation Tabs */}
      <nav className="bg-slate-900/60 border-b border-slate-800/80 px-6 py-2">
        <div className="max-w-[1600px] mx-auto flex items-center justify-between">
          <div className="flex flex-wrap items-center gap-1.5 text-xs">
            <button
              onClick={() => setActiveMainTab('dashboard')}
              className={`px-3.5 py-1.5 rounded-xl font-medium transition flex items-center gap-2 ${
                activeMainTab === 'dashboard'
                  ? 'bg-emerald-600 text-white shadow-sm shadow-emerald-600/30'
                  : 'text-slate-400 hover:text-white hover:bg-slate-800/60'
              }`}
            >
              <LayoutDashboard className="w-3.5 h-3.5" />
              Command Dashboard
            </button>

            <button
              onClick={() => setActiveMainTab('vault')}
              className={`px-3.5 py-1.5 rounded-xl font-medium transition flex items-center gap-2 ${
                activeMainTab === 'vault'
                  ? 'bg-sky-600 text-white shadow-sm shadow-sky-600/30'
                  : 'text-slate-400 hover:text-white hover:bg-slate-800/60'
              }`}
            >
              <KeyRound className="w-3.5 h-3.5" />
              Key Vault
              {unusedPure !== undefined && (
                <span className="text-[10px] px-1.5 py-0.2 rounded-full bg-slate-950 text-sky-400 border border-sky-800 font-mono">
                  {unusedPure}
                </span>
              )}
            </button>

            <button
              onClick={() => setActiveMainTab('quiz')}
              className={`px-3.5 py-1.5 rounded-xl font-medium transition flex items-center gap-2 ${
                activeMainTab === 'quiz'
                  ? 'bg-teal-600 text-white shadow-sm shadow-teal-600/30'
                  : 'text-slate-400 hover:text-white hover:bg-slate-800/60'
              }`}
            >
              <Award className="w-3.5 h-3.5" />
              Riwayat Kuis
            </button>

            <button
              onClick={() => setActiveMainTab('logs')}
              className={`px-3.5 py-1.5 rounded-xl font-medium transition flex items-center gap-2 ${
                activeMainTab === 'logs'
                  ? 'bg-indigo-600 text-white shadow-sm shadow-indigo-600/30'
                  : 'text-slate-400 hover:text-white hover:bg-slate-800/60'
              }`}
            >
              <Terminal className="w-3.5 h-3.5" />
              Live System Logs
            </button>
          </div>

          <button
            onClick={() => {
              fetchOverallData();
              telemetry.refreshInstances();
              setRefreshTick((n) => n + 1);
            }}
            className="p-1.5 text-slate-500 hover:text-white transition"
            title="Refresh KPI & Status"
          >
            <RefreshCw className="w-3.5 h-3.5" />
          </button>
        </div>
      </nav>

      {/* Main Content Area */}
      <main className="flex-1 max-w-[1600px] w-full mx-auto p-4 md:p-6 space-y-5">
        {activeMainTab === 'dashboard' && (
          <>
            {/* 8 KPI Cards (2-layer global + per-instance) */}
            <KPICards kpiData={kpiData} instances={telemetry.instances} timezone={timezone} />

            {/* Swapped v2 Layout: LEFT 2/3 Live Emulator Preview Grid | RIGHT 1/3 Bulk Command Center */}
            <div className="grid grid-cols-1 lg:grid-cols-3 gap-5 items-start">
              <div className="lg:col-span-2">
                <EmulatorPreview
                  instances={telemetry.instances}
                  intervalSec={previewRefreshSec}
                  patchInstance={telemetry.patchInstance}
                  toast={addToast}
                  refreshInstances={telemetry.refreshInstances}
                  onAdd={() => setIsAddDeviceOpen(true)}
                />
              </div>

              <div className="lg:col-span-1">
                <CommandCenter
                  instances={telemetry.instances}
                  toast={addToast}
                  onAdd={() => setIsAddDeviceOpen(true)}
                  onActionComplete={() => {
                    fetchOverallData();
                    telemetry.refreshInstances();
                  }}
                />
              </div>
            </div>

            {/* Analytics Charts (Global & Per-Device) */}
            <AnalyticsCharts instances={telemetry.instances} refreshSignal={refreshTick} timezone={timezone} />
          </>
        )}

        {activeMainTab === 'vault' && (
          <KeyVault
            onKeysUpdated={fetchOverallData}
            instances={telemetry.instances}
            refreshSignal={refreshTick}
            timezone={timezone}
          />
        )}

        {activeMainTab === 'quiz' && (
          <QuizHistory instances={telemetry.instances} refreshSignal={refreshTick} timezone={timezone} />
        )}

        {activeMainTab === 'logs' && (
          <SystemLogs liveLogs={telemetry.liveLogs} instances={telemetry.instances} timezone={timezone} />
        )}
      </main>

      {/* Footer */}
      <footer className="border-t border-slate-900 bg-slate-950 text-slate-600 text-xs py-3 px-6 text-center">
        Nusuk Automation &amp; Key Management Hub v2.0 &bull; Multi-Instance Orchestration System
      </footer>

      {/* Modals */}
      <SettingsModal
        isOpen={isSettingsOpen}
        onClose={() => setIsSettingsOpen(false)}
        onSaved={fetchSettings}
        timezone={timezone}
      />

      {isAddDeviceOpen && (
        <AddDeviceModal
          onClose={() => setIsAddDeviceOpen(false)}
          toast={addToast}
          refreshInstances={() => {
            telemetry.refreshInstances();
            fetchOverallData();
          }}
        />
      )}

      <Toasts toasts={toasts} onDismiss={dismissToast} />
    </div>
  );
}
