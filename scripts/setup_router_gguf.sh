#!/usr/bin/env bash
# setup_router_gguf.sh: ONLY if you retrained the router: merge your adapter into its base, convert it to GGUF and quantise it.
# (Everyone else: scripts/setup_router.sh downloads the published GGUF.) The adapter is the output of
# https://github.com/Martinnavs/ME3-LoRA-training: follow its README to retrain, then set ROUTER_REPO to your own adapter repo.
# Result: $GGUF_TOOLS/models/$ROUTER_NAME/gguf/router-Q4_K_M.gguf, next to a prebuilt llama.cpp (no compiling).
# Needs git, curl, uv, about 4 GB free and network; about 10 minutes on a laptop CPU.
set -euo pipefail
G=${GGUF_TOOLS:-$HOME/work/gguf-tools}
REPO=${ROUTER_REPO:-martinnavs/granite-4.0-h-350m-router-kd-web4-s0}
BASE=${BASE:-ibm-granite/granite-4.0-h-350m}
NAME=${ROUTER_NAME:-router-350m-kd-web4-s0-unverified}
LLAMA_REF=${LLAMA_REF:-8ca4060b7e30e76baaa5670ee1ec1b760b7f8552}  # the llama.cpp commit (2026-10-28) this router was converted with
TAG=${LLAMA_TAG:-b12345}  # the prebuilt release used for quantising and serving
M=$G/models/$NAME
mkdir -p "$G/llama.cpp/build/bin" "$M/gguf"
if [ ! -x "$G/llama.cpp/build/bin/llama-quantize" ]; then  # prebuilt binaries (llama-server, llama-quantize)
  curl -fL "https://github.com/ggml-org/llama.cpp/releases/download/$TAG/llama-$TAG-bin-ubuntu-x64.tar.gz" | tar xz -C "$G/llama.cpp/build/bin" --strip-components=1
fi
if [ ! -f "$G/llama.cpp/convert_hf_to_gguf.py" ]; then  # the converter script and its requirements, from the pinned commit
  rm -rf "$G/llama.cpp-src"
  git init -q "$G/llama.cpp-src"
  git -C "$G/llama.cpp-src" remote add origin https://github.com/ggml-org/llama.cpp
  git -C "$G/llama.cpp-src" fetch -q --depth 1 origin "$LLAMA_REF"
  git -C "$G/llama.cpp-src" checkout -q FETCH_HEAD
  cp -r "$G/llama.cpp-src/convert_hf_to_gguf.py" "$G/llama.cpp-src/gguf-py" "$G/llama.cpp-src/requirements" "$G/llama.cpp/"
  cp "$G/llama.cpp-src"/requirements.txt "$G/llama.cpp/" 2>/dev/null || true
  rm -rf "$G/llama.cpp-src"
fi
if [ ! -x "$G/venv-merge/bin/python" ]; then  # merging the adapter into its base model
  uv venv --python 3.12 "$G/venv-merge"
  uv pip install --python "$G/venv-merge/bin/python" --index-url https://download.pytorch.org/whl/cpu torch
  uv pip install --python "$G/venv-merge/bin/python" transformers==5.21.0 peft==0.21.2 accelerate safetensors huggingface_hub
fi
if [ ! -x "$G/venv-convert/bin/python" ]; then  # llama.cpp's own converter
  uv venv --python 3.12 "$G/venv-convert"
  uv pip install --python "$G/venv-convert/bin/python" -r "$G/llama.cpp/requirements/requirements-convert_hf_to_gguf.txt"
fi
[ -d "$M/adapter" ] || "$G/venv-merge/bin/hf" download "$REPO" --local-dir "$M/adapter"
if [ ! -f "$M/merged/model.safetensors" ]; then
  CUDA_VISIBLE_DEVICES= "$G/venv-merge/bin/python" - "$M" "$BASE" <<'PY'
import sys, torch
from peft import PeftModel
from transformers import AutoModelForCausalLM
M, base = sys.argv[1:]
m = AutoModelForCausalLM.from_pretrained(base, dtype=torch.bfloat16)
m = PeftModel.from_pretrained(m, f"{M}/adapter").merge_and_unload()
m.save_pretrained(f"{M}/merged", safe_serialization=True)
PY
  # transformers 5 writes a config and tokenizer llama.cpp cannot read: use the base model's originals
  rm -f "$M"/merged/tokenizer* "$M/merged/special_tokens_map.json" "$M/merged/chat_template.jinja" "$M/merged/config.json"
  "$G/venv-merge/bin/hf" download "$BASE" config.json tokenizer.json tokenizer_config.json special_tokens_map.json chat_template.jinja --local-dir "$M/merged" > /dev/null
fi
"$G/venv-convert/bin/python" "$G/llama.cpp/convert_hf_to_gguf.py" "$M/merged" --outfile "$M/gguf/router-f16.gguf" --outtype f16
"$G/llama.cpp/build/bin/llama-quantize" "$M/gguf/router-f16.gguf" "$M/gguf/router-Q4_K_M.gguf" Q4_K_M
rm -f "$M/gguf/router-f16.gguf"
ls -la "$M/gguf"
echo "make up serves this file (ROUTER_MODEL=$NAME, GGUF_TOOLS=$G)"
