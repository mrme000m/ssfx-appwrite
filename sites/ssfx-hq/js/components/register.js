/**
 * SSFX HQ — Registration page (create account before linking cTrader).
 */
window.RegisterComponent = (function () {
  function mount(container) {
    container.innerHTML = `
      <div class="public-page">
        <div class="public-card">
          <div class="public-brand">
            <h1>Create Account</h1>
            <p>Create a username and PIN. You will link cTrader after logging in.</p>
          </div>
          <form id="register-form">
            <div class="form-group" style="margin-bottom:16px">
              <label class="form-label">Username</label>
              <input name="username" class="form-input" placeholder="username" required autofocus />
            </div>
            <label class="form-label">PIN (4-6 digits)</label>
            <div class="pin-input" id="pin-input">
              <input type="password" inputmode="numeric" maxlength="1" class="pin-digit" />
              <input type="password" inputmode="numeric" maxlength="1" class="pin-digit" />
              <input type="password" inputmode="numeric" maxlength="1" class="pin-digit" />
              <input type="password" inputmode="numeric" maxlength="1" class="pin-digit" />
              <input type="password" inputmode="numeric" maxlength="1" class="pin-digit" />
              <input type="password" inputmode="numeric" maxlength="1" class="pin-digit" />
            </div>
            <input type="hidden" name="pin" id="pin-field" />
            <div id="register-error" class="alert alert-error" style="display:none;margin-top:16px"></div>
            <button type="submit" class="btn btn-primary w-full" id="register-submit" style="margin-top:16px">Create Account</button>
          </form>
          <div class="text-center mt-4">
            <a href="#/login" class="text-sm text-dim">Already have an account? Login</a>
          </div>
        </div>
      </div>
    `;

    const digits = container.querySelectorAll('.pin-digit');
    const pinField = container.querySelector('#pin-field');
    const submitBtn = container.querySelector('#register-submit');
    const errorBox = container.querySelector('#register-error');

    function showError(msg) {
      errorBox.textContent = msg;
      errorBox.style.display = 'block';
    }

    function clearError() {
      errorBox.style.display = 'none';
      errorBox.textContent = '';
    }

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

    container.querySelector('#register-form').addEventListener('submit', async (ev) => {
      ev.preventDefault();
      const fd = new FormData(ev.target);
      const username = fd.get('username');
      const pin = fd.get('pin');

      clearError();

      const usernameInput = ev.target.elements.username;
      const pinInputs = container.querySelectorAll('.pin-digit');
      window.UI.clearInlineError(usernameInput);
      pinInputs.forEach(input => window.UI.clearInlineError(input));

      let isValid = true;
      if (!username || username.length < 3 || username.length > 32) {
        window.UI.showInlineError(usernameInput, 'Username must be 3-32 characters');
        isValid = false;
      }
      if (pin.length < 4 || pin.length > 6) {
        window.UI.showInlineError(pinInputs[0], 'PIN must be 4-6 digits');
        isValid = false;
      }
      if (!isValid) {
        return;
      }

      submitBtn.disabled = true;
      submitBtn.textContent = 'Creating account…';

      try {
        await window.API.AuthAPI.register({ username, pin });
        await window.Auth.refresh();
        window.commandBus.dispatchEvent(new CustomEvent('auth-changed'));
        window.Router.navigate('/dashboard');
      } catch (err) {
        showError(err.message || 'Registration failed');
        digits.forEach((d) => (d.value = ''));
        pinField.value = '';
        digits[0].focus();
      } finally {
        submitBtn.disabled = false;
        submitBtn.textContent = 'Create Account';
      }
    });
  }

  return { mount };
})();
