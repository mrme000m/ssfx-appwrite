/**
 * SSFX cTrader Auth — SPA
 * Backend-integrated auth, session management, toast notifications,
 * PIN dots, reauth flow, loading skeletons, view transitions.
 */
(function () {
  'use strict';

  // ─── Config ──────────────────────────────────────────────────────
  const CFG = window.APP_CONFIG || {};
  const ENDPOINT = CFG.endpoint || 'https://sgp.cloud.appwrite.io/v1';
  const PROJECT_ID = CFG.projectId || '6a22a362002b9ae880bb';
  const AUTH_FN = CFG.authFunctionUrl || 'https://auth.mrme.tech';
  const PIN_FN = CFG.pinFunctionUrl || 'https://pin.mrme.tech';
  const DB_ID = 'ctrader_auth';
  const SESSION_KEY = `a_session_${PROJECT_ID}`;

  // ─── State ───────────────────────────────────────────────────────
  const state = {
    user: null,
    grantId: null,
    role: 'slave',
    username: '',
    status: '',
    active: false,
    ctraderAccountIds: '',
    selectedAccountId: '',
    lastHeartbeat: null,
    accounts: [],
    loading: true,
    error: null,
    configLoading: false,
  };

  let currentView = null;
  let sessionRefreshTimer = null;
  let slaveRefreshTimer = null;

  // ─── Appwrite SDK ────────────────────────────────────────────────
  let client, account, tablesDB;

  function initSdk() {
    if (!window.Appwrite) {
      state.error = 'SDK failed to load';
      return false;
    }
    client = new Appwrite.Client().setEndpoint(ENDPOINT).setProject(PROJECT_ID);
    account = new Appwrite.Account(client);
    tablesDB = new Appwrite.TablesDB(client);
    return true;
  }

  // ─── API Client ──────────────────────────────────────────────────
  async function api(base, path, options = {}) {
    const opts = {
      credentials: 'include',
      ...options,
      headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
    };
    const resp = await fetch(`${base}${path}`, opts);
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) {
      throw new Error(data.error || data.message || `HTTP ${resp.status}`);
    }
    return data;
  }

  const authApi = (path, opts) => api(AUTH_FN, path, opts);
  const pinApi = (path, opts) => api(PIN_FN, path, opts);

  // ─── Toast Notifications ──────────────────────────────────────────
  function showToast(type, message, duration = 3000) {
    const container = document.getElementById('toast-container');
    if (!container) return;
    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;
    const icons = { success: '\u2713', error: '\u2717', warning: '\u26A0' };
    toast.innerHTML = `<span style="font-weight:700">${icons[type] || ''}</span> ${esc(message)}`;
    container.appendChild(toast);
    setTimeout(() => {
      toast.classList.add('removing');
      setTimeout(() => toast.remove(), 260);
    }, duration);
  }

  // ─── Session ─────────────────────────────────────────────────────
  async function loadSession() {
    try {
      const data = await authApi('/session');
      if (data.authenticated) {
        state.user = { $id: data.user_id, name: data.name };
        state.grantId = data.grant_id;
        state.role = data.role;
        state.username = data.username || data.name || '';
        state.status = data.status || '';
        state.active = data.active || false;
        state.ctraderAccountIds = data.ctrader_account_ids || '';
        state.selectedAccountId = data.selected_account_id || '';
        state.lastHeartbeat = data.last_heartbeat_at || null;
        state.accounts = Array.isArray(data.accounts) ? data.accounts : [];
        return true;
      }
    } catch (e) {
      console.warn('Session check failed:', e.message);
    }
    return false;
  }

  /** Silent background refresh — only update data fields, no re-render. */
  async function refreshSessionSilent() {
    try {
      const data = await authApi('/session');
      if (data.authenticated) {
        state.status = data.status || '';
        state.active = data.active || false;
        state.lastHeartbeat = data.last_heartbeat_at || null;
        state.ctraderAccountIds = data.ctrader_account_ids || '';
        state.selectedAccountId = data.selected_account_id || '';
        state.accounts = Array.isArray(data.accounts) ? data.accounts : [];

        // If token needs reauth, show a toast
        if (data.status === 'reauth_required' && state.status !== 'reauth_required') {
          showToast('warning', 'cTrader token expired — please reconnect.');
        }
      } else {
        // Session lost
        stopSessionRefresh();
        resetUser();
        navigate('/');
        render();
      }
    } catch (e) {
      // Silently ignore network errors during background refresh
    }
  }

  function startSessionRefresh() {
    stopSessionRefresh();
    sessionRefreshTimer = setInterval(refreshSessionSilent, 30000);
  }

  function stopSessionRefresh() {
    if (sessionRefreshTimer) {
      clearInterval(sessionRefreshTimer);
      sessionRefreshTimer = null;
    }
  }

  async function logout() {
    try { await authApi('/logout', { method: 'POST' }); } catch {}
    stopSessionRefresh();
    resetUser();
    navigate('/');
    render();
  }

  function resetUser() {
    state.user = null;
    state.grantId = null;
    state.role = 'slave';
    state.username = '';
    state.status = '';
    state.active = false;
    state.ctraderAccountIds = '';
    state.selectedAccountId = '';
    state.lastHeartbeat = null;
    state.accounts = [];
  }

  // ─── Router ──────────────────────────────────────────────────────
  function getRoute() {
    return (location.hash.replace('#', '') || '/').split('?')[0];
  }
  function getParams() {
    const qs = (location.hash.replace('#', '').split('?')[1]) || '';
    const out = {};
    new URLSearchParams(qs).forEach((v, k) => out[k] = v);
    return out;
  }
  function navigate(path) { location.hash = path; }

  // ─── Render ──────────────────────────────────────────────────────
  function render() {
    const app = document.getElementById('app');
    if (!app) return;
    const route = getRoute();

    let view;
    switch (route) {
      case '/onboarding':
        view = ViewOnboarding; break;
      case '/login':
        if (state.user) { navigate(state.role === 'master' ? '/master' : '/dashboard'); return; }
        view = ViewLogin; break;
      case '/reset':
        if (state.user) { navigate(state.role === 'master' ? '/master' : '/dashboard'); return; }
        view = ViewReset; break;
      case '/dashboard':
        if (!state.user) { navigate('/login'); return; }
        if (state.role === 'master') { navigate('/master'); return; }
        view = ViewDashboard; break;
      case '/master':
        if (!state.user) { navigate('/login'); return; }
        if (state.role !== 'master') { navigate('/dashboard'); return; }
        view = ViewMaster; break;
      default: view = ViewLanding;
    }

    if (currentView && currentView.cleanup) currentView.cleanup();
    app.innerHTML = view.render();
    view.bind(app);
    currentView = view;

    // Start or stop background session refresh
    if (state.user) startSessionRefresh();
    else stopSessionRefresh();
  }

  // ─── Reauth Banner ───────────────────────────────────────────────
  function reauthBannerHtml() {
    if (state.status !== 'reauth_required') return '';
    return `
      <div class="reauth-banner">
        <span class="reauth-icon">\u26A0</span>
        <span class="reauth-text"><strong>Token expired.</strong> Reconnect your cTrader account to continue trading.</span>
        <button class="btn btn-primary btn-sm btn-reauth" data-action="reauth">Reconnect</button>
      </div>`;
  }

  // ─── Skeleton Helpers ─────────────────────────────────────────────
  function skeletonRows(count) {
    return Array.from({ length: count }, () =>
      `<tr>${Array.from({ length: 6 }, () => `<td><div class="skeleton skeleton-text" style="width:60px;height:12px"></div></td>`).join('')}</tr>`
    ).join('');
  }

  function skeletonInfoGrid(count) {
    return Array.from({ length: count }, () =>
      `<dt><div class="skeleton skeleton-text short"></div></dt><dd><div class="skeleton skeleton-text long"></div></dd>`
    ).join('');
  }

  // ─── Account Cards Helper ────────────────────────────────────────
  function accountsHtml(accounts) {
    if (!accounts || accounts.length === 0) {
      return `
        <div class="empty-state" style="padding:20px 0">
          <div class="empty-icon">\u{1F4B9}</div>
          <div class="empty-title">No accounts discovered yet</div>
          <p class="empty-desc">Start trading to see your accounts here.</p>
        </div>`;
    }
    return `<div class="account-cards">${accounts.map(acc => {
      const id = String(acc.ctid_trader_account_id || acc.ctidTraderAccountId || '');
      const login = String(acc.trader_login || acc.traderLogin || '');
      const broker = String(acc.broker_title_short || acc.brokerTitleShort || 'Unknown Broker');
      const isLive = acc.is_live === true || acc.isLive === true;
      const selected = acc.selected === true;
      return `
        <div class="account-card ${selected ? 'account-card-selected' : ''}">
          <div class="account-card-header">
            <span class="account-card-broker">${esc(broker)}</span>
            <span class="badge badge-${isLive ? 'active' : 'inactive'}">${isLive ? 'Live' : 'Demo'}</span>
          </div>
          <div class="account-card-login">${login ? esc(login) : '\u2014'}</div>
          <div class="account-card-meta">
            <span class="code" title="${esc(id)}">${esc(id).slice(0, 10)}${id.length > 10 ? '...' : ''}</span>
            ${selected ? '<span class="account-card-selected-label">Selected</span>' : ''}
          </div>
        </div>`;
    }).join('')}</div>`;
  }

  // ─── PIN Dots Helper ──────────────────────────────────────────────
  function pinDotsHtml(id) {
    return `<div class="pin-dots" id="${id}">${Array.from({ length: 6 }, (_, i) =>
      `<span class="pin-dot" data-idx="${i}"></span>`).join('')}</div>`;
  }

  function bindPinDots(app, inputId, dotsId) {
    const input = app.querySelector(`#${inputId}`);
    const dotsContainer = app.querySelector(`#${dotsId}`);
    if (!input || !dotsContainer) return;
    const update = () => {
      const len = input.value.length;
      dotsContainer.querySelectorAll('.pin-dot').forEach((dot, i) => {
        dot.classList.toggle('filled', i < len);
      });
    };
    input.addEventListener('input', update);
    update();
  }

  // ═══════════════════════════════════════════════════════════════
  // VIEWS
  // ═══════════════════════════════════════════════════════════════

  const ViewLanding = {
    render() {
      const errBanner = state.error
        ? `<div class="reauth-banner" style="border-color:rgba(251,113,133,0.25);background:var(--error-muted)"><span class="reauth-icon">\u2717</span><span class="reauth-text" style="color:var(--error)">${esc(state.error)}</span></div>`
        : '';

      if (state.user) {
        const dash = state.role === 'master' ? '/master' : '/dashboard';
        const connectLabel = state.grantId ? 'Reconnect cTrader Account' : 'Connect cTrader Account';
        return `
          <div class="card center">
            ${errBanner}
            <h1>Welcome back</h1>
            <p class="muted">${esc(state.username)}</p>
            <div style="margin:16px 0">
              <span class="badge badge-${state.active ? 'active' : 'inactive'}">
                <span class="status-dot ${state.active ? 'active' : 'inactive'}"></span>
                ${state.active ? 'Active' : 'Inactive'}
              </span>
              <span class="badge badge-role" style="margin-left:6px">${esc(state.role)}</span>
            </div>
            <button id="btn-dash" class="btn btn-primary btn-block">Go to Dashboard</button>
            <button id="btn-connect" class="btn btn-secondary btn-block">${connectLabel}</button>
            <button id="btn-logout" class="btn btn-secondary btn-block">Log Out</button>
          </div>`;
      }
      return `
        <div class="card center">
          ${errBanner}
          <h1>cTrader Copy Trading</h1>
          <p class="muted">Connect your cTrader account to enable copy trading.</p>
          <button id="btn-connect" class="btn btn-primary btn-block">Connect cTrader Account</button>
          <nav class="nav-links">
            <a href="#/login">Log in</a>
            <a href="#/reset">Forgot PIN?</a>
          </nav>
        </div>`;
    },
    bind(app) {
      const connect = app.querySelector('#btn-connect');
      if (connect) connect.onclick = () => {
        const uid = state.user ? state.user.$id : '';
        location.href = `${AUTH_FN}/auth/ctrader/start${uid ? '?user_id=' + encodeURIComponent(uid) : ''}`;
      };
      const dash = app.querySelector('#btn-dash');
      if (dash) dash.onclick = () => navigate(state.role === 'master' ? '/master' : '/dashboard');
      const lo = app.querySelector('#btn-logout');
      if (lo) lo.onclick = logout;
    }
  };

  const ViewOnboarding = {
    render() {
      const p = getParams();
      if (p.success !== 'true') {
        return `
          <div class="card center">
            <div class="empty-state">
              <div class="empty-icon">\u2717</div>
              <div class="empty-title">Connection Failed</div>
              <p class="empty-desc">${esc(p.error || 'We could not connect your cTrader account. Please try again.')}</p>
              <button id="btn-retry" class="btn btn-primary" style="margin-top:12px">Try Again</button>
            </div>
          </div>`;
      }
      return `
        <div class="card">
          <div class="empty-state" style="padding:16px 0">
            <div class="empty-icon">\u2713</div>
            <div class="empty-title">Account Connected</div>
            <p class="empty-desc">Your cTrader account has been linked successfully.</p>
          </div>
          <div style="text-align:center;margin:12px 0 8px">
            <span class="code">${esc(p.grant_id || '')}</span>
          </div>
          <hr>
          <h2>Set Your Credentials</h2>
          <p class="muted">Choose a username and PIN to log in later.</p>
          ${state.user ? `<form id="form-creds">
            <label>Username</label>
            <input type="text" id="username" required minlength="3" maxlength="32" placeholder="Choose a username" autocomplete="username">
            <label>PIN (4-6 digits)</label>
            <div class="pin-input-wrap">
              <input type="password" id="pin" required pattern="\\d{4,6}" placeholder="4-6 digit PIN" autocomplete="new-password" maxlength="6" inputmode="numeric">
              <button type="button" class="pin-toggle" id="pin-toggle" tabindex="-1" aria-label="Toggle PIN visibility">show</button>
            </div>
            ${pinDotsHtml('onboard-pin-dots')}
            <button type="submit" class="btn btn-primary btn-block" id="btn-save">Save Credentials</button>
          </form>` : `<div class="reauth-banner" style="border-color:rgba(251,113,133,0.25);background:var(--error-muted)"><span class="reauth-icon">\u26A0</span><span class="reauth-text" style="color:var(--error)">Session expired. Please try connecting again.</span></div>`}
          <p id="msg" class="msg"></p>
        </div>`;
    },
    bind(app) {
      const retry = app.querySelector('#btn-retry');
      if (retry) retry.onclick = () => navigate('/');

      // PIN toggle
      const pinInput = app.querySelector('#pin');
      const pinToggle = app.querySelector('#pin-toggle');
      if (pinToggle && pinInput) {
        pinToggle.onclick = () => {
          const isPassword = pinInput.type === 'password';
          pinInput.type = isPassword ? 'text' : 'password';
          pinToggle.textContent = isPassword ? 'hide' : 'show';
        };
      }

      bindPinDots(app, 'pin', 'onboard-pin-dots');

      const form = app.querySelector('#form-creds');
      if (!form) return;
      form.onsubmit = async (e) => {
        e.preventDefault();
        const btn = app.querySelector('#btn-save');
        const msg = app.querySelector('#msg');
        const username = app.querySelector('#username').value.trim();
        const pin = app.querySelector('#pin').value;
        btn.disabled = true; btn.textContent = 'Saving...'; msg.className = 'msg';
        try {
          await pinApi('/set-credentials', {
            method: 'POST', body: JSON.stringify({ username, pin }),
          });
          showToast('success', 'Credentials saved');
          state.username = username;
          setTimeout(() => navigate('/dashboard'), 600);
        } catch (err) {
          msg.textContent = err.message; msg.className = 'msg error';
          btn.disabled = false; btn.textContent = 'Save Credentials';
        }
      };
    }
  };

  const ViewLogin = {
    render() {
      return `
        <div class="card">
          <h1>Log In</h1>
          <p class="muted">Enter your username and PIN to continue.</p>
          <form id="form-login">
            <label>Username</label>
            <input type="text" id="login-user" required placeholder="Username" autocomplete="username">
            <label>PIN</label>
            <div class="pin-input-wrap">
              <input type="password" id="login-pin" required pattern="\\d{4,6}" placeholder="PIN" autocomplete="current-password" maxlength="6" inputmode="numeric">
              <button type="button" class="pin-toggle" id="pin-toggle" tabindex="-1" aria-label="Toggle PIN visibility">show</button>
            </div>
            ${pinDotsHtml('login-pin-dots')}
            <button type="submit" class="btn btn-primary btn-block" id="btn-login">Log In</button>
          </form>
          <p id="msg" class="msg"></p>
          <nav class="nav-links">
            <a href="#/">New here?</a>
            <a href="#/reset">Forgot PIN?</a>
          </nav>
        </div>`;
    },
    bind(app) {
      const pinInput = app.querySelector('#login-pin');
      const pinToggle = app.querySelector('#pin-toggle');
      if (pinToggle && pinInput) {
        pinToggle.onclick = () => {
          const isPassword = pinInput.type === 'password';
          pinInput.type = isPassword ? 'text' : 'password';
          pinToggle.textContent = isPassword ? 'hide' : 'show';
        };
      }
      bindPinDots(app, 'login-pin', 'login-pin-dots');

      const form = app.querySelector('#form-login');
      if (!form) return;
      form.onsubmit = async (e) => {
        e.preventDefault();
        const btn = app.querySelector('#btn-login');
        const msg = app.querySelector('#msg');
        const username = app.querySelector('#login-user').value.trim();
        const pin = app.querySelector('#login-pin').value;
        btn.disabled = true; btn.textContent = 'Logging in...'; msg.className = 'msg';
        try {
          const data = await pinApi('/pin-login', {
            method: 'POST', body: JSON.stringify({ username, pin }),
          });
          if (data.success) {
            showToast('success', 'Logged in');
            state.user = { $id: data.user_id };
            state.role = data.role;
            state.grantId = data.grant_id;
            state.username = data.username || username;
            await loadSession();
            navigate(data.role === 'master' ? '/master' : '/dashboard');
          } else {
            msg.textContent = data.error || 'Login failed'; msg.className = 'msg error';
          }
        } catch (err) {
          msg.textContent = err.message; msg.className = 'msg error';
        } finally {
          btn.disabled = false; btn.textContent = 'Log In';
        }
      };
    }
  };

  const ViewReset = {
    render() {
      return `
        <div class="card">
          <h1>Reset PIN</h1>
          <div id="step-request">
            <p class="muted">Enter the email associated with your account to receive a reset token.</p>
            <form id="form-request">
              <label>Email</label>
              <input type="email" id="reset-email" required placeholder="your@email.com">
              <button type="submit" class="btn btn-primary btn-block" id="btn-request">Send Reset Token</button>
            </form>
            <p id="msg-req" class="msg"></p>
          </div>
          <div id="step-confirm" style="display:none">
            <p class="muted">Enter the reset token from your email and choose a new PIN.</p>
            <form id="form-confirm">
              <label>Reset Token</label>
              <input type="text" id="reset-token" required placeholder="Paste token here" class="mono-input">
              <label>New PIN (4-6 digits)</label>
              <div class="pin-input-wrap">
                <input type="password" id="reset-pin" required pattern="\\d{4,6}" placeholder="New PIN" maxlength="6" inputmode="numeric">
                <button type="button" class="pin-toggle" id="pin-toggle" tabindex="-1" aria-label="Toggle PIN visibility">show</button>
              </div>
              ${pinDotsHtml('reset-pin-dots')}
              <button type="submit" class="btn btn-primary btn-block" id="btn-confirm">Reset PIN</button>
            </form>
            <p id="msg-conf" class="msg"></p>
          </div>
          <nav class="nav-links">
            <a href="#/login">Back to login</a>
          </nav>
        </div>`;
    },
    bind(app) {
      const pinInput = app.querySelector('#reset-pin');
      const pinToggle = app.querySelector('#pin-toggle');
      if (pinToggle && pinInput) {
        pinToggle.onclick = () => {
          const isPassword = pinInput.type === 'password';
          pinInput.type = isPassword ? 'text' : 'password';
          pinToggle.textContent = isPassword ? 'hide' : 'show';
        };
      }
      bindPinDots(app, 'reset-pin', 'reset-pin-dots');

      const reqForm = app.querySelector('#form-request');
      if (!reqForm) return;
      reqForm.onsubmit = async (e) => {
        e.preventDefault();
        const btn = app.querySelector('#btn-request');
        const msg = app.querySelector('#msg-req');
        const email = app.querySelector('#reset-email').value.trim();
        btn.disabled = true; btn.textContent = 'Sending...'; msg.className = 'msg';
        try {
          const data = await pinApi('/pin-reset/request', {
            method: 'POST', body: JSON.stringify({ email }),
          });
          msg.textContent = data.message || 'Reset token generated.'; msg.className = 'msg success';
          showToast('success', 'Reset token sent to your email');
          app.querySelector('#step-request').style.display = 'none';
          app.querySelector('#step-confirm').style.display = 'block';
        } catch (err) {
          msg.textContent = err.message; msg.className = 'msg error';
        } finally { btn.disabled = false; btn.textContent = 'Send Reset Token'; }
      };
      const confForm = app.querySelector('#form-confirm');
      if (confForm) {
        confForm.onsubmit = async (e) => {
          e.preventDefault();
          const btn = app.querySelector('#btn-confirm');
          const msg = app.querySelector('#msg-conf');
          const token = app.querySelector('#reset-token').value;
          const newPin = app.querySelector('#reset-pin').value;
          btn.disabled = true; btn.textContent = 'Resetting...'; msg.className = 'msg';
          try {
            await pinApi('/pin-reset/confirm', {
              method: 'POST', body: JSON.stringify({ token, new_pin: newPin }),
            });
            showToast('success', 'PIN reset successfully');
            msg.textContent = 'PIN reset! Redirecting to login...'; msg.className = 'msg success';
            setTimeout(() => navigate('/login'), 1200);
          } catch (err) {
            msg.textContent = err.message; msg.className = 'msg error';
          } finally { btn.disabled = false; btn.textContent = 'Reset PIN'; }
        };
      }
    }
  };

  const ViewDashboard = {
    _abortController: null,
    _cfgLoaded: false,
    render() {
      const hasGrant = !!state.grantId;
      const statusClass = statusBadgeClass(state.status);

      // Info grid — use skeleton while loading for first time
      const infoGrid = state.loading
        ? `<dl class="info-grid">${skeletonInfoGrid(4)}</dl>`
        : `<dl class="info-grid">
            <dt>Username</dt><dd>${esc(state.username) || '\u2014'}</dd>
            <dt>Grant ID</dt><dd><span class="code">${esc(state.grantId) || '\u2014'}</span></dd>
            <dt>Status</dt><dd><span class="badge badge-${statusClass}"><span class="status-dot ${statusClass}"></span>${esc(state.status || 'unknown')}</span></dd>
            <dt>Last Heartbeat</dt><dd>${state.lastHeartbeat ? timeAgo(state.lastHeartbeat) : '\u2014'}</dd>
          </dl>`;

      const accountsSection = state.loading
        ? `<div class="account-cards-skeleton">${Array.from({length: 2}, () => `<div class="account-card"><div class="skeleton skeleton-text medium"></div><div class="skeleton skeleton-text short"></div></div>`).join('')}</div>`
        : accountsHtml(state.accounts);

      // No grant — show empty state
      if (!hasGrant) {
        return `
          <div class="card">
            <div class="header-row">
              <h1>Dashboard</h1>
              <button id="btn-logout" class="btn btn-secondary btn-sm">Log Out</button>
            </div>
            ${reauthBannerHtml()}
            <div class="empty-state">
              <div class="empty-icon">\u{1F517}</div>
              <div class="empty-title">No cTrader Account</div>
              <p class="empty-desc">Connect your cTrader account to start copy trading.</p>
              <button id="btn-connect-ctrader" class="btn btn-primary" style="margin-top:8px">Connect cTrader Account</button>
            </div>
          </div>`;
      }

      return `
        <div class="card">
          <div class="header-row">
            <h1>Dashboard</h1>
            <div style="display:flex;gap:8px;align-items:center">
              <span class="badge badge-${state.active ? 'active' : 'inactive'}">
                <span class="status-dot ${state.active ? 'active' : 'inactive'}"></span>
                ${state.active ? 'Active' : 'Inactive'}
              </span>
              <button id="btn-logout" class="btn btn-secondary btn-sm">Log Out</button>
            </div>
          </div>
          ${reauthBannerHtml()}
          ${infoGrid}
          <h2>Linked Accounts <span class="subtitle">${state.accounts.length} discovered</span></h2>
          ${accountsSection}
          <hr>
          <h2>Trade Configuration <span class="subtitle">Control how signals are copied to your account</span></h2>
          <div id="cfg-loading"><div class="spinner"></div><p class="muted center-text">Loading configuration...</p></div>
          <form id="form-config" style="display:none">
            <div class="field-group">
              <div class="field-group-title">Copy Control</div>
              <label><input type="checkbox" id="cfg-copy" checked> Enable Copy Trading</label>
              <label>Lot Size</label>
              <input type="number" id="cfg-lot" step="0.01" min="0.01" value="0.01" class="mono-input">
              <label>Lot Multiplier</label>
              <input type="number" id="cfg-mult" step="0.1" min="0.1" value="1.0" class="mono-input">
            </div>
            <div class="field-group">
              <div class="field-group-title">Risk Management</div>
              <label>Max Daily Drawdown (%)</label>
              <input type="number" id="cfg-dd" step="0.1" min="0" value="5.0" class="mono-input">
            </div>
            <div class="field-group">
              <div class="field-group-title">Symbol Filter</div>
              <label>Allowed Symbols (comma-separated, empty = all)</label>
              <input type="text" id="cfg-syms" placeholder="EURUSD, GBPUSD, XAUUSD">
            </div>
            <button type="submit" class="btn btn-primary btn-block" id="btn-save-cfg">Save Configuration</button>
          </form>
          <p id="msg-cfg" class="msg"></p>
        </div>`;
    },
    async bind(app) {
      app.querySelector('#btn-logout').onclick = logout;

      // Reauth button
      const reauthBtn = app.querySelector('[data-action="reauth"]');
      if (reauthBtn) reauthBtn.onclick = () => {
        location.href = `${AUTH_FN}/auth/ctrader/start?user_id=${encodeURIComponent(state.user.$id)}`;
      };

      const connectBtn = app.querySelector('#btn-connect-ctrader');
      if (connectBtn) connectBtn.onclick = () => {
        location.href = `${AUTH_FN}/auth/ctrader/start?user_id=${encodeURIComponent(state.user.$id)}`;
      };

      if (!state.grantId) return;

      const msg = app.querySelector('#msg-cfg');
      const btn = app.querySelector('#btn-save-cfg');
      const form = app.querySelector('#form-config');
      const loading = app.querySelector('#cfg-loading');

      this._abortController = new AbortController();

      try {
        const list = await tablesDB.listRows({
          databaseId: DB_ID, tableId: 'trade_configs',
          queries: [Appwrite.Query.equal('slave_user_id', state.user.$id)],
        });
        if (list.rows && list.rows.length > 0) {
          const cfg = list.rows[0];
          app.querySelector('#cfg-lot').value = cfg.lot_size;
          app.querySelector('#cfg-mult').value = cfg.lot_multiplier;
          app.querySelector('#cfg-dd').value = cfg.max_daily_drawdown_pct;
          app.querySelector('#cfg-syms').value = cfg.allowed_symbols || '';
          app.querySelector('#cfg-copy').checked = cfg.copy_enabled;
        }
        this._cfgLoaded = true;
      } catch (e) {
        if (e.name === 'AbortError') return;
        console.warn('Config load failed', e);
        this._cfgLoaded = true; // Still show the form
      } finally {
        if (!this._abortController.signal.aborted) {
          loading.style.display = 'none';
          form.style.display = 'block';
          // Update the info grid with real data
          const gridContainer = app.querySelector('.info-grid');
          if (gridContainer) {
            const accountCount = state.ctraderAccountIds
              ? state.ctraderAccountIds.split(',').filter(Boolean).length
              : 0;
            const statusClass = statusBadgeClass(state.status);
            gridContainer.innerHTML = `
              <dt>Username</dt><dd>${esc(state.username) || '\u2014'}</dd>
              <dt>Grant ID</dt><dd><span class="code">${esc(state.grantId) || '\u2014'}</span></dd>
              <dt>Status</dt><dd><span class="badge badge-${statusClass}"><span class="status-dot ${statusClass}"></span>${esc(state.status || 'unknown')}</span></dd>
              <dt>Last Heartbeat</dt><dd>${state.lastHeartbeat ? timeAgo(state.lastHeartbeat) : '\u2014'}</dd>`;
            const accountsTitle = app.querySelector('h2');
            if (accountsTitle && accountsTitle.textContent.includes('Linked Accounts')) {
              const cardsContainer = accountsTitle.nextElementSibling;
              if (cardsContainer && cardsContainer.classList.contains('account-cards')) {
                cardsContainer.outerHTML = accountsHtml(state.accounts);
              }
            }
          }
        }
      }

      form.onsubmit = async (e) => {
        e.preventDefault();
        btn.disabled = true; btn.textContent = 'Saving...'; msg.className = 'msg';
        const payload = {
          lot_size: parseFloat(app.querySelector('#cfg-lot').value),
          lot_multiplier: parseFloat(app.querySelector('#cfg-mult').value),
          max_daily_drawdown_pct: parseFloat(app.querySelector('#cfg-dd').value),
          allowed_symbols: app.querySelector('#cfg-syms').value.trim(),
          copy_enabled: app.querySelector('#cfg-copy').checked,
        };
        try {
          const list = await tablesDB.listRows({
            databaseId: DB_ID, tableId: 'trade_configs',
            queries: [Appwrite.Query.equal('slave_user_id', state.user.$id)],
          });
          const existing = list.rows && list.rows[0];
          if (existing) {
            await tablesDB.updateRow({
              databaseId: DB_ID, tableId: 'trade_configs',
              rowId: existing.$id, data: payload,
            });
          } else {
            await tablesDB.createRow({
              databaseId: DB_ID, tableId: 'trade_configs',
              rowId: Appwrite.ID.unique(),
              data: { slave_user_id: state.user.$id, ...payload },
            });
          }
          showToast('success', 'Configuration saved');
          msg.textContent = ''; msg.className = 'msg';
        } catch (err) {
          if (err.name === 'AbortError') return;
          msg.textContent = 'Failed: ' + err.message; msg.className = 'msg error';
        } finally { btn.disabled = false; btn.textContent = 'Save Configuration'; }
      };
    },
    cleanup() {
      if (this._abortController) {
        this._abortController.abort();
        this._abortController = null;
      }
      this._cfgLoaded = false;
    }
  };

  const ViewMaster = {
    _realtimeSub: null,
    _slaveRefreshTimer: null,
    render() {
      return `
        <div class="card">
          <div class="header-row">
            <h1>Master Dashboard</h1>
            <div style="display:flex;gap:8px;align-items:center">
              <span class="badge badge-role">master</span>
              <button id="btn-logout" class="btn btn-secondary btn-sm">Log Out</button>
            </div>
          </div>
          ${reauthBannerHtml()}
          ${state.grantId ? '' : `
            <div class="empty-state" style="padding:20px 0">
              <p class="empty-desc">Connect your master cTrader account to send signals.</p>
              <button id="btn-connect-ctrader" class="btn btn-primary" style="margin-top:8px">Connect Master Account</button>
            </div><hr>`}
          <h2>Connected Slaves</h2>
          <div id="slave-list">
            <div class="table-wrap"><table>
              <thead><tr><th>Username</th><th>Grant</th><th>Accounts</th><th>Status</th><th>Active</th><th>Heartbeat</th></tr></thead>
              <tbody>${skeletonRows(5)}</tbody>
            </table></div>
          </div>
          <hr>
          <h2>Live Signals</h2>
          <div id="signal-feed">
            <div class="empty-state" style="padding:20px 0">
              <div class="empty-icon">\u{1F4E1}</div>
              <div class="empty-title">Waiting for signals</div>
              <p class="empty-desc">Signals will appear here in real time.</p>
            </div>
          </div>
        </div>`;
    },
    async bind(app) {
      app.querySelector('#btn-logout').onclick = logout;

      // Reauth button
      const reauthBtn = app.querySelector('[data-action="reauth"]');
      if (reauthBtn) reauthBtn.onclick = () => {
        location.href = `${AUTH_FN}/auth/ctrader/start?user_id=${encodeURIComponent(state.user.$id)}`;
      };

      const connectBtn = app.querySelector('#btn-connect-ctrader');
      if (connectBtn) connectBtn.onclick = () => {
        location.href = `${AUTH_FN}/auth/ctrader/start?user_id=${encodeURIComponent(state.user.$id)}`;
      };

      // Load slaves
      const loadSlaves = async () => {
        try {
          const data = await authApi('/admin/slaves');
          const container = app.querySelector('#slave-list');
          if (!container) return;
          const rows = data.slaves || [];
          if (rows.length === 0) {
            container.innerHTML = `
              <div class="empty-state" style="padding:20px 0">
                <div class="empty-icon">\u{1F465}</div>
                <div class="empty-title">No Slaves Connected</div>
                <p class="empty-desc">Slaves will appear here once they connect.</p>
              </div>`;
          } else {
            container.innerHTML = `<div class="table-wrap"><table>
              <thead><tr><th>Username</th><th>Grant</th><th>Accounts</th><th>Status</th><th>Active</th><th>Heartbeat</th></tr></thead>
              <tbody>${rows.map(s => {
                const acctCount = s.ctrader_account_ids
                  ? s.ctrader_account_ids.split(',').filter(Boolean).length
                  : 0;
                const hbClass = heartbeatColorClass(s.last_heartbeat_at);
                return `<tr>
                  <td style="font-family:var(--font-body);font-weight:500">${esc(s.username) || '\u2014'}</td>
                  <td><span class="code" title="${esc(s.grant_id)}">${esc(s.grant_id).slice(0, 12)}...</span></td>
                  <td><span class="badge badge-${acctCount > 0 ? 'active' : 'pending'}">${acctCount}</span></td>
                  <td><span class="badge badge-${statusBadgeClass(s.status)}"><span class="status-dot ${statusBadgeClass(s.status)}"></span>${esc(s.status)}</span></td>
                  <td>${s.active ? '\u2713' : '\u2014'}</td>
                  <td class="${hbClass}">${s.last_heartbeat_at ? timeAgo(s.last_heartbeat_at) : '\u2014'}</td>
                </tr>`;
              }).join('')}</tbody></table></div>`;
          }
        } catch (e) {
          const container = app.querySelector('#slave-list');
          if (container) container.innerHTML = `<p class="error">Failed to load slaves: ${esc(e.message)}</p>`;
        }
      };

      await loadSlaves();
      // Auto-refresh slave list every 30 seconds
      this._slaveRefreshTimer = setInterval(loadSlaves, 30000);

      // Realtime signal subscription
      try {
        this._realtimeSub = client.subscribe(
          `tablesdb.${DB_ID}.tables.master_signals.rows`,
          (response) => {
            const feed = app.querySelector('#signal-feed');
            if (!feed) return;
            // Clear empty state on first signal
            if (feed.querySelector('.empty-state')) feed.innerHTML = '';
            const sig = response.payload;
            const dirIcon = (sig.direction || '').toLowerCase() === 'buy' ? '\u25B2' : '\u25BC';
            const dirColor = (sig.direction || '').toLowerCase() === 'buy' ? 'var(--success)' : 'var(--error)';
            const entry = document.createElement('div');
            entry.className = 'signal';
            entry.innerHTML = `<span style="color:${dirColor};font-weight:700">${dirIcon}</span> <strong>${esc(sig.symbol)}</strong> ${esc(sig.direction)} @ ${sig.lot_size} lots \u2014 <em style="color:var(--text-dim)">${esc(sig.status)}</em>`;
            feed.prepend(entry);
            while (feed.children.length > 20) feed.lastChild.remove();
          }
        );
      } catch (e) { console.warn('Realtime failed:', e.message); }
    },
    cleanup() {
      if (this._realtimeSub) {
        try { this._realtimeSub.unsubscribe(); } catch {}
        this._realtimeSub = null;
      }
      if (this._slaveRefreshTimer) {
        clearInterval(this._slaveRefreshTimer);
        this._slaveRefreshTimer = null;
      }
    }
  };

  // ─── Helpers ─────────────────────────────────────────────────────
  function esc(str) {
    if (str == null) return '';
    return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }

  function statusBadgeClass(status) {
    const map = { active: 'active', reauth_required: 'inactive' };
    return map[status] || 'pending';
  }

  function heartbeatColorClass(iso) {
    if (!iso) return '';
    const diff = Date.now() - new Date(iso).getTime();
    const min = Math.floor(diff / 60000);
    if (min < 5) return 'heartbeat-fresh';
    if (min < 60) return 'heartbeat-warm';
    return 'heartbeat-stale';
  }

  function timeAgo(iso) {
    const diff = Date.now() - new Date(iso).getTime();
    const sec = Math.floor(diff / 1000);
    if (sec < 60) return 'just now';
    const min = Math.floor(sec / 60);
    if (min < 60) return `${min}m ago`;
    const hr = Math.floor(min / 60);
    if (hr < 24) return `${hr}h ago`;
    return `${Math.floor(hr / 24)}d ago`;
  }

  // ─── Init ────────────────────────────────────────────────────────
  async function init() {
    if (!initSdk()) {
      document.getElementById('app').innerHTML =
        '<div class="card center"><h1>Error</h1><p class="error">Failed to load Appwrite SDK.</p></div>';
      return;
    }
    try {
      await loadSession();
    } catch (e) {
      console.error('Session init failed:', e);
      state.error = e.message || 'Failed to initialize session';
    }
    state.loading = false;
    render();
    window.addEventListener('hashchange', render);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
