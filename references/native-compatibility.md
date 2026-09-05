# Native Codex compatibility

Live test on 2026-09-04, Codex CLI 0.153.2:

| Test | Result |
| --- | --- |
| OpenAI/GPT-6 Astra parent spawns custom GLM/Corvex agent | Failed: GLM model applied but child retained provider `openai` |
| Separate Codex process using provider `corvex` | Passed: shell read returned the fixture marker and correct operand sum |
| Corvex parent asked to spawn a native child | Inconclusive; no child observed before the test was stopped |

The failed child's session metadata recorded the configured custom role and `model_provider=openai`; its turn model was `zai-org/GLM-5.2-FP8`. The parent selected the custom role with `fork_turns=none`. Inference failed with HTTP 400 because the GLM model was not supported with ChatGPT account authentication.

This is evidence for that build, not a universal statement about future versions. Inspect a real child's recorded provider and task results before claiming native cross-provider support. An API probe only verifies the provider protocol.

## Experimental helpers

`scripts/configure_corvee.py check-responses MODEL_ID` probes the API. `scripts/configure_corvee.py install-agent MODEL_ID` modifies user-level Codex configuration and installs a custom-agent file. Use it only when explicitly investigating native integration; it is not part of normal installation. `agent-status` reports configuration presence, not working delegation.

The auth helper prints the bearer token to its caller for Codex's command-backed authentication. Do not invoke it in chat-visible terminal output or include its output in reports.

References: [Codex subagents](https://developers.openai.com/codex/subagents), [Corvex API](https://docs.corvex.cloud/api-reference/openapi).
