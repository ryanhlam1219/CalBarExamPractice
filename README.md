# CalBar Exam Tutor

AI-powered California Bar Exam essay practice and analysis system. Browse 156 real CalBar essay questions (2012–2026), write timed essays with a rich text editor, and receive detailed AI-graded feedback with rule funnels, essay highlights, and suggested rewrites.

## Quick Start

```bash
./start.sh
```

That's it. The startup script handles everything:
1. Installs Homebrew, Python 3.12, PostgreSQL, and Ollama (if not already installed)
2. Creates a virtual environment and installs dependencies
3. Sets up the database and loads pre-parsed data (156 questions, 16 Schimmel templates, 5,400+ supplemental rules)
4. Starts the web server at **http://localhost:8000**

No engineering background required — just run the script and open the browser.

## Features

### Practice
- **156 essay questions** from CalBar exams (2012–2026) with official selected answers
- **Rich text editor** (Quill.js) with headings, bold, italic, lists, and blockquotes
- **1-hour countdown timer** with auto-submit
- **Random question** selector with year/month filters

### AI Analysis
- **Two-phase grading**: fast scoring (~30s) then deep analysis (~2-5 min)
- **Scoring rubric**: Issue Spotting /35, Rule Statements /25, Fact Application /30, Organization /10
- **Rule Funnel**: maps question facts to legal elements, shows which the student addressed
- **Essay Review**: passage-level highlights (strength/improvement/missing/structure) with suggested rewrites
- **BM25 RAG retrieval**: finds the most relevant rules for each specific question
- **Selected answer calibration**: compares student work against official passing essays
- **Ask AI**: follow-up chat grounded in the saved analysis

### Data
- **16 Schimmel templates** parsed from Prof. Schimmel's essay template PDF (510 template rules)
- **5,400+ supplemental rules** parsed from MyThemis Learners outlines (all 16 subjects)
- **312 official selected answers** (2 per question) used for scoring calibration
- **BM25 indexes** for question-specific rule and passage retrieval

### Tracking
- **Analysis History** with score trend charts, radar breakdowns, and per-subject performance cards
- **Subject gap tracker** showing which of the 16 subjects you've practiced
- **Re-analyze button** to re-run analysis with the latest prompts
- **Data Browser** to inspect templates, nodes, and rules with clickable modals
- **Grading Context tab** showing exactly what the AI received (template hierarchy, rules, selected answer passages, essay structure)

## Architecture

```
Question → Subject Mapper → Schimmel Template + BM25 Rules + Selected Answer Passages
                                        ↓
                              Phase 1: Scoring (temperature=0)
                                        ↓
                              Phase 2: Issues, Rule Funnels, Essay Review
                                        ↓
                              Results Page (6 tabs)
```

- **Backend**: Python 3.12, FastAPI, SQLAlchemy 2.0, PostgreSQL 16
- **AI**: Ollama (local LLM, default: gemma4:31b-cloud) with mock fallback
- **Frontend**: Server-rendered Jinja2 templates, Chart.js, Quill.js
- **Retrieval**: BM25Okapi (rank_bm25) for rule and passage relevance ranking

## Manual Setup (alternative to start.sh)

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
cp .env.example .env

# Start PostgreSQL (Docker or Homebrew)
docker compose up -d postgres
# or: brew install postgresql@16 && brew services start postgresql@16

# Initialize database and load pre-parsed data
python -m app.cli init-db
python -m app.cli load-seed

# Start Ollama (optional, for AI analysis)
ollama serve &
ollama pull gemma4:31b-cloud

# Launch
python -m app.cli serve --port 8000 --reload
```

## CLI Commands

| Command | Description |
|---------|-------------|
| `init-db` | Create database tables |
| `load-seed` | Load pre-parsed questions, templates, and rules from JSON |
| `serve` | Start the web server |
| `run-pipeline` | Download and parse CalBar PDFs |
| `parse-essay-template` | Parse a Schimmel template PDF |
| `parse-all-rules` | Parse all MyThemis outline PDFs from CalBarRules/ |
| `discover-calbar` | List available CalBar PDFs |
| `download-calbar` | Download CalBar PDFs |

## Tests

```bash
python -m pytest tests/ -q
# 76 passed
```

## Data Policy

- Official CalBar PDFs are public documents downloaded from the CalBar website
- Pre-parsed JSON data (questions + selected answers) is included in the repo
- Schimmel template PDF and MyThemis outline PDFs are gitignored (private use only)
- Student essay submissions are stored locally in PostgreSQL, never transmitted
- The `.env` file with database credentials is gitignored
