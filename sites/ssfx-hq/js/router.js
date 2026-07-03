/**
 * SSFX HQ — Hash router with role-based route guards.
 */
window.Router = (function () {
  const routes = {
    '/': { component: 'LandingComponent', public: true },
    '/login': { component: 'LoginComponent', public: true },
    '/reset': { component: 'ResetComponent', public: true },
    '/onboarding': { component: 'OnboardingComponent', public: true },
    '/dashboard': { component: 'DashboardComponent' },
    '/trade-config': { component: 'TradeConfigComponent', slave: true },
    '/market': { component: 'MarketComponent', master: true },
    '/fleet': { component: 'FleetComponent', master: true },
    '/signals': { component: 'SignalsComponent', master: true },
    '/pipeline': { component: 'AgentPipelineComponent', master: true },
    '/inject': { component: 'InjectorComponent', master: true },
    '/terminal': { component: 'TerminalComponent', master: true },
  };

  function getPath() {
    return window.location.hash.replace(/^#/, '') || '/';
  }

  function navigate(path, replace = false) {
    const target = path.startsWith('/') ? path : `/${path}`;
    if (replace) {
      window.location.replace(`#${target}`);
    } else {
      window.location.hash = target;
    }
  }

  function renderShell() {
    const app = document.getElementById('app');
    if (!app) return;
    const authenticated = window.Auth.isAuthenticated();
    const master = window.Auth.isMaster();
    const onlineClass = window.appState.online ? 'online' : window.appState.initialized ? 'error' : '';

    const masterNav = master ? `
      <a class="nav-item" href="#/dashboard" data-route="/dashboard">Dashboard</a>
      <a class="nav-item" href="#/fleet" data-route="/fleet">Fleet</a>
      <a class="nav-item" href="#/signals" data-route="/signals">Signals</a>
      <a class="nav-item" href="#/pipeline" data-route="/pipeline">Agents</a>
      <a class="nav-item" href="#/market" data-route="/market">Market</a>
      <a class="nav-item" href="#/inject" data-route="/inject">Inject</a>
      <a class="nav-item" href="#/terminal" data-route="/terminal">Terminal</a>
    ` : '';

    const slaveNav = authenticated && !master ? `
      <a class="nav-item" href="#/dashboard" data-route="/dashboard">Dashboard</a>
      <a class="nav-item" href="#/trade-config" data-route="/trade-config">Trade Config</a>
    ` : '';

    const role = window.appState.role || 'slave';
    const isMaster = window.Auth.isMaster();
    const isAdmin = window.Auth.isAdmin();
    const roleClass = isAdmin ? 'admin' : isMaster ? 'master' : 'slave';
    const roleLabel = isAdmin ? 'admin' : isMaster ? 'master' : role;
    const userBlock = authenticated ? `
      <div class="user-menu">
        <span class="topbar-role ${roleClass}">${roleLabel}</span>
        <span>${window.UI.esc(window.appState.username || window.appState.user?.email || 'User')}</span>
        ${isAdmin ? '<span class="admin-badge" title="Administrator">⭐</span>' : ''}
      </div>
      <button class="btn btn-sm btn-ghost" id="logout-btn">Logout</button>
    ` : `
      <a class="btn btn-sm btn-primary" href="#/login">Login</a>
    `;

    app.innerHTML = `
      <div class="app-shell">
        <header class="topbar">
          <a class="topbar-brand" href="#/">
            <div class="topbar-logo">⬡</div>
            <div class="topbar-title">SSFX <span>Command Deck</span></div>
          </a>
          <nav class="topbar-nav">${masterNav}${slaveNav}</nav>
          <div class="topbar-right">
            <span class="status-pill">
              <span class="status-dot ${onlineClass}"></span>
              ${window.appState.online ? 'Online' : 'Offline'}
            </span>
            ${userBlock}
          </div>
        </header>
        <main class="app-main" id="app-main"></main>
      </div>
    `;

    const logoutBtn = document.getElementById('logout-btn');
    if (logoutBtn) {
      logoutBtn.addEventListener('click', async () => {
        await window.Auth.logout();
        renderShell();
        navigate('/login');
      });
    }

    updateActiveNav();
  }

  function updateActiveNav() {
    const path = getPath();
    document.querySelectorAll('.nav-item').forEach((el) => {
      el.classList.toggle('active', el.dataset.route === path);
    });
  }

  function render() {
    const path = getPath();
    const route = routes[path] || routes['/'];
    const authenticated = window.Auth.isAuthenticated();

    if (!route.public && !authenticated) {
      navigate('/login', true);
      return;
    }
    if (route.master && !window.Auth.isMaster()) {
      navigate('/dashboard', true);
      return;
    }

    if (route.public && authenticated && path !== '/onboarding') {
      navigate('/dashboard', true);
      return;
    }

    const main = document.getElementById('app-main');
    if (!main) {
      renderShell();
      setTimeout(render, 0);
      return;
    }

    main.innerHTML = '';
    updateActiveNav();

    const Component = window[route.component];
    if (Component && typeof Component.mount === 'function') {
      try {
        Component.mount(main);
      } catch (err) {
        main.innerHTML = `<div class="empty-state">
          <div class="empty-title">Render error</div>
          <p class="empty-desc">${window.UI.esc(err.message)}</p>
        </div>`;
      }
    } else {
      main.innerHTML = `<div class="empty-state">
        <div class="empty-title">Not implemented</div>
        <p class="empty-desc">The ${window.UI.esc(path)} screen is under construction.</p>
      </div>`;
    }
  }

  function init() {
    window.addEventListener('hashchange', render);
  }

  return {
    init,
    render,
    renderShell,
    navigate,
    getPath,
  };
})();
