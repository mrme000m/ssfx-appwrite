/**
 * ctrader-pin-auth — PIN login, set-credentials, PIN reset
 * Runtime: node-22, entrypoint: src/main.js
 */

const crypto = require('crypto');
const { Account, Users } = require('node-appwrite');
const {
  makeAdminClient,
  makeAdminDb,
  makeAdminUsers,
  generateToken,
  ID,
  Query,
  Permission,
  Role,
} = require('../../_shared');

const PROJECT_ID = process.env.APPWRITE_PROJECT_ID;
const DB_ID = process.env.CTRADER_AUTH_DATABASE_ID;
const BCRYPT_SALT_ROUNDS = parseInt(process.env.BCRYPT_SALT_ROUNDS || '10', 10);

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

// In-memory rate limiting
const loginAttempts = new Map(); // key: "ip:username" -> { count, lockedUntil }
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
    // Flip active=false on the slave row
    db.listDocuments(DB_ID, 'slave_accounts', [Query.equal('username', username)])
      .then(list => {
        if (list.documents[0]) {
          return db.updateDocument(DB_ID, 'slave_accounts', list.documents[0].$id, { active: false });
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

// Helper: get current user from session cookie
async function getCurrentUser(req) {
  const cookie = req.headers['cookie'] || '';
  const match = cookie.match(new RegExp(`${cookieName()}=([^;]+)`));
  if (!match) return null;
  const client = makeAdminClient();
  const account = new Account(client);
  try {
    client.setSession(match[1]);
    return await account.get();
  } catch {
    return null;
  }
}

module.exports = async function main({ req, res, log, error }) {
  const path = req.path;
  const method = req.method;

  try {
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
    return res.json({ error: 'Not found' }, 404);
  } catch (err) {
    error(String(err));
    return res.json({ error: 'Internal error', detail: err.message }, 500);
  }
};

// ─── POST /pin-login ────────────────────────────────────────────────

async function handlePinLogin(req, res, log, error) {
  const body = req.bodyJson || {};
  const username = String(body.username || '').trim();
  const pin = String(body.pin || '');
  const ip = req.headers['x-forwarded-for'] || 'unknown';

  if (!username || !pin) {
    return res.json({ error: 'Username and PIN required' }, 400);
  }

  if (isLocked(ip, username)) {
    return res.json({ error: 'Account locked due to too many failed attempts' }, 423);
  }

  const db = makeAdminDb();
  const list = await db.listDocuments(DB_ID, 'slave_accounts', [
    Query.equal('username', username),
  ]);

  if (list.documents.length === 0) {
    recordFailure(ip, username, db);
    return res.json({ error: 'Invalid credentials' }, 401);
  }

  const slave = list.documents[0];

  if (!slave.pin_hash) {
    return res.json({ error: 'PIN not set. Please complete onboarding.' }, 403);
  }

  if (!verifyPin(pin, slave.pin_hash)) {
    recordFailure(ip, username, db);
    return res.json({ error: 'Invalid credentials' }, 401);
  }

  if (!slave.active) {
    return res.json({ error: 'Account inactive or locked' }, 403);
  }

  resetAttempts(ip, username);

  // Create session
  const adminClient = makeAdminClient();
  const users = new Users(adminClient);
  const account = new Account(adminClient);
  const token = await users.createToken({ userId: slave.appwrite_user_id });
  const session = await account.createSession({
    userId: slave.appwrite_user_id,
    secret: token.secret,
  });

  const cookie = `${cookieName()}=${session.secret}; HttpOnly; Secure; SameSite=Strict; Path=/; Max-Age=604800`;
  log(`PIN login success user=${slave.appwrite_user_id} role=${slave.role}`);

  return res.send(JSON.stringify({
    success: true,
    user_id: slave.appwrite_user_id,
    role: slave.role,
    grant_id: slave.grant_id,
  }), 200, {
    'Content-Type': 'application/json',
    'Set-Cookie': cookie,
  });
}

// ─── POST /set-credentials ──────────────────────────────────────────

async function handleSetCredentials(req, res, log, error) {
  const user = await getCurrentUser(req);
  if (!user) {
    return res.json({ error: 'Unauthorized' }, 401);
  }

  const body = req.bodyJson || {};
  const username = String(body.username || '').trim();
  const pin = String(body.pin || '');

  if (!username || !pin) {
    return res.json({ error: 'Username and PIN required' }, 400);
  }

  if (username.length < 3 || username.length > 32) {
    return res.json({ error: 'Username must be 3-32 characters' }, 400);
  }

  if (!/^\d{4,6}$/.test(pin)) {
    return res.json({ error: 'PIN must be 4-6 digits' }, 400);
  }

  const db = makeAdminDb();

  // Check username uniqueness (excluding self)
  const existing = await db.listDocuments(DB_ID, 'slave_accounts', [
    Query.equal('username', username),
  ]);
  if (existing.documents.length > 0) {
    const other = existing.documents[0];
    if (other.appwrite_user_id !== user.$id) {
      return res.json({ error: 'Username already taken' }, 409);
    }
  }

  // Find own row
  const ownList = await db.listDocuments(DB_ID, 'slave_accounts', [
    Query.equal('appwrite_user_id', user.$id),
  ]);

  if (ownList.documents.length === 0) {
    return res.json({ error: 'Account record not found' }, 404);
  }

  const row = ownList.documents[0];
  await db.updateDocument(DB_ID, 'slave_accounts', row.$id, {
    username,
    pin_hash: hashPin(pin),
  });

  // Update Appwrite user name
  const users = makeAdminUsers();
  try {
    await users.updateName({ userId: user.$id, name: username });
  } catch (err) {
    log(`Failed to update user name: ${err.message}`);
  }

  log(`Set credentials user=${user.$id} username=${username}`);
  return res.json({ success: true, username });
}

// ─── POST /pin-reset/request ────────────────────────────────────────

async function handlePinResetRequest(req, res, log, error) {
  const body = req.bodyJson || {};
  const email = String(body.email || '').trim().toLowerCase();

  if (!email) {
    return res.json({ error: 'Email required' }, 400);
  }

  const db = makeAdminDb();
  const list = await db.listDocuments(DB_ID, 'slave_accounts', [
    Query.equal('email', email),
  ]);

  if (list.documents.length === 0) {
    // Don't reveal whether email exists
    return res.json({ success: true, message: 'If the email exists, a reset link has been sent.' });
  }

  const slave = list.documents[0];
  const resetToken = generateToken();
  const expiresAt = new Date(Date.now() + 60 * 60 * 1000).toISOString(); // 1 hour

  await db.createDocument(DB_ID, 'ephemeral_tokens', ID.unique(), {
    token: resetToken,
    kind: 'pin_reset',
    user_id: slave.appwrite_user_id,
    payload: JSON.stringify({ grant_id: slave.grant_id }),
    expires_at: expiresAt,
  });

  // TODO: send email via Appwrite Messaging or Resend
  // For v1, log the token (master can read logs or we show on dashboard)
  log(`PIN reset token for ${email}: ${resetToken} (expires ${expiresAt})`);

  return res.json({ success: true, message: 'If the email exists, a reset link has been sent.' });
}

// ─── POST /pin-reset/confirm ────────────────────────────────────────

async function handlePinResetConfirm(req, res, log, error) {
  const body = req.bodyJson || {};
  const token = String(body.token || '');
  const newPin = String(body.new_pin || '');

  if (!token || !newPin) {
    return res.json({ error: 'Token and new PIN required' }, 400);
  }

  if (!/^\d{4,6}$/.test(newPin)) {
    return res.json({ error: 'PIN must be 4-6 digits' }, 400);
  }

  const db = makeAdminDb();
  const list = await db.listDocuments(DB_ID, 'ephemeral_tokens', [
    Query.equal('token', token),
    Query.equal('kind', 'pin_reset'),
  ]);

  if (list.documents.length === 0) {
    return res.json({ error: 'Invalid or expired token' }, 400);
  }

  const et = list.documents[0];

  // Check expiry
  if (new Date(et.expires_at) < new Date()) {
    await db.deleteDocument(DB_ID, 'ephemeral_tokens', et.$id);
    return res.json({ error: 'Token expired' }, 400);
  }

  // Consume token
  await db.deleteDocument(DB_ID, 'ephemeral_tokens', et.$id);

  // Update PIN
  const slaveList = await db.listDocuments(DB_ID, 'slave_accounts', [
    Query.equal('appwrite_user_id', et.user_id),
  ]);

  if (slaveList.documents.length === 0) {
    return res.json({ error: 'User not found' }, 404);
  }

  await db.updateDocument(DB_ID, 'slave_accounts', slaveList.documents[0].$id, {
    pin_hash: hashPin(newPin),
    active: true,
  });

  log(`PIN reset confirmed user=${et.user_id}`);
  return res.json({ success: true });
}
