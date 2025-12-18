import asyncio
import json
import struct
import hashlib
import time
import uuid
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

DB_HOST = os.getenv('DB_HOST')
DB_PORT = int(os.getenv('DB_PORT'))
HEADER_SIZE = 4
NETWORK_BYTE_ORDER = '!I'
GAME_ROOMS = {}
GAME_SERVER_PORT_BASE = int(os.getenv('GAME_SERVER_PORT_BASE'))
NEXT_PORT = GAME_SERVER_PORT_BASE

ACTIVE_SESSIONS = {} 


def encode_message(data_dict):
    try:
        json_body = json.dumps(data_dict, ensure_ascii=False)
        body_bytes = json_body.encode(ENCODING)
        
        length = len(body_bytes)
        header_bytes = struct.pack(NETWORK_BYTE_ORDER, length)
        
        return header_bytes + body_bytes
    except Exception as e:
        print(f"Error encoding message: {e}")
        return b''

async def read_response(reader):
    try:
        header_data = await reader.readexactly(HEADER_SIZE)
        body_length = struct.unpack(NETWORK_BYTE_ORDER, header_data)[0]
        
        response_body_bytes = await reader.readexactly(body_length)
        
        response_json = response_body_bytes.decode(ENCODING)
        return json.loads(response_json)
    
    except asyncio.IncompleteReadError:
        print("DB Server connection closed unexpectedly.")
        return {"status": "error", "errorMsg": "DB Server disconnected."}
    except Exception as e:
        print(f"Error reading DB response: {e}")
        return {"status": "error", "errorMsg": "Protocol error receiving DB response."}

async def send_db_request(request_dict):
    try:
        reader, writer = await asyncio.open_connection(DB_HOST, DB_PORT)
        
        request_bytes = encode_message(request_dict)
        writer.write(request_bytes)
        await writer.drain()
        
        response_dict = await read_response(reader)
        
        writer.close()
        await writer.wait_closed()
        
        return response_dict
        
    except ConnectionRefusedError:
        print(f"Connection to DB Server refused. Is DB Server running on {DB_PORT}?")
        return {"status": "error", "errorMsg": "Database server unavailable."}
    except Exception as e:
        print(f"General DB communication error: {e}")
        return {"status": "error", "errorMsg": f"DB communication error: {e}"}
    
async def handle_list_games(data):
    """從 DB 抓取所有遊戲資產清單並回傳"""
    session_id = data.get("sessionID")
    
    if session_id not in ACTIVE_SESSIONS:
        return {"status": "error", "errorMsg": "Invalid session or not logged in."}

    try:
        db_req = {
            "collection": "Game",
            "action": "query",
            "data": {}
        }
        
        db_res = await send_db_request(db_req)

        if db_res.get("status") == "success":
            game_list = db_res.get("data", [])
            
            sanitized_list = []
            for game in game_list:
                sanitized_list.append({
                    "gameName": game.get("gameName"),
                    "owner": game.get("owner"),
                    "gameId": game.get("id"),
                    "description": game.get("description")
                })

            return {"status": "success", "data": sanitized_list}
        else:
            return {"status": "error", "errorMsg": f"DB query failed: {db_res.get('errorMsg')}"}

    except Exception as e:
        print(f"[Error] List games failed: {e}")
        return {"status": "error", "errorMsg": "Internal server error while listing games."}

async def wait_for_game_end(room_id, process):
    print(f"[Game Monitor] Monitoring game {room_id}...")
    
    try:
        stdout_data, stderr_data = await process.communicate()
    except Exception as e:
        print(f"[Game Monitor] Error communicating with process {room_id}: {e}")
        return

    if process.returncode != 0:
        error_msg = stderr_data.decode().strip()
        print(f"[Game Monitor] Game {room_id} failed with code {process.returncode}: {error_msg}")
        return

    result_str = stdout_data.decode().strip()
    try:
        game_result = json.loads(result_str)
        print(f"[Game Monitor] Game {room_id} finished. Result: {game_result}")
        # await handle_game_history({"id": room_id, "winner": game_result["winner"]})
        
    except json.JSONDecodeError:
        print(f"[Game Monitor] Game {room_id} finished, but failed to decode JSON result: {result_str}")
    
    if room_id in GAME_ROOMS:
        await handle_delete_room({"id": room_id}, admin=True)
        print(f"[Game Monitor] Cleaned up room {room_id}.")

    
async def handle_create_room(data):
    global NEXT_PORT

    session_id = data.get("sessionID")
    game_id = data.get("gameId")
    invite = data.get("invite")

    if session_id not in ACTIVE_SESSIONS:
        return {"status": "error", "errorMsg": "Invalid session or not logged in."}

    if not game_id:
        return {"status": "error", "errorMsg": "gameId is required to create a room."}

    try:
        db_game_req = {
            "collection": "Game",
            "action": "query",
            "data": {"id": game_id}
        }
        db_game_res = await send_db_request(db_game_req)

        if db_game_res.get("status") != "success" or not db_game_res.get("data"):
            return {"status": "error", "errorMsg": "Selected game asset not found."}

        game_folder_path = db_game_res["data"][0]["folderPath"]

        port = NEXT_PORT
        NEXT_PORT += 1

        room_data = {
            "owner": ACTIVE_SESSIONS[session_id]["name"],
            "players": [],
            "spectators": [],
            "invite": invite,
            "visibility": data.get("visibility", "public"),
            "port": port,
            "gameId": game_id
        }

        create_req = {
            "collection": "Room",
            "action": "create",
            "data": room_data
        }
        create_res = await send_db_request(create_req)
        
        if create_res.get("status") != "success":
            return {"status": "error", "errorMsg": f"Failed to create room in DB: {create_res.get('errorMsg')}"}
        
        room_info = create_res['data']
        room_id = room_info['id']

        print(f"[Lobby] Starting Game Server for room {room_id} using game in {game_folder_path}...")
        
        process = await create_subprocess_exec(
            "python", "server.py", str(LOBBY_HOST), str(port), str(room_id),
            cwd=game_folder_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        GAME_ROOMS[room_id] = process

        asyncio.create_task(wait_for_game_end(room_id, process))

        return {"status": "success", "data": {"id": room_id, "port": port}}

    except Exception as e:
        print(f"[Error] Create room exception: {e}")
        return {"status": "error", "errorMsg": f"Server internal error: {str(e)}"}
    

async def handle_delete_room(data, admin=False):
    room_id = data.get("id")
    session_id = data.get("sessionID")

    matching_rooms = [
        key for key in GAME_ROOMS.keys() 
        if key.startswith(room_id)
    ]

    if matching_rooms == []:
        return {"status": "error", "errorMsg": "Room not found."}
    elif len(matching_rooms) >=2 :
        return {"status": "error", "errorMsg": "Ambiguous ID."}
    
    room_id = matching_rooms[0]

    room = GAME_ROOMS[room_id]
    if not admin:
        user = ACTIVE_SESSIONS.get(session_id)

    query_req = {
        "collection": "Room",
        "action": "query",
        "data": {"id": room_id}
    }
    query_res = await send_db_request(query_req)

    if query_res.get("status") != "success":
        return {"status": "error", "errorMsg": "DB dead."}
    
    owner = query_res.get("data")[0]['owner']

    if not admin and (not user or user["name"] != owner):
        return {"status": "error", "errorMsg": "Only the room owner can delete the room."}
    
    delete_req = {
        "collection": "Room",
        "action": "delete",
        "data": {"id": room_id}
    }
    delete_res = await send_db_request(delete_req)

    if delete_res.get("status") != "success":
        return {"status": "error", "errorMsg": "DB dead."}

    proc = room
    if proc:
        try:
            proc.terminate()
            await proc.wait()
        except:
            pass

    del GAME_ROOMS[room_id]
    print(f"[Lobby] Room {room_id} deleted.")
    return {"status": "success", "data": {"deletedRoom": room_id}}


async def handle_join_room(data):
    room_id = data.get("id")
    session_id = data.get("sessionID")
    role = data.get("role", "spectator")

    matching_rooms = [
        key for key in GAME_ROOMS.keys() 
        if key.startswith(room_id)
    ]

    if matching_rooms == []:
        return {"status": "error", "errorMsg": "Room not found."}
    elif len(matching_rooms) >=2 :
        return {"status": "error", "errorMsg": "Ambiguous ID."}
    
    room_id = matching_rooms[0]

    user = ACTIVE_SESSIONS.get(session_id)
    if not user:
        return {"status": "error", "errorMsg": "Invalid session."}

    room = GAME_ROOMS[room_id]
    name = user["name"]

    query_req = {
        "collection": "Room",
        "action": "query",
        "data": {"id": room_id}
    }
    query_res = await send_db_request(query_req)

    if query_res.get("status") != "success":
        return {"status": "error", "errorMsg": "DB dead."}
    
    room = query_res.get("data")[0]

    if role in ["p1", "p2"]:
        if len(room["players"]) >= 2:
            return {"status": "error", "errorMsg": "Room is full."}
        is_role_taken = any(player["role"] == role for player in room["players"])
        if not is_role_taken:
            room["players"].append({"name": name, "role": role})
        else:
            return {"status": "error", "errorMsg": f"Role '{role}' is already taken."}
    else:
        room["spectators"].append(name)

    update_req = {
        "collection": "Room",
        "action": "update",
        "data": room
    }
    await send_db_request(update_req)

    try:
        game_id = room.get("gameId")
        db_game_req = {
            "collection": "Game",
            "action": "query",
            "data": {"id": game_id}
        }
        db_game_res = await send_db_request(db_game_req)
        
        if db_game_res.get("status") != "success":
            return {"status": "error", "errorMsg": "Failed to retrieve game assets info."}
        
        game_folder = db_game_res["data"][0]["folderPath"]
        client_code_path = os.path.join(game_folder, "client.py")
        
        if not os.path.exists(client_code_path):
            return {"status": "error", "errorMsg": "Game client file not found on server."}
            
        with open(client_code_path, "rb") as f:
            client_code_b64 = base64.b64encode(f.read()).decode('utf-8')

        print(f"[Lobby] {name} joined room {room_id}, sending client code.")
        
        return {
            "status": "success", 
            "data": {
                "id": room_id, 
                "port": room['port'], 
                "role": role,
                "clientCode": client_code_b64,
                "gameName": db_game_res["data"][0].get("gameName", "game"),
                "owner": db_game_res["data"][0].get("owner", "unknown")
            }
        }

    except Exception as e:
        return {"status": "error", "errorMsg": f"Join room failed: {str(e)}"}


def hash_password(password):
    return hashlib.sha256(password.encode(ENCODING)).hexdigest()

def verify_password(password, password_hash):
    return hash_password(password) == password_hash


async def handle_register(data):
    username = data.get("username")
    password = data.get("password")
    
    if not username or not password:
        return {"status": "error", "errorMsg": "Username and password are required."}
        
    query_req = {
        "collection": "Player",
        "action": "query",
        "data": {"name": username}
    }
    query_res = await send_db_request(query_req)
    
    if query_res.get("status") == "success" and query_res.get("data"):
        if query_res['data']:
            return {"status": "error", "errorMsg": "User already exists."}
    
    password_hash = hash_password(password)
    
    create_req = {
        "collection": "Player",
        "action": "create",
        "data": {
            "name": username,
            "passwordHash": password_hash,
        }
    }
    create_res = await send_db_request(create_req)
    
    if create_res.get("status") == "success":
        user_info = create_res['data']
        print(f"User registered: {username} (ID: {user_info['id']})")
        return {"status": "success", "data": {"userId": user_info['id'], "name": user_info['name']}}
    else:
        return {"status": "error", "errorMsg": f"Registration failed in DB: {create_res.get('errorMsg')}"}

async def handle_login(data):
    username = data.get("username")
    password = data.get("password")
    
    if not username or not password:
        return {"status": "error", "errorMsg": "Username and password are required."}

    query_req = {
        "collection": "Player",
        "action": "query",
        "data": {"name": username}
    }
    query_res = await send_db_request(query_req)
    
    if query_res.get("status") != "success" or not query_res['data']:
        return {"status": "error", "errorMsg": "Invalid username or password."}
        
    user_info = query_res['data'][0]

    online = user_info.get("online")
    if online:
        return {"status": "error", "errorMsg": "User already online."}
    
    stored_hash = user_info.get("passwordHash")
    if not verify_password(password, stored_hash):
        return {"status": "error", "errorMsg": "Invalid username or password."}
    

    session_id = str(uuid.uuid4())
    user_data = {
        "userId": user_info['id'],
        "name": user_info['name'],
    }
    ACTIVE_SESSIONS[session_id] = user_data
    
    update_req = {
        "collection": "Player",
        "action": "update",
        "data": {"id": user_info['id'], "lastLoginAt": int(time.time()), "online": True}
    }
    await send_db_request(update_req)
    
    print(f"User logged in: {username} (Session: {session_id})")
    return {"status": "success", "data": {"sessionID": session_id, "userId": user_data['userId'], "name": user_data['name']}}

async def handle_logout(session_id):
    if session_id in ACTIVE_SESSIONS:
        username = ACTIVE_SESSIONS[session_id]['name']
        userid = ACTIVE_SESSIONS[session_id]['userId']

        update_req = {
            "collection": "Player",
            "action": "update",
            "data": {"id": userid, "online": False}
        }
        await send_db_request(update_req)

        del ACTIVE_SESSIONS[session_id]
        print(f"User logged out: {username} (Session: {session_id})")
        return {"status": "success", "data": {"message": "Logged out successfully."}}
    else:
        return {"status": "error", "errorMsg": "Invalid or expired session ID."}
    
async def handle_rooms(data):
    collection = "Room"
    filter_data = {"visibility": "public"}

    db_request = {
        "collection": collection,
        "action": "query",
        "data": filter_data
    }

    print(f"[LOG] Forwarding query to DB: {collection} with filter {filter_data}")
    db_response_public = await send_db_request(db_request)

    if db_response_public.get("status") != "success":
        return {"status": "error", "errorMsg": db_response_public.get("errorMsg", "Database query failed.")}
    
    collection = "Room"
    filter_data = {"visibility": "private", "invite": data["invite"]}

    db_request = {
        "collection": collection,
        "action": "query",
        "data": filter_data
    }

    print(f"[LOG] Forwarding query to DB: {collection} with filter {filter_data}")
    db_response_private = await send_db_request(db_request)

    collection = "Room"
    filter_data = {"visibility": "private", "owner": data["invite"]}

    db_request = {
        "collection": collection,
        "action": "query",
        "data": filter_data
    }

    print(f"[LOG] Forwarding query to DB: {collection} with filter {filter_data}")
    db_response_own = await send_db_request(db_request)
    
    #print(db_response_private, db_response_public, db_response_own)

    return {"status": "success", "data": db_response_private.get('data') + db_response_public.get('data') + db_response_own.get('data')}

async def handle_client(reader, writer):
    addr = writer.get_extra_info('peername')
    print(f"[LOBBY] New client connection from {addr}")
    current_session_id = None
    
    try:
        while True:
            header_data = await reader.readexactly(HEADER_SIZE)
            body_length = struct.unpack(NETWORK_BYTE_ORDER, header_data)[0]
            
            request_body_bytes = await reader.readexactly(body_length)
            request_data = json.loads(request_body_bytes.decode(ENCODING))
            
            action = request_data.get("action")
            data = request_data.get("data", {})
            response_dict = {"status": "error", "errorMsg": "Invalid request or action."}
            
            if action == "register":
                response_dict = await handle_register(data)
                
            elif action == "login":
                response_dict = await handle_login(data)
                if response_dict.get("status") == "success":
                    current_session_id = response_dict['data']['sessionID']
                    
            elif action == "logout":
                session_id = data.get("sessionID")
                if not session_id:
                    session_id = current_session_id
                
                response_dict = await handle_logout(session_id)
                current_session_id = None

            elif action == "create-room":
                response_dict = await handle_create_room(data)

            elif action == "delete-room":
                response_dict = await handle_delete_room(data)

            elif action == "join-room":
                response_dict = await handle_join_room(data)

            elif action == "rooms":
                response_dict = await handle_rooms(data)
            
            elif action == "list-games":
                response_dict = await handle_list_games(data)
                
            response_bytes = encode_message(response_dict)
            writer.write(response_bytes)
            await writer.drain()

    except asyncio.IncompleteReadError:
        pass
    except json.JSONDecodeError:
        print(f"[LOBBY] Invalid JSON from client {addr}.")
        writer.write(encode_message({"status": "error", "errorMsg": "Invalid JSON format."}))
        await writer.drain()
    except Exception as e:
        print(f"[LOBBY] Unexpected error with client {addr}: {e}")
        
    finally:
        if current_session_id in ACTIVE_SESSIONS:
            print(f"[LOBBY] Force logging out session {current_session_id} due to connection closure.")
            _ = await handle_logout(current_session_id)
            
        writer.close()
        await writer.wait_closed()
        print(f"[LOBBY] Connection from {addr} closed.")

async def main():
    """Initializes and runs the Asyncio TCP lobby server."""
    
    try:
        server = await asyncio.start_server(
            handle_client, LOBBY_HOST, LOBBY_PORT
        )
    except OSError as e:
        print(f"Error: Could not bind to {LOBBY_HOST}:{LOBBY_PORT}. Is the LOBBY_PORT already in use?")
        print(f"Details: {e}")
        return

    addr = server.sockets[0].getsockname()
    print(f"Lobby Server serving on {addr}")

    async with server:
        await server.serve_forever()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Server shutting down...")
    finally:
        print("Server stopped.")