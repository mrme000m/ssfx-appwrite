# Init Scripts

This directory contains repeatable scripts that configure third-party services and persist their settings to Appwrite Database.

## Pattern

1. Each init script reads required values from `config.yml` (or a service-specific YAML file).
2. It validates the values.
3. It writes the configuration to Appwrite Database tables.
4. Local and remote runtimes then read these values from Appwrite Database at runtime.

## Usage

```bash
# 1. Copy the example config and fill in real values
cp dev/scripts/init/config.example.yml dev/scripts/init/config.yml

# 2. Run a single init script
./dev/scripts/init/ctrader-oauth.sh

# 3. Or run all init scripts via dev.sh
./dev.sh init
```

## Adding a New Init Script

1. Create `dev/scripts/init/<service>.py` (preferred for structured config) or `dev/scripts/init/<service>.sh`.
2. Make it executable: `chmod +x dev/scripts/init/<service>.*`.
3. Read values from `dev/scripts/init/config.yml`.
4. Upsert rows into the appropriate Appwrite Database table.
5. Ensure the script is idempotent and safe to rerun.
