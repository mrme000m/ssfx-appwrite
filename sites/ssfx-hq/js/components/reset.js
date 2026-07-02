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
              <label class="form-label">Username</label>
              <input name="username" class="form-input" placeholder="username" required />
            </div>
            <div class="form-group" style="margin-bottom:16px">
              <label class="form-label">Email</label>
              <input name="email" type="email" class="form-input" placeholder="email" required />
            </div>
            <button type="submit" class="btn btn-primary w-full">Send Reset Token</button>
          </form>

          <form id="reset-confirm-form" class="mt-6">
            <div class="form-group" style="margin-bottom:16px">
              <label class="form-label">Token</label>
              <input name="token" class="form-input" placeholder="reset token" />
            </div>
            <div class="form-group" style="margin-bottom:16px">
              <label class="form-label">New PIN (6 digits)</label>
              <input name="pin" type="password" inputmode="numeric" maxlength="6" class="form-input" placeholder="000000" />
            </div>
            <button type="submit" class="btn btn-primary w-full">Set New PIN</button>
          </form>

          <div class="text-center mt-4">
            <a href="#/login" class="text-sm text-dim">Back to login</a>
          </div>
        </div>
      </div>
    `;

    container.querySelector('#reset-request-form').addEventListener('submit', async (ev) => {
      ev.preventDefault();
      const fd = new FormData(ev.target);
      try {
        await window.API.AuthAPI.pinResetRequest({
          username: fd.get('username'),
          email: fd.get('email'),
        });
        window.UI.toast('success', 'Reset token sent if email matches.');
      } catch (err) {
        window.UI.toast('error', err.message);
      }
    });

    container.querySelector('#reset-confirm-form').addEventListener('submit', async (ev) => {
      ev.preventDefault();
      const fd = new FormData(ev.target);
      try {
        await window.API.AuthAPI.pinResetConfirm({
          token: fd.get('token'),
          new_pin: fd.get('pin'),
        });
        window.UI.toast('success', 'PIN reset. Please login.');
        window.Router.navigate('/login');
      } catch (err) {
        window.UI.toast('error', err.message);
      }
    });
  }

  return { mount };
})();
