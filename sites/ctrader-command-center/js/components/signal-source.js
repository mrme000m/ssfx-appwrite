import { UI } from '../ui.js';
import { API } from '../api.js';

function signalRow(signal) {
  const dirClass = signal.direction === 'BUY' ? 'buy' : signal.direction === 'SELL' ? 'sell' : 'pending';
  return `
    <tr>
      <td>${UI.esc(UI.fmtTime(signal.timestamp_ms))}</td>
      <td>${UI.esc(signal.signal_type)}</td>
      <td><span class="badge ${dirClass}">${UI.esc(signal.direction || '-')}</span></td>
      <td>${UI.esc(signal.symbol || '-')}</td>
      <td>${UI.fmtNumber(signal.entry_price)}</td>
      <td>SL ${UI.fmtNumber(signal.sl)} / TP ${UI.fmtNumber(signal.tp1)}</td>
      <td>${UI.esc(signal.parser_used)} · ${(signal.parse_confidence * 100).toFixed(0)}%</td>
      <td><span class="badge ${signal.status}">${UI.esc(signal.status)}</span></td>
    </tr>
  `;
}

export function mount(container) {
  const section = UI.el('section', 'section glass-panel');
  section.innerHTML = `
    <div class="section-header">
      <h2 class="section-title">Signal Source</h2>
      <span class="section-sub">Parsed Telegram signals + manual injections</span>
    </div>
    <div style="overflow-x:auto;">
      <table class="data-table">
        <thead>
          <tr>
            <th>Time</th>
            <th>Type</th>
            <th>Dir</th>
            <th>Symbol</th>
            <th>Entry</th>
            <th>SL / TP</th>
            <th>Parser</th>
            <th>Status</th>
          </tr>
        </thead>
        <tbody id="signals-body"></tbody>
      </table>
    </div>
  `;
  container.appendChild(section);

  function render() {
    const body = section.querySelector('#signals-body');
    if (!body) return;
    const signals = window.commandState.signals || [];
    body.innerHTML = signals.length ? signals.map(signalRow).join('') : `<tr><td colspan="8" style="color:var(--text-dim)">No signals yet.</td></tr>`;
  }

  render();
  window.commandBus.addEventListener('signals', render);
}

window.SignalSourceComponent = { mount };
