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

# Track which services this script started (so we clean them up on exit)
STARTED_POSTGRES=false
STARTED_OLLAMA=false

banner

# ── Pre-flight checks ────────────────────────────────────────────────────────

# macOS: ensure Xcode CommandLineTools are installed (needed for git, clang, etc.)
if [[ "$(uname)" == "Darwin" ]] && ! xcode-select -p &>/dev/null; then
    echo -e "${YELLOW}    Xcode Command Line Tools are required.${NC}"
    echo -e "    A system dialog may appear — click ${BOLD}Install${NC} and wait for it to finish."
    echo -e "    ${DIM}This is a one-time install and may take 5-10 minutes.${NC}"
    echo ""
    xcode-select --install 2>/dev/null || true
    echo ""
    echo -e "    Press ${BOLD}Enter${NC} after the installation completes..."
    read -r
fi

# Ensure curl is available
if ! command -v curl &>/dev/null; then
    fail "curl is required but not found.\n    macOS: xcode-select --install\n    Linux: sudo apt install curl"
fi

# Ensure data directories exist (gitignored, won't exist on fresh clone)
mkdir -p data/input data/raw data/extracted data/parsed data/manifests

# Skip Homebrew interactive confirmation prompts
export HOMEBREW_NO_AUTO_UPDATE=1

# ══════════════════════════════════════════════════════════════════════════════
# STEP 1: Homebrew (macOS package manager)
# ══════════════════════════════════════════════════════════════════════════════
step 1 "Package manager (Homebrew)"

ensure_brew() {
    if command -v brew &>/dev/null; then return 0; fi
    info "Homebrew is a package manager that installs tools for you."
    info "You may be asked for your Mac password — this is normal."
    echo ""
    NONINTERACTIVE=1 /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
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
            fail "Python 3.12+ not found. Install it with:\n    sudo apt install python3.12 python3.12-venv"
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
    .venv/bin/pip install -q -e . 2>/dev/null || fail "Failed to install Python dependencies.\n    Check your internet connection and try again."
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
    if psql -h localhost -p 5432 -U calbar -d calbar_tutor -c "SELECT 1" &>/dev/null; then
        return 0
    fi
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
    STARTED_POSTGRES=true
elif start_pg_brew; then
    ok "PostgreSQL started via Homebrew"
    STARTED_POSTGRES=true
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

# Test if a model actually works (not just downloaded)
ollama_model_works() {
    local test_response
    test_response=$(curl -sf --max-time 15 http://localhost:11434/api/chat -d "{
        \"model\": \"$OLLAMA_MODEL\",
        \"messages\": [{\"role\": \"user\", \"content\": \"Say OK\"}],
        \"stream\": false
    }" 2>/dev/null) || return 1
    echo "$test_response" | grep -q "message" 2>/dev/null
}

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
        OLLAMA_PID=$!
        for i in $(seq 1 15); do
            ollama_running && break
            sleep 1
        done
        STARTED_OLLAMA=true
    fi

    if ollama_running; then
        ok "Ollama is running"

        # 1. Already pulled and working — done silently
        MODEL_READY=false
        if ollama list 2>/dev/null | grep -qF "$OLLAMA_MODEL"; then
            info "Verifying model $OLLAMA_MODEL..."
            if ollama_model_works; then
                ok "Model $OLLAMA_MODEL is ready"
                MODEL_READY=true
            fi
        fi

        # 2. Not ready — try pulling (works for local models and logged-in users)
        if [ "$MODEL_READY" = false ]; then
            info "Pulling model $OLLAMA_MODEL..."
            ollama pull "$OLLAMA_MODEL" 2>/dev/null || true
            if ollama_model_works; then
                ok "Model $OLLAMA_MODEL is ready"
                MODEL_READY=true
            fi
        fi

        # 3. Cloud model still not working — needs account login
        if [ "$MODEL_READY" = false ] && [[ "$OLLAMA_MODEL" == *"cloud"* ]]; then
            echo ""
            echo -e "    ${BOLD}The AI model needs an Ollama account to run.${NC}"
            echo -e "    ${DIM}You have two options:${NC}"
            echo ""
            echo -e "    ${GREEN}[1] Cloud model (recommended)${NC}"
            echo -e "        Uses Ollama cloud — faster and higher quality."
            echo -e "        Requires a free account at https://ollama.com (takes 30 seconds)."
            echo ""
            echo -e "    ${YELLOW}[2] Local model${NC}"
            echo -e "        Runs entirely on your computer — no account needed."
            echo -e "        Slower, but works completely offline."
            echo ""
            ai_choice=""
            while true; do
                read -rp "    Enter 1 or 2: " ai_choice
                case "$ai_choice" in
                    1) break ;;
                    2) break ;;
                    *) echo -e "    ${RED}Please enter 1 or 2.${NC}" ;;
                esac
            done
            echo ""

            if [[ "$ai_choice" == "1" ]]; then
                info "Opening Ollama login — a browser window will open."
                info "Sign up or log in, then come back here."
                echo ""
                ollama login || {
                    warn "Ollama login failed. You can try again later with: ollama login"
                }
                echo ""
                info "Pulling cloud model $OLLAMA_MODEL..."
                if ollama pull "$OLLAMA_MODEL" && ollama_model_works; then
                    ok "Cloud model $OLLAMA_MODEL is ready"
                    MODEL_READY=true
                else
                    warn "Cloud model not working. Try 'ollama login' again or switch to local."
                fi
            else
                LOCAL_MODEL="gemma3:4b"
                info "Downloading local model $LOCAL_MODEL (~3GB)..."
                echo -e "    ${DIM}This may take a few minutes. Good time for a coffee break.${NC}"
                echo ""
                if ollama pull "$LOCAL_MODEL"; then
                    OLLAMA_MODEL="$LOCAL_MODEL"
                    sed -i '' "s/^CALBAR_OLLAMA_MODEL=.*/CALBAR_OLLAMA_MODEL=$LOCAL_MODEL/" .env 2>/dev/null || true
                    ok "Local model $LOCAL_MODEL ready"
                    MODEL_READY=true
                else
                    warn "Failed to download local model."
                fi
            fi
        fi

        # 4. Local model not working (non-cloud) — just report
        if [ "$MODEL_READY" = false ] && [[ "$OLLAMA_MODEL" != *"cloud"* ]]; then
            warn "Could not set up model '$OLLAMA_MODEL'."
            info "Check your internet connection and try: ollama pull $OLLAMA_MODEL"
        fi

        if [ "$MODEL_READY" = false ]; then
            warn "AI analysis will use simplified mock grading for now."
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

# Final data check — warn if empty
if [ "$QUESTION_COUNT" = "0" ]; then
    warn "No essay questions were loaded."
    warn "The app will start but the Practice page will be empty."
    info "Try running: .venv/bin/python -m app.cli run-pipeline --year 2025 --month february"
fi

# ══════════════════════════════════════════════════════════════════════════════
# STEP 8: Launch the application
# ══════════════════════════════════════════════════════════════════════════════
step 8 "Starting CalBar Exam Tutor"

# Check if port 8000 is already in use
if lsof -i :8000 &>/dev/null; then
    warn "Port 8000 is already in use by another application."
    info "Either stop the other application or change the port:"
    info "  .venv/bin/python -m app.cli serve --port 8001"
    echo ""
fi

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
    echo -e "     ${DIM}Set up Ollama for full AI analysis (see Step 6 above).${NC}"
fi
echo -e "  ${GREEN}✓${NC}  Questions: $QUESTION_COUNT"
echo -e "  ${GREEN}✓${NC}  Templates: $TEMPLATE_COUNT"
echo -e "  ${GREEN}✓${NC}  Rules: $RULE_COUNT"
echo ""
echo -e "  ${DIM}Press Ctrl+C to stop the server and all services.${NC}"
echo ""

cleanup() {
    echo ""
    echo -e "${BOLD}Shutting down...${NC}"
    if [ "$STARTED_OLLAMA" = true ]; then
        info "Stopping Ollama..."
        pkill -f "ollama serve" 2>/dev/null || true
        ok "Ollama stopped"
    fi
    if [ "$STARTED_POSTGRES" = true ]; then
        info "Stopping PostgreSQL..."
        brew services stop postgresql@16 2>/dev/null || brew services stop postgresql 2>/dev/null || docker compose stop postgres 2>/dev/null || true
        ok "PostgreSQL stopped"
    fi
    echo -e "${GREEN}All services stopped. Goodbye!${NC}"
}

trap cleanup EXIT

.venv/bin/python -m app.cli serve --port 8000 --reload
