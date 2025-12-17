import asyncio
import json
import struct
import sys
import time
import random

ENCODING = "utf-8"
HEADER_SIZE = 4
NETWORK_BYTE_ORDER = "!I"

BOARD_W = 10
BOARD_H = 20
VISIBLE_TOP = 0  # top visible row index
SPAWN_X = 4      # spawn x
SPAWN_Y = -1     # spawn y

GRAVITY = 0.8
SNAPSHOT_INTERVAL = 0.2
LOCK_DELAY = 0.4


def encode_message(data):
    b = json.dumps(data, ensure_ascii=False).encode(ENCODING)
    return struct.pack(NETWORK_BYTE_ORDER, len(b)) + b

async def read_message(reader):
    header = await reader.readexactly(HEADER_SIZE)
    length = struct.unpack(NETWORK_BYTE_ORDER, header)[0]
    body = await reader.readexactly(length)
    return json.loads(body.decode(ENCODING))


TETROMINO_BASE = {
    # spawn orientation chosen to match common conventions (approx)
    "I": [(-2,0),(-1,0),(0,0),(1,0)],
    "O": [(0,0),(1,0),(0,1),(1,1)],
    "T": [(-1,0),(0,0),(1,0),(0,1)],
    "S": [(-1,1),(0,1),(0,0),(1,0)],
    "Z": [(-1,0),(0,0),(0,1),(1,1)],
    "J": [(-1,0),(0,0),(1,0),(-1,1)],
    "L": [(-1,0),(0,0),(1,0),(1,1)]
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

# Rotation (90 clockwise): (x,y) -> (y, -x)
def rotate_cw(coords):
    return [(y, -x) for (x,y) in coords]

def rotate_ccw(coords):
    return [(-y, x) for (x,y) in coords]

# SRS wall-kick data (from standard SRS):
# For J/L/S/Z/T (called "JLSTZ") kicks between rotation states
# Kick tests are ordered pairs of (x,y) offsets to apply when rotating (from -> to).
JLSTZ_KICKS = {
    (0,1): [(0,0), (-1,0), (-1,1), (0,-2), (-1,-2)],
    (1,0): [(0,0), (1,0), (1,-1), (0,2), (1,2)],
    (1,2): [(0,0), (1,0), (1,-1), (0,2), (1,2)],
    (2,1): [(0,0), (-1,0), (-1,1), (0,-2), (-1,-2)],
    (2,3): [(0,0), (1,0), (1,1), (0,-2), (1,-2)],
    (3,2): [(0,0), (-1,0), (-1,-1), (0,2), (-1,2)],
    (3,0): [(0,0), (-1,0), (-1,-1), (0,2), (-1,2)],
    (0,3): [(0,0), (1,0), (1,1), (0,-2), (1,-2)],
}

# I-piece kicks (different)
I_KICKS = {
    (0,1): [(0,0), (-2,0), (1,0), (-2,-1), (1,2)],
    (1,0): [(0,0), (2,0), (-1,0), (2,1), (-1,-2)],
    (1,2): [(0,0), (-1,0), (2,0), (-1,2), (2,-1)],
    (2,1): [(0,0), (1,0), (-2,0), (1,-2), (-2,1)],
    (2,3): [(0,0), (2,0), (-1,0), (2,1), (-1,-2)],
    (3,2): [(0,0), (-2,0), (1,0), (-2,-1), (1,2)],
    (3,0): [(0,0), (1,0), (-2,0), (1,-2), (-2,1)],
    (0,3): [(0,0), (-1,0), (2,0), (-1,2), (2,-1)],
}

# rotation states: 0,1,2,3 (clockwise)
def get_kicks(piece_type, from_state, to_state):
    key = (from_state, to_state)
    if piece_type == "I":
        return I_KICKS.get(key, [(0,0)])
    elif piece_type == "O":
        return [(0,0)]
    else:
        return JLSTZ_KICKS.get(key, [(0,0)])


class BagGenerator:
    def __init__(self, seed):
        self.rng = random.Random(seed)
        self.queue = []
        self._refill()

    def _refill(self):
        bag = list(TETROMINO_BASE.keys())
        # Fisher-Yates shuffle
        for i in range(len(bag)-1, 0, -1):
            j = self.rng.randint(0, i)
            bag[i], bag[j] = bag[j], bag[i]
        self.queue.extend(bag)

    def next(self):
        if not self.queue:
            self._refill()
        return self.queue.pop(0)


class Piece:
    def __init__(self, kind, x, y, orientation=0):
        self.kind = kind
        self.x = x
        self.y = y
        self.orientation = orientation  # 0..3
        # base coords are rotation 0
        self.base = TETROMINO_BASE[kind]

    def get_blocks(self, orientation=None, x=None, y=None):
        if orientation is None:
            orientation = self.orientation
        if x is None:
            x = self.x
        if y is None:
            y = self.y
        coords = self.base
        # rotate orientation times cw
        for _ in range(orientation % 4):
            coords = rotate_cw(coords)
        # translate
        return [(x + cx, y + cy) for (cx, cy) in coords]

class PlayerState:
    def __init__(self, role, name, seed_bag):
        self.role = role
        self.name = name
        self.board = [[0]*BOARD_W for _ in range(BOARD_H)]
        self.score = 0
        self.lines = 0
        self.alive = True

        self.baggen = seed_bag  # shared bag generator reference
        self.next_queue = []
        self.fill_next()
        self.current = None
        self.spawn_new()
        self.hold = None
        self.hold_used = False

        self.lock_timer = None  # timestamp when piece contacted ground
        self.last_move_ts = time.time()

    def fill_next(self):
        while len(self.next_queue) < 7:
            self.next_queue.append(self.baggen.next())

    def spawn_new(self):
        self.fill_next()
        kind = self.next_queue.pop(0)
        self.current = Piece(kind, SPAWN_X, SPAWN_Y, 0)
        self.hold_used = False
        # if spawn collides immediately, player is top-out
    def to_snapshot(self):
        # provide reduced board and meta for clients
        return {
            "role": self.role,
            "name": self.name,
            "board": self.board,
            "score": self.score,
            "lines": self.lines,
            "alive": self.alive,
            "current_piece": {"kind": self.current.kind if self.current else None,
                              "x": self.current.x if self.current else None,
                              "y": self.current.y if self.current else None,
                              "orientation": self.current.orientation if self.current else None},
            "next": self.next_queue[:5],
            "hold": self.hold
        }



def in_bounds(x,y):
    return 0 <= x < BOARD_W and y < BOARD_H  # y can be <0 (above visible)

def collides(board, blocks):
    for (x,y) in blocks:
        if y >= 0 and (not in_bounds(x,y) or board[y][x]):
            return True
    return False

def place_on_board(board, blocks, kind):
    for (x,y) in blocks:
        if 0 <= y < BOARD_H and 0 <= x < BOARD_W:
            board[y][x] = COLOR_CODE[kind]

def clear_lines(board):
    cleared = 0
    new_board = []
    for row in board:
        if all(row):
            cleared += 1
        else:
            new_board.append(row)
    for _ in range(cleared):
        new_board.insert(0, [0]*BOARD_W)
    return new_board, cleared


def score_for_clear(lines_cleared):
    if lines_cleared == 0:
        return 0
    # basic:
    table = {1:100, 2:300, 3:500, 4:800}
    return table.get(lines_cleared, lines_cleared*200)


class GameServer:
    def __init__(self, host, port, room_id, seed=None):
        self.host = host
        self.port = port
        self.room_id = room_id
        self.seed = seed if seed is not None else int(time.time())
        self.baggen = BagGenerator(self.seed)
        self.players = {}  # role -> PlayerState
        self.spectators = set()
        self.clients = {}  # writer -> (role,name)
        self.lock = asyncio.Lock()
        self.running = False
        self._server = None

    async def start(self):
        self._server = await asyncio.start_server(self._on_connect, self.host, self.port)
        # print(f"[Game:{self.room_id}] Listening on 127.0.0.1:{self.port} seed={self.seed}")

    async def _on_connect(self, reader, writer):
        addr = writer.get_extra_info('peername')
        # print(f"[Game:{self.room_id}] connection from {addr}")
        # initial expect join msg
        try:
            msg = await read_message(reader)
        except Exception:
            writer.close()
            await writer.wait_closed()
            return
        action = msg.get("action")
        data = msg.get("data", {})
        if action != "join":
            writer.write(encode_message({"status":"error","msg":"must join first"}))
            await writer.drain()
            writer.close()
            await writer.wait_closed()
            return
        role = data.get("role","spectator")
        name = data.get("name", str(addr))
        token = data.get("token")  # optional

        # register writer
        self.clients[writer] = (role,name)
        if role in ("p1","p2"):
            # create player state if not present
            if role in self.players:
                # slot occupied -> reject
                writer.write(encode_message({"status":"error","msg":"slot taken"}))
                await writer.drain()
                del self.clients[writer]
                writer.close()
                await writer.wait_closed()
                return
            player = PlayerState(role, name, self.baggen)
            # spawn may collide => check topout
            if collides(player.board, player.current.get_blocks()):
                player.alive = False
            self.players[role] = player
            # print(f"[Game:{self.room_id}] player {name} joined as {role}")
        else:
            self.spectators.add(writer)
            # print(f"[Game:{self.room_id}] spectator {name} joined")

        # send game meta (seed + bag rule)
        writer.write(encode_message({"type":"game_meta","seed":self.seed,"bagRule":"7-bag-FisherYates","gravity":GRAVITY}))
        await writer.drain()

        # start client read loop
        asyncio.create_task(self._client_loop(reader, writer))

    async def _client_loop(self, reader, writer):
        try:
            while True:
                msg = await read_message(reader)
                await self.process_client_message(msg, writer)
        except asyncio.IncompleteReadError:
            pass
        except Exception as e:
            # print(f"[Game:{self.room_id}] client loop error: {e}")
            pass
        finally:
            # cleanup
            info = self.clients.pop(writer, None)
            if info:
                role,name = info
                if role in ("p1","p2") and role in self.players:
                    # mark player as disconnected but not necessarily dead - here we'll mark them dead
                    # print(f"[Game:{self.room_id}] player {name} disconnected, top-out/forfeit.")
                    self.players[role].alive = False

                elif writer in self.spectators:
                    self.spectators.remove(writer)
            try:
                writer.close()
                await writer.wait_closed()
            except:
                pass

    async def process_client_message(self, msg, writer):
        action = msg.get("action")
        data = msg.get("data", {})
        if action == "start_game" and len(self.players.items()) == 2:
            await self.handle_start()
        elif action == "input":
            await self.handle_input(writer, data)
        elif action == "request_snapshot":
            snap = await self.make_snapshot()
            writer.write(encode_message({"type":"snapshot","payload":snap}))
            await writer.drain()
        else:
            # unknown action: ignore or reply
            writer.write(encode_message({"status":"error","msg":"unknown action"}))
            await writer.drain()

    async def handle_start(self):
        async with self.lock:
            if self.running:
                return
            self.running = True
            # ensure two players exist (p1 & p2) or allow 1-vs-bot? we'll allow starting with what we have
            # print(f"[Game:{self.room_id}] game started")
            # start loops
            asyncio.create_task(self.gravity_loop())
            asyncio.create_task(self.snapshot_loop())

            # broadcast start
            await self.broadcast({"type":"game_start","seed":self.seed,"bagRule":"7-bag-FisherYates"})

    async def handle_input(self, writer, data):
        info = self.clients.get(writer)
        if not info:
            return
        role,name = info
        if role not in self.players:
            return
        player = self.players[role]
        if not player.alive:
            return

        # example input format: {"move":"Left"} or {"move":"HardDrop"}
        move = data.get("move")
        ts = data.get("ts", time.time())
        async with self.lock:
            player.last_move_ts = ts
            # process moves
            if move == "Left":
                await self._try_move(player, dx=-1)
            elif move == "Right":
                await self._try_move(player, dx=1)
            elif move == "RotateCW":
                await self._try_rotate(player, clockwise=True)
            elif move == "RotateCCW":
                await self._try_rotate(player, clockwise=False)
            elif move == "SoftDrop":
                await self._soft_drop(player)
            elif move == "HardDrop":
                await self._hard_drop(player)
            elif move == "Hold":
                await self._do_hold(player)
            # after handling input, optionally send small update to all
            #await self.broadcast_player_update(player)

    # Move/rotation logic with SRS kicks
    async def _try_move(self, player, dx):
        if not player.current:
            return
        new_x = player.current.x + dx
        new_blocks = player.current.get_blocks(x=new_x, y=player.current.y)
        if not collides(player.board, new_blocks):
            player.current.x = new_x
            player.lock_timer = None  # moving resets lock timer typically
            return True
        return False

    async def _try_rotate(self, player, clockwise=True):
        if not player.current:
            return
        old_o = player.current.orientation
        new_o = (old_o + (1 if clockwise else -1)) % 4
        piece_type = player.current.kind
        kicks = get_kicks(piece_type, old_o, new_o)
        # compute rotated blocks at origin (0,0) and try kicks
        for (kx, ky) in kicks:
            nx = player.current.x + kx
            ny = player.current.y + ky
            blocks = player.current.get_blocks(orientation=new_o, x=nx, y=ny)
            if not collides(player.board, blocks):
                # apply
                player.current.orientation = new_o
                player.current.x = nx
                player.current.y = ny
                player.lock_timer = None
                return True
        return False

    async def _soft_drop(self, player):
        if not player.current:
            return
        # try move down one
        nx = player.current.x
        ny = player.current.y + 1
        blocks = player.current.get_blocks(x=nx,y=ny)
        if not collides(player.board, blocks):
            player.current.y = ny
            # soft drop adds small score
            player.score += 1
            player.lock_timer = None
            return True
        else:
            # contacting the ground starts lock timer
            if player.lock_timer is None:
                player.lock_timer = time.time()
            return False

    async def _hard_drop(self, player):
        if not player.current:
            return
        # drop until collision would occur; place at last valid y
        while True:
            ny = player.current.y + 1
            blocks = player.current.get_blocks(x=player.current.x, y=ny)
            if collides(player.board, blocks):
                break
            player.current.y = ny
        # lock immediately
        await self._lock_piece(player, hard=True)
        # score for harddrop distance unknown here; we added basic
        return True

    async def _do_hold(self, player):
        if player.hold_used:
            return False
        if player.hold is None:
            player.hold = player.current.kind
            player.spawn_new()
        else:
            # swap
            tmp = player.hold
            player.hold = player.current.kind
            # spawn tmp as current (but maintain orientation and position)
            player.current = Piece(tmp, SPAWN_X, SPAWN_Y, 0)
            # if spawn collides -> topout
            if collides(player.board, player.current.get_blocks()):
                player.alive = False
        player.hold_used = True
        return True

    async def _lock_piece(self, player, hard=False):
        # place current onto board
        blocks = player.current.get_blocks()
        kind = player.current.kind
        place_on_board(player.board, blocks, kind)
        # clear lines
        player.board, cleared = clear_lines(player.board)
        if cleared:
            player.lines += cleared
            player.score += score_for_clear(cleared)
        # spawn next piece
        player.spawn_new()
        # check spawn collision -> topout
        if collides(player.board, player.current.get_blocks()):
            player.alive = False
        # reset lock timer
        player.lock_timer = None
        # if hard drop, optionally add points (simplified)
        if hard:
            player.score += 10

    async def gravity_loop(self):
        # gravity tick every GRAVITY seconds
        try:
            while self.running:
                await asyncio.sleep(GRAVITY)
                async with self.lock:
                    # for each player, try move down or manage lock timer
                    for role, player in list(self.players.items()):
                        if not player.alive:
                            continue
                        # attempt drop
                        blocks_below = player.current.get_blocks(x=player.current.x, y=player.current.y+1)
                        if not collides(player.board, blocks_below):
                            player.current.y += 1
                            player.lock_timer = None
                        else:
                            # start or check lock timer
                            if player.lock_timer is None:
                                player.lock_timer = time.time()
                            else:
                                if time.time() - player.lock_timer >= LOCK_DELAY:
                                    await self._lock_piece(player)
                        # after processing, check topout -> handled in spawn_new
                    # check end condition
                    alive = [p for p in self.players.values() if p.alive]
                    if len(alive) <= 1:
                        self.running = False
                        winner = alive[0].name if alive else None
                        result = {"type":"game_over","winner":winner}
                        await self.broadcast(result)
                        # print(f"[Game:{self.room_id}] game over winner={winner}")
                        json_output = json.dumps(result)
                        print(json_output)
                        sys.stdout.flush()
                        sys.exit(0)
                        
                    # else minimal state update
                    await self.broadcast_minimal()
        except asyncio.CancelledError:
            pass

    async def snapshot_loop(self):
        try:
            while self.running:
                await asyncio.sleep(SNAPSHOT_INTERVAL)
                snap = await self.make_snapshot()
                await self.broadcast({"type":"snapshot","payload":snap})
        except asyncio.CancelledError:
            pass

    async def make_snapshot(self):
        # build snapshot dictionary
        snap_players = {}
        for role, p in self.players.items():
            snap_players[role] = p.to_snapshot()
        return {"room": self.room_id, "ts": int(time.time()*1000), "players": snap_players}

    async def broadcast(self, data):
        b = encode_message(data)
        to_remove = []
        for w in list(self.clients.keys()):
            try:
                w.write(b)
                await w.drain()
            except Exception:
                to_remove.append(w)
        for w in to_remove:
            self.clients.pop(w, None)
            if w in self.spectators:
                self.spectators.remove(w)

    async def broadcast_minimal(self):
        # minimal state_update with current piece positions and scores
        payload = {}
        for role,p in self.players.items():
            payload[role] = {
                "current_piece": {"kind": p.current.kind, "x": p.current.x, "y": p.current.y, "orientation": p.current.orientation},
                "score": p.score,
                "lines": p.lines,
                "alive": p.alive
            }
        await self.broadcast({"type":"state_update","payload":payload,"ts":int(time.time()*1000)})



async def main():
    if len(sys.argv) < 4:
        #print("Usage: python game_server_full.py <host> <port> <room_id> [seed]")
        return
    host = sys.argv[1]
    port = int(sys.argv[2])
    room = sys.argv[3]
    seed = int(sys.argv[4]) if len(sys.argv) >= 5 else None
    gs = GameServer(host, port, room, seed)
    await gs.start()
    async with gs._server:
        await gs._server.serve_forever()

if __name__ == "__main__":
    asyncio.run(main())
