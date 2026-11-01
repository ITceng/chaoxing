# Phase 6E — authorized target preflight and acceptance status

**Status: blocked before deployment.** This records only work possible in the Arena checkout. No target host was contacted, no service was deployed, and no production acceptance or release is declared.

## 1. Source baseline and review

At the start of Phase 6E, `arena/bd5e1acd-chaoxing` was clean and synchronized with `origin` at `ddde002`. This is the current application candidate baseline pending host acceptance—not a production release marker. The Phase 6A–6D deployment materials were reviewed, including migrations `0001`–`0005`, production and operations Compose files, host/edge Nginx templates, backup/isolated-restore and key-rotation tooling, Worker profiles, and documented historical blockers.

The source and documentation identify the following unresolved gates: no admin MFA/server-side reauthentication for high-risk actions; supplier-side revocation/exposure review of a historical AI key is unconfirmed; no external authenticated alert delivery; no validated platform Worker adapters/authorization (all profiles, including Yinghua, remain disabled); and no real PostgreSQL/Ubuntu/container/Nginx/public-HTTPS acceptance. The restore utility is deliberately isolated-only and is not a production overwrite/rollback command. The normal deployment rollback plan still requires target-specific backup, migration compatibility, previous immutable images/configuration, and an authorized maintenance window; none has been exercised.

A production release commit/tag is intentionally **not** created while these acceptance gates are open. The preflight tool/documentation changes do not convert `ddde002` into a release.

## 2. Host evidence boundary

The available Arena execution host is Debian 12, kernel `6.1.158+`, x86_64, 2 CPUs, about 4 GiB RAM and about 18 GiB free on its root filesystem at inspection. Docker/Compose, PostgreSQL/`psql`, Nginx and `age` are absent. It is not the requested target. No target address/session was supplied, so SSH was not attempted and target facts were not independently verified.

The target details previously supplied—Ubuntu-family system, kernel 6.8, `/home/agentuser`, Python 3.12.3—remain unverified. Host-provided versions/resources, Docker socket permissions, current services/listeners, and repository branch/commit/cleanliness are still required evidence.

## 3. Read-only target collector

`unified_system/deploy/phase6e-preflight.sh` is a read-only inventory helper. It reports OS/kernel/architecture/Python/resources; Git branch, commit, cleanliness, remote host/path with URL credentials removed and upstream divergence; tool versions and Docker daemon reachability; selected Compose service states; production env-file presence and mode only; Nginx presence/version without running a syntax test; listening TCP **port numbers only**; and, when safe, a quiet Compose syntax/interpolation check.

It does not print env-file contents, database URLs, container environments, IP addresses, listening addresses, certificates, private keys, backup contents, or command-line secrets. It does not probe DNS/HTTPS, call external application APIs, build images, run deployment containers, migrate a database, install packages, change files or permissions, alter systemd/SSH/firewall/DNS, or publish ports. It intentionally does not run `nginx -t` during preflight; perform config/runtime validation only later in an authorized controlled stage. It is a preflight report, not a security audit or deployment acceptance test. It returns findings for operator review; a PASS on any one check is not proof of overall readiness.

### Run on the authorized target

Only use this if `/home/agentuser/chaoxing` is the intended reviewed checkout and access to fetch this repository is authorized. Fetching updates Git objects/remote-tracking refs but does not switch branches or modify the worktree. Alternatively, transfer the reviewed script through an approved internal channel. Do not run `git pull`, checkout/reset, build, `compose up`, migrations, Nginx reloads, or firewall/DNS commands as part of preflight.

```bash
set -euo pipefail
repo=/home/agentuser/chaoxing
collector="$(mktemp)"
trap 'rm -f "$collector"' EXIT

git -C "$repo" fetch --no-tags origin \
  refs/heads/arena/bd5e1acd-chaoxing:refs/remotes/origin/arena/bd5e1acd-chaoxing
git -C "$repo" show refs/remotes/origin/arena/bd5e1acd-chaoxing:unified_system/deploy/phase6e-preflight.sh > "$collector"
chmod 700 "$collector"
REPO_DIR="$repo" \
PHASE6E_EXPECTED_BRANCH=arena/bd5e1acd-chaoxing \
PHASE6E_EXPECTED_COMMIT=ddde002 \
  bash "$collector"
```

Do not use `sudo`; run as the deployment account. Do not copy or paste any `.env*` file, command output containing secrets, private key, age identity, backup/archive, database dump, cookie, or API token. The collector suppresses sensitive diagnostics, but review the output before sharing. Report redacted output and any manually known version information only; hostname, public IP/domain, organization names and unrelated infrastructure identifiers may be removed. Keep the `ddde002` branch/commit and cleanliness fields intact if possible so the baseline can be assessed. If the target repository is not exactly at that baseline, do not modify it; report the observed branch/commit/dirty count and stop for review.

## 4. Acceptance status — not executed

| Required evidence | Status | Boundary / next evidence needed |
|---|---|---|
| Clean pushed source baseline | **Verified at Phase 6E start** | Current working branch began clean and synchronized at `ddde002`; later preflight-doc changes are not a production release. Target checkout still needs read-only verification. |
| Migrations, Compose, Nginx, backup/recovery, rotation, Worker and historical-blocker review | **Source/document review only** | Does not prove Docker/Compose parsing by the production binary, compatible migrations, runtime isolation, key custody, effective Nginx policy or host configuration. |
| Actual Ubuntu OS/kernel/Python/resources and installed versions | **Not verified** | Run the read-only target collector and share reviewed/sanitized output. |
| Docker/Compose build/start and isolated service/network/volume acceptance | **Not run** | Requires an authorized target or dedicated test host; no services were started in Arena. |
| Real PostgreSQL constraints, transactions, row locks, task concurrency/fencing, cancellation/recovery, and data isolation | **Not run** | Requires disposable PostgreSQL databases and the guarded Phase 6B test runner. SQLite/fakes are not substitutes. |
| Encrypted PostgreSQL backup and isolated restore, row/relationship/revision verification, synthetic keyring check and rotation/rollback | **Not run** | Requires real isolated PostgreSQL, `age`, controlled synthetic test data, separately held identity/keyring and review of the generated restore report. Do not use real user platform cookies/API keys. Never commit a backup. |
| Nginx config/runtime and authorized real HTTPS | **Not run** | Requires the actual authorized domain/certificate and external vantage point. Self-signed/local TLS cannot count as public HTTPS. No DNS/firewall/system service changes were authorized or made. |
| Admin step-up/MFA, high-risk audit/security, historical key revocation, authenticated external alerts | **Open product/security gates** | Must be resolved and independently verified before launch; documentation/tests alone are not completion. |
| Worker adapters and platform authorization | **Open; profiles disabled** | Do not enable Chaoxing/Zhihuishu absent reviewed adapters and real platform authorization. Yinghua remains disabled pending auditable/buildable Linux source. |
| Full functional tests, controlled gray release, monitoring and tested rollback | **Not run** | Require authorization, target-specific runbook and evidence from an approved maintenance/gray-release window. |

## 5. Next step and stop condition

The next action is only the target read-only preflight above. Review the sanitized result before deciding whether an authorized test/deployment session is possible. Do not request or send deployment secrets in chat. Do not claim full business acceptance, public HTTPS, recovery readiness, production launch, or create a production release tag until every required gate is passed with evidence at its proper layer. Stop Phase 6E here pending target evidence and authorization.
