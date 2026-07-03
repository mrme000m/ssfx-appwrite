/**
 * SSFX HQ — PIN reset page.
 */
window.ResetComponent = (function () {
  function mount(container) {
    container.innerHTML = `
      <div class="public-page">
        <div class="public-card">
          <div class="public-brand">
            <h1>Reset PIN</h1>
            <p>Request a reset token and set a new PIN.</p>
          </div>

          <form id="reset-request-form">
            <div class="form-group" style="margin-bottom:16px">
              <label class="form-label">Email</label>
              <input name="email" type="email" class="form-input" placeholder="your@email.com" required autofocus />
            </div>
            <div id="reset-req-error" class="alert alert-error" style="display:none;margin-bottom:12px"></div>
            <button type="submit" class="btn btn-primary w-full" id="reset-req-btn">Send Reset Token</button>
          </form>

          <form id="reset-confirm-form" style="display:none;margin-top:24px">
            <div class="alert alert-info" style="margin-bottom:16px">Check your email for a reset token.</div>
            <div class="form-group" style="margin-bottom:16px">
              <label class="form-label">Reset Token</label>
              <input name="token" class="form-input" placeholder="paste token here" required />
            </div>
            <div class="form-group" style="margin-bottom:16px">
              <label class="form-label">New PIN (6 digits)</label>
              <input name="pin" type="password" inputmode="numeric" maxlength="6" class="form-input" placeholder="000000" required />
            </div>
            <div id="reset-conf-error" class="alert alert-error" style="display:none;margin-bottom:12px"></div>
            <button type="submit" class="btn btn-primary w-full" id="reset-conf-btn">Set New PIN</button>
          </form>

          <div class="text-center mt-4">
            <a href="#/login" class="text-sm text-dim">Back to login</a>
          </div>
        </div>
      </div>
    `;

    const reqForm = container.querySelector('#reset-request-form');
    const confForm = container.querySelector('#reset-confirm-form');
    const reqBtn = container.querySelector('#reset-req-btn');
    const confBtn = container.querySelector('#reset-conf-btn');
    const reqError = container.querySelector('#reset-req-error');
    const confError = container.querySelector('#reset-conf-error');

    function showErr(el, msg) { el.textContent = msg; el.style.display = 'block'; }
    function clearErr(el) { el.style.display = 'none'; el.textContent = ''; }

    reqForm.addEventListener('submit', async (ev) => {
      ev.preventDefault();
      const fd = new FormData(ev.target);
      const email = fd.get('email');

      clearErr(reqError);

      const emailInput = ev.target.elements.email;
      window.UI.clearInlineError(emailInput);

      if (!email || !email.includes('@')) {
        window.UI.showInlineError(emailInput, 'Valid email is required');
        return;
      }

      reqBtn.disabled = true;
      reqBtn.textContent = 'Sending...';

      try {
        await window.API.AuthAPI.pinResetRequest({ email });
        window.UI.toast('success', 'Reset token sent if email matches.');
        reqForm.style.display = 'none';
        confForm.style.display = 'block';
        confForm.querySelector('[name="token"]').focus();
      } catch (err) {
        showErr(reqError, err.message || 'Failed to request reset');
      } finally {
        reqBtn.disabled = false;
        reqBtn.textContent = 'Send Reset Token';
      }
    });

    confForm.addEventListener('submit', async (ev) => {
      ev.preventDefault();
      const fd = new FormData(ev.target);
      const token = fd.get('token');
      const pin = fd.get('pin');

      clearErr(confError);

      const tokenInput = ev.target.elements.token;
      const pinInput = ev.target.elements.pin;
      window.UI.clearInlineError(tokenInput);
      window.UI.clearInlineError(pinInput);

      let isValid = true;
      if (!token) {
        window.UI.showInlineError(tokenInput, 'Token is required');
        isValid = false;
      }
      if (!pin || pin.length !== 6) {
        window.UI.showInlineError(pinInput, '6-digit PIN required');
        isValid = false;
      }
      if (!isValid) return;

      confBtn.disabled = true;
      confBtn.textContent = 'Resetting...';

      try {
        await window.API.AuthAPI.pinResetConfirm({ token, new_pin: pin });
        window.UI.toast('success', 'PIN reset. Please login.');
        window.Router.navigate('/login');
      } catch (err) {
        showErr(confError, err.message || 'Failed to reset PIN');
      } finally {
        confBtn.disabled = false;
        confBtn.textContent = 'Set New PIN';
      }
    });
  }

  return { mount };
})();
