/**
 * SSFX HQ — Agent pipeline visualization + reasoning (master only).
 */
window.AgentPipelineComponent = (function () {
  async function runIntent(signalText) {
    const payload = {
      raw_text: signalText,
      message_id: Date.now(),
      chat_id: 'manual',
      recent_messages: [],
      open_positions: [],
    };
    try {
      return await window.API.AgentAPI.signalIntent(payload);
    } catch (err) {
      window.UI.toast('error', `Intent agent failed: ${err.message}`);
      return null;
    }
  }

  function renderLog(log) {
    const signal = log.signal || {};
    const agents = log.agents || [];
    return `
      <div class="card mb-4">
        <div class="card-header">
          <div>
            <span class="badge ${signal.direction === 'BUY' ? 'badge-teal' : 'badge-red'}">${window.UI.esc(signal.direction)}</span>
            <span class="mono text-sm ml-2">${window.UI.esc(signal.symbol)}</span>
            <span class="text-dim text-sm ml-2">@${window.UI.formatDateTime(signal.timestamp_ms)}</span>
          </div>
          <span class="badge ${window.UI.badge(signal.status)}">${window.UI.esc(signal.status)}</span>
        </div>
        <p class="text-dim text-sm mb-4">${window.UI.esc(signal.raw_text)}</p>
        <div class="pipeline-flow" style="padding:0;justify-content:flex-start">
          ${agents.map((a) => `
            <div class="pipeline-node">
              <div class="pipeline-icon done">${a.agent[0].toUpperCase()}</div>
              <div class="pipeline-label">${window.UI.esc(a.agent)}</div>
            </div>
            <div class="pipeline-connector"></div>
          `).join('')}
          <div class="pipeline-node">
            <div class="pipeline-icon done">✓</div>
            <div class="pipeline-label">done</div>
          </div>
        </div>
        <div class="reasoning-card mt-4">
          <div class="text-xs text-dim mb-2">PARSER · ${window.UI.esc(signal.parser_used)} · confidence ${window.UI.formatNumber(signal.parse_confidence)}</div>
          <pre>${window.UI.esc(signal.llm_reasoning || 'No reasoning recorded.')}</pre>
        </div>
      </div>
    `;
  }

  function mount(container) {
    const section = document.createElement('section');
    section.innerHTML = `
      <div class="page-header">
        <h1>Agent Pipeline <span class="page-subtitle">intent · entry · lifecycle</span></h1>
      </div>

      <div class="card mb-6">
        <h3 class="mb-4">Test Intent Agent</h3>
        <div class="flex gap-3">
          <input id="intent-input" class="form-input w-full" placeholder="BUY XAUUSD @ 2350.50 SL 2345 TP 2360" />
          <button id="intent-run" class="btn btn-primary">Run</button>
        </div>
        <pre id="intent-output" class="mono text-xs text-dim mt-4" style="min-height:60px;background:var(--bg-ink);padding:12px;border-radius:var(--radius-md)"></pre>
      </div>

      <div id="agent-logs"></div>
    `;
    container.appendChild(section);

    const intentInput = section.querySelector('#intent-input');
    section.querySelector('#intent-run').addEventListener('click', async () => {
      const text = intentInput.value.trim();
      if (!text) return;
      const result = await runIntent(text);
      section.querySelector('#intent-output').textContent = result
        ? JSON.stringify(result, null, 2)
        : 'No response';
    });

    function render() {
      const logs = window.appState.agentLogs || [];
      const out = section.querySelector('#agent-logs');
      if (logs.length === 0) {
        out.innerHTML = `
          <div class="empty-state">
            <div class="empty-title">No agent logs yet</div>
            <p class="empty-desc">Agent pipeline logs are synthesized from recent signals and executions.</p>
          </div>`;
        return;
      }
      out.innerHTML = logs.map(renderLog).join('');
    }

    render();
    window.commandBus.addEventListener('agent-logs', render);
  }

  return { mount };
})();
