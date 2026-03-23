import requests
import tkinter as tk
from tkinter import ttk
import threading

# --- Cache for ESI lookups ---
_type_cache: dict[int, dict] = {}
_type_is_weapon_cache: dict[int, bool] = {}
_market_group_cache: dict[int, dict] = {}

# Correct killmail item flags
# Low slots:  11–18
# Med slots:  19–26
# High slots: 27–34
HIGH_SLOT_FLAGS = set(range(27, 35))

# Drone bay flag
DRONE_BAY_FLAG = 87

# Dogma effect IDs that mark a module as a weapon
WEAPON_EFFECT_IDS = {10, 21, 42}

# Market group IDs known to be weapon-related roots
WEAPON_MARKET_GROUP_ROOTS = {10, 639, 640, 641, 642, 643, 644, 645, 656, 657}


# --- EVE / zKillboard API helpers ---

def get_character_id(name: str) -> int | None:
    url = "https://esi.evetech.net/latest/universe/ids/"
    resp = requests.post(url, json=[name], params={"datasource": "tranquility"})
    resp.raise_for_status()
    data = resp.json()
    chars = data.get("characters", [])
    if not chars:
        return None
    return chars[0]["id"]


def get_type_detail(type_id: int) -> dict:
    if type_id in _type_cache:
        return _type_cache[type_id]
    url = f"https://esi.evetech.net/latest/universe/types/{type_id}/"
    resp = requests.get(url, params={"datasource": "tranquility", "language": "en"})
    resp.raise_for_status()
    data = resp.json()
    _type_cache[type_id] = data
    return data


def get_type_name(type_id: int) -> str:
    return get_type_detail(type_id).get("name", "Unknown")


def get_market_group(mg_id: int) -> dict:
    if mg_id in _market_group_cache:
        return _market_group_cache[mg_id]
    try:
        url = f"https://esi.evetech.net/latest/markets/groups/{mg_id}/"
        resp = requests.get(url, params={"datasource": "tranquility"})
        resp.raise_for_status()
        data = resp.json()
        _market_group_cache[mg_id] = data
        return data
    except Exception:
        return {}


def is_weapon_type(type_id: int) -> bool:
    if type_id in _type_is_weapon_cache:
        return _type_is_weapon_cache[type_id]

    data = get_type_detail(type_id)
    result = False

    # Check 1: dogma effects
    dogma_effects = data.get("dogma_effects", [])
    for eff in dogma_effects:
        if eff.get("effect_id") in WEAPON_EFFECT_IDS:
            result = True
            break

    # Check 2: market group tree walk
    if not result:
        mg_id = data.get("market_group_id")
        visited = set()
        while mg_id and mg_id not in visited:
            if mg_id in WEAPON_MARKET_GROUP_ROOTS:
                result = True
                break
            visited.add(mg_id)
            mg_data = get_market_group(mg_id)
            mg_id = mg_data.get("parent_group_id")

    # Check 3: name-based fallback
    if not result:
        name_lower = data.get("name", "").lower()
        weapon_keywords = [
            "autocannon", "artillery", "howitzer", "repeating",
            "blaster", "railgun", "neutron", "electron", "ion",
            "pulse laser", "beam laser", "modal", "mega", "dual light",
            "gatling", "scout", "anode",
            "torpedo", "missile launcher", "rocket launcher",
            "cruise", "rapid light", "rapid heavy", "assault missile",
            "heavy missile", "light missile",
            "vorton", "disintegrator",
            "smartbomb",
        ]
        for kw in weapon_keywords:
            if kw in name_lower:
                result = True
                break

    _type_is_weapon_cache[type_id] = result
    return result


def fetch_solo_kills(character_id: int, page: int = 1) -> list[dict]:
    url = f"https://zkillboard.com/api/solo/kills/characterID/{character_id}/page/{page}/"
    headers = {
        "User-Agent": "EVE-Solo-Lookup/1.0 (contact: your_email@example.com)",
        "Accept": "application/json",
    }
    resp = requests.get(url, headers=headers)
    resp.raise_for_status()
    return resp.json()


def fetch_solo_losses(character_id: int, page: int = 1) -> list[dict]:
    url = f"https://zkillboard.com/api/solo/losses/characterID/{character_id}/page/{page}/"
    headers = {
        "User-Agent": "EVE-Solo-Lookup/1.0 (contact: your_email@example.com)",
        "Accept": "application/json",
    }
    resp = requests.get(url, headers=headers)
    resp.raise_for_status()
    return resp.json()


def get_killmail_detail(killmail_id: int, killmail_hash: str) -> dict:
    url = f"https://esi.evetech.net/latest/killmails/{killmail_id}/{killmail_hash}/"
    resp = requests.get(url, params={"datasource": "tranquility"})
    resp.raise_for_status()
    return resp.json()


def find_victim_weapons(km_detail: dict) -> list[str]:
    """
    Scan the victim's fitted items for all weapons in high slots.
    Falls back to checking drone bay. Returns list of unique weapon names.
    """
    items = km_detail.get("victim", {}).get("items", [])
    weapons = []
    seen = set()

    # Check high slots (flags 27–34) for weapons
    for item in items:
        flag = item.get("flag", 0)
        if flag in HIGH_SLOT_FLAGS:
            type_id = item.get("item_type_id")
            if type_id and type_id not in seen and is_weapon_type(type_id):
                seen.add(type_id)
                weapons.append(get_type_name(type_id))

    # Also check drone bay
    for item in items:
        flag = item.get("flag", 0)
        if flag == DRONE_BAY_FLAG:
            type_id = item.get("item_type_id")
            if type_id and type_id not in seen:
                seen.add(type_id)
                weapons.append(f"{get_type_name(type_id)} (drone)")

    return weapons if weapons else ["None found"]


def extract_kill_info(character_id: int, km_detail: dict, is_kill: bool) -> dict:
    result = {"ship": "Unknown", "weapons": ["Unknown"]}

    if is_kill:
        for attacker in km_detail.get("attackers", []):
            if attacker.get("character_id") == character_id:
                ship_id = attacker.get("ship_type_id")
                weapon_id = attacker.get("weapon_type_id")
                if ship_id:
                    result["ship"] = get_type_name(ship_id)
                if weapon_id:
                    result["weapons"] = [get_type_name(weapon_id)]
                break
    else:
        victim = km_detail.get("victim", {})
        ship_id = victim.get("ship_type_id")
        if ship_id:
            result["ship"] = get_type_name(ship_id)
        result["weapons"] = find_victim_weapons(km_detail)

    return result

def lookup_pilot(name: str) -> dict:
    char_id = get_character_id(name)
    if char_id is None:
        raise ValueError(f"Character '{name}' not found.")

    result = {
        "character_id": char_id,
        "solo_kill": None,
        "solo_loss": None,
    }

    # Last solo kill
    zkill_kills = fetch_solo_kills(char_id, page=1)
    if zkill_kills:
        entry = zkill_kills[0]
        km_id = entry["killmail_id"]
        km_hash = entry.get("zkb", {}).get("hash", "")
        if km_hash:
            detail = get_killmail_detail(km_id, km_hash)
            info = extract_kill_info(char_id, detail, is_kill=True)
            result["solo_kill"] = {
                "killmail_id": km_id,
                "ship": info["ship"],
                "weapons": info["weapons"],
                "datetime": detail.get("killmail_time", "N/A"),
            }

    # Last solo loss
    zkill_losses = fetch_solo_losses(char_id, page=1)
    if zkill_losses:
        entry = zkill_losses[0]
        km_id = entry["killmail_id"]
        km_hash = entry.get("zkb", {}).get("hash", "")
        if km_hash:
            detail = get_killmail_detail(km_id, km_hash)
            info = extract_kill_info(char_id, detail, is_kill=False)
            result["solo_loss"] = {
                "killmail_id": km_id,
                "ship": info["ship"],
                "weapons": info["weapons"],
                "datetime": detail.get("killmail_time", "N/A"),
            }

    return result


# --- GUI (always-on-top overlay) ---

class OverlayApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("EVE Solo")
        self.root.attributes("-topmost", True)
        self.root.geometry("380x320")
        self.root.configure(bg="#1a1a2e")
        self.root.resizable(True, True)
        self.root.minsize(300, 250)

        try:
            self.root.attributes("-alpha", 0.92)
        except tk.TclError:
            pass

        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TLabel", background="#1a1a2e", foreground="#e0e0e0",
                         font=("Consolas", 8))
        style.configure("Header.TLabel", font=("Consolas", 9, "bold"),
                         foreground="#00d4ff")
        style.configure("TEntry", fieldbackground="#16213e", foreground="#e0e0e0",
                         font=("Consolas", 8))
        style.configure("TButton", background="#0f3460", foreground="#e0e0e0",
                         font=("Consolas", 8, "bold"))
        style.configure("TFrame", background="#1a1a2e")

        # Input frame
        input_frame = ttk.Frame(root)
        input_frame.pack(padx=6, pady=(6, 3), fill="x")

        ttk.Label(input_frame, text="Pilot:").pack(side="left", padx=(0, 3))
        self.name_var = tk.StringVar()
        self.entry = ttk.Entry(input_frame, textvariable=self.name_var, width=24)
        self.entry.pack(side="left", padx=(0, 3), fill="x", expand=True)
        self.entry.bind("<Return>", lambda e: self.do_lookup())

        self.paste_btn = ttk.Button(input_frame, text="📋", width=3,
                                     command=self.paste_from_clipboard)
        self.paste_btn.pack(side="left", padx=(0, 3))

        self.btn = ttk.Button(input_frame, text="Lookup", command=self.do_lookup)
        self.btn.pack(side="left")

        # Results frame
        self.results_frame = ttk.Frame(root)
        self.results_frame.pack(padx=6, pady=6, fill="both", expand=True)

        self.status_var = tk.StringVar(value="Enter a pilot name and press Lookup.")
        self.status_label = ttk.Label(self.results_frame, textvariable=self.status_var,
                                       wraplength=360)
        self.status_label.pack(anchor="w")

        self.kill_header = ttk.Label(self.results_frame, text="", style="Header.TLabel")
        self.kill_header.pack(anchor="w", pady=(6, 0))
        self.kill_info = ttk.Label(self.results_frame, text="", wraplength=360,
                                    justify="left")
        self.kill_info.pack(anchor="w", padx=(8, 0))

        self.loss_header = ttk.Label(self.results_frame, text="", style="Header.TLabel")
        self.loss_header.pack(anchor="w", pady=(6, 0))
        self.loss_info = ttk.Label(self.results_frame, text="", wraplength=360,
                                    justify="left")
        self.loss_info.pack(anchor="w", padx=(8, 0))

    def paste_from_clipboard(self):
        try:
            text = self.root.clipboard_get().strip()
            if text:
                self.name_var.set(text)
                self.entry.icursor(tk.END)
        except tk.TclError:
            pass

    def do_lookup(self):
        name = self.name_var.get().strip()
        if not name:
            return
        self.btn.config(state="disabled")
        self.status_var.set(f"Looking up '{name}'...")
        self.kill_header.config(text="")
        self.kill_info.config(text="")
        self.loss_header.config(text="")
        self.loss_info.config(text="")

        thread = threading.Thread(target=self._lookup_thread, args=(name,), daemon=True)
        thread.start()

    def _lookup_thread(self, name: str):
        try:
            data = lookup_pilot(name)
            self.root.after(0, self._display_results, name, data)
        except Exception as e:
            self.root.after(0, self._display_error, str(e))

    def _display_results(self, name: str, data: dict):
        self.btn.config(state="normal")
        self.status_var.set(f"Results for: {name}  (ID: {data['character_id']})")

        kill = data.get("solo_kill")
        if kill:
            weapons_str = "\n              ".join(kill["weapons"])
            self.kill_header.config(text="▶ Last Solo Kill")
            self.kill_info.config(
                text=(
                    f"  Date    : {kill['datetime']}\n"
                    f"  Ship    : {kill['ship']}\n"
                    f"  Weapons : {weapons_str}\n"
                    f"  KM ID   : {kill['killmail_id']}"
                )
            )
        else:
            self.kill_header.config(text="▶ Last Solo Kill")
            self.kill_info.config(text="  No solo kills found.")

        loss = data.get("solo_loss")
        if loss:
            weapons_str = "\n              ".join(loss["weapons"])
            self.loss_header.config(text="▶ Last Solo Loss")
            self.loss_info.config(
                text=(
                    f"  Date    : {loss['datetime']}\n"
                    f"  Ship    : {loss['ship']}\n"
                    f"  Weapons : {weapons_str}  (fitted)\n"
                    f"  KM ID   : {loss['killmail_id']}"
                )
            )
        else:
            self.loss_header.config(text="▶ Last Solo Loss")
            self.loss_info.config(text="  No solo losses found.")

    def _display_error(self, msg: str):
        self.btn.config(state="normal")
        self.status_var.set(f"Error: {msg}")


def main():
    root = tk.Tk()
    OverlayApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
