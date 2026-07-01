import { UI } from './ui.js';
import { API } from './api.js';
import { Auth } from './auth.js';

window.commandState = {
  user: null,
  online: false,
  accounts: [],
  signals: [],
  agentLogs: [],
  health: {},
};

window.commandBus = new EventTarget();

const ROUTES = [
  { id: 'fleet', label: 'Slave Fleet', path: '/fleet' },
  { id: 'signals', label: 'Signal Source', path: '/signals' },
  { id: 'pipeline', label: 'Agent Pipeline', path: '/pipeline' },
  { id: 'inject', label: 'Inject Signal', path: '/inject' },
  { id: 'presets', label: 'Presets', path: '/presets' },
  { id: 'terminal', label: 'Terminal', path: '/terminal' },
  { id: 'market', label: 'Market Context', path: '/market' },
];

function getRoute() {
  return (location.hash.replace('#', '') || '/fleet').split('?')[0];
}

function renderNav() {
  const nav = document.getElementById('main-nav');
  if (!nav) return;
  nav.innerHTML = '';
  const current = getRoute();
  for (const r of ROUTES) {
    const a = document.createElement('a');
    a.className = `nav-link ${current === r.path ? 'active' : ''}`;
    a.href = `#${r.path}`;
    a.textContent = r.label;
    nav.appendChild(a);
  }
}

function renderLoginGate() {
  const stage = document.getElementById('main-stage');
  stage.innerHTML = '';
  const gate = UI.el('div', 'login-gate glass-panel section');
  gate.innerHTML = `
    <h2>AUTHORIZATION REQUIRED</h2>
    <p style="color:var(--text-dim);text-align:center;max-width:360px;">
      This console is restricted to master operators. Sign in via the cTrader auth site, then return here.
    </p>
    <a class="btn" href="https://app.mrme.tech/#/login?redirect=https://command.mrme.tech">Sign In</a>
  `;
  stage.appendChild(gate);
}

async function loadRoute() {
  renderNav();
  const stage = document.getElementById('main-stage');
  stage.innerHTML = '';
  const route = getRoute();

  switch (route) {
    case '/signals': {
      const mod = await import('./components/signal-source.js');
      mod.mount(stage);
      break;
    }
    case '/pipeline': {
      const mod = await import('./components/agent-pipeline.js');
      mod.mount(stage);
      break;
    }
    case '/fleet': {
      const mod = await import('./components/slave-fleet.js');
      mod.mount(stage);
      break;
    }
    case '/presets': {
      const mod = await import('./components/presets.js');
      mod.mount(stage);
      break;
    }
    case '/inject': {
      const mod = await import('./components/signal-injector.js');
      mod.mount(stage);
      break;
    }
    case '/terminal': {
      const mod = await import('./components/terminal.js');
      mod.mount(stage);
      break;
    }
    case '/market': {
      const mod = await import('./components/market-context.js');
      mod.mount(stage);
      break;
    }
    default: {
      const mod = await import('./components/slave-fleet.js');
      mod.mount(stage);
    }
  }
}

async function refreshGlobalState() {
  try {
    const health = await API.health();
    window.commandState.online = true;
    window.commandState.health = health;
    UI.setConnection('online');
  } catch (e) {
    window.commandState.online = false;
    UI.setConnection('offline');
  }
}

async function refreshAccounts() {
  if (!window.commandState.online) return;
  try {
    window.commandState.accounts = await API.listAccounts();
    window.commandBus.dispatchEvent(new CustomEvent('accounts'));
  } catch (e) {
    console.warn('accounts refresh failed', e);
  }
}

async function refreshSignals() {
  if (!window.commandState.online) return;
  try {
    window.commandState.signals = await API.listSignals(30);
    window.commandBus.dispatchEvent(new CustomEvent('signals'));
  } catch (e) {
    console.warn('signals refresh failed', e);
  }
}

async function refreshAgentLogs() {
  if (!window.commandState.online) return;
  try {
    window.commandState.agentLogs = await API.agentLogs(20);
    window.commandBus.dispatchEvent(new CustomEvent('agent-logs'));
  } catch (e) {
    console.warn('agent logs refresh failed', e);
  }
}

async function boot() {
  UI.showLoading('Authenticating...');
  Auth.init();
  const session = await Auth.checkSession();
  UI.hideLoading();

  if (!session.ok || !session.isMaster) {
    renderLoginGate();
    return;
  }

  window.commandState.user = session.user;
  window.addEventListener('hashchange', loadRoute);
  await refreshGlobalState();
  await Promise.all([refreshAccounts(), refreshSignals(), refreshAgentLogs()]);
  await loadRoute();

  setInterval(refreshGlobalState, 10000);
  setInterval(refreshAccounts, 10000);
  setInterval(refreshSignals, 5000);
  setInterval(refreshAgentLogs, 10000);
}

boot().catch((err) => {
  UI.hideLoading();
  UI.toast('error', `Boot failed: ${err.message}`);
  console.error(err);
});
