# Phase 4A Worker/adapter foundation

This package defines the new isolated read-only adapter contract and a serial Worker runtime for the existing private HTTP lease protocol. It lives under `unified_system/`; legacy `platforms/`, `api/`, `fuckZHS-master/`, and executable projects are not imported or modified.

## Safety boundary

The adapter interface exposes credential validation, explicit authorization lifecycle hooks, course/catalog reads, resource reads and learning-progress reads. It intentionally has **no** methods for video progress writes, study completion, attendance mutation, quizzes, exams, submissions, or answer generation. Workers only start if a concrete adapter advertises at least one verified task kind; no concrete platform Worker is registered yet.

The runtime sends only `X-Worker-Platform`, `X-Worker-ID`, and `X-Worker-Token` to `/api/internal/workers/*`; it never uses browser sessions. Credentials arrive only in the claim response, are held in a `repr=False` context, are never logged, and are not included in task summaries. The Worker clears the credential mapping and lease token from its consumed envelope in a `finally` block; Python cannot guarantee zeroization of immutable string copies. Each process executes one task at a time, heartbeats its database lease while an adapter runs, and signals cooperative cancellation. If the lease heartbeat is lost or an adapter ignores cancellation past the grace period, the Worker stops rather than claiming unrelated work concurrently. Normal shutdown sends an authenticated offline notification; crashed Workers are fenced by heartbeat/lease expiry and the persisted lease reaper. Platform methods must use bounded network timeouts and call the cancellation checkpoint between requests.

## Contract status

`unified_system.platforms.common.PlatformAdapter` is the versioned foundation. `AdapterCapabilities` defaults every capability to false. A platform module must separately declare its auth methods, supported read task kinds, verification time, and health check only after tests demonstrate those behaviors. Planned/unverified platform code must not be registered with the Worker runtime.

`WorkerSettings.from_env()` accepts a platform-specific token (`WORKER_TOKEN_CHAOXING`, `WORKER_TOKEN_ZHIHUISHU`, or `WORKER_TOKEN_YINGHUA`) of at least 32 characters. Server configuration may bind a token to a Worker ID using `WORKER_TOKENS["<platform>:<worker_id>"]`; when any worker-specific token is configured for a platform, other IDs cannot fall back to the shared platform token. Existing platform-wide tokens remain supported for compatibility if no specific keys are configured. The client prevents non-TLS remote API URLs; plain HTTP is allowed only for the private Compose service name or loopback development. Worker credentials are excluded from settings repr and logs.

## Current implementation state

- Implemented here: capability/course/auth/error models, abstract adapter methods, explicit registry, environment validation, authenticated worker HTTP client, task runner, heartbeat/cancel loop and fake-client tests.
- Implemented: minimal per-account course catalog persistence and read API (`GET /api/v1/courses`) with personal/shared-grant isolation; bounded course sync payload is accepted only through a valid Worker task lease. A personal-account verification task endpoint exists but is gated on the server platform catalog exposing `account_validate`.
- Task lifecycle/status events are persisted in the existing SQL `Task`/`TaskEvent` and `AccountExecutionLease` models; no in-memory replacement queue was introduced. There is no numeric percent-progress API yet—do not synthesize percentages. SQLite migrations are checked; a live PostgreSQL server/concurrency run remains outstanding.
- Not implemented/verified yet: registered/usable platform adapters (Chaoxing and the new Zhihuishu course-list candidate remain capability-disabled; Yinghua remains source-blocked), actual platform credential-validation test for either account mode, course detail/resource/progress persistence APIs, Docker Compose build. The Zhihuishu adapter uses offline Mock coverage only; do not treat it as real authorization or a verified integration.
- `workers/Dockerfile` builds the generic runtime image, but has no built-in platform adapter. Startup fails closed unless `WORKER_ADAPTER_FACTORY=package.module:factory` points to a verified adapter.
- A missing adapter or unsupported capability remains unavailable; no worker should be started with placeholder capabilities. Docker build/run was not available for validation in this environment.
