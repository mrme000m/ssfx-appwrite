/**
 * SSFX HQ — Slave trade configuration editor (TablesDB trade_settings).
 */
window.TradeConfigComponent = (function () {
  const TABLE_ID = 'trade_settings';

  async function loadConfig(db) {
    const userId = window.appState.userId;
    if (!userId) return null;
    try {
      const result = await db.listRows({
        databaseId: window.API.CFG.databaseId,
        tableId: TABLE_ID,
        queries: [window.Appwrite.Query.equal('slave_user_id', userId)],
      });
      const rows = result.rows || [];
      if (rows.length === 0) return null;
      const data = rows[0].data || rows[0];
      const cfg = { $id: data.$id || data._id, ...data };
      window.appState.tradeConfig = cfg;
      return cfg;
    } catch (err) {
      window.UI.toast('error', `Failed to load trade config: ${err.message}`);
      return null;
    }
  }

  async function saveConfig(db, existing, values) {
    const userId = window.appState.userId;
    const row = {
      slave_user_id: userId,
      lot_size: Number(values.lot_size) || 0.01,
      lot_multiplier: Number(values.lot_multiplier) || 1.0,
      max_daily_drawdown_pct: Number(values.max_daily_drawdown_pct) || 5.0,
      allowed_symbols: values.allowed_symbols.split(',').map((s) => s.trim()).filter(Boolean),
      copy_enabled: values.copy_enabled === 'on',
    };

    try {
      if (existing?.$id) {
        await db.updateRow({
          databaseId: window.API.CFG.databaseId,
          tableId: TABLE_ID,
          rowId: existing.$id,
          data: row,
        });
      } else {
        const created = await db.createRow({
          databaseId: window.API.CFG.databaseId,
          tableId: TABLE_ID,
          data: row,
          permissions: [
            window.Appwrite.Permission.read(window.Appwrite.Role.user(userId)),
            window.Appwrite.Permission.update(window.Appwrite.Role.user(userId)),
            window.Appwrite.Permission.delete(window.Appwrite.Role.user(userId)),
          ],
        });
        existing = { $id: created.$id };
      }
      window.appState.tradeConfig = { ...existing, ...row };
      window.UI.toast('success', 'Trade config saved.');
    } catch (err) {
      window.UI.toast('error', `Save failed: ${err.message}`);
    }
  }

  function mount(container) {
    const db = window.Auth.getTablesDB();
    if (!db) {
      container.innerHTML = `<div class="empty-state">
        <div class="empty-title">Appwrite SDK not loaded</div>
      </div>`;
      return;
    }

    container.innerHTML = `
      <div class="page-header">
        <h1>Trade Config <span class="page-subtitle">copy-trading settings</span></h1>
      </div>
      <div class="card">
        <form id="trade-config-form" class="form-grid">
          <div class="form-group">
            <label class="form-label">Lot Size</label>
            <input name="lot_size" type="number" step="0.01" class="form-input" />
          </div>
          <div class="form-group">
            <label class="form-label">Lot Multiplier</label>
            <input name="lot_multiplier" type="number" step="0.01" class="form-input" />
          </div>
          <div class="form-group">
            <label class="form-label">Max Daily Drawdown %</label>
            <input name="max_daily_drawdown_pct" type="number" step="0.1" class="form-input" />
          </div>
          <div class="form-group">
            <label class="form-label">Allowed Symbols</label>
            <input name="allowed_symbols" class="form-input" placeholder="XAUUSD, EURUSD" />
          </div>
          <div class="form-group" style="grid-column:1/-1">
            <label class="form-row">
              <input type="checkbox" name="copy_enabled" />
              <span>Enable copy trading</span>
            </label>
          </div>
          <div style="grid-column:1/-1">
            <button type="submit" class="btn btn-primary">Save Trade Config</button>
          </div>
        </form>
      </div>
    `;

    loadConfig(db).then((cfg) => {
      const form = container.querySelector('#trade-config-form');
      if (!form) return;
      form.elements.lot_size.value = cfg?.lot_size ?? 0.01;
      form.elements.lot_multiplier.value = cfg?.lot_multiplier ?? 1.0;
      form.elements.max_daily_drawdown_pct.value = cfg?.max_daily_drawdown_pct ?? 5.0;
      const syms = Array.isArray(cfg?.allowed_symbols)
        ? cfg.allowed_symbols
        : (cfg?.allowed_symbols || '').toString().split(',').map((s) => s.trim()).filter(Boolean);
      form.elements.allowed_symbols.value = syms.join(', ');
      form.elements.copy_enabled.checked = cfg?.copy_enabled ?? true;

      form.addEventListener('submit', (ev) => {
        ev.preventDefault();
        const fd = new FormData(ev.target);
        const values = Object.fromEntries(fd.entries());
        values.copy_enabled = fd.get('copy_enabled');
        
        // Validate required fields
        let isValid = true;
        const lotSize = form.elements.lot_size;
        const lotMultiplier = form.elements.lot_multiplier;
        const maxDrawdown = form.elements.max_daily_drawdown_pct;
        
        window.UI.clearInlineError(lotSize);
        window.UI.clearInlineError(lotMultiplier);
        window.UI.clearInlineError(maxDrawdown);
        
        if (!lotSize.value || Number(lotSize.value) <= 0) {
          window.UI.showInlineError(lotSize, 'Lot size must be greater than 0');
          isValid = false;
        }
        if (!lotMultiplier.value || Number(lotMultiplier.value) <= 0) {
          window.UI.showInlineError(lotMultiplier, 'Multiplier must be greater than 0');
          isValid = false;
        }
        if (!maxDrawdown.value || Number(maxDrawdown.value) <= 0) {
          window.UI.showInlineError(maxDrawdown, 'Max drawdown must be greater than 0');
          isValid = false;
        }
        
        if (!isValid) {
          return;
        }
        
        saveConfig(db, cfg, values).then(() => {
          window.commandBus.dispatchEvent(new CustomEvent('accounts'));
        });
      });
    });
  }

  return { mount };
})();
