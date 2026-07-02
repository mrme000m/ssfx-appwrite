/**
 * SSFX HQ — Authentication and session management via Appwrite Functions.
 */
window.Auth = (function () {
  const CFG = window.APP_CONFIG || {};
  let client = null;
  let account = null;
  let tablesDB = null;

  function init() {
    if (!window.Appwrite) return;
    client = new window.Appwrite.Client();
    client.setEndpoint(CFG.endpoint).setProject(CFG.projectId);
    account = new window.Appwrite.Account(client);
    tablesDB = new window.Appwrite.TablesDB(client);
  }

  function getClient() { return client; }
  function getAccount() { return account; }
  function getTablesDB() { return tablesDB; }

  async function checkSession() {
    try {
      const data = await window.API.AuthAPI.session();
      window.appState.user = data.user || null;
      window.appState.userId = data.user_id || data.user?.$id || null;
      window.appState.username = data.username || '';
      window.appState.role = data.role || 'slave';
      window.appState.grantId = data.grant_id || null;
      window.appState.status = data.status || '';
      window.appState.active = data.active === true;
      window.appState.lastHeartbeat = data.last_heartbeat_at || null;
      return data;
    } catch (err) {
      window.appState.user = null;
      window.appState.userId = null;
      window.appState.username = '';
      window.appState.role = null;
      window.appState.grantId = null;
      window.appState.status = '';
      window.appState.active = false;
      return null;
    }
  }

  function isMaster() {
    return window.appState.role === 'master' || window.appState.role === 'admin';
  }

  function isAuthenticated() {
    return !!window.appState.user;
  }

  async function login(username, pin) {
    const data = await window.API.AuthAPI.pinLogin({ username, pin });
    await checkSession();
    return data;
  }

  async function logout() {
    try {
      await window.API.AuthAPI.logout();
    } catch (err) {
      // ignore
    }
    window.appState.user = null;
    window.appState.userId = null;
    window.appState.username = '';
    window.appState.role = null;
    window.appState.grantId = null;
  }

  async function setCredentials(username, pin) {
    return window.API.AuthAPI.setCredentials({ username, pin });
  }

  return {
    init,
    getClient,
    getAccount,
    getTablesDB,
    checkSession,
    isMaster,
    isAuthenticated,
    login,
    logout,
    setCredentials,
  };
})();
