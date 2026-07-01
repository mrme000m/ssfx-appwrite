/**
 * cTrader Auth Site — Hash-routed SPA
 */

(function () {
  'use strict';

  // ─── Config ─────────────────────────────────────────────────────────
  const CFG = window.APP_CONFIG || {};
  const ENDPOINT = CFG.endpoint || 'https://sgp.cloud.appwrite.io/v1';
  const PROJECT_ID = CFG.projectId || '6a22a362002b9ae880bb';
  const AUTH_FN = CFG.authFunctionUrl || '/.proxy/ctrader-auth';
  const PIN_FN = CFG.pinFunctionUrl || '/.proxy/ctrader-pin-auth';
  const SITE_URL = CFG.siteUrl || (typeof location !== 'undefined' ? location.origin : '');

  // ─── Appwrite Client ────────────────────────────────────────────────
  let client, account, tablesDB, realtime;

  function initSdk() {
    if (!window.Appwrite) {
      console.error('Appwrite SDK not loaded');
      return false;
    }
    client = new Appwrite.Client()
      .setEndpoint(ENDPOINT)
      .setProject(PROJECT_ID);
    account = new Appwrite.Account(client);
    tablesDB = new Appwrite.TablesDB(client);
    realtime = new Appwrite.Realtime(client);
    return true;
  }

  // ─── State ──────────────────────────────────────────────────────────
  const state = {
    user: null,
    grantId: null,
    role: 'slave',
    loading: false,
  };

  // ─── Router ─────────────────────────────────────────────────────────
  function getRoute() {
    const hash = location.hash.replace('#', '') || '/';
    return hash.split('?')[0];
  }

  function getParams() {
    const hash = location.hash.replace('#', '');
    const qs = hash.split('?')[1] || '';
    const params = new URLSearchParams(qs);
    const out = {};
    for (const [k, v] of params) out[k] = v;
    return out;
  }

  function navigate(path) {
    location.hash = path;
  }

  // ─── Session ────────────────────────────────────────────────────────
  async function loadSession() {
    try {
      const resp = await fetch(`${AUTH_FN}/session`, {
        credentials: 'include',
      });
      const data = await resp.json();
      if (data.authenticated) {
        state.user = { $id: data.user_id, name: data.name };
        state.grantId = data.grant_id;
        state.role = data.role;
        return true;
      }
    } catch (e) {
      console.warn('Session check failed', e);
    }
    state.user = null;
    state.grantId = null;
    state.role = 'slave';
    return false;
  }

  async function logout() {
    try {
      await fetch(`${AUTH_FN}/logout`, {
        method: 'POST',
        credentials: 'include',
      });
    } catch {}
    state.user = null;
    state.grantId = null;
    navigate('/');
    render();
  }

  // ─── Render ─────────────────────────────────────────────────────────
  function render() {
    const app = document.getElementById('app');
    if (!app) return;
    const route = getRoute();

    if (route === '/onboarding') {
      app.innerHTML = renderOnboarding();
      bindOnboarding();
      return;
    }
    if (route === '/login') {
      app.innerHTML = renderLogin();
      bindLogin();
      return;
    }
    if (route === '/dashboard') {
      if (!state.user) { navigate('/login'); return; }
      if (state.role === 'master') { navigate('/master'); return; }
      app.innerHTML = renderDashboard();
      bindDashboard();
      return;
    }
    if (route === '/master') {
      if (!state.user) { navigate('/login'); return; }
      if (state.role !== 'master') { navigate('/dashboard'); return; }
      app.innerHTML = renderMaster();
      bindMaster();
      return;
    }
    // Landing
    app.innerHTML = renderLanding();
    bindLanding();
  }

  // ─── Views ──────────────────────────────────────────────────────────

  function renderLanding() {
    const loggedIn = !!state.user;
    return `
      <div class="card">
        <h1>cTrader Copy Trading</h1>
        <p>Connect your cTrader account to enable copy trading.</p>
        ${loggedIn
          ? `<p>Welcome back! <button id="btn-dashboard" class="btn btn-primary">Go to Dashboard</button></p>`
          : `<button id="btn-connect" class="btn btn-primary">Connect cTrader Account</button>
             <p class="sub"><a href="#/login">Already connected? Log in</a></p>`}
      </div>
    `;
  }

  function bindLanding() {
    const btn = document.getElementById('btn-connect');
    if (btn) {
      btn.addEventListener('click', () => {
        // Start OAuth flow
        const url = `${AUTH_FN}/auth/ctrader/start`;
        location.href = url;
      });
    }
    const dash = document.getElementById('btn-dashboard');
    if (dash) {
      dash.addEventListener('click', () => navigate('/dashboard'));
    }
  }

  function renderOnboarding() {
    const params = getParams();
    const success = params.success === 'true';
    const grantId = params.grant_id || '';

    if (!success) {
      return `
        <div class="card">
          <h1>Connection Failed</h1>
          <p>We could not connect your cTrader account.</p>
          <p><code>${params.error || 'unknown'}</code></p>
          <button id="btn-retry" class="btn btn-primary">Try Again</button>
        </div>
      `;
    }

    return `
      <div class="card">
        <h1>Account Connected</h1>
        <p>Your cTrader account has been linked successfully.</p>
        <p class="code">Grant ID: ${escapeHtml(grantId)}</p>
        <p>Now set your username and PIN to log in later.</p>
        <form id="form-credentials">
          <label>Username</label>
          <input type="text" id="username" required minlength="3" maxlength="32" placeholder="Choose a username">
          <label>PIN (4-6 digits)</label>
          <input type="password" id="pin" required pattern="\\d{4,6}" placeholder="4-6 digit PIN">
          <button type="submit" class="btn btn-primary">Save Credentials</button>
        </form>
        <p id="msg-credentials" class="msg"></p>
      </div>
    `;
  }

  function bindOnboarding() {
    const retry = document.getElementById('btn-retry');
    if (retry) retry.addEventListener('click', () => navigate('/'));

    const form = document.getElementById('form-credentials');
    if (!form) return;
    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      const username = document.getElementById('username').value.trim();
      const pin = document.getElementById('pin').value;
      const msg = document.getElementById('msg-credentials');

      try {
        const resp = await fetch(`${PIN_FN}/set-credentials`, {
          method: 'POST',
          credentials: 'include',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ username, pin }),
        });
        const data = await resp.json();
        if (data.success) {
          msg.textContent = 'Saved! Redirecting to dashboard...';
          msg.className = 'msg success';
          setTimeout(() => navigate('/dashboard'), 1000);
        } else {
          msg.textContent = data.error || 'Failed to save';
          msg.className = 'msg error';
        }
      } catch (err) {
        msg.textContent = 'Network error';
        msg.className = 'msg error';
      }
    });
  }

  function renderLogin() {
    return `
      <div class="card">
        <h1>Log In</h1>
        <form id="form-login">
          <label>Username</label>
          <input type="text" id="login-username" required placeholder="Username">
          <label>PIN</label>
          <input type="password" id="login-pin" required pattern="\\d{4,6}" placeholder="PIN">
          <button type="submit" class="btn btn-primary">Log In</button>
        </form>
        <p id="msg-login" class="msg"></p>
        <p class="sub"><a href="#/">New here? Connect your account</a></p>
      </div>
    `;
  }

  function bindLogin() {
    const form = document.getElementById('form-login');
    if (!form) return;
    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      const username = document.getElementById('login-username').value.trim();
      const pin = document.getElementById('login-pin').value;
      const msg = document.getElementById('msg-login');

      try {
        const resp = await fetch(`${PIN_FN}/pin-login`, {
          method: 'POST',
          credentials: 'include',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ username, pin }),
        });
        const data = await resp.json();
        if (data.success) {
          state.user = { $id: data.user_id };
          state.role = data.role;
          state.grantId = data.grant_id;
          navigate(data.role === 'master' ? '/master' : '/dashboard');
        } else {
          msg.textContent = data.error || 'Login failed';
          msg.className = 'msg error';
        }
      } catch (err) {
        msg.textContent = 'Network error';
        msg.className = 'msg error';
      }
    });
  }

  function renderDashboard() {
    return `
      <div class="card">
        <div class="header-row">
          <h1>Slave Dashboard</h1>
          <button id="btn-logout" class="btn btn-secondary">Log Out</button>
        </div>
        <p>Grant ID: <code>${escapeHtml(state.grantId || 'N/A')}</code></p>
        <p>Role: ${escapeHtml(state.role)}</p>
        <hr>
        <h2>Trade Config</h2>
        <form id="form-config">
          <label>Lot Size</label>
          <input type="number" id="cfg-lot" step="0.01" min="0.01" value="0.01">
          <label>Lot Multiplier</label>
          <input type="number" id="cfg-mult" step="0.1" min="0.1" value="1.0">
          <label>Max Daily Drawdown %</label>
          <input type="number" id="cfg-dd" step="0.1" min="0" value="5.0">
          <label>Allowed Symbols (comma-separated, empty = all)</label>
          <input type="text" id="cfg-syms" placeholder="EURUSD,GBPUSD">
          <label>
            <input type="checkbox" id="cfg-copy" checked>
            Copy Enabled
          </label>
          <button type="submit" class="btn btn-primary">Save Config</button>
        </form>
        <p id="msg-config" class="msg"></p>
      </div>
    `;
  }

  async function bindDashboard() {
    document.getElementById('btn-logout').addEventListener('click', logout);

    // Fetch existing config
    try {
      const list = await tablesDB.listRows({
        databaseId: 'ctrader_auth',
        tableId: 'trade_configs',
        queries: [Appwrite.Query.equal('slave_user_id', state.user.$id)],
      });
      if (list.documents.length > 0) {
        const cfg = list.documents[0];
        document.getElementById('cfg-lot').value = cfg.lot_size;
        document.getElementById('cfg-mult').value = cfg.lot_multiplier;
        document.getElementById('cfg-dd').value = cfg.max_daily_drawdown_pct;
        document.getElementById('cfg-syms').value = cfg.allowed_symbols || '';
        document.getElementById('cfg-copy').checked = cfg.copy_enabled;
      }
    } catch (e) {
      console.warn('Config load failed', e);
    }

    // Create or update config
    document.getElementById('form-config').addEventListener('submit', async (e) => {
      e.preventDefault();
      const msg = document.getElementById('msg-config');
      const payload = {
        lot_size: parseFloat(document.getElementById('cfg-lot').value),
        lot_multiplier: parseFloat(document.getElementById('cfg-mult').value),
        max_daily_drawdown_pct: parseFloat(document.getElementById('cfg-dd').value),
        allowed_symbols: document.getElementById('cfg-syms').value.trim(),
        copy_enabled: document.getElementById('cfg-copy').checked,
      };

      try {
        const list = await tablesDB.listRows({
          databaseId: 'ctrader_auth',
          tableId: 'trade_configs',
          queries: [Appwrite.Query.equal('slave_user_id', state.user.$id)],
        });
        if (list.documents.length > 0) {
          await tablesDB.updateRow({
            databaseId: 'ctrader_auth',
            tableId: 'trade_configs',
            rowId: list.documents[0].$id,
            data: payload,
          });
        } else {
          await tablesDB.createRow({
            databaseId: 'ctrader_auth',
            tableId: 'trade_configs',
            rowId: Appwrite.ID.unique(),
            data: { slave_user_id: state.user.$id, ...payload },
          });
        }
        msg.textContent = 'Config saved.';
        msg.className = 'msg success';
      } catch (err) {
        msg.textContent = 'Failed to save: ' + err.message;
        msg.className = 'msg error';
      }
    });
  }

  function renderMaster() {
    return `
      <div class="card">
        <div class="header-row">
          <h1>Master Dashboard</h1>
          <button id="btn-logout" class="btn btn-secondary">Log Out</button>
        </div>
        <p>Connected Slaves:</p>
        <div id="slave-list">Loading...</div>
        <hr>
        <h2>Master Signals</h2>
        <div id="signal-feed">Waiting for signals...</div>
      </div>
    `;
  }

  async function bindMaster() {
    document.getElementById('btn-logout').addEventListener('click', logout);

    // Load slaves
    try {
      const list = await tablesDB.listRows({
        databaseId: 'ctrader_auth',
        tableId: 'slave_accounts',
        queries: [
          Appwrite.Query.equal('role', 'slave'),
          Appwrite.Query.limit(100),
        ],
      });
      const container = document.getElementById('slave-list');
      if (list.documents.length === 0) {
        container.innerHTML = '<p>No slaves connected.</p>';
      } else {
        container.innerHTML = '<table><thead><tr><th>Username</th><th>Grant ID</th><th>Accounts</th><th>Status</th><th>Active</th><th>Heartbeat</th></tr></thead><tbody>' +
          list.documents.map(s => `
            <tr>
              <td>${escapeHtml(s.username || 'N/A')}</td>
              <td><code>${escapeHtml(s.grant_id)}</code></td>
              <td>${escapeHtml(s.ctrader_account_ids || '—')}</td>
              <td>${escapeHtml(s.status)}</td>
              <td>${s.active ? 'Yes' : 'No'}</td>
              <td>${s.last_heartbeat_at ? timeAgo(s.last_heartbeat_at) : '—'}</td>
            </tr>
          `).join('') +
          '</tbody></table>';
      }
    } catch (e) {
      document.getElementById('slave-list').innerHTML = '<p class="error">Failed to load slaves.</p>';
    }

    // Realtime subscription to master_signals
    try {
      const sub = realtime.subscribe(
        Appwrite.Channel.tablesdb('ctrader_auth').table('master_signals').row(),
        (response) => {
          const feed = document.getElementById('signal-feed');
          if (!feed) return;
          const sig = response.payload;
          const entry = document.createElement('div');
          entry.className = 'signal';
          entry.innerHTML = `<strong>${escapeHtml(sig.symbol)}</strong> ${escapeHtml(sig.direction)} @ ${sig.lot_size} lots — <em>${escapeHtml(sig.status)}</em>`;
          feed.prepend(entry);
        }
      );
      // Store for cleanup if needed
      window._signalSub = sub;
    } catch (e) {
      console.warn('Realtime subscribe failed', e);
    }
  }

  // ─── Helpers ────────────────────────────────────────────────────────

  function escapeHtml(str) {
    if (str == null) return '';
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function timeAgo(iso) {
    const diff = Date.now() - new Date(iso).getTime();
    const sec = Math.floor(diff / 1000);
    if (sec < 60) return 'just now';
    const min = Math.floor(sec / 60);
    if (min < 60) return `${min}m ago`;
    const hr = Math.floor(min / 60);
    return `${hr}h ago`;
  }

  // ─── Init ───────────────────────────────────────────────────────────

  async function init() {
    if (!initSdk()) {
      document.getElementById('app').innerHTML = '<div class="card error">Failed to load Appwrite SDK. Check console.</div>';
      return;
    }
    await loadSession();
    render();
    window.addEventListener('hashchange', render);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
