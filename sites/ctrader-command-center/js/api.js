const CFG = window.APP_CONFIG || {};

function getHeaders(extra = {}) {
  const headers = { 'Content-Type': 'application/json', ...extra };
  if (CFG.adminApiKey) headers['x-admin-key'] = CFG.adminApiKey;
  return headers;
}

async function v2(path, options = {}) {
  const resp = await fetch(`${CFG.v2ApiBase}${path}`, {
    credentials: 'include',
    ...options,
    headers: getHeaders(options.headers),
  });
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) throw new Error(data.detail || data.error || `HTTP ${resp.status}`);
  return data;
}

async function ds(path, options = {}) {
  const resp = await fetch(`${CFG.dataserviceBase}${path}`, {
    credentials: 'include',
    ...options,
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
  });
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) throw new Error(data.detail || data.error || `HTTP ${resp.status}`);
  return data;
}

export const API = {
  health: () => v2('/health'),
  listAccounts: () => v2('/api/accounts'),
  accountState: (name) => v2(`/api/accounts/${encodeURIComponent(name)}/state`),
  listSignals: (limit = 50) => v2(`/api/signals?limit=${limit}`),
  signalExecutions: (chatId, messageId) => v2(`/api/signals/${encodeURIComponent(chatId)}/${messageId}/executions`),
  injectSignal: (payload) => v2('/api/signals/inject', { method: 'POST', body: JSON.stringify(payload) }),
  listExecutions: (limit = 50) => v2(`/api/executions?limit=${limit}`),
  agentLogs: (limit = 50) => v2(`/api/agent-logs?limit=${limit}`),
  executionsStream: () => new EventSource(`${CFG.v2ApiBase}/api/executions/stream`),

  dataservice: {
    price: (symbol) => ds(`/data/${encodeURIComponent(symbol)}`),
    context: (symbol) => ds(`/context/${encodeURIComponent(symbol)}`),
    quality: (symbol) => ds(`/quality/${encodeURIComponent(symbol)}`),
  },
};

window.API = API;
