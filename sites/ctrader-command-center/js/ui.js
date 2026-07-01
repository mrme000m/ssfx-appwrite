export const UI = {
  esc(text) {
    const div = document.createElement('div');
    div.textContent = text ?? '';
    return div.innerHTML;
  },

  fmtTime(ms) {
    if (!ms) return '--';
    const d = new Date(Number(ms));
    return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
  },

  fmtDate(ms) {
    if (!ms) return '--';
    const d = new Date(Number(ms));
    return d.toLocaleString();
  },

  fmtNumber(n, digits = 2) {
    if (n === null || n === undefined || Number.isNaN(n)) return '--';
    return Number(n).toFixed(digits);
  },

  toast(type, message, duration = 3000) {
    const container = document.getElementById('toast-container');
    if (!container) return;
    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;
    const icons = { success: '✓', error: '✗', warn: '⚠', info: 'ℹ' };
    toast.innerHTML = `<span style="font-weight:700">${icons[type] || ''}</span> ${UI.esc(message)}`;
    container.appendChild(toast);
    setTimeout(() => {
      toast.classList.add('removing');
      setTimeout(() => toast.remove(), 220);
    }, duration);
  },

  el(tag, classes = '', children = []) {
    const node = document.createElement(tag);
    if (classes) node.className = classes;
    for (const child of children) {
      if (typeof child === 'string') node.innerHTML += child;
      else if (child) node.appendChild(child);
    }
    return node;
  },

  setConnection(status) {
    const dot = document.getElementById('connection-dot');
    const text = document.getElementById('connection-text');
    if (!dot || !text) return;
    dot.className = 'dot';
    if (status === 'online') { dot.classList.add('online'); text.textContent = 'ONLINE'; }
    else if (status === 'warn') { dot.classList.add('warn'); text.textContent = 'DEGRADED'; }
    else { dot.classList.add('offline'); text.textContent = 'OFFLINE'; }
  },

  showLoading(text = 'Initializing...') {
    let overlay = document.getElementById('loading-overlay');
    if (!overlay) {
      overlay = document.createElement('div');
      overlay.id = 'loading-overlay';
      overlay.className = 'loading-overlay';
      document.body.appendChild(overlay);
    }
    overlay.innerHTML = `<div class="spinner"></div><div>${UI.esc(text)}</div>`;
  },

  hideLoading() {
    const overlay = document.getElementById('loading-overlay');
    if (overlay) overlay.remove();
  },
};

window.UI = UI;
