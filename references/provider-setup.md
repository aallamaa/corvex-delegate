# Provider setup

Resolve `<skill-dir>` from the installed `SKILL.md`. The CLI is `python3 <skill-dir>/scripts/corvee`.

## Configure

Run `configure` locally to confirm the endpoint and enter the key with hidden input. The default URL is `https://api.tokenfactory.corvex.cloud/v1`. From Codex, use an existing environment variable with `configure --non-interactive --api-key-env CORVEX_API_KEY`, or a user-identified dotenv file through `--from-env-file PATH`. Never request a key in chat.

The wizard fetches the public catalog, optionally selects an exact model, and makes a one-token Chat Completions request to authenticate. Provider charges may apply. Validation must succeed before saving settings. `check` repeats credential/inference validation; `models` only discovers availability.

Settings and credentials are separate files under `${CODEX_HOME:-~/.codex}/corvee/`, both mode 0600. Settings contain `version`, `base_url`, `model`, `api_key_env`, `credentials_file`, and `default_complexity`. The credential file contains only `api_key`.

## Models

- `models [PATTERN]`: print exact live model IDs, optionally filtered.
- `select` with no argument: show current non-secret settings.
- `select MODEL_ID`: validate the ID against the catalog and save it.
- `select auto`: clear the default locally without network access or credentials; do not automatically select another model.

Mission model precedence: `--model`, project `--model-config DELEGATE.json`, `CORVEX_MODEL` from the environment or a dotenv, then the user config. A dotenv must name `CORVEX_MODEL`; a bare `MODEL` is ignored because it collides with other tools' files. `DELEGATE.json` contains only `{"model": "exact-model-id"}`.

The runner accepts temporary URL overrides through `--base-url` or `CORVEX_API_URL`. A dotenv may set the URL only when it also supplies the key, so a repository-local file cannot redirect an externally configured credential. Only use an endpoint the user trusts with their key and repository content.

## Failures

HTTP 401/403 indicates an authentication/access problem. HTTP 404 warrants checking the URL and `/v1` suffix. For unknown models use the live catalog without silently substituting. For unsupported reasoning effort omit `--effort`. Missing tool calls or malformed reports mean incomplete work. Authenticated requests refuse redirects; configure the intended endpoint directly.

The runner uses `POST /chat/completions` with function tools.

## Mechanical-worker inference controls

For Corvex `zai-org/GLM-5.2-FP8`, a live 2026-09-07 probe verified
`chat_template_kwargs: {"enable_thinking": false}` disables reasoning. Use
`run --thinking disabled` for that setting. Native `thinking.type: disabled`
and `reasoning_effort: low` still yielded reasoning-only, length-limited replies
on this endpoint; accepting a field does not prove that it has an effect.
This is not a universal mapping for other providers.

`--max-output-tokens N` forwards `max_tokens`; omission keeps the provider
default. For thinking-enabled work, allow enough output for internal reasoning
and the actual tool call/report. Inspect `finish_reason`, not just a returned
message. Length-limited responses cannot execute tools or pass acceptance.
Controls are restored on resume unless explicitly overridden. No automatic
output-budget escalation occurs.

[Z.AI thinking documentation](https://docs.z.ai/guides/capabilities/thinking-mode)
describes native GLM behavior and reasoning continuity; it does not establish
which fields a third-party Corvex endpoint honors. Provider reasoning fields
remain private in checkpoints and are forwarded for tool-call continuity.


### Kimi thinking control on Corvex

The exact catalog model `moonshotai/Kimi-K2.7-Code` uses
`chat_template_kwargs.thinking` for its thinking toggle. Both `run` and `job`
now route `--thinking enabled|disabled` to that key. GLM retains
`chat_template_kwargs.enable_thinking`; other provider deployments may differ.
A 256-token probe with Kimi `thinking=false` returned JSON in 1.10s, six output
tokens, and zero reasoning bytes. This verifies the control, not coding quality.

The upstream [Moonshot K2.5 examples](https://github.com/MoonshotAI/Kimi-K2.5)
distinguish the native API `thinking.type` setting from the vLLM/SGLang template
`thinking` boolean. Those older-model docs motivated the probe; they do not by
themselves establish behavior for Corvex's K2.7 deployment.
