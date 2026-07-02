/**
 * SSFX HQ — PIN login page.
 */
window.LoginComponent = (function () {
  function mount(container) {
    container.innerHTML = `
      <div class="public-page">
        <div class="public-card">
          <div class="public-brand">
            <h1>Enter PIN</h1>
            <p>Authenticate to access your command deck.</p>
          </div>
          <form id="login-form" autocomplete="off">
            <div class="form-group" style="margin-bottom:16px">
              <label class="form-label">Username</label>
              <input name="username" class="form-input" placeholder="username" required autofocus />
            </div>
            <label class="form-label">PIN</label>
            <div class="pin-input" id="pin-input">
              <input type="password" inputmode="numeric" maxlength="1" class="pin-digit" />
              <input type="password" inputmode="numeric" maxlength="1" class="pin-digit" />
              <input type="password" inputmode="numeric" maxlength="1" class="pin-digit" />
              <input type="password" inputmode="numeric" maxlength="1" class="pin-digit" />
              <input type="password" inputmode="numeric" maxlength="1" class="pin-digit" />
              <input type="password" inputmode="numeric" maxlength="1" class="pin-digit" />
            </div>
            <input type="hidden" name="pin" id="pin-field" />
            <button type="submit" class="btn btn-primary w-full" style="margin-top:16px">Unlock</button>
          </form>
          <div class="text-center mt-4">
            <a href="#/reset" class="text-sm text-dim">Forgot PIN?</a>
          </div>
        </div>
      </div>
    `;

    const digits = container.querySelectorAll('.pin-digit');
    const pinField = container.querySelector('#pin-field');

    digits.forEach((input, idx) => {
      input.addEventListener('input', (ev) => {
        const val = ev.target.value.replace(/\D/g, '');
        ev.target.value = val;
        const pin = Array.from(digits).map((d) => d.value).join('');
        pinField.value = pin;
        if (val && idx < digits.length - 1) {
          digits[idx + 1].focus();
        }
        if (pin.length === digits.length) {
          container.querySelector('#login-form').dispatchEvent(new Event('submit'));
        }
      });
      input.addEventListener('keydown', (ev) => {
        if (ev.key === 'Backspace' && !input.value && idx > 0) {
          digits[idx - 1].focus();
        }
      });
    });

    container.querySelector('#login-form').addEventListener('submit', async (ev) => {
      ev.preventDefault();
      const fd = new FormData(ev.target);
      const username = fd.get('username');
      const pin = fd.get('pin');
      if (!username || pin.length !== 6) {
        window.UI.toast('error', 'Enter username and 6-digit PIN.');
        return;
      }
      try {
        await window.Auth.login(username, pin);
        window.commandBus.dispatchEvent(new CustomEvent('auth-changed'));
        window.Router.navigate('/dashboard');
      } catch (err) {
        window.UI.toast('error', err.message || 'Login failed');
        digits.forEach((d) => (d.value = ''));
        pinField.value = '';
        digits[0].focus();
      }
    });
  }

  return { mount };
})();
