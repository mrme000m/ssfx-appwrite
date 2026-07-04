/**
 * SSFX HQ — API clients for auth functions, SSFX v2 server, agent harness, and data service.
 * Supports both new (post-consolidation) and legacy config keys during cutover.
 */
window.API = (function () {
  const CFG = window.APP_CONFIG || {};

  // Backward-compatible base URLs (new names preferred, old names as fallback)
  const API_BASE = CFG.apiBase || CFG.v2ApiBase || '';
  const MARKET_BASE = CFG.marketBase || CFG.dataserviceBase || '';
  const AI_BASE = CFG.aiBase || CFG.agentHarnessBase || '';
  const RESEARCH_BASE = CFG.researchBase || CFG.pplxAgentBase || '';
  const ADMIN_KEY = CFG.adminKey || CFG.v2AdminKey || '';
  const MARKET_API_KEY = CFG.marketApiKey || CFG.dataserviceApiKey || '';

  async function fetchJson(url, options = {}) {
    const headers = {
      'Content-Type': 'application/json',
      ...(options.headers || {}),
    };
    const resp = await fetch(url, { credentials: 'include', ...options, headers });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) {
      throw new Error(data.detail || data.error || data.message || `HTTP ${resp.status}`);
    }
    return data;
  }

  async function fetchApi(url, options = {}) {
    const headers = {
      'Content-Type': 'application/json',
      ...(options.headers || {}),
    };
    if (ADMIN_KEY) {
      headers['x-admin-key'] = ADMIN_KEY;
    }
    return fetchJson(url, { ...options, headers });
  }

  async function fetchMarket(url, options = {}) {
    const headers = {
      'Content-Type': 'application/json',
      ...(options.headers || {}),
    };
    if (MARKET_API_KEY) {
      headers['Authorization'] = `Bearer ${MARKET_API_KEY}`;
    }
    return fetchJson(url, { ...options, headers });
  }

  const AuthAPI = {
    session: () => fetchJson(`${CFG.authDomain}/session`),
    logout: () => fetchJson(`${CFG.authDomain}/logout`, { method: 'POST' }),
    adminSlaves: () => fetchJson(`${CFG.authDomain}/admin/slaves`),
    adminSlaveAccounts: (grantId) => fetchJson(`${CFG.authDomain}/admin/slaves/${encodeURIComponent(grantId)}/accounts`),
    adminDeleteSlaveAccount: (grantId, accountId) => fetchJson(`${CFG.authDomain}/admin/slaves/${encodeURIComponent(grantId)}/accounts/${encodeURIComponent(accountId)}`, { method: 'DELETE' }),
    adminUnlinkSlave: (grantId) => fetchJson(`${CFG.authDomain}/admin/slaves/unlink`, { method: 'POST', body: JSON.stringify({ grant_id: grantId }) }),
    adminResetSlave: (grantId) => fetchJson(`${CFG.authDomain}/admin/slaves/${encodeURIComponent(grantId)}/reset`, { method: 'POST' }),
    pinLogin: (body) => fetchJson(`${CFG.pinDomain}/pin-login`, { method: 'POST', body: JSON.stringify(body) }),
    setCredentials: (body) => fetchJson(`${CFG.pinDomain}/set-credentials`, { method: 'POST', body: JSON.stringify(body) }),
    pinResetRequest: (body) => fetchJson(`${CFG.pinDomain}/pin-reset/request`, { method: 'POST', body: JSON.stringify(body) }),
    pinResetConfirm: (body) => fetchJson(`${CFG.pinDomain}/pin-reset/confirm`, { method: 'POST', body: JSON.stringify(body) }),
    register: (body) => fetchJson(`${CFG.pinDomain}/register`, { method: 'POST', body: JSON.stringify(body) }),
  };

  const API = {
    health: () => fetchApi(`${API_BASE}/health`),
    listAccounts: () => fetchApi(`${API_BASE}/api/accounts`),
    accountState: (name) => fetchApi(`${API_BASE}/api/accounts/${encodeURIComponent(name)}/state`),
    updateAccount: (name, patch) => fetchApi(`${API_BASE}/api/accounts/${encodeURIComponent(name)}`, { method: 'PATCH', body: JSON.stringify(patch) }),
    listSignals: (limit = 50) => fetchApi(`${API_BASE}/api/signals?limit=${limit}`),
    signalExecutions: (chatId, messageId) => fetchApi(`${API_BASE}/api/signals/${encodeURIComponent(chatId)}/${messageId}/executions`),
    injectSignal: (payload) => fetchApi(`${API_BASE}/api/signals/inject`, { method: 'POST', body: JSON.stringify(payload) }),
    listExecutions: (limit = 50) => fetchApi(`${API_BASE}/api/executions?limit=${limit}`),
    agentLogs: (limit = 50) => fetchApi(`${API_BASE}/api/agent-logs?limit=${limit}`),
    signalExperience: () => fetchApi(`${API_BASE}/api/signal-experience`).catch(() => ({})),
    executionsStream: () => new EventSource(`${API_BASE}/api/executions/stream`, { withCredentials: true }),
  };

  const AgentAPI = {
    health: () => fetchJson(`${AI_BASE}/health`),
    signalIntent: (payload) => fetchJson(`${AI_BASE}/agent/v1/signal/intent`, { method: 'POST', body: JSON.stringify(payload) }),
    entryDecision: (payload) => fetchJson(`${AI_BASE}/agent/v1/entry/decision`, { method: 'POST', body: JSON.stringify(payload) }),
    lifecyclePlan: (payload) => fetchJson(`${AI_BASE}/agent/v1/lifecycle/plan`, { method: 'POST', body: JSON.stringify(payload) }),
  };

  const ResearchAPI = {
    health: () => fetchJson(`${RESEARCH_BASE}/health`).catch(() => ({})),
  };

  const MarketAPI = {
    health: () => fetchMarket(`${MARKET_BASE}/api/v1/health`),
    goldQuant: () => fetchMarket(`${MARKET_BASE}/api/v1/gold/quant`).catch(() => ({})),
    goldMtf: () => fetchMarket(`${MARKET_BASE}/api/v1/gold/mtf`).catch(() => ({})),
    feedStatus: () => fetchMarket(`${MARKET_BASE}/api/v1/feed/status`).catch(() => ({})),
    symbols: () => fetchMarket(`${MARKET_BASE}/api/v1/symbols`).catch(() => []),
    quality: (symbol) => {
      const path = symbol ? `/api/v1/quality/${encodeURIComponent(symbol)}` : '/api/v1/quality';
      return fetchMarket(`${MARKET_BASE}${path}`).catch(() => ({}));
    },
  };

  function getTablesDB() {
    if (!window.Appwrite) return null;
    const client = new window.Appwrite.Client();
    client.setEndpoint(CFG.endpoint).setProject(CFG.projectId);
    return new window.Appwrite.TablesDB(client);
  }

  // Backward-compatible aliases during hostname/naming cutover
  const V2API = API;
  const DataAPI = MarketAPI;

  return {
    CFG,
    fetchJson,
    AuthAPI,
    API,
    V2API,
    AgentAPI,
    ResearchAPI,
    MarketAPI,
    DataAPI,
    getTablesDB,
  };
})();
