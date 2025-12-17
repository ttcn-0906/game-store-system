import asyncio
import json
import struct
import time
import uuid
import os
from pathlib import Path
from dotenv import load_dotenv

current_dir = Path(__file__).resolve().parent
env_path = current_dir / ".." / ".env"
load_dotenv(dotenv_path=env_path)

DB_HOST = os.getenv('DB_HOST')
DB_PORT = os.getenv('DB_PORT')
DATABASE_FILE = 'users.json'
STATS_FILE = 'data.json'
ENCODING = 'utf-8'
HEADER_SIZE = 4  
NETWORK_BYTE_ORDER = '!I' 

DB_FILE_PATH = 'database.json'

class DatabaseHandler:
    def __init__(self):
        self.db = self._load_data()
        
        if not self.db:
            self.db = {
                "Player": {},
                "Developer": {},
                "Game": {},
                "Room": {}
            }
            print("Database file not found or empty. Initializing new database.")
        else:
            self.db['Room'] = {}
            print(f"Database loaded successfully from {DB_FILE_PATH}.")
            
        print("Database initialized with collections: Player, Developer, Game, Room")
    
    def _load_data(self):
        if not os.path.exists(DB_FILE_PATH):
            return None
        
        try:
            with open(DB_FILE_PATH, 'r', encoding=ENCODING) as f:
                return json.load(f)
        except Exception as e:
            print(f"Error loading database from file: {e}")
            return None

    def _save_data(self):
        try:
            with open(DB_FILE_PATH, 'w', encoding=ENCODING) as f:
                json.dump(self.db, f, indent=4, ensure_ascii=False)
            return True
        except Exception as e:
            print(f"Error saving database to file: {e}")
            return False

    def generate_id(self):
        return str(uuid.uuid4())

    def _get_collection(self, collection_name):
        if collection_name not in self.db:
            raise ValueError(f"Collection '{collection_name}' not found.")
        return self.db[collection_name]

    def create(self, collection, data):
        try:
            coll = self._get_collection(collection)
            item_id = self.generate_id()
            timestamp = int(time.time())
            
            new_item = {"id": item_id}
            new_item.update(data)
            
            if collection == "Player":
                new_item['createdAt'] = timestamp
                new_item['lastLoginAt'] = timestamp
                new_item['online'] = False
            elif collection == "Developer":
                new_item['createdAt'] = timestamp
                new_item['lastLoginAt'] = timestamp
                new_item['online'] = False
            elif collection == "Game":
                new_item['createdAt'] = timestamp
            elif collection == "Room":
                new_item['createdAt'] = timestamp
            
            coll[new_item['id']] = new_item
            
            self._save_data()
            
            return {"status": "success", "data": new_item}

        except ValueError as e:
            return {"status": "error", "errorMsg": str(e)}
        except Exception as e:
            return {"status": "error", "errorMsg": f"Database create error: {e}"}

    def read(self, collection, item_id):
        try:
            coll = self._get_collection(collection)
            if item_id in coll:
                return {"status": "success", "data": coll[item_id]}
            else:
                return {"status": "error", "errorMsg": f"{collection} with ID {item_id} not found."}
        except ValueError as e:
            return {"status": "error", "errorMsg": str(e)}

    def update(self, collection, item_id, update_data):
        try:
            coll = self._get_collection(collection)
            if item_id in coll:
                if 'id' in update_data:
                    del update_data['id']
                
                coll[item_id].update(update_data)
                
                self._save_data()
                
                return {"status": "success", "data": coll[item_id]}
            else:
                return {"status": "error", "errorMsg": f"{collection} with ID {item_id} not found."}
        except ValueError as e:
            return {"status": "error", "errorMsg": str(e)}
        except Exception as e:
            return {"status": "error", "errorMsg": f"Database update error: {e}"}

    def delete(self, collection, item_id):
        try:
            coll = self._get_collection(collection)
            if item_id in coll:
                del coll[item_id]
                
                self._save_data()
                
                return {"status": "success", "data": {"id": item_id, "deleted": True}}
            else:
                return {"status": "error", "errorMsg": f"{collection} with ID {item_id} not found."}
        except ValueError as e:
            return {"status": "error", "errorMsg": str(e)}

    def query(self, collection, filter_data):
        try:
            coll = self._get_collection(collection)
            results = []
            
            for item in coll.values():
                match = all(item.get(k) == v for k, v in filter_data.items()) or filter_data == {}
                if match:
                    results.append(item)
            
            return {"status": "success", "data": results}

        except ValueError as e:
            return {"status": "error", "errorMsg": str(e)}
        except Exception as e:
            return {"status": "error", "errorMsg": f"Database query error: {e}"}


DB = DatabaseHandler()


def encode_response(response_dict):
    try:
        json_body = json.dumps(response_dict, ensure_ascii=False)
        body_bytes = json_body.encode(ENCODING)
        
        length = len(body_bytes)

        header_bytes = struct.pack(NETWORK_BYTE_ORDER, length)
        
        return header_bytes + body_bytes
    except Exception as e:
        print(f"Failed to encode response: {e}")
        error_msg = json.dumps({"status": "error", "errorMsg": "Server encoding error."}).encode(ENCODING)
        return struct.pack(NETWORK_BYTE_ORDER, len(error_msg)) + error_msg

def decode_request(data_bytes):
    try:
        request_json = data_bytes.decode(ENCODING)
        return json.loads(request_json)
    except json.JSONDecodeError:
        raise ValueError("Invalid JSON format in request body.")
    except UnicodeDecodeError:
        raise ValueError("Invalid character encoding in request body.")

async def handle_request(request_data: dict):
    collection = request_data.get("collection")
    action = request_data.get("action")
    data = request_data.get("data", {})
    
    if not collection or not action:
        return {"status": "error", "errorMsg": "Missing 'collection' or 'action' field."}

    if action == "create":
        return DB.create(collection, data)
    elif action == "read":
        item_id = data.get("id")
        if not item_id:
            return {"status": "error", "errorMsg": "Missing 'id' for read action."}
        return DB.read(collection, item_id)
    elif action == "update":
        item_id = data.get("id")
        if not item_id:
            return {"status": "error", "errorMsg": "Missing 'id' for update action."}
        return DB.update(collection, item_id, data)
    elif action == "delete":
        item_id = data.get("id")
        if not item_id:
            return {"status": "error", "errorMsg": "Missing 'id' for delete action."}
        return DB.delete(collection, item_id)
    elif action == "query":
        return DB.query(collection, data)
    else:
        return {"status": "error", "errorMsg": f"Invalid action: {action}"}
    

async def handle_client(reader, writer):
    addr = writer.get_extra_info('peername')
    
    default_error_response = encode_response({
        "status": "error", 
        "errorMsg": "Protocol or internal server failure."
    })

    try:
        while True:
            header_data = await reader.readexactly(HEADER_SIZE)
            body_length = struct.unpack(NETWORK_BYTE_ORDER, header_data)[0]
            
            request_body_bytes = await reader.readexactly(body_length)
            
            request_data = decode_request(request_body_bytes)
            print(f"Received from {addr} (Length: {body_length}): {request_data}")
            
            response_dict = await handle_request(request_data)

            response_bytes = encode_response(response_dict)
            writer.write(response_bytes)
            await writer.drain()

    except asyncio.IncompleteReadError:
        pass

    except Exception as e:
        print(f"Protocol or internal server error from {addr}: {e}")
        try:
            writer.write(default_error_response)
            await writer.drain()
        except:
            pass

    finally:
        writer.close()
        await writer.wait_closed()

async def main():
    
    try:
        server = await asyncio.start_server(
            handle_client, DB_HOST, DB_PORT
        )
    except OSError as e:
        print(f"Error: Could not bind to {DB_HOST}:{DB_PORT}. Is the DB_PORT already in use?")
        print(f"Details: {e}")
        return

    addr = server.sockets[0].getsockname()
    print(f"DB server serving on {addr}")

    async with server:
        await server.serve_forever()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("DB server shutting down...")
    finally:
        print("DB server stopped.")