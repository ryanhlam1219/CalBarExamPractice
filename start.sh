#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════════
#  CalBar Exam Tutor — One-Click Setup & Launch
#  This script installs everything needed and starts the application.
#  No engineering knowledge required — just run: ./start.sh
# ═══════════════════════════════════════════════════════════════════════════════
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_DIR"

# ── Colors & helpers ──────────────────────────────────────────────────────────
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'
BOLD='\033[1m'; DIM='\033[2m'; NC='\033[0m'

banner() {
    echo ""
    echo -e "${BOLD}╔══════════════════════════════════════════════════╗${NC}"
    echo -e "${BOLD}║       CalBar Exam Tutor — Setup & Launch        ║${NC}"
    echo -e "${BOLD}╚══════════════════════════════════════════════════╝${NC}"
    echo ""
}

step()  { echo -e "\n${GREEN}${BOLD}[$1/$TOTAL_STEPS]${NC} ${BOLD}$2${NC}"; }
ok()    { echo -e "    ${GREEN}✓${NC}  $1"; }
warn()  { echo -e "    ${YELLOW}⚠${NC}  $1"; }
fail()  { echo -e "    ${RED}✗  $1${NC}"; echo -e "    ${DIM}If you need help, open an issue with this error message.${NC}"; exit 1; }
info()  { echo -e "    ${DIM}$1${NC}"; }

TOTAL_STEPS=8
banner

# Pre-flight: ensure curl is available (needed for Homebrew, Ollama)
if ! command -v curl &>/dev/null; then
    fail "curl is required but not found. Install it first:\n    macOS: xcode-select --install\n    Linux: sudo apt install curl"
fi

# Ensure data directories exist (gitignored, won't exist on fresh clone)
mkdir -p data/input data/raw data/extracted data/parsed data/manifests

# ══════════════════════════════════════════════════════════════════════════════
# STEP 1: Homebrew (macOS package manager)
# ══════════════════════════════════════════════════════════════════════════════
step 1 "Package manager (Homebrew)"

ensure_brew() {
    if command -v brew &>/dev/null; then return 0; fi
    info "Homebrew is a package manager that installs tools for you."
    info "You may be asked for your password — this is normal."
    echo ""
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    # Add brew to path for this session
    if [ -f /opt/homebrew/bin/brew ]; then
        eval "$(/opt/homebrew/bin/brew shellenv)"
    elif [ -f /usr/local/bin/brew ]; then
        eval "$(/usr/local/bin/brew shellenv)"
    fi
}

if [[ "$(uname)" == "Darwin" ]]; then
    if command -v brew &>/dev/null; then
        ok "Homebrew is installed"
    else
        ensure_brew
        ok "Homebrew installed"
    fi
else
    ok "Linux detected — skipping Homebrew"
fi

# ══════════════════════════════════════════════════════════════════════════════
# STEP 2: Python 3.12+
# ══════════════════════════════════════════════════════════════════════════════
step 2 "Python 3.12+"

find_python() {
    for candidate in python3.12 python3.13 python3.14 python3; do
        if command -v "$candidate" &>/dev/null; then
            local ver
            ver=$("$candidate" --version 2>&1 | grep -oE '[0-9]+\.[0-9]+' | head -1)
            local major minor
            major=$(echo "$ver" | cut -d. -f1)
            minor=$(echo "$ver" | cut -d. -f2)
            if [ "$major" -ge 3 ] && [ "$minor" -ge 12 ]; then
                echo "$candidate"
                return 0
            fi
        fi
    done
    return 1
}

# Create or verify virtual environment
if [ -f .venv/bin/python ] && .venv/bin/python --version &>/dev/null; then
    ok "Python virtual environment exists ($(.venv/bin/python --version))"
else
    [ -d .venv ] && rm -rf .venv
    PYTHON=$(find_python 2>/dev/null) || {
        if [[ "$(uname)" == "Darwin" ]]; then
            ensure_brew
            info "Installing Python 3.12 via Homebrew..."
            brew install python@3.12
        else
            fail "Python 3.12+ not found. Install it with: sudo apt install python3.12 python3.12-venv"
        fi
        PYTHON=$(find_python) || fail "Could not find Python 3.12+ after installation"
    }
    info "Creating virtual environment with $PYTHON..."
    "$PYTHON" -m venv .venv
    ok "Virtual environment created ($(.venv/bin/python --version))"
fi

# ══════════════════════════════════════════════════════════════════════════════
# STEP 3: Python dependencies
# ══════════════════════════════════════════════════════════════════════════════
step 3 "Python dependencies"

info "Installing/updating packages (this may take a minute on first run)..."
.venv/bin/pip install -q --upgrade pip 2>/dev/null || true
if .venv/bin/pip install -q -e ".[dev]" 2>/dev/null; then
    ok "All Python packages installed"
else
    warn "Dev dependencies failed — trying core dependencies only..."
    .venv/bin/pip install -q -e . 2>/dev/null || fail "Failed to install Python dependencies"
    ok "Core Python packages installed"
fi

# ══════════════════════════════════════════════════════════════════════════════
# STEP 4: Environment configuration
# ══════════════════════════════════════════════════════════════════════════════
step 4 "Environment configuration"

if [ ! -f .env ]; then
    if [ -f .env.example ]; then
        cp .env.example .env
        ok "Created .env from template"
    else
        cat > .env << 'ENVEOF'
DATABASE_URL=postgresql+psycopg://calbar:calbar@localhost:5432/calbar_tutor
CALBAR_DATA_DIR=data
CALBAR_USER_AGENT=CalBarExamTutor/0.1 (+local research; contact: local)
CALBAR_ANALYSIS_PROVIDER=ollama
CALBAR_OLLAMA_BASE_URL=http://localhost:11434
CALBAR_OLLAMA_MODEL=gemma4:31b-cloud
ENVEOF
        ok "Created .env with default settings"
    fi
else
    ok ".env file exists"
fi

# ══════════════════════════════════════════════════════════════════════════════
# STEP 5: PostgreSQL database
# ══════════════════════════════════════════════════════════════════════════════
step 5 "PostgreSQL database"

pg_running() { pg_isready -h localhost -p 5432 -q 2>/dev/null; }

wait_for_pg() {
    for i in $(seq 1 20); do
        pg_running && return 0
        sleep 1
    done
    return 1
}

start_pg_docker() {
    command -v docker &>/dev/null || return 1
    docker info &>/dev/null 2>&1 || return 1
    info "Starting PostgreSQL via Docker..."
    docker compose up -d postgres 2>/dev/null || docker-compose up -d postgres 2>/dev/null || return 1
    wait_for_pg
}

start_pg_brew() {
    if ! command -v pg_isready &>/dev/null; then
        [[ "$(uname)" != "Darwin" ]] && return 1
        ensure_brew
        info "Installing PostgreSQL 16 via Homebrew..."
        brew install postgresql@16
        brew link postgresql@16 --force 2>/dev/null || true
        # Ensure pg tools are on PATH for this session
        local pg_bin
        pg_bin="$(brew --prefix postgresql@16 2>/dev/null)/bin"
        if [ -d "$pg_bin" ]; then
            export PATH="$pg_bin:$PATH"
        fi
    fi
    if ! pg_running; then
        info "Starting PostgreSQL service..."
        brew services start postgresql@16 2>/dev/null || brew services start postgresql 2>/dev/null || true
        wait_for_pg || return 1
    fi
    return 0
}

ensure_pg_database() {
    # Test if our database already exists
    if psql -h localhost -p 5432 -U calbar -d calbar_tutor -c "SELECT 1" &>/dev/null; then
        return 0
    fi
    # Try to create the role and database
    for superuser in "$USER" postgres; do
        if psql -h localhost -p 5432 -U "$superuser" -d postgres -c "SELECT 1" &>/dev/null 2>&1; then
            psql -h localhost -p 5432 -U "$superuser" -d postgres -c "
                DO \$\$
                BEGIN
                    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'calbar') THEN
                        CREATE ROLE calbar WITH LOGIN PASSWORD 'calbar' CREATEDB;
                    END IF;
                END \$\$;
            " &>/dev/null
            if ! psql -h localhost -p 5432 -U "$superuser" -d postgres -c "SELECT 1 FROM pg_database WHERE datname = 'calbar_tutor'" 2>/dev/null | grep -q 1; then
                psql -h localhost -p 5432 -U "$superuser" -d postgres -c "CREATE DATABASE calbar_tutor OWNER calbar" &>/dev/null
            fi
            return 0
        fi
    done
    return 1
}

if pg_running; then
    ok "PostgreSQL is already running"
elif start_pg_docker; then
    ok "PostgreSQL started via Docker"
elif start_pg_brew; then
    ok "PostgreSQL started via Homebrew"
else
    fail "Could not start PostgreSQL.\n    Try: brew install postgresql@16 && brew services start postgresql@16\n    Or: docker compose up -d postgres"
fi

if ensure_pg_database; then
    ok "Database 'calbar_tutor' is ready"
else
    warn "Could not auto-create database — you may need to create it manually"
    info "Run: createdb -U \$USER calbar_tutor"
fi

# ══════════════════════════════════════════════════════════════════════════════
# STEP 6: Ollama (AI analysis engine)
# ══════════════════════════════════════════════════════════════════════════════
step 6 "AI analysis engine (Ollama)"

OLLAMA_MODEL=$(grep -E "^CALBAR_OLLAMA_MODEL=" .env 2>/dev/null | cut -d= -f2 || echo "gemma4:31b-cloud")
[ -z "$OLLAMA_MODEL" ] && OLLAMA_MODEL="gemma4:31b-cloud"

ollama_running() { curl -sf http://localhost:11434/api/tags &>/dev/null; }

if ! command -v ollama &>/dev/null; then
    info "Ollama provides AI-powered essay analysis."
    if [[ "$(uname)" == "Darwin" ]]; then
        ensure_brew
        info "Installing Ollama via Homebrew..."
        brew install ollama
    elif [[ "$(uname)" == "Linux" ]]; then
        info "Installing Ollama..."
        curl -fsSL https://ollama.com/install.sh | sh
    else
        warn "Cannot auto-install Ollama on this OS."
        warn "Visit https://ollama.com/download to install manually."
        warn "The app will use simplified mock analysis until Ollama is available."
    fi
fi

if command -v ollama &>/dev/null; then
    if ! ollama_running; then
        info "Starting Ollama service..."
        ollama serve &>/dev/null &
        for i in $(seq 1 15); do
            ollama_running && break
            sleep 1
        done
    fi

    if ollama_running; then
        ok "Ollama is running"

        # Cloud models (name contains "-cloud") require an Ollama account
        if [[ "$OLLAMA_MODEL" == *"-cloud"* ]] || [[ "$OLLAMA_MODEL" == *":cloud"* ]] || [[ "$OLLAMA_MODEL" == *"cloud"* ]]; then
            if ! ollama list 2>/dev/null | grep -qF "$OLLAMA_MODEL"; then
                echo ""
                echo -e "    ${BOLD}The model '$OLLAMA_MODEL' runs in the cloud and requires an Ollama account.${NC}"
                echo ""
                echo -e "    ${BOLD}To set up:${NC}"
                echo -e "    1. Create a free account at ${GREEN}https://ollama.com/signup${NC}"
                echo -e "    2. Run: ${GREEN}ollama login${NC}"
                echo -e "    3. Re-run this script"
                echo ""
                echo -e "    ${DIM}Or switch to a local model by editing .env:${NC}"
                echo -e "    ${DIM}  CALBAR_OLLAMA_MODEL=gemma3:12b${NC}"
                echo ""
                read -p "    Have you already logged in to Ollama? (y/n) " -n 1 -r
                echo ""
                if [[ $REPLY =~ ^[Yy]$ ]]; then
                    info "Pulling cloud model: $OLLAMA_MODEL"
                    if ollama pull "$OLLAMA_MODEL"; then
                        ok "Model $OLLAMA_MODEL ready"
                    else
                        warn "Failed to pull cloud model — you may need to run: ollama login"
                        warn "Mock analysis will be used until the model is available."
                    fi
                else
                    info "Skipping cloud model setup for now."
                    warn "Mock analysis will be used. Run 'ollama login' and restart to enable AI."
                fi
            else
                ok "Model $OLLAMA_MODEL is ready"
            fi
        else
            # Local model — just pull it
            if ! ollama list 2>/dev/null | grep -qF "$OLLAMA_MODEL"; then
                info "Downloading AI model: $OLLAMA_MODEL"
                info "This is a one-time download and may take 10-20 minutes..."
                echo ""
                if ollama pull "$OLLAMA_MODEL"; then
                    ok "Model $OLLAMA_MODEL downloaded"
                else
                    warn "Failed to download model — mock analysis will be used"
                fi
            else
                ok "Model $OLLAMA_MODEL is ready"
            fi
        fi
    else
        warn "Could not start Ollama — mock analysis will be used"
    fi
else
    warn "Ollama not installed — mock analysis will be used"
fi

# ══════════════════════════════════════════════════════════════════════════════
# STEP 7: Database setup & data loading
# ══════════════════════════════════════════════════════════════════════════════
step 7 "Database setup & data loading"

info "Creating database tables..."
if .venv/bin/python -m app.cli init-db 2>/dev/null; then
    ok "Database tables ready"
else
    fail "Could not initialize database tables. Is PostgreSQL running?\n    Check: pg_isready -h localhost -p 5432\n    Create DB: createdb -U \$USER calbar_tutor"
fi

# Check current data state
QUESTION_COUNT=$(.venv/bin/python -c "
from app.db.session import SessionLocal
from sqlalchemy import func, select
from app.db.models.essays import EssayQuestion
with SessionLocal() as s:
    print(s.scalar(select(func.count(EssayQuestion.id))) or 0)
" 2>/dev/null || echo "0")

TEMPLATE_COUNT=$(.venv/bin/python -c "
from app.db.session import SessionLocal
from sqlalchemy import func, select
from app.db.models.templates import EssayTemplate
with SessionLocal() as s:
    print(s.scalar(select(func.count(EssayTemplate.id))) or 0)
" 2>/dev/null || echo "0")

RULE_COUNT=$(.venv/bin/python -c "
from app.db.session import SessionLocal
from sqlalchemy import func, select
from app.db.models.rules import LegalRule
with SessionLocal() as s:
    print(s.scalar(select(func.count(LegalRule.id))) or 0)
" 2>/dev/null || echo "0")

info "Current data: $QUESTION_COUNT questions, $TEMPLATE_COUNT templates, $RULE_COUNT supplemental rules"

# If database is empty but parsed JSON files exist, load from seed
PARSED_COUNT=$(find data/parsed -maxdepth 1 -name '*.essays.json' 2>/dev/null | wc -l | tr -d ' ' || echo "0")

if [ "$QUESTION_COUNT" = "0" ] && [ "$PARSED_COUNT" -gt 0 ]; then
    info "Loading pre-parsed data from data/parsed/ (no PDF downloads needed)..."
    .venv/bin/python -m app.cli load-seed || warn "Seed loading had errors — see above"

    # Re-check counts
    QUESTION_COUNT=$(.venv/bin/python -c "
from app.db.session import SessionLocal; from sqlalchemy import func, select; from app.db.models.essays import EssayQuestion
with SessionLocal() as s: print(s.scalar(select(func.count(EssayQuestion.id))) or 0)
" 2>/dev/null || echo "0")
    TEMPLATE_COUNT=$(.venv/bin/python -c "
from app.db.session import SessionLocal; from sqlalchemy import func, select; from app.db.models.templates import EssayTemplate
with SessionLocal() as s: print(s.scalar(select(func.count(EssayTemplate.id))) or 0)
" 2>/dev/null || echo "0")
    RULE_COUNT=$(.venv/bin/python -c "
from app.db.session import SessionLocal; from sqlalchemy import func, select; from app.db.models.rules import LegalRule
with SessionLocal() as s: print(s.scalar(select(func.count(LegalRule.id))) or 0)
" 2>/dev/null || echo "0")
    ok "Loaded from seed: $QUESTION_COUNT questions, $TEMPLATE_COUNT templates, $RULE_COUNT rules"
elif [ "$QUESTION_COUNT" = "0" ]; then
    # No parsed data available — download from CalBar website
    info "No pre-parsed data found. Downloading from CalBar website..."
    info "This may take several minutes on first run."
    for year in $(seq 2026 -1 2012); do
        for month in february july october; do
            .venv/bin/python -m app.cli run-pipeline --year "$year" --month "$month" --limit 1 2>/dev/null || true
        done
    done
    QUESTION_COUNT=$(.venv/bin/python -c "
from app.db.session import SessionLocal; from sqlalchemy import func, select; from app.db.models.essays import EssayQuestion
with SessionLocal() as s: print(s.scalar(select(func.count(EssayQuestion.id))) or 0)
" 2>/dev/null || echo "0")
    ok "Downloaded and parsed $QUESTION_COUNT essay questions"

    # Try loading templates and rules from PDFs if available
    SCHIMMEL_PDF="data/input/Schimmel Templates_Bullet Version.pdf"
    if [ -f "$SCHIMMEL_PDF" ]; then
        .venv/bin/python -m app.cli parse-essay-template --file "$SCHIMMEL_PDF" 2>/dev/null || warn "Template parsing failed"
    fi
    if [ -d "CalBarRules" ]; then
        .venv/bin/python -m app.cli parse-all-rules 2>/dev/null || warn "Rule parsing failed"
    fi
else
    ok "Database already populated: $QUESTION_COUNT questions, $TEMPLATE_COUNT templates, $RULE_COUNT rules"
fi

# ══════════════════════════════════════════════════════════════════════════════
# STEP 8: Launch the application
# ══════════════════════════════════════════════════════════════════════════════
step 8 "Starting CalBar Exam Tutor"

PROVIDER=$(.venv/bin/python -c "
from app.services.analysis import get_analysis_service
svc = get_analysis_service()
print(type(svc).__name__)
" 2>/dev/null || echo "MockAnalysisService")

echo ""
echo -e "  ${BOLD}═══════════════════════════════════════════════${NC}"
echo -e "  ${BOLD}  CalBar Exam Tutor is ready!${NC}"
echo -e "  ${BOLD}═══════════════════════════════════════════════${NC}"
echo ""
echo -e "  ${BOLD}Open in your browser:${NC}"
echo -e "  ${GREEN}${BOLD}  → http://localhost:8000${NC}"
echo ""
if [ "$PROVIDER" = "OllamaAnalysisService" ]; then
    echo -e "  ${GREEN}✓${NC}  AI Engine: Ollama (${OLLAMA_MODEL})"
else
    echo -e "  ${YELLOW}⚠${NC}  AI Engine: Mock (Ollama not available)"
    echo -e "     ${DIM}Install and start Ollama for full AI analysis.${NC}"
fi
echo -e "  ${GREEN}✓${NC}  Questions: $QUESTION_COUNT"
echo -e "  ${GREEN}✓${NC}  Templates: $TEMPLATE_COUNT"
echo -e "  ${GREEN}✓${NC}  Rules: $RULE_COUNT"
echo ""
echo -e "  ${DIM}Press Ctrl+C to stop the server.${NC}"
echo ""

exec .venv/bin/python -m app.cli serve --port 8000 --reload
