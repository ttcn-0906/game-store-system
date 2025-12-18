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

# 2. Cleanup existing session
screen -S $SCREEN_NAME -X quit 2>/dev/null

# 3. Create a detached screen session and run DB in the first window (window 0)
echo "-> Starting DB in Window 0..."
screen -dmS $SCREEN_NAME -t database bash -c "source $VENV_NAME/bin/activate && python server/db.py; exec bash"

sleep 2 # Wait for DB

# 4. Create a new window (window 1) for Developer Server
echo "-> Starting Developer Server in Window 1..."
screen -S $SCREEN_NAME -X screen -t developer bash -c "source $VENV_NAME/bin/activate && python server/developer_server.py; exec bash"

sleep 1

# 5. Create a new window (window 2) for Player Server
echo "-> Starting Player Server in Window 2..."
screen -S $SCREEN_NAME -X screen -t player bash -c "source $VENV_NAME/bin/activate && python server/player_server.py; exec bash"

echo "------------------------------------------------"
echo "SUCCESS: 3 Servers are running in Screen session: $SCREEN_NAME"
echo "Use 'screen -r $SCREEN_NAME' to attach."
echo "Inside Screen, use 'Ctrl+A' then 'N' to switch to next window."
echo "------------------------------------------------"