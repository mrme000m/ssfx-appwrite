import { UI } from '../ui.js';
import { API } from '../api.js';

function node(label, status) {
  return UI.el('div', `node ${status}`, [label]);
}

export function mount(container) {
  const section = UI.el('section', 'section glass-panel');
  section.innerHTML = `
    <div class="section-header">
      <h2 class="section-title">Agent Pipeline</h2>
      <span class="section-sub">Signal flow: parser → risk → router → executor</span>
    </div>
    <div id="pipeline-wrap" class="pipeline"></div>
    <div id="pipeline-detail" style="margin-top:16px;color:var(--text-dim);font-size:12px;"></div>
  `;
  container.appendChild(section);

  async function render() {
    const wrap = section.querySelector('#pipeline-wrap');
    const detail = section.querySelector('#pipeline-detail');
    const signal = (window.commandState.signals || [])[0];
    if (!signal) {
      wrap.innerHTML = '';
      wrap.appendChild(node('WAITING', 'active'));
      detail.textContent = 'No signal in the pipeline.';
      return;
    }

    let executions = [];
    try {
      executions = await API.signalExecutions(signal.chat_id, signal.message_id);
    } catch (e) {
      console.warn('pipeline executions failed', e);
    }

    const executed = executions.filter(e => e.status === 'executed').length;
    const failed = executions.filter(e => ['failed', 'rejected'].includes(e.status)).length;
    const skipped = executions.filter(e => e.status === 'skipped').length;

    const parserStatus = signal.parser_used === 'manual' ? 'done' : 'done';
    const riskStatus = signal.parse_confidence >= 0.75 ? 'done' : 'warn';
    const routerStatus = 'done';
    const executorStatus = failed > 0 ? 'warn' : executed > 0 ? 'done' : 'active';

    wrap.innerHTML = '';
    wrap.appendChild(node('PARSER', parserStatus));
    wrap.appendChild(node('RISK', riskStatus));
    wrap.appendChild(node('ROUTER', routerStatus));
    wrap.appendChild(node('EXECUTOR', executorStatus));

    detail.innerHTML = `
      Latest signal: <strong>${UI.esc(signal.signal_type)} ${UI.esc(signal.direction)} ${UI.esc(signal.symbol)} @ ${UI.fmtNumber(signal.entry_price)}</strong> ·
      ${executions.length} follower execution(s) · ${executed} executed · ${failed} failed · ${skipped} skipped
    `;
  }

  render();
  window.commandBus.addEventListener('signals', render);
  window.commandBus.addEventListener('accounts', render);
}

window.AgentPipelineComponent = { mount };
