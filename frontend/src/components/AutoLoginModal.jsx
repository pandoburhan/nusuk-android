import React, { useState, useEffect, useRef } from 'react';
import {
  X,
  Mail,
  Lock,
  Eye,
  EyeOff,
  KeyRound,
  ShieldCheck,
  Loader2,
  AlertTriangle,
  CheckCircle2,
  LogIn,
} from 'lucide-react';
import { instancesApi, errorDetail } from '../api/client';
import { StatusBadge, ConnectionBadge } from './common';

const STEP_META = [
  { key: 'creds', label: '1. Kredensial' },
  { key: 'otp', label: '2. Verifikasi OTP' },
  { key: 'done', label: '3. Dashboard' },
];

export default function AutoLoginModal({ inst, onClose, toast }) {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [otpDigits, setOtpDigits] = useState(['', '', '', '']);
  const [submitting, setSubmitting] = useState(false);
  const [localError, setLocalError] = useState('');

  const otpRefs = [useRef(null), useRef(null), useRef(null), useRef(null)];
  const emailRef = useRef(null);
  const prevLoginStatusRef = useRef(inst?.login_status);
  const closedOnSuccessRef = useRef(false);

  const loginStatus = inst?.login_status || 'LOGGED_OUT';
  const serverError = inst?.login_error || '';
  const displayError = localError || serverError;

  // Close on ESC
  useEffect(() => {
    const onKeyDown = (e) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [onClose]);

  // React to live telemetry transitions
  useEffect(() => {
    const prev = prevLoginStatusRef.current;
    prevLoginStatusRef.current = loginStatus;

    if (loginStatus === 'AWAITING_CREDENTIALS') {
      setSubmitting(false);
      if (prev !== 'AWAITING_CREDENTIALS') {
        setLocalError('');
        setPassword('');
        setTimeout(() => emailRef.current?.focus(), 60);
      }
    } else if (loginStatus === 'AWAITING_OTP') {
      setSubmitting(false);
      if (prev !== 'AWAITING_OTP') {
        setLocalError('');
        setOtpDigits(['', '', '', '']);
        setTimeout(() => otpRefs[0].current?.focus(), 80);
      }
    } else if (loginStatus === 'PROCESSING_LOGIN') {
      setLocalError('');
    } else if (loginStatus === 'LOGGED_IN' && !closedOnSuccessRef.current) {
      closedOnSuccessRef.current = true;
      setSubmitting(false);
      setPassword('');
      setOtpDigits(['', '', '', '']);
      toast?.({
        type: 'success',
        title: inst?.name || 'Auto-Login',
        message: 'Auto-Login berhasil! Perangkat telah masuk ke Dashboard Nusuk.',
      });
      const timer = setTimeout(() => {
        onClose();
      }, 1100);
      return () => clearTimeout(timer);
    }
    return undefined;
  }, [loginStatus, inst?.name, onClose, toast]);

  // Reset OTP inputs if a fresh server error arrives while staying in AWAITING_OTP
  useEffect(() => {
    if (loginStatus === 'AWAITING_OTP' && serverError) {
      setOtpDigits(['', '', '', '']);
      setTimeout(() => otpRefs[0].current?.focus(), 80);
    }
  }, [loginStatus, serverError]);

  if (!inst) return null;

  const activeStepIdx =
    loginStatus === 'LOGGED_IN'
      ? 2
      : loginStatus === 'AWAITING_OTP' || (loginStatus === 'PROCESSING_LOGIN' && inst.current_screen === 'SCREEN_VERIFY_OTP_FORM')
        ? 1
        : 0;

  const handleSubmitCredentials = async (e) => {
    e.preventDefault();
    const cleanEmail = email.trim();
    if (!cleanEmail || !cleanEmail.includes('@')) {
      setLocalError('Masukkan alamat email yang valid.');
      return;
    }
    if (!password) {
      setLocalError('Password tidak boleh kosong.');
      return;
    }

    setLocalError('');
    setSubmitting(true);
    try {
      await instancesApi.submitLoginCredentials(inst.uuid, cleanEmail, password);
      // Clear password from React component state immediately after dispatch
      setPassword('');
    } catch (err) {
      setLocalError(errorDetail(err, 'Gagal mengirim kredensial Auto-Login.'));
      setSubmitting(false);
    }
  };

  const handleOtpChange = (index, rawVal) => {
    const digitsOnly = rawVal.replace(/\D/g, '');
    if (!digitsOnly) {
      setOtpDigits((prev) => {
        const next = [...prev];
        next[index] = '';
        return next;
      });
      return;
    }

    // Handle pasting multiple digits (e.g. 4-digit code)
    if (digitsOnly.length > 1) {
      const next = ['', '', '', ''];
      for (let i = 0; i < Math.min(4, digitsOnly.length); i += 1) {
        next[i] = digitsOnly[i];
      }
      setOtpDigits(next);
      const focusIdx = Math.min(3, digitsOnly.length);
      otpRefs[focusIdx].current?.focus();
      return;
    }

    setOtpDigits((prev) => {
      const next = [...prev];
      next[index] = digitsOnly[0];
      return next;
    });
    if (index < 3) {
      otpRefs[index + 1].current?.focus();
    }
  };

  const handleOtpKeyDown = (index, e) => {
    if (e.key === 'Backspace' && !otpDigits[index] && index > 0) {
      otpRefs[index - 1].current?.focus();
    } else if (e.key === 'ArrowLeft' && index > 0) {
      otpRefs[index - 1].current?.focus();
    } else if (e.key === 'ArrowRight' && index < 3) {
      otpRefs[index + 1].current?.focus();
    }
  };

  const otpValue = otpDigits.join('');

  const handleSubmitOtp = async (e) => {
    e.preventDefault();
    if (otpValue.length !== 4) {
      setLocalError('Masukkan tepat 4 digit kode OTP dari email Anda.');
      return;
    }
    setLocalError('');
    setSubmitting(true);
    try {
      await instancesApi.submitLoginOtp(inst.uuid, otpValue);
    } catch (err) {
      setLocalError(errorDetail(err, 'Gagal mengirim kode OTP.'));
      setSubmitting(false);
    }
  };

  const isWorkerStopped = !['RUNNING', 'RECOVERING'].includes(inst.status);

  return (
    <div
      className="fixed inset-0 z-[65] bg-slate-950/80 backdrop-blur-sm flex items-center justify-center p-4"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="bg-slate-900 border border-slate-700/80 rounded-2xl max-w-md w-full shadow-2xl overflow-hidden">
        {/* Header */}
        <div className="px-5 py-4 bg-slate-900/95 border-b border-slate-800 flex items-center justify-between gap-3">
          <div className="min-w-0">
            <div className="flex items-center gap-2 text-sm font-bold text-white">
              <LogIn className="w-4 h-4 text-amber-400 shrink-0" />
              <span className="truncate">Auto-Login — {inst.name}</span>
            </div>
            <div className="flex items-center gap-1.5 mt-1 text-[10px] text-slate-400 font-mono">
              <span className="truncate">{inst.address}</span>
              <ConnectionBadge type={inst.connection_type} compact />
              <StatusBadge status={inst.status} />
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="p-1.5 text-slate-400 hover:text-white rounded-lg hover:bg-slate-800 transition shrink-0"
            title="Tutup modal (ESC)"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* Progress Stepper */}
        <div className="px-5 pt-3.5 pb-2 bg-slate-950/40 border-b border-slate-800/70">
          <div className="grid grid-cols-3 gap-2">
            {STEP_META.map((s, idx) => {
              const done = idx < activeStepIdx || loginStatus === 'LOGGED_IN';
              const current = idx === activeStepIdx && loginStatus !== 'LOGGED_IN';
              return (
                <div
                  key={s.key}
                  className={`text-center py-1 px-2 rounded-lg text-[10px] font-semibold border transition ${
                    done
                      ? 'bg-emerald-500/15 text-emerald-300 border-emerald-500/40'
                      : current
                        ? 'bg-amber-500/15 text-amber-300 border-amber-500/40'
                        : 'bg-slate-900 text-slate-500 border-slate-800'
                  }`}
                >
                  {s.label}
                </div>
              );
            })}
          </div>
        </div>

        {/* Body */}
        <div className="p-5 space-y-4">
          {displayError && (
            <div className="p-3 rounded-xl bg-rose-950/60 border border-rose-500/50 text-rose-200 text-xs flex items-start gap-2">
              <AlertTriangle className="w-4 h-4 text-rose-400 shrink-0 mt-0.5" />
              <div className="min-w-0 break-words">{displayError}</div>
            </div>
          )}

          {isWorkerStopped && loginStatus !== 'LOGGED_IN' ? (
            <div className="py-6 text-center space-y-2.5">
              <AlertTriangle className="w-10 h-10 text-amber-400 mx-auto" />
              <div className="text-sm font-semibold text-white">Worker Tidak Sedang Berjalan</div>
              <p className="text-xs text-slate-400">
                Status perangkat saat ini <strong className="text-slate-200">{inst.status}</strong>. Jalankan
                bot (<strong>START</strong>) terlebih dahulu agar Auto-Login dapat mengontrol layar Nusuk.
              </p>
            </div>
          ) : loginStatus === 'LOGGED_IN' ? (
            <div className="py-7 text-center space-y-2.5">
              <CheckCircle2 className="w-12 h-12 text-emerald-400 mx-auto" />
              <div className="text-base font-bold text-emerald-300">Auto-Login Berhasil!</div>
              <p className="text-xs text-slate-400">
                Perangkat telah masuk ke Dashboard Nusuk dan melanjutkan otomatisasi.
              </p>
            </div>
          ) : loginStatus === 'PROCESSING_LOGIN' || submitting ? (
            <div className="py-7 text-center space-y-3">
              <div className="relative w-12 h-12 mx-auto flex items-center justify-center">
                <Loader2 className="w-11 h-11 text-sky-400 animate-spin" />
              </div>
              <div className="text-sm font-semibold text-white">Memproses Auto-Login di Emulator…</div>
              <p className="text-xs text-slate-300 px-2">{inst.current_step || 'Menavigasi aplikasi Nusuk...'}</p>
              <div className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-slate-950 border border-slate-800 text-[10px] font-mono text-slate-400">
                Layar: <span className="text-sky-300">{inst.current_screen || '—'}</span>
              </div>
            </div>
          ) : loginStatus === 'AWAITING_OTP' ? (
            <form onSubmit={handleSubmitOtp} className="space-y-4">
              <div className="space-y-1 text-center">
                <div className="inline-flex items-center justify-center w-10 h-10 rounded-full bg-amber-500/15 border border-amber-500/30 text-amber-300 mb-1">
                  <KeyRound className="w-5 h-5" />
                </div>
                <h4 className="text-sm font-semibold text-white">Masukkan Kode Verifikasi (OTP)</h4>
                <p className="text-xs text-slate-400">
                  Cek kotak masuk email akun Nusuk Anda dan masukkan 4 digit kode verifikasi.
                </p>
              </div>

              <div className="flex items-center justify-center gap-3 pt-1">
                {otpDigits.map((digit, idx) => (
                  <input
                    key={idx}
                    ref={otpRefs[idx]}
                    type="text"
                    inputMode="numeric"
                    autoComplete="one-time-code"
                    maxLength={4}
                    value={digit}
                    onChange={(e) => handleOtpChange(idx, e.target.value)}
                    onKeyDown={(e) => handleOtpKeyDown(idx, e)}
                    className="w-13 h-14 text-center text-2xl font-bold font-mono rounded-xl bg-slate-950 border border-slate-700 focus:border-amber-400 focus:ring-2 focus:ring-amber-400/20 text-white focus:outline-none transition"
                    aria-label={`Digit OTP ${idx + 1}`}
                  />
                ))}
              </div>

              <div className="flex items-center justify-end gap-2 pt-2">
                <button
                  type="button"
                  onClick={onClose}
                  className="px-4 py-2 rounded-xl text-xs text-slate-400 hover:text-white hover:bg-slate-800 transition"
                >
                  Tutup Sementara
                </button>
                <button
                  type="submit"
                  disabled={otpValue.length !== 4 || submitting}
                  className="px-5 py-2.5 rounded-xl text-xs font-semibold bg-amber-500 hover:bg-amber-400 disabled:opacity-40 text-slate-950 shadow-lg shadow-amber-500/20 flex items-center gap-1.5 transition"
                >
                  <ShieldCheck className="w-4 h-4" />
                  Verifikasi OTP
                </button>
              </div>
            </form>
          ) : (
            <form onSubmit={handleSubmitCredentials} className="space-y-3.5">
              <div className="space-y-1">
                <h4 className="text-sm font-semibold text-white">Login Akun Nusuk</h4>
                <p className="text-xs text-slate-400">
                  Masukkan Email dan Password akun Nusuk. Bot akan menavigasi layar login dan mengisi
                  kredensial secara otomatis.
                </p>
              </div>

              <label className="block space-y-1">
                <span className="text-xs font-medium text-slate-300 flex items-center gap-1.5">
                  <Mail className="w-3.5 h-3.5 text-slate-400" />
                  Email Address
                </span>
                <input
                  ref={emailRef}
                  type="email"
                  required
                  autoComplete="off"
                  placeholder="nama@email.com"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  className="w-full bg-slate-950 border border-slate-700 focus:border-orange-400 rounded-xl px-3.5 py-2.5 text-xs text-white placeholder-slate-600 focus:outline-none transition"
                />
              </label>

              <label className="block space-y-1">
                <span className="text-xs font-medium text-slate-300 flex items-center gap-1.5">
                  <Lock className="w-3.5 h-3.5 text-slate-400" />
                  Password
                </span>
                <div className="relative">
                  <input
                    type={showPassword ? 'text' : 'password'}
                    required
                    autoComplete="new-password"
                    placeholder="••••••••••••"
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    className="w-full bg-slate-950 border border-slate-700 focus:border-orange-400 rounded-xl pl-3.5 pr-10 py-2.5 text-xs text-white placeholder-slate-600 focus:outline-none transition"
                  />
                  <button
                    type="button"
                    onClick={() => setShowPassword((v) => !v)}
                    className="absolute right-2.5 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-200 p-1"
                    tabIndex={-1}
                    title={showPassword ? 'Sembunyikan password' : 'Tampilkan password'}
                  >
                    {showPassword ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                  </button>
                </div>
              </label>

              <div className="p-2.5 rounded-xl bg-slate-950/70 border border-slate-800 text-[11px] text-slate-400 flex items-start gap-2">
                <ShieldCheck className="w-4 h-4 text-emerald-400 shrink-0 mt-0.5" />
                <span>
                  Toggle <strong className="text-slate-200">Remember me</strong> otomatis dipastikan{' '}
                  <strong className="text-emerald-300">OFF</strong> dan password tidak pernah disimpan atau
                  dicatat di log sistem.
                </span>
              </div>

              <div className="flex items-center justify-end gap-2 pt-1">
                <button
                  type="button"
                  onClick={onClose}
                  className="px-4 py-2 rounded-xl text-xs text-slate-400 hover:text-white hover:bg-slate-800 transition"
                >
                  Batal
                </button>
                <button
                  type="submit"
                  disabled={!email.trim() || !password || submitting}
                  className="px-5 py-2.5 rounded-xl text-xs font-semibold bg-gradient-to-r from-orange-600 to-rose-600 hover:from-orange-500 hover:to-rose-500 disabled:opacity-40 text-white shadow-lg shadow-orange-600/25 flex items-center gap-1.5 transition"
                >
                  <LogIn className="w-3.5 h-3.5" />
                  Kirim &amp; Auto Login
                </button>
              </div>
            </form>
          )}
        </div>
      </div>
    </div>
  );
}
