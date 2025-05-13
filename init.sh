#!/bin/bash

# *** dependencies install ***
if ! command -v uv &>/dev/null; then
  echo "'uv' is not installed. Installing 'uv'..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
fi

git submodule update --init --recursive --progress --depth 1

pushd openpilot
tools/op.sh setup
tools/op.sh build
popd
