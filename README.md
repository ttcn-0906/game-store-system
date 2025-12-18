# game-store-system

A distributed game platform system that enables developers to upload games and players to join rooms with instant-download clients.

## Project Structure

The project is organized into administrative servers, user-facing clients, and game templates.

```text
.
├── client/
│   ├── developer_client.py  # GUI/CLI for game developers (Upload/Update/Delete)
│   └── player_client.py     # GUI/CLI for players (List rooms/Join/Create)
├── game/                    # Template folder for new games
│   ├── client.py            # Client-side game logic (Downloaded by players)
│   └── server.py            # Server-side game logic (Launched in rooms)
├── server/
│   ├── db.py                # Database API Service (Must start first)
│   ├── developer_server.py  # Handles asset management requests
│   └── player_server.py     # Lobby server for matchmaking and room creation
├── .env                     # Environment variables (Host, Port, etc.)
├── .gitignore               # Git exclusion rules (Excludes .env, .venv)
├── README.md                # Project documentation
├── requirements.txt         # Python dependencies
├── server_setup.sh          # Automation script for the backend servers
└── user_setup.sh            # Automation script for the end-user clients
```

## Configuration (.env)

Before running the servers or clients, you **must** create a `.env` file in the root directory. This file stores network configurations to avoid hardcoding IP addresses and ports.

Create a file named `.env` and paste the following:

```
# Server Network Settings
SERVER_HOST=127.0.0.1
PLAYER_PORT=10001
DEVELOPER_PORT=10002

# Database Settings
DB_HOST=127.0.0.1
DB_PORT=10000

# Game Room Settings
GAME_SERVER_PORT_BASE=10003
```

## Server Setup (Backend)

The `server_setup.sh` script automates the deployment of the database and both management servers using **GNU Screen**.

### Prerequisites

- Linux environment.
- `screen` utility.
- Python 3.8+

### Launching the Infrastructure

1. **Run the script:**

```
bash
source server_setup.sh
```

This script creates a virtual environment, installs dependencies, and launches a screen session named `game_system` with three dedicated windows: `database`, `developer`, and `player`.

Managing Server Windows

To monitor the servers or debug, attach to the screen session:

```
screen -r game_system
```

- **Switch Windows:** Press Ctrl+A, then N (Next) or P (Previous).
- **Detach:** Press Ctrl+A, then D to leave servers running in the background.
- **Kill Session:** screen -S game_system -X quit

## User Setup (Client)

The `user_setup.sh` script prepares the environment for players and developers to run their respective clients.

1. **Run the setup:**

```
bash
source user_setup.sh
```

2. **Start the Player Client:**

```
cd client
python player_client.py
```


3. **Start the Developer Client:**

```
cd client
python developer_client.py
```


## Key Features

- **Sequential Bootstrapping:** Automatically ensures db.py is initialized before dependent servers start.
- **Instant Play:** When a player joins a room, the system dynamically fetches the latest game/client.py from the server, saves it locally, and executes it.
- **Environment Isolation:** Uses cwd (Current Working Directory) logic to run game server instances in their own dedicated asset folders.
