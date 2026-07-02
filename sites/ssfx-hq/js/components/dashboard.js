/**
 * SSFX HQ — Dashboard overview for slaves and masters.
 */
window.DashboardComponent = (function () {
  function renderSlaveDashboard(container) {
    const cfg = window.appState.user || {};
    container.innerHTML = `
      <div class="page-header">
        <h1>Dashboard <span class="page-subtitle">slave overview</span></h1>
      </div>
      <div class="data-grid data-grid-3">
        <div class="stat-card">
          <div class="stat-label">Status</div>
          <div class="stat-value">${window.UI.esc(window.appState.status || 'unknown')}</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">Grant ID</div>
          <div class="stat-value" style="font-size:1rem">${window.UI.esc(window.appState.grantId || 'none')}</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">Active</div>
          <div class="stat-value">${window.appState.active ? 'Yes' : 'No'}</div>
        </div>
      </div>
      <div class="card mt-6">
        <h3 class="mb-4">Quick Actions</h3>
        <div class="flex gap-3">
          <a class="btn btn-primary" href="#/trade-config">Edit Trade Config</a>
          <a class="btn btn-ghost" href="${window.API.CFG.authDomain}/auth/ctrader/start">Reconnect cTrader</a>
        </div>
      </div>
    `;
  }

  function renderMasterDashboard(container) {
    const accounts = window.appState.accounts || [];
    const running = accounts.filter((a) => a.runtime?.running).length;
    const connected = accounts.filter((a) => a.runtime?.connected !== false).length;

    container.innerHTML = `
      <div class="page-header">
        <h1>Dashboard <span class="page-subtitle">master overview</span></h1>
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

  function mount(container) {
    function render() {
      if (window.Auth.isMaster()) {
        renderMasterDashboard(container);
      } else {
        renderSlaveDashboard(container);
      }
    }
    render();
    window.commandBus.addEventListener('accounts', render);
    window.commandBus.addEventListener('health', render);
  }

  return { mount };
})();
