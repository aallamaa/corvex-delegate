# Provider setup

Resolve `<skill-dir>` from the installed `SKILL.md`. The CLI is `python3 <skill-dir>/scripts/corvex-delegate`.

## Configure

Run `configure` locally to confirm the endpoint and enter the key with hidden input. The default URL is `https://api.tokenfactory.corvex.cloud/v1`. From Codex, use an existing environment variable with `configure --non-interactive --api-key-env CORVEX_API_KEY`, or a user-identified dotenv file through `--from-env-file PATH`. Never request a key in chat.

The wizard fetches the public catalog, optionally selects an exact model, and makes a one-token Chat Completions request to authenticate. Provider charges may apply. Validation must succeed before saving settings. `check` repeats credential/inference validation; `models` only discovers availability.

Settings and credentials are separate files under `${CODEX_HOME:-~/.codex}/corvex-delegate/`, both mode 0600. Settings contain `version`, `base_url`, `model`, `api_key_env`, `credentials_file`, and `default_complexity`. The credential file contains only `api_key`.

## Models

- `models [PATTERN]`: print exact live model IDs, optionally filtered.
- `select` or `show`: show current non-secret settings.
- `select MODEL_ID`: validate the ID against the catalog and save it.
- `select auto`: clear the default; do not automatically select another model.

Mission model precedence: `--model`, project `--model-config DELEGATE.json`, `CORVEX_MODEL`/dotenv, user config. `DELEGATE.json` contains only `{"model": "exact-model-id"}`.

The runner accepts temporary URL overrides through `--base-url`, `CORVEX_API_URL`, or dotenv `API_URL`. Only use an endpoint the user trusts with their key and repository content.

## Failures

HTTP 401/403 indicates an authentication/access problem. HTTP 404 warrants checking the URL and `/v1` suffix. For unknown models use the live catalog without silently substituting. For unsupported reasoning effort omit `--effort`. Missing tool calls or malformed reports mean incomplete work. Authenticated requests refuse redirects; configure the intended endpoint directly.

The runner uses `POST /chat/completions` with function tools. Optional Responses/native diagnostics are described in [native-compatibility.md](native-compatibility.md) and are not required.
