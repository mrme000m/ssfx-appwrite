/**
 * SSFX HQ — Manual signal injection (master only).
 */
window.InjectorComponent = (function () {
  function mount(container) {
    const section = document.createElement('section');
    section.innerHTML = `
      <div class="page-header">
        <h1>Inject Signal <span class="page-subtitle">manual broadcast</span></h1>
      </div>
      <div class="card">
        <form id="inject-form" class="form-grid">
          <div class="form-group">
            <label class="form-label">Symbol</label>
            <input name="symbol" class="form-input" value="XAUUSD" required />
          </div>
          <div class="form-group">
            <label class="form-label">Direction</label>
            <select name="direction" class="form-select">
              <option value="BUY">BUY</option>
              <option value="SELL">SELL</option>
            </select>
          </div>
          <div class="form-group">
            <label class="form-label">Entry Price</label>
            <input name="entry_price" type="number" step="0.01" class="form-input" />
          </div>
          <div class="form-group">
            <label class="form-label">SL</label>
            <input name="sl" type="number" step="0.01" class="form-input" />
          </div>
          <div class="form-group">
            <label class="form-label">TP1</label>
            <input name="tp1" type="number" step="0.01" class="form-input" />
          </div>
          <div class="form-group">
            <label class="form-label">TP2</label>
            <input name="tp2" type="number" step="0.01" class="form-input" />
          </div>
          <div class="form-group">
            <label class="form-label">TP3</label>
            <input name="tp3" type="number" step="0.01" class="form-input" />
          </div>
          <div class="form-group">
            <label class="form-label">Order Type</label>
            <select name="order_type" class="form-select">
              <option value="MARKET">MARKET</option>
              <option value="LIMIT">LIMIT</option>
            </select>
          </div>
          <div class="form-group" style="grid-column:1/-1">
            <label class="form-label">Raw Text (optional)</label>
            <input name="raw_text" class="form-input" placeholder="BUY XAUUSD @ 2350.50 SL 2345 TP 2360" />
          </div>
          <div style="grid-column:1/-1">
            <button type="submit" class="btn btn-primary">Broadcast Signal</button>
          </div>
        </form>
      </div>
    `;
    container.appendChild(section);

    section.querySelector('#inject-form').addEventListener('submit', async (ev) => {
      ev.preventDefault();
      const fd = new FormData(ev.target);
      const payload = {
        symbol: fd.get('symbol'),
        direction: fd.get('direction'),
        entry_price: fd.get('entry_price') ? Number(fd.get('entry_price')) : null,
        sl: fd.get('sl') ? Number(fd.get('sl')) : null,
        tp1: fd.get('tp1') ? Number(fd.get('tp1')) : null,
        tp2: fd.get('tp2') ? Number(fd.get('tp2')) : null,
        tp3: fd.get('tp3') ? Number(fd.get('tp3')) : null,
        order_type: fd.get('order_type'),
        raw_text: fd.get('raw_text') || undefined,
        chat_id: 'manual',
        message_id: Date.now(),
      };
      try {
        await window.API.V2API.injectSignal(payload);
        window.UI.toast('success', 'Signal injected and broadcast.');
        window.refreshSignals();
      } catch (err) {
        window.UI.toast('error', `Injection failed: ${err.message}`);
      }
    });
  }

  return { mount };
})();
