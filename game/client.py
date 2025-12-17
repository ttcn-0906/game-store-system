import sys
import json
import time
import struct
import asyncio
import pygame

ENCODING = "utf-8"
HEADER_SIZE = 4
NETWORK_BYTE_ORDER = "!I"

CELL_SIZE = 20
BOARD_WIDTH = 10
BOARD_HEIGHT = 20
MARGIN = 50
FPS = 30

TETROMINO_BASE = {
    "I": [(-2,0),(-1,0),(0,0),(1,0)],
    "O": [(0,0),(1,0),(0,1),(1,1)],
    "T": [(-1,0),(0,0),(1,0),(0,1)],
    "S": [(-1,1),(0,1),(0,0),(1,0)],
    "Z": [(-1,0),(0,0),(0,1),(1,1)],
    "J": [(-1,0),(0,0),(1,0),(-1,1)],
    "L": [(-1,0),(0,0),(1,0),(1,1)]
}

COLORS = {
    0: (20, 20, 20),
    1: (255, 100, 100),
    2: (100, 255, 100),
    3: (100, 100, 255),
    4: (255, 255, 100),
    5: (255, 100, 255),
    6: (100, 255, 255),
    7: (255, 180, 50),
}

COLOR_CODE = {
    "I": 1,
    "O": 2,
    "T": 3,
    "S": 4,
    "Z": 5,
    "J": 6,
    "L": 7
}

def encode_message(data_dict):
    """
    [4-byte length] [JSON body]
    """
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
        pass
    except Exception as e:
        #print(f"\n[{time.strftime('%H:%M:%S')}] protocol error -> {e}")
        return {"status": "error", "errorMsg": "Protocol error receiving response."}


class Tetromino:
    def __init__(self, kind, x, y, orientation=0):
        self.kind = kind
        self.x = x
        self.y = y
        self.orientation = orientation
        self.base = TETROMINO_BASE[kind]

    def get_blocks(self, orientation=None, x=None, y=None):
        if orientation is None:
            orientation = self.orientation
        if x is None:
            x = self.x
        if y is None:
            y = self.y
        coords = self.base
        for _ in range(orientation % 4):
            coords = self.rotate_cw(coords)
        return [(x + cx, y + cy) for (cx, cy) in coords]

    def rotate_cw(self, coords):
        return [(y, -x) for (x, y) in coords]

class AsyncGameClient:
    def __init__(self, ip, port, role, name):
        self.ip = ip
        self.port = port
        self.role = role
        self.name = name
        self.reader = None
        self.writer = None
        self.snapshot = None
        self.connected = False

    async def connect(self):
        #print(f"[INFO] Connecting to {self.ip}:{self.port} ...")
        self.reader, self.writer = await asyncio.open_connection(self.ip, int(self.port))
        self.connected = True
        #print("[INFO] Connected.")

        # 加入遊戲
        join_msg = {
            "action": "join",
            "data": {"role": self.role, "name": self.name}
        }
        await self.send(join_msg)
        #print("[SEND] join", join_msg)

    async def send(self, msg_dict):
        if not self.connected:
            #print("[WARN] Not connected yet.")
            return
        try:
            data = encode_message(msg_dict)
            self.writer.write(data)
            await self.writer.drain()
        except Exception as e:
            #print("[ERROR] send failed:", e)
            pass

    async def listen(self):
        try:
            while True:
                msg = await read_response(self.reader)
                if msg.get("type") == "snapshot":
                    self.snapshot = msg["payload"]
                if msg.get("type") == "game_over":
                    await self.close()
        except Exception as e:
            #print("[WARN] listen loop stopped:", e)
            pass

    async def close(self):
        if self.writer:
            self.writer.close()
            await self.writer.wait_closed()
        self.connected = False


class GameGUI:
    def __init__(self, client: AsyncGameClient):
        pygame.init()
        self.client = client
        self.screen = pygame.display.set_mode(
            (BOARD_WIDTH * CELL_SIZE * 2 + MARGIN * 3, BOARD_HEIGHT * CELL_SIZE + MARGIN * 2)
        )
        pygame.display.set_caption(f"Tetris GUI - {client.role}")
        self.clock = pygame.time.Clock()
        self.font = pygame.font.SysFont("Consolas", 18)

    def draw_board(self, board, x_offset, y_offset):
        for y in range(BOARD_HEIGHT):
            for x in range(BOARD_WIDTH):
                cell = board[y][x]
                color = COLORS.get(cell, (50, 50, 50))
                rect = pygame.Rect(
                    x_offset + x * CELL_SIZE,
                    y_offset + y * CELL_SIZE,
                    CELL_SIZE - 1,
                    CELL_SIZE - 1
                )
                pygame.draw.rect(self.screen, color, rect)

    def render_snapshot(self, snapshot):
        players = snapshot["players"]
        p1 = players.get("p1")
        p2 = players.get("p2")

        self.screen.fill((0, 0, 0))
        self.draw_board(p1["board"], MARGIN, MARGIN)
        self.draw_board(p2["board"], MARGIN * 2 + BOARD_WIDTH * CELL_SIZE, MARGIN)

        for player, offset_x in [(p1, MARGIN), (p2, MARGIN * 2 + BOARD_WIDTH * CELL_SIZE)]:
            piece_info = player.get("current_piece")
            if piece_info:
                tet = Tetromino(piece_info["kind"], piece_info["x"], piece_info["y"], piece_info["orientation"])
                for bx, by in tet.get_blocks():
                    rect = pygame.Rect(
                        offset_x + bx * CELL_SIZE,
                        MARGIN + by * CELL_SIZE,
                        CELL_SIZE - 1,
                        CELL_SIZE - 1
                    )
                    color = COLORS.get(COLOR_CODE[tet.kind], (255, 255, 255))
                    pygame.draw.rect(self.screen, color, rect)

        text1 = self.font.render(f"{p1['name']}  score:{p1['score']}", True, (255, 255, 255))
        text2 = self.font.render(f"{p2['name']}  score:{p2['score']}", True, (255, 255, 255))
        self.screen.blit(text1, (MARGIN, 10))
        self.screen.blit(text2, (MARGIN * 2 + BOARD_WIDTH * CELL_SIZE, 10))

        pygame.display.flip()

    def input_to_action(self, key):
        mapping = {
            pygame.K_LEFT: "Left",
            pygame.K_RIGHT: "Right",
            pygame.K_UP: "RotateCW",
            pygame.K_z: "RotateCCW",
            pygame.K_DOWN: "SoftDrop",
            pygame.K_SPACE: "HardDrop",
            pygame.K_c: "Hold"
        }
        return mapping.get(key, None)

    async def run(self):
        while True:
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    await self.client.close()
                    pygame.quit()
                    return
                elif event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_RETURN:
                        msg = {"action": "start_game", "data": {}}
                        await self.client.send(msg)
                        #print("[SEND] start_game")
                    elif event.key == pygame.K_ESCAPE:
                        sys.exit(0)
                    else:
                        move = self.input_to_action(event.key)
                        if move:
                            msg = {"action": "input", "data": {"move": move, "ts": time.time()}}
                            await self.client.send(msg)

            if self.client.snapshot:
                self.render_snapshot(self.client.snapshot)

            await asyncio.sleep(1 / FPS)


async def main(ip, port, role, name):
    client = AsyncGameClient(ip, port, role, name)
    await client.connect()

    asyncio.create_task(client.listen())

    gui = GameGUI(client)
    await gui.run()


if __name__ == "__main__":
    if len(sys.argv) != 5:
        sys.exit(1)

    try:
        ip, port, role, name = sys.argv[1:]
        asyncio.run(main(ip, port, role, name))
    except:
        pass
