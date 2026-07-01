const { Client, Users, Databases, ID, Query, Permission, Role } = require('node-appwrite');
const crypto = require('crypto');
const fs = require('fs');
const path = require('path');

async function main() {
  // Load env from root .env
  const envPath = path.join(__dirname, '..', '.env');
  if (fs.existsSync(envPath)) {
    const env = fs.readFileSync(envPath, 'utf8');
    for (const line of env.split('\n')) {
      const match = line.match(/^([A-Z_]+)=(.*)$/);
      if (match) process.env[match[1]] = match[2];
    }
  }

  // Load config
  const configPath = path.join(__dirname, 'config.yml');
  if (!fs.existsSync(configPath)) {
    console.error(`Error: ${configPath} not found`);
    process.exit(1);
  }

  const yaml = fs.readFileSync(configPath, 'utf8');
  const emailMatch = yaml.match(/email:\s*"?([^"\n]+)"?/);
  if (!emailMatch) {
    console.error('Error: master.email not found in config.yml');
    process.exit(1);
  }
  const masterEmail = emailMatch[1].trim();

  console.log('[init] Setting up master admin account...');
  console.log('[init] Email:', masterEmail);

  // Prompt for PIN
  const pin = await promptPin();
  if (!/^\d{4,6}$/.test(pin)) {
    console.error('Error: PIN must be 4-6 digits');
    process.exit(1);
  }

  // Setup Appwrite client
  const client = new Client()
    .setEndpoint(process.env.APPWRITE_ENDPOINT || 'https://sgp.cloud.appwrite.io/v1')
    .setProject(process.env.APPWRITE_PROJECT_ID)
    .setKey(process.env.APPWRITE_API_KEY);

  const users = new Users(client);
  const db = new Databases(client);

  const PROJECT_ID = process.env.APPWRITE_PROJECT_ID;
  const DB_ID = 'ctrader_auth';

  // Find or create user
  let userId = null;
  try {
    const list = await users.list({ queries: [Query.limit(100)] });
    const found = list.users.find(u => u.email === masterEmail);
    if (found) {
      userId = found.$id;
      console.log('[init] Found existing user', userId);
    }
  } catch (e) {
    console.log('[init] User list error:', e.message);
  }

  if (!userId) {
    console.log('[init] Creating Appwrite user for master...');
    const tempPass = crypto.randomBytes(24).toString('base64').replace(/[=+/]/g, '');
    const newUser = await users.createBcryptUser({
      userId: ID.unique(),
      email: masterEmail,
      password: tempPass,
      name: 'admin',
    });
    userId = newUser.$id;
    console.log('[init] Created user', userId);
  }

  // Update label
  console.log('[init] Setting master label...');
  try {
    await users.updateLabels({ userId, labels: ['master'] });
  } catch (e) {
    console.log('[init] Label update warning:', e.message);
  }

  // Hash PIN with scrypt (same format as PIN auth Function)
  const salt = crypto.randomBytes(16).toString('hex');
  const hash = crypto.scryptSync(pin, salt, 64).toString('hex');
  const pinHash = `${salt}:${hash}`;

  // Upsert slave_accounts row
  console.log('[init] Upserting slave_accounts master row...');
  const masterRowId = `master_${userId}`;

  let existingRow = null;
  try {
    const list = await db.listDocuments(DB_ID, 'slave_accounts', [
      Query.equal('appwrite_user_id', userId),
    ]);
    if (list.documents.length > 0) {
      existingRow = list.documents[0];
    }
  } catch (e) {
    console.log('[init] Row lookup error:', e.message);
  }

  if (!existingRow) {
    console.log('[init] Creating master row...');
    try {
      await db.createDocument(DB_ID, 'slave_accounts', masterRowId, {
        appwrite_user_id: userId,
        username: 'admin',
        pin_hash: pinHash,
        role: 'master',
        grant_id: '',
        status: 'active',
        active: true,
        email: masterEmail,
      }, [
        Permission.read(Role.user(userId)),
        Permission.update(Role.user(userId)),
      ]);
      console.log('[init] Created master row', masterRowId);
    } catch (e) {
      console.error('[init] Row creation failed:', e.message);
      process.exit(1);
    }
  } else {
    console.log('[init] Updating master PIN...');
    try {
      await db.updateDocument(DB_ID, 'slave_accounts', existingRow.$id, {
        pin_hash: pinHash,
        active: true,
      });
      console.log('[init] Updated master PIN on row', existingRow.$id);
    } catch (e) {
      console.error('[init] Row update failed:', e.message);
      process.exit(1);
    }
  }

  console.log('');
  console.log('[init] Master admin ready.');
  console.log('       appwrite_user_id:', userId);
  console.log('       username:        admin');
  console.log('       email:           ', masterEmail);
  console.log('');
  console.log('To rotate the PIN, simply rerun this script.');
}

function promptPin() {
  return new Promise((resolve, reject) => {
    process.stdout.write('Enter master PIN (4-6 digits): ');
    process.stdin.setRawMode?.(true);
    process.stdin.resume();
    process.stdin.setEncoding('utf8');

    let pin = '';
    process.stdin.on('data', (ch) => {
      ch = ch.toString();
      if (ch === '\n' || ch === '\r' || ch === '\u0004') {
        process.stdin.setRawMode?.(false);
        process.stdin.pause();
        process.stdout.write('\n');
        resolve(pin);
      } else if (ch === '\u0003') {
        process.stdin.pause();
        reject(new Error('Cancelled'));
      } else {
        pin += ch;
      }
    });
  });
}

main().catch(err => {
  console.error('Error:', err.message);
  process.exit(1);
});
