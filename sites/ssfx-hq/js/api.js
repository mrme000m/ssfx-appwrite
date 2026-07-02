/**
 * SSFX HQ — API clients for auth functions, SSFX v2 server, agent harness, and data service.
 */
window.API = (function () {
  const CFG = window.APP_CONFIG || {};

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

  async function fetchV2(url, options = {}) {
    const headers = {
      'Content-Type': 'application/json',
      ...(options.headers || {}),
    };
    if (CFG.v2AdminKey) {
      headers['x-admin-key'] = CFG.v2AdminKey;
    }
    return fetchJson(url, { ...options, headers });
  }

  async function fetchDataService(url, options = {}) {
    const headers = {
      'Content-Type': 'application/json',
      ...(options.headers || {}),
    };
    if (CFG.dataserviceApiKey) {
      headers['Authorization'] = `Bearer ${CFG.dataserviceApiKey}`;
    }
    return fetchJson(url, { ...options, headers });
  }

  const AuthAPI = {
    session: () => fetchJson(`${CFG.authDomain}/session`),
    logout: () => fetchJson(`${CFG.authDomain}/logout`, { method: 'POST' }),
    adminSlaves: () => fetchJson(`${CFG.authDomain}/admin/slaves`),
    pinLogin: (body) => fetchJson(`${CFG.pinDomain}/pin-login`, { method: 'POST', body: JSON.stringify(body) }),
    setCredentials: (body) => fetchJson(`${CFG.pinDomain}/set-credentials`, { method: 'POST', body: JSON.stringify(body) }),
    pinResetRequest: (body) => fetchJson(`${CFG.pinDomain}/pin-reset/request`, { method: 'POST', body: JSON.stringify(body) }),
    pinResetConfirm: (body) => fetchJson(`${CFG.pinDomain}/pin-reset/confirm`, { method: 'POST', body: JSON.stringify(body) }),
  };

  const V2API = {
    health: () => fetchV2(`${CFG.v2ApiBase}/health`),
    listAccounts: () => fetchV2(`${CFG.v2ApiBase}/api/accounts`),
    accountState: (name) => fetchV2(`${CFG.v2ApiBase}/api/accounts/${encodeURIComponent(name)}/state`),
    updateAccount: (name, patch) => fetchV2(`${CFG.v2ApiBase}/api/accounts/${encodeURIComponent(name)}`, { method: 'PATCH', body: JSON.stringify(patch) }),
    listSignals: (limit = 50) => fetchV2(`${CFG.v2ApiBase}/api/signals?limit=${limit}`),
    signalExecutions: (chatId, messageId) => fetchV2(`${CFG.v2ApiBase}/api/signals/${encodeURIComponent(chatId)}/${messageId}/executions`),
    injectSignal: (payload) => fetchV2(`${CFG.v2ApiBase}/api/signals/inject`, { method: 'POST', body: JSON.stringify(payload) }),
    listExecutions: (limit = 50) => fetchV2(`${CFG.v2ApiBase}/api/executions?limit=${limit}`),
    agentLogs: (limit = 50) => fetchV2(`${CFG.v2ApiBase}/api/agent-logs?limit=${limit}`),
    signalExperience: () => fetchV2(`${CFG.v2ApiBase}/api/signal-experience`).catch(() => ({})),
    executionsStream: () => new EventSource(`${CFG.v2ApiBase}/api/executions/stream`, { withCredentials: true }),
  };

  const AgentAPI = {
    health: () => fetchJson(`${CFG.agentHarnessBase}/health`),
    signalIntent: (payload) => fetchJson(`${CFG.agentHarnessBase}/agent/v1/signal/intent`, { method: 'POST', body: JSON.stringify(payload) }),
    entryDecision: (payload) => fetchJson(`${CFG.agentHarnessBase}/agent/v1/entry/decision`, { method: 'POST', body: JSON.stringify(payload) }),
    lifecyclePlan: (payload) => fetchJson(`${CFG.agentHarnessBase}/agent/v1/lifecycle/plan`, { method: 'POST', body: JSON.stringify(payload) }),
  };

  const DataAPI = {
    health: () => fetchDataService(`${CFG.dataserviceBase}/api/v1/health`),
    goldQuant: () => fetchDataService(`${CFG.dataserviceBase}/api/v1/gold/quant`).catch(() => ({})),
    goldMtf: () => fetchDataService(`${CFG.dataserviceBase}/api/v1/gold/mtf`).catch(() => ({})),
    feedStatus: () => fetchDataService(`${CFG.dataserviceBase}/api/v1/feed/status`).catch(() => ({})),
    symbols: () => fetchDataService(`${CFG.dataserviceBase}/api/v1/symbols`).catch(() => []),
    quality: (symbol) => {
      const path = symbol ? `/api/v1/quality/${encodeURIComponent(symbol)}` : '/api/v1/quality';
      return fetchDataService(`${CFG.dataserviceBase}${path}`).catch(() => ({}));
    },
  };

  function getTablesDB() {
    if (!window.Appwrite) return null;
    const client = new window.Appwrite.Client();
    client.setEndpoint(CFG.endpoint).setProject(CFG.projectId);
    return new window.Appwrite.TablesDB(client);
  }

  return {
    CFG,
    fetchJson,
    AuthAPI,
    V2API,
    AgentAPI,
    DataAPI,
    getTablesDB,
  };
})();
