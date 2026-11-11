# FaceTrack implementation status

Reviewed: 8 November 2026

## Working application scope

FaceTrack is a local, single-organization attendance web app. Its database stores people, users, sessions, shifts, attendance events, corrections, audit records, persistent login sessions, encrypted biometric templates, and recognition outcomes. SQLite foreign keys are enabled; Alembic tracks the initial schema and teacher-assignment update; unique database indexes protect one education mark and one workplace check-in/check-out per person and schedule.

The role-aware UI supports school teachers and department HODs. An HOD creates teacher accounts, adds people, assigns teachers to class/group sessions, and reviews that department's totals and reports. Teachers see their assigned roster and sessions, then record attendance for their students during the active class period. Their API access is restricted to those assigned sessions and groups. The top-level administrator retains workspace-wide access. Workplace shifts, correction history, CSV reporting, audit history, face enrollment/deletion, camera-based candidate matching, and system status are also available to authorized roles. Attendance is never created by recognition alone: the operator must verify the person and submit the attendance action.

Face enrollment processes three to five images in memory, requires a consent confirmation, and stores only an encrypted SFace vector. The provided YuNet and SFace model files are hash-verified. The local camera path detects, aligns and matches faces and logs recognized, ambiguous, or unknown outcomes. The default cosine threshold follows the upstream example and has not been calibrated on this organization’s cameras or users.

## Verification performed

- `python -m pytest -q`: **10 passed**. Tests cover health, login page and access control, SQLite foreign keys, bootstrap password policy, CSRF, attendance schedule rules and duplicate handling, corrections and CSV output, logout revocation, encrypted-vector round trips, rejection of a frame with no face when models are available, teacher-to-session/HOD department scoping, and the enrollment dialog save gate.
- `python -m compileall -q app migrations tests`: passed.
- `node --check static/app.js`: passed.
- The test app startup applies Alembic through `0002_teacher_assignments` on its isolated SQLite database.
- OpenCV 4.10, pinned model hashes, encryption-key readiness, and recognition engine initialization were checked in the local environment. No representative identity dataset or physical camera session was available, so matching accuracy and camera behavior were not certified.
- Tests use a unique temporary SQLite database and do not write to `facetrack.db`.

## Remaining before production use

- Liveness/presentation-attack detection is not implemented. A photo or screen replay may pass face matching; operator confirmation is required and is not a substitute for PAD.
- Recognition thresholds and false-match/false-reject rates need evaluation with representative consenting users, cameras, lighting, and deployment conditions.
- Central synchronization is not implemented. `/api/sync` only records local receipt IDs; it does not replicate or reconcile events with a server.
- The app is single-organization and local. It has no organization/site isolation, authenticated device identity, MFA, account recovery, or deployment hardening.
- The migration is an initial baseline, not a reviewed upgrade path for arbitrary legacy data. Backups, restore drills, migration rollback, and existing-database upgrade testing remain necessary before rollout.
- Attendance does not include leave, absence, payroll, or institution-specific policy workflows.
- Validation was local and functional. It did not include penetration testing, load testing, a full browser/device matrix, camera-device QA, or biometric accuracy evaluation.

## Local setup

Use `README.md` and `start_windows.bat`. First run creates `.env` through `app.configure`, where the operator chooses the initial admin password and a biometric encryption key is generated. Run `python -m app.download_models` to enable face matching. Back up the encryption key securely; saved templates cannot be read without it.
