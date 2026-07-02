/**
 * SSFX HQ — Onboarding page (set username/PIN after OAuth).
 */
window.OnboardingComponent = (function () {
  function mount(container) {
    container.innerHTML = `
      <div class="public-page">
        <div class="public-card">
          <div class="public-brand">
            <h1>Set Credentials</h1>
            <p>Create a username and 6-digit PIN to access your deck.</p>
          </div>
          <form id="onboarding-form">
            <div class="form-group" style="margin-bottom:16px">
              <label class="form-label">Username</label>
              <input name="username" class="form-input" placeholder="username" required autofocus />
            </div>
            <label class="form-label">PIN (6 digits)</label>
            <div class="pin-input" id="pin-input">
              <input type="password" inputmode="numeric" maxlength="1" class="pin-digit" />
              <input type="password" inputmode="numeric" maxlength="1" class="pin-digit" />
              <input type="password" inputmode="numeric" maxlength="1" class="pin-digit" />
              <input type="password" inputmode="numeric" maxlength="1" class="pin-digit" />
              <input type="password" inputmode="numeric" maxlength="1" class="pin-digit" />
              <input type="password" inputmode="numeric" maxlength="1" class="pin-digit" />
            </div>
            <input type="hidden" name="pin" id="pin-field" />
            <button type="submit" class="btn btn-primary w-full" style="margin-top:16px">Save & Continue</button>
          </form>
        </div>
      </div>
    `;

    const digits = container.querySelectorAll('.pin-digit');
    const pinField = container.querySelector('#pin-field');

    digits.forEach((input, idx) => {
      input.addEventListener('input', (ev) => {
        const val = ev.target.value.replace(/\D/g, '');
        ev.target.value = val;
        pinField.value = Array.from(digits).map((d) => d.value).join('');
        if (val && idx < digits.length - 1) digits[idx + 1].focus();
      });
      input.addEventListener('keydown', (ev) => {
        if (ev.key === 'Backspace' && !input.value && idx > 0) digits[idx - 1].focus();
      });
    });

    container.querySelector('#onboarding-form').addEventListener('submit', async (ev) => {
      ev.preventDefault();
      const fd = new FormData(ev.target);
      const username = fd.get('username');
      const pin = fd.get('pin');
      if (!username || pin.length !== 6) {
        window.UI.toast('error', 'Username and 6-digit PIN required.');
        return;
      }
      try {
        await window.Auth.setCredentials({ username, pin });
        await window.Auth.checkSession();
        window.commandBus.dispatchEvent(new CustomEvent('auth-changed'));
        window.Router.navigate('/dashboard');
      } catch (err) {
        window.UI.toast('error', err.message || 'Failed to save credentials');
      }
    });
  }

  return { mount };
})();
