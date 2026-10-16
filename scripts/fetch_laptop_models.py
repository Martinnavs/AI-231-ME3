"""Download the laptop speech-server model files into models/ and verify SHA-256.

Run: uv run python scripts/fetch_laptop_models.py [--models models] [--check]
Exit 2 names the file on a missing download or checksum mismatch.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import urllib.request
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

SHERPA_DIR = "sherpa-zipformer-en-2023-06-26"
KOKORO_DIR = "kokoro-v1.0"

_SHERPA_URL = (
    "https://huggingface.co/csukuangfj/sherpa-onnx-streaming-zipformer-en-2023-06-26/resolve/main/"
)
_KOKORO_GH = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/"
_KOKORO_HF = "https://huggingface.co/onnx-community/Kokoro-82M-v1.0-ONNX/resolve/main/onnx/"

LICENSES = {
    SHERPA_DIR: (
        "Apache-2.0 — k2-fsa/icefall streaming Zipformer trained on LibriSpeech "
        "(model card: huggingface.co/csukuangfj/sherpa-onnx-streaming-zipformer-en-2023-06-26)"
    ),
    KOKORO_DIR: (
        "Apache-2.0 — hexgrad/Kokoro-82M weights and voices "
        "(ONNX exports: onnx-community/Kokoro-82M-v1.0-ONNX, thewh1teagle/kokoro-onnx MIT)"
    ),
}


@dataclass(frozen=True)
class ModelFile:
    path: str
    url: str
    sha256: str


_STEM = "epoch-99-avg-1-chunk-16-left-128"

MANIFEST: tuple[ModelFile, ...] = (
    ModelFile(
        f"{SHERPA_DIR}/tokens.txt",
        _SHERPA_URL + "tokens.txt",
        "49e3c2646595fd907228b3c6787069658f67b17377c60aeb8619c4551b2316fb",
    ),
    ModelFile(
        f"{SHERPA_DIR}/encoder-{_STEM}.onnx",
        _SHERPA_URL + f"encoder-{_STEM}.onnx",
        "a423883ce5754507fd941755ab0b5bc426a84ac670cbe21cf060e9e2c66dc660",
    ),
    ModelFile(
        f"{SHERPA_DIR}/decoder-{_STEM}.onnx",
        _SHERPA_URL + f"decoder-{_STEM}.onnx",
        "7bf787f90b194b307e5a4ad6a34fadb4e748304c35f78a8d66358a05b13ee6ef",
    ),
    ModelFile(
        f"{SHERPA_DIR}/joiner-{_STEM}.onnx",
        _SHERPA_URL + f"joiner-{_STEM}.onnx",
        "210591f72b3c56b8364f85f345dca240bc2b4c00632848f4aa923630d5639d3b",
    ),
    ModelFile(
        f"{SHERPA_DIR}/encoder-{_STEM}.int8.onnx",
        _SHERPA_URL + f"encoder-{_STEM}.int8.onnx",
        "563fde436d16cf7607cf408cd6b30909819d03162652ef389c2450ced3f45ac1",
    ),
    ModelFile(
        f"{SHERPA_DIR}/decoder-{_STEM}.int8.onnx",
        _SHERPA_URL + f"decoder-{_STEM}.int8.onnx",
        "98da299f471e38bb4e1a8df579b8cc9122d6039576a77e357b3c60f17dd83b02",
    ),
    ModelFile(
        f"{SHERPA_DIR}/joiner-{_STEM}.int8.onnx",
        _SHERPA_URL + f"joiner-{_STEM}.int8.onnx",
        "d944208d660d67c8d72cd2acaeac971fa5ceb8c80e76c1968148846fedd6e297",
    ),
    ModelFile(
        f"{KOKORO_DIR}/kokoro-v1.0.onnx",
        _KOKORO_HF + "model.onnx",
        "8fbea51ea711f2af382e88c833d9e288c6dc82ce5e98421ea61c058ce21a34cb",
    ),
    ModelFile(
        f"{KOKORO_DIR}/kokoro-v1.0.int8.onnx",
        _KOKORO_GH + "kokoro-v1.0.int8.onnx",
        "6e742170d309016e5891a994e1ce1559c702a2ccd0075e67ef7157974f6406cb",
    ),
    ModelFile(
        f"{KOKORO_DIR}/voices-v1.0.bin",
        _KOKORO_GH + "voices-v1.0.bin",
        "bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d",
    ),
)


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while block := f.read(1 << 20):
            h.update(block)
    return h.hexdigest()


def verify(models: Path, entry: ModelFile) -> str | None:
    path = models / entry.path
    if not path.is_file():
        return f"{entry.path}: missing"
    if sha256_of(path) != entry.sha256:
        return f"{entry.path}: checksum mismatch"
    return None


def verify_all(models: Path) -> list[str]:
    return [p for e in MANIFEST if (p := verify(models, e))]


def download(models: Path, entry: ModelFile) -> str | None:
    path = models / entry.path
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(path.name + ".part")
    try:
        with urllib.request.urlopen(entry.url, timeout=60) as resp, part.open("wb") as f:
            while block := resp.read(1 << 20):
                f.write(block)
    except OSError as exc:
        part.unlink(missing_ok=True)
        return f"{entry.path}: download failed ({type(exc).__name__})"
    if sha256_of(part) != entry.sha256:
        part.unlink()
        return f"{entry.path}: checksum mismatch"
    part.replace(path)
    return None


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="fetch_laptop_models")
    ap.add_argument("--models", type=Path, default=Path("models"))
    ap.add_argument("--check", action="store_true", help="verify only, never download")
    args = ap.parse_args(argv)

    for name, lic in LICENSES.items():
        print(f"license {name}: {lic}")
    problems = []
    for entry in MANIFEST:
        problem = verify(args.models, entry)
        if problem and not args.check:
            print(f"fetching {entry.path}")
            problem = download(args.models, entry)
        if problem:
            problems.append(problem)
        else:
            print(f"ok {entry.path}")
    for p in problems:
        print(f"error: {p}", file=sys.stderr)
    return 2 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
