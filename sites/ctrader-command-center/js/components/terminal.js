import { UI } from '../ui.js';
import { API } from '../api.js';

function line(type, text) {
  const div = UI.el('div', `terminal-line terminal-${type}`);
  div.innerHTML = `<span class="terminal-time">${UI.esc(UI.fmtTime(Date.now()))}</span>${text}`;
  return div;
}

export async function mount(container) {
  const section = UI.el('section', 'section glass-panel');
  section.innerHTML = `
    <div class="section-header">
      <h2 class="section-title">Execution Terminal</h2>
      <span class="section-sub">Live execution stream</span>
    </div>
    <div id="terminal" class="terminal"></div>
  `;
  container.appendChild(section);
  const term = section.querySelector('#terminal');

  function append(type, text) {
    term.appendChild(line(type, text));
    term.scrollTop = term.scrollHeight;
  }

  try {
    const recent = await API.listExecutions(30);
    for (const ex of recent.reverse()) {
      const status = ex.status;
      const type = status === 'executed' ? 'success' : status === 'skipped' ? 'warn' : 'error';
      append(type, `[${UI.esc(ex.follower_id)}] ${UI.esc(ex.signal_type)} ${UI.esc(ex.status)} ${ex.error || ex.skip_reason || ''}`);
    }
  } catch (e) {
    append('warn', `Could not load recent executions: ${e.message}`);
  }

  append('info', 'Connecting to execution stream...');

  try {
    const es = API.executionsStream();
    es.onmessage = (ev) => {
      try {
        const data = JSON.parse(ev.data);
        const sig = data.signal || {};
        const result = data.result || {};
        const accepted = result.accepted;
        append(
          accepted ? 'success' : 'warn',
          `[STREAM] ${sig.signal_type || ''} ${sig.direction || ''} ${sig.symbol || ''} → ${accepted ? 'EXECUTED' : result.reason || 'REJECTED'}`
        );
      } catch (err) {
        append('info', ev.data);
      }
    };
    es.onerror = () => {
      append('warn', 'SSE connection error — retrying...');
    };
  } catch (err) {
    append('error', `Stream failed: ${err.message}`);
  }
}

window.TerminalComponent = { mount };
