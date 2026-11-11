"""Local YuNet/SFace inference. Raw images are processed in memory and discarded."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

import numpy as np
from cryptography.fernet import Fernet, InvalidToken

ROOT = Path(__file__).resolve().parent.parent
MODEL_DIR = Path(os.getenv('FACETRACK_MODEL_DIR', str(ROOT / 'models')))
MODEL_SPECS = {
    'yunet': ('face_detection_yunet_2023mar.onnx', '8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4'),
    'sface': ('face_recognition_sface_2021dec.onnx', '0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79'),
}
_ENGINE = None


def _model_paths() -> dict[str, Path]:
    return {key: MODEL_DIR / name for key, (name, _) in MODEL_SPECS.items()}


def _verified(path: Path, expected: str) -> bool:
    if not path.is_file():
        return False
    digest = hashlib.sha256()
    with path.open('rb') as model_file:
        for block in iter(lambda: model_file.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest().lower() == expected


def encryption_ready() -> bool:
    key = os.getenv('FACETRACK_BIOMETRIC_KEY', '')
    if not key:
        return False
    try:
        Fernet(key.encode('ascii'))
        return True
    except (ValueError, UnicodeError):
        return False


def model_status() -> dict:
    try:
        import cv2
        opencv_version = cv2.__version__
    except ImportError:
        opencv_version = None
    paths = _model_paths()
    present = {key: _verified(paths[key], MODEL_SPECS[key][1]) for key in paths}
    ready = opencv_version is not None and all(present.values()) and encryption_ready()
    return {
        'configured': ready,
        'engine': 'OpenCV YuNet + SFace' if ready else 'none',
        'opencv_version': opencv_version,
        'models': present,
        'encrypted_templates': encryption_ready(),
        'liveness': 'operator_confirmation_required',
        'message': 'Face matches are suggestions only. An operator must verify the person and confirm attendance; no liveness or presentation-attack detector is installed.' if ready else 'Install the pinned local models and set FACETRACK_BIOMETRIC_KEY to enable recognition.',
    }


def _engine():
    global _ENGINE
    if _ENGINE is not None:
        return _ENGINE
    status = model_status()
    if not status['configured']:
        raise RuntimeError(status['message'])
    import cv2
    paths = _model_paths()
    detector = cv2.FaceDetectorYN.create(str(paths['yunet']), '', (320, 320), 0.9, 0.3, 5000)
    recognizer = cv2.FaceRecognizerSF.create(str(paths['sface']), '')
    _ENGINE = (cv2, detector, recognizer)
    return _ENGINE


def extract_embedding(image_bytes: bytes) -> np.ndarray:
    cv2, detector, recognizer = _engine()
    if not image_bytes or len(image_bytes) > 5 * 1024 * 1024:
        raise ValueError('Image must be between 1 byte and 5 MB')
    image = cv2.imdecode(np.frombuffer(image_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None or image.size == 0:
        raise ValueError('The uploaded file is not a valid image')
    height, width = image.shape[:2]
    detector.setInputSize((width, height))
    _, faces = detector.detect(image)
    if faces is None or len(faces) == 0:
        raise ValueError('No face detected. Center your face in the camera and try again.')
    if len(faces) != 1:
        raise ValueError('Capture one person at a time')
    face = faces[0]
    x, y, face_width, face_height = [int(v) for v in face[:4]]
    if face[14] < 0.90 or min(face_width, face_height) < 100:
        raise ValueError('Face image is too small or unclear; move closer and improve lighting')
    crop = image[max(0, y):min(height, y + face_height), max(0, x):min(width, x + face_width)]
    if crop.size == 0 or float(cv2.Laplacian(crop, cv2.CV_64F).var()) < 25:
        raise ValueError('Image is too blurry; hold still and try again')
    aligned = recognizer.alignCrop(image, face)
    feature = np.asarray(recognizer.feature(aligned), dtype=np.float32).reshape(-1)
    norm = float(np.linalg.norm(feature))
    if not norm or not np.isfinite(norm):
        raise ValueError('Face feature extraction failed')
    return feature / norm


def encrypt_embedding(embedding: np.ndarray) -> bytes:
    key = os.getenv('FACETRACK_BIOMETRIC_KEY', '')
    if not key:
        raise RuntimeError('Set FACETRACK_BIOMETRIC_KEY before biometric enrollment')
    try:
        return Fernet(key.encode('ascii')).encrypt(np.asarray(embedding, dtype=np.float32).tobytes())
    except (ValueError, UnicodeError) as error:
        raise RuntimeError('FACETRACK_BIOMETRIC_KEY must be a valid Fernet key') from error


def decrypt_embedding(encrypted: bytes) -> np.ndarray:
    key = os.getenv('FACETRACK_BIOMETRIC_KEY', '')
    if not key:
        raise RuntimeError('Set FACETRACK_BIOMETRIC_KEY to access enrolled templates')
    try:
        raw = Fernet(key.encode('ascii')).decrypt(encrypted)
        vector = np.frombuffer(raw, dtype=np.float32).copy()
    except (InvalidToken, ValueError, UnicodeError) as error:
        raise RuntimeError('Biometric template could not be decrypted; check the configured key') from error
    if vector.shape != (128,) or not np.isfinite(vector).all():
        raise RuntimeError('Stored biometric template has an unsupported format')
    return vector


def match_threshold() -> float:
    try:
        threshold = float(os.getenv('FACETRACK_MATCH_THRESHOLD', '0.363'))
    except ValueError:
        threshold = 0.363
    return min(0.95, max(0.1, threshold))
