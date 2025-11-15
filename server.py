# server.py
import json
import logging
from typing import Dict, Set
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import PlainTextResponse


# ⬇️ NIEUW: Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()

class PlayerConnection:
    def __init__(self, websocket: WebSocket, player_id: str):
        self.websocket = websocket
        self.player_id = player_id

class MatchRoom:
    def __init__(self, match_id: str):
        self.match_id = match_id
        self.players: Dict[str, PlayerConnection] = {}
        self.ready_players: Set[str] = set()  # ⬅️ NIEUW: Track wie ready is
    
    def is_full(self) -> bool:
        return len(self.players) >= 2
    
    def add_player(self, conn: PlayerConnection):
        self.players[conn.player_id] = conn
    
    def remove_player(self, player_id: str):
        if player_id in self.players:
            del self.players[player_id]
        if player_id in self.ready_players:  # ⬅️ NIEUW
            self.ready_players.remove(player_id)
    
    def mark_ready(self, player_id: str) -> bool:  # ⬅️ NIEUW
        """Markeer speler als ready. Return True als BEIDE spelers ready zijn."""
        self.ready_players.add(player_id)
        return len(self.ready_players) >= 2 and self.is_full()
    
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
    match_id = websocket.query_params.get("match_id")
    player_id = websocket.query_params.get("player_id")

    if not match_id or not player_id:
        await websocket.close(code=4000)
        return

    await websocket.accept()
    logger.info(f"✅ {player_id} connected to {match_id}")

    # Room ophalen of maken
    room = rooms.get(match_id)
    if room is None:
        room = MatchRoom(match_id)
        rooms[match_id] = room
        logger.info(f"🆕 Created room: {match_id}")

    # Als speler opnieuw joint, reset ALLES
    if player_id in room.players:
        logger.info(f"🔄 {player_id} is reconnecting")
        
        # Sluit oude connectie
        old_conn = room.players[player_id]
        try:
            await old_conn.websocket.close()
        except:
            pass
        
        # Verwijder speler
        room.remove_player(player_id)
        
        # RESET ready state voor HELE room
        room.ready_players.clear()
        
        logger.info("🔄 Room reset - all players must send ready again")

    # Als room vol is (2 spelers) EN dit is een nieuwe speler
    if room.is_full() and player_id not in room.players:
        await websocket.send_text(json.dumps({
            "type": "error",
            "message": "Match is full"
        }))
        await websocket.close(code=4001)
        return

    conn = PlayerConnection(websocket, player_id)
    room.add_player(conn)
    logger.info(f"👥 Room {match_id} now has {len(room.players)} player(s)")

    # Laat andere speler weten dat deze joined
    join_message = json.dumps({
        "type": "player_joined",
        "player_id": player_id,
        "match_id": match_id
    })
    for other in room.other_players(player_id):
        await other.websocket.send_text(join_message)

    try:
        while True:
            raw = await websocket.receive_text()
            
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                continue
            
            # Check voor player_ready event
            if isinstance(data, dict) and data.get("type") == "player_ready":
                logger.info(f"🟢 {player_id} is ready")
                is_match_ready = room.mark_ready(player_id)
                
                logger.info(f"📊 Ready: {len(room.ready_players)}/{len(room.players)}")
                
                if is_match_ready:
                    logger.info(f"🎮 MATCH START for {match_id}!")
                    match_start_msg = json.dumps({
                        "type": "match_start",
                        "match_id": match_id
                    })
                    
                    for pid, conn in room.players.items():
                        await conn.websocket.send_text(match_start_msg)
                
                continue
            
            # Voeg match_id/player_id toe
            if isinstance(data, dict):
                data.setdefault("match_id", match_id)
                data.setdefault("from_player_id", player_id)
            
            message = json.dumps(data)
            
            # Stuur door naar andere speler(s)
            for other in room.other_players(player_id):
                await other.websocket.send_text(message)
                
    except WebSocketDisconnect:
        logger.info(f"❌ {player_id} disconnected from {match_id}")
        room.remove_player(player_id)

        # Laat andere speler weten
        leave_message = json.dumps({
            "type": "player_left",
            "player_id": player_id,
            "match_id": match_id
        })
        for other in room.other_players(player_id):
            try:
                await other.websocket.send_text(leave_message)
            except:
                pass

        # ⬇️ CLEANUP: Verwijder lege rooms
        if len(room.players) == 0:
            if match_id in rooms:
                del rooms[match_id]
                logger.info(f"🗑️ Deleted empty room: {match_id}")
