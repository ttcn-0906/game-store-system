import asyncio
import json
import struct
import sys
import base64
import os
from pathlib import Path
from dotenv import load_dotenv

current_dir = Path(__file__).resolve().parent
env_path = current_dir / ".." / ".env"
load_dotenv(dotenv_path=env_path)

LOBBY_HOST = os.getenv('SERVER_HOST')
LOBBY_PORT = os.getenv('DEVELOPER_PORT')
ENCODING = 'utf-8'
HEADER_SIZE = 4
NETWORK_BYTE_ORDER = '!I'

client_state = {
    'sessionID': None,
    'userId': None,
    'name': None,
    'reader': None,
    'writer': None,
    'connected': False,
}

def encode_message(data_dict):
    try:
        json_body = json.dumps(data_dict, ensure_ascii=False)
        body_bytes = json_body.encode(ENCODING)
        
        length = len(body_bytes)
        header_bytes = struct.pack(NETWORK_BYTE_ORDER, length)
        
        return header_bytes + body_bytes
    except Exception as e:
        #print(f"[{time.strftime('%H:%M:%S')}] decode error -> {e}")
        return b''

async def read_response(reader):
    try:
        header_data = await reader.readexactly(HEADER_SIZE)
        body_length = struct.unpack(NETWORK_BYTE_ORDER, header_data)[0]
        
        response_body_bytes = await reader.readexactly(body_length)
        
        response_json = response_body_bytes.decode(ENCODING)
        return json.loads(response_json)
    
    except asyncio.IncompleteReadError:
        #print(f"\n[{time.strftime('%H:%M:%S')}] server closed")
        raise
    except Exception as e:
        #print(f"\n[{time.strftime('%H:%M:%S')}] protocol error -> {e}")
        return {"status": "error", "errorMsg": "Protocol error receiving response."}

async def list_game_logic():
    print("\n--- Available Games ---")
    response = await send_command("list-games", {})
    
    if response and response.get("status") == "success":
        games = response.get("data", [])
        if not games:
            print("No games found.")
            return []

        print(f"{'No.':<4} | {'Game Name':<20} | {'ID':<10}")
        print("-" * 40)
        for idx, game in enumerate(games, 1):
            print(f"{idx:<4} | {game.get('gameName')[:8]:<20} | {game.get('gameId'):<10}")
        return games
    else:
        print(f"[Error] Failed to fetch games: {response.get('errorMsg')}")
        return []

async def upload_game_logic():
    print("\n--- Upload Game Files ---")
    file1_path = await get_input("Enter path for server.py: ")
    file2_path = await get_input("Enter path for client.py: ")

    if not os.path.exists(file1_path) or not os.path.exists(file2_path):
        print("Error: One or both files do not exist.")
        return

    try:
        with open(file1_path, "rb") as f1, open(file2_path, "rb") as f2:
            file1_content = base64.b64encode(f1.read()).decode('utf-8')
            file2_content = base64.b64encode(f2.read()).decode('utf-8')

        data = {
            "gameName": await get_input("Enter a name for this game: "),
            "files": [
                {"filename": os.path.basename(file1_path), "content": file1_content},
                {"filename": os.path.basename(file2_path), "content": file2_content}
            ]
        }

        # 發送至 Server
        await send_command("upload-game", data)
    except Exception as e:
        print(f"Upload failed: {e}")

async def update_game_logic():
    games = await list_game_logic()
    if not games:
        return

    choice = await get_input("\nEnter the No. to update (or 'q' to cancel): ")
    if choice.lower() == 'q':
        return

    try:
        idx = int(choice) - 1
        if idx < 0 or idx >= len(games):
            print("Invalid number.")
            return
        
        target_game = games[idx]
        game_id = target_game['gameId']
        print(f"Updating Game: {target_game['gameName']} (ID: {game_id})")

    except ValueError:
        print("Please enter a valid number.")
        return

    file1_path = await get_input("Enter new path for server.py: ")
    file2_path = await get_input("Enter new path for client.py: ")

    if not os.path.exists(file1_path) or not os.path.exists(file2_path):
        print("Error: Files do not exist.")
        return

    try:
        with open(file1_path, "rb") as f1, open(file2_path, "rb") as f2:
            file1_content = base64.b64encode(f1.read()).decode('utf-8')
            file2_content = base64.b64encode(f2.read()).decode('utf-8')

        data = {
            "gameId": game_id,
            "files": [
                {"filename": os.path.basename(file1_path), "content": file1_content},
                {"filename": os.path.basename(file2_path), "content": file2_content}
            ]
        }

        await send_command("update-game", data)
    except Exception as e:
        print(f"Update failed: {e}")

async def delete_game_logic():
    games = await list_game_logic()
    if not games:
        return

    choice = await get_input("\nEnter the No. to DELETE (or 'q' to cancel): ")
    if choice.lower() == 'q':
        return

    try:
        idx = int(choice) - 1
        if idx < 0 or idx >= len(games):
            print("Invalid number.")
            return
        
        target_game = games[idx]
        game_id = target_game['gameId']
        game_name = target_game['gameName']

        confirm = await get_input(f"Are you sure you want to delete '{game_name}'? (y/N): ")
        if confirm.lower() != 'y':
            print("Deletion cancelled.")
            return

        data = {"gameId": game_id}
        await send_command("delete-game", data)

    except ValueError:
        print("Please enter a valid number.")

async def send_command(action, data=None):
    if data is None:
        data = {}

    if client_state['sessionID'] and action not in ["register", "login"]:
        data['sessionID'] = client_state['sessionID']

    request = {
        "action": action,
        "data": data,
    }
    
    if not client_state['connected']:
        print("Please 'connect' to the server first.")
        return

    try:
        writer = client_state['writer']
        
        request_bytes = encode_message(request)
        writer.write(request_bytes)
        await writer.drain()
        
        response = await read_response(client_state['reader'])
        
        if action == "login" and response.get("status") == "success":
            client_state['sessionID'] = response['data']['sessionID']
            client_state['userId'] = response['data']['userId']
            client_state['name'] = response['data']['name']
            print(f"Login successful! User: {client_state['name']} (Session: {client_state['sessionID'][:8]}...)")
            
        elif action == "logout" and response.get("status") == "success":
            print(f"Logout successful!")
            client_state['sessionID'] = None
            client_state['userId'] = None
            client_state['name'] = None
            
        elif action == "register" and response.get("status") == "success":
            print(f"Register successful! Username: {response['data']['name']}")

        elif action == "upload-game" and response.get("status") == "success":
            print(f"Upload successful! GameID: {response['data']['gameId']}")
            
        else:
            if response.get('errorMsg'):
                print(f"Error: {response['errorMsg']}")
            else:
                pass

        return response
            
    except asyncio.IncompleteReadError:
        client_state['connected'] = False
        client_state['sessionID'] = None
        client_state['userId'] = None
        client_state['name'] = None
    except Exception as e:
        #print(f"[{time.strftime('%H:%M:%S')}] Internet error: {e}")
        pass

async def get_input(prompt):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: input(prompt))

async def show_games_menu():
    while True:
        print("\n------- Games Menu -------")
        print("1. List Games")
        print("2. Upload Game")
        print("3. Update Game")
        print("4. Delete Game")
        print("5. Back to Main Menu")
        print("----------------------------")
        
        choice = await get_input("Choice (1-5): ")

        if choice == '1':
            await list_game_logic()
        
        elif choice == '2':
            await upload_game_logic()

        elif choice == '3':
            await update_game_logic()

        elif choice == '4':
            await delete_game_logic()
        
        elif choice == '5':
            break
        else:
            print("Invalid choice, please try again.")

async def show_main_menu():
    while True:
        status = f"Logged in as: {client_state['name']}" if client_state['sessionID'] else "Not Logged In"
        print(f"\n--- Main Menu ({status}) ---")
        print("1. Register")
        print("2. Login")
        print("3. Games")
        print("4. Exit")
        print("----------------------------")
        
        choice = await get_input("Choice (1-4): ")

        if choice == '1':
            username = await get_input("Username: ")
            password = await get_input("Password: ")
            await send_command("register", {"username": username, "password": password})

        elif choice == '2':
            username = await get_input("Username: ")
            password = await get_input("Password: ")
            await send_command("login", {"username": username, "password": password})

        elif choice == '3':
            if not client_state['sessionID']:
                print("\n[Error] Please login first before entering Games menu.")
            else:
                await show_games_menu()

        elif choice == '4':
            if client_state['sessionID']:
                await send_command("logout", {})
            print("Exiting...")
            sys.exit(0)
        else:
            print("Invalid choice, please try again.")

async def main_loop():
    print(f"Client Started.")
    print(f"Connecting to {LOBBY_HOST}:{LOBBY_PORT}...")
    
    try:
        reader, writer = await asyncio.open_connection(LOBBY_HOST, LOBBY_PORT)
        client_state['reader'] = reader
        client_state['writer'] = writer
        client_state['connected'] = True
        print(f"Successfully connected to the server!")

        await show_main_menu()

    except ConnectionRefusedError:
        print(f"Connect failed. Is server running at {LOBBY_HOST}:{LOBBY_PORT}?")
    except Exception as e:
        print(f"Connect failed: {e}")

if __name__ == "__main__":
    try:
        asyncio.run(main_loop())
    except (KeyboardInterrupt, SystemExit):
        print("\nClient terminated.")