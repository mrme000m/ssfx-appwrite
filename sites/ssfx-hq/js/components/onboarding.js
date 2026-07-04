/**
 * SSFX HQ — Onboarding page (set username/PIN after OAuth).
 */
window.OnboardingComponent = (function () {
  function getQueryParams() {
    const hash = window.location.hash.replace(/^#/, '');
    const qs = hash.split('?')[1] || '';
    const params = {};
    new URLSearchParams(qs).forEach((v, k) => params[k] = v);
    return params;
  }

  function mount(container) {
    const params = getQueryParams();

    // Onboarding is for authenticated users only (to set/update credentials after login).
    if (!window.Auth.isAuthenticated()) {
      container.innerHTML = `
        <div class="public-page">
          <div class="public-card">
            <div class="public-brand">
              <h1>Authentication Required</h1>
              <p>Please login or create an account first before setting credentials.</p>
            </div>
            <a href="#/login" class="btn btn-primary w-full" style="margin-top:16px">Login</a>
            <a href="#/register" class="btn btn-ghost w-full" style="margin-top:12px">Create Account</a>
          </div>
        </div>
      `;
      return;
    }

    if (params.success === 'false') {
      container.innerHTML = `
        <div class="public-page">
          <div class="public-card">
            <div class="public-brand">
              <h1>Connection Failed</h1>
              <p>${window.UI.esc(params.error || 'We could not connect your cTrader account. Please try again.')}</p>
            </div>
            <a href="#/" class="btn btn-primary w-full" style="margin-top:16px">Try Again</a>
          </div>
        </div>
      `;
      return;
    }

    const grantId = params.grant_id || '';
    container.innerHTML = `
      <div class="public-page">
        <div class="public-card">
          <div class="public-brand">
            <h1>Set Credentials</h1>
            <p>Create or update your username and PIN.</p>
          </div>
          ${grantId ? `<div class="alert alert-info" style="margin-bottom:16px">cTrader account linked. Grant ID: <code>${window.UI.esc(grantId)}</code></div>` : ''}
          <form id="onboarding-form">
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
            <div id="onboarding-error" class="alert alert-error" style="display:none;margin-top:16px"></div>
            <button type="submit" class="btn btn-primary w-full" id="onboarding-submit" style="margin-top:16px">Save &amp; Continue</button>
          </form>
        </div>
      </div>
    `;

    const digits = container.querySelectorAll('.pin-digit');
    const pinField = container.querySelector('#pin-field');
    const submitBtn = container.querySelector('#onboarding-submit');
    const errorBox = container.querySelector('#onboarding-error');

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

    function showError(msg) {
      errorBox.textContent = msg;
      errorBox.style.display = 'block';
    }

    function clearError() {
      errorBox.style.display = 'none';
      errorBox.textContent = '';
    }

    container.querySelector('#onboarding-form').addEventListener('submit', async (ev) => {
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
      if (!username) {
        window.UI.showInlineError(usernameInput, 'Username is required');
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
      submitBtn.textContent = 'Saving...';

      try {
        await window.Auth.setCredentials(username, pin);
        await window.Auth.refresh();
        window.commandBus.dispatchEvent(new CustomEvent('auth-changed'));
        window.Router.navigate('/dashboard');
      } catch (err) {
        showError(err.message || 'Failed to save credentials');
        digits.forEach((d) => (d.value = ''));
        pinField.value = '';
        digits[0].focus();
      } finally {
        submitBtn.disabled = false;
        submitBtn.textContent = 'Save & Continue';
      }
    });
  }

  return { mount };
})();
