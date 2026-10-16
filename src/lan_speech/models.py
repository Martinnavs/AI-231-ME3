"""Model definitions, manifests, and verification for LAN speech server (ticket 21)."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

SHERPA_DIR = "sherpa-zipformer-en-2023-06-26"
KOKORO_DIR = "kokoro-v1.0"
SUPERTONIC_DIR = "supertonic-3"

_SHERPA_URL = (
    "https://huggingface.co/csukuangfj/sherpa-onnx-streaming-zipformer-en-2023-06-26/resolve/main/"
)
_KOKORO_GH = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/"
_KOKORO_HF = "https://huggingface.co/onnx-community/Kokoro-82M-v1.0-ONNX/resolve/main/onnx/"
_SUPERTONIC_URL = "https://huggingface.co/Supertone/supertonic-3/resolve/724fb5abbf5502583fb520898d45929e62f02c0b/"

LICENSES: dict[str, str] = {
    SHERPA_DIR: (
        "Apache-2.0 — k2-fsa/icefall streaming Zipformer trained on LibriSpeech "
        "(model card: huggingface.co/csukuangfj/sherpa-onnx-streaming-zipformer-en-2023-06-26)"
    ),
    KOKORO_DIR: (
        "Apache-2.0 — hexgrad/Kokoro-82M weights and voices "
        "(ONNX exports: onnx-community/Kokoro-82M-v1.0-ONNX, thewh1teagle/kokoro-onnx MIT)"
    ),
    SUPERTONIC_DIR: (
        "OpenRAIL-M — Supertone/supertonic-3 model weights "
        "(model card: huggingface.co/Supertone/supertonic-3; SDK is MIT)"
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
    ModelFile(
        f"{SUPERTONIC_DIR}/LICENSE",
        _SUPERTONIC_URL + "LICENSE",
        "0d944a9110fed9a9602d60e0423a272903e7bd21ab060490774efc77c2275e9f",
    ),
    ModelFile(
        f"{SUPERTONIC_DIR}/config.json",
        _SUPERTONIC_URL + "config.json",
        "4099082b107a9d4029849ac76b89eca65e03732660969c2babe5bf308c7357f2",
    ),
    ModelFile(
        f"{SUPERTONIC_DIR}/onnx/duration_predictor.onnx",
        _SUPERTONIC_URL + "onnx/duration_predictor.onnx",
        "c3eb91414d5ff8a7a239b7fe9e34e7e2bf8a8140d8375ffb14718b1c639325db",
    ),
    ModelFile(
        f"{SUPERTONIC_DIR}/onnx/text_encoder.onnx",
        _SUPERTONIC_URL + "onnx/text_encoder.onnx",
        "c7befd5ea8c3119769e8a6c1486c4edc6a3bc8365c67621c881bbb774b9902ff",
    ),
    ModelFile(
        f"{SUPERTONIC_DIR}/onnx/tts.json",
        _SUPERTONIC_URL + "onnx/tts.json",
        "42078d3aef1cd43ab43021f3c54f47d2d75ceb4e75f627f118890128b06a0d09",
    ),
    ModelFile(
        f"{SUPERTONIC_DIR}/onnx/unicode_indexer.json",
        _SUPERTONIC_URL + "onnx/unicode_indexer.json",
        "9bf7346e43883a81f8645c81224f786d43c5b57f3641f6e7671a7d6c493cb24f",
    ),
    ModelFile(
        f"{SUPERTONIC_DIR}/onnx/vector_estimator.onnx",
        _SUPERTONIC_URL + "onnx/vector_estimator.onnx",
        "883ac868ea0275ef0e991524dc64f16b3c0376efd7c320af6b53f5b780d7c61c",
    ),
    ModelFile(
        f"{SUPERTONIC_DIR}/onnx/vocoder.onnx",
        _SUPERTONIC_URL + "onnx/vocoder.onnx",
        "085de76dd8e8d5836d6ca66826601f615939218f90e519f70ee8a36ed2a4c4ba",
    ),
    ModelFile(
        f"{SUPERTONIC_DIR}/voice_styles/F1.json",
        _SUPERTONIC_URL + "voice_styles/F1.json",
        "bbdec6ee00231c2c742ad05483df5334cab3b52fda3ba38e6a07059c4563dbc2",
    ),
    ModelFile(
        f"{SUPERTONIC_DIR}/voice_styles/F2.json",
        _SUPERTONIC_URL + "voice_styles/F2.json",
        "7c722c6a72707b1a77f035d67f0d1351ba187738e06f7683e8c72b1df3477fc6",
    ),
    ModelFile(
        f"{SUPERTONIC_DIR}/voice_styles/F3.json",
        _SUPERTONIC_URL + "voice_styles/F3.json",
        "12f6ef2573baa2defa1128069cb59f203e3ab67c92af77b42df8a0e3a2f7c6ab",
    ),
    ModelFile(
        f"{SUPERTONIC_DIR}/voice_styles/F4.json",
        _SUPERTONIC_URL + "voice_styles/F4.json",
        "c2fa764c1225a76dfc3e2c73e8aa4f70d9ee48793860eb34c295fff01c2e032b",
    ),
    ModelFile(
        f"{SUPERTONIC_DIR}/voice_styles/F5.json",
        _SUPERTONIC_URL + "voice_styles/F5.json",
        "45966e73316415626cf41a7d1c6f3b4c70dbc1ba2bee5c1978ef0ce33244fc8d",
    ),
    ModelFile(
        f"{SUPERTONIC_DIR}/voice_styles/M1.json",
        _SUPERTONIC_URL + "voice_styles/M1.json",
        "e35604687f5d23694b8e91593a93eec0e4eca6c0b02bb8ed69139ab2ea6b0a5b",
    ),
    ModelFile(
        f"{SUPERTONIC_DIR}/voice_styles/M2.json",
        _SUPERTONIC_URL + "voice_styles/M2.json",
        "b76cbf62bac707c710cf0ae5aba5e31eea1a6339a9734bfae33ab98499534a50",
    ),
    ModelFile(
        f"{SUPERTONIC_DIR}/voice_styles/M3.json",
        _SUPERTONIC_URL + "voice_styles/M3.json",
        "ea1ac35ccb91b0d7ecad533a2fbd0eec10c91513d8951e3b25fbba99954e159b",
    ),
    ModelFile(
        f"{SUPERTONIC_DIR}/voice_styles/M4.json",
        _SUPERTONIC_URL + "voice_styles/M4.json",
        "ca8eefad4fcd989c9379032ff3e50738adc547eeb5e221b82593a6d7b3bac303",
    ),
    ModelFile(
        f"{SUPERTONIC_DIR}/voice_styles/M5.json",
        _SUPERTONIC_URL + "voice_styles/M5.json",
        "dd22b92740314321f8ae11c5e87f8dd60d060f15dd3a632b5adf77f471f77af2",
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
