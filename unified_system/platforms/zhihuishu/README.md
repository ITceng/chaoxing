# Zhihuishu adapter — pending safe extraction

No concrete unified adapter is registered. `platforms/zhihuishu.py` is an explicit planned skeleton. The separate `fuckZHS-master` tree includes login/QR code and course reads, but also CAPTCHA/security states, Cookie debug logging and many study/quiz/exam mutation methods. Do not import it directly into the tenant Worker. A future adapter must separately implement and test official user-driven authorization and read-only course/resource/progress methods, with sensitive logging removed from the copied boundary.

See [`docs/phase4-source-audit.md`](../../../docs/phase4-source-audit.md).