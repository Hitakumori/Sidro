"""Download and verify the exact PP-OCRv6 small models used by Sidro's release."""
import hashlib
import os
from pathlib import Path
import requests
from rapidocr import LangDet, ModelType, OCRVersion
from rapidocr.inference_engine.base import FileInfo, InferSession
from rapidocr.utils.typings import EngineType, TaskType

root = Path(__file__).resolve().parents[1] / 'ocr_release_models' / 'rapidocr'
root.mkdir(parents=True, exist_ok=True)
for task in (TaskType.DET, TaskType.REC):
    info = InferSession.get_model_url(FileInfo(EngineType.ONNXRUNTIME, OCRVersion.PPOCRV6, task, LangDet.CH, ModelType.SMALL))
    target = root / info['model_dir'].rsplit('/', 1)[-1]
    expected = info['SHA256'].lower()
    if target.exists() and hashlib.sha256(target.read_bytes()).hexdigest() == expected:
        continue
    temporary = target.with_suffix('.download')
    try:
        with requests.get(info['model_dir'], stream=True, timeout=(10, 60)) as response:
            response.raise_for_status()
            digest = hashlib.sha256()
            with temporary.open('wb') as stream:
                for chunk in response.iter_content(1024 * 1024):
                    stream.write(chunk)
                    digest.update(chunk)
            if digest.hexdigest() != expected:
                raise RuntimeError(f'Model checksum mismatch: {target.name}')
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
print('Verified release RapidOCR models:', root)
