/**
 * ctrader-auth — OAuth start, callback, session, logout
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

// Load OAuth config from service_config when available, falling back to env vars.
async function getOAuthConfig() {
  const db = makeAdminDb();
  const svc = await getServiceConfig(db, 'ctrader_oauth', 'CTRADER_OAUTH_JSON');
  if (svc && typeof svc === 'object' && svc.client_id) {
    return {
      clientId: svc.client_id,
      clientSecret: svc.client_secret,
      redirectUri: svc.redirect_uri,
      environment: svc.environment,
    };
  }
  return {
    clientId: process.env.CTRADER_CLIENT_ID,
    clientSecret: process.env.CTRADER_CLIENT_SECRET,
    redirectUri: process.env.CTRADER_REDIRECT_URI || `${process.env.SITES_URL}/callback`,
    environment: process.env.CTRADER_ENVIRONMENT || 'demo',
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
    if (path === '/echo' && method === 'GET') {
      return res.json({ cookie: req.headers['cookie'] || '', origin: req.headers['origin'] || '' }, 200, corsHeaders(origin));
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
          tableId: 'slave_accounts',
          queries: [Query.equal('appwrite_user_id', user.$id)],
        });
        const slave = slaveList.rows[0] || null;
        let accounts = [];
        if (slave && slave.grant_id) {
          const accountList = await db.listRows({
            databaseId: DB_ID,
            tableId: 'accounts',
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

  const userId = req.query.user_id || '';
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
      }),
      expires_at: new Date(Date.now() + 10 * 60 * 1000).toISOString(),
    },
  });

  const ctraderUrl = new URL('https://id.ctrader.com/my/settings/openapi/grantingaccess/');
  ctraderUrl.searchParams.set('client_id', oauth.clientId);
  ctraderUrl.searchParams.set('redirect_uri', oauth.redirectUri);
  ctraderUrl.searchParams.set('scope', 'trading');
  ctraderUrl.searchParams.set('product', 'web');
  ctraderUrl.searchParams.set('state', nonce);

  log(`OAuth start → ${ctraderUrl.toString()}`);
  return res.send('', 302, { Location: ctraderUrl.toString() });
}

// ─── GET /callback ──────────────────────────────────────────────────

async function handleCallback(req, res, log, error) {
  const code = req.query.code;
  const state = req.query.state;
  const errorCode = req.query.error;
  const sitesUrl = process.env.SITES_URL;

  if (errorCode) {
    return res.redirect(`${sitesUrl}/#/onboarding?success=false&error=${encodeURIComponent(errorCode)}`);
  }

  if (!code || !state) {
    return res.redirect(`${sitesUrl}/#/onboarding?success=false&error=missing_params`);
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
      return res.redirect(`${sitesUrl}/#/onboarding?success=false&error=invalid_state`);
    }
    ephemeral = list.rows[0];
    const payload = JSON.parse(ephemeral.payload || '{}');
    const stateUserId = verifyState(payload.signed_state, process.env.SESSION_HMAC_KEY);
    if (!stateUserId) {
      return res.redirect(`${sitesUrl}/#/onboarding?success=false&error=invalid_state`);
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
    return res.redirect(`${sitesUrl}/#/onboarding?success=false&error=state_lookup_failed`);
  }

  const oauth = await getOAuthConfig();

  let tokenData;
  try {
    tokenData = await exchangeCtraderCode(code, oauth);
  } catch (err) {
    error(`Token exchange failed: ${err.message}`);
    return res.redirect(`${sitesUrl}/#/onboarding?success=false&error=token_exchange`);
  }

  const { access_token, refresh_token, expires_in } = tokenData;
  const expiresAt = new Date(Date.now() + (expires_in || 3600) * 1000).toISOString();

  const users = makeAdminUsers();
  let appwriteUserId = ephemeral.user_id;

  if (!appwriteUserId || appwriteUserId.startsWith('anon_')) {
    const generatedEmail = `slave_${generateGrantId().slice(6)}@local.slwp`;
    const generatedPassword = generateToken() + generateToken();
    try {
      const user = await users.createArgon2User({
        userId: ID.unique(),
        email: generatedEmail,
        password: generatedPassword,
        name: 'New Slave',
      });
      appwriteUserId = user.$id;
    } catch (err) {
      error(`User creation failed: ${err.message}`);
      return res.redirect(`${sitesUrl}/#/onboarding?success=false&error=user_create`);
    }
  } else {
    try {
      await users.get({ userId: appwriteUserId });
    } catch {
      const generatedEmail = `slave_${generateGrantId().slice(6)}@local.slwp`;
      const generatedPassword = generateToken() + generateToken();
      const user = await users.createArgon2User({
        userId: ID.unique(),
        email: generatedEmail,
        password: generatedPassword,
        name: 'New Slave',
      });
      appwriteUserId = user.$id;
    }
  }

  let grantId;
  let role = 'slave';
  let hasCredentials = false;
  try {
    const existingList = await db.listRows({
      databaseId: DB_ID,
      tableId: 'slave_accounts',
      queries: [Query.equal('appwrite_user_id', appwriteUserId)],
    });
    if (existingList.rows.length > 0) {
      const existing = existingList.rows[0];
      grantId = existing.grant_id || generateGrantId();
      role = existing.role || 'slave';
      hasCredentials = !!(existing.username && existing.pin_hash);
      await db.updateRow({
        databaseId: DB_ID,
        tableId: 'slave_accounts',
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
      grantId = generateGrantId();
      await db.createRow({
        databaseId: DB_ID,
        tableId: 'slave_accounts',
        rowId: ID.unique(),
        data: {
          appwrite_user_id: appwriteUserId,
          username: '',
          pin_hash: '',
          role: 'slave',
          grant_id: grantId,
          access_token_enc: encrypt(access_token),
          refresh_token_enc: encrypt(refresh_token),
          access_token_expires_at: expiresAt,
          ctrader_account_ids: '',
          selected_account_id: '',
          status: 'active',
          active: false,
          email: '',
          last_heartbeat_at: null,
        },
        permissions: [
          Permission.read(Role.user(appwriteUserId)),
          Permission.update(Role.user(appwriteUserId)),
          Permission.read(Role.users()),
        ],
      });
    }
  } catch (err) {
    error(`Slave row upsert failed: ${err.message}`);
    return res.redirect(`${sitesUrl}/#/onboarding?success=false&error=db_create`);
  }

  const adminClient = makeAdminClient();
  const account = new Account(adminClient);
  const token = await users.createToken({ userId: appwriteUserId });
  const session = await account.createSession({ userId: appwriteUserId, secret: token.secret });

  const cookie = sessionCookie(cookieName(), session.secret);
  log(`OAuth success user=${appwriteUserId} grant=${grantId} role=${role} hasCredentials=${hasCredentials}`);

  // Masters and users that already set a PIN go straight to the dashboard.
  // New slaves without credentials land on onboarding to choose username+PIN.
  let redirectPath;
  if (role === 'master' || hasCredentials) {
    redirectPath = role === 'master' ? '/master' : '/dashboard';
  } else {
    redirectPath = '/onboarding';
  }
  return res.send('', 302, {
    'Location': `${sitesUrl}/#${redirectPath}?success=true&grant_id=${grantId}`,
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
      tableId: 'slave_accounts',
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
        const masterConfig = masterConfigList.rows[0] || null;
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
          tableId: 'accounts',
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
  const user = await getSessionUser(req);
  if (!user) {
    return res.json({ error: 'Unauthorized' }, 401, corsHeaders(req.headers['origin'] || ''));
  }

  const db = makeAdminDb();

  // Verify caller is a master
  const callerList = await db.listRows({
    databaseId: DB_ID,
    tableId: 'slave_accounts',
    queries: [Query.equal('appwrite_user_id', user.$id)],
  });
  const caller = callerList.rows[0] || null;
  if (!caller || caller.role !== 'master') {
    return res.json({ error: 'Forbidden' }, 403, corsHeaders(req.headers['origin'] || ''));
  }

  try {
    const list = await db.listRows({
      databaseId: DB_ID,
      tableId: 'slave_accounts',
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
