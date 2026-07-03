/**
 * SSFX HQ — Global application state and event bus.
 */
window.appState = {
  user: null,
  userId: null,
  username: '',
  role: null,
  grantId: null,
  status: '',
  active: false,
  lastHeartbeat: null,
  accounts: [],
  selectedAccountId: '',
  signals: [],
  agentLogs: [],
  health: {},
  online: false,
  dataserviceOnline: false,
  agentOnline: false,
  initialized: false,
};

window.commandBus = new EventTarget();
