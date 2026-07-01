import { UI } from '../ui.js';
import { API } from '../api.js';
import { Auth } from '../auth.js';

const CFG = window.APP_CONFIG || {};

let presets = [];

async function loadPresets() {
  const db = Auth.getTablesDB();
  const result = await db.listRows({ databaseId: CFG.databaseId, tableId: CFG.presetsTable });
  presets = (result.rows || []).map(r => r.data || r);
}

async function applyPreset(preset, accountName) {
  const db = Auth.getTablesDB();
  const [accountRow, accountState] = await Promise.all([
    db.getRow({ databaseId: CFG.databaseId, tableId: CFG.accountsTable, rowId: accountName }),
    API.accountState(accountName).catch(() => null),
  ]);
  const data = accountRow.data || accountRow;
  const cfg = JSON.parse(data.config_json || '{}');
  const presetCfg = JSON.parse(preset.config_json || '{}');
  cfg.trading = { ...(cfg.trading || {}), ...(presetCfg.trading || {}) };
  await db.updateRow({
    databaseId: CFG.databaseId,
    tableId: CFG.accountsTable,
    rowId: accountName,
    data: {
      ...data,
      config_json: JSON.stringify(cfg),
      updated_at: new Date().toISOString(),
    },
  });
  UI.toast('success', `Applied ${preset.name} to ${accountName}`);
}

function renderPresets(container) {
  const grid = container.querySelector('#presets-grid');
  grid.innerHTML = '';
  const accounts = window.commandState.accounts || [];
  for (const preset of presets) {
    const card = UI.el('div', 'card');
    const desc = preset.description || '';
    card.innerHTML = `
      <h3 class="card-title">${UI.esc(preset.name)}</h3>
      <div class="card-meta">${UI.esc(desc)}</div>
      <div style="margin-top:12px;display:flex;gap:8px;">
        <select class="preset-account">${accounts.map(a => `<option value="${UI.esc(a.name)}">${UI.esc(a.name)}</option>`).join('')}</select>
        <button class="btn btn-apply">Apply</button>
      </div>
    `;
    card.querySelector('.btn-apply').addEventListener('click', () => {
      const accountName = card.querySelector('.preset-account').value;
      applyPreset(preset, accountName).catch(err => UI.toast('error', err.message));
    });
    grid.appendChild(card);
  }
}

export async function mount(container) {
  const section = UI.el('section', 'section glass-panel');
  section.innerHTML = `
    <div class="section-header">
      <h2 class="section-title">Trading Presets</h2>
      <span class="section-sub">Apply reusable risk/execution profiles</span>
    </div>
    <div class="card-grid" id="presets-grid"></div>
  `;
  container.appendChild(section);
  try {
    await loadPresets();
    renderPresets(section);
  } catch (err) {
    UI.toast('error', `Failed to load presets: ${err.message}`);
  }
}

window.PresetsComponent = { mount };
