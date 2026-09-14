# Forsa Financial News Monitoring Agent

An autonomous AI-powered system that monitors Egyptian financial, consumer-finance, and regulatory news daily, verifies it, summarises it factually, and delivers a concise digest to the Forsa CEO by email.

**This is not a chatbot.** It runs on a schedule, processes news autonomously, and terminates.

---

## Architecture

```
SCHEDULER
    ↓
collect_news       (CBE scraper · FRA scraper · SerpAPI · RSS feeds · Competitor monitors)
    ↓
preprocess_news    (HTML cleaning · encoding · deduplication · normalisation)
    ↓
classify_articles  (Keyword pre-filter → Gemini Flash LLM)
    ↓
deduplicate_articles (URL hash · title fuzzy · entity overlap · embedding cosine)
    ↓
verify_sources     (Official → Verified → Unverified tier assignment)
    ↓
filter_important_news (Gemini Flash importance scoring → DB idempotency check)
    ↓
[no important news] → handle_no_news → format_email → send_email
    ↓
summarize_news     (Gemini Pro — factual only, no analysis)
    ↓
format_email       (CEO digest — source · date · link only)
    ↓
send_email         (SMTP · record in DB · update workflow_run)
```

---

## Prerequisites

- Python 3.13 (`py` launcher)
- Remote PostgreSQL instance
- Google API key (Gemini)
- SerpAPI key
- SMTP mail server credentials

---

## Quick Start

### 1. Clone & install

```powershell
cd "d:\Tasks\Forsa News Agent"
py -3 -m venv .venv
.venv\Scripts\Activate.ps1
py -3 -m pip install -r requirements.txt
```

### 2. Configure environment

```powershell
Copy-Item .env.example .env
# Edit .env with your real credentials
notepad .env
```

### 3. Initialise the database

```powershell
# Create all tables
py -3 app/main.py --init-db

# Run Alembic migrations (preferred for production)
$env:DATABASE_URL="your-database-url-here"
py -3 -m alembic upgrade head
```

### 4. Seed sources and competitors

```powershell
py -3 scripts/seed_sources.py
py -3 scripts/seed_competitors.py
```

### 5. Run a manual pipeline test

```powershell
py -3 scripts/run_once.py
```

### 6. Start the scheduler (production)

```powershell
py -3 app/main.py
```

---

## Configuration

All configuration lives in `.env`. Key settings:

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | — | PostgreSQL asyncpg connection URL |
| `GOOGLE_API_KEY` | — | Google Gemini API key |
| `SERPAPI_KEY` | — | SerpAPI key for Google News |
| `EMAIL_PROVIDER` | `power_automate` | `power_automate \| microsoft365 \| smtp` |
| `POWER_AUTOMATE_WEBHOOK_URL` | — | HTTP POST URL from Power Automate cloud flow |
| `MICROSOFT_TENANT_ID` | — | Azure AD Tenant ID (for `microsoft365`) |
| `MICROSOFT_CLIENT_ID` | — | Azure App Registration Client ID |
| `MICROSOFT_CLIENT_SECRET` | — | Azure App Registration Secret |
| `SMTP_HOST` | — | SMTP server hostname (for `smtp`) |
| `EMAIL_RECIPIENT` | — | Recipient email address |
| `SCHEDULE_FREQUENCY` | `daily` | `daily \| hourly \| twice_daily \| custom_cron` |
| `SCHEDULE_TIME` | `08:00` | Local time for daily execution |
| `TIMEZONE` | `Africa/Cairo` | Scheduling timezone |
| `IMPORTANCE_THRESHOLD` | `60` | Minimum score (0–100) to email an event |
| `NO_NEWS_BEHAVIOUR` | `skip` | `skip \| send_empty` |
| `OVERLAP_HOURS` | `2` | Window overlap to catch delayed articles |

---

## Adding / Removing Sources

Sources are managed in the `sources` database table — no code changes needed.

```sql
-- Disable a source
UPDATE sources SET active = false WHERE name = 'Source Name';

-- Add a new RSS source
INSERT INTO sources (name, url, source_type, tier, priority, language, active)
VALUES ('New Source', 'https://newsource.com/feed/', 'rss', 2, 30, 'en', true);
```

Or re-run `scripts/seed_sources.py` after editing the seed list.

---

## Adding / Removing Competitors

Competitors are managed in the `competitors` database table — no code changes needed.

```sql
-- Disable a competitor
UPDATE competitors SET active = false WHERE name = 'Company Name';

-- Add a new competitor
INSERT INTO competitors (name, aliases, website, priority, active)
VALUES ('New Company', '["NewCo", "New Company Finance"]', 'https://newco.com', 1, true);
```

Or re-run `scripts/seed_competitors.py` after editing the seed list.

---

## Changing the Schedule

Edit `.env` only — no code changes:

```bash
# Daily at 08:00 Cairo
SCHEDULE_FREQUENCY=daily
SCHEDULE_TIME=08:00

# Twice daily
SCHEDULE_FREQUENCY=twice_daily
SCHEDULE_TIME=08:00
SCHEDULE_TIME_2=18:00

# Custom cron (every weekday at 07:30)
SCHEDULE_FREQUENCY=custom_cron
SCHEDULE_CRON=30 7 * * 1-5
```

---

## Running Tests

```powershell
# Unit tests (no API keys or DB needed)
py -3 -m pytest tests/unit/ -v

# Integration tests (mocked — no API keys needed)
py -3 -m pytest tests/integration/ -v

# Evaluation (requires real API keys)
py -3 tests/evaluation/eval_runner.py
```

---

## Project Structure

```
forsa-news-agent/
├── app/
│   ├── config.py                    # All configuration (Pydantic Settings)
│   ├── main.py                      # Entry point + APScheduler
│   ├── graph/
│   │   ├── graph.py                 # LangGraph StateGraph
│   │   ├── state.py                 # AgentState TypedDict
│   │   └── nodes/
│   │       ├── collect.py           # News collection (all sources)
│   │       ├── preprocess.py        # HTML/encoding cleaning
│   │       ├── classify.py          # Keyword filter + Gemini Flash
│   │       ├── deduplicate.py       # Multi-signal deduplication
│   │       ├── verify.py            # Source verification
│   │       ├── filter.py            # Importance filter + idempotency
│   │       ├── summarize.py         # Gemini Pro factual summaries
│   │       ├── format_email.py      # CEO email assembly
│   │       └── send_email.py        # SMTP delivery + DB recording
│   ├── collectors/
│   │   ├── base.py                  # NewsSourceCollector ABC
│   │   ├── cbe.py                   # CBE official website scraper
│   │   ├── fra.py                   # FRA official website scraper
│   │   ├── rss.py                   # Generic RSS/Atom collector
│   │   └── search.py                # SerpAPI Google News collector
│   ├── models/
│   │   ├── article.py               # Article, ArticleClassification (Pydantic)
│   │   └── event.py                 # NewsEvent, EventSummary (Pydantic)
│   ├── services/
│   │   ├── llm.py                   # Gemini Flash/Pro LLM client
│   │   ├── embeddings.py            # Gemini embedding client
│   │   ├── deduplication.py         # DeduplicationService
│   │   ├── verification.py          # VerificationService
│   │   └── email.py                 # SMTP EmailService
│   └── database/
│       ├── models.py                # SQLAlchemy ORM models
│       ├── connection.py            # Async engine + session factory
│       ├── repositories.py          # Repository pattern (all DB access)
│       └── migrations/              # Alembic migration scripts
├── scripts/
│   ├── seed_sources.py              # Seed news sources into DB
│   ├── seed_competitors.py          # Seed competitor watchlist into DB
│   └── run_once.py                  # Manual pipeline trigger
├── tests/
│   ├── unit/                        # Unit tests (no network/LLM)
│   ├── integration/                 # Integration tests (mocked)
│   └── evaluation/                  # AI evaluation dataset + runner
├── .env.example                     # Configuration template
├── requirements.txt
├── alembic.ini
├── pytest.ini
└── docker-compose.yml               # For future containerised deployment
```

---

## Security Notes

- Never commit `.env` — it is listed in `.gitignore`
- All article content is wrapped in prompt-injection delimiters before LLM processing
- The LLM system prompt explicitly instructs the model to ignore directives inside article content
- Scraped content is treated as untrusted data throughout the pipeline
- No secrets are hard-coded anywhere in the codebase

---

## Monitoring

Every pipeline run is recorded in the `workflow_runs` table:

```sql
SELECT
    started_at,
    status,
    articles_collected,
    articles_relevant,
    events_detected,
    events_sent,
    email_sent,
    error_message
FROM workflow_runs
ORDER BY started_at DESC
LIMIT 10;
```
