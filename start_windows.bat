@echo off
setlocal
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  echo Creating Python virtual environment...
  py -3 -m venv .venv
  if errorlevel 1 goto error
)
.venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 goto error
if not exist .env .venv\Scripts\python.exe -m app.configure
if errorlevel 1 goto error
if not exist models\face_detection_yunet_2023mar.onnx echo Face recognition models are missing. Run: .venv\Scripts\python.exe -m app.download_models
.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
if errorlevel 1 goto error
goto end
:error
echo FaceTrack could not start. Review the error above.
pause
:end
endlocal
