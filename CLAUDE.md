# MoneyManager

Stock screening and analysis platform. The core app is `stock_screener/` — a database-driven rule engine that screens stocks across HK/US/A markets, with optional post-screening LLM signal analysis and Feishu delivery.

## Project Memory

Detailed context lives in the memory directory. Read the relevant files before working on these subsystems:

- **[Stock Screener Rule Engine](memory/stock-screener-rule-engine.md)** — Rule chain JSON DSL, `screening_rule_metadata` / `screening_rule_chains` tables, built-in filters and strategies, modification protocol.
- **[Signal Analysis Chain](memory/signal-analysis-chain.md)** — Post-screening search + LLM pipeline, provider fallback chain (`OpenAICompatible` / `Codex` / `DeepSeek`), environment variable reference, API quota discipline, artifact rules.
- **[Stock Sector Enrichment](memory/stock-sector-enrichment.md)** — Sector/industry enrichment pipeline, provider priority order, merge rules.
- **[Skill Abstraction Guidance](memory/skill-abstraction-guidance.md)** — When to propose new project skills from repeatable workflows.

## Key Conventions

- **Failure isolation**: AI analysis and sector enrichment failures must never block CSV generation or Feishu delivery.
- **Sync SQL and Python**: Whenever default rules/chains/schema change, update both the `sql/` deployment files and the Python default constants.
- **Quota awareness**: Before adding external API calls, estimate call counts for a realistic batch (e.g., 100 stocks) and prefer batching.
- **Environment variables**: Provider configuration is centralized in `signal_analysis/factories.py` — never add provider-specific branches in chain code.
