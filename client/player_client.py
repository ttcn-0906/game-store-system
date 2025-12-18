import asyncio
import json
import struct
import sys
import os
import base64
from pathlib import Path
from dotenv import load_dotenv
from asyncio.subprocess import create_subprocess_exec

current_dir = Path(__file__).resolve().parent
env_path = current_dir / ".." / ".env"
load_dotenv(dotenv_path=env_path)

LOBBY_HOST = os.getenv('SERVER_HOST')
LOBBY_PORT = int(os.getenv('PLAYER_PORT'))
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
    
async def list_room_logic():
    print("\n--- Available Rooms ---")
    
    response = await send_command("rooms", {
        "invite": client_state['name']
    })

    if response and response.get("status") == "success":
        rooms = response.get("data", [])
        if not rooms:
            print("No available rooms found. Why not create one?")
            return []

        print(f"{'No.':<4} | {'Owner':<15} | {'Room ID':<10} | {'Visibility':<10}")
        print("-" * 50)
        
        for idx, room in enumerate(rooms, 1):
            owner = room.get("owner", "Unknown")
            room_id = room.get("id", "N/A")[:8]
            visibility = room.get("visibility", "public")
            
            print(f"{idx:<4} | {owner:<15} | {room_id:<10} | {visibility:<10}")
        
        return rooms
    else:
        error_msg = response.get("errorMsg", "Unknown error")
        print(f"[Error] Failed to fetch rooms: {error_msg}")
        return []

async def create_room_logic():
    games = await list_game_logic()
    if not games:
        print("You need to upload a game first before creating a room!")
        return

    choice = await get_input("\nSelect a game to host (No.) or 'q' to cancel: ")
    if choice.lower() == 'q':
        return

    try:
        idx = int(choice) - 1
        if 0 <= idx < len(games):
            selected_game = games[idx]
            game_id = selected_game['gameId']
            game_name = selected_game['gameName']
            
            print(f"Selected Game: {game_name}")
            
            # visibility = await get_input("Enter visibility (public/private) [default: public]: ")
            # if visibility not in ["public", "private"]:
            #     visibility = "public"
            visibility = "public"
                
            # invite = await get_input("Enter invited player name (optional, press Enter to skip): ")
            invite = None
            
            await send_command("create-room", {
                "gameId": game_id,
                "visibility": visibility,
                "invite": invite if invite and invite.strip() else None
            })
        else:
            print("Invalid number.")
    except ValueError:
        print("Please enter a valid number.")

async def join_room_by_number_logic():
    rooms = await list_room_logic()
    if not rooms:
        return

    choice = await get_input("\nEnter the No. to join (or 'q' to cancel): ")
    if choice.lower() == 'q':
        return

    try:
        idx = int(choice) - 1
        if 0 <= idx < len(rooms):
            target_room = rooms[idx]
            room_id = target_room['id']
            
            role = await get_input("Enter Role (player/spectator): ")
            if not role: role = "player"
            
            await send_command("join-room", {
                "id": room_id,
                "role": role
            })
        else:
            print("Invalid number.")
    except ValueError:
        print("Please enter a valid number.")

async def list_game_logic():
    print("\n--- Available Games ---")
    response = await send_command("list-games", {})
    
    if response and response.get("status") == "success":
        games = response.get("data", [])
        if not games:
            print("No games found.")
            return []

        print(f"{'No.':<4} | {'Game Name':<10} | {'ID':<10} | {'Description':<20}")
        print("-" * 50)
        for idx, game in enumerate(games, 1):
            print(f"{idx:<4} | {game.get('gameName'):<10} | {game.get('gameId')[:8]:<10} | {game.get('description'):<20}")
        return games
    else:
        print(f"[Error] Failed to fetch games: {response.get('errorMsg')}")
        return []

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

        elif action == "create-room" and response.get("status") == "success":
            print(f"Room {response['data']['id'][:8]}... created. Use 'join-room' to join.")
        elif action == "join-room" and response.get("status") == "success":
            res_data = response['data']
            room_id_short = res_data['id'][:8]
            print(f"Joined room {room_id_short}... as {res_data['role']}.")
            
            client_code_b64 = res_data.get("clientCode")
            if client_code_b64:
                download_dir = f"game/{client_state['name']}"
                if not os.path.exists(download_dir):
                    os.makedirs(download_dir)
                
                temp_filename = os.path.join(download_dir, f"client_{res_data['gameName']}_{res_data['owner']}.py")
                
                with open(temp_filename, "wb") as f:
                    f.write(base64.b64decode(client_code_b64))
                
                print(f"Game client downloaded: {temp_filename}")
                
                await create_subprocess_exec(
                    "python", temp_filename, 
                    str(LOBBY_HOST), 
                    str(res_data['port']), 
                    res_data['role'], 
                    client_state['name']
                )
            else:
                print("Error: No client code received from server.")

        elif action == "delete-room" and response.get("status") == "success":
            print(f"Deleted room {response['data']['id'][:8]}....")
            
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
        print("2. List Rooms")
        print("3. Create Room")
        print("4. Join Room")
        print("5. Back to Main Menu")
        print("----------------------------")
        
        choice = await get_input("Choice (1-5): ")

        if choice == '1':
            await list_game_logic()
        
        elif choice == '2':
            await list_room_logic()

        elif choice == '3':
            await create_room_logic()
        
        elif choice == '4':
            await join_room_by_number_logic()

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