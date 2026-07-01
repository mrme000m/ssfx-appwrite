import { UI } from '../ui.js';
import { API } from '../api.js';

export function mount(container) {
  const section = UI.el('section', 'section glass-panel');
  section.innerHTML = `
    <div class="section-header">
      <h2 class="section-title">Signal Injector</h2>
      <span class="section-sub">Inject a manual signal into the live pipeline</span>
    </div>
    <form id="inject-form" class="form-grid">
      <div class="form-group"><label>Symbol</label><input name="symbol" required placeholder="EURUSD" /></div>
      <div class="form-group"><label>Direction</label>
        <select name="direction" required>
          <option value="BUY">BUY</option>
          <option value="SELL">SELL</option>
        </select>
      </div>
      <div class="form-group"><label>Signal Type</label>
        <select name="signal_type">
          <option value="NEW">NEW</option>
          <option value="TP_HIT">TP_HIT</option>
          <option value="SL_HIT">SL_HIT</option>
          <option value="CLOSE">CLOSE</option>
          <option value="CLOSE_HALF">CLOSE_HALF</option>
          <option value="SL_TO_ENTRY">SL_TO_ENTRY</option>
        </select>
      </div>
      <div class="form-group"><label>Entry Price</label><input name="entry_price" type="number" step="any" /></div>
      <div class="form-group"><label>SL</label><input name="sl" type="number" step="any" /></div>
      <div class="form-group"><label>TP1</label><input name="tp1" type="number" step="any" /></div>
      <div class="form-group"><label>TP2</label><input name="tp2" type="number" step="any" /></div>
      <div class="form-group"><label>TP3</label><input name="tp3" type="number" step="any" /></div>
      <div class="form-group"><label>Reply To Message ID</label><input name="reply_to_message_id" type="number" /></div>
      <div class="form-group" style="grid-column:1/-1"><label>Raw Text (optional)</label><textarea name="raw_text" rows="2"></textarea></div>
      <div class="form-group" style="grid-column:1/-1">
        <button type="submit" class="btn">Inject Signal</button>
      </div>
    </form>
  `;
  container.appendChild(section);

  section.querySelector('#inject-form').addEventListener('submit', async (ev) => {
    ev.preventDefault();
    const fd = new FormData(ev.target);
    const payload = {
      symbol: fd.get('symbol'),
      direction: fd.get('direction'),
      signal_type: fd.get('signal_type'),
      entry_price: fd.get('entry_price') ? parseFloat(fd.get('entry_price')) : null,
      sl: fd.get('sl') ? parseFloat(fd.get('sl')) : null,
      tp1: fd.get('tp1') ? parseFloat(fd.get('tp1')) : null,
      tp2: fd.get('tp2') ? parseFloat(fd.get('tp2')) : null,
      tp3: fd.get('tp3') ? parseFloat(fd.get('tp3')) : null,
      reply_to_message_id: fd.get('reply_to_message_id') ? parseInt(fd.get('reply_to_message_id'), 10) : null,
      raw_text: fd.get('raw_text') || '',
    };
    try {
      await API.injectSignal(payload);
      UI.toast('success', 'Signal injected');
      ev.target.reset();
      window.commandState.signals = await API.listSignals(30);
      window.commandBus.dispatchEvent(new CustomEvent('signals'));
    } catch (err) {
      UI.toast('error', `Inject failed: ${err.message}`);
    }
  });
}

window.SignalInjectorComponent = { mount };
