/**
 * auth-pin — PIN login, set-credentials, PIN reset
 * Runtime: node-22, entrypoint: src/main.js
 */

const crypto = require('crypto');
const { Account, Users } = require('node-appwrite');
const {
  makeAdminClient,
  makeAdminDb,
  makeAdminUsers,
  getServiceConfig,
  generateToken,
  corsHeaders,
  handleOptions,
  sessionCookie,
  ID,
  Query,
  Permission,
  Role,
} = require('./_shared');

const PROJECT_ID = process.env.APPWRITE_PROJECT_ID;
const DB_ID = process.env.CTRADER_AUTH_DATABASE_ID;

const RESEND_API_KEY = process.env.RESEND_API_KEY || '';
const RESEND_FROM_EMAIL = process.env.RESEND_FROM_EMAIL || 'noreply@email.mrme.tech';
const PIN_RESET_BASE_URL = process.env.PIN_RESET_BASE_URL || (process.env.SITES_URL || 'https://app.mrme.tech').split(',')[0].trim();

function cookieName() {
  return `a_session_${PROJECT_ID}`;
}

function hashPin(pin) {
  const salt = crypto.randomBytes(16).toString('hex');
  const hash = crypto.scryptSync(pin, salt, 64).toString('hex');
  return `${salt}:${hash}`;
}

function verifyPin(pin, stored) {
  try {
    const [salt, hash] = stored.split(':');
    const check = crypto.scryptSync(pin, salt, 64).toString('hex');
    return hash === check;
  } catch {
    return false;
  }
}

const loginAttempts = new Map();
const MAX_ATTEMPTS = 5;
const LOCKOUT_MS = 15 * 60 * 1000;

function getAttemptKey(ip, username) {
  return `${ip}:${username}`;
}

function recordFailure(ip, username, db) {
  const key = getAttemptKey(ip, username);
  const now = Date.now();
  let rec = loginAttempts.get(key);
  if (!rec || rec.lockedUntil < now) {
    rec = { count: 0, lockedUntil: 0 };
  }
  rec.count++;
  if (rec.count >= MAX_ATTEMPTS) {
    rec.lockedUntil = now + LOCKOUT_MS;
    db.listRows({
      databaseId: DB_ID,
      tableId: 'users',
      queries: [Query.equal('username', username)],
    })
      .then(list => {
        if (list.rows[0]) {
          return db.updateRow({
            databaseId: DB_ID,
            tableId: 'users',
            rowId: list.rows[0].$id,
            data: { active: false },
          });
        }
      })
      .catch(() => {});
  }
  loginAttempts.set(key, rec);
  return rec;
}

function resetAttempts(ip, username) {
  loginAttempts.delete(getAttemptKey(ip, username));
}

function isLocked(ip, username) {
  const rec = loginAttempts.get(getAttemptKey(ip, username));
  return rec && rec.lockedUntil > Date.now();
}

async function getMasterAuth(db) {
  try {
    const svc = await getServiceConfig(db, 'master_auth');
    if (svc && svc.appwrite_user_id && svc.pin_hash) {
      return {
        appwrite_user_id: svc.appwrite_user_id,
        username: svc.username || 'admin',
        pin_hash: svc.pin_hash,
        role: svc.role || 'master',
        active: svc.active !== false,
        grant_id: svc.grant_id || '',
      };
    }
  } catch (err) {
    // fall through to legacy lookup
  }
  return null;
}

async function getPinUser(db, username) {
  if (username === 'admin') {
    const master = await getMasterAuth(db);
    if (master) return master;
  }

  const list = await db.listRows({
    databaseId: DB_ID,
    tableId: 'users',
    queries: [Query.equal('username', username)],
  });

  if (!list.rows.length) return null;
  const s = list.rows[0];
  return {
    appwrite_user_id: s.appwrite_user_id,
    username: s.username,
    pin_hash: s.pin_hash,
    role: s.role || 'slave',
    active: s.active !== false,
    grant_id: s.grant_id,
  };
}

async function isMasterUser(db, userId) {
  const master = await getMasterAuth(db);
  return master && master.appwrite_user_id === userId;
}

async function getCurrentUser(req) {
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

module.exports = async function main({ req, res, log, error }) {
  const preflight = handleOptions(req, res);
  if (preflight) return preflight;

  const path = req.path;
  const method = req.method;

  try {
    if (path === '/health' && method === 'GET') {
      return res.json({ status: 'ok', service: 'auth-pin' }, 200, corsHeaders(req.headers['origin'] || ''));
    }
    if (path === '/pin-login' && method === 'POST') {
      return await handlePinLogin(req, res, log, error);
    }
    if (path === '/set-credentials' && method === 'POST') {
      return await handleSetCredentials(req, res, log, error);
    }
    if (path === '/pin-reset/request' && method === 'POST') {
      return await handlePinResetRequest(req, res, log, error);
    }
    if (path === '/pin-reset/confirm' && method === 'POST') {
      return await handlePinResetConfirm(req, res, log, error);
    }
    if (path === '/register' && method === 'POST') {
      return await handleRegister(req, res, log, error);
    }
    return res.json({ error: 'Not found' }, 404, corsHeaders(req.headers['origin'] || ''));
  } catch (err) {
    error(String(err));
    return res.json({ error: 'Internal error', detail: err.message }, 500, corsHeaders(req.headers['origin'] || ''));
  }
};

// ─── POST /pin-login ────────────────────────────────────────────────

async function handlePinLogin(req, res, log, error) {
  const body = req.bodyJson || {};
  const username = String(body.username || '').trim();
  const pin = String(body.pin || '');
  const ip = req.headers['x-forwarded-for'] || 'unknown';

  if (!username || !pin) {
    return res.json({ error: 'Username and PIN required' }, 400, corsHeaders(req.headers['origin'] || ''));
  }

  if (isLocked(ip, username)) {
    return res.json({ error: 'Account locked due to too many failed attempts' }, 423, corsHeaders(req.headers['origin'] || ''));
  }

  const db = makeAdminDb();
  const user = await getPinUser(db, username);

  if (!user) {
    recordFailure(ip, username, db);
    return res.json({ error: 'Invalid credentials' }, 401, corsHeaders(req.headers['origin'] || ''));
  }

  if (!user.pin_hash) {
    return res.json({ error: 'PIN not set. Please complete onboarding.' }, 403, corsHeaders(req.headers['origin'] || ''));
  }

  if (!verifyPin(pin, user.pin_hash)) {
    recordFailure(ip, username, db);
    return res.json({ error: 'Invalid credentials' }, 401, corsHeaders(req.headers['origin'] || ''));
  }

  if (!user.active) {
    return res.json({ error: 'Account inactive or locked' }, 403, corsHeaders(req.headers['origin'] || ''));
  }

  resetAttempts(ip, username);

  const adminClient = makeAdminClient();
  const users = new Users(adminClient);
  const account = new Account(adminClient);
  const token = await users.createToken({ userId: user.appwrite_user_id });
  const session = await account.createSession({
    userId: user.appwrite_user_id,
    secret: token.secret,
  });

  const cookie = sessionCookie(cookieName(), session.secret);
  log(`PIN login success user=${user.appwrite_user_id} role=${user.role}`);

  return res.send(JSON.stringify({
    success: true,
    user_id: user.appwrite_user_id,
    role: user.role,
    grant_id: user.grant_id,
    username: user.username,
  }), 200, {
    'Content-Type': 'application/json',
    'Set-Cookie': cookie,
    ...corsHeaders(req.headers['origin'] || ''),
  });
}

// ─── POST /set-credentials ──────────────────────────────────────────

async function handleSetCredentials(req, res, log, error) {
  const user = await getCurrentUser(req);
  if (!user) {
    return res.json({ error: 'Unauthorized' }, 401, corsHeaders(req.headers['origin'] || ''));
  }

  const body = req.bodyJson || {};
  const username = String(body.username || '').trim();
  const pin = String(body.pin || '');

  if (!username || !pin) {
    return res.json({ error: 'Username and PIN required' }, 400, corsHeaders(req.headers['origin'] || ''));
  }

  if (username.length < 3 || username.length > 32) {
    return res.json({ error: 'Username must be 3-32 characters' }, 400, corsHeaders(req.headers['origin'] || ''));
  }

  if (!/^\d{4,6}$/.test(pin)) {
    return res.json({ error: 'PIN must be 4-6 digits' }, 400, corsHeaders(req.headers['origin'] || ''));
  }

  const db = makeAdminDb();

  // Master admin credentials live in service_config, not slave_accounts.
  if (await isMasterUser(db, user.$id)) {
    const body = {
      config_key: 'master_auth',
      config_value: JSON.stringify({
        appwrite_user_id: user.$id,
        username,
        pin_hash: hashPin(pin),
        role: 'master',
        active: true,
      }),
      description: 'Master admin authentication record',
      updated_at: new Date().toISOString(),
    };
    const existing = await db.listRows({
      databaseId: DB_ID,
      tableId: 'service_config',
      queries: [Query.equal('config_key', 'master_auth')],
    });
    if (existing.rows.length === 0) {
      await db.createRow({
        databaseId: DB_ID,
        tableId: 'service_config',
        rowId: ID.unique(),
        data: body,
      });
    } else {
      await db.updateRow({
        databaseId: DB_ID,
        tableId: 'service_config',
        rowId: existing.rows[0].$id,
        data: body,
      });
    }

    const users = makeAdminUsers();
    try {
      await users.updateName({ userId: user.$id, name: username });
    } catch (err) {
      log(`Failed to update master user name: ${err.message}`);
    }

    log(`Set master credentials user=${user.$id} username=${username}`);
    return res.json({ success: true, username }, 200, corsHeaders(req.headers['origin'] || ''));
  }

  const existing = await db.listRows({
    databaseId: DB_ID,
    tableId: 'users',
    queries: [Query.equal('username', username)],
  });
  if (existing.rows.length > 0) {
    const other = existing.rows[0];
    if (other.appwrite_user_id !== user.$id) {
      return res.json({ error: 'Username already taken' }, 409, corsHeaders(req.headers['origin'] || ''));
    }
  }

  const ownList = await db.listRows({
    databaseId: DB_ID,
    tableId: 'users',
    queries: [Query.equal('appwrite_user_id', user.$id)],
  });

  if (ownList.rows.length === 0) {
    return res.json({ error: 'Account record not found' }, 404, corsHeaders(req.headers['origin'] || ''));
  }

  const row = ownList.rows[0];
  await db.updateRow({
    databaseId: DB_ID,
    tableId: 'users',
    rowId: row.$id,
    data: {
      username,
      pin_hash: hashPin(pin),
      active: true,
    },
  });

  const users = makeAdminUsers();
  try {
    await users.updateName({ userId: user.$id, name: username });
  } catch (err) {
    log(`Failed to update user name: ${err.message}`);
  }

  log(`Set credentials user=${user.$id} username=${username}`);
  return res.json({ success: true, username }, 200, corsHeaders(req.headers['origin'] || ''));
}

// ─── POST /pin-reset/request ────────────────────────────────────────

async function handlePinResetRequest(req, res, log, error) {
  const body = req.bodyJson || {};
  const email = String(body.email || '').trim().toLowerCase();

  if (!email) {
    return res.json({ error: 'Email required' }, 400, corsHeaders(req.headers['origin'] || ''));
  }

  const db = makeAdminDb();
  let list = await db.listRows({
    databaseId: DB_ID,
    tableId: 'users',
    queries: [Query.equal('email', email)],
  });

  let target = list.rows[0] || null;

  // If no slave account matches, check master admin email in service_config.
  if (!target) {
    const master = await getMasterAuth(db);
    if (master && master.email && master.email.toLowerCase() === email) {
      target = master;
    }
  }

  if (!target) {
    return res.json({ success: true, message: 'If the email exists, a reset link has been sent.' }, 200, corsHeaders(req.headers['origin'] || ''));
  }

  const resetToken = generateToken();
  const expiresAt = new Date(Date.now() + 60 * 60 * 1000).toISOString();

  await db.createRow({
    databaseId: DB_ID,
    tableId: 'ephemeral_tokens',
    rowId: ID.unique(),
    data: {
      token: resetToken,
      kind: 'pin_reset',
      user_id: target.appwrite_user_id,
      payload: JSON.stringify({ grant_id: target.grant_id || '' }),
      expires_at: expiresAt,
    },
  });

  log(`PIN reset requested for ${email}`);

  if (RESEND_API_KEY) {
    try {
      await sendResetEmail(email, resetToken);
      log(`PIN reset email sent to ${email}`);
    } catch (err) {
      error(`Failed to send PIN reset email to ${email}: ${err.message}`);
      // Still return opaque success to avoid leaking whether the email exists.
    }
  } else {
    log(`RESEND_API_KEY not configured; PIN reset email not sent to ${email}`);
  }

  return res.json({ success: true, message: 'If the email exists, a reset link has been sent.' }, 200, corsHeaders(req.headers['origin'] || ''));
}

// ─── Email helper ───────────────────────────────────────────────────

async function sendResetEmail(email, resetToken) {
  const resetUrl = `${PIN_RESET_BASE_URL}/#/pin-reset?token=${encodeURIComponent(resetToken)}`;
  const res = await fetch('https://api.resend.com/emails', {
    method: 'POST',
    headers: {
      'Authorization': `Bearer ${RESEND_API_KEY}`,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      from: `SSFX <${RESEND_FROM_EMAIL}>`,
      to: email,
      subject: 'Reset your SSFX PIN',
      html: `<p>Click the link below to reset your SSFX PIN. This link expires in 1 hour.</p><p><a href="${resetUrl}">${resetUrl}</a></p><p>If you did not request this reset, you can ignore this email.</p>`,
      text: `Reset your SSFX PIN: ${resetUrl}\n\nThis link expires in 1 hour. If you did not request this reset, you can ignore this email.`,
    }),
  });

  if (!res.ok) {
    const text = await res.text().catch(() => '');
    throw new Error(`Resend API error ${res.status}: ${text}`);
  }
}

// ─── POST /pin-reset/confirm ────────────────────────────────────────

async function handlePinResetConfirm(req, res, log, error) {
  const body = req.bodyJson || {};
  const token = String(body.token || '');
  const newPin = String(body.new_pin || '');

  if (!token || !newPin) {
    return res.json({ error: 'Token and new PIN required' }, 400, corsHeaders(req.headers['origin'] || ''));
  }

  if (!/^\d{4,6}$/.test(newPin)) {
    return res.json({ error: 'PIN must be 4-6 digits' }, 400, corsHeaders(req.headers['origin'] || ''));
  }

  const db = makeAdminDb();
  const list = await db.listRows({
    databaseId: DB_ID,
    tableId: 'ephemeral_tokens',
    queries: [
      Query.equal('token', token),
      Query.equal('kind', 'pin_reset'),
    ],
  });

  if (list.rows.length === 0) {
    return res.json({ error: 'Invalid or expired token' }, 400, corsHeaders(req.headers['origin'] || ''));
  }

  const et = list.rows[0];

  if (new Date(et.expires_at) < new Date()) {
    await db.deleteRow({
      databaseId: DB_ID,
      tableId: 'ephemeral_tokens',
      rowId: et.$id,
    });
    return res.json({ error: 'Token expired' }, 400, corsHeaders(req.headers['origin'] || ''));
  }

  await db.deleteRow({
    databaseId: DB_ID,
    tableId: 'ephemeral_tokens',
    rowId: et.$id,
  });

  // Master admin PIN is stored in service_config.
  if (await isMasterUser(db, et.user_id)) {
    const master = await getMasterAuth(db);
    const body = {
      config_key: 'master_auth',
      config_value: JSON.stringify({
        appwrite_user_id: master.appwrite_user_id,
        username: master.username,
        email: master.email || '',
        pin_hash: hashPin(newPin),
        role: 'master',
        active: true,
      }),
      description: 'Master admin authentication record',
      updated_at: new Date().toISOString(),
    };
    const existing = await db.listRows({
      databaseId: DB_ID,
      tableId: 'service_config',
      queries: [Query.equal('config_key', 'master_auth')],
    });
    if (existing.rows.length === 0) {
      await db.createRow({
        databaseId: DB_ID,
        tableId: 'service_config',
        rowId: ID.unique(),
        data: body,
      });
    } else {
      await db.updateRow({
        databaseId: DB_ID,
        tableId: 'service_config',
        rowId: existing.rows[0].$id,
        data: body,
      });
    }

    log(`PIN reset confirmed user=${et.user_id} role=master`);
    return res.json({ success: true }, 200, corsHeaders(req.headers['origin'] || ''));
  }

  const slaveList = await db.listRows({
    databaseId: DB_ID,
    tableId: 'users',
    queries: [Query.equal('appwrite_user_id', et.user_id)],
  });

  if (slaveList.rows.length === 0) {
    return res.json({ error: 'User not found' }, 404, corsHeaders(req.headers['origin'] || ''));
  }

  await db.updateRow({
    databaseId: DB_ID,
    tableId: 'users',
    rowId: slaveList.rows[0].$id,
    data: {
      pin_hash: hashPin(newPin),
      active: true,
    },
  });

  log(`PIN reset confirmed user=${et.user_id}`);
  return res.json({ success: true }, 200, corsHeaders(req.headers['origin'] || ''));
}

// ─── POST /register ─────────────────────────────────────────────────

async function handleRegister(req, res, log, error) {
  const body = req.bodyJson || {};
  const username = String(body.username || '').trim();
  const pin = String(body.pin || '');

  if (!username || !pin) {
    return res.json({ error: 'Username and PIN required' }, 400, corsHeaders(req.headers['origin'] || ''));
  }

  if (username.length < 3 || username.length > 32) {
    return res.json({ error: 'Username must be 3-32 characters' }, 400, corsHeaders(req.headers['origin'] || ''));
  }

  if (!/^\d{4,6}$/.test(pin)) {
    return res.json({ error: 'PIN must be 4-6 digits' }, 400, corsHeaders(req.headers['origin'] || ''));
  }

  const db = makeAdminDb();

  // Check if username already exists
  const existing = await db.listRows({
    databaseId: DB_ID,
    tableId: 'users',
    queries: [Query.equal('username', username)],
  });
  if (existing.rows.length > 0) {
    return res.json({ error: 'Username already taken' }, 409, corsHeaders(req.headers['origin'] || ''));
  }

  const users = makeAdminUsers();
  const generatedEmail = `${username}_${generateToken().slice(0, 8)}@local.slwp`;
  const generatedPassword = generateToken() + generateToken();

  let appwriteUserId;
  try {
    const user = await users.createArgon2User({
      userId: ID.unique(),
      email: generatedEmail,
      password: generatedPassword,
      name: username,
    });
    appwriteUserId = user.$id;
  } catch (err) {
    error(`User creation failed: ${err.message}`);
    return res.json({ error: 'Failed to create account' }, 500, corsHeaders(req.headers['origin'] || ''));
  }

  try {
    await db.createRow({
      databaseId: DB_ID,
      tableId: 'users',
      rowId: ID.unique(),
      data: {
        appwrite_user_id: appwriteUserId,
        username,
        pin_hash: hashPin(pin),
        role: 'slave',
        grant_id: '',
        access_token_enc: '',
        refresh_token_enc: '',
        access_token_expires_at: '',
        ctrader_account_ids: '',
        selected_account_id: '',
        status: 'pending',
        active: true,
        email: '',
        last_heartbeat_at: null,
      },
      permissions: [
        Permission.read(Role.user(appwriteUserId)),
        Permission.update(Role.user(appwriteUserId)),
        Permission.read(Role.users()),
      ],
    });
  } catch (err) {
    error(`Slave row creation failed: ${err.message}`);
    // Clean up Appwrite user
    try { await users.delete(appwriteUserId); } catch {}
    return res.json({ error: 'Failed to create account record' }, 500, corsHeaders(req.headers['origin'] || ''));
  }

  // Create session immediately so user is logged in
  const adminClient = makeAdminClient();
  const account = new Account(adminClient);
  const token = await users.createToken({ userId: appwriteUserId });
  const session = await account.createSession({
    userId: appwriteUserId,
    secret: token.secret,
  });

  const cookie = sessionCookie(cookieName(), session.secret);
  log(`Registration success user=${appwriteUserId} username=${username}`);

  return res.send(JSON.stringify({
    success: true,
    user_id: appwriteUserId,
    username,
    role: 'slave',
  }), 200, {
    'Content-Type': 'application/json',
    'Set-Cookie': cookie,
    ...corsHeaders(req.headers['origin'] || ''),
  });
}
