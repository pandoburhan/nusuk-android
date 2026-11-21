import React, { useState, useEffect, useCallback } from 'react';
import {
  X, CheckCircle2, XCircle, AlertTriangle, Loader2, Plug, Save, Info,
  Monitor, Usb, Wifi, RefreshCw, Link2, Smartphone, Box, Download, Rocket, Trash2,
} from 'lucide-react';
import { instancesApi, redroidApi, errorDetail } from '../api/client';
import { ConnectionBadge } from './common';

const Line = ({ ok, warn, children }) => (
  <div className="flex items-start gap-2 text-[11px]">
    {ok === true ? (
      <CheckCircle2 className="w-3.5 h-3.5 text-emerald-400 shrink-0 mt-0.5" />
    ) : ok === false ? (
      <XCircle className="w-3.5 h-3.5 text-rose-400 shrink-0 mt-0.5" />
    ) : warn ? (
      <AlertTriangle className="w-3.5 h-3.5 text-amber-400 shrink-0 mt-0.5" />
    ) : (
      <Info className="w-3.5 h-3.5 text-sky-400 shrink-0 mt-0.5" />
    )}
    <span className="text-slate-300 leading-relaxed">{children}</span>
  </div>
);

const TABS = [
  { id: 'tcp', label: 'IP & Port', icon: Monitor, sub: 'Emulator / TCP' },
  { id: 'usb', label: 'USB Debugging', icon: Usb, sub: 'Scan HP Fisik' },
  { id: 'wireless', label: 'Wireless Pair', icon: Wifi, sub: 'Android 11+' },
  { id: 'redroid', label: 'Redroid', icon: Box, sub: 'Linux Only' },
];

export default function AddDeviceModal({ onClose, toast, refreshInstances, lastRedroidEvent }) {
  const [tab, setTab] = useState('tcp'); // 'tcp' | 'usb' | 'wireless' | 'redroid'

  // Shared alias & check/save state
  const [name, setName] = useState('');
  const [testing, setTesting] = useState(false);
  const [saving, setSaving] = useState(false);
  const [result, setResult] = useState(null);
  const [forceNew, setForceNew] = useState(false);
  const [error, setError] = useState('');

  // Tab 1: IP & Port
  const [ip, setIp] = useState('');
  const [port, setPort] = useState('5555');

  // Tab 2: USB Debugging (Auto-Scan)
  const [scanning, setScanning] = useState(false);
  const [discovered, setDiscovered] = useState([]);
  const [selectedUsb, setSelectedUsb] = useState(null); // discovered item

  // Tab 3: Wireless Pairing (Android 11+)
  const [pairIp, setPairIp] = useState('');
  const [pairPort, setPairPort] = useState('');
  const [pairCode, setPairCode] = useState('');
  const [pairing, setPairing] = useState(false);
  const [pairInfo, setPairInfo] = useState(null); // { success, message, suggested_connect_port }
  const [connectPort, setConnectPort] = useState('');

  // Tab 4: Redroid (Linux Only)
  const [redroidLoading, setRedroidLoading] = useState(false);
  const [redroidStatus, setRedroidStatus] = useState(null);
  const [pullingImage, setPullingImage] = useState(false);
  const [pullProgressMsg, setPullProgressMsg] = useState('');
  const [deployingRedroid, setDeployingRedroid] = useState(false);
  const [destroyingId, setDestroyingId] = useState(null);
  const [bindToUuid, setBindToUuid] = useState('');
  const [migrationTargets, setMigrationTargets] = useState([]);
  const [loadingMigrationTargets, setLoadingMigrationTargets] = useState(false);

  const resetResult = useCallback(() => {
    setResult(null);
    setForceNew(false);
    setError('');
  }, []);

  const switchTab = (next) => {
    setTab(next);
    resetResult();
  };

  // Scan USB / ADB devices
  const scanDevices = useCallback(async () => {
    setScanning(true);
    setError('');
    try {
      const res = await instancesApi.discover();
      setDiscovered(res.data?.items || []);
    } catch (e) {
      setError(errorDetail(e, 'Gagal memindai perangkat ADB/USB'));
    } finally {
      setScanning(false);
    }
  }, []);

  // Load Redroid / Docker status
  const loadRedroidStatus = useCallback(async () => {
    setRedroidLoading(true);
    setError('');
    try {
      const res = await redroidApi.getStatus();
      const d = res.data;
      setRedroidStatus(d);
      setPullingImage(Boolean(d?.pulling_image));
      if (d?.pull_status) setPullProgressMsg(d.pull_status);
    } catch (e) {
      setRedroidStatus(null);
      setError(errorDetail(e, 'Gagal memeriksa status Redroid / Docker'));
    } finally {
      setRedroidLoading(false);
    }
  }, []);

  // Load all active + archived instances for Redroid identity migration
  const loadMigrationTargets = useCallback(async () => {
    setLoadingMigrationTargets(true);
    try {
      const [activeRes, archivedRes] = await Promise.all([
        instancesApi.list(),
        instancesApi.archived(),
      ]);
      const activeItems = (activeRes.data?.items || []).map((inst) => ({
        uuid: inst.uuid,
        name: inst.name || inst.adb_serial || inst.uuid,
        status: inst.status || 'OFFLINE',
        isArchived: false,
      }));
      const archivedItems = (archivedRes.data?.items || []).map((inst) => ({
        uuid: inst.uuid,
        name: inst.name || inst.adb_serial || inst.uuid,
        status: 'OFFLINE',
        isArchived: true,
      }));
      setMigrationTargets([...activeItems, ...archivedItems]);
    } catch {
      setMigrationTargets([]);
    } finally {
      setLoadingMigrationTargets(false);
    }
  }, []);

  useEffect(() => {
    if (tab === 'usb') {
      scanDevices();
    } else if (tab === 'redroid') {
      loadRedroidStatus();
      loadMigrationTargets();
    }
  }, [tab, scanDevices, loadRedroidStatus, loadMigrationTargets]);

  // Listen to WebSocket Redroid pull progress while modal is open
  useEffect(() => {
    if (!lastRedroidEvent || tab !== 'redroid') return;
    if (lastRedroidEvent.stage === 'pull_image') {
      if (lastRedroidEvent.message) setPullProgressMsg(lastRedroidEvent.message);
      if (lastRedroidEvent.status === 'success' || lastRedroidEvent.status === 'error') {
        setPullingImage(false);
        loadRedroidStatus();
      } else {
        setPullingImage(true);
      }
    } else if (lastRedroidEvent.status === 'ready') {
      loadRedroidStatus();
    }
  }, [lastRedroidEvent, tab, loadRedroidStatus]);

  const handleInstallRedroidImage = async () => {
    setPullingImage(true);
    setPullProgressMsg('Memulai unduhan image redroid/redroid:9.0.0-latest...');
    setError('');
    try {
      const res = await redroidApi.installImage();
      const d = res.data;
      if (d?.data?.already_exists) {
        setPullingImage(false);
        await loadRedroidStatus();
      } else {
        toast?.({
          type: 'info',
          title: 'Redroid Image',
          message: d?.message || 'Mengunduh image Redroid di latar belakang...',
        });
      }
    } catch (e) {
      setPullingImage(false);
      setError(errorDetail(e, 'Gagal memulai instalasi image Redroid'));
    }
  };

  const handleDeployRedroid = async () => {
    setDeployingRedroid(true);
    setError('');
    try {
      const payload = {
        ...(name.trim() ? { name: name.trim() } : {}),
        bind_to_uuid: bindToUuid || null,
      };
      await redroidApi.deployInstance(payload);
      toast?.({
        type: 'success',
        title: 'Redroid Deploy',
        message: 'Deploying new instance in the background. It will appear on the dashboard soon.',
      });
      onClose();
    } catch (e) {
      setError(errorDetail(e, 'Gagal men-deploy instance Redroid'));
    } finally {
      setDeployingRedroid(false);
    }
  };

  const handleDestroyRedroid = async (containerId, containerName) => {
    setDestroyingId(containerId);
    setError('');
    try {
      await redroidApi.destroyInstance(containerId);
      toast?.({
        type: 'success',
        title: 'Redroid',
        message: `Container ${containerName} berhasil dihentikan & dihapus.`,
      });
      refreshInstances?.();
      await loadRedroidStatus();
    } catch (e) {
      setError(errorDetail(e, 'Gagal menghapus container Redroid'));
    } finally {
      setDestroyingId(null);
    }
  };

  // Tab 1 test
  const portNum = parseInt(port, 10);
  const validTcpAddr = ip.trim() && Number.isInteger(portNum) && portNum > 0 && portNum < 65536;

  const testTcp = async () => {
    setTesting(true);
    resetResult();
    try {
      const res = await instancesApi.testConnection({
        connection_type: 'tcp',
        ip: ip.trim(),
        port: portNum,
      });
      setResult(res.data);
    } catch (e) {
      setError(errorDetail(e, 'Cek koneksi gagal'));
    } finally {
      setTesting(false);
    }
  };

  // Tab 2: select & test USB/ADB device
  const testDiscoveredDevice = async (dev) => {
    setSelectedUsb(dev);
    if (!name.trim() && dev.model && dev.model !== dev.adb_serial) {
      setName(dev.model);
    }
    setTesting(true);
    resetResult();
    try {
      const payload =
        dev.connection_type === 'usb'
          ? { connection_type: 'usb', adb_serial: dev.adb_serial }
          : { connection_type: 'tcp', ip: dev.ip, port: dev.port, adb_serial: dev.adb_serial };
      const res = await instancesApi.testConnection(payload);
      setResult(res.data);
    } catch (e) {
      setError(errorDetail(e, 'Cek perangkat USB gagal'));
    } finally {
      setTesting(false);
    }
  };

  // Tab 3: Step 1 pair
  const pairPortNum = parseInt(pairPort, 10);
  const validPair =
    pairIp.trim() &&
    Number.isInteger(pairPortNum) &&
    pairPortNum > 0 &&
    pairPortNum < 65536 &&
    pairCode.trim().length >= 4;

  const handlePair = async () => {
    setPairing(true);
    setPairInfo(null);
    resetResult();
    try {
      const res = await instancesApi.pair(pairIp.trim(), pairPortNum, pairCode.trim());
      const d = res.data;
      setPairInfo(d);
      if (d?.suggested_connect_port) {
        setConnectPort(String(d.suggested_connect_port));
      }
      toast?.({ type: 'success', message: d?.message || 'Pairing wireless berhasil!' });
    } catch (e) {
      const msg = errorDetail(e, 'Pairing wireless gagal');
      setPairInfo({ success: false, message: msg });
      setError(msg);
    } finally {
      setPairing(false);
    }
  };

  // Tab 3: Step 2 connect & check Nusuk
  const connectPortNum = parseInt(connectPort, 10);
  const validWirelessConnect =
    pairIp.trim() && Number.isInteger(connectPortNum) && connectPortNum > 0 && connectPortNum < 65536;

  const testWireless = async () => {
    setTesting(true);
    resetResult();
    try {
      const res = await instancesApi.testConnection({
        connection_type: 'wireless',
        ip: pairIp.trim(),
        port: connectPortNum,
      });
      setResult(res.data);
      if (!name.trim() && res.data?.model) {
        setName(res.data.model);
      }
    } catch (e) {
      setError(errorDetail(e, 'Cek koneksi wireless gagal'));
    } finally {
      setTesting(false);
    }
  };

  const canSave =
    result && result.can_save && !saving && (!result.clone_conflict || forceNew);

  const save = async () => {
    if (!result) return;
    setSaving(true);
    setError('');
    try {
      const body = {
        connection_type: result.connection_type || tab,
        adb_serial: result.adb_serial || undefined,
        ip: result.ip || undefined,
        port: result.port || undefined,
        force_new_uuid: !!(result.clone_conflict && forceNew),
      };
      if (name.trim()) body.name = name.trim();
      const res = await instancesApi.create(body);
      const restored = res.data?.data?.restored;
      const relinked = res.data?.data?.relinked;
      toast({
        type: 'success',
        message:
          res.data?.message ||
          (restored
            ? 'Instance lama dipulihkan'
            : relinked
              ? 'Koneksi perangkat diperbarui (UUID sama)'
              : 'Instance ditambahkan'),
      });
      refreshInstances();
      onClose();
    } catch (e) {
      setError(errorDetail(e, 'Gagal menyimpan instance'));
    } finally {
      setSaving(false);
    }
  };

  const input =
    'w-full bg-slate-950 border border-slate-800 rounded-xl px-3 py-2 text-white text-xs focus:outline-none focus:border-sky-500';

  return (
    <div className="fixed inset-0 z-50 bg-slate-950/75 backdrop-blur-sm flex items-center justify-center p-4">
      <div className="bg-slate-900 border border-slate-800 rounded-2xl max-w-xl w-full p-5 shadow-2xl space-y-4 max-h-[92vh] overflow-y-auto">
        <div className="flex items-center justify-between border-b border-slate-800 pb-3">
          <div>
            <h3 className="text-sm font-semibold text-white">Tambah Instance / Perangkat Android</h3>
            <p className="text-[11px] text-slate-400">Mendukung Emulator TCP/IP, HP Fisik USB, Wireless Debugging &amp; Redroid Linux</p>
          </div>
          <button onClick={onClose} className="text-slate-400 hover:text-white">
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* 4-Tab Connection Method Switcher */}
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-1.5 bg-slate-950 p-1 rounded-xl border border-slate-800 text-xs">
          {TABS.map((t) => {
            const Icon = t.icon;
            const active = tab === t.id;
            const activeCls =
              t.id === 'redroid'
                ? 'bg-indigo-600 text-white shadow'
                : 'bg-emerald-600 text-white shadow';
            const subActiveCls = t.id === 'redroid' ? 'text-indigo-100' : 'text-emerald-100';
            return (
              <button
                key={t.id}
                type="button"
                onClick={() => switchTab(t.id)}
                className={`px-2 py-2 rounded-lg font-medium transition flex flex-col items-center gap-0.5 ${
                  active
                    ? activeCls
                    : 'text-slate-400 hover:text-white hover:bg-slate-900'
                }`}
              >
                <span className="flex items-center gap-1 text-[11px] font-semibold">
                  <Icon className="w-3.5 h-3.5" />
                  {t.label}
                </span>
                <span className={`text-[9px] ${active ? subActiveCls : 'text-slate-500'}`}>{t.sub}</span>
              </button>
            );
          })}
        </div>

        {/* Shared Optional Alias */}
        <div className="text-xs">
          <label className="block text-slate-400">
            Alias / Nama Perangkat (opsional)
            <input
              className={`${input} mt-1`}
              value={name}
              placeholder={
                tab === 'usb'
                  ? 'otomatis dari model HP (misal: Samsung A54)'
                  : tab === 'wireless'
                    ? 'contoh: HP-Utama-WiFi'
                    : tab === 'redroid'
                      ? 'contoh: Redroid-VPS-01 (otomatis jika kosong)'
                      : 'contoh: LD-01'
              }
              onChange={(e) => setName(e.target.value)}
            />
          </label>
        </div>

        {/* TAB 1: IP & Port */}
        {tab === 'tcp' && (
          <div className="space-y-3 text-xs">
            <div className="grid grid-cols-3 gap-2">
              <label className="col-span-2 block text-slate-400">
                IP Address
                <input
                  className={`${input} mt-1 font-mono`}
                  value={ip}
                  placeholder="192.168.100.8"
                  onChange={(e) => {
                    setIp(e.target.value);
                    resetResult();
                  }}
                />
              </label>
              <label className="block text-slate-400">
                Port
                <input
                  className={`${input} mt-1 font-mono`}
                  value={port}
                  onChange={(e) => {
                    setPort(e.target.value);
                    resetResult();
                  }}
                />
              </label>
            </div>

            <button
              type="button"
              onClick={testTcp}
              disabled={!validTcpAddr || testing}
              className="w-full px-3 py-2 bg-sky-600/20 hover:bg-sky-600/30 text-sky-400 border border-sky-500/30 rounded-xl font-medium flex items-center justify-center gap-1.5 disabled:opacity-40"
            >
              {testing ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Plug className="w-3.5 h-3.5" />}
              {testing ? 'Memeriksa…' : 'Cek Koneksi & App Nusuk'}
            </button>
          </div>
        )}

        {/* TAB 2: USB Debugging (Auto-Scan) */}
        {tab === 'usb' && (
          <div className="space-y-3 text-xs">
            <div className="flex items-center justify-between gap-2">
              <span className="text-[11px] text-slate-400">
                Colok kabel USB HP dengan <strong>USB Debugging</strong> aktif, lalu scan:
              </span>
              <button
                type="button"
                onClick={scanDevices}
                disabled={scanning}
                className="px-3 py-1.5 bg-indigo-600/20 hover:bg-indigo-600/30 text-indigo-300 border border-indigo-500/40 rounded-lg font-medium flex items-center gap-1.5 shrink-0 disabled:opacity-40"
              >
                <RefreshCw className={`w-3.5 h-3.5 ${scanning ? 'animate-spin' : ''}`} />
                {scanning ? 'Memindai…' : 'Scan USB / ADB'}
              </button>
            </div>

            <div className="space-y-2 max-h-56 overflow-y-auto pr-0.5">
              {scanning && discovered.length === 0 ? (
                <div className="py-6 text-center text-slate-500 flex flex-col items-center gap-2">
                  <Loader2 className="w-5 h-5 animate-spin text-indigo-400" />
                  <span>Memindai perangkat ADB yang terhubung…</span>
                </div>
              ) : discovered.length === 0 ? (
                <div className="py-6 px-3 text-center text-slate-500 bg-slate-950/70 border border-slate-800 rounded-xl space-y-1">
                  <Smartphone className="w-5 h-5 mx-auto text-slate-600" />
                  <div>Tidak ada perangkat USB / ADB yang terdeteksi.</div>
                  <div className="text-[10px] text-slate-600">
                    Pastikan Developer Options &amp; USB Debugging aktif di HP, dan kabel USB mendukung transfer data.
                  </div>
                </div>
              ) : (
                discovered.map((dev) => {
                  const isSel = selectedUsb?.adb_serial === dev.adb_serial;
                  const isReady = dev.state === 'device';
                  const isUnauth = dev.state === 'unauthorized';
                  return (
                    <div
                      key={dev.adb_serial}
                      className={`p-2.5 rounded-xl border transition space-y-1.5 ${
                        isSel
                          ? 'bg-indigo-950/30 border-indigo-500/60'
                          : 'bg-slate-950/70 border-slate-800'
                      }`}
                    >
                      <div className="flex items-center justify-between gap-2">
                        <div className="min-w-0">
                          <div className="flex items-center gap-1.5 flex-wrap">
                            <span className="font-semibold text-white truncate">{dev.model}</span>
                            <ConnectionBadge type={dev.connection_type} />
                            {dev.already_registered ? (
                              <span className="text-[9px] px-1.5 py-0.5 rounded bg-slate-800 text-slate-400 border border-slate-700 font-semibold">
                                Sudah Terdaftar
                              </span>
                            ) : isReady ? (
                              <span className="text-[9px] px-1.5 py-0.5 rounded bg-emerald-500/15 text-emerald-300 border border-emerald-500/40 font-semibold">
                                Siap
                              </span>
                            ) : isUnauth ? (
                              <span className="text-[9px] px-1.5 py-0.5 rounded bg-amber-500/15 text-amber-300 border border-amber-500/40 font-semibold">
                                Unauthorized
                              </span>
                            ) : (
                              <span className="text-[9px] px-1.5 py-0.5 rounded bg-rose-500/15 text-rose-300 border border-rose-500/40 font-semibold uppercase">
                                {dev.state}
                              </span>
                            )}
                          </div>
                          <div className="text-[10px] font-mono text-slate-500 truncate mt-0.5">
                            Serial: {dev.adb_serial}
                          </div>
                        </div>

                        <button
                          type="button"
                          onClick={() => testDiscoveredDevice(dev)}
                          disabled={!isReady || dev.already_registered || testing}
                          className="px-2.5 py-1.5 rounded-lg text-[11px] font-medium bg-sky-600/20 hover:bg-sky-600/30 text-sky-300 border border-sky-500/40 disabled:opacity-40 disabled:cursor-not-allowed shrink-0 flex items-center gap-1"
                        >
                          {testing && isSel ? (
                            <Loader2 className="w-3 h-3 animate-spin" />
                          ) : (
                            <Plug className="w-3 h-3" />
                          )}
                          Cek App Nusuk &amp; Pilih
                        </button>
                      </div>
                      {dev.hint && (
                        <div className={`text-[10px] ${isUnauth ? 'text-amber-300' : 'text-slate-400'}`}>
                          {dev.hint}
                        </div>
                      )}
                    </div>
                  );
                })
              )}
            </div>
          </div>
        )}

        {/* TAB 3: Wireless Pairing (Android 11+) */}
        {tab === 'wireless' && (
          <div className="space-y-3 text-xs">
            {/* Step 1: Pair */}
            <div className="p-3 rounded-xl bg-slate-950/70 border border-slate-800 space-y-2.5">
              <div className="flex items-center justify-between">
                <span className="font-semibold text-teal-300 flex items-center gap-1.5">
                  <Link2 className="w-3.5 h-3.5" />
                  Langkah 1 — Pair Perangkat (Android 11+)
                </span>
                <span className="text-[10px] text-slate-500">Wireless Debugging → Pair with pairing code</span>
              </div>

              <div className="grid grid-cols-6 gap-2">
                <label className="col-span-3 block text-slate-400">
                  IP Address HP
                  <input
                    className={`${input} mt-1 font-mono`}
                    value={pairIp}
                    placeholder="192.168.100.25"
                    onChange={(e) => {
                      setPairIp(e.target.value);
                      resetResult();
                    }}
                  />
                </label>
                <label className="col-span-3 block text-slate-400">
                  Pairing Port
                  <input
                    className={`${input} mt-1 font-mono`}
                    value={pairPort}
                    placeholder="37123"
                    onChange={(e) => setPairPort(e.target.value)}
                  />
                </label>
                <label className="col-span-4 block text-slate-400">
                  Pairing Code (6 digit)
                  <input
                    className={`${input} mt-1 font-mono tracking-widest`}
                    value={pairCode}
                    placeholder="123456"
                    onChange={(e) => setPairCode(e.target.value)}
                  />
                </label>
                <div className="col-span-2 flex items-end">
                  <button
                    type="button"
                    onClick={handlePair}
                    disabled={!validPair || pairing}
                    className="w-full py-2 px-2 bg-teal-600/20 hover:bg-teal-600/30 text-teal-300 border border-teal-500/40 rounded-xl font-semibold flex items-center justify-center gap-1 disabled:opacity-40"
                  >
                    {pairing ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Wifi className="w-3.5 h-3.5" />}
                    {pairing ? 'Pairing…' : 'Pair Device'}
                  </button>
                </div>
              </div>

              {pairInfo && (
                <Line ok={!!pairInfo.success}>
                  {pairInfo.message}
                  {pairInfo.suggested_connect_port && (
                    <span className="text-emerald-300 font-mono">
                      {' '}
                      (Connect Port terdeteksi otomatis: {pairInfo.suggested_connect_port})
                    </span>
                  )}
                </Line>
              )}
            </div>

            {/* Step 2: Connect & Check Nusuk */}
            <div className="p-3 rounded-xl bg-slate-950/70 border border-slate-800 space-y-2.5">
              <div className="flex items-center justify-between">
                <span className="font-semibold text-sky-300 flex items-center gap-1.5">
                  <Plug className="w-3.5 h-3.5" />
                  Langkah 2 — Sambungkan &amp; Cek Nusuk
                </span>
                <span className="text-[10px] text-slate-500">Port di layar utama Wireless Debugging</span>
              </div>

              <div className="grid grid-cols-5 gap-2 items-end">
                <label className="col-span-3 block text-slate-400">
                  Connect Port (berbeda dari Pairing Port)
                  <input
                    className={`${input} mt-1 font-mono`}
                    value={connectPort}
                    placeholder="contoh: 41235"
                    onChange={(e) => {
                      setConnectPort(e.target.value);
                      resetResult();
                    }}
                  />
                </label>
                <div className="col-span-2">
                  <button
                    type="button"
                    onClick={testWireless}
                    disabled={!validWirelessConnect || testing}
                    className="w-full py-2 px-2 bg-sky-600/20 hover:bg-sky-600/30 text-sky-300 border border-sky-500/40 rounded-xl font-semibold flex items-center justify-center gap-1 disabled:opacity-40"
                  >
                    {testing ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Plug className="w-3.5 h-3.5" />}
                    {testing ? 'Memeriksa…' : 'Cek Koneksi'}
                  </button>
                </div>
              </div>
            </div>
          </div>
        )}

        {/* TAB 4: Redroid (Linux Only) */}
        {tab === 'redroid' && (
          <div className="space-y-3 text-xs">
            <div className="flex items-center justify-between">
              <span className="text-[11px] text-slate-400">
                Deploy emulator Android 9 headless instan via Docker Engine (Linux VPS).
              </span>
              <button
                type="button"
                onClick={loadRedroidStatus}
                disabled={redroidLoading}
                className="px-2.5 py-1 bg-slate-800 hover:bg-slate-700 text-slate-300 border border-slate-700 rounded-lg text-[11px] font-medium flex items-center gap-1 disabled:opacity-40 shrink-0"
              >
                <RefreshCw className={`w-3 h-3 ${redroidLoading ? 'animate-spin' : ''}`} />
                Refresh
              </button>
            </div>

            {redroidLoading && !redroidStatus ? (
              <div className="py-8 text-center text-slate-400 bg-slate-950/70 border border-slate-800 rounded-xl flex flex-col items-center gap-2">
                <Loader2 className="w-5 h-5 animate-spin text-indigo-400" />
                <span>Memeriksa status OS, Docker Engine &amp; image Redroid…</span>
              </div>
            ) : !redroidStatus || !redroidStatus.supported || !redroidStatus.docker_available ? (
              <div className="p-4 rounded-xl bg-amber-950/30 border border-amber-700/50 space-y-2">
                <div className="flex items-start gap-2.5">
                  <AlertTriangle className="w-4 h-4 text-amber-400 shrink-0 mt-0.5" />
                  <div className="space-y-1">
                    <div className="font-semibold text-amber-200">
                      Redroid deployment is only supported on Linux with Docker installed.
                    </div>
                    <div className="text-[11px] text-amber-300/80">
                      Host OS terdeteksi: <span className="font-mono text-white">{redroidStatus?.os || 'Unknown'}</span>
                      {redroidStatus?.error && redroidStatus.supported ? ` — ${redroidStatus.error}` : ''}
                    </div>
                  </div>
                </div>
                <button
                  type="button"
                  disabled
                  className="w-full py-2.5 px-3 rounded-xl bg-slate-800 text-slate-500 border border-slate-700 font-semibold cursor-not-allowed flex items-center justify-center gap-1.5"
                >
                  <Rocket className="w-3.5 h-3.5" />
                  Deploy New Instance (Android 9)
                </button>
              </div>
            ) : !redroidStatus.image_exists ? (
              <div className="p-4 rounded-xl bg-slate-950/80 border border-indigo-800/60 space-y-3">
                <div className="space-y-1">
                  <Line ok={true}>
                    Docker Engine terhubung ({redroidStatus.os}).
                  </Line>
                  <Line warn>
                    Image <span className="font-mono text-indigo-300">{redroidStatus.image_name}</span> belum terpasang di host ini.
                  </Line>
                </div>

                {pullingImage && (
                  <div className="p-2.5 rounded-lg bg-indigo-950/40 border border-indigo-700/50 text-indigo-200 text-[11px] flex items-center gap-2">
                    <Loader2 className="w-4 h-4 animate-spin text-indigo-400 shrink-0" />
                    <span className="truncate">{pullProgressMsg || 'Mengunduh image Redroid…'}</span>
                  </div>
                )}

                <button
                  type="button"
                  onClick={handleInstallRedroidImage}
                  disabled={pullingImage}
                  className="w-full py-2.5 px-4 bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 text-white rounded-xl font-semibold flex items-center justify-center gap-2 shadow-lg shadow-indigo-600/20 transition"
                >
                  {pullingImage ? (
                    <Loader2 className="w-4 h-4 animate-spin" />
                  ) : (
                    <Download className="w-4 h-4" />
                  )}
                  {pullingImage ? 'Installing Redroid Image…' : 'Install Redroid Image'}
                </button>
              </div>
            ) : (
              <div className="space-y-3">
                <div className="p-4 rounded-xl bg-slate-950/80 border border-indigo-700/50 space-y-3">
                  <div className="space-y-1.5">
                    <Line ok={true}>
                      Docker Engine siap &amp; image <span className="font-mono text-indigo-300">{redroidStatus.image_name}</span> tersedia.
                    </Line>
                    <Line>
                      Auto-provisioning: ADB connect otomatis, tanam UUID anchor, dan instal Split APKs Nusuk dari <span className="font-mono">Nusuk_Backup/</span>.
                    </Line>
                  </div>

                  {/* Advanced / Migration Options */}
                  <div className="p-3 rounded-lg bg-slate-900/70 border border-slate-800 space-y-1.5">
                    <div className="text-[11px] font-semibold text-slate-300">
                      Advanced / Migration Options
                    </div>
                    <label
                      htmlFor="redroid-bind-uuid"
                      className="block text-[11px] font-medium text-slate-400"
                    >
                      Bind to Existing Identity (Migration)
                    </label>
                    <select
                      id="redroid-bind-uuid"
                      value={bindToUuid}
                      onChange={(e) => setBindToUuid(e.target.value)}
                      disabled={deployingRedroid || loadingMigrationTargets}
                      className="w-full px-2.5 py-2 rounded-lg bg-slate-950 border border-slate-700 text-xs text-slate-200 focus:outline-none focus:border-indigo-500 disabled:opacity-60"
                    >
                      <option value="">Create as Brand New Instance</option>
                      {migrationTargets.map((item) => (
                        <option key={item.uuid} value={item.uuid}>
                          {item.name} - {item.status}{item.isArchived ? ' (Arsip)' : ''}
                        </option>
                      ))}
                    </select>
                    <p className="text-[10px] text-slate-400 leading-relaxed">
                      If selected, this Redroid will inherit the quiz history, limits, and keys of the old device.
                    </p>
                  </div>

                  <button
                    type="button"
                    onClick={handleDeployRedroid}
                    disabled={deployingRedroid}
                    className="w-full py-2.5 px-4 bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 text-white rounded-xl font-semibold flex items-center justify-center gap-2 shadow-lg shadow-indigo-600/25 transition active:scale-[0.99]"
                  >
                    {deployingRedroid ? (
                      <Loader2 className="w-4 h-4 animate-spin" />
                    ) : (
                      <Rocket className="w-4 h-4" />
                    )}
                    {deployingRedroid ? 'Deploying…' : 'Deploy New Instance (Android 9)'}
                  </button>

                  <p className="text-[10px] text-slate-400 text-center font-mono">
                    Default spec: 720x1280, 320DPI, 1 CPU, 1536MB RAM
                  </p>
                </div>

                {/* Running Redroid Containers */}
                {Array.isArray(redroidStatus.containers) && redroidStatus.containers.length > 0 && (
                  <div className="p-3 rounded-xl bg-slate-950/60 border border-slate-800 space-y-2">
                    <div className="text-[11px] font-semibold text-slate-300 flex items-center justify-between">
                      <span>Active Redroid Containers ({redroidStatus.containers.length})</span>
                    </div>
                    <div className="space-y-1.5 max-h-36 overflow-y-auto pr-0.5">
                      {redroidStatus.containers.map((c) => (
                        <div
                          key={c.container_id}
                          className="flex items-center justify-between gap-2 px-2.5 py-1.5 rounded-lg bg-slate-900 border border-slate-800 text-[11px]"
                        >
                          <div className="min-w-0">
                            <div className="font-mono text-slate-200 truncate">{c.container_name}</div>
                            <div className="text-[10px] text-slate-500 font-mono">
                              {c.adb_serial || `port ${c.adb_port ?? '?'}`} · {c.status}
                            </div>
                          </div>
                          <button
                            type="button"
                            disabled={destroyingId === c.container_id}
                            onClick={() => handleDestroyRedroid(c.container_id, c.container_name)}
                            className="px-2 py-1 rounded-lg bg-rose-600/20 hover:bg-rose-600/30 text-rose-300 border border-rose-500/40 text-[10px] font-medium flex items-center gap-1 disabled:opacity-40 shrink-0"
                            title="Hentikan container & soft-delete instance"
                          >
                            {destroyingId === c.container_id ? (
                              <Loader2 className="w-3 h-3 animate-spin" />
                            ) : (
                              <Trash2 className="w-3 h-3" />
                            )}
                            Destroy
                          </button>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            )}
          </div>
        )}

        {error && (
          <div className="p-2.5 rounded-xl bg-rose-950/40 border border-rose-800/60 text-rose-300 text-[11px] break-words">
            {error}
          </div>
        )}

        {tab !== 'redroid' && result && (
          <div className="p-3 rounded-xl bg-slate-950/70 border border-slate-800 space-y-2">
            <Line ok={!!result.adb_ok}>
              ADB ({(result.connection_type || tab).toUpperCase()} · <span className="font-mono">{result.adb_serial}</span>):{' '}
              {result.adb_ok ? 'Terhubung' : 'Tidak dapat terhubung'}
              {result.model ? ` [${result.model}]` : ''}
              {result.message ? ` — ${result.message}` : ''}
            </Line>
            {result.adb_ok && (
              <Line ok={result.nusuk_installed === null ? undefined : result.nusuk_installed} warn={result.nusuk_installed === null}>
                Aplikasi Nusuk:{' '}
                {result.nusuk_installed === true
                  ? 'Terpasang'
                  : result.nusuk_installed === false
                    ? 'Belum terpasang'
                    : 'Tidak dapat dipastikan'}
              </Line>
            )}
            {result.already_registered && (
              <Line ok={false}>
                Perangkat ini sudah terdaftar
                {result.known_device ? ` sebagai "${result.known_device.name}"` : ''}.
              </Line>
            )}
            {result.known_device && !result.already_registered && !result.clone_conflict && (
              <Line warn>
                Terdeteksi perangkat lama (Device UUID cocok): <strong>{result.known_device.name}</strong> (
                <span className="font-mono">{result.known_device.address}</span>).{' '}
                {result.known_device.is_deleted
                  ? 'Instance terarsip ini akan dipulihkan beserta seluruh riwayatnya.'
                  : 'Koneksi akan diperbarui tanpa duplikasi dan seluruh histori kuis/key tetap utuh.'}
              </Line>
            )}
            {result.clone_conflict && (
              <div className="p-2 rounded-lg bg-amber-950/30 border border-amber-700/40 space-y-1.5">
                <Line warn>
                  Konflik UUID aktif bersamaan di koneksi lain (<span className="font-mono">{result.known_device?.address}</span>).
                  Cabut/putuskan koneksi lama atau centang untuk membuat <strong>UUID baru</strong>.
                </Line>
                <label className="flex items-center gap-2 text-[11px] text-amber-200 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={forceNew}
                    onChange={(e) => setForceNew(e.target.checked)}
                    className="accent-amber-500"
                  />
                  Ya, buat UUID baru (force new uuid)
                </label>
              </div>
            )}
            {!result.can_save && !result.already_registered && (
              <Line ok={false}>Instance belum dapat disimpan. Periksa koneksi di atas.</Line>
            )}
          </div>
        )}

        <div className="flex justify-end gap-2 text-xs pt-1">
          <button onClick={onClose} className="px-4 py-2 text-slate-400 hover:text-white rounded-xl hover:bg-slate-800">
            {tab === 'redroid' ? 'Tutup' : 'Batal'}
          </button>
          {tab !== 'redroid' && (
            <button
              onClick={save}
              disabled={!canSave}
              className="px-4 py-2 bg-emerald-600 hover:bg-emerald-500 disabled:opacity-40 disabled:cursor-not-allowed text-white rounded-xl font-semibold flex items-center gap-1.5"
            >
              {saving ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Save className="w-3.5 h-3.5" />}
              Simpan &amp; Tambahkan Instance
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
