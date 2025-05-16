#!/bin/bash

# can't find common/ without this
touch openpilot/__init__.py

uv run main.py
