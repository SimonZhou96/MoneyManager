# Unified Launcher Design

## Goal

Provide one interactive shell entrypoint for MoneyManager stock screener, option lab, agent, and Quant Lab web startup scenarios.

## Scope

The root user-facing entrypoint is `stock_screener/run_moneymanager.sh`. Existing startup scripts are organized under `stock_screener/scripts/` and remain reusable by the unified launcher.

## Design

`run_moneymanager.sh` presents a numbered menu when launched without arguments. Each menu item delegates to a focused script or command:

- Stock screener: `scripts/run_screening.sh`
- Option Lab shell: `scripts/run_option_lab_shell.sh`
- Python local agent: `scripts/run_local_agent.sh`
- Go agent worker: `scripts/run_go_agent.sh`
- Go agent one-shot worker: `scripts/run_go_agent_once.sh`
- Go agent tests: `scripts/test_go_agent.sh`
- Backend API: `python -m uvicorn web.main:app`
- Frontend web: `npm run dev` in `web_frontend`
- Full stack web: starts backend in the background and frontend in the foreground, with cleanup on exit

The unified launcher also supports direct non-interactive subcommands such as `screening`, `option`, `backend`, `frontend`, and `web` so it can be used from automation.

The frontend proxy reads `VITE_BACKEND_URL`, which the launcher derives from `MM_API_HOST` and `MM_API_PORT`. This keeps frontend API requests aligned with custom backend host or port choices.

## Script Organization

Move top-level startup scripts into `stock_screener/scripts/`:

- `stock_screener/run_screening.sh` to `stock_screener/scripts/run_screening.sh`
- `stock_screener/run_option_lab_shell.sh` to `stock_screener/scripts/run_option_lab_shell.sh`

Because these scripts currently assume they live in `stock_screener/`, update their path handling after the move so `.env`, `.venv`, Python modules, and runtime directories still resolve from the stock screener root.

## Error Handling

The launcher exits on command failures and prints the command list for `--list` or `--help`. Full stack startup traps termination and stops the background backend process.

## Verification

Verification covers shell syntax, launcher command listing, updated option lab shell script path, the existing option lab script smoke test, and the frontend production build.
