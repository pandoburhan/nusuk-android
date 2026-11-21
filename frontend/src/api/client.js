import axios from 'axios';

const api = axios.create({
  baseURL: '/api',
  timeout: 60000,
});

// Purely in-memory auth token & 401 handler (never stored in Cookie, localStorage, or sessionStorage)
let _inMemoryToken = null;
let _unauthorizedHandler = null;

export const setAuthToken = (token) => {
  _inMemoryToken = token || null;
};

export const getAuthToken = () => _inMemoryToken;

export const setUnauthorizedHandler = (fn) => {
  _unauthorizedHandler = typeof fn === 'function' ? fn : null;
};

api.interceptors.request.use((config) => {
  if (_inMemoryToken) {
    config.headers = config.headers || {};
    config.headers.Authorization = `Bearer ${_inMemoryToken}`;
  }
  return config;
});

api.interceptors.response.use(
  (response) => response,
  (error) => {
    const status = error?.response?.status;
    const reqUrl = error?.config?.url || '';
    if (status === 401 && !reqUrl.includes('/auth/verify')) {
      _inMemoryToken = null;
      if (_unauthorizedHandler) {
        _unauthorizedHandler();
      }
    }
    return Promise.reject(error);
  }
);

export const authApi = {
  verify: (password) => api.post('/auth/verify', { password }),
};

export const errorDetail = (err, fallback = 'Terjadi kesalahan') => {
  const d = err?.response?.data?.detail;
  if (typeof d === 'string') return d;
  if (Array.isArray(d)) return d.map((x) => x.msg || JSON.stringify(x)).join('; ');
  if (d && typeof d === 'object') return d.message || JSON.stringify(d);
  return err?.response?.data?.message || err?.message || fallback;
};

export const instancesApi = {
  list: () => api.get('/instances'),
  archived: () => api.get('/instances/archived'),
  discover: () => api.get('/instances/discover'),
  pair: (ip, pairing_port, pairing_code) =>
    api.post('/instances/pair', { ip, pairing_port, pairing_code }),
  testConnection: (payloadOrIp, port) => {
    const body =
      typeof payloadOrIp === 'object' && payloadOrIp !== null
        ? payloadOrIp
        : { connection_type: 'tcp', ip: payloadOrIp, port };
    return api.post('/instances/test-connection', body);
  },
  create: (payload) => api.post('/instances', payload),
  update: (uuid, payload) => api.patch(`/instances/${uuid}`, payload),
  remove: (uuid) => api.delete(`/instances/${uuid}`),
  start: (uuid, mode, stop_mode = 'limit', stop_limit = 100) =>
    api.post(`/instances/${uuid}/start`, { mode, stop_mode, stop_limit }),
  stop: (uuid) => api.post(`/instances/${uuid}/stop`),
  forceRestartApp: (uuid) => api.post(`/instances/${uuid}/force-restart-app`),
  forceRestartDevice: (uuid) => api.post(`/instances/${uuid}/force-restart-device`),
  reconnect: (uuid) => api.post(`/instances/${uuid}/reconnect`),
  submitLoginCredentials: (uuid, email, password) =>
    api.post(`/instances/${uuid}/login-credentials`, { email, password }),
  submitLoginOtp: (uuid, otp_code) =>
    api.post(`/instances/${uuid}/login-otp`, { otp_code }),
  // Returns a blob. Throws axios error: 409 {detail:'preview_disabled'}, 503 offline.
  screenshot: (uuid, signal) =>
    api.get(`/instances/${uuid}/screenshot`, {
      responseType: 'blob',
      timeout: 15000,
      params: { t: Date.now() },
      signal,
    }),
};

export const controlApi = {
  getStatus: () => api.get('/control/status'),
  bulkStart: (device_uuids, mode, device_stop_conditions = null, stop_mode = 'limit', stop_limit = 100) =>
    api.post('/control/bulk/start', { device_uuids, mode, device_stop_conditions, stop_mode, stop_limit }),
  bulkStop: (device_uuids) => api.post('/control/bulk/stop', { device_uuids }),
  bulkForceRestart: (device_uuids, kind) =>
    api.post('/control/bulk/force-restart', { device_uuids, kind }),
  reconnectAll: () => api.post('/control/reconnect-all'),
};

export const keysApi = {
  listKeys: (page = 1, pageSize = 20, status = 'all', search = '', deviceUuid = '') =>
    api.get('/keys', {
      params: {
        page,
        page_size: pageSize,
        status,
        search,
        ...(deviceUuid ? { device_uuid: deviceUuid } : {}),
      },
    }),
  getClaims: (id) => api.get(`/keys/${id}/claims`),
  addManual: (code) => api.post('/keys/manual', { code }),
  addBulk: (raw_text) => api.post('/keys/bulk', { raw_text }),
  resetKey: (id, deviceUuid = '') =>
    api.post(`/keys/${id}/reset`, null, {
      params: deviceUuid ? { device_uuid: deviceUuid } : {},
    }),
  deleteKey: (id) => api.delete(`/keys/${id}`),
  clearInvalid: () => api.delete('/keys/actions/clear-invalid'),
  exportUnused: (deviceUuid = '') =>
    api.get('/keys/actions/export', {
      params: deviceUuid ? { device_uuid: deviceUuid } : {},
      responseType: 'blob',
    }),
};

export const analyticsApi = {
  getTime: () => api.get('/analytics/time'),
  getKPI: (tz = 'wib') => api.get('/analytics/kpi', { params: { tz } }),
  getCharts: (timeframe = '24h', deviceUuid = 'all', tz = 'wib') =>
    api.get('/analytics/charts', { params: { timeframe, device_uuid: deviceUuid, tz } }),
};

export const logsApi = {
  getQuizLogs: (page = 1, pageSize = 20, result = 'all', door = 'all', deviceUuid = 'all') =>
    api.get('/logs/quiz', {
      params: { page, page_size: pageSize, result, door, device_uuid: deviceUuid },
    }),
  getSystemLogs: (page = 1, pageSize = 50, severity = 'ALL', deviceUuid = 'all') =>
    api.get('/logs/system', {
      params: { page, page_size: pageSize, severity, device_uuid: deviceUuid },
    }),
  getDevices: () => api.get('/logs/devices'),
};

export const settingsApi = {
  getSettings: () => api.get('/settings'),
  updateSettings: (payload) => api.post('/settings', payload),
  testAI: (payload) => api.post('/settings/test-ai', payload),
};

export const redroidApi = {
  getStatus: (androidVersion = 'a11_magisk_ndk_amd') =>
    api.get('/redroid/status', { params: { android_version: androidVersion } }),
  listContainers: () => api.get('/redroid/containers'),
  installImage: (payload = {}) => api.post('/redroid/install-image', payload),
  deployInstance: (payload = {}) => api.post('/redroid/deploy', payload),
  startContainer: (containerId) => api.post(`/redroid/${encodeURIComponent(containerId)}/start`),
  stopContainer: (containerId) => api.post(`/redroid/${encodeURIComponent(containerId)}/stop`),
  destroyInstance: (containerId) => api.delete(`/redroid/destroy/${encodeURIComponent(containerId)}`),
};

export const redroid = redroidApi;

export default api;
