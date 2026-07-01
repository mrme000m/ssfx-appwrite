/**
 * ctrader-internal — server-to-server for Python backends
 * POST /internal/ctrader/refresh
 * GET /internal/grant/latest
 * POST /internal/grant/:grant_id/accounts
 * Gated by x-internal-key header
 */

const {
  makeAdminClient,
  makeAdminDb,
  decrypt,
  encrypt,
  acquireGrantLock,
  releaseGrantLock,
  refreshCtraderToken,
  Query,
} = require('../../_shared');

const DB_ID = process.env.CTRADER_AUTH_DATABASE_ID;
const INTERNAL_KEY = process.env.INTERNAL_API_KEY;

function checkInternalKey(req) {
  const key = req.headers['x-internal-key'] || '';
  return key === INTERNAL_KEY;
}

module.exports = async function main({ req, res, log, error }) {
  const path = req.path;
  const method = req.method;

  try {
    if (!checkInternalKey(req)) {
      return res.json({ error: 'Unauthorized' }, 401);
    }

    if (path === '/internal/ctrader/refresh' && method === 'POST') {
      return await handleRefresh(req, res, log, error);
    }
    if (path === '/internal/grant/latest' && method === 'GET') {
      return await handleGrantLatest(req, res, log, error);
    }
    if (path.startsWith('/internal/grant/') && path.endsWith('/accounts') && method === 'POST') {
      return await handleGrantAccounts(req, res, log, error);
    }

    return res.json({ error: 'Not found' }, 404);
  } catch (err) {
    error(String(err));
    return res.json({ error: 'Internal error', detail: err.message }, 500);
  }
};

// ─── POST /internal/ctrader/refresh ─────────────────────────────────

async function handleRefresh(req, res, log, error) {
  const body = req.bodyJson || {};
  const grantId = String(body.grantId || '');

  if (!grantId) {
    return res.json({ error: 'grantId required' }, 400);
  }

  const db = makeAdminDb();

  // Load slave row by grant_id
  const list = await db.listDocuments(DB_ID, 'slave_accounts', [
    Query.equal('grant_id', grantId),
  ]);

  if (list.documents.length === 0) {
    return res.json({ error: 'Grant not found' }, 404);
  }

  const slave = list.documents[0];

  if (slave.status !== 'active') {
    return res.json({ error: `Grant status is ${slave.status}` }, 403);
  }

  // Check if cached token is still fresh (buffer 5 min)
  const now = Date.now();
  const expiresAt = slave.access_token_expires_at
    ? new Date(slave.access_token_expires_at).getTime()
    : 0;

  if (expiresAt > now + 5 * 60 * 1000 && slave.access_token_enc) {
    try {
      const accessToken = decrypt(slave.access_token_enc);
      return res.json({
        access_token: accessToken,
        expires_at: slave.access_token_expires_at,
        refreshed: false,
      });
    } catch {
      // decryption failed, fall through to refresh
    }
  }

  // Need to refresh
  if (!slave.refresh_token_enc) {
    return res.json({ error: 'No refresh token available' }, 403);
  }

  const acquired = await acquireGrantLock(db, grantId, 'refresh', 30000);
  if (!acquired) {
    return res.json({ error: 'Grant locked by another refresh' }, 423);
  }

  try {
    const refreshToken = decrypt(slave.refresh_token_enc);
    const tokenData = await refreshCtraderToken(refreshToken);
    const { access_token, refresh_token, expires_in } = tokenData;
    const newExpiresAt = new Date(Date.now() + (expires_in || 3600) * 1000).toISOString();

    // Re-encrypt tokens
    const newAccessEnc = encrypt(access_token);
    const newRefreshEnc = refresh_token ? encrypt(refresh_token) : slave.refresh_token_enc;

    await db.updateDocument(DB_ID, 'slave_accounts', slave.$id, {
      access_token_enc: newAccessEnc,
      refresh_token_enc: newRefreshEnc,
      access_token_expires_at: newExpiresAt,
    });

    log(`Refreshed grant=${grantId} new_expires=${newExpiresAt}`);

    return res.json({
      access_token: access_token,
      expires_at: newExpiresAt,
      refreshed: true,
    });
  } catch (err) {
    error(`Refresh failed for ${grantId}: ${err.message}`);
    // If cTrader returns 400/401, mark as reauth_required
    if (err.status === 400 || err.status === 401) {
      await db.updateDocument(DB_ID, 'slave_accounts', slave.$id, {
        status: 'reauth_required',
      });
      return res.json({ error: 'Refresh token invalid, re-authentication required' }, 401);
    }
    return res.json({ error: 'cTrader refresh failed', detail: err.message }, 502);
  } finally {
    await releaseGrantLock(db, grantId);
  }
}

// ─── GET /internal/grant/latest ─────────────────────────────────────

async function handleGrantLatest(req, res, log, error) {
  const userId = String(req.query.user_id || '');
  if (!userId) {
    return res.json({ error: 'user_id required' }, 400);
  }

  const db = makeAdminDb();
  const list = await db.listDocuments(DB_ID, 'slave_accounts', [
    Query.equal('appwrite_user_id', userId),
    Query.equal('status', 'active'),
    Query.orderDesc('$updatedAt'),
    Query.limit(1),
  ]);

  if (list.documents.length === 0) {
    return res.json({ grant_id: null }, 200);
  }

  return res.json({
    grant_id: list.documents[0].grant_id,
    updated_at: list.documents[0].$updatedAt,
  });
}

// ─── POST /internal/grant/:grant_id/accounts ────────────────────────

async function handleGrantAccounts(req, res, log, error) {
  // Parse path: /internal/grant/<grant_id>/accounts
  const parts = req.path.split('/');
  const grantId = parts[3]; // ['', 'internal', 'grant', '<id>', 'accounts']

  if (!grantId) {
    return res.json({ error: 'grant_id required in path' }, 400);
  }

  const body = req.bodyJson || {};
  const accountIds = Array.isArray(body.accountIds) ? body.accountIds : [];
  const selectedAccountId = String(body.selectedAccountId || '');

  const db = makeAdminDb();
  const list = await db.listDocuments(DB_ID, 'slave_accounts', [
    Query.equal('grant_id', grantId),
  ]);

  if (list.documents.length === 0) {
    return res.json({ error: 'Grant not found' }, 404);
  }

  const updates = {
    ctrader_account_ids: accountIds.join(','),
    last_heartbeat_at: new Date().toISOString(),
  };
  if (selectedAccountId) {
    updates.selected_account_id = selectedAccountId;
  }

  await db.updateDocument(DB_ID, 'slave_accounts', list.documents[0].$id, updates);

  log(`Updated accounts grant=${grantId} accounts=${accountIds.length}`);
  return res.json({ success: true, grant_id: grantId, accounts: accountIds.length });
}
