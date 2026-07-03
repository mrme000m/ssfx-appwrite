/**
 * SSFX HQ — Parsed signal feed (master only).
 */
window.SignalsComponent = (function () {
  function signalRow(signal) {
    return `
      <tr>
        <td class="cell-mono">${window.UI.formatDateTime(signal.timestamp_ms)}</td>
        <td><span class="badge ${signal.direction === 'BUY' ? 'badge-teal' : 'badge-red'}">${window.UI.esc(signal.direction)}</span></td>
        <td class="cell-mono">${window.UI.esc(signal.symbol)}</td>
        <td class="cell-mono">${window.UI.formatPrice(signal.entry_price)}</td>
        <td class="cell-mono">${window.UI.formatPrice(signal.sl)}</td>
        <td class="cell-mono">${window.UI.formatPrice(signal.tp1)}</td>
        <td><span class="badge ${window.UI.badge(signal.status)}">${window.UI.esc(signal.status)}</span></td>
        <td>${window.UI.formatNumber(signal.parse_confidence, 2)}</td>
        <td><span class="badge ${signal.experience_action === 'allow' ? 'badge-teal' : signal.experience_action === 'block' ? 'badge-red' : 'badge-amber'}">${window.UI.esc(signal.experience_action || '—')}</span></td>
      </tr>
    `;
  }

  function mount(container) {
    const section = document.createElement('section');
    section.innerHTML = `
      <div class="page-header">
        <h1>Signals <span class="page-subtitle">parsed feed</span></h1>
      </div>
      <div class="panel">
        <div class="panel-body" style="padding:0;overflow:auto">
          <table class="data-table" id="signals-table">
            <thead>
              <tr>
                <th>Time</th><th>Dir</th><th>Symbol</th>
                <th>Entry</th><th>SL</th><th>TP1</th>
                <th>Status</th><th>Conf</th><th>Exp</th>
              </tr>
            </thead>
            <tbody></tbody>
          </table>
        </div>
      </div>
    `;
    container.appendChild(section);

    function render() {
      const tbody = section.querySelector('tbody');
      if (!tbody) return;
      const signals = window.appState.signals || [];
      if (signals.length === 0) {
        tbody.innerHTML = `<tr><td colspan="9">
          <div class="empty-state" style="border:none;padding:var(--sp-10)">
            <div class="empty-title">No signals yet</div>
            <p class="empty-desc">Signals will appear once the Telegram webhook or manual injector receives a message.</p>
            <div class="mt-4">
              <button class="btn btn-sm btn-primary" onclick="window.location.hash='#/inject'">
                Inject Test Signal
              </button>
            </div>
          </div>
        </td></tr>`;
        return;
      }
      tbody.innerHTML = signals.map(signalRow).join('');
    }

    render();
    window.commandBus.addEventListener('signals', render);
  }

  return { mount };
})();
