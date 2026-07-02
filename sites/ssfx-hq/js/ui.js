/**
 * SSFX HQ — UI utilities, formatters, and toast notifications.
 */
window.UI = (function () {
  function esc(str) {
    if (str == null) return '';
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#039;');
  }

  function toast(type, message, title) {
    const container = document.getElementById('toast-container');
    if (!container) return;
    const el = document.createElement('div');
    el.className = `toast ${type}`;
    el.innerHTML = `
      <div class="toast-title">${esc(title || type)}</div>
      <div class="toast-message">${esc(message)}</div>
    `;
    container.appendChild(el);
    setTimeout(() => {
      el.style.opacity = '0';
      el.style.transform = 'translateX(20px)';
      el.style.transition = 'opacity 200ms, transform 200ms';
      setTimeout(() => el.remove(), 220);
    }, 4000);
  }

  function formatNumber(n, digits = 2) {
    if (n == null || Number.isNaN(Number(n))) return '—';
    return Number(n).toLocaleString('en-US', {
      minimumFractionDigits: digits,
      maximumFractionDigits: digits,
    });
  }

  function formatPrice(n, digits = 2) {
    return formatNumber(n, digits);
  }

  function formatPct(n, digits = 1) {
    if (n == null || Number.isNaN(Number(n))) return '—';
    const sign = Number(n) > 0 ? '+' : '';
    return `${sign}${formatNumber(n, digits)}%`;
  }

  function formatTime(iso) {
    if (!iso) return '—';
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return String(iso);
    return d.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
  }

  function formatDateTime(iso) {
    if (!iso) return '—';
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return String(iso);
    return d.toLocaleString('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
  }

  function setLoading(isLoading) {
    const app = document.getElementById('app');
    if (!app) return;
    if (isLoading && !app.querySelector('.loading-screen')) {
      app.innerHTML = `
        <div class="loading-screen">
          <div class="loading-spinner"></div>
          <p>Loading...</p>
        </div>
      `;
    } else if (!isLoading && app.querySelector('.loading-screen')) {
      app.innerHTML = '';
    }
  }

  function badge(status) {
    const map = {
      executed: 'badge-green',
      running: 'badge-green',
      connected: 'badge-teal',
      enabled: 'badge-teal',
      online: 'badge-teal',
      failed: 'badge-red',
      error: 'badge-red',
      rejected: 'badge-red',
      skipped: 'badge-amber',
      stopped: 'badge-amber',
      pending: 'badge-amber',
      disabled: 'badge-red',
      demo: 'badge-coral',
      live: 'badge-coral',
    };
    return map[String(status).toLowerCase()] || 'badge-ghost';
  }

  return {
    esc,
    toast,
    formatNumber,
    formatPrice,
    formatPct,
    formatTime,
    formatDateTime,
    setLoading,
    badge,
  };
})();
