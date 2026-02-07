import math
import os
import json
import subprocess
import queue
import threading
import time
import re
import platform
from mcrcon import MCRcon

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
stop_flag = threading.Event()
RCON_HOST = os.getenv("RCON_HOST", "minecraft")
RCON_PORT = int(os.getenv("RCON_PORT", "25575"))
RCON_PASSWORD = os.getenv("RCON_PASSWORD", "change_me_super_secret")
event_q = []
def load_player_json():
    player_names = {}
    try:
        if os.path.exists("/data/player_names.json"):
            with open("/data/player_names.json", "r") as file:
                player_names = json.load(file)
            print("INFO: Player data loaded.")
        else:
            player_names = {}
    except Exception as e:
        print(f"ERROR: Failed to load player data: {e}")
    
    loaded_players = []
    for player, lives in player_names.items():
        loaded_players.append(Player(player, 0.0, lives)) # add ip eventually 
    
    return loaded_players
    
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

def start_minecraft_server():
    """Starts the Minecraft server and logs its output."""
    # Define the command to start the Minecraft server
    minecraft_command = [
        'java',
        "-Dterminal.jline=false",
        "-Djline.terminal=jline.UnsupportedTerminal",
        "-Dlog4j.skipJansi=true",
        '@user_jvm_args.txt', 
        '@libraries/net/minecraftforge/forge/1.20.1-47.4.13/win_args.txt', 
        'nogui'  # Add nogui argument here if needed
    ]

    # Start the Minecraft server process
    process = subprocess.Popen(
        minecraft_command,
        stdin=subprocess.PIPE,  # Allow programmatic input
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        #universal_newlines=True,
        text=True,
        bufsize=1
    )
    return process

def check_for_death(line):
    if ("<" in line and ">" in line) or "[Server]" in line:
        return
    
    m = DEATH_RE.search(line)
    if m:
        player_name = m.group("player")
        event_q.append(["death", player_name, line])


def check_for_join(line):
    m = JOIN_RE.search(line)
    if m:
        player_name = m.group("player")
        event_q.append(["join", player_name, line])
 
def log_output(process, stop_event, event_q):
    try:
        for line in process.stdout:
            if stop_event.is_set():
                break
            print(line, end="")

            check_for_join(line, event_q)
            check_for_death(line, event_q)

    finally:
        event_q.put(("__shutdown__", None, None))

def update_player_count(player_name, count):
    if os.path.exists("/data/player_names.json"):
        with open("player_names.json", "r") as f:
            try:
                data = json.load(f)
            except json.JSONDecodeError:
                data = {}
    else:
        data = {}
    
    data[player_name] = count
    with open("/data/player_names.json", "w") as f:
        json.dump(data, f, indent=2)

def log_reader():
    proc = subprocess.Popen(
        ['tail', '-F', "/data/logs/latest.log"],
        stdout=subprocess.PIPE,
        text=True,
        bufsize=1
    )
    for line in proc.stdout:
        check_for_death(line)
        check_for_join(line)

            

def run_game():
    # Load json with player data
    current_players = load_player_json()

    # Load server and set death count stats
    theServer = Server(len(current_players), current_players)
    theServer.set_max_death_count()
    theServer.set_cur_death_count()

    threading.Thread(target=log_reader, daemon=True).start()
    while True:
        if event_q:
            [event, player, line] = event_q.pop(0)

            if event == "death":
                send_command(f"say §l§4{player} has fucking died... dumb fuck...§r")
                time.sleep(5)
                for p in theServer.players:
                    if p.name == player:
                        p.add_death()
                        update_player_count(player, p.deaths)
                        theServer.add_death()
                        send_command(f"say Now yall are rocking with §4§l{theServer.currentDeathCount}§r / §4§l{theServer.get_max_death_count()}§r")
                        break
                if theServer.get_death_count() > theServer.get_max_death_count():
                    send_command(f"say you guys fucking lost... gg... lightning strike incoming...")
                    time.sleep(3)
                    send_command(f"say here are some stats, so yall can pick the blame...")
                    for p in theServer.players:
                        send_command(f"say {p.name} died {p.deaths} time(s)")
                        time.sleep(2)
                    
                    send_command("say time to execute log and his friends")
                    time.sleep(3)
                    send_command("execute at @a run summon lightning_bolt ~ ~ ~")
                    time.sleep(1)
                    send_command("say 3...")
                    time.sleep(1)
                    send_command("say 2...")
                    time.sleep(1)
                    send_command("say 1...")
                    time.sleep(1)
                    reset_run()
            elif event == "join":
                print(f"{player} joined")
            
                player_exists = False
                for p in theServer.players:
                    if p.name == player:
                        player_exists = True
                        break
                    
                if not player_exists:
                    update_player_count(player, 0)
                    theServer.add_player(Player(player))
                    send_command(f"say {player} has joined")
                    send_command(f"say The new max Death Count is {theServer.get_max_death_count()}")

def main():
    run_game()
        
    

def send_command(command):
    with MCRcon(RCON_HOST, RCON_PASSWORD, port=RCON_PORT) as rcon:
        response = rcon.command(command)
        print(response)

# def stop_minecraft_server(process):
   
def reset_run():

    send_command("stop")
    time.sleep(5)

    """Deletes the Minecraft world folder to reset the world."""
    world_folder = "/data/world"  # Adjust this if your world folder has a different name or 
    if os.path.exists(world_folder):
        if platform.system() == "Windows":
            os.system(f"rmdir /s /q {world_folder}")
        else:
            os.system(f"rm -rf {world_folder}")
        print(f"INFO: Deleted the '{world_folder}' folder.")
    else:
        print(f"WARNING: '{world_folder}' folder not found, skipping deletion.")

    if os.path.exists("player_names.json"):
        with open("player_names.json", "w") as f:
            json.dump({}, f)
 
    print("INFO: World + player data reset complete")

if __name__ == "__main__":
    main()
