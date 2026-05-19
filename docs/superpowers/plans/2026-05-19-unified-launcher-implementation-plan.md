# Unified Launcher Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add one interactive launcher for MoneyManager startup scenarios while moving existing startup scripts into `stock_screener/scripts/`.

**Architecture:** Keep startup behavior in focused scripts and make `stock_screener/run_moneymanager.sh` a thin menu and dispatcher. Moved scripts resolve paths from the stock screener root so existing Python modules, `.env`, `.venv`, and runtime directories continue to work.

**Tech Stack:** Bash, Python unittest, FastAPI/Uvicorn, Vite/npm.

---

### Task 1: Move Existing Startup Scripts

**Files:**
- Move: `stock_screener/run_screening.sh` to `stock_screener/scripts/run_screening.sh`
- Move: `stock_screener/run_option_lab_shell.sh` to `stock_screener/scripts/run_option_lab_shell.sh`
- Modify: `stock_screener/tests/test_interactive_option_lab.py`

- [ ] Move the two root startup scripts into `stock_screener/scripts/`.
- [ ] Update each moved script so `SCRIPT_DIR` points to `scripts/`, `STOCK_SCREENER_DIR` points to the parent directory, and all Python entrypoints run from `STOCK_SCREENER_DIR`.
- [ ] Update `test_shell_script_starts_persistent_cli` to load `scripts/run_option_lab_shell.sh`.
- [ ] Run `bash -n` against both moved scripts.
- [ ] Run `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_interactive_option_lab -v`.

### Task 2: Add Unified Launcher

**Files:**
- Create: `stock_screener/run_moneymanager.sh`
- Create: `stock_screener/tests/test_run_moneymanager_script.py`
- Modify: `stock_screener/web_frontend/vite.config.ts`

- [ ] Add a launcher test that runs `run_moneymanager.sh --list` and checks the output contains `screening`, `option`, `backend`, `frontend`, and `web`.
- [ ] Implement the launcher with interactive menu, direct subcommands, backend startup, frontend startup, and full stack cleanup trap.
- [ ] Update Vite proxy configuration to read `VITE_BACKEND_URL`, which the launcher sets from `MM_API_HOST` and `MM_API_PORT`.
- [ ] Make the launcher executable.
- [ ] Run `bash -n stock_screener/run_moneymanager.sh`.
- [ ] Run `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_run_moneymanager_script -v`.

### Task 3: Final Verification

**Files:**
- Verify: `stock_screener/scripts/*.sh`
- Verify: `stock_screener/run_moneymanager.sh`

- [ ] Run shell syntax checks for all startup scripts.
- [ ] Run both launcher-related unittest modules.
- [ ] Run `npm run build` in `stock_screener/web_frontend`.
- [ ] Check `git diff --stat` and confirm only launcher, moved scripts, tests, and superpowers docs changed.
