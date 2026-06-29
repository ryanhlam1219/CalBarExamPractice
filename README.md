# CalBar Exam Tutor

AI-powered California Bar Exam essay practice and analysis system. Browse 156 real CalBar essay questions (2012-2026), write timed essays with a rich text editor, and receive detailed AI-graded feedback with rule funnels, essay highlights, and suggested rewrites.

## Quick Start

```bash
./start.sh
```

That's it. The startup script handles everything:
1. Installs Homebrew, Python 3.12, PostgreSQL, and Ollama (if not already installed)
2. Creates a virtual environment and installs dependencies
3. Sets up the database and loads pre-parsed data (156 questions, 16 Schimmel templates, 5,400+ supplemental rules)
4. Starts the web server at **http://localhost:8000**

No engineering background required.

---

## How It Works

```
                          ┌─────────────────────┐
                          │   Practice Page      │
                          │  156 CalBar Essays   │
                          │  (2012-2026)         │
                          └─────────┬───────────┘
                                    │ Student writes essay
                                    ▼
                          ┌─────────────────────┐
                          │   Subject Matcher    │
                          │  Official labels +   │
                          │  keyword scoring     │
                          └─────────┬───────────┘
                                    │ Maps to 1 of 16 subjects
                                    ▼
              ┌─────────────────────────────────────────────┐
              │           Context Assembly                   │
              │                                             │
              │  ┌──────────┐ ┌──────────┐ ┌─────────────┐ │
              │  │ Schimmel  │ │  BM25    │ │  Selected   │ │
              │  │ Template  │ │  Rules   │ │  Answers    │ │
              │  │ 510 rules │ │ 5,400+   │ │  312 total  │ │
              │  └──────────┘ └──────────┘ └─────────────┘ │
              └─────────────────┬───────────────────────────┘
                                │
                                ▼
              ┌─────────────────────────────────────────────┐
              │           Two-Phase AI Analysis              │
              │                                             │
              │  Phase 1: Scoring        (~30 seconds)      │
              │  ├─ Overall score /100                      │
              │  ├─ Issue Spotting /35                      │
              │  ├─ Rule Statements /25                     │
              │  ├─ Fact Application /30                    │
              │  └─ Organization /10                        │
              │                                             │
              │  Phase 2: Deep Analysis  (~2-5 minutes)     │
              │  ├─ Issue-by-issue breakdown                │
              │  ├─ Rule funnels with elements              │
              │  ├─ Essay highlights with rewrites          │
              │  └─ Missing issue detection                 │
              └─────────────────┬───────────────────────────┘
                                │
                                ▼
                      ┌─────────────────────┐
                      │   Results Page       │
                      │   (6 tabs)           │
                      └─────────────────────┘
```

---

## Features

### Practice
- **156 essay questions** from CalBar exams (2012-2026) with official selected answers
- **Rich text editor** (Quill.js) with headings, bold, italic, lists, and blockquotes
- **1-hour countdown timer** with auto-submit
- **Subject gap tracker** showing which of the 16 subjects you've practiced

### AI Analysis Pipeline

```
┌──────────────────┐     ┌──────────────────┐     ┌──────────────────┐
│  Schimmel         │     │  MyThemis         │     │  Selected        │
│  Templates        │     │  Outlines         │     │  Answers         │
│                   │     │                   │     │                  │
│  16 subjects      │     │  16 PDFs parsed   │     │  312 official    │
│  510 template     │     │  5,400+ rules     │     │  passing essays  │
│  rules            │     │  BM25 indexed     │     │  BM25 ranked     │
└────────┬─────────┘     └────────┬─────────┘     └────────┬─────────┘
         │                        │                         │
         └────────────────────────┼─────────────────────────┘
                                  │
                                  ▼
                    ┌──────────────────────────┐
                    │  Prompt Builder           │
                    │                          │
                    │  Template hierarchy       │
                    │  + Top 15 relevant rules  │
                    │  + Top 6 answer passages  │
                    │  + Essay structure summary │
                    │  + Scoring rubric          │
                    └────────────┬─────────────┘
                                 │
                                 ▼
                    ┌──────────────────────────┐
                    │  Ollama (Local LLM)       │
                    │  gemma4:31b-cloud         │
                    │                          │
                    │  Phase 1: temp=0 (score)  │
                    │  Phase 2: temp=0.3 (deep) │
                    └──────────────────────────┘
```

### Results (6 Tabs)

| Tab | What it shows |
|-----|--------------|
| **Issue Analysis** | Table: each issue spotted? Rule stated? Facts applied? Per-issue feedback |
| **Essay Review** | Your essay with highlighted passages. Click highlights to jump between essay and notes |
| **Rule Funnel** | Visual: Issue → Rule Statement → Elements. Maps question facts to legal elements |
| **Feedback** | Strengths, areas for improvement, overall summary |
| **Grading Context** | Full transparency: template hierarchy, rules, selected answer passages, flow diagram |
| **Ask AI** | Chat follow-up grounded in your saved analysis |

### Essay Review Highlight Types

```
┌─────────────────────────────────────────────────────┐
│  ██ Strength      Strong rule statement or analysis │
│  ██ Improvement   Needs better precision + rewrite  │
│  ██ Missing       Issue not addressed at all        │
│  ██ Structure     IRAC organization feedback        │
└─────────────────────────────────────────────────────┘
```

### Tracking
- **Score trend chart** with per-subject filtering
- **Radar chart** showing breakdown across 4 grading dimensions
- **Subject performance cards** with average scores and attempt counts
- **Re-analyze button** to re-run with the latest prompts

---

## Scoring Rubric

```
Issue Spotting (0-35)
├── 30-35  All major issues identified
├── 20-29  Most issues found, 1-2 missed
├── 10-19  Several key issues missed
└──  0-9   Fundamental issues missed

Rule Statements (0-25)
├── 20-25  Precise rules with correct elements
├── 12-19  Rules stated but imprecise
└──  0-11  Rules missing or incorrect

Fact Application (0-30)
├── 25-30  Facts applied to each element
├── 15-24  Some application but gaps
└──  0-14  Conclusory or missing

Organization (0-10)
├──  8-10  Clear IRAC with headings
├──  5-7   Identifiable structure
└──  0-4   Disorganized

Overall (0-100)
└── 75+ is passing quality
```

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    Web Application                       │
│                                                         │
│  FastAPI + Jinja2 Templates + Quill.js + Chart.js       │
│                                                         │
│  Routes:                                                │
│  /           Practice (question browser)                │
│  /exam/:id   Timed essay editor                         │
│  /results/:id Analysis results (6 tabs)                 │
│  /history/   Score trends + subject tracking             │
│  /data       Template + rule browser                    │
│  /guide      User guide                                 │
└──────────────────────┬──────────────────────────────────┘
                       │
         ┌─────────────┼─────────────┐
         ▼             ▼             ▼
┌──────────────┐ ┌──────────┐ ┌───────────┐
│ PostgreSQL   │ │  Ollama  │ │  BM25     │
│              │ │          │ │  Index    │
│ Questions    │ │ gemma4:  │ │           │
│ Templates    │ │ 31b-cloud│ │ 5,400+    │
│ Rules        │ │          │ │ rules     │
│ Submissions  │ │ Local    │ │ ranked    │
│ Analyses     │ │ only     │ │ per query │
└──────────────┘ └──────────┘ └───────────┘
```

---

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
- All AI analysis runs locally via Ollama — no data leaves your machine
