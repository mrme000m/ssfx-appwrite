/**
 * ctrader-token-refresh-worker — Scheduled cron + on-demand HTTP
 * Rotates near-expiry active grants and sweeps stale ephemeral tokens
 */

const {
  makeAdminDb,
  decrypt,
  encrypt,
  acquireGrantLock,
  releaseGrantLock,
  refreshCtraderToken,
  Query,
} = require('../../_shared');

const DB_ID = process.env.CTRADER_AUTH_DATABASE_ID;
const BUFFER_HOURS = parseFloat(process.env.REFRESH_BUFFER_HOURS || '48');

module.exports = async function main({ req, res, log, error }) {
  try {
    const results = {
      rotated: 0,
      failed: 0,
      skipped: 0,
      swept: 0,
      errors: [],
    };

    const db = makeAdminDb();
    const now = new Date();
    const expiryThreshold = new Date(now.getTime() + BUFFER_HOURS * 60 * 60 * 1000).toISOString();

    // Find active grants expiring soon
    const expiringList = await db.listDocuments(DB_ID, 'slave_accounts', [
      Query.equal('status', 'active'),
      Query.lessThan('access_token_expires_at', expiryThreshold),
      Query.limit(100),
    ]);

    log(`Found ${expiringList.documents.length} grants needing refresh`);

    for (const slave of expiringList.documents) {
      const rotated = await rotateOne(db, slave, log, error);
      if (rotated === true) results.rotated++;
      else if (rotated === false) results.failed++;
      else results.skipped++;
    }

    // Sweep old ephemeral tokens
    const staleTokenCutoff = new Date(now.getTime() - 24 * 60 * 60 * 1000).toISOString();
    try {
      const staleList = await db.listDocuments(DB_ID, 'ephemeral_tokens', [
        Query.lessThan('expires_at', staleTokenCutoff),
        Query.limit(100),
      ]);
      for (const token of staleList.documents) {
        try {
          await db.deleteDocument(DB_ID, 'ephemeral_tokens', token.$id);
          results.swept++;
        } catch (e) {
          // ignore individual delete errors
        }
      }
      log(`Swept ${results.swept} stale ephemeral tokens`);
    } catch (e) {
      error(`Token sweep failed: ${e.message}`);
    }

    return res.json({
      success: true,
      processed: expiringList.documents.length,
      ...results,
    });
  } catch (err) {
    error(String(err));
    return res.json({ error: 'Worker failed', detail: err.message }, 500);
  }
};

async function rotateOne(db, slave, log, error) {
  const grantId = slave.grant_id;

  if (!slave.refresh_token_enc) {
    log(`Skip ${grantId}: no refresh token`);
    return null; // skipped
  }

  const acquired = await acquireGrantLock(db, grantId, 'cron', 30000);
  if (!acquired) {
    log(`Skip ${grantId}: could not acquire lock`);
    return null; // skipped
  }

  try {
    const refreshToken = decrypt(slave.refresh_token_enc);
    const tokenData = await refreshCtraderToken(refreshToken);
    const { access_token, refresh_token, expires_in } = tokenData;
    const newExpiresAt = new Date(Date.now() + (expires_in || 3600) * 1000).toISOString();

    const newAccessEnc = encrypt(access_token);
    const newRefreshEnc = refresh_token ? encrypt(refresh_token) : slave.refresh_token_enc;

    await db.updateDocument(DB_ID, 'slave_accounts', slave.$id, {
      access_token_enc: newAccessEnc,
      refresh_token_enc: newRefreshEnc,
      access_token_expires_at: newExpiresAt,
    });

    log(`Rotated grant=${grantId} new_expires=${newExpiresAt}`);
    return true;
  } catch (err) {
    error(`Rotation failed for ${grantId}: ${err.message}`);
    if (err.status === 400 || err.status === 401) {
      await db.updateDocument(DB_ID, 'slave_accounts', slave.$id, {
        status: 'reauth_required',
      });
    }
    return false;
  } finally {
    await releaseGrantLock(db, grantId);
  }
}
