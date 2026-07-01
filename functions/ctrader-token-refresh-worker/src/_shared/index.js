/**
 * Shared helpers for cTrader auth functions
 * AES-GCM-256, grant locks, Appwrite client factory, cTrader token exchange
 */

const crypto = require('crypto');
const { Client, Databases, ID, Query, Users, Permission, Role } = require('node-appwrite');

// ─── Crypto ─────────────────────────────────────────────────────────

function getEncKey() {
  const k = process.env.TOKEN_ENCRYPTION_KEY;
  if (!k || k.length < 32) throw new Error('TOKEN_ENCRYPTION_KEY missing or too short');
  return crypto.createHash('sha256').update(k).digest();
}

function encrypt(plaintext) {
  const iv = crypto.randomBytes(12);
  const cipher = crypto.createCipheriv('aes-256-gcm', getEncKey(), iv);
  const enc = Buffer.concat([cipher.update(plaintext, 'utf8'), cipher.final()]);
  const tag = cipher.getAuthTag();
  return Buffer.concat([iv, tag, enc]).toString('base64');
}

function decrypt(ciphertext) {
  const buf = Buffer.from(ciphertext, 'base64');
  const iv = buf.slice(0, 12);
  const tag = buf.slice(12, 28);
  const enc = buf.slice(28);
  const decipher = crypto.createDecipheriv('aes-256-gcm', getEncKey(), iv);
  decipher.setAuthTag(tag);
  return Buffer.concat([decipher.update(enc), decipher.final()]).toString('utf8');
}

// ─── HMAC state signing ─────────────────────────────────────────────

function signState(userId, secret) {
  const payload = `${userId}:${Date.now()}`;
  const sig = crypto.createHmac('sha256', secret).update(payload).digest('hex');
  return Buffer.from(`${payload}:${sig}`).toString('base64url');
}

function verifyState(stateB64, secret, maxAgeMs = 600_000) {
  try {
    const raw = Buffer.from(stateB64, 'base64url').toString('utf8');
    const [userId, tsStr, sig] = raw.split(':');
    if (!userId || !tsStr || !sig) return null;
    const ts = parseInt(tsStr, 10);
    if (Date.now() - ts > maxAgeMs) return null;
    const expected = crypto.createHmac('sha256', secret).update(`${userId}:${tsStr}`).digest('hex');
    if (!crypto.timingSafeEqual(Buffer.from(sig, 'hex'), Buffer.from(expected, 'hex'))) return null;
    return userId;
  } catch {
    return null;
  }
}

// ─── Appwrite client ────────────────────────────────────────────────

function makeAdminClient() {
  const client = new Client()
    .setEndpoint(process.env.APPWRITE_ENDPOINT || 'https://sgp.cloud.appwrite.io/v1')
    .setProject(process.env.APPWRITE_PROJECT_ID)
    .setKey(process.env.APPWRITE_API_KEY);
  return client;
}

function makeAdminDb() {
  return new Databases(makeAdminClient());
}

function makeAdminUsers() {
  return new Users(makeAdminClient());
}

// ─── Grant locks (TablesDB row-level) ───────────────────────────────

async function acquireGrantLock(db, grantId, lockContext = 'refresh', timeoutMs = 30000) {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    try {
      await db.createDocument(
        process.env.CTRADER_AUTH_DATABASE_ID,
        'grant_locks',
        grantId,
        { locked_at: new Date().toISOString(), locked_by: lockContext }
      );
      return true;
    } catch (e) {
      if (e.code === 409) {
        await sleep(500);
        continue;
      }
      throw e;
    }
  }
  return false;
}

async function releaseGrantLock(db, grantId) {
  try {
    await db.deleteDocument(
      process.env.CTRADER_AUTH_DATABASE_ID,
      'grant_locks',
      grantId
    );
  } catch {
    // ignore not-found or race
  }
}

// ─── cTrader token exchange ─────────────────────────────────────────

async function exchangeCtraderCode(code) {
  const body = new URLSearchParams({
    grant_type: 'authorization_code',
    client_id: process.env.CTRADER_CLIENT_ID,
    client_secret: process.env.CTRADER_CLIENT_SECRET,
    code,
    redirect_uri: process.env.CTRADER_REDIRECT_URI,
  });

  const res = await fetch('https://id.ctrader.com/Apps/token', {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: body.toString(),
  });

  if (!res.ok) {
    const text = await res.text().catch(() => '');
    const err = new Error(`cTrader token exchange failed: ${res.status} ${text}`);
    err.status = res.status;
    throw err;
  }

  return res.json();
}

async function refreshCtraderToken(refreshToken) {
  const body = new URLSearchParams({
    grant_type: 'refresh_token',
    client_id: process.env.CTRADER_CLIENT_ID,
    client_secret: process.env.CTRADER_CLIENT_SECRET,
    refresh_token: refreshToken,
  });

  const res = await fetch('https://id.ctrader.com/Apps/token', {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: body.toString(),
  });

  if (!res.ok) {
    const text = await res.text().catch(() => '');
    const err = new Error(`cTrader refresh failed: ${res.status} ${text}`);
    err.status = res.status;
    throw err;
  }

  return res.json();
}

// ─── Helpers ────────────────────────────────────────────────────────

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

function jsonResponse(body, status = 200, extraHeaders = {}) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json', ...extraHeaders },
  });
}

function redirectResponse(url, status = 302) {
  return new Response(null, { status, headers: { Location: url } });
}

function generateGrantId() {
  return 'grant_' + crypto.randomBytes(16).toString('hex');
}

function generateToken() {
  return crypto.randomBytes(32).toString('hex');
}

// ─── Exports ────────────────────────────────────────────────────────

module.exports = {
  encrypt,
  decrypt,
  signState,
  verifyState,
  makeAdminClient,
  makeAdminDb,
  makeAdminUsers,
  acquireGrantLock,
  releaseGrantLock,
  exchangeCtraderCode,
  refreshCtraderToken,
  sleep,
  jsonResponse,
  redirectResponse,
  generateGrantId,
  generateToken,
  ID,
  Query,
  Permission,
  Role,
};
