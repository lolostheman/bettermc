import math
import os
import json
import subprocess
import queue
import threading
import time
import re
import platform
import shutil
from mctools import RCONClient

JOIN_RE = re.compile(
    r"(?:^.*?:\s+)?(?P<player>[A-Za-z0-9_]{3,16}) joined the game",
    re.IGNORECASE
)
LEAVE_RE = re.compile(
    r"(?:^.*?:\s+)?(?P<player>[A-Za-z0-9_]{3,16}) left the game",
    re.IGNORECASE
)
DEATH_RE = re.compile(
    r"""
    # Optional Forge / MC log prefix
    (?:^.*?:\s+)?

    # Player name (Minecraft-valid)
    (?P<player>[A-Za-z0-9_]{3,16})\s+

    # Death messages
    (?:
        died |
        drowned |
        experienced\ kinetic\ energy |
        intentional\ game\ design |
        blew\ up |
        blown\ up |
        pummeled |
        killed |
        hit\ the\ ground\ too\ hard |
        fell(?:\ into\ a\ ravine)? |
        left\ the\ confines\ of\ this\ world |
        squished |
        suffocated |
        was\ burnt |
        cactus |
        was\ slain(?:\ by\ .+)? |
        was\ shot(?:\ by\ .+)? |
        burned\ to\ death |
        tried\ to\ swim\ in\ lava |
        got\ melted\ by\ a\ blaze |
        failed\ to\ escape\ the\ Nether |
        fell\ out\ of\ the\ world |
        withered\ away |
        discovered\ the\ void |
        discovered\ the\ floor\ was\ lava |
        was\ doomed\ by\ the\ Wither |
        got\ struck\ by\ lightning |
        was\ pricked\ to\ death |
        got\ stung\ by\ a\ bee |
        was\ stung\ to\ death |
        doomed\ to\ fall |
        starved(?:\ to\ death)? |
        was\ doomed\ by\ a\ witch |
        was\ fireballed |
        was\ blown\ off\ a\ cliff |
        got\ suffocated\ in\ a\ wall |
        impaled |
        squashed |
        went\ up\ in\ flames |
        didn['’]t\ want\ to\ live |
        skewered |
        walked\ into\ fire |
        went\ off\ with\ a\ bang |
        walked\ into\ the\ danger\ zone |
        was\ killed\ by\ magic |
        froze\ to\ death |
        obliterated
    )
    """,
    re.IGNORECASE | re.VERBOSE
)
# {"spathak": 1, "xxtenation": 2, "lolostheman": 1}

RCON_HOST = os.getenv("RCON_HOST", "minecraft")
RCON_PORT = int(os.getenv("RCON_PORT", "25575"))
RCON_PASSWORD = os.getenv("RCON_PASSWORD", "")

PLAYER_JSON_PATH = os.getenv("PLAYER_JSON_PATH", "/data/player_names.json")
LOG_PATH = os.getenv("LOG_PATH", "/data/logs/latest.log")
WORLD_PATH = os.getenv("WORLD_PATH", "/data/world")

def load_player_json():
    try:
        if os.path.exists(PLAYER_JSON_PATH):
            with open(PLAYER_JSON_PATH, "r", encoding="utf-8") as f:
                data = json.load(f) or {}
            print("INFO: Player data loaded.")
        else:
            data = {}
    except Exception as e:
        print(f"ERROR: Failed to load player data: {e}")
        data = {}

    players = []
    for player, deaths in data.items():
        players.append(Player(player, 0.0, deaths))
    return players

def update_player_count(player_name, count):
    try:
        if os.path.exists(PLAYER_JSON_PATH):
            with open(PLAYER_JSON_PATH, "r", encoding="utf-8") as f:
                try:
                    data = json.load(f) or {}
                except json.JSONDecodeError:
                    data = {}
        else:
            data = {}

        data[player_name] = count
        with open(PLAYER_JSON_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        print(f"ERROR: Failed to update {PLAYER_JSON_PATH}: {e}")   

class Player:
    def __init__(self, name, ip = 0.0, cur_deaths = 0): # add ip eventually
        self.name = name
        self.ip = ip
        self.deaths = cur_deaths

    def get_death_count(self):
        return self.deaths

    def add_death(self):
        self.deaths += 1
    
class Server:
    def __init__(self, playerCount = 3, players = None):
        self.playerCount = playerCount
        self.players = players if players is not None else []
        self.maxDeathCount = 3
        self.currentDeathCount = 0
    
    def add_death(self):
        self.currentDeathCount += 1 # need to have logic to detect when game is over
    
    def set_cur_death_count(self):
        for player in self.players:
            self.currentDeathCount += player.get_death_count()

    def set_max_death_count(self):
        self.maxDeathCount = math.floor(len(self.players) * 1.5)

    def get_max_death_count(self):
        return self.maxDeathCount
    
    def get_death_count(self):
        return self.currentDeathCount
    
    def add_player(self, player):
        if any(p.name == player.name for p in self.players):
            print("Existing player re-joined the server")
            return
        self.players.append(player)
        self.set_max_death_count()
        print("New player joined the server")

def rcon_connect():
    while True:
        client = RCONClient(RCON_HOST, port=RCON_PORT)
        try:
            ok = client.login(RCON_PASSWORD)
            if ok:
                print("INFO: RCON connected.")
                return client
            print("WARN: RCON login failed; retrying...")
        except Exception as e:
            print(f"WARN: RCON connect error: {e}")
        time.sleep(3)

def rcon_cmd(client, command):
    try:
        return client.command(command)
    except Exception as e:
        print(f"WARN: RCON command failed ({command}): {e}")
        raise
def tail_file_lines(path):
    """
    Tail a file like `tail -F`:
    - waits for the file to exist
    - follows across truncation/rotation
    """
    print(f"INFO: Tailing log at {path}")
    last_inode = None
    f = None

    while True:
        try:
            if not os.path.exists(path):
                time.sleep(1)
                continue

            st = os.stat(path)
            inode = (st.st_ino, st.st_dev)

            if f is None or inode != last_inode:
                if f:
                    f.close()
                f = open(path, "r", encoding="utf-8", errors="ignore")
                # Start at end (only new events)
                f.seek(0, os.SEEK_END)
                last_inode = inode

            line = f.readline()
            if not line:
                time.sleep(0.2)
                continue

            yield line.rstrip("\n")

        except Exception as e:
            print(f"WARN: tail error: {e}")
            try:
                if f:
                    f.close()
            except Exception:
                pass
            f = None
            time.sleep(1)

def reset_world_and_players(client):
    # tell server to stop
    print("INFO: Stopping server via RCON...")
    try:
        rcon_cmd(client, "say Server stopping for world reset...")
        time.sleep(1)
        rcon_cmd(client, "stop")
    except Exception:
        pass

    # wait for the server to actually stop (log will go quiet; we just sleep a bit)
    time.sleep(8)

    # delete world folder
    if os.path.exists(WORLD_PATH):
        print(f"INFO: Deleting world folder: {WORLD_PATH}")
        shutil.rmtree(WORLD_PATH, ignore_errors=True)
    else:
        print(f"WARN: World folder not found at {WORLD_PATH}")

    # reset json
    try:
        with open(PLAYER_JSON_PATH, "w", encoding="utf-8") as f:
            json.dump({}, f)
        print("INFO: Reset player_names.json")
    except Exception as e:
        print(f"ERROR: Failed resetting player json: {e}")

def run_game():
    current_players = load_player_json()
    theServer = Server(len(current_players), players=current_players)
    theServer.set_max_death_count()
    theServer.set_cur_death_count()

    client = rcon_connect()

    for line in tail_file_lines(LOG_PATH):
        # JOIN
        m = JOIN_RE.search(line)
        if m:
            player = m.group("player")
            print(f"{player} joined")

            if not any(p.name == player for p in theServer.players):
                update_player_count(player, 0)
                theServer.add_player(Player(player))
                rcon_cmd(client, f"say {player} has joined")
                rcon_cmd(client, f"say The new max Death Count is {theServer.get_max_death_count()}")
            continue

        # DEATH
        if ("<" in line and ">" in line) or "[Server]" in line:
            continue

        d = DEATH_RE.search(line)
        if d:
            player = d.group("player")

            
            rcon_cmd(client, f"say §l§4{player} has fucking died... dumb fuck...§r")
            time.sleep(2)

            for p in theServer.players:
                if p.name == player:
                    p.add_death()
                    update_player_count(player, p.deaths)
                    theServer.add_death()
                    rcon_cmd(client, f"say Now yall are rocking with §4§l{theServer.currentDeathCount}§r / §4§l{theServer.get_max_death_count()}§r")
                    break

            if theServer.get_death_count() > theServer.get_max_death_count():
                rcon_cmd(client, "say you guys fucking lost... gg... lightning strike incoming...")
                time.sleep(2)
                rcon_cmd(client, "say here are some stats, so yall can pick the blame...")

                for p in theServer.players:
                    rcon_cmd(client, f"say {p.name} died {p.deaths} time(s)")
                    time.sleep(1)

                rcon_cmd(client, "say time to execute log and his friends")
                time.sleep(2)
                rcon_cmd(client, "execute at @a run summon lightning_bolt ~ ~ ~")

                reset_world_and_players(client)
                # after stop/reset, exit and let container restart loop re-run
                return
        

def main():
    while True:
        run_game()
        print("INFO: Restarting in 5 seconds....")
        time.sleep(5)

if __name__ == "__main__":
    main()
