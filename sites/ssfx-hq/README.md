# SSFX HQ

Consolidated command platform for SSFX cTrader copy trading. Hosts both user-facing auth/dashboard and master command center in a single Appwrite static site.

## Structure

- `index.html` — entry point
- `styles.css` — industrial/utilitarian command console theme
- `config.js` — runtime config (overwritten by `build.js` at deploy time)
- `build.js` — injects environment variables into `config.js`
- `js/app.js` — router + auth + shell controller
- `js/api.js` — clients for auth functions, v2 API, dataservice
- `js/auth.js` — Appwrite SDK wrapper
- `js/router.js` — hash-based router
- `js/ui.js` — shared UI utilities
- `js/components/*` — page modules

## Pages

| Route | Access | Description |
|-------|--------|-------------|
| `/` | public | landing / connect cTrader |
| `/login` | public | PIN login |
| `/reset` | public | PIN reset |
| `/onboarding` | public | set credentials after OAuth |
| `/dashboard` | authenticated | user overview or master fleet dashboard |
| `/trade-config` | slave | copy trading settings |
| `/market` | master | dataservice price / context / quality |
| `/fleet` | master | slave fleet management |
| `/signals` | master | parsed signal feed |
| `/pipeline` | master | agent pipeline + agent logs |
| `/inject` | master | manual signal injection |
| `/terminal` | master | live execution SSE terminal |

## Deployment

```bash
./dev.sh deploy-site ssfx-hq
```

Or deploy all sites:

```bash
./dev.sh deploy-site --all
```

## Design

Industrial command console aesthetic: dark charcoal surfaces, JetBrains Mono for data, Instrument Sans for UI, signal-inspired accent colors (electric green, coral, amber, electric blue).
