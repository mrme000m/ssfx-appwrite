/**
 * auth-oauth — OAuth start, callback, session, logout
 * Runtime: node-22, entrypoint: src/main.js
 */

const { Account, Users } = require('node-appwrite');
const {
  makeAdminClient,
  makeAdminDb,
  makeAdminUsers,
  getServiceConfig,
  signState,
  verifyState,
  exchangeCtraderCode,
  validateRedirectUri,
  encrypt,
  generateGrantId,
  generateToken,
  corsHeaders,
  handleOptions,
  sessionCookie,
  clearCookie,
  rowData,
  ID,
  Query,
  Permission,
  Role,
} = require('./_shared');

const PROJECT_ID = process.env.APPWRITE_PROJECT_ID;
const DB_ID = process.env.CTRADER_AUTH_DATABASE_ID;
const ACCOUNTS_TABLE_ID = process.env.CTRADER_ACCOUNTS_TABLE_ID || 'ctrader_accounts';

const ALLOWED_SCOPES = ['accounts', 'trading'];
const DEFAULT_SCOPE = 'trading';

function normalizeScope(value) {
  const scope = typeof value === 'string' ? value.trim().toLowerCase() : '';
  return ALLOWED_SCOPES.includes(scope) ? scope : DEFAULT_SCOPE;
}

// Load OAuth config from service_config when available, falling back to env vars.
// The redirect URI is validated and must be configured explicitly; the old
// `${SITES_URL}/callback` fallback is removed because the OAuth callback must
// point to the auth-oauth function domain (e.g. auth.mrme.tech), not the SPA.
async function getOAuthConfig() {
  const db = makeAdminDb();
  const svc = await getServiceConfig(db, 'ctrader_oauth', 'CTRADER_OAUTH_JSON');
  if (svc && typeof svc === 'object' && svc.client_id) {
    return {
      clientId: svc.client_id,
      clientSecret: svc.client_secret,
      redirectUri: validateRedirectUri(svc.redirect_uri),
      environment: svc.environment,
      scope: normalizeScope(svc.scope),
    };
  }
  return {
    clientId: process.env.CTRADER_CLIENT_ID,
    clientSecret: process.env.CTRADER_CLIENT_SECRET,
    redirectUri: validateRedirectUri(process.env.CTRADER_REDIRECT_URI),
    environment: process.env.CTRADER_ENVIRONMENT || 'demo',
    scope: normalizeScope(process.env.CTRADER_SCOPE),
  };
}

function cookieName() {
  return `a_session_${PROJECT_ID}`;
}

const startRateLimit = new Map();

function checkStartRate(ip) {
  const now = Date.now();
  const window = 60_000;
  const max = 10;
  let rec = startRateLimit.get(ip);
  if (!rec || now - rec.t > window) {
    rec = { t: now, c: 0 };
  }
  rec.c++;
  startRateLimit.set(ip, rec);
  return rec.c <= max;
}

module.exports = async function main({ req, res, log, error }) {
  const preflight = handleOptions(req, res);
  if (preflight) return preflight;

  const path = req.path;
  const method = req.method;
  const origin = req.headers['origin'] || '';

  try {
    if (path === '/health' && method === 'GET') {
      return res.json({ status: 'ok', service: 'auth-oauth' }, 200, corsHeaders(origin));
    }
    if (path === '/auth/ctrader/start' && method === 'GET') {
      return await handleStart(req, res, log);
    }
    if (path === '/callback' && method === 'GET') {
      return await handleCallback(req, res, log, error);
    }
    if (path === '/session' && method === 'GET') {
      return await handleSession(req, res, log);
    }
    if (path === '/logout' && method === 'POST') {
      return await handleLogout(req, res, log);
    }
    if (path === '/admin/slaves' && method === 'GET') {
      return await handleAdminSlaves(req, res, log, error);
    }
    if (path === '/admin/slaves/unlink' && method === 'POST') {
      return await handleAdminUnlinkSlave(req, res, log, error);
    }
    const adminAccountsGet = path.match(/^\/admin\/slaves\/([^/]+)\/accounts$/);
    if (adminAccountsGet && method === 'GET') {
      return await handleAdminSlaveAccounts(req, res, adminAccountsGet[1], log, error);
    }
    const adminAccountDelete = path.match(/^\/admin\/slaves\/([^/]+)\/accounts\/([^/]+)$/);
    if (adminAccountDelete && method === 'DELETE') {
      return await handleAdminDeleteAccount(req, res, adminAccountDelete[1], adminAccountDelete[2], log, error);
    }
    const adminReset = path.match(/^\/admin\/slaves\/([^/]+)\/reset$/);
    if (adminReset && method === 'POST') {
      return await handleAdminResetSlave(req, res, adminReset[1], log, error);
    }
    if (path === '/echo' && method === 'GET') {
      return res.json({ cookie: req.headers['cookie'] || '', origin: req.headers['origin'] || '' }, 200, corsHeaders(origin));
    }
    if (path === '/debug-headers' && method === 'GET') {
      const dump = {
        headers: req.headers,
        path: req.path,
        method: req.method,
        query: req.query,
        bodyPreview: String(req.body || '').slice(0, 200),
      };
      log('DEBUG HEADERS: ' + JSON.stringify(dump));
      return res.json(dump, 200, corsHeaders(origin));
    }
    if (path === '/session-debug' && method === 'GET') {
      const cookie = req.headers['cookie'] || '';
      const sessionMatch = cookie.match(new RegExp(`${cookieName()}=([^;]+)`));
      if (!sessionMatch) {
        return res.json({ hasCookie: false, cookieLen: cookie.length }, 200, corsHeaders(origin));
      }
      const sessionCookieValue = sessionMatch[1];
      const accountRes = await fetch(`${process.env.APPWRITE_ENDPOINT}/account`, {
        headers: {
          'x-appwrite-project': PROJECT_ID,
          'Cookie': `${cookieName()}=${sessionCookieValue}`,
        },
      });
      const body = await accountRes.text().catch(() => '');
      if (!accountRes.ok) {
        return res.json({ status: accountRes.status, ok: accountRes.ok, bodyPreview: body.slice(0, 200) }, 200, corsHeaders(origin));
      }
      const user = JSON.parse(body);
      const db = makeAdminDb();
      try {
        const slaveList = await db.listRows({
          databaseId: DB_ID,
          tableId: 'users',
          queries: [Query.equal('appwrite_user_id', user.$id)],
        });
        const slave = slaveList.rows[0] || null;
        let accounts = [];
        if (slave && slave.grant_id) {
          const accountList = await db.listRows({
            databaseId: DB_ID,
            tableId: ACCOUNTS_TABLE_ID,
            queries: [Query.equal('grant_id', slave.grant_id)],
          });
          accounts = accountList.rows || [];
        }
        return res.json({ ok: true, user_id: user.$id, slave_found: !!slave, accounts_count: accounts.length }, 200, corsHeaders(origin));
      } catch (err) {
        return res.json({ ok: false, account_ok: true, error: err.message }, 200, corsHeaders(origin));
      }
    }

    return res.json({ error: 'Not found' }, 404, corsHeaders(origin));
  } catch (err) {
    error(String(err));
    return res.json({ error: 'Internal error', detail: err.message }, 500, corsHeaders(origin));
  }
};

// ─── GET /auth/ctrader/start ────────────────────────────────────────

async function handleStart(req, res, log) {
  const clientIp = req.headers['x-forwarded-for'] || 'unknown';
  if (!checkStartRate(clientIp)) {
    return res.json({ error: 'Rate limited' }, 429, corsHeaders(req.headers['origin'] || ''));
  }

  const oauth = await getOAuthConfig();
  if (!oauth.clientId || !oauth.redirectUri) {
    return res.json({ error: 'OAuth not configured' }, 503, corsHeaders(req.headers['origin'] || ''));
  }

  // OAuth start requires an authenticated session. Users must login via PIN first.
  let userId = '';
  const sessionUser = await getSessionUser(req);
  if (sessionUser) {
    userId = sessionUser.$id;
    log(`OAuth start using authenticated user=${userId}`);
  } else {
    log('OAuth start rejected: no authenticated session');
    const sitesUrl = process.env.SITES_URL || 'https://app.mrme.tech';
    return res.send('', 302, {
      Location: `${sitesUrl}/#/login?error=login_required`,
    });
  }

  const requestedScope = typeof req.query.scope === 'string' ? req.query.scope.trim().toLowerCase() : '';
  const scope = ALLOWED_SCOPES.includes(requestedScope) ? requestedScope : oauth.scope;
  const nonce = generateToken();
  const signedState = signState(userId || 'anon_' + nonce, process.env.SESSION_HMAC_KEY);

  const db = makeAdminDb();
  await db.createRow({
    databaseId: DB_ID,
    tableId: 'ephemeral_tokens',
    rowId: ID.unique(),
    data: {
      token: nonce,
      kind: 'oauth_state',
      user_id: userId || null,
      payload: JSON.stringify({
        redirect_uri: oauth.redirectUri,
        signed_state: signedState,
        scope,
      }),
      expires_at: new Date(Date.now() + 10 * 60 * 1000).toISOString(),
    },
  });

  const ctraderUrl = new URL('https://id.ctrader.com/my/settings/openapi/grantingaccess/');
  ctraderUrl.searchParams.set('client_id', oauth.clientId);
  ctraderUrl.searchParams.set('redirect_uri', oauth.redirectUri);
  ctraderUrl.searchParams.set('scope', scope);
  ctraderUrl.searchParams.set('product', 'web');
  ctraderUrl.searchParams.set('state', nonce);

  log(`OAuth start scope=${scope} user=${userId || 'anon'} → ${ctraderUrl.toString()}`);
  return res.send('', 302, { Location: ctraderUrl.toString() });
}

// ─── GET /callback ──────────────────────────────────────────────────

async function handleCallback(req, res, log, error) {
  const code = req.query.code;
  const state = req.query.state;
  const errorCode = req.query.error;
  const sitesUrl = process.env.SITES_URL;

  if (errorCode) {
    return res.redirect(`${sitesUrl}/#/dashboard?success=false&error=${encodeURIComponent(errorCode)}`);
  }

  if (!code || !state) {
    return res.redirect(`${sitesUrl}/#/dashboard?success=false&error=missing_params`);
  }

  const db = makeAdminDb();
  let ephemeral;
  try {
    const list = await db.listRows({
      databaseId: DB_ID,
      tableId: 'ephemeral_tokens',
      queries: [
        Query.equal('token', state),
        Query.equal('kind', 'oauth_state'),
      ],
    });
    if (list.rows.length === 0) {
      return res.redirect(`${sitesUrl}/#/dashboard?success=false&error=invalid_state`);
    }
    ephemeral = list.rows[0];
    const payload = JSON.parse(ephemeral.payload || '{}');
    const stateUserId = verifyState(payload.signed_state, process.env.SESSION_HMAC_KEY);
    if (!stateUserId) {
      return res.redirect(`${sitesUrl}/#/dashboard?success=false&error=invalid_state`);
    }
    if (!ephemeral.user_id && stateUserId) {
      ephemeral.user_id = stateUserId;
    }
    await db.deleteRow({
      databaseId: DB_ID,
      tableId: 'ephemeral_tokens',
      rowId: ephemeral.$id,
    });
  } catch (err) {
    error(`State lookup failed: ${err.message}`);
    return res.redirect(`${sitesUrl}/#/dashboard?success=false&error=state_lookup_failed`);
  }

  // Reject anonymous OAuth flows — user must be authenticated (PIN login) first.
  const users = makeAdminUsers();
  let appwriteUserId = ephemeral.user_id;
  if (!appwriteUserId || appwriteUserId.startsWith('anon_')) {
    log('OAuth callback rejected: anonymous flow not allowed');
    return res.redirect(`${sitesUrl}/#/login?error=login_required`);
  }

  // Verify the Appwrite user exists.
  try {
    await users.get({ userId: appwriteUserId });
  } catch (err) {
    log(`OAuth callback rejected: user ${appwriteUserId} not found`);
    return res.redirect(`${sitesUrl}/#/login?error=login_required`);
  }

  const oauth = await getOAuthConfig();

  let tokenData;
  try {
    tokenData = await exchangeCtraderCode(code, oauth);
  } catch (err) {
    error(`Token exchange failed: ${err.message}`);
    return res.redirect(`${sitesUrl}/#/dashboard?success=false&error=token_exchange`);
  }

  const { access_token, refresh_token, expires_in } = tokenData;
  const expiresAt = new Date(Date.now() + (expires_in || 3600) * 1000).toISOString();

  let grantId;
  let role = 'slave';
  try {
    const existingList = await db.listRows({
      databaseId: DB_ID,
      tableId: 'users',
      queries: [Query.equal('appwrite_user_id', appwriteUserId)],
    });
    if (existingList.rows.length > 0) {
      const existing = existingList.rows[0];
      grantId = existing.grant_id || generateGrantId();
      role = existing.role || 'slave';
      await db.updateRow({
        databaseId: DB_ID,
        tableId: 'users',
        rowId: existing.$id,
        data: {
          grant_id: grantId,
          access_token_enc: encrypt(access_token),
          refresh_token_enc: encrypt(refresh_token),
          access_token_expires_at: expiresAt,
          status: 'active',
        },
      });
    } else {
      // Should not happen — user should have registered first.
      error(`OAuth callback: no users row for appwrite_user_id=${appwriteUserId}`);
      return res.redirect(`${sitesUrl}/#/dashboard?success=false&error=account_not_found`);
    }
  } catch (err) {
    error(`Slave row upsert failed: ${err.message}`);
    return res.redirect(`${sitesUrl}/#/dashboard?success=false&error=db_create`);
  }

  // Refresh the Appwrite session cookie so the browser stays authenticated.
  const adminClient = makeAdminClient();
  const account = new Account(adminClient);
  const token = await users.createToken({ userId: appwriteUserId });
  const session = await account.createSession({ userId: appwriteUserId, secret: token.secret });

  const cookie = sessionCookie(cookieName(), session.secret);
  log(`OAuth success user=${appwriteUserId} grant=${grantId} role=${role}`);

  return res.send('', 302, {
    'Location': `${sitesUrl}/#/dashboard?success=true&grant_id=${grantId}`,
    'Set-Cookie': cookie,
  });
}

// ─── GET /session ───────────────────────────────────────────────────

async function handleSession(req, res, log) {
  const cookie = req.headers['cookie'] || '';
  const sessionMatch = cookie.match(new RegExp(`${cookieName()}=([^;]+)`));
  const debug = req.query?.debug === '1' || req.headers['x-debug'] === '1';
  if (!sessionMatch) {
    return res.json({ authenticated: false, reason: 'no_session_cookie' }, 200, corsHeaders(req.headers['origin'] || ''));
  }

  const sessionCookieValue = sessionMatch[1];

  try {
    const accountRes = await fetch(`${process.env.APPWRITE_ENDPOINT}/account`, {
      headers: {
        'x-appwrite-project': PROJECT_ID,
        'Cookie': `${cookieName()}=${sessionCookieValue}`,
      },
    });

    if (!accountRes.ok) {
      const body = await accountRes.text().catch(() => '');
      if (debug) {
        return res.json({ authenticated: false, reason: 'account_lookup_failed', status: accountRes.status, bodyPreview: body.slice(0, 200) }, 200, corsHeaders(req.headers['origin'] || ''));
      }
      return res.json({ authenticated: false }, 200, corsHeaders(req.headers['origin'] || ''));
    }

    const user = await accountRes.json();

    const db = makeAdminDb();
    const slaveList = await db.listRows({
      databaseId: DB_ID,
      tableId: 'users',
      queries: [Query.equal('appwrite_user_id', user.$id)],
    });

    const slave = rowData(slaveList.rows[0]) || null;
    
    // Check if user is master/admin via service_config table
    let role = slave ? slave.role : 'slave';
    let isMaster = false;
    
    if (!slave || role !== 'master') {
      try {
        const masterConfigList = await db.listRows({
          databaseId: DB_ID,
          tableId: 'service_config',
          queries: [Query.equal('config_key', 'master_auth')],
        });
        const masterConfig = rowData(masterConfigList.rows[0]) || null;
        if (masterConfig && masterConfig.config_value) {
          const config = JSON.parse(masterConfig.config_value);
          if (config.appwrite_user_id === user.$id) {
            role = config.role || 'master';
            isMaster = true;
          }
        }
      } catch (err) {
        error(`Master config lookup failed: ${err.message}`);
      }
    } else {
      isMaster = true;
    }

    let accounts = [];
    if (slave && slave.grant_id) {
      try {
        const accountList = await db.listRows({
          databaseId: DB_ID,
          tableId: ACCOUNTS_TABLE_ID,
          queries: [Query.equal('grant_id', slave.grant_id)],
        });
        accounts = (accountList.rows || []).map((row) => {
          const acc = rowData(row) || {};
          return {
            ctid_trader_account_id: acc.ctidTraderAccountId,
            is_live: acc.isLive,
            trader_login: acc.traderLogin,
            broker_title_short: acc.brokerTitleShort,
            broker_name: acc.brokerName,
            last_closing_deal_timestamp: acc.lastClosingDealTimestamp,
            last_balance_update_timestamp: acc.lastBalanceUpdateTimestamp,
            balance: typeof acc.balance === 'number' ? acc.balance : null,
            money_digits: acc.moneyDigits,
            account_type: acc.accountType,
            deposit_asset_id: acc.depositAssetId,
            leverage_in_cents: acc.leverageInCents,
            registration_timestamp: acc.registrationTimestamp,
            selected: acc.selected,
          };
        });
      } catch (accErr) {
        error(`Account lookup failed: ${accErr.message}`);
      }
    }

    // Use slave data if available, but override role if user is master/admin
    const responseSlave = slave || {
      grant_id: '',
      username: '',
      status: '',
      active: false,
      ctrader_account_ids: '',
      selected_account_id: '',
      last_heartbeat_at: null,
    };
    
    return res.json({
      authenticated: true,
      user_id: user.$id,
      grant_id: responseSlave.grant_id,
      role: role,  // Use the determined role (slave, master, or admin)
      name: user.name,
      username: responseSlave.username,
      status: responseSlave.status,
      active: responseSlave.active,
      ctrader_account_ids: responseSlave.ctrader_account_ids,
      selected_account_id: responseSlave.selected_account_id,
      last_heartbeat_at: responseSlave.last_heartbeat_at,
      accounts,
    }, 200, corsHeaders(req.headers['origin'] || ''));
  } catch (err) {
    if (debug) {
      return res.json({ authenticated: false, reason: 'exception', error: err.message }, 200, corsHeaders(req.headers['origin'] || ''));
    }
    return res.json({ authenticated: false }, 200, corsHeaders(req.headers['origin'] || ''));
  }
}

// ─── POST /logout ───────────────────────────────────────────────────

async function handleLogout(req, res, log) {
  const cookie = req.headers['cookie'] || '';
  const sessionMatch = cookie.match(new RegExp(`${cookieName()}=([^;]+)`));

  if (sessionMatch) {
    try {
      await fetch(`${process.env.APPWRITE_ENDPOINT}/account/sessions/current`, {
        method: 'DELETE',
        headers: {
          'x-appwrite-project': PROJECT_ID,
          'Cookie': `${cookieName()}=${sessionMatch[1]}`,
        },
      });
    } catch {
      // ignore
    }
  }

  return res.json({ success: true }, 200, {
    ...corsHeaders(req.headers['origin'] || ''),
    'Set-Cookie': clearCookie(cookieName()),
  });
}

// ─── Session helper ─────────────────────────────────────────────────

async function getSessionUser(req) {
  const cookie = req.headers['cookie'] || '';
  const sessionMatch = cookie.match(new RegExp(`${cookieName()}=([^;]+)`));
  if (!sessionMatch) return null;

  try {
    const accountRes = await fetch(`${process.env.APPWRITE_ENDPOINT}/account`, {
      headers: {
        'x-appwrite-project': PROJECT_ID,
        'Cookie': `${cookieName()}=${sessionMatch[1]}`,
      },
    });
    if (!accountRes.ok) return null;
    return await accountRes.json();
  } catch {
    return null;
  }
}

// ─── GET /admin/slaves ──────────────────────────────────────────────

async function handleAdminSlaves(req, res, log, error) {
  const auth = await requireMaster(req, res);
  if (auth.error) return auth.error;
  const { db } = auth;

  try {
    const list = await db.listRows({
      databaseId: DB_ID,
      tableId: 'users',
      queries: [Query.equal('role', 'slave'), Query.limit(100)],
    });

    const slaves = (list.rows || []).map((row) => {
      const s = rowData(row) || {};
      return {
        username: s.username || '',
        email: s.email || '',
        grant_id: s.grant_id || '',
        status: s.status || '',
        active: s.active || false,
        ctrader_account_ids: s.ctrader_account_ids || '',
        selected_account_id: s.selected_account_id || '',
        last_heartbeat_at: s.last_heartbeat_at || null,
      };
    });

    return res.json({ success: true, slaves }, 200, corsHeaders(req.headers['origin'] || ''));
  } catch (err) {
    error(`Admin slaves query failed: ${err.message}`);
    return res.json({ error: 'Failed to load slaves' }, 500, corsHeaders(req.headers['origin'] || ''));
  }
}

// ─── Admin helpers ──────────────────────────────────────────────────

async function requireMaster(req, res) {
  const user = await getSessionUser(req);
  if (!user) {
    return { error: res.json({ error: 'Unauthorized' }, 401, corsHeaders(req.headers['origin'] || '')) };
  }
  const db = makeAdminDb();

  // Allow either a slave_accounts row with role master or the service_config master_auth record.
  const callerList = await db.listRows({
    databaseId: DB_ID,
    tableId: 'users',
    queries: [Query.equal('appwrite_user_id', user.$id)],
  });
  const caller = rowData(callerList.rows[0]) || null;
  if (caller && caller.role === 'master') {
    return { user, db };
  }

  const masterConfigList = await db.listRows({
    databaseId: DB_ID,
    tableId: 'service_config',
    queries: [Query.equal('config_key', 'master_auth')],
  });
  const masterConfig = rowData(masterConfigList.rows[0]) || null;
  if (masterConfig && masterConfig.config_value) {
    try {
      const config = JSON.parse(masterConfig.config_value);
      if (config.appwrite_user_id === user.$id) {
        return { user, db };
      }
    } catch {
      // ignore parse error
    }
  }

  return { error: res.json({ error: 'Forbidden' }, 403, corsHeaders(req.headers['origin'] || '')) };
}

// ─── GET /admin/slaves/:grant_id/accounts ───────────────────────────

async function handleAdminSlaveAccounts(req, res, grantId, log, error) {
  const auth = await requireMaster(req, res);
  if (auth.error) return auth.error;
  const { db } = auth;

  try {
    const accountList = await db.listRows({
      databaseId: DB_ID,
      tableId: 'accounts',
      queries: [Query.equal('grant_id', grantId)],
    });
    const accounts = (accountList.rows || []).map((row) => {
      const acc = rowData(row) || {};
      return {
        ctid_trader_account_id: acc.ctidTraderAccountId,
        is_live: acc.isLive,
        trader_login: acc.traderLogin,
        broker_title_short: acc.brokerTitleShort,
        broker_name: acc.brokerName,
        last_closing_deal_timestamp: acc.lastClosingDealTimestamp,
        last_balance_update_timestamp: acc.lastBalanceUpdateTimestamp,
        balance: typeof acc.balance === 'number' ? acc.balance : null,
        money_digits: acc.moneyDigits,
        account_type: acc.accountType,
        deposit_asset_id: acc.depositAssetId,
        leverage_in_cents: acc.leverageInCents,
        registration_timestamp: acc.registrationTimestamp,
        selected: acc.selected,
      };
    });
    return res.json({ success: true, grant_id: grantId, accounts }, 200, corsHeaders(req.headers['origin'] || ''));
  } catch (err) {
    error(`Admin slave accounts query failed: ${err.message}`);
    return res.json({ error: 'Failed to load accounts' }, 500, corsHeaders(req.headers['origin'] || ''));
  }
}

// ─── DELETE /admin/slaves/:grant_id/accounts/:account_id ────────────

async function handleAdminDeleteAccount(req, res, grantId, accountId, log, error) {
  const auth = await requireMaster(req, res);
  if (auth.error) return auth.error;
  const { db } = auth;

  try {
    const numericId = Number(accountId);
    const accountList = await db.listRows({
      databaseId: DB_ID,
      tableId: 'accounts',
      queries: [
        Query.equal('grant_id', grantId),
        Query.equal('ctidTraderAccountId', numericId),
      ],
    });
    if (!accountList.rows || accountList.rows.length === 0) {
      return res.json({ error: 'Account not found' }, 404, corsHeaders(req.headers['origin'] || ''));
    }
    const row = accountList.rows[0];
    await db.deleteRow({
      databaseId: DB_ID,
      tableId: 'accounts',
      rowId: row.$id,
    });

    // Update slave row to remove the account id from its list and clear selection if needed.
    const slaveList = await db.listRows({
      databaseId: DB_ID,
      tableId: 'users',
      queries: [Query.equal('grant_id', grantId)],
    });
    if (slaveList.rows && slaveList.rows.length > 0) {
      const slaveRow = slaveList.rows[0];
      const slave = rowData(slaveRow) || {};
      const ids = String(slave.ctrader_account_ids || '')
        .split(',')
        .map((s) => s.trim())
        .filter(Boolean)
        .filter((id) => id !== String(numericId));
      const update = { ctrader_account_ids: ids.join(',') };
      if (String(slave.selected_account_id) === String(numericId)) {
        update.selected_account_id = '';
      }
      await db.updateRow({
        databaseId: DB_ID,
        tableId: 'users',
        rowId: slaveRow.$id,
        data: update,
      });
    }

    log(`Admin deleted account ${accountId} for grant ${grantId}`);
    return res.json({ success: true, deleted_account_id: numericId }, 200, corsHeaders(req.headers['origin'] || ''));
  } catch (err) {
    error(`Admin delete account failed: ${err.message}`);
    return res.json({ error: 'Failed to delete account' }, 500, corsHeaders(req.headers['origin'] || ''));
  }
}

// ─── POST /admin/slaves/unlink ──────────────────────────────────────

async function handleAdminUnlinkSlave(req, res, log, error) {
  const auth = await requireMaster(req, res);
  if (auth.error) return auth.error;
  const { db } = auth;

  const body = req.bodyJson || {};
  const grantId = String(body.grant_id || '').trim();
  if (!grantId) {
    return res.json({ error: 'grant_id required' }, 400, corsHeaders(req.headers['origin'] || ''));
  }

  try {
    // Delete all persisted accounts for this grant.
    const accountList = await db.listRows({
      databaseId: DB_ID,
      tableId: 'accounts',
      queries: [Query.equal('grant_id', grantId)],
    });
    for (const row of accountList.rows || []) {
      await db.deleteRow({
        databaseId: DB_ID,
        tableId: 'accounts',
        rowId: row.$id,
      });
    }

    // Clear tokens and account references on the slave row.
    const slaveList = await db.listRows({
      databaseId: DB_ID,
      tableId: 'users',
      queries: [Query.equal('grant_id', grantId)],
    });
    if (slaveList.rows && slaveList.rows.length > 0) {
      const slaveRow = slaveList.rows[0];
      await db.updateRow({
        databaseId: DB_ID,
        tableId: 'users',
        rowId: slaveRow.$id,
        data: {
          access_token_enc: null,
          refresh_token_enc: null,
          access_token_expires_at: null,
          ctrader_account_ids: '',
          selected_account_id: '',
          status: 'unlinked',
          active: false,
        },
      });
    }

    log(`Admin unlinked grant ${grantId}`);
    return res.json({ success: true, grant_id: grantId }, 200, corsHeaders(req.headers['origin'] || ''));
  } catch (err) {
    error(`Admin unlink slave failed: ${err.message}`);
    return res.json({ error: 'Failed to unlink slave' }, 500, corsHeaders(req.headers['origin'] || ''));
  }
}

// ─── POST /admin/slaves/:grant_id/reset ────────────────────────────────────

async function handleAdminResetSlave(req, res, grantId, log, error) {
  const auth = await requireMaster(req, res);
  if (auth.error) return auth.error;
  const { db } = auth;

  try {
    // Delete all persisted accounts for this grant.
    const accountList = await db.listRows({
      databaseId: DB_ID,
      tableId: 'accounts',
      queries: [Query.equal('grant_id', grantId)],
    });
    for (const row of accountList.rows || []) {
      await db.deleteRow({
        databaseId: DB_ID,
        tableId: 'accounts',
        rowId: row.$id,
      });
    }

    // Delete the slave row entirely.
    const slaveList = await db.listRows({
      databaseId: DB_ID,
      tableId: 'users',
      queries: [Query.equal('grant_id', grantId)],
    });
    if (slaveList.rows && slaveList.rows.length > 0) {
      const slaveRow = slaveList.rows[0];
      await db.deleteRow({
        databaseId: DB_ID,
        tableId: 'users',
        rowId: slaveRow.$id,
      });
    }

    // Also delete any trade configs for this grant
    const configList = await db.listRows({
      databaseId: DB_ID,
      tableId: 'trade_settings',
      queries: [Query.equal('grant_id', grantId)],
    });
    for (const row of configList.rows || []) {
      await db.deleteRow({
        databaseId: DB_ID,
        tableId: 'trade_settings',
        rowId: row.$id,
      });
    }

    log(`Admin reset grant ${grantId} - deleted slave row and all related data`);
    return res.json({ success: true, grant_id: grantId }, 200, corsHeaders(req.headers['origin'] || ''));
  } catch (err) {
    error(`Admin reset slave failed: ${err.message}`);
    return res.json({ error: 'Failed to reset slave' }, 500, corsHeaders(req.headers['origin'] || ''));
  }
}
// cache-bust: 1783199021
