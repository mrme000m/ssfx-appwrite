/**
 * SSFX HQ — Public landing page.
 */
window.LandingComponent = (function () {
  function mount(container) {
    const authenticated = window.Auth.isAuthenticated();
    const cta = authenticated
      ? `<a class="btn btn-lg btn-primary" href="#/dashboard">Open Command Deck</a>
         <a class="btn btn-lg btn-ghost" href="#/login">Switch Account</a>`
      : `<a class="btn btn-lg btn-primary" href="#/login">Login with PIN</a>
         <a class="btn btn-lg btn-ghost" href="${window.API.CFG.authDomain}/auth/ctrader/start">Connect cTrader</a>`;

    container.innerHTML = `
      <div class="public-page">
        <div class="public-card" style="text-align:center">
          <div class="public-brand">
            <h1>SSFX Command Deck</h1>
            <p>cTrader copy-trading control surface for slaves and masters.</p>
          </div>
          <div style="display:flex;gap:12px;justify-content:center;flex-wrap:wrap">
            ${cta}
          </div>
        </div>
      </div>
    `;
  }

  return { mount };
})();
