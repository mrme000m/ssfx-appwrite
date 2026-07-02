/**
 * SSFX HQ — Live execution terminal via SSE (master only).
 */
window.TerminalComponent = (function () {
  let eventSource = null;

  function renderLine(trade) {
    const result = trade.result || {};
    const signal = trade.signal || {};
    const status = result.status || 'unknown';
    return `
      <div class="terminal-line">
        <span class="terminal-time">${window.UI.formatTime(trade.updated_at)}</span>
        <span class="terminal-tag ${status}">${window.UI.esc(status)}</span>
        <span class="terminal-message">
          ${window.UI.esc(signal.symbol || '—')} ${window.UI.esc(signal.direction || '')} ·
          ${window.UI.esc(signal.key || trade.signal_key || '')}
        </span>
      </div>
    `;
  }

  function mount(container) {
    const section = document.createElement('section');
    section.innerHTML = `
      <div class="page-header">
        <h1>Terminal <span class="page-subtitle">live execution stream</span></h1>
        <div class="page-actions">
          <button id="term-clear" class="btn btn-sm btn-ghost">Clear</button>
        </div>
      </div>
      <div class="terminal">
        <div class="terminal-header">
          <span class="mono text-xs text-dim">SSE /api/executions/stream</span>
          <span id="term-status" class="badge badge-amber">connecting</span>
        </div>
        <div class="terminal-body" id="terminal-body"></div>
      </div>
    `;
    container.appendChild(section);

    const body = section.querySelector('#terminal-body');
    const status = section.querySelector('#term-status');
    const lines = [];

    function appendLine(html) {
      lines.push(html);
      if (lines.length > 200) lines.shift();
      body.innerHTML = lines.join('');
      body.scrollTop = body.scrollHeight;
    }

    // Fetch recent executions first
    window.API.V2API.listExecutions(50).then((trades) => {
      (trades || []).reverse().forEach((t) => appendLine(renderLine(t)));
    });

    // Open SSE
    try {
      let url = `${window.API.CFG.v2ApiBase}/api/executions/stream`;
      if (window.API.CFG.v2AdminKey) {
        const sep = url.includes('?') ? '&' : '?';
        url += `${sep}admin_key=${encodeURIComponent(window.API.CFG.v2AdminKey)}`;
      }
      eventSource = new EventSource(url, { withCredentials: true });
      eventSource.onopen = () => {
        status.className = 'badge badge-teal';
        status.textContent = 'live';
      };
      eventSource.onmessage = (ev) => {
        try {
          const trade = JSON.parse(ev.data);
          appendLine(renderLine(trade));
        } catch (err) {
          appendLine(`<div class="terminal-line">
            <span class="terminal-time">${window.UI.formatTime(new Date().toISOString())}</span>
            <span class="terminal-tag error">parse</span>
            <span class="terminal-message">Failed to parse SSE payload</span>
          </div>`);
        }
      };
      eventSource.onerror = () => {
        status.className = 'badge badge-red';
        status.textContent = 'error';
      };
    } catch (err) {
      status.className = 'badge badge-red';
      status.textContent = 'failed';
      appendLine(`<div class="terminal-line">
        <span class="terminal-time">${window.UI.formatTime(new Date().toISOString())}</span>
        <span class="terminal-tag error">ssse</span>
        <span class="terminal-message">${window.UI.esc(err.message)}</span>
      </div>`);
    }

    section.querySelector('#term-clear').addEventListener('click', () => {
      lines.length = 0;
      body.innerHTML = '';
    });

    // Cleanup when component unmounts (hashchange will replace innerHTML anyway)
    window.addEventListener('hashchange', () => {
      if (eventSource) {
        eventSource.close();
        eventSource = null;
      }
    }, { once: true });
  }

  return { mount };
})();
