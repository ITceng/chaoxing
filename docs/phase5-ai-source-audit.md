# Phase 5 — AI implementation and security audit (initial)

Date: 2026-11-01
Branch baseline: `arena/bd5e1acd-chaoxing`, before Phase 5 changes: `2a8a1c4`.

This is a static source/configuration audit only. No provider endpoint was contacted, no provider credential was used, and no legacy connection test was run. A code path or SDK dependency is not evidence of a working provider account.

## Scope and findings

- The requested root-level `utils/{ai,bailian,sensenova,volcengine,flow}.py` files do not exist. The similarly named legacy implementation is under `Yinghua_auto_answer_script-main/backend/ai-ocs-question_bank-main/utils/`.
- That legacy package dispatches among DashScope/Bailian, SenseNova, Volcengine Ark, and SiliconFlow. These functions are synchronous question-answer helpers with static or locally configured model names; source inspection does not establish live connectivity, current model availability, streaming support, or trustworthy token usage.
- The old SiliconFlow helper logs the complete request headers (including its Authorization value), request/prompt body, and response details, and disables TLS certificate verification (`verify=False`). The SenseNova helper prints a partial key and response/error content; other helpers print response text and/or tracebacks. These modules must not be imported or called by the unified service as-is. Their question/answer prompts are also not suitable for reuse as a general learning-assistant conversation API.
- `api/answer.py` contains a legacy AI/answer path associated with the older course-answer workflow. It is not a safe substitute for the new assistant and must not be wired into user-facing study conversations or assessment answers.
- A tracked legacy `config.json` contained two non-empty strings matching a provider API-key format; neither was verified with a provider, and their values are intentionally not reproduced here. They are treated as potentially live credentials. Seven key-shaped strings found in `tests/test_connection.py` look like placeholders; those tests were not executed because they may make external calls.
- The key-shaped strings were present in Git history beginning at commit `08062a3326dc92ea2e2eeae8762c5a65ddf63b19`. Removing the current tracked file does not erase those historical blobs. The credential owner must revoke/rotate the affected provider credentials and review any other clones, logs, or artifacts. This branch does not rewrite shared Git history.
- Before this audit there was no AI blueprint/API registered in `unified_system`, and the unified frontend had no AI assistant page. Phase 2 already provides `AIProviderConfig` and `AIUsageLog`, but there are no model catalog, quota, conversation, or message tables. Provider configs have nullable `owner_user_id`, provider, endpoint, model, encrypted secret, and enabled state; usage logs store user/provider/model/purpose/status plus input/output token counts.
- Existing credential helpers (`encrypt_secret`/`decrypt_secret`) use the platform credential-encryption key. AI-provider secrets should have their own production-managed encryption key rather than silently sharing the platform credential key. Any configurable endpoint must be constrained against SSRF (scheme/host allowlist, no localhost/private/link-local targets, and redirect protection).

## Immediate containment

- Removed the legacy `config.json` from the versioned tree, retained a sanitized `config.example.json`, and ignored local `config.json` files. The workspace copy was scrubbed as well. This protects the current tree and future commits, but does **not** remove the historical copy or revoke the provider credentials.
- No secrets are copied into application logs, documentation, or this report.

## Reuse boundary and next implementation steps

Reuse the unified database, existing authentication/session and permission framework, encrypted-secret primitives after separating the AI key, and the existing Vue console. Do not import the legacy question-answer modules.

The first provider layer now exists in `unified_system/services/ai_providers.py`: typed chat/usage/capability contracts, deterministic offline Mock provider, and a synchronous JSON OpenAI-compatible adapter. Endpoint URLs are restricted to exact reviewed HTTPS host/path pairs; redirects are disabled, TLS verification is mandatory, response bodies are bounded, and provider error bodies/credentials are not exposed. Token counts are accepted only when provider-reported; missing counts remain unavailable. This adapter advertises no streaming, tools, vision, structured output, or file-upload capability. The host/path set and protocol behavior are implementation candidates, **not** proof that any account/model is reachable or verified. No model catalog is published yet.

Next, separate AI encryption-key configuration from platform credential encryption; add admin-only provider/model management with write-only secret rotation and sanitized connection tests; then quotas/usage, private conversation/message APIs, existing Vue console integration, bounded document parsing, and security/authorization tests in that order. Until an operator configures and verifies a real provider, expose only Mock/unverified status.

## Validation status

- Static review and metadata-only secret scanning completed; no key values were emitted.
- Historical scan identified the commit above; history remains unchanged.
- Provider contract/adapter tests use mocked HTTP only; live provider checks and legacy provider tests: **not run**.
- OS/deployment environment: Debian 12 in the prior Phase 4 validation; this audit does not claim Ubuntu or production validation.
