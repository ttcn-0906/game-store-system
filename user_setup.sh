#!/bin/bash

# Configuration
VENV_NAME=".venv"
SCREEN_NAME="game_system"

echo "------------------------------------------------"
echo "  Game Store System: Multi-Window Launcher"
echo "------------------------------------------------"

# 1. Environment Setup
if [ ! -d "$VENV_NAME" ]; then
    python3 -m venv $VENV_NAME
fi
source $VENV_NAME/bin/activate
pip install -r requirements.txt

echo "------------------------------------------------"
echo "SUCCESS: Finish Virtual Environment Setup."
echo "------------------------------------------------"