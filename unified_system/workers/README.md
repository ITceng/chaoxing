# Phase 4A Worker/adapter foundation

This package defines the new isolated read-only adapter contract and a serial Worker runtime for the existing private HTTP lease protocol. It lives under `unified_system/`; legacy `platforms/`, `api/`, `fuckZHS-master/`, and executable projects are not imported or modified.

## Safety boundary

The adapter interface exposes credential validation, explicit authorization lifecycle hooks, course/catalog reads, resource reads and learning-progress reads. It intentionally has **no** methods for video progress writes, study completion, attendance mutation, quizzes, exams, submissions, or answer generation. Workers only start if a concrete adapter advertises at least one verified task kind; no concrete platform Worker is registered yet.

The runtime sends only `X-Worker-Platform`, `X-Worker-ID`, and `X-Worker-Token` to `/api/internal/workers/*`; it never uses browser sessions. Credentials arrive only in the claim response, are held in a `repr=False` context, are never logged, and are not included in task summaries. Each process executes one task at a time, heartbeats its lease while an adapter runs, and signals cooperative cancellation. Platform methods must use bounded network timeouts and call the cancellation checkpoint between requests.

## Contract status

`unified_system.platforms.common.PlatformAdapter` is the versioned foundation. `AdapterCapabilities` defaults every capability to false. A platform module must separately declare its auth methods, supported read task kinds, verification time, and health check only after tests demonstrate those behaviors. Planned/unverified platform code must not be registered with the Worker runtime.

`WorkerSettings.from_env()` accepts a platform-specific token (`WORKER_TOKEN_CHAOXING`, `WORKER_TOKEN_ZHIHUISU`, or `WORKER_TOKEN_YINGHUA`) of at least 32 characters. It prevents non-TLS remote API URLs; plain HTTP is allowed only for the private Compose service name or loopback development. Worker credentials are excluded from settings repr and logs.

## Current implementation state

- Implemented here: capability/course/auth/error models, abstract adapter methods, explicit registry, environment validation, authenticated worker HTTP client, task runner, heartbeat/cancel loop and fake-client tests.
- Implemented: minimal per-account course catalog persistence and read API (`GET /api/v1/courses`) with personal/shared-grant isolation; bounded course sync payload is accepted only through a valid Worker task lease.
- Not implemented/verified yet: registered/usable platform adapters (the Chaoxing candidate remains capability-disabled; Zhihuishu and Yinghua remain pending), user-driven account verification endpoint, course detail/resource/progress persistence APIs, real authorized-platform test, Docker Compose build.
- `workers/Dockerfile` builds the generic runtime image, but has no built-in platform adapter. Startup fails closed unless `WORKER_ADAPTER_FACTORY=package.module:factory` points to a verified adapter.
- A missing adapter or unsupported capability remains unavailable; no worker should be started with placeholder capabilities. Docker build/run was not available for validation in this environment.
