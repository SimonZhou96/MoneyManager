# Account provider settings and UUID rollout

## Account IDs and config storage

`web_users.id` remains the internal auto-increment primary key. New accounts
receive a UUID v4 in `web_users.user_id`; login APIs and all ownership columns use
that UUID as a string. Provider settings are stored per `(user_id, provider_key)`
in `web_account_provider_configs` and encrypted with AES-GCM before they reach
MySQL. The API returns only whether a secret is configured, never its value.

Account provider settings include the LLM provider order and OpenAI-compatible,
Codex Responses, and DeepSeek credentials; search order, Tavily, and Zhipu Web
Search; FRED, Funda, market-intel source order; and Feishu webhook/app settings.
MySQL, Redis, session, agent, and other infrastructure settings stay in the
deployment environment. A user task with an account context does not fall back
to another account's key or a process-wide provider key. Scheduled jobs without
an account context continue to read their existing environment configuration.

## Deploying to an existing database

Use a short maintenance window because the migration replaces indexed
`user_id` columns. Stop the web server and RQ/background workers, allow current
screening jobs to finish or stop them, and take a verified database backup.

Create a stable high-entropy encryption key once and place the same value in
the secret store for every web and worker process. For example, generate a
candidate with `openssl rand -base64 32`; do not commit it or print it in logs.
Set it as `ACCOUNT_CONFIG_ENCRYPTION_KEY` before restarting any process. Losing
or changing this key makes saved provider credentials unreadable.

From `stock_screener/`, run:

```sh
python scripts/migrate_account_user_ids_to_uuid.py
```

The script prompts for `MIGRATE-UUID`, assigns UUIDs to existing accounts,
assigns UUID v4 values to existing accounts, maps ownership rows using the old internal `web_users.id`, converts the
`user_id` columns to `CHAR(36)`, and creates the provider-config table. It
stops if an ownership row cannot be mapped. It is safe to rerun after an
interrupted conversion; completed UUID columns are left in place.

Deploy the updated application and SQL files, start the web server and workers,
then confirm login and the API 配置 page. The local and ECS databases must be
migrated independently during their own maintenance windows. Do not run this
script from application startup.

For a new database, the updated `sql/005_web_platform.sql`,
`sql/013_option_lab.sql`, and `sql/014_quant_lab.sql` create UUID ownership
columns directly.

## Connection-test request budget

The UI sends no provider request until the user clicks 测试连接. Each click
makes at most one request to the selected service with an 8-second read timeout
and no automatic retry. Search tests request one result. LLM tests send one
short prompt. FRED and Funda tests make one lookup request. The Feishu webhook
test posts one visible test message; the Feishu app test only requests a tenant
token. These checks do not run during screening.
