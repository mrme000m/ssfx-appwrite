/**
 * SSFX HQ — Slave fleet management (master only).
 */
window.FleetComponent = (function () {
  function fleetCard(account) {
    const cfg = account.config || {};
    const runtime = account.runtime || {};
    const isConnected = runtime.connected !== false;
    const isRunning = runtime.running === true;
    const card = document.createElement('div');
    card.className = `fleet-card ${!isConnected ? 'danger' : isRunning ? 'running' : 'stopped'}`;
    card.innerHTML = `
      <div class="fleet-card-header">
        <span class="fleet-card-name">${window.UI.esc(account.name)}</span>
        <span class="badge ${isRunning ? 'badge-green' : 'badge-amber'}">${isRunning ? 'RUNNING' : 'STOPPED'}</span>
      </div>
      <div class="fleet-card-meta">
        <span>${window.UI.esc(cfg.ctrader?.host_type || 'demo')} / ${window.UI.esc(cfg.trading?.execution_mode || 'demo')}</span>
        <span>positions: ${runtime.active_positions ?? 0} / connected: ${isConnected ? 'yes' : 'no'}</span>
        <span>vol: ${cfg.trading?.volume_mode || 'fixed'} ${cfg.trading?.volume_value ?? cfg.trading?.default_volume ?? 0.01}</span>
      </div>
      <div class="fleet-card-actions">
        <button class="btn btn-sm btn-edit">Edit</button>
        <button class="btn btn-sm btn-ghost btn-toggle">${account.enabled ? 'Disable' : 'Enable'}</button>
      </div>
    `;
    card.querySelector('.btn-edit').addEventListener('click', () => window.AccountEditor.openAccountEditor(account));
    card.querySelector('.btn-toggle').addEventListener('click', () => toggleAccount(account));
    return card;
  }

  async function toggleAccount(account) {
    const next = !account.enabled;
    try {
      await window.API.V2API.updateAccount(account.name, { enabled: next });
      window.UI.toast('success', `${account.name} ${next ? 'enabled' : 'disabled'}`);
      await window.refreshAccounts();
    } catch (err) {
      window.UI.toast('error', `Toggle failed: ${err.message}`);
    }
  }

  function mount(container) {
    const section = document.createElement('section');
    section.innerHTML = `
      <div class="page-header">
        <h1>Slave Fleet <span class="page-subtitle">${window.appState.accounts?.length ?? 0} account(s)</span></h1>
      </div>
      <div class="data-grid data-grid-auto" id="fleet-grid"></div>
    `;
    container.appendChild(section);

    function render() {
      const grid = section.querySelector('#fleet-grid');
      if (!grid) return;
      const accounts = window.appState.accounts || [];
      grid.innerHTML = '';
      if (accounts.length === 0) {
        grid.innerHTML = `
          <div class="empty-state">
            <div class="empty-title">No accounts configured</div>
            <p class="empty-desc">Accounts are managed in the SSFX server config and stored in Appwrite TablesDB.</p>
          </div>`;
        return;
      }
      for (const acct of accounts) {
        grid.appendChild(fleetCard(acct));
      }
    }

    render();
    window.commandBus.addEventListener('accounts', render);
  }

  return { mount };
})();
