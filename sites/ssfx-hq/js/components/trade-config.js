/**
 * SSFX HQ — Trade Config editor (slave dashboard)
 */
import { UI } from '../ui.js';
import { Auth } from '../auth.js';

const CFG = window.APP_CONFIG || {};
const DB_ID = CFG.databaseId;

export function mount(container) {
  const section = document.createElement('section');
  section.innerHTML = `
    <div class="page-header">
      <h1>Trade Configuration <span class="page-subtitle">Control how signals are copied to your account</span></h1>
    </div>
    <div class="panel">
      <div class="panel-body">
        <div id="cfg-loading"><div class="loading-spinner"></div><p class="text-dim" style="margin-top:var(--sp-2)">Loading configuration...</p></div>
        <form id="form-config" style="display:none">
          <div class="form-section">
            <div class="form-section-title">Copy Control</div>
            <div class="form-grid">
              <div class="form-group"><label class="form-label">Copy Trading</label><div class="form-row"><input type="checkbox" id="cfg-copy" checked /><label>Enabled</label></div></div>
              <div class="form-group"><label class="form-label">Lot Size</label><input type="number" id="cfg-lot" class="form-input" step="0.01" min="0.01" value="0.01" /></div>
              <div class="form-group"><label class="form-label">Lot Multiplier</label><input type="number" id="cfg-mult" class="form-input" step="0.1" min="0.1" value="1.0" /></div>
            </div>
          </div>
          <div class="form-section">
            <div class="form-section-title">Risk Management</div>
            <div class="form-grid">
              <div class="form-group"><label class="form-label">Max Daily Drawdown (%)</label><input type="number" id="cfg-dd" class="form-input" step="0.1" min="0" value="5.0" /></div>
            </div>
          </div>
          <div class="form-section">
            <div class="form-section-title">Symbol Filter</div>
            <div class="form-grid">
              <div class="form-group" style="grid-column:1/-1"><label class="form-label">Allowed Symbols</label><input type="text" id="cfg-syms" class="form-input" placeholder="XAUUSD, EURUSD (empty = all)" /></div>
            </div>
          </div>
          <button type="submit" class="btn btn-primary">Save Configuration</button>
          <p id="cfg-msg" class="auth-msg" style="margin-top:var(--sp-3)"></p>
        </form>
      </div>
    </div>
  `;
  container.appendChild(section);

  const db = Auth.getTablesDB();
  const userId = Auth.user?.$id;
  if (!userId || !db) return;

  (async () => {
    const loading = section.querySelector('#cfg-loading');
    const form = section.querySelector('#form-config');
    try {
      const list = await db.listRows({
        databaseId: DB_ID, tableId: 'trade_configs',
        queries: [Appwrite.Query.equal('slave_user_id', userId)],
      });
      if (list.rows?.length > 0) {
        const cfg = list.rows[0];
        section.querySelector('#cfg-lot').value = cfg.lot_size;
        section.querySelector('#cfg-mult').value = cfg.lot_multiplier;
        section.querySelector('#cfg-dd').value = cfg.max_daily_drawdown_pct;
        section.querySelector('#cfg-syms').value = cfg.allowed_symbols || '';
        section.querySelector('#cfg-copy').checked = cfg.copy_enabled;
      }
    } catch (e) {
      console.warn('Config load failed', e);
    } finally {
      loading.style.display = 'none';
      form.style.display = 'block';
    }
  })();

  section.querySelector('#form-config').addEventListener('submit', async (ev) => {
    ev.preventDefault();
    const btn = section.querySelector('#form-config button[type="submit"]');
    const msg = section.querySelector('#cfg-msg');
    btn.disabled = true; btn.textContent = 'Saving...'; msg.className = 'auth-msg';

    const payload = {
      lot_size: parseFloat(section.querySelector('#cfg-lot').value),
      lot_multiplier: parseFloat(section.querySelector('#cfg-mult').value),
      max_daily_drawdown_pct: parseFloat(section.querySelector('#cfg-dd').value),
      allowed_symbols: section.querySelector('#cfg-syms').value.trim(),
      copy_enabled: section.querySelector('#cfg-copy').checked,
    };

    try {
      const list = await db.listRows({
        databaseId: DB_ID, tableId: 'trade_configs',
        queries: [Appwrite.Query.equal('slave_user_id', userId)],
      });
      const existing = list.rows?.[0];
      if (existing) {
        await db.updateRow({ databaseId: DB_ID, tableId: 'trade_configs', rowId: existing.$id, data: payload });
      } else {
        await db.createRow({
          databaseId: DB_ID,
          tableId: 'trade_configs',
          rowId: Appwrite.ID.unique(),
          data: { slave_user_id: userId, ...payload },
          permissions: [
            Appwrite.Permission.read(Appwrite.Role.user(userId)),
            Appwrite.Permission.update(Appwrite.Role.user(userId)),
            Appwrite.Permission.delete(Appwrite.Role.user(userId)),
          ],
        });
      }
      UI.toast('success', 'Configuration saved');
    } catch (err) {
      msg.textContent = 'Failed: ' + err.message; msg.className = 'auth-msg error';
    } finally {
      btn.disabled = false; btn.textContent = 'Save Configuration';
    }
  });
}

window.TradeConfigComponent = { mount };
