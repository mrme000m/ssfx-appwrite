/**
 * SSFX HQ — Dashboard overview for slaves and masters.
 *
 * Shows the user's persisted auth state, cTrader connection, discovered
 * live/demo accounts, selected trading account, and linked trade-config preset.
 */
window.DashboardComponent = (function () {
  function fmtMoney(n, digits) {
    if (n == null || Number.isNaN(Number(n))) return '—';
    return Number(n).toLocaleString('en-US', {
      minimumFractionDigits: digits ?? 2,
      maximumFractionDigits: digits ?? 2,
    });
  }

  function leverageDisplay(cents) {
    const n = Number(cents);
    if (!n) return '—';
    return `${(n / 100).toFixed(0)}:1`;
  }

  function accountTypeLabel(type) {
    const t = String(type || '').toUpperCase();
    if (t === 'NETTED' || t === '1') return 'Netting';
    if (t === 'SPREAD_BETTING' || t === '2') return 'Spread Bet';
    return t || 'Hedging';
  }

  function roleBadge(role) {
    const isMaster = role === 'master';
    const isAdmin = role === 'admin';
    const badgeClass = isAdmin ? 'badge-purple' : isMaster ? 'badge-coral' : 'badge-teal';
    const label = isAdmin ? 'admin' : isMaster ? 'master' : role || 'slave';
    return `<span class="badge ${badgeClass}">${label}</span>`;
  }

  function statusClass(status) {
    if (status === 'active') return 'badge-green';
    if (status === 'reauth_required') return 'badge-red';
    return 'badge-amber';
  }

  function renderConnectionCard() {
    const connected = !!window.appState.grantId;
    const status = window.appState.status || (connected ? 'active' : 'disconnected');
    const userId = window.appState.userId || '';
    const connectUrl = `${window.API.CFG.authDomain}/auth/ctrader/start${userId ? '?user_id=' + encodeURIComponent(userId) : ''}`;
    const accounts = window.appState.accounts || [];
    const hasAccounts = accounts.length > 0;

    return `
      <div class="card account-connection-card">
        <div class="card-header">
          <div>
            <h3>cTrader Connection</h3>
            <p class="text-dim" style="margin:0;font-size:0.8125rem">${connected ? 'Your cTrader identity is linked.' : 'Link a cTrader account to start trading.'}</p>
            ${connected && hasAccounts ? `<p class="text-dim" style="margin:4px 0 0 0;font-size:0.75rem">${accounts.length} account${accounts.length > 1 ? 's' : ''} discovered</p>` : ''}
          </div>
          <span class="badge ${statusClass(status)}">${window.UI.esc(status)}</span>
        </div>
        <div class="flex gap-3" style="margin-top:8px">
          <a class="btn ${connected ? 'btn-ghost' : 'btn-primary'}" href="${connectUrl}">
            ${connected ? 'Add Another Account' : 'Connect cTrader Account'}
          </a>
          ${connected ? `<a class="btn btn-ghost" href="#/trade-config">Edit Trade Config</a>` : ''}
        </div>
        ${connected && hasAccounts ? `
          <div class="account-connection-info" style="margin-top:12px;padding-top:12px;border-top:1px solid var(--border)">
            <div class="flex items-center gap-2">
              <span class="status-dot ${status === 'active' ? 'online' : 'offline'}"></span>
              <span class="text-sm">Grant ID: <code class="mono">${window.UI.esc(window.appState.grantId || '')}</code></span>
            </div>
          </div>
        ` : ''}
      </div>`;
  }

  function renderAccountCards() {
    const accounts = window.appState.accounts || [];
    const selectedId = window.appState.selectedAccountId || '';

    if (!window.appState.grantId) {
      return '';
    }

    if (accounts.length === 0) {
      return `
        <div class="card">
          <div class="card-header">
            <h3>Linked Accounts</h3>
          </div>
          <div class="empty-state" style="padding:var(--sp-8) var(--sp-4)">
            <div class="empty-title">No accounts discovered</div>
            <p class="empty-desc">After connecting cTrader, your live/demo accounts will appear here once the runtime discovers them.</p>
          </div>
        </div>`;
    }

    const cards = accounts.map((acc) => {
      const id = String(acc.ctid_trader_account_id || acc.ctidTraderAccountId || '');
      const login = String(acc.trader_login || acc.traderLogin || '');
      const broker = String(acc.broker_title_short || acc.brokerTitleShort || acc.broker_name || acc.brokerName || 'Unknown Broker');
      const isLive = acc.is_live === true || acc.isLive === true;
      const selected = String(selectedId) === String(id);
      const balance = typeof acc.balance === 'number' ? acc.balance : null;
      const moneyDigits = acc.money_digits || acc.moneyDigits || 0;
      const formattedBalance = balance !== null ? fmtMoney(balance, moneyDigits > 0 ? moneyDigits : 2) : '—';
      const currency = String(acc.deposit_asset_id || acc.depositAssetId || '');

      return `
        <div class="account-card ${selected ? 'account-card-selected' : ''}" data-account-id="${window.UI.esc(id)}">
          <div class="account-card-header">
            <span class="account-card-broker">${window.UI.esc(broker)}</span>
            <span class="badge ${isLive ? 'badge-green' : 'badge-amber'}">${isLive ? 'Live' : 'Demo'}</span>
          </div>
          <div class="account-card-balance">
            ${formattedBalance}<span class="account-card-currency">${window.UI.esc(currency)}</span>
          </div>
          <div class="account-card-meta">
            <span class="text-dim">${window.UI.esc(accountTypeLabel(acc.account_type || acc.accountType))}</span>
            <span class="text-dim">${leverageDisplay(acc.leverage_in_cents || acc.leverageInCents)}</span>
            <span class="text-dim mono">${login ? 'Login ' + window.UI.esc(login) : ''}</span>
          </div>
          <div class="account-card-actions">
            ${selected
              ? '<span class="badge badge-coral"><span class="status-dot online"></span> Selected</span>'
              : `<button type="button" class="btn btn-sm btn-primary select-account-btn" data-account-id="${window.UI.esc(id)}">Select Account</button>`}
          </div>
        </div>`;
    }).join('');

    return `
      <div class="card">
        <div class="card-header">
          <div>
            <h3>Linked Accounts <span class="page-subtitle">${accounts.length} discovered</span></h3>
            <p class="text-dim" style="margin:0;font-size:0.8125rem">Choose the account you want to trade on. The selected account is used for copy trading.</p>
          </div>
        </div>
        <div class="account-grid">${cards}</div>
      </div>`;
  }

  function renderTradeConfigPreview() {
    const cfg = window.appState.tradeConfig || {};
    const copyEnabled = cfg.copy_enabled;
    const hasCfg = Object.keys(cfg).length > 0;

    return `
      <div class="card">
        <div class="card-header">
          <h3>Trade Config Preset</h3>
          <span class="badge ${copyEnabled ? 'badge-green' : 'badge-ghost'}">${copyEnabled ? 'Copy enabled' : 'Copy disabled'}</span>
        </div>
        ${hasCfg ? `
          <div class="data-grid data-grid-3" style="margin-bottom:12px">
            <div class="stat-card">
              <div class="stat-label">Lot Size</div>
              <div class="stat-value" style="font-size:1.25rem">${cfg.lot_size ?? '—'}</div>
            </div>
            <div class="stat-card">
              <div class="stat-label">Multiplier</div>
              <div class="stat-value" style="font-size:1.25rem">${cfg.lot_multiplier ?? '—'}</div>
            </div>
            <div class="stat-card">
              <div class="stat-label">Max Drawdown</div>
              <div class="stat-value" style="font-size:1.25rem">${cfg.max_daily_drawdown_pct ?? '—'}%</div>
            </div>
          </div>
          <p class="text-dim" style="font-size:0.8125rem">Allowed symbols: ${(cfg.allowed_symbols || []).length ? cfg.allowed_symbols.map(window.UI.esc).join(', ') : 'All symbols'}</p>
        ` : `
          <div class="empty-state" style="padding:var(--sp-8) var(--sp-4)">
            <div class="empty-title">No preset configured</div>
            <p class="empty-desc">Set your lot size, multiplier, and symbol filters before starting copy trading.</p>
            <a class="btn btn-primary" href="#/trade-config" style="margin-top:12px">Create Preset</a>
          </div>
        `}
      </div>`;
  }

  function renderMasterDashboard(container) {
    const accounts = window.appState.accounts || [];
    const running = accounts.filter((a) => a.runtime?.running).length;
    const connected = accounts.filter((a) => a.runtime?.connected !== false).length;

    if (!window.appState.initialized) {
      container.innerHTML = `
        <div class="page-header">
          <h1>Dashboard <span class="page-subtitle">master overview</span></h1>
          ${roleBadge(window.appState.role)}
        </div>
        <div class="skeleton" style="height:200px;border-radius:var(--radius-lg)"></div>
      `;
      return;
    }

    container.innerHTML = `
      <div class="page-header">
        <h1>Dashboard <span class="page-subtitle">master overview</span></h1>
        ${roleBadge(window.appState.role)}
      </div>
      <div class="data-grid data-grid-4">
        <div class="stat-card">
          <div class="stat-label">Accounts</div>
          <div class="stat-value">${accounts.length}</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">Running</div>
          <div class="stat-value">${running}</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">Connected</div>
          <div class="stat-value">${connected}</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">System</div>
          <div class="stat-value" style="font-size:1.1rem">${window.appState.online ? 'Online' : 'Offline'}</div>
        </div>
      </div>

      ${renderConnectionCard()}
      ${renderAccountCards()}
      ${renderTradeConfigPreview()}

      <div class="card mt-6">
        <h3 class="mb-4">Fleet Snapshot</h3>
        ${accounts.length === 0 ? '<p class="text-dim">No accounts configured.</p>' : `
          <table class="data-table">
            <thead>
              <tr><th>Name</th><th>Host</th><th>Status</th><th>Positions</th></tr>
            </thead>
            <tbody>
              ${accounts.slice(0, 10).map((a) => `
                <tr>
                  <td class="cell-mono">${window.UI.esc(a.name)}</td>
                  <td>${window.UI.esc(a.host_type || 'demo')}</td>
                  <td><span class="badge ${a.runtime?.running ? 'badge-green' : 'badge-amber'}">${a.runtime?.running ? 'Running' : 'Stopped'}</span></td>
                  <td>${a.runtime?.active_positions ?? 0}</td>
                </tr>
              `).join('')}
            </tbody>
          </table>
        `}
      </div>
    `;
  }

  function renderSlaveDashboard(container) {
    if (!window.appState.initialized) {
      container.innerHTML = `
        <div class="page-header">
          <h1>Dashboard <span class="page-subtitle">slave overview</span></h1>
          ${roleBadge(window.appState.role)}
        </div>
        <div class="skeleton" style="height:200px;border-radius:var(--radius-lg)"></div>
      `;
      return;
    }

    container.innerHTML = `
      <div class="page-header">
        <h1>Dashboard <span class="page-subtitle">slave overview</span></h1>
        ${roleBadge(window.appState.role)}
      </div>
      <div class="data-grid data-grid-3">
        <div class="stat-card">
          <div class="stat-label">Status</div>
          <div class="stat-value">${window.UI.esc(window.appState.status || 'unknown')}</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">Grant ID</div>
          <div class="stat-value" style="font-size:0.9rem;word-break:break-all">${window.UI.esc(window.appState.grantId || 'none')}</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">Active</div>
          <div class="stat-value">${window.appState.active ? 'Yes' : 'No'}</div>
        </div>
      </div>

      ${renderConnectionCard()}
      ${renderAccountCards()}
      ${renderTradeConfigPreview()}
    `;
  }

  function bindAccountSelection(container) {
    container.querySelectorAll('.select-account-btn').forEach((btn) => {
      btn.addEventListener('click', async () => {
        const id = btn.dataset.accountId;
        if (!id) return;
        btn.disabled = true;
        btn.textContent = 'Selecting...';
        try {
          await window.Auth.selectAccount(id);
          window.UI.toast('success', 'Trading account selected', 'Account');
        } catch (err) {
          window.UI.toast('error', err.message || 'Could not select account', 'Account');
        }
      });
    });
  }

  async function loadTradeConfig() {
    try {
      const db = window.Auth.getTablesDB();
      if (!db || !window.appState.userId) return;
      const result = await db.listRows({
        databaseId: window.API.CFG.databaseId,
        tableId: 'trade_configs',
        queries: [window.Appwrite.Query.equal('slave_user_id', window.appState.userId)],
      });
      const rows = result.rows || [];
      window.appState.tradeConfig = rows.length ? rows[0] : {};
    } catch (err) {
      window.appState.tradeConfig = {};
    }
  }

  function mount(container) {
    let pending = false;

    async function render() {
      if (pending) return;
      pending = true;
      await loadTradeConfig();
      if (window.Auth.isMaster()) {
        renderMasterDashboard(container);
      } else {
        renderSlaveDashboard(container);
      }
      bindAccountSelection(container);
      pending = false;
    }

    render();
    window.commandBus.addEventListener('accounts', render);
    window.commandBus.addEventListener('health', render);
    window.commandBus.addEventListener('auth-changed', render);
  }

  return { mount };
})();
