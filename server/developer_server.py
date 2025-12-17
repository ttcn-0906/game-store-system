import asyncio
import json
import struct
import hashlib
import time
import uuid
import os
import base64
import shutil
from pathlib import Path
from dotenv import load_dotenv

current_dir = Path(__file__).resolve().parent
env_path = current_dir / ".." / ".env"
load_dotenv(dotenv_path=env_path)

LOBBY_HOST = os.getenv('SERVER_HOST')
LOBBY_PORT = os.getenv('DEVELOPER_PORT')
ENCODING = 'utf-8'

DB_HOST = os.getenv('DB_HOST')
DB_PORT = os.getenv('DB_PORT')
HEADER_SIZE = 4
NETWORK_BYTE_ORDER = '!I'

ACTIVE_SESSIONS = {} 
UPLOAD_ROOT = "game"


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
    session_id = data.get("sessionID")
    
    if session_id not in ACTIVE_SESSIONS:
        return {"status": "error", "errorMsg": "Invalid session or not logged in."}

    try:
        db_req = {
            "collection": "Game",
            "action": "query",
            "data": {"owner": ACTIVE_SESSIONS[session_id]["name"]}
        }
        
        db_res = await send_db_request(db_req)

        if db_res.get("status") == "success":
            game_list = db_res.get("data", [])
            
            sanitized_list = []
            for game in game_list:
                sanitized_list.append({
                    "gameName": game.get("gameName"),
                    "owner": game.get("owner"),
                    "gameId": game.get("id")
                })

            return {"status": "success", "data": sanitized_list}
        else:
            return {"status": "error", "errorMsg": f"DB query failed: {db_res.get('errorMsg')}"}

    except Exception as e:
        print(f"[Error] List games failed: {e}")
        return {"status": "error", "errorMsg": "Internal server error while listing games."}
    
async def handle_upload_game(data):
    session_id = data.get("sessionID")
    
    if session_id not in ACTIVE_SESSIONS:
        return {"status": "error", "errorMsg": "Invalid session."}

    game_name = data.get("gameName", "untitled_game")
    files = data.get("files", [])

    if len(files) < 2:
        return {"status": "error", "errorMsg": "Two files are required."}

    try:
        unique_id = str(uuid.uuid4())
        folder_name = f"{game_name}_{unique_id[:8]}"
        save_path = os.path.join(UPLOAD_ROOT, folder_name)
        
        if not os.path.exists(save_path):
            os.makedirs(save_path)

        for file_info in files:
            filename = file_info.get("filename")
            content_b64 = file_info.get("content")
            
            safe_filename = os.path.basename(filename)
            file_data = base64.b64decode(content_b64)
            
            with open(os.path.join(save_path, safe_filename), "wb") as f:
                f.write(file_data)

        db_req = {
            "collection": "Game",
            "action": "create",
            "data": {
                "id": unique_id,
                "owner": ACTIVE_SESSIONS[session_id]["name"],
                "gameName": game_name,
                "folderPath": save_path
            }
        }
        await send_db_request(db_req)

        print(f"[Lobby] Game '{game_name}' uploaded successfully to {save_path}")
        return {
            "status": "success", 
            "data": {"gameId": unique_id, "folder": folder_name}
        }

    except Exception as e:
        print(f"[Error] Upload handling failed: {e}")
        return {"status": "error", "errorMsg": f"Server failed to save files: {str(e)}"}

async def handle_update_game(data):
    session_id = data.get("sessionID")
    game_id = data.get("gameId")
    files = data.get("files", [])

    if session_id not in ACTIVE_SESSIONS:
        return {"status": "error", "errorMsg": "Invalid session."}

    try:
        db_req = {
            "collection": "Game",
            "action": "query",
            "data": {"id": game_id}
        }
        db_res = await send_db_request(db_req)

        if db_res.get("status") != "success" or not db_res.get("data"):
            return {"status": "error", "errorMsg": "Game not found in database."}

        folder_path = db_res["data"][0]["folderPath"]
        
        if not os.path.exists(folder_path):
            os.makedirs(folder_path)

        for file_info in files:
            filename = os.path.basename(file_info.get("filename"))
            content_b64 = file_info.get("content")
            
            file_data = base64.b64decode(content_b64)
            with open(os.path.join(folder_path, filename), "wb") as f:
                f.write(file_data)

        print(f"[Lobby] Game assets updated for ID: {game_id} at {folder_path}")
        return {"status": "success", "data": {"gameId": game_id}}

    except Exception as e:
        print(f"[Error] Update failed: {e}")
        return {"status": "error", "errorMsg": f"Server update error: {str(e)}"}

async def handle_delete_game(data):
    session_id = data.get("sessionID")
    game_id = data.get("gameId")

    if session_id not in ACTIVE_SESSIONS:
        return {"status": "error", "errorMsg": "Invalid session."}

    try:
        db_find_req = {
            "collection": "Game",
            "action": "query",
            "data": {"id": game_id}
        }
        db_res = await send_db_request(db_find_req)

        if db_res.get("status") != "success" or not db_res.get("data"):
            return {"status": "error", "errorMsg": "Game not found in database."}

        folder_path = db_res["data"][0]["folderPath"]

        if os.path.exists(folder_path):
            shutil.rmtree(folder_path)
            print(f"[Lobby] Deleted directory: {folder_path}")
        else:
            print(f"[Lobby] Warning: Folder {folder_path} not found on disk, proceeding to DB deletion.")

        db_del_req = {
            "collection": "Game",
            "action": "delete",
            "data": {"id": game_id}
        }
        db_del_res = await send_db_request(db_del_req)

        if db_del_res.get("status") == "success":
            print(f"[Lobby] Game asset {game_id} removed from database.")
            return {"status": "success", "data": {"gameId": game_id}}
        else:
            return {"status": "error", "errorMsg": f"Failed to delete from DB: {db_del_res.get('errorMsg')}"}

    except Exception as e:
        print(f"[Error] Delete failed: {e}")
        return {"status": "error", "errorMsg": f"Server delete error: {str(e)}"}

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
        "collection": "Developer",
        "action": "query",
        "data": {"name": username}
    }
    query_res = await send_db_request(query_req)
    
    if query_res.get("status") == "success" and query_res.get("data"):
        if query_res['data']:
            return {"status": "error", "errorMsg": "User already exists."}
    
    password_hash = hash_password(password)
    
    create_req = {
        "collection": "Developer",
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
        "collection": "Developer",
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
        "collection": "Developer",
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
            "collection": "Developer",
            "action": "update",
            "data": {"id": userid, "online": False}
        }
        await send_db_request(update_req)

        del ACTIVE_SESSIONS[session_id]
        print(f"User logged out: {username} (Session: {session_id})")
        return {"status": "success", "data": {"message": "Logged out successfully."}}
    else:
        return {"status": "error", "errorMsg": "Invalid or expired session ID."}
    

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

            elif action == "list-games":
                response_dict = await handle_list_games(data)

            elif action == "upload-game":
                response_dict = await handle_upload_game(data)
            
            elif action == "update-game":
                response_dict = await handle_update_game(data)
            
            elif action == "delete-game":
                response_dict = await handle_delete_game(data)

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