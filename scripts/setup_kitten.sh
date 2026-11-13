#!/usr/bin/env bash
# setup_kitten.sh: install the Kitten TTS Micro voice the speech server uses (kittentts is not a project dependency).
# Makes $KITTEN_VENV (default ~/work/tts-bakeoff/venv, the place scripts/stack/up.sh looks for it as KITTEN_SITE), installs its
# dependencies, and unpacks the release wheel into it (its metadata says 0.1.0 whatever the file name, so pip refuses it).
# Keep KITTEN_VENV short: espeak-ng cuts data paths over about 160 characters and then aborts. The model weights
# (KittenML/kitten-tts-micro-0.8) download on first use. Needs uv, curl, unzip, network.
set -euo pipefail
VENV=${KITTEN_VENV:-$HOME/work/tts-bakeoff/venv}
URL=${KITTEN_WHL_URL:-https://github.com/KittenML/KittenTTS/releases/download/0.8/kittentts-0.8.0-py3-none-any.whl}
uv venv --python 3.12 "$VENV"
uv pip install --python "$VENV/bin/python" --index-url https://download.pytorch.org/whl/cpu torch  # the CPU build: misaki would pull the multi-GB CUDA one
# the versions the voice was tested in; plain misaki, not misaki[en]: its extra swaps in a phonemizer fork that cannot find espeak's data
uv pip install --python "$VENV/bin/python" num2words==0.5.14 spacy==3.8.18 espeakng_loader==0.2.4 misaki==0.9.4 phonemizer==3.4.0 onnxruntime==1.31.1 soundfile==0.14.0 numpy huggingface_hub
SITE=$("$VENV/bin/python" -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')
tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
curl -fsSL "$URL" -o "$tmp/kitten.whl"
unzip -oq "$tmp/kitten.whl" -d "$SITE"
"$VENV/bin/python" -c "
from kittentts import KittenTTS
audio = KittenTTS('KittenML/kitten-tts-micro-0.8').generate('Hello from the replicated setup.', voice='Jasper')
print('kittentts speaks:', len(audio), 'samples')"
echo "KITTEN_SITE=$SITE   (make up reads ~/work/tts-bakeoff/venv/lib/python3.12/site-packages by default; export KITTEN_SITE if you used another KITTEN_VENV)"
