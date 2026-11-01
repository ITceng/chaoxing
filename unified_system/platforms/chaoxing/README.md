# Learning Platform adapter — candidate, not enabled

`adapter.py` is a new isolated, cookies-only candidate that reads the known course-list and course-folder endpoints and parses only course ID/name/teacher. It does not import the legacy scheduler, file-based Cookie manager, password login, or any study/quiz/exam methods. Every request is HTTPS-only, fixed-host, redirect-disabled, timeout-bounded, and uses a fresh per-call session. Session closure, task cancellation checkpoints, and narrow parser output are covered by offline Mock tests.

This candidate is deliberately **not registered or advertised**: `capabilities()` returns all false and `health_check()` is unhealthy until a real user-authorized account test verifies login status and course results. Do not deploy it as a usable platform Worker or claim the live platform is integrated. Do not call study, attendance, progress-write, quiz, exam, or submission methods.

An owner can perform an opt-in smoke check in their own authorized environment without pasting credentials into chat or storing them in the repo. In Windows PowerShell from the repository root:

```powershell
.\.venv\Scripts\python.exe -m unified_system.platforms.chaoxing.manual_verify
```

The command prompts for the cookie without echoing it and prints only success/failure, course count, and time. It never enables capabilities; the adapter remains disabled, and Mock coverage is not a substitute for this live check or a separate code review. If the platform requests login, verification, CAPTCHA, or other interaction, stop and handle it manually—do not retry around it.

See [`docs/phase4-source-audit.md`](../../../docs/phase4-source-audit.md).
