"""Download the pinned OpenCV Zoo models after the operator runs this command."""
import hashlib
import os
import urllib.request
from pathlib import Path

from .recognition import MODEL_DIR, MODEL_SPECS

URLS = {
    'yunet': 'https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx',
    'sface': 'https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/face_recognition_sface/face_recognition_sface_2021dec.onnx',
}


def download_models():
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    for model_name, (filename, expected_sha256) in MODEL_SPECS.items():
        target = MODEL_DIR / filename
        if target.exists() and hashlib.sha256(target.read_bytes()).hexdigest() == expected_sha256:
            print(f'{filename}: verified, already present')
            continue
        temporary = target.with_suffix(target.suffix + '.download')
        request = urllib.request.Request(URLS[model_name], headers={'User-Agent': 'FaceTrack/0.2 model setup'})
        digest = hashlib.sha256()
        try:
            with urllib.request.urlopen(request, timeout=60) as response, temporary.open('wb') as out:
                while chunk := response.read(1024 * 1024):
                    digest.update(chunk)
                    out.write(chunk)
            if digest.hexdigest() != expected_sha256:
                raise RuntimeError(f'SHA-256 mismatch for {filename}; refusing to install the model')
            os.replace(temporary, target)
            print(f'{filename}: downloaded and verified')
        finally:
            if temporary.exists():
                temporary.unlink()


if __name__ == '__main__':
    download_models()
