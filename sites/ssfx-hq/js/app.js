/**
 * SSFX HQ — Boot sequence and global polling loop.
 */
(function () {
  let timers = {};

  async function refreshSession() {
    try {
      await window.Auth.checkSession();
      if (!window.Auth.isAuthenticated() && window.Router.getPath() !== '/login') {
        window.Router.navigate('/login');
      }
    } catch (err) {
      // ignore
    }
  }

  async function refreshAccounts() {
    try {
      const data = await window.API.V2API.listAccounts();
      window.appState.fleetAccounts = data || [];
      window.commandBus.dispatchEvent(new CustomEvent('accounts'));
    } catch (err) {
      window.appState.online = false;
    }
  }

  async function refreshSignals() {
    try {
      const data = await window.API.V2API.listSignals(50);
      window.appState.signals = data || [];
      window.commandBus.dispatchEvent(new CustomEvent('signals'));
    } catch (err) {
      // ignore
    }
  }

  async function refreshAgentLogs() {
    try {
      const data = await window.API.V2API.agentLogs(50);
      window.appState.agentLogs = data || [];
      window.commandBus.dispatchEvent(new CustomEvent('agent-logs'));
    } catch (err) {
      // ignore
    }
  }

  async function refreshHealth() {
    try {
      await window.API.V2API.health();
      window.appState.online = true;
    } catch (err) {
      window.appState.online = false;
    }
    try {
      await window.API.AgentAPI.health();
      window.appState.agentOnline = true;
    } catch (err) {
      window.appState.agentOnline = false;
    }
    window.commandBus.dispatchEvent(new CustomEvent('health'));
  }

  async function refreshAll() {
    await Promise.all([
      refreshHealth(),
      refreshAccounts().catch(() => {}),
      refreshSignals().catch(() => {}),
      refreshAgentLogs().catch(() => {}),
    ]);
  }

  function startPolling() {
    if (timers.health) return;
    timers.health = setInterval(refreshHealth, 10000);
    timers.session = setInterval(refreshSession, 30000);
    timers.accounts = setInterval(refreshAccounts, 10000);
    timers.signals = setInterval(refreshSignals, 10000);
    timers.agentLogs = setInterval(refreshAgentLogs, 10000);
  }

  function stopPolling() {
    Object.values(timers).forEach(clearInterval);
    timers = {};
  }

  window.refreshAccounts = refreshAccounts;
  window.refreshSignals = refreshSignals;
  window.refreshAgentLogs = refreshAgentLogs;
  window.refreshHealth = refreshHealth;

  async function boot() {
    window.Auth.init();
    window.Router.init();

    const session = await window.Auth.checkSession();
    window.appState.initialized = true;

    if (session) {
      window.Router.renderShell();
      await refreshAll();
      startPolling();
      window.Router.render();
    } else {
      window.Router.renderShell();
      window.Router.render();
    }

    // Restart polling when user logs in
    window.commandBus.addEventListener('auth-changed', () => {
      if (window.Auth.isAuthenticated()) {
        startPolling();
      } else {
        stopPolling();
      }
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
})();
