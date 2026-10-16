"""Model manifest: Kroko is the default STT model and the server only checks what it uses."""

from __future__ import annotations

from pathlib import Path

import pytest

from lan_speech import models
from lan_speech.models import (
    KROKO_DIR,
    MANIFEST,
    SHERPA_DIR,
    SUPERTONIC_DIR,
    required_dirs,
    sha256_of,
    verify_all,
)


def test_required_dirs_follow_the_chosen_stt_model() -> None:
    assert required_dirs("kroko") == {KROKO_DIR, SUPERTONIC_DIR}
    assert required_dirs("zipformer") == {SHERPA_DIR, SUPERTONIC_DIR}
    with pytest.raises(KeyError):
        required_dirs("whisper")


def test_verify_only_reports_the_requested_folders(tmp_path: Path) -> None:
    problems = verify_all(tmp_path, required_dirs("kroko"))
    assert problems
    assert all(p.split("/", 1)[0] in {KROKO_DIR, SUPERTONIC_DIR} for p in problems)
    assert f"{KROKO_DIR}/encoder.onnx: missing" in problems
    assert not any(p.startswith(("kokoro", SHERPA_DIR)) for p in problems)
    assert len(verify_all(tmp_path)) == len(MANIFEST)


def test_a_corrupt_file_is_reported_by_name(tmp_path: Path) -> None:
    for entry in MANIFEST:
        if entry.path.startswith(f"{KROKO_DIR}/"):
            target = tmp_path / entry.path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"wrong")
    problems = verify_all(tmp_path, frozenset({KROKO_DIR}))
    assert f"{KROKO_DIR}/tokens.txt: checksum mismatch" in problems


def test_kroko_license_note_is_honest_about_the_empty_license_file() -> None:
    note = models.LICENSES[KROKO_DIR]
    assert "CC-BY-SA" in note and "empty" in note


def test_real_kroko_files_match_the_pinned_hashes_when_present() -> None:
    root = Path("models")
    entries = [e for e in MANIFEST if e.path.startswith(f"{KROKO_DIR}/")]
    if not all((root / e.path).is_file() for e in entries):
        pytest.skip("Kroko files not downloaded")
    assert all(sha256_of(root / e.path) == e.sha256 for e in entries)
