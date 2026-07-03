# SSFX v2 Architecture Documentation

## Table of Contents

1. [System Overview](#system-overview)
2. [Component Architecture](#component-architecture)
3. [Data Flow](#data-flow)
4. [Authentication Layer](#authentication-layer)
5. [Signal Processing Pipeline](#signal-processing-pipeline)
6. [Agent Integration](#agent-integration)
7. [Deployment Topology](#deployment-topology)
8. [Security Considerations](#security-considerations)

## System Overview

SSFX v2 is a cTrader copy trading platform built on Appwrite as the backend foundation. The system receives trading signals from Telegram channels, processes them through AI agents, and executes trades across multiple cTrader accounts.

### Key Capabilities

- **Multi-account copy trading**: Execute signals across multiple cTrader slave accounts
- **AI-powered signal processing**: Intent classification, entry decision, and lifecycle planning
- **Real-time market data**: Gold quant analysis and multi-timeframe confluence
- **Web-based administration**: Dashboard for monitoring accounts, signals, and executions
- **Secure authentication**: OAuth2 with cTrader, PIN-based login, and session management

## Component Architecture

The system consists of four main layers:

### 1. Frontend Layer (sites/ssfx-hq)

**Location**: `/Volumes/ExMac/code/ssfx/appwrite-auth/sites/ssfx-hq/`

**Technology Stack**:
- Vanilla JavaScript (ES6+)
- Appwrite Web SDK
- HTML5/CSS3
- Hash-based routing

**Key Components**:

- `index.html`: Main entry point with loading screen
- `config.js`: Runtime configuration (endpoints, project ID, domains)
- `js/api.js`: API clients for auth, v2 server, agent harness, and data service
- `js/auth.js`: Authentication state management
- `js/router.js`: Client-side routing
- `js/components/*`: UI components (landing, login, dashboard, onboarding, etc.)

**API Clients**:
- `AuthAPI`: Session management, login/logout, admin operations
- `V2API`: Account management, signal injection, execution tracking
- `AgentAPI`: Signal intent classification, entry decisions, lifecycle planning
- `DataAPI`: Market data, gold quant analysis, feed status

### 2. Authentication Layer (Appwrite Functions)

**Location**: `/Volumes/ExMac/code/ssfx/appwrite-auth/functions/`

**Technology Stack**:
- Node.js 22
- Appwrite JavaScript SDK
- AES-GCM-256 encryption for token storage

**Key Functions**:

#### ctrader-auth
- **Endpoints**: `/auth/ctrader/start`, `/callback`, `/session`, `/logout`, `/admin/slaves`
- **Purpose**: OAuth2 flow with cTrader, session management, master/slave administration
- **Key Features**:
  - Rate-limited OAuth start endpoint
  - State token verification with HMAC
  - Automatic Appwrite user creation for new slaves
  - Grant ID generation and encrypted token storage
  - Session cookie management (`a_session_<PROJECT_ID>`)
  - Master role verification via service_config table

#### ctrader-pin-auth
- **Endpoints**: `/pin-login`, `/set-credentials`, `/pin-reset/request`, `/pin-reset/confirm`
- **Purpose**: PIN-based authentication and credential management
- **Key Features**:
  - BCrypt password hashing
  - PIN reset token generation
  - Email-based PIN reset (via Resend API)
  - Username uniqueness validation

#### ctrader-internal
- **Endpoints**: `/internal/ctrader/refresh`, `/internal/grant/latest`, `/internal/grant/:grant_id/accounts`
- **Purpose**: Server-to-server API for Python backends
- **Key Features**:
  - x-internal-key authentication
  - Token refresh with distributed locking
  - Account discovery and persistence
  - Grant-based access control

#### ctrader-token-refresh-worker
- **Endpoints**: Cron-triggered token refresh
- **Purpose**: Scheduled refresh of near-expiry cTrader tokens
- **Key Features**:
  - Daily cron execution (03:00 UTC)
  - Ephemeral token cleanup
  - Graceful handling of expired refresh tokens

### 3. Runtime Services Layer (remote-services)

**Location**: `/Volumes/ExMac/code/ssfx/appwrite-auth/remote-services/`

**Technology Stack**:
- Python 3.12
- FastAPI
- ctrader-open-api
- MongoDB (legacy, optional)
- Appwrite TablesDB (primary datastore)

**Key Services**:

#### ssfx_server
- **File**: `ssfx_server/web_app.py`
- **Purpose**: Telegram webhook receiver and signal processor
- **Key Features**:
  - Telegram Bot API webhook endpoint
  - Signal parsing with AI intent classification
  - Account follower management
  - Signal experience scoring
  - Background task processing
  - Long-polling fallback mode

#### market_data_service
- **Purpose**: Real-time market data feed and gold quant analysis
- **Key Features**:
  - cTrader tick data subscription
  - Multi-timeframe analysis (M15/H1/H4/D1)
  - Order flow and volume analysis
  - Key level detection (S/R, FVG, order blocks)
  - REST API endpoints for market data

#### agent_harness
- **File**: `agent_harness/`
- **Purpose**: AI decision making service
- **Key Features**:
  - SignalIntentAgent (Mistral Small 3.2)
  - EntryDecisionAgent (Hermes 3)
  - LifecyclePlannerAgent (Kimi K2.7)
  - PplxResearchAgent (Perplexity integration)
  - OpenAI-compatible API endpoints
  - Configurable model selection

#### account_hub
- **Purpose**: Account state management
- **Key Features**:
  - Real-time account state tracking
  - WebSocket notifications
  - Balance and equity monitoring
  - Position and order tracking

### 4. Data Storage Layer

**Primary Datastore**: Appwrite TablesDB

**Database**: `ctrader_auth`

**Key Tables**:

- `slave_accounts`: User identities, encrypted tokens, grant mappings
- `trade_configs`: Per-account trading configuration
- `accounts`: Discovered cTrader trading accounts
- `account_events`: Real-time account state events
- `ctrader_trading_events`: Trading operation event log
- `master_signals`: Master-to-slave signal broadcast
- `ssfx_accounts`: Telegram signal follower configuration
- `ssfx_executions`: Signal execution history
- `ephemeral_tokens`: Short-lived tokens (OAuth state, PIN reset)
- `grant_locks`: Distributed locks for token refresh
- `service_config`: System configuration (OAuth, master auth, etc.)

**Secondary Datastore**: MongoDB (legacy)
- Used for signal history when Appwrite is unavailable
- Gradually being migrated to Appwrite

**Time-Series Data**: InfluxDB Cloud Serverless
- Market data storage
- Performance metrics
- Historical analysis

## Data Flow

### 1. Authentication Flow

```
User → SPA → ctrader-auth/start → cTrader OAuth → /callback → 
Appwrite user creation → slave_accounts row → Session cookie → Dashboard
```

### 2. Signal Ingestion Flow

```
Telegram Channel → Telegram Bot Webhook → ssfx_server/webhook → 
SignalIntentAgent → Parser → SignalExperienceScorer → 
AccountFollowers → cTrader Execution → account_events
```

### 3. Trading Execution Flow

```
Signal → EntryDecisionAgent → AccountFollower → 
ctrader-open-api → cTrader Platform → Position → 
LifecyclePlannerAgent → Follow-up Actions
```

### 4. Market Data Flow

```
ctrader-open-api → market_data_service → 
GoldQuantEngine → DataAPI → AgentHarness → 
EntryDecisionAgent/LifecyclePlannerAgent
```

## Authentication Layer

### OAuth2 Flow

1. User clicks "Connect cTrader" in SPA
2. SPA redirects to `/auth/ctrader/start`
3. Function generates state token, stores in `ephemeral_tokens`
4. Function redirects to cTrader OAuth consent page
5. User authorizes access
6. cTrader redirects to `/callback` with code
7. Function exchanges code for tokens
8. Function creates/updates Appwrite user
9. Function creates/updates `slave_accounts` row with encrypted tokens
10. Function creates Appwrite session
11. Function sets session cookie and redirects to dashboard

### PIN Authentication

1. User enters username + PIN in SPA
2. SPA calls `/pin-login` with credentials
3. Function verifies PIN hash against `slave_accounts`
4. Function creates Appwrite session
5. Function returns session cookie
6. SPA stores cookie and redirects to dashboard

### Session Management

- Session cookie: `a_session_<PROJECT_ID>`
- Cookie is HTTP-only, Secure, SameSite=Lax
- Session validation via `/session` endpoint
- Logout via `/logout` endpoint (clears cookie and Appwrite session)

## Signal Processing Pipeline

### 1. Webhook Reception

- Telegram Bot API posts to `/webhook`
- Webhook secret token verification (X-Telegram-Bot-Api-Secret-Token)
- Source chat ID filtering
- Background task processing

### 2. Intent Classification

- SignalIntentAgent analyzes message text
- Classifies as: new_signal, update, orphan, or noise
- Links to prior messages when applicable
- Confidence scoring

### 3. Signal Parsing

- ChainedParser (LLM → regex fallback)
- Extracts: symbol, direction, entry price, SL, TP levels
- Context-aware parsing with recent messages
- Confidence scoring

### 4. Experience Scoring

- SignalExperienceScorer evaluates historical performance
- Adjusts lot size based on author track record
- Actions: normal, reduce, block
- Persists to signal_experience table

### 5. Execution Routing

- Signal distributed to all configured AccountFollowers
- Each follower applies account-specific filters
- Executes via ctrader-open-api
- Records execution in ssfx_executions table

## Agent Integration

### SignalIntentAgent

- **Model**: Mistral Small 3.2
- **Purpose**: Classify incoming messages
- **Endpoint**: `/agent/v1/signal/intent`
- **Input**: Raw text, message context, recent messages
- **Output**: Intent classification, confidence, reasoning, linked message

### EntryDecisionAgent

- **Model**: Hermes 3
- **Purpose**: Approve/reject/modify trade entries
- **Endpoint**: `/agent/v1/entry/decision`
- **Input**: Signal details, gold quant snapshot, account state
- **Output**: Approval decision, modified parameters, confidence

### LifecyclePlannerAgent

- **Model**: Kimi K2.7
- **Purpose**: In-trade management suggestions
- **Endpoint**: `/agent/v1/lifecycle/plan`
- **Input**: Open position, market conditions, signal experience
- **Output**: Action recommendations (partial close, breakeven, hold, close)

### PplxResearchAgent

- **Service**: Perplexity Space
- **Purpose**: Long-term gold market research
- **Integration**: Enriches agent decisions with macro context
- **Data Sources**: Perplexity knowledge, TradingView scans

## Deployment Topology

### Cloud Infrastructure

- **Cloud Provider**: Azure VM (primary), with Cloudflare Tunnel
- **Domain**: mrme.tech
- **Public Services**:
  - `auth.mrme.tech`: ctrader-auth function
  - `pin.mrme.tech`: ctrader-pin-auth function
  - `ssfx-api.mrme.tech`: ssfx_server webhook
  - `dataservice.mrme.tech`: market_data_service API
  - `agent.mrme.tech`: agent_harness API
  - `app.mrme.tech`: SSFX HQ SPA

### Containerized Services

Docker Compose stack includes:
- ssfx_server (port 8000)
- market_data_service (ports 9000-9002)
- agent_harness (port 9003)
- pplx_agent (port 9004)
- account_hub (port 9301)

### CI/CD Pipeline

- **Branch Strategy**: develop → main (protected)
- **Deployment**: GitHub Actions on push to develop
- **Process**:
  1. Push TablesDB schema
  2. Deploy Appwrite Functions
  3. Deploy Appwrite Site
  4. Verify custom domains
  5. Smoke test endpoints
  6. Cleanup old deployments

## Security Considerations

### Authentication Security

- **OAuth State Tokens**: HMAC-signed, single-use, short-lived
- **Session Cookies**: HTTP-only, Secure, SameSite=Lax
- **Token Encryption**: AES-GCM-256 for cTrader tokens at rest
- **Rate Limiting**: OAuth start endpoint (10 requests/minute)
- **Webhook Verification**: Telegram secret token required

### Data Security

- **Encryption at Rest**: cTrader tokens encrypted in database
- **Row-Level Permissions**: Appwrite TablesDB permissions enforced
- **No Plaintext Logging**: Sensitive data never logged
- **Secret Management**: Environment variables, never in git

### API Security

- **Internal API**: x-internal-key required
- **Admin API**: x-admin-key required
- **CORS**: Restricted to known origins
- **Input Validation**: All endpoints validate input
- **Error Handling**: Generic error messages, detailed logs server-side

### Operational Security

- **Distributed Locks**: Prevent concurrent token refresh
- **Status Monitoring**: Health endpoints on all services
- **Graceful Degradation**: Fallback to deterministic rules when agents fail
- **Kill Switches**: Feature flags for agent enablement

## Key Integration Points

### Appwrite Integration

- **Database**: TablesDB as primary datastore
- **Authentication**: Appwrite sessions and users
- **Functions**: Node.js runtime for auth logic
- **Realtime**: WebSocket notifications for account state

### cTrader Integration

- **OAuth2**: Standard authorization code flow
- **Open API**: Protobuf over WebSocket
- **Token Management**: Automatic refresh with distributed locking
- **Account Discovery**: Real-time account enumeration

### Telegram Integration

- **Bot API**: Webhook for channel posts
- **Secret Token**: Webhook verification
- **Long Polling**: Fallback for development
- **Message Parsing**: Context-aware signal extraction

### AI Agent Integration

- **Model Selection**: Configurable per agent
- **Fallback Behavior**: Deterministic rules on failure
- **Confidence Thresholds**: Minimum scores for action
- **Context Enrichment**: Market data and signal history

## Configuration Management

### Environment Variables

Primary configuration via `.env` files:
- `remote-services/config/v2.env`: Runtime services
- `remote-services/config/v2.env.example`: Template
- Project root `.env`: Development secrets

### Appwrite Configuration

- **Service Config**: `service_config` table
- **OAuth Settings**: `ctrader_oauth` config key
- **Master Auth**: `master_auth` config key
- **Feature Flags**: Agent enablement, signal experience

### Deployment Configuration

- **GitHub Secrets**: CI/CD credentials
- **Cloudflare Tunnel**: Ingress rules in `remote-services/config/tunnel-ingress.json`
- **Docker Compose**: Service definitions and networking

## Monitoring and Observability

### Health Endpoints

- `/health` on all services
- Status includes: trading_enabled, active_positions, account counts
- Response time monitoring

### Logging

- Structured JSON logs
- Error-level logging for critical failures
- Warning-level for recoverable issues
- Info-level for normal operation
- Debug-level for development

### Metrics

- Execution latency
- Signal processing time
- Agent response times
- Token refresh success/failure rates
- Webhook delivery statistics

## Future Evolution

### Planned Improvements

1. **Complete MongoDB Migration**: Move all signal history to Appwrite
2. **Enhanced Agent Orchestration**: Dynamic model selection based on performance
3. **Multi-Symbol Support**: Expand beyond XAUUSD
4. **Risk Management Dashboard**: Visualize exposure across accounts
5. **Mobile App**: Native iOS/Android clients
6. **Performance Optimization**: Caching and batch processing
7. **Enhanced Security**: IP allow-listing, rate limiting improvements
8. **Multi-Region Deployment**: Geographic redundancy

### Architecture Principles

1. **Appwrite as Source of Truth**: All configuration in Appwrite Database
2. **Stateless Services**: Horizontal scalability
3. **Graceful Degradation**: Fallback mechanisms for all critical paths
4. **Security First**: Encryption, validation, and verification at every layer
5. **Observability**: Comprehensive logging and monitoring
6. **Automation**: CI/CD for all deployments
7. **Documentation**: Architecture decisions captured in docs/

## Conclusion

SSFX v2 represents a modern, AI-enhanced copy trading platform built on Appwrite's scalable backend. The architecture separates concerns across authentication, signal processing, execution, and monitoring layers while maintaining tight security and operational reliability. The system is designed for continuous evolution with clear migration paths from legacy components.

For detailed component-specific documentation, see:
- `docs/account-hub-and-dataservice.md` - Account hub and data service architecture
- `cpr00.md` - Signal ingestion guide
- `AGENTS.md` - Project context and development operations