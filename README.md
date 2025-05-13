# openpilot clip app

## setup
```bash
git clone https://github.com/greatgitsby/openpilot-clip-discord.git
cd openpilot-clip-discord
git submodule update --init --recursive
cd openpilot
tools/op.sh setup
cd ..
DISCORD_TOKEN='' uv run main.py
```