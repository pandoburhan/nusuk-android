import React, { useState, useRef, useEffect } from 'react';
import { Lock, Eye, EyeOff, ShieldCheck, AlertCircle, Loader2, ArrowRight } from 'lucide-react';
import { authApi, errorDetail } from '../api/client';

export default function LockScreen({ onUnlock, noticeMessage }) {
  const [password, setPassword] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [loading, setLoading] = useState(false);
  const [errorMsg, setErrorMsg] = useState('');
  const inputRef = useRef(null);

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!password || loading) return;

    setLoading(true);
    setErrorMsg('');
    try {
      const res = await authApi.verify(password);
      const token = res.data?.access_token;
      if (!token) {
        setErrorMsg('Respons autentikasi tidak valid dari server.');
        return;
      }
      setPassword('');
      onUnlock(token);
    } catch (err) {
      setErrorMsg(errorDetail(err, 'Password salah atau server tidak merespons.'));
      inputRef.current?.focus();
      inputRef.current?.select();
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100 flex items-center justify-center p-4 relative overflow-hidden">
      {/* Subtle Ambient Background */}
      <div className="absolute inset-0 pointer-events-none overflow-hidden">
        <div className="absolute -top-32 left-1/2 -translate-x-1/2 w-[560px] h-[360px] bg-emerald-600/10 blur-[120px] rounded-full" />
        <div className="absolute -bottom-32 right-1/4 w-[420px] h-[300px] bg-sky-600/10 blur-[120px] rounded-full" />
      </div>

      <div className="w-full max-w-md relative z-10">
        <div className="bg-slate-900/90 backdrop-blur-xl border border-slate-800 rounded-2xl shadow-2xl shadow-black/60 p-7">
          {/* Header / Branding */}
          <div className="flex flex-col items-center text-center mb-6">
            <div className="w-14 h-14 rounded-2xl bg-gradient-to-br from-emerald-500/20 to-sky-500/20 border border-emerald-500/30 flex items-center justify-center mb-4 shadow-lg shadow-emerald-950/50">
              <Lock className="w-6 h-6 text-emerald-400" />
            </div>
            <h1 className="text-lg font-bold tracking-tight text-white">
              Nusuk Automation Hub
            </h1>
            <p className="text-xs text-slate-400 mt-1">
              Dashboard terkunci. Masukkan password akses untuk melanjutkan.
            </p>
          </div>

          {/* Session Expired / Server Restart Notice */}
          {noticeMessage && !errorMsg && (
            <div className="mb-4 px-3.5 py-2.5 rounded-xl bg-amber-500/10 border border-amber-500/30 text-amber-300 text-xs flex items-start gap-2.5">
              <AlertCircle className="w-4 h-4 shrink-0 mt-0.5 text-amber-400" />
              <span>{noticeMessage}</span>
            </div>
          )}

          {/* Error Message */}
          {errorMsg && (
            <div className="mb-4 px-3.5 py-2.5 rounded-xl bg-rose-500/10 border border-rose-500/30 text-rose-300 text-xs flex items-start gap-2.5">
              <AlertCircle className="w-4 h-4 shrink-0 mt-0.5 text-rose-400" />
              <span>{errorMsg}</span>
            </div>
          )}

          <form onSubmit={handleSubmit} className="space-y-4">
            <div>
              <label
                htmlFor="dashboard-password"
                className="block text-[11px] font-semibold uppercase tracking-wider text-slate-400 mb-1.5"
              >
                Password Akses
              </label>
              <div className="relative">
                <input
                  id="dashboard-password"
                  ref={inputRef}
                  type={showPassword ? 'text' : 'password'}
                  value={password}
                  onChange={(e) => {
                    setPassword(e.target.value);
                    if (errorMsg) setErrorMsg('');
                  }}
                  placeholder="Masukkan password..."
                  autoComplete="off"
                  autoFocus
                  disabled={loading}
                  className="w-full bg-slate-950/90 border border-slate-700/80 focus:border-emerald-500 focus:ring-2 focus:ring-emerald-500/20 rounded-xl pl-3.5 pr-10 py-2.5 text-sm text-white placeholder-slate-500 outline-none transition disabled:opacity-60"
                />
                <button
                  type="button"
                  onClick={() => setShowPassword((prev) => !prev)}
                  tabIndex={-1}
                  aria-label={showPassword ? 'Sembunyikan password' : 'Tampilkan password'}
                  className="absolute right-2.5 top-1/2 -translate-y-1/2 p-1 text-slate-400 hover:text-slate-200 transition rounded-lg"
                >
                  {showPassword ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                </button>
              </div>
            </div>

            <button
              type="submit"
              disabled={loading || !password}
              className="w-full bg-emerald-600 hover:bg-emerald-500 active:bg-emerald-700 disabled:bg-slate-800 disabled:text-slate-500 text-white font-semibold text-sm py-2.5 px-4 rounded-xl transition flex items-center justify-center gap-2 shadow-lg shadow-emerald-950/40 cursor-pointer disabled:cursor-not-allowed"
            >
              {loading ? (
                <>
                  <Loader2 className="w-4 h-4 animate-spin" />
                  Memverifikasi...
                </>
              ) : (
                <>
                  <ShieldCheck className="w-4 h-4" />
                  Unlock Dashboard
                  <ArrowRight className="w-4 h-4" />
                </>
              )}
            </button>
          </form>

          <div className="mt-5 pt-4 border-t border-slate-800/80 flex items-center justify-center gap-1.5 text-[11px] text-slate-500">
            <Lock className="w-3 h-3" />
            <span>Sesi bersifat in-memory (terkunci otomatis saat tab ditutup atau refresh)</span>
          </div>
        </div>
      </div>
    </div>
  );
}
