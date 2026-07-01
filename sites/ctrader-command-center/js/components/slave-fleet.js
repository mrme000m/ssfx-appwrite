import { UI } from '../ui.js';
import { API } from '../api.js';
import { Auth } from '../auth.js';
import { openAccountEditor } from './account-editor.js';

const CFG = window.APP_CONFIG || {};

function accountCard(account) {
  const cfg = account.config || {};
  const runtime = account.runtime || {};
  const card = UI.el('div', `card ${runtime.connected === false ? 'danger' : ''}`);
  card.innerHTML = `
    <div class="section-header" style="margin-bottom:8px;">
      <h3 class="card-title">${UI.esc(account.name)}</h3>
      <span class="badge ${runtime.running ? 'executed' : 'pending'}">${runtime.running ? 'RUNNING' : 'STOPPED'}</span>
    </div>
    <div class="card-meta">
      host: ${UI.esc(cfg.ctrader?.host_type || 'demo')} · mode: ${UI.esc(cfg.trading?.execution_mode || 'demo')} ·
      positions: ${runtime.active_positions || 0} · connected: ${runtime.connected === false ? 'NO' : 'YES'}
    </div>
    <div class="card-meta" style="margin-top:6px;">
      volume: ${cfg.trading?.volume_mode || 'fixed_lots'} ${cfg.trading?.volume_value ?? cfg.trading?.default_volume ?? 0.01}
    </div>
    <div style="margin-top:12px;display:flex;gap:8px;">
      <button class="btn btn-edit">Edit</button>
      <button class="btn secondary btn-toggle">${account.enabled ? 'Disable' : 'Enable'}</button>
    </div>
  `;
  card.querySelector('.btn-edit').addEventListener('click', () => openAccountEditor(account));
  card.querySelector('.btn-toggle').addEventListener('click', () => toggleAccount(account));
  return card;
}

async function toggleAccount(account) {
  const db = Auth.getTablesDB();
  try {
    await db.updateRow({
      databaseId: CFG.databaseId,
      tableId: CFG.accountsTable,
      rowId: account.name,
      data: { enabled: !account.enabled },
    });
    UI.toast('success', `${account.name} ${!account.enabled ? 'enabled' : 'disabled'}`);
    window.commandState.accounts = await API.listAccounts();
    render();
  } catch (err) {
    UI.toast('error', `Toggle failed: ${err.message}`);
  }
}

function render() {
  const stage = document.getElementById('main-stage');
  if (!stage) return;
  stage.innerHTML = '';
  const section = UI.el('section', 'section glass-panel');
  section.innerHTML = `
    <div class="section-header">
      <h2 class="section-title">Slave Fleet</h2>
      <span class="section-sub">${window.commandState.accounts.length} account(s) configured</span>
    </div>
    <div class="card-grid" id="fleet-grid"></div>
  `;
  const grid = section.querySelector('#fleet-grid');
  for (const account of window.commandState.accounts) {
    grid.appendChild(accountCard(account));
  }
  stage.appendChild(section);
}

export function mount(container) {
  render();
  window.commandBus.addEventListener('accounts', render);
}

window.commandBus?.addEventListener('accounts', render);

window.FleetComponent = { mount };
