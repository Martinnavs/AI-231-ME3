#!/usr/bin/env bash
# setup_router.sh: get the router that `make up` serves: the published GGUF and a prebuilt llama.cpp. No compiling, no torch.
# Result: $GGUF_TOOLS/models/$ROUTER_NAME/gguf/router-Q4_K_M.gguf and $GGUF_TOOLS/llama.cpp/build/bin/llama-server
# (the layout scripts/stack/up.sh expects). Needs curl, tar, network (about 250 MB). Linux x64 CPU build; other platforms:
# take the matching asset from https://github.com/ggml-org/llama.cpp/releases and unpack it into $GGUF_TOOLS/llama.cpp/build/bin.
# To use your own retrained adapter instead: scripts/setup_router_gguf.sh.
set -euo pipefail
G=${GGUF_TOOLS:-$HOME/work/gguf-tools}
NAME=${ROUTER_NAME:-router-350m-kd-web4-s0-unverified}
REPO=${ROUTER_GGUF_REPO:-martinnavs/granite-4.0-h-350m-router-kd-web4-s0-GGUF}
SHA=${ROUTER_GGUF_SHA256:-51d2dd3c599db9225f514ed16db82ec9146c55caf8f4c74c28377470e9581e3e}
TAG=${LLAMA_TAG:-b12345}  # the llama.cpp release the router was checked with (same router lines as the source build on 12 of 12 phrases)
F=$G/models/$NAME/gguf/router-Q4_K_M.gguf
mkdir -p "$(dirname "$F")" "$G/llama.cpp/build/bin"
[ -f "$F" ] || curl -fL -o "$F" "https://huggingface.co/$REPO/resolve/main/router-Q4_K_M.gguf"
echo "$SHA  $F" | sha256sum -c -
if [ ! -x "$G/llama.cpp/build/bin/llama-server" ]; then
  curl -fL "https://github.com/ggml-org/llama.cpp/releases/download/$TAG/llama-$TAG-bin-ubuntu-x64.tar.gz" | tar xz -C "$G/llama.cpp/build/bin" --strip-components=1
fi
LD_LIBRARY_PATH="$G/llama.cpp/build/bin" "$G/llama.cpp/build/bin/llama-server" --version 2>&1 | head -2
echo "ready: make up serves $F (ROUTER_MODEL=$NAME, GGUF_TOOLS=$G)"
