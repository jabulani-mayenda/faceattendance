# FaceTrack

FaceTrack is a local attendance application for a single organization. It supports student/session attendance and employee/shift attendance, with optional local face matching and administrator confirmation.

## School accounts and class workflow

- The top-level administrator creates HOD accounts with the `HOD / department head` role and a department name.
- An HOD signs in with their own username and password, creates teacher accounts for that department, adds student profiles, and creates class sessions with a class/group and assigned teacher.
- Teachers sign in with their own account. Their dashboard, roster, attendance history, recognition candidates, and attendance capture are limited to their assigned class groups. They cannot create accounts or change schedules.
- The HOD dashboard and reports show attendance from teachers and classes in that HOD's department. The top-level administrator can oversee the whole local workspace.

Every class session must have a teacher and class/group assigned. Use the same group label on each student profile and its class session. A teacher can record an event only during an assigned active session.

## Implemented

- Responsive role-aware interface, teacher and HOD accounts, class assignments, people directory, sessions, reports and audit history.
- PBKDF2 password hashing, persistent revocable database sessions, CSRF tokens, login throttling and role checks.
- SQLite persistence with foreign-key enforcement and race-resistant partial unique indexes for one education mark and one shift check-in/check-out.
- Session and shift time-window validation, late-arrival classification, explicit check-in/check-out, duplicate request handling and correction-aware reports.
- Optional offline face enrollment and 1:N matching using OpenCV YuNet and SFace. Face images are processed in memory; only an encrypted 128-float feature vector is stored.
- Face matches are suggestions. An operator must verify the person in front of the camera and explicitly confirm attendance.
- Consent confirmation during enrollment, template replacement/deletion controls, recognition outcome logs and configurable similarity threshold.

## Recognition limits

The SFace cosine threshold defaults to `0.363`, matching the OpenCV example; it is **not calibrated for this organization or camera**. Validate false-match and false-reject rates on representative local data before use. The camera workflow has no liveness/presentation-attack detector. A photo or screen replay may fool face matching; the app therefore does not let a face match create attendance by itself. Manual attendance remains available.

The recognition models are optional. They are downloaded only when requested and verified against pinned SHA-256 values. OpenCV Zoo lists the YuNet model under MIT and SFace under Apache 2.0; retain attribution and review model/data terms for your use.

## Setup on Windows PowerShell

```powershell
cd facetrack
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m app.configure
python -m app.download_models
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

`app.configure` asks for an initial admin password and creates a private, git-ignored `.env` with a generated encryption key. Keep a secure backup of that key: losing it makes saved face templates unreadable. `app.download_models` fetches the pinned YuNet and SFace files and verifies their hashes. The first model download is about 39 MB.

Open <http://127.0.0.1:8000> and sign in as `admin`. For PowerShell activation issues, call `.\.venv\Scripts\python.exe` directly. `start_windows.bat` installs dependencies, creates `.env` if needed, and starts the local server; run the model download command once to enable face matching.

## Configuration

The app loads `facetrack/.env` at startup. Key settings:

- `FACETRACK_ADMIN_PASSWORD`: initial admin password (12+ characters); set through `app.configure` on first run.
- `FACETRACK_BIOMETRIC_KEY`: generated Fernet key for encrypting face templates.
- `FACETRACK_TIMEZONE`: IANA timezone used for daily dashboard boundaries (default `UTC`).
- `FACETRACK_MATCH_THRESHOLD`: cosine match threshold between `0.1` and `0.95` (default `0.363`).
- `COOKIE_SECURE=1`: use when served over HTTPS.
- `DATABASE_URL`: optional SQLAlchemy URL; default is `sqlite:///./facetrack.db`.
- `FACETRACK_MODEL_DIR`: optional directory for the downloaded ONNX models.

Do not rotate the biometric key unless templates are first deleted or migrated; existing templates cannot be decrypted with a different key.

## Tests

```powershell
python -m pytest -q
```

The suite covers app health, authentication, CSRF, bootstrap password handling, SQLite foreign keys, idempotent education attendance, explicit shift check-in/out, corrections, reporting, teacher-to-class permission scopes, HOD department scopes, and the enrollment save-state gate. It does not certify recognition accuracy or liveness.

## API highlights

- `GET /api/health`, `GET /api/recognition/status`
- `POST /api/login`, `POST /api/logout`, `GET /api/me`
- `GET/POST /api/people`, `PATCH /api/people/{id}`, `POST /api/people/{id}/deactivate`
- `POST/DELETE /api/people/{id}/biometrics`
- `POST /api/recognition/match` (candidate only; never writes attendance)
- `GET/POST/PATCH /api/sessions`, `GET/POST/PATCH /api/shifts`
- `GET/POST /api/attendance`, `POST /api/attendance/{id}/correction`, `GET /api/attendance/{id}/corrections`
- `GET /api/dashboard`, `GET /api/audit`, `GET /api/reports/attendance.csv`
- `GET/POST/PATCH /api/users`

## Not production complete

This is a local single-organization MVP. It does not include a robust liveness/PAD model, multi-tenant isolation, MFA, account recovery, central sync/replication, authenticated device management, leave/payroll rules, or a production deployment/migration pipeline. Recognition remains human-confirmed; do not use the system as the sole basis for consequential employment or education decisions.
