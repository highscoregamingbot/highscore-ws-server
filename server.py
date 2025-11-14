# server.py
import json
from typing import Dict, Set
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import PlainTextResponse

app = FastAPI()

class PlayerConnection:
    def __init__(self, websocket: WebSocket, player_id: str):
        self.websocket = websocket
        self.player_id = player_id

class MatchRoom:
    def __init__(self, match_id: str):
        self.match_id = match_id
        self.players: Dict[str, PlayerConnection] = {}  # player_id -> PlayerConnection

    def is_full(self) -> bool:
        return len(self.players) >= 2

    def add_player(self, conn: PlayerConnection):
        self.players[conn.player_id] = conn

    def remove_player(self, player_id: str):
        if player_id in self.players:
            del self.players[player_id]

    def other_players(self, player_id: str):
        for pid, conn in self.players.items():
            if pid != player_id:
                yield conn

# Alle rooms in memory
rooms: Dict[str, MatchRoom] = {}


@app.get("/health")
async def health():
    return PlainTextResponse("OK", status_code=200)


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    # Query params uitlezen
    match_id = websocket.query_params.get("match_id")
    player_id = websocket.query_params.get("player_id")

    if not match_id or not player_id:
        await websocket.close(code=4000)
        return

    await websocket.accept()

    # Room ophalen of maken
    room = rooms.get(match_id)
    if room is None:
        room = MatchRoom(match_id)
        rooms[match_id] = room

    # Als room vol is -> disconnect
    if room.is_full() and player_id not in room.players:
        await websocket.send_text(json.dumps({
            "type": "error",
            "message": "Match is full"
        }))
        await websocket.close(code=4001)
        return

    conn = PlayerConnection(websocket, player_id)
    room.add_player(conn)

    # Laat andere speler weten dat deze joined
    join_message = json.dumps({
        "type": "player_joined",
        "player_id": player_id,
        "match_id": match_id
    })
    for other in room.other_players(player_id):
        await other.websocket.send_text(join_message)

    try:
        # Hoofdlus: berichten ontvangen en doorsturen
        while True:
            raw = await websocket.receive_text()
            # We gaan er vanuit dat client JSON stuurt
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                # stuur niks door als het geen geldige JSON is
                continue

            # Voeg eventueel match_id/player_id toe
            if isinstance(data, dict):
                data.setdefault("match_id", match_id)
                data.setdefault("from_player_id", player_id)

            message = json.dumps(data)

            # Stuur door naar andere speler(s) in dezelfde room
            for other in room.other_players(player_id):
                await other.websocket.send_text(message)

    except WebSocketDisconnect:
        # Speler is weg
        room.remove_player(player_id)

        # Laat andere speler weten dat hij weg is
        leave_message = json.dumps({
            "type": "player_left",
            "player_id": player_id,
            "match_id": match_id
        })
        for other in room.other_players(player_id):
            await other.websocket.send_text(leave_message)

        # Ruim lege room op
        if len(room.players) == 0:
            del rooms[match_id]
