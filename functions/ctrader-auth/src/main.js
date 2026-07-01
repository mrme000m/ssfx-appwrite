/**
 * ctrader-auth — OAuth start, callback, session, logout
 * Runtime: node-22, entrypoint: src/main.js
 */

const { Account, Users } = require('node-appwrite');
const {
  makeAdminClient,
  makeAdminDb,
  makeAdminUsers,
  signState,
  verifyState,
  exchangeCtraderCode,
  encrypt,
  generateGrantId,
  generateToken,
  ID,
  Query,
  Permission,
  Role,
} = require('../../_shared');

const PROJECT_ID = process.env.APPWRITE_PROJECT_ID;
const DB_ID = process.env.CTRADER_AUTH_DATABASE_ID;

function cookieName() {
  return `a_session_${PROJECT_ID}`;
}

// In-memory rate limiter for OAuth start (per IP)
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
  const path = req.path;
  const method = req.method;

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

    return res.json({ error: 'Not found' }, 404);
  } catch (err) {
    error(String(err));
    return res.json({ error: 'Internal error', detail: err.message }, 500);
  }
};

// ─── GET /auth/ctrader/start ────────────────────────────────────────

async function handleStart(req, res, log) {
  const clientIp = req.headers['x-forwarded-for'] || 'unknown';
  if (!checkStartRate(clientIp)) {
    return res.json({ error: 'Rate limited' }, 429);
  }

  const userId = req.query.user_id || '';
  const state = signState(userId || 'anon_' + generateToken(), process.env.SESSION_HMAC_KEY);

  // Store ephemeral token (oauth_state)
  const db = makeAdminDb();
  await db.createDocument(DB_ID, 'ephemeral_tokens', ID.unique(), {
    token: state,
    kind: 'oauth_state',
    user_id: userId || null,
    payload: JSON.stringify({ redirect_uri: process.env.CTRADER_REDIRECT_URI }),
    expires_at: new Date(Date.now() + 10 * 60 * 1000).toISOString(),
  });

  const ctraderUrl = new URL('https://id.ctrader.com/Apps/GetAuthToken');
  ctraderUrl.searchParams.set('client_id', process.env.CTRADER_CLIENT_ID);
  ctraderUrl.searchParams.set('redirect_uri', process.env.CTRADER_REDIRECT_URI);
  ctraderUrl.searchParams.set('scope', 'trading');
  ctraderUrl.searchParams.set('response_type', 'code');
  ctraderUrl.searchParams.set('state', state);

  log(`OAuth start → ${ctraderUrl.toString()}`);
  return res.redirect(ctraderUrl.toString());
}

// ─── GET /callback ──────────────────────────────────────────────────

async function handleCallback(req, res, log, error) {
  const code = req.query.code;
  const state = req.query.state;
  const errorCode = req.query.error;

  if (errorCode) {
    return res.redirect(`${process.env.SITES_URL}/#/onboarding?success=false&error=${encodeURIComponent(errorCode)}`);
  }

  if (!code || !state) {
    return res.redirect(`${process.env.SITES_URL}/#/onboarding?success=false&error=missing_params`);
  }

  // Verify ephemeral token (oauth_state)
  const db = makeAdminDb();
  let ephemeral;
  try {
    const list = await db.listDocuments(DB_ID, 'ephemeral_tokens', [
      Query.equal('token', state),
      Query.equal('kind', 'oauth_state'),
    ]);
    if (list.documents.length === 0) {
      return res.redirect(`${process.env.SITES_URL}/#/onboarding?success=false&error=invalid_state`);
    }
    ephemeral = list.documents[0];
    // Consume (delete)
    await db.deleteDocument(DB_ID, 'ephemeral_tokens', ephemeral.$id);
  } catch (err) {
    error(`State lookup failed: ${err.message}`);
    return res.redirect(`${process.env.SITES_URL}/#/onboarding?success=false&error=state_lookup_failed`);
  }

  // Exchange code for tokens
  let tokenData;
  try {
    tokenData = await exchangeCtraderCode(code);
  } catch (err) {
    error(`Token exchange failed: ${err.message}`);
    return res.redirect(`${process.env.SITES_URL}/#/onboarding?success=false&error=token_exchange`);
  }

  const { access_token, refresh_token, expires_in } = tokenData;
  const expiresAt = new Date(Date.now() + (expires_in || 3600) * 1000).toISOString();

  // Determine or create Appwrite user
  const users = makeAdminUsers();
  let appwriteUserId = ephemeral.user_id;

  if (!appwriteUserId || appwriteUserId.startsWith('anon_')) {
    // Create a new synthetic user
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
      return res.redirect(`${process.env.SITES_URL}/#/onboarding?success=false&error=user_create`);
    }
  } else {
    // Verify existing user
    try {
      await users.get({ userId: appwriteUserId });
    } catch {
      // User gone, create new
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

  // Create slave_accounts row
  const grantId = generateGrantId();
  try {
    await db.createDocument(DB_ID, 'slave_accounts', ID.unique(), {
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
    }, [
      Permission.read(Role.user(appwriteUserId)),
      Permission.update(Role.user(appwriteUserId)),
    ]);
  } catch (err) {
    error(`Slave row creation failed: ${err.message}`);
    return res.redirect(`${process.env.SITES_URL}/#/onboarding?success=false&error=db_create`);
  }

  // Create session via custom token → server-side session creation
  const adminClient = makeAdminClient();
  const account = new Account(adminClient);
  const token = await users.createToken({ userId: appwriteUserId });
  const session = await account.createSession({ userId: appwriteUserId, secret: token.secret });

  // Set cookie and redirect
  const cookie = `${cookieName()}=${session.secret}; HttpOnly; Secure; SameSite=Strict; Path=/; Max-Age=604800`;
  log(`OAuth success user=${appwriteUserId} grant=${grantId}`);

  return res.send('', 302, {
    'Location': `${process.env.SITES_URL}/#/onboarding?success=true&grant_id=${grantId}`,
    'Set-Cookie': cookie,
  });
}

// ─── GET /session ───────────────────────────────────────────────────

async function handleSession(req, res, log) {
  const cookie = req.headers['cookie'] || '';
  const sessionMatch = cookie.match(new RegExp(`${cookieName()}=([^;]+)`));
  if (!sessionMatch) {
    return res.json({ authenticated: false }, 200);
  }

  const sessionSecret = sessionMatch[1];
  const client = makeAdminClient();
  const account = new Account(client);

  try {
    // Set session on the client to validate
    client.setSession(sessionSecret);
    const user = await account.get();

    // Fetch slave row for role/grant
    const db = makeAdminDb();
    const slaveList = await db.listDocuments(DB_ID, 'slave_accounts', [
      Query.equal('appwrite_user_id', user.$id),
    ]);

    const slave = slaveList.documents[0] || null;

    return res.json({
      authenticated: true,
      user_id: user.$id,
      grant_id: slave ? slave.grant_id : '',
      role: slave ? slave.role : 'slave',
      name: user.name,
      expires_at: null, // Appwrite session expiry not exposed directly
    });
  } catch (err) {
    return res.json({ authenticated: false }, 200);
  }
}

// ─── POST /logout ───────────────────────────────────────────────────

async function handleLogout(req, res, log) {
  const cookie = req.headers['cookie'] || '';
  const sessionMatch = cookie.match(new RegExp(`${cookieName()}=([^;]+)`));

  if (sessionMatch) {
    const client = makeAdminClient();
    const account = new Account(client);
    try {
      client.setSession(sessionMatch[1]);
      await account.deleteSession({ sessionId: 'current' });
    } catch {
      // ignore
    }
  }

  const clearCookie = `${cookieName()}=; HttpOnly; Secure; SameSite=Strict; Path=/; Max-Age=0`;
  return res.send('', 200, {
    'Set-Cookie': clearCookie,
  });
}
