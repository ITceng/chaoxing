# Learning Platform adapter — candidate, not enabled

`adapter.py` is a new isolated, cookies-only candidate that reads the known course-list and course-folder endpoints and parses only course ID/name/teacher. It does not import the legacy scheduler, file-based Cookie manager, password login, or any study/quiz/exam methods. Every request is HTTPS-only, fixed-host, redirect-disabled, timeout-bounded, and uses a fresh per-call session. Session closure, task cancellation checkpoints, and narrow parser output are covered by offline Mock tests.

This candidate is deliberately **not registered or advertised**: `capabilities()` returns all false and `health_check()` is unhealthy until a real user-authorized account test verifies login status and course results. Do not deploy it as a usable platform Worker or claim the live platform is integrated. Do not call study, attendance, progress-write, quiz, exam, or submission methods.

See [`docs/phase4-source-audit.md`](../../../docs/phase4-source-audit.md).