# Documentation Index

Quick reference for the `docs/` directory.

---

## 📘 Current Architecture

| Document | Purpose | Freshness |
|----------|---------|-----------|
| **ARCHITECTURE.md** | Full platform architecture: logical planes, data flows, deployment topology, Appwrite Cloud optimization patterns. | ✅ Current (2026-07-03) |
| **AUTHENTICATION_ARCHITECTURE.md** | Concise operational reference for auth components, endpoints, tokens, and security checklist. | ✅ Current (2026-07-03) |
| **account-hub-and-dataservice.md** | AccountHub v2 and DataService integration: connection model, config, migration notes. | Current |
| **gold-quantitative-analysis-design.md** | Gold Quant Engine design: tick-volume analysis, multi-timeframe confluence, agent integration. | Current |

---

## 📋 Operational References

| Document | Purpose | Freshness |
|----------|---------|-----------|
| **INTEGRATION_AND_TRIAL_READINESS.md** | Pre-trial checklist: auth integration, schema validation, demo account setup, risk mitigations. | ✅ Updated for current state |
| **project-devstack-deployment-learnings.md** | Dev stack conventions: `dev.sh` commands, Appwrite auth layer, tunnel ingress, CI/CD flow. | ✅ Updated for current paths |
| **AGENTS.md** (project root) | Master project context for all AI agents: conventions, tools, secrets, deployment procedures. | ✅ Updated |

---

## 🔧 Maintenance & Audit

| Document | Purpose | Freshness |
|----------|---------|-----------|
| **REMOTE_SERVICES_AUDIT.md** | Audit findings from `remote-services/` deduplication pass: inline Client() fixes, deprecations. | Current |
| **SESSION_FIXES_SUMMARY.md** | Historical record of session/auth fixes applied. | Historical |

---

## 📜 Historical / Decision Records

| Document | Purpose | Note |
|----------|---------|------|
| **NAMING_AND_CONSOLIDATION_OVERHAUL.md** | Rename matrix and migration order for tables, hostnames, function IDs. | Phases 1 completed; Phases 2–4 pending live migration. |
| **CONSOLIDATION_ANALYSIS.md** | The original audit that drove consolidation Phases A–F. All actions completed. | Marked historical. See commit log on `develop`. |
| **phase-2-plan.md** | Phase 2 hardening plan (security, kill-switches, schema gaps, tests). | **Completed** 2026-07-02. Includes post-Phase-2 consolidation summary. |

---

## Quick Links

- **Run dev command:** `../dev.sh <command>`
- **Deploy auth layer:** `../dev.sh deploy-auth`
- **Deploy to VM:** `../dev.sh deploy-remote aws`
- **Init third-party services:** `../dev.sh init`
- **Check tunnel status:** `../dev.sh cf-tunnel-status`
