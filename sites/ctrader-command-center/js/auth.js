const CFG = window.APP_CONFIG || {};

let client = null;
let account = null;
let tablesDB = null;

export const Auth = {
  user: null,

  init() {
    if (!window.Appwrite) {
      console.error('Appwrite SDK not loaded');
      return false;
    }
    client = new Appwrite.Client().setEndpoint(CFG.appwriteEndpoint).setProject(CFG.appwriteProjectId);
    account = new Appwrite.Account(client);
    tablesDB = new Appwrite.TablesDB(client);
    return true;
  },

  async checkSession() {
    if (!account) this.init();
    try {
      const user = await account.get();
      Auth.user = user;
      return { ok: true, user, isMaster: (user.labels || []).includes('master') };
    } catch (err) {
      Auth.user = null;
      return { ok: false, error: err.message };
    }
  },

  isMaster() {
    return Auth.user && (Auth.user.labels || []).includes('master');
  },

  getTablesDB() {
    return tablesDB;
  },

  getClient() {
    return client;
  },
};

window.Auth = Auth;
