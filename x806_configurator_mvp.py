#!/usr/bin/env python3
"""
X806 / A804F Replacement Configurator
======================================

Reverse-engineered for:
    VID 0x30FA / PID 0x1340
    INSTANT USB GAMING MOUSE / X806
    MI_01 / Col04
    Feature Report ID 7, length 8

Capture-verified scope:
- Three onboard profiles: Game I, Game II, Game III
- Complete three-profile upload with globally packed objects
- Vendor-exact 8-byte memory blocks and final-apply command
- Eight physical buttons
- Ordinary HID keyboard keys
- Macros made from explicit key-down, key-up, tap, and delay events
- Modifier keys (Ctrl/Shift/Alt/Win) as ordinary HID usages
- Confirmed Game II switch action (0x39)
- Experimental raw direct-action byte for further reverse engineering
- JSON save/load
- Packet preview and explicit confirmation before writes
- Capacity checks so an object bank cannot overflow into the next observed bank

Known limitations are intentionally enforced in the UI.
"""

import json
import math
import re
import time
import configparser
import tkinter as tk
from tkinter import ttk, messagebox, filedialog, simpledialog
from pathlib import Path
from datetime import datetime

try:
    import pywinusb.hid as hid
except Exception as exc:
    hid = None
    HID_IMPORT_ERROR = exc
else:
    HID_IMPORT_ERROR = None

VID = 0x30FA
PID = 0x1340
REPORT_ID = 0x07
REPORT_LEN = 8
TARGET_FRAGMENT = "col04"

# X806 config.ini KEYMAP, confirmed against captures:
# physical key -> A804F internal switch number.
PHYSICAL_TO_INTERNAL = {
    1: 7,
    2: 3,
    3: 2,
    4: 1,
    5: 9,
    6: 6,
    7: 5,
    8: 4,
}

# Hardware layout reconstructed from complete vendor Apply captures.  The vendor
# UI exposes three onboard game profiles.  "Office" in older project files was a
# mistaken interpretation and remains local-only for backwards compatibility.
PROFILE_BANKS = {
    "Game I": {
        "function_addr": 0x0008,
        "descriptor_base": 0x00A0,
        "verified": True,
    },
    "Game II": {
        "function_addr": 0x0050,
        "descriptor_base": 0x01B8,
        "verified": True,
    },
    "Game III": {
        "function_addr": 0x0060,
        "descriptor_base": 0x0244,
        "verified": True,
    },
}
PROFILE_NAMES = ["Game I", "Game II", "Game III"]
HARDWARE_PROFILE_NAMES = PROFILE_NAMES
OBJECT_START = 0x0400
# Highest byte positively observed in the supplied vendor captures (macro test3).
OBJECT_END = 0x0547
PROJECT_FORMAT = "x806-configurator-project"
PROJECT_VERSION = 6
UPLOAD_LOG_FORMAT = "x806-upload-record"
UPLOAD_LOG_VERSION = 1
VENDOR_CONFIG_CANDIDATES = [
    Path(r"C:\Program Files (x86)\K806\skins\config\config.ini"),
    Path(r"C:\Program Files\K806\skins\config\config.ini"),
]
VENDOR_PROFILE_INDEX = {0:"Game I", 1:"Game I", 2:"Game II", 3:"Game III"}

# Primary action values confirmed in captures.
ACTION_EXTENDED = 0x2E
ACTION_MACRO = 0x2D
ACTION_GAME_II = 0x39
WRITE_COMMIT_DELAY = 0.016
FINAL_APPLY_DELAY = 0.032
FINAL_APPLY_PACKETS = (
    bytes([0x07,0x18,0x10,0x00,0x00,0x00,0x00,0x00]),
    bytes([0x07,0x18,0x00,0x00,0x00,0x00,0x00,0x00]),
)

DIRECT_ACTIONS = {
    "left_mouse":{"label":"Mouse: Left button","primary":0x21},
    "middle_mouse":{"label":"Mouse: Middle button","primary":0x22},
    "right_mouse":{"label":"Mouse: Right button","primary":0x23},
    "dpi_loop":{"label":"DPI Loop","primary":0x26},
    "double_click":{"label":"Mouse: Double Click","primary":0x28},
    "fire":{"label":"Mouse: Fire","primary":0x29},
    "dpi_minus":{"label":"DPI −","primary":0x2C},
    "switch_game_i":{"label":"Switch → Game I","primary":0x37},
    "switch_game_ii":{"label":"Switch → Game II","primary":0x39},
    "switch_game_iii":{"label":"Switch → Game III","primary":0x3A},
    "play_pause":{"label":"Media: Play / Pause","primary":0x2F,"descriptor":bytes.fromhex("01 00 00 CD 00 00 04 00 00 00")},
    "volume_up":{"label":"Media: Volume +","primary":0x2F,"descriptor":bytes.fromhex("01 00 00 E9 00 00 04 00 00 00")},
    "volume_down":{"label":"Media: Volume −","primary":0x2F,"descriptor":bytes.fromhex("01 00 0F EA 00 00 04 00 00 00")},
    "mute":{"label":"Media: Mute","primary":0x2F,"descriptor":bytes.fromhex("01 00 00 E2 00 00 04 00 00 00")},
    "previous":{"label":"Media: Previous","primary":0x2F,"descriptor":bytes.fromhex("01 00 00 B6 00 00 04 00 00 00")},
    "next":{"label":"Media: Next","primary":0x2F,"descriptor":bytes.fromhex("01 00 00 B5 00 00 04 00 00 00")},
    "player":{"label":"Application: Media player","primary":0x2F,"descriptor":bytes.fromhex("01 00 00 83 01 00 04 00 00 00")},
    "mail":{"label":"Application: Mail","primary":0x2F,"descriptor":bytes.fromhex("01 00 00 8A 01 00 04 00 00 00")},
    "calculator":{"label":"Application: Calculator","primary":0x2F,"descriptor":bytes.fromhex("01 00 00 92 01 00 04 00 00 00")},
    "browser":{"label":"Application: Browser","primary":0x2F,"descriptor":bytes.fromhex("01 00 00 23 02 00 04 00 00 00")},
    "show_desktop":{"label":"Desktop: Show desktop","primary":0x27},
    "led_loop":{"label":"LED Loop","primary":0x35},
    "dpi_plus":{"label":"DPI +","primary":0x2B},
}
DIRECT_ACTION_GROUPS = {
    "Mouse":["left_mouse","middle_mouse","right_mouse","double_click","fire"],
    "Media":["play_pause","previous","next","volume_up","volume_down","mute"],
    "Applications":["player","mail","calculator","browser","show_desktop"],
    "Profiles":["switch_game_i","switch_game_ii","switch_game_iii"],
    "DPI and lighting":["dpi_loop","dpi_plus","dpi_minus","led_loop"],
}
DIRECT_ACTION_LABELS={value["label"]:key for key,value in DIRECT_ACTIONS.items()}

LIGHTING_MODES={
    "cycle_breath":{"label":"Cycle breath","value":0x93},
    "button_response":{"label":"Button response","value":0x9B},
}
LIGHTING_LABELS={value["label"]:key for key,value in LIGHTING_MODES.items()}
LIGHTING_ADDRESS=0x001C
LIGHTING_COLOR_ADDRESS=0x001D
# 0x55 is four 2-bit fields set to 01. Controlled captures identify Blue,
# Pink, Red, and Purple independently.
LIGHTING_COLOR_FIELDS = {
    "blue": {"label":"Blue", "shift":0, "verified":True},
    "pink": {"label":"Pink", "shift":2, "verified":True},
    "red": {"label":"Red", "shift":4, "verified":True},
    "purple": {"label":"Purple", "shift":6, "verified":True},
}
USB_REPORT_CODES = {125:0x07,250:0x03,500:0x01,1000:0x00}
USB_REPORT_ADDRESS=0x001E
# Additional active descriptors outside the three editable key banks. The
# vendor rewrites their header byte whenever the USB report interval changes.
USB_REPORT_MIRROR_ADDRESSES=(0x0226,0x0230,0x023A,0x02B2,0x02BC,0x02C6)

# HID Keyboard/Keypad Usage IDs.
HID_KEYS = {
    "A":0x04,"B":0x05,"C":0x06,"D":0x07,"E":0x08,"F":0x09,"G":0x0A,"H":0x0B,
    "I":0x0C,"J":0x0D,"K":0x0E,"L":0x0F,"M":0x10,"N":0x11,"O":0x12,"P":0x13,
    "Q":0x14,"R":0x15,"S":0x16,"T":0x17,"U":0x18,"V":0x19,"W":0x1A,"X":0x1B,
    "Y":0x1C,"Z":0x1D,
    "1":0x1E,"2":0x1F,"3":0x20,"4":0x21,"5":0x22,"6":0x23,"7":0x24,"8":0x25,
    "9":0x26,"0":0x27,
    "Enter":0x28,"Escape":0x29,"Backspace":0x2A,"Tab":0x2B,"Space":0x2C,
    "-":0x2D,"=":0x2E,"[":0x2F,"]":0x30,"\\":0x31,
    ";":0x33,"'":0x34,"`":0x35,",":0x36,".":0x37,"/":0x38,
    "Caps Lock":0x39,
    "F1":0x3A,"F2":0x3B,"F3":0x3C,"F4":0x3D,"F5":0x3E,"F6":0x3F,
    "F7":0x40,"F8":0x41,"F9":0x42,"F10":0x43,"F11":0x44,"F12":0x45,
    "Print Screen":0x46,"Scroll Lock":0x47,"Pause":0x48,
    "Insert":0x49,"Home":0x4A,"Page Up":0x4B,"Delete":0x4C,"End":0x4D,
    "Page Down":0x4E,"Right":0x4F,"Left":0x50,"Down":0x51,"Up":0x52,
    "Num Lock":0x53,
    "Numpad /":0x54,"Numpad *":0x55,"Numpad -":0x56,"Numpad +":0x57,
    "Numpad Enter":0x58,"Numpad 1":0x59,"Numpad 2":0x5A,"Numpad 3":0x5B,
    "Numpad 4":0x5C,"Numpad 5":0x5D,"Numpad 6":0x5E,"Numpad 7":0x5F,
    "Numpad 8":0x60,"Numpad 9":0x61,"Numpad 0":0x62,"Numpad .":0x63,
    "Left Ctrl":0xE0,"Left Shift":0xE1,"Left Alt":0xE2,"Left Win":0xE3,
    "Right Ctrl":0xE4,"Right Shift":0xE5,"Right Alt":0xE6,"Right Win":0xE7,
}
HID_NAMES = {v:k for k,v in HID_KEYS.items()}

# US keyboard layout used by the "Type text" macro helper.  The firmware stores
# USB usages, not characters, so shifted punctuation must be expanded explicitly.
TEXT_KEYS = {
    **{chr(ord("a") + i): (chr(ord("A") + i), False) for i in range(26)},
    **{chr(ord("A") + i): (chr(ord("A") + i), True) for i in range(26)},
    **{str(i): (str(i), False) for i in range(10)},
    " ": ("Space", False), "\n": ("Enter", False), "\t": ("Tab", False),
    "-": ("-", False), "_": ("-", True), "=": ("=", False), "+": ("=", True),
    "[": ("[", False), "{": ("[", True), "]": ("]", False), "}": ("]", True),
    "\\": ("\\", False), "|": ("\\", True), ";": (";", False), ":": (";", True),
    "'": ("'", False), '"': ("'", True), "`": ("`", False), "~": ("`", True),
    ",": (",", False), "<": (",", True), ".": (".", False), ">": (".", True),
    "/": ("/", False), "?": ("/", True),
    "!": ("1", True), "@": ("2", True), "#": ("3", True), "$": ("4", True),
    "%": ("5", True), "^": ("6", True), "&": ("7", True), "*": ("8", True),
    "(": ("9", True), ")": ("0", True),
}

def hx(data):
    return " ".join(f"{b:02X}" for b in data)

def align8(n):
    return (n + 7) & ~7

def eight_byte_writes(address,data,pad=False):
    """Split memory into capture-matching <=8-byte writes.

    With pad=True the surrounding aligned bytes are known-zero descriptor padding
    observed in vendor captures (needed for the unaligned Game III descriptor base).
    """
    data=bytes(data)
    if not data:
        return []
    if pad:
        start=address & ~7
        prefix=address-start
        image=(b"\x00"*prefix)+data
        image += b"\x00"*((-len(image))%8)
        return [(start+i,image[i:i+8]) for i in range(0,len(image),8)]
    return [(address+i,data[i:i+8]) for i in range(0,len(data),8)]

def build_write_packets(address, data):
    if not data:
        raise ValueError("Cannot write an empty block")
    if len(data) > 256:
        raise ValueError("A single write block cannot exceed 256 bytes")
    lo = address & 0xFF
    hi = (address >> 8) & 0xFF
    lm1 = len(data) - 1
    packets = []
    for i, value in enumerate(data):
        packets.append([
            0x07, 0x18, 0x03, i & 0xFF,
            lo, hi, value & 0xFF, lm1 & 0xFF,
        ])
    packets.append([0x07,0x18,0x09,0x00,lo,hi,0x00,lm1 & 0xFF])
    packets.append([0x07,0x18,0x00,0x00,lo,hi,0x00,0x00])
    return packets

def single_key_object(usage):
    return bytes([0x01,0x00,0x80,usage & 0xFF,0,0,0,0])

def encode_delay_tokens(ms):
    """
    Controlled capture confirmed 2 ms timing units and max chunk 0x7F.
    Example 1000 ms -> 500 units -> 0x77 + 0x7F + 0x7F + 0x7F.
    """
    if ms < 0:
        raise ValueError("Delay cannot be negative")
    if ms == 0:
        return []
    units = int(math.ceil(ms / 2.0))
    chunks = []
    # To match vendor ordering, put the remainder first, then full 0x7F chunks.
    full, rem = divmod(units, 0x7F)
    if rem:
        chunks.append((rem, 0x00))
    chunks.extend([(0x7F, 0x00)] * full)
    return chunks

def text_to_events(text, inter_key_ms=0):
    """Compile US-layout text to explicit HID press/release source events."""
    if not isinstance(text, str) or not text:
        raise ValueError("Text macro cannot be empty")
    if inter_key_ms < 0:
        raise ValueError("Inter-key delay cannot be negative")
    events = []
    for pos, char in enumerate(text):
        mapping = TEXT_KEYS.get(char)
        if mapping is None:
            raise ValueError(f"Character {char!r} at position {pos + 1} is not available on the US keyboard helper")
        key_name, shifted = mapping
        if shifted:
            events.append({"type":"down", "usage":HID_KEYS["Left Shift"]})
        usage = HID_KEYS[key_name]
        events.extend([{"type":"down", "usage":usage}, {"type":"up", "usage":usage}])
        if shifted:
            events.append({"type":"up", "usage":HID_KEYS["Left Shift"]})
        if inter_key_ms and pos != len(text) - 1:
            events.append({"type":"delay", "ms":int(inter_key_ms)})
    return events

TK_KEYSYM_TO_HID={
    "Return":"Enter","Escape":"Escape","BackSpace":"Backspace","Tab":"Tab","space":"Space",
    "minus":"-","equal":"=","bracketleft":"[","bracketright":"]","backslash":"\\",
    "semicolon":";","apostrophe":"'","grave":"`","comma":",","period":".","slash":"/",
    "Caps_Lock":"Caps Lock","Print":"Print Screen","Scroll_Lock":"Scroll Lock","Pause":"Pause",
    "Insert":"Insert","Home":"Home","Prior":"Page Up","Delete":"Delete","End":"End",
    "Next":"Page Down","Right":"Right","Left":"Left","Down":"Down","Up":"Up",
    "Num_Lock":"Num Lock","Control_L":"Left Ctrl","Shift_L":"Left Shift","Alt_L":"Left Alt",
    "Super_L":"Left Win","Win_L":"Left Win","Control_R":"Right Ctrl","Shift_R":"Right Shift",
    "Alt_R":"Right Alt","Super_R":"Right Win","Win_R":"Right Win","KP_Divide":"Numpad /",
    "KP_Multiply":"Numpad *","KP_Subtract":"Numpad -","KP_Add":"Numpad +",
    "KP_Enter":"Numpad Enter","KP_Decimal":"Numpad .",
    **{f"KP_{i}":f"Numpad {i}" for i in range(10)},
    **{f"F{i}":f"F{i}" for i in range(1,13)},
}

def tkinter_event_usage(event):
    keysym=getattr(event,"keysym","")
    name=TK_KEYSYM_TO_HID.get(keysym)
    if name is None and len(keysym)==1:
        candidate=keysym.upper() if keysym.isalpha() else keysym
        if candidate in HID_KEYS: name=candidate
    return HID_KEYS.get(name) if name else None

def chord_events(modifiers,key):
    events=[{"type":"down","usage":HID_KEYS[modifier]} for modifier in modifiers]
    events.extend([{"type":"down","usage":HID_KEYS[key]},{"type":"up","usage":HID_KEYS[key]}])
    events.extend({"type":"up","usage":HID_KEYS[modifier]} for modifier in reversed(modifiers))
    return events

def macro_preset_events(name):
    chords={
        "Copy":(["Left Ctrl"],"C"),"Paste":(["Left Ctrl"],"V"),"Cut":(["Left Ctrl"],"X"),
        "Undo":(["Left Ctrl"],"Z"),"Redo":(["Left Ctrl"],"Y"),"Select all":(["Left Ctrl"],"A"),
        "Find":(["Left Ctrl"],"F"),"Save":(["Left Ctrl"],"S"),"Close window":(["Left Alt"],"F4"),
        "Show desktop":(["Left Win"],"D"),"Lock PC":(["Left Win"],"L"),"Run dialog":(["Left Win"],"R"),
        "My computer":(["Left Win"],"E"),
    }
    if name in chords: return chord_events(*chords[name])
    if name=="Calculator":
        return chord_events(["Left Win"],"R") + [{"type":"delay","ms":250}] + text_to_events("calc",20) + chord_events([],"Enter")
    raise ValueError(f"Unknown macro preset: {name}")

MACRO_PRESETS=["Copy","Paste","Cut","Undo","Redo","Select all","Find","Save","Close window","Show desktop","Lock PC","My computer","Run dialog","Calculator"]

def compile_macro(events):
    """
    Event token format confirmed by controlled capture:
        0x81 0x04 = A down, minimum event interval
        0x01 0x04 = A up, minimum event interval
        delay-only tokens use TT 00
    Explicit Delay events are encoded separately; key events use minimum 2 ms.
    """
    tokens = []
    for ev in events:
        et = ev["type"]
        if et == "down":
            usage = int(ev["usage"])
            tokens.append((0x81, usage))
        elif et == "up":
            usage = int(ev["usage"])
            tokens.append((0x01, usage))
        elif et == "delay":
            tokens.extend(encode_delay_tokens(int(ev["ms"])))
        else:
            raise ValueError(f"Unknown macro event type: {et}")
    if not tokens:
        raise ValueError("Macro must contain at least one event")
    if len(tokens) > 255:
        raise ValueError("Macro exceeds 255 compiled tokens")

    raw = bytearray([0x01,0x00])
    for t,k in tokens:
        raw += bytes([t & 0xFF, k & 0xFF])
    raw += b"\x00\x00"  # terminator
    while len(raw) % 8:
        raw.append(0)
    return bytes(raw), len(tokens)

def make_descriptor(action, ptr, macro_tokens=None, report_code=0x01):
    lo, hi = ptr & 0xFF, (ptr >> 8) & 0xFF
    if action["type"] == "key":
        usage = action["usage"]
        return bytes([report_code,0x01,usage,0,0,lo,hi,0,0,0])
    if action["type"] == "macro":
        if macro_tokens is None:
            raise ValueError("Macro token count required")
        return bytes([report_code,macro_tokens & 0xFF,0,0,0,lo,hi,0,0x0A,0])
    if action["type"] == "direct":
        descriptor=DIRECT_ACTIONS[action["action"]].get("descriptor",b"\x00"*10)
        if not any(descriptor):
            return descriptor
        # The vendor stores the current shared-object allocation pointer even
        # for direct HID-consumer actions, which do not allocate an object.
        # This becomes observable when a direct action follows a macro.
        result=bytearray(descriptor)
        result[0]=report_code
        result[5:7]=bytes([lo,hi])
        return bytes(result)
    if action["type"] in ("game2","raw"):
        return b"\x00" * 10
    raise ValueError("Unsupported action type")

def default_profile():
    return {
        str(i): {"type":"key","usage":HID_KEYS[str(i)]}
        for i in range(1,9)
    }

def default_project():
    return {
        "format":PROJECT_FORMAT,
        "version":PROJECT_VERSION,
        "lighting":{
            "mode":"cycle_breath",
            "cycle_time":4,
            "colors":{name:True for name in LIGHTING_COLOR_FIELDS},
        },
        "usb_report_hz":500,
        "profiles": {name: default_profile() for name in PROFILE_NAMES},
    }

def normalize_project(data):
    """Validate a saved project and migrate the original MVP v1 format."""
    if not isinstance(data, dict):
        raise ValueError("Project root must be an object")
    if data.get("format") not in ("x806-mvp-project", PROJECT_FORMAT):
        raise ValueError("Not an X806 configurator project")
    profiles = data.get("profiles")
    if not isinstance(profiles, dict):
        raise ValueError("Project has no profiles object")
    clean = default_project()
    lighting=data.get("lighting",{})
    if lighting is not None and not isinstance(lighting,dict):
        raise ValueError("Project lighting setting must be an object")
    lighting_mode=(lighting or {}).get("mode","cycle_breath")
    if lighting_mode not in LIGHTING_MODES:
        raise ValueError(f"Unknown lighting mode {lighting_mode!r}")
    cycle_time=int((lighting or {}).get("cycle_time",4))
    if not 1 <= cycle_time <= 6:
        raise ValueError("Lighting cycle time must be between 1 and 6")
    source_colors=(lighting or {}).get("colors",{})
    if source_colors is not None and not isinstance(source_colors,dict):
        raise ValueError("Project lighting colors must be an object")
    clean["lighting"]={
        "mode":lighting_mode,
        "cycle_time":cycle_time,
        "colors":{
            name:bool((source_colors or {}).get(name,True))
            for name in LIGHTING_COLOR_FIELDS
        },
    }
    usb_report_hz=int(data.get("usb_report_hz",500))
    if usb_report_hz not in USB_REPORT_CODES:
        raise ValueError(f"Unsupported USB report rate {usb_report_hz}")
    clean["usb_report_hz"]=usb_report_hz
    # v1/v2 exposed a spurious Office profile for hardware bank 0. If that
    # profile was edited while Game I remained untouched, carry those edits into
    # the real Game I profile so users do not lose their attempted mappings.
    if int(data.get("version",1)) <= 2 and isinstance(profiles.get("Office"),dict):
        if profiles.get("Game I") == default_profile() and profiles["Office"] != default_profile():
            profiles=dict(profiles)
            profiles["Game I"]=profiles["Office"]
    for profile_name in PROFILE_NAMES:
        source = profiles.get(profile_name)
        if not isinstance(source, dict):
            raise ValueError(f"Profile {profile_name} is missing")
        for physical in range(1, 9):
            action = source.get(str(physical))
            if not isinstance(action, dict):
                raise ValueError(f"{profile_name} key {physical} has no action")
            kind = action.get("type")
            if kind == "key":
                usage = int(action.get("usage", -1))
                if not 0 <= usage <= 255:
                    raise ValueError(f"{profile_name} key {physical} has an invalid HID usage")
                clean["profiles"][profile_name][str(physical)] = {"type":"key", "usage":usage}
            elif kind == "macro":
                events = action.get("events")
                if not isinstance(events, list):
                    raise ValueError(f"{profile_name} key {physical} has an invalid macro")
                compile_macro(events)
                clean["profiles"][profile_name][str(physical)] = {"type":"macro", "events":[dict(e) for e in events]}
            elif kind == "game2":
                clean["profiles"][profile_name][str(physical)] = {"type":"direct","action":"switch_game_ii"}
            elif kind == "direct":
                action_name=action.get("action")
                if action_name not in DIRECT_ACTIONS:
                    raise ValueError(f"{profile_name} key {physical} has unknown direct action {action_name!r}")
                clean["profiles"][profile_name][str(physical)]={"type":"direct","action":action_name}
            elif kind == "raw":
                value = int(action.get("value", -1))
                if not 0 <= value <= 255:
                    raise ValueError(f"{profile_name} key {physical} has an invalid raw action")
                clean["profiles"][profile_name][str(physical)] = {"type":"raw", "value":value}
            else:
                raise ValueError(f"{profile_name} key {physical} has unknown action type {kind!r}")
    return clean

def upload_log_directory():
    local_app_data=os.environ.get("LOCALAPPDATA") or str(Path.home()/"AppData"/"Local")
    return Path(local_app_data)/"X806 Configurator"/"upload_history"

def build_upload_record(project,compiled,blocks,selected_profile,project_path=None,when=None):
    when=when or datetime.now().astimezone()
    return {
        "format":UPLOAD_LOG_FORMAT,"version":UPLOAD_LOG_VERSION,
        "uploaded_at":when.isoformat(),"selected_profile":selected_profile,
        "project_path":str(project_path) if project_path else None,
        "project":normalize_project(json.loads(json.dumps(project))),
        "write_plan":{
            "used_bytes":compiled["used_bytes"],"capacity":compiled["capacity"],
            "blocks":[{"address":f"0x{address:04X}","data":data.hex(" ")} for address,data in blocks],
        },
    }

def persist_upload_record(record,directory=None):
    directory=Path(directory) if directory else upload_log_directory()
    directory.mkdir(parents=True,exist_ok=True)
    latest=directory/"last_uploaded_configuration.json"
    safe_time=re.sub(r"[^0-9]","",record["uploaded_at"])[:14]
    archive=directory/f"upload_{safe_time}.json"
    payload=json.dumps(record,indent=2,ensure_ascii=False)
    latest.write_text(payload,encoding="utf-8"); archive.write_text(payload,encoding="utf-8")
    with (directory/"upload_history.jsonl").open("a",encoding="utf-8") as stream:
        stream.write(json.dumps({
            "uploaded_at":record["uploaded_at"],"selected_profile":record["selected_profile"],
            "snapshot":archive.name,"project_path":record.get("project_path"),
        },ensure_ascii=False)+"\n")
    return latest,archive

def detect_vendor_active_profile(paths=None):
    """Read the vendor application's last selected onboard profile, if present."""
    for path in paths or VENDOR_CONFIG_CANDIDATES:
        path=Path(path)
        if not path.is_file():
            continue
        parser=configparser.RawConfigParser(interpolation=None,strict=False)
        try:
            parser.read(path,encoding="cp1252")
            index=parser.getint("Init","CurConfig")
            return VENDOR_PROFILE_INDEX.get(index),path
        except (OSError,ValueError,configparser.Error):
            continue
    return None,None

def parse_feature_packets(text):
    """Extract 8-byte report-7 packets from common text/USBPcap exports."""
    packets = []
    for line_no, line in enumerate(text.splitlines(), 1):
        compact=re.search(r"(?i)(?<![0-9a-f])([0-9a-f]{16})(?![0-9a-f])",line)
        if compact:
            candidate=bytes.fromhex(compact.group(1))
            if candidate[0]==REPORT_ID:
                packets.append((line_no,candidate))
                continue
        values = [int(x, 16) for x in re.findall(r"(?i)(?<![0-9a-f])[0-9a-f]{2}(?![0-9a-f])", line)]
        for start in range(max(0, len(values) - 7)):
            candidate = values[start:start + 8]
            if len(candidate) == 8 and candidate[0] == REPORT_ID:
                packets.append((line_no, bytes(candidate)))
                break
    return packets

def reconstruct_memory_writes(packets):
    """Reassemble command-0x18 packet streams into committed memory blocks."""
    pending = {}
    result = []
    warnings = []
    for source, packet in packets:
        p = bytes(packet)
        if len(p) != 8 or p[0] != REPORT_ID or p[1] != 0x18:
            continue
        op, index = p[2], p[3]
        address = p[4] | (p[5] << 8)
        expected = p[7] + 1
        key = (address, expected)
        if op == 0x03:
            pending.setdefault(key, {})[index] = p[6]
        elif op == 0x09:
            values = pending.get(key, {})
            missing = [i for i in range(expected) if i not in values]
            if missing:
                warnings.append(f"line {source}: 0x{address:04X} commit missing indexes {missing}")
            else:
                result.append((address, bytes(values[i] for i in range(expected))))
        elif op == 0x00:
            pending.pop(key, None)
    return result, warnings

def action_label(action):
    t = action.get("type")
    if t == "key":
        return HID_NAMES.get(action.get("usage"), f"HID 0x{action.get('usage',0):02X}")
    if t == "macro":
        evs = action.get("events",[])
        return f"Macro ({len(evs)} events)"
    if t == "direct":
        return DIRECT_ACTIONS.get(action.get("action"),{}).get("label","Unknown device action")
    if t == "game2":
        return "Switch → Game II"
    if t == "raw":
        return f"Raw action 0x{action.get('value',0):02X}"
    return "?"

class HidSession:
    def __init__(self):
        self.device = None
        self.report = None

    def connect(self):
        self.close()
        if hid is None:
            raise RuntimeError(f"pywinusb unavailable: {HID_IMPORT_ERROR}")
        devs = hid.HidDeviceFilter(vendor_id=VID, product_id=PID).get_devices()
        ordered = sorted(devs, key=lambda d: TARGET_FRAGMENT not in str(getattr(d,"device_path","")).lower())
        for dev in ordered:
            try:
                dev.open()
                for r in dev.find_feature_reports():
                    if r.report_id == REPORT_ID and len(r.get_raw_data()) == REPORT_LEN:
                        self.device = dev
                        self.report = r
                        return str(getattr(dev,"device_path",""))
                dev.close()
            except Exception:
                try: dev.close()
                except Exception: pass
        raise RuntimeError("Could not find X806 Feature Report 7 / 8-byte HID collection.")

    def send(self, packet):
        if not self.report:
            raise RuntimeError("Not connected")
        self.report.set_raw_data(packet)
        ok = self.report.send()
        if ok is False:
            raise RuntimeError("SetFeature failed")
        return ok

    def get_feature(self):
        if not self.report:
            raise RuntimeError("Not connected")
        self.report.get()
        return list(self.report.get_raw_data())

    def write_memory_block(self,address,data):
        """Send one <=8-byte memory block with vendor-capture timing."""
        packets=build_write_packets(address,data)
        for packet in packets[:-2]:
            self.send(packet)
            time.sleep(0.0005)
        time.sleep(WRITE_COMMIT_DELAY)
        self.send(packets[-2])       # 0x09 commit
        time.sleep(WRITE_COMMIT_DELAY)
        self.send(packets[-1])       # 0x00 finish block

    def apply_configuration(self):
        """Vendor Apply terminator; without this the new mappings stay inactive."""
        self.send(FINAL_APPLY_PACKETS[0])
        time.sleep(FINAL_APPLY_DELAY)
        self.send(FINAL_APPLY_PACKETS[1])

    def close(self):
        if self.device:
            try: self.device.close()
            except Exception: pass
        self.device = None
        self.report = None

class LegacyMacroEditor(tk.Toplevel):
    def __init__(self, parent, events, on_save):
        super().__init__(parent)
        self.title("Macro editor")
        self.geometry("720x560")
        self.transient(parent)
        self.grab_set()
        self.events = [dict(x) for x in events]
        self.on_save = on_save
        self.key_var = tk.StringVar(value="A")
        self.delay_var = tk.StringVar(value="100")
        self._build()
        self.refresh()

    def _build(self):
        top = ttk.Frame(self, padding=8)
        top.pack(fill="x")
        ttk.Label(top,text="Key:").pack(side="left")
        ttk.Combobox(top,textvariable=self.key_var,values=list(HID_KEYS.keys()),
                     state="readonly",width=18).pack(side="left",padx=5)
        ttk.Button(top,text="Tap",command=self.add_tap).pack(side="left",padx=3)
        ttk.Button(top,text="Down",command=lambda:self.add_key("down")).pack(side="left",padx=3)
        ttk.Button(top,text="Up",command=lambda:self.add_key("up")).pack(side="left",padx=3)
        ttk.Label(top,text="Delay ms:").pack(side="left",padx=(15,3))
        ttk.Entry(top,textvariable=self.delay_var,width=8).pack(side="left")
        ttk.Button(top,text="Add delay",command=self.add_delay).pack(side="left",padx=3)

        shortcuts = ttk.Frame(self,padding=(8,0,8,8))
        shortcuts.pack(fill="x")
        ttk.Button(shortcuts,text="Ctrl+C",command=lambda:self.add_shortcut(["Left Ctrl"],"C")).pack(side="left",padx=3)
        ttk.Button(shortcuts,text="Ctrl+V",command=lambda:self.add_shortcut(["Left Ctrl"],"V")).pack(side="left",padx=3)
        ttk.Button(shortcuts,text="Ctrl+Shift+S",command=lambda:self.add_shortcut(["Left Ctrl","Left Shift"],"S")).pack(side="left",padx=3)
        ttk.Button(shortcuts,text="Win+R",command=lambda:self.add_shortcut(["Left Win"],"R")).pack(side="left",padx=3)
        ttk.Button(shortcuts,text="Type text…",command=self.add_text).pack(side="left",padx=(15,3))

        f=ttk.Frame(self,padding=8); f.pack(fill="both",expand=True)
        self.tree=ttk.Treeview(f,columns=("n","event"),show="headings",selectmode="browse")
        self.tree.heading("n",text="#"); self.tree.column("n",width=45,anchor="center")
        self.tree.heading("event",text="Event"); self.tree.column("event",width=570)
        sy=ttk.Scrollbar(f,orient="vertical",command=self.tree.yview); self.tree.configure(yscrollcommand=sy.set)
        self.tree.pack(side="left",fill="both",expand=True); sy.pack(side="right",fill="y")

        row=ttk.Frame(self,padding=8); row.pack(fill="x")
        ttk.Button(row,text="Move up",command=lambda:self.move(-1)).pack(side="left",padx=3)
        ttk.Button(row,text="Move down",command=lambda:self.move(1)).pack(side="left",padx=3)
        ttk.Button(row,text="Delete",command=self.delete).pack(side="left",padx=3)
        ttk.Button(row,text="Clear",command=self.clear).pack(side="left",padx=3)
        ttk.Button(row,text="Cancel",command=self.destroy).pack(side="right",padx=3)
        ttk.Button(row,text="Save macro",command=self.save).pack(side="right",padx=3)

        self.info=tk.StringVar()
        ttk.Label(self,textvariable=self.info,padding=(10,0,10,8)).pack(fill="x")

    def refresh(self):
        for x in self.tree.get_children(): self.tree.delete(x)
        for i,ev in enumerate(self.events,1):
            if ev["type"] in ("down","up"):
                name=HID_NAMES.get(ev["usage"],f"0x{ev['usage']:02X}")
                desc=f"{name} {'DOWN' if ev['type']=='down' else 'UP'}"
            else:
                desc=f"Delay {ev['ms']} ms"
            self.tree.insert("","end",iid=str(i-1),values=(i,desc))
        try:
            raw,tokens=compile_macro(self.events) if self.events else (b"",0)
            self.info.set(f"Compiled tokens: {tokens}    Firmware object size: {len(raw)} bytes")
        except Exception as e:
            self.info.set(str(e))

    def add_key(self,t):
        self.events.append({"type":t,"usage":HID_KEYS[self.key_var.get()]}); self.refresh()
    def add_tap(self):
        u=HID_KEYS[self.key_var.get()]
        self.events += [{"type":"down","usage":u},{"type":"up","usage":u}]
        self.refresh()
    def add_delay(self):
        try: ms=int(self.delay_var.get())
        except: messagebox.showerror("Invalid delay","Enter an integer number of milliseconds.",parent=self); return
        if ms<0: return
        self.events.append({"type":"delay","ms":ms}); self.refresh()
    def add_shortcut(self,mods,key):
        for m in mods: self.events.append({"type":"down","usage":HID_KEYS[m]})
        self.events.append({"type":"down","usage":HID_KEYS[key]})
        self.events.append({"type":"up","usage":HID_KEYS[key]})
        for m in reversed(mods): self.events.append({"type":"up","usage":HID_KEYS[m]})
        self.refresh()
    def add_text(self):
        value=simpledialog.askstring(
            "Type text macro", "US keyboard text to append (use \\n for Enter and \\t for Tab):", parent=self
        )
        if value is None: return
        try:
            value=value.replace("\\n","\n").replace("\\t","\t")
            self.events.extend(text_to_events(value))
            self.refresh()
        except Exception as e:
            messagebox.showerror("Cannot encode text",str(e),parent=self)
    def selected(self):
        s=self.tree.selection()
        return int(s[0]) if s else None
    def move(self,d):
        i=self.selected()
        if i is None: return
        j=i+d
        if 0<=j<len(self.events):
            self.events[i],self.events[j]=self.events[j],self.events[i]; self.refresh(); self.tree.selection_set(str(j))
    def delete(self):
        i=self.selected()
        if i is not None: self.events.pop(i); self.refresh()
    def clear(self):
        self.events.clear(); self.refresh()
    def save(self):
        try: compile_macro(self.events)
        except Exception as e: messagebox.showerror("Invalid macro",str(e),parent=self); return
        self.on_save(self.events); self.destroy()

class LegacyActionEditor(tk.Toplevel):
    def __init__(self,parent,action,on_save,physical,profile):
        super().__init__(parent)
        self.title(f"{profile} – Physical key {physical}")
        self.geometry("600x390")
        self.transient(parent); self.grab_set()
        self.action=dict(action)
        if "events" in action: self.action["events"]=[dict(x) for x in action["events"]]
        self.on_save=on_save
        self.type_var=tk.StringVar(value=self.action.get("type","key"))
        self.key_var=tk.StringVar(value=HID_NAMES.get(self.action.get("usage",0x04),"A"))
        self.raw_var=tk.StringVar(value=f"0x{self.action.get('value',0):02X}")
        self.macro_summary=tk.StringVar()
        self._build(); self.update_state()

    def _build(self):
        f=ttk.Frame(self,padding=12); f.pack(fill="both",expand=True)
        ttk.Label(f,text="Action type:").grid(row=0,column=0,sticky="w")
        types=[("Keyboard key","key"),("Macro","macro"),("Switch → Game II (confirmed)","game2"),("Raw direct action (experimental)","raw")]
        for r,(label,val) in enumerate(types,1):
            ttk.Radiobutton(f,text=label,variable=self.type_var,value=val,command=self.update_state).grid(row=r,column=0,columnspan=2,sticky="w",pady=3)

        self.key_frame=ttk.LabelFrame(f,text="Keyboard key",padding=8)
        self.key_frame.grid(row=5,column=0,columnspan=2,sticky="ew",pady=10)
        ttk.Combobox(self.key_frame,textvariable=self.key_var,values=list(HID_KEYS.keys()),state="readonly",width=28).pack(side="left")

        self.macro_frame=ttk.LabelFrame(f,text="Macro",padding=8)
        self.macro_frame.grid(row=6,column=0,columnspan=2,sticky="ew",pady=6)
        ttk.Label(self.macro_frame,textvariable=self.macro_summary).pack(side="left")
        ttk.Button(self.macro_frame,text="Edit macro…",command=self.edit_macro).pack(side="right")

        self.raw_frame=ttk.LabelFrame(f,text="Raw primary action byte",padding=8)
        self.raw_frame.grid(row=7,column=0,columnspan=2,sticky="ew",pady=6)
        ttk.Entry(self.raw_frame,textvariable=self.raw_var,width=12).pack(side="left")
        ttk.Label(self.raw_frame,text="Only for protocol experiments.").pack(side="left",padx=8)

        b=ttk.Frame(f); b.grid(row=8,column=0,columnspan=2,sticky="ew",pady=(15,0))
        ttk.Button(b,text="Cancel",command=self.destroy).pack(side="right",padx=4)
        ttk.Button(b,text="Save",command=self.save).pack(side="right",padx=4)
        f.columnconfigure(0,weight=1)

    def set_enabled(self,frame,enabled):
        for w in frame.winfo_children():
            try: w.configure(state="normal" if enabled else "disabled")
            except: pass
    def update_state(self):
        t=self.type_var.get()
        self.set_enabled(self.key_frame,t=="key")
        self.set_enabled(self.macro_frame,t=="macro")
        self.set_enabled(self.raw_frame,t=="raw")
        evs=self.action.get("events",[])
        self.macro_summary.set(f"{len(evs)} source events")
    def edit_macro(self):
        evs=self.action.get("events",[])
        def done(x):
            self.action["events"]=[dict(e) for e in x]; self.update_state()
        MacroEditor(self,evs,done)
    def save(self):
        t=self.type_var.get()
        if t=="key":
            result={"type":"key","usage":HID_KEYS[self.key_var.get()]}
        elif t=="macro":
            evs=self.action.get("events",[])
            try: compile_macro(evs)
            except Exception as e: messagebox.showerror("Macro",str(e),parent=self); return
            result={"type":"macro","events":evs}
        elif t=="game2":
            result={"type":"game2"}
        else:
            try: v=int(self.raw_var.get(),0)
            except: messagebox.showerror("Raw value","Use e.g. 0x39 or 57.",parent=self); return
            if not 0<=v<=255: return
            result={"type":"raw","value":v}
        self.on_save(result); self.destroy()

class MacroEditor(ttk.Frame):
    """Embedded macro recorder and multi-selection event editor."""
    def __init__(self,parent,events):
        super().__init__(parent)
        self.events=[dict(event) for event in events]
        self.key_var=tk.StringVar(value="A"); self.delay_var=tk.StringVar(value="100")
        self.preset_var=tk.StringVar(value="Copy")
        self.recording=False; self.recorded_down=set(); self.last_record_time=None
        self._record_window=None; self._press_binding=None; self._release_binding=None
        self.record_status=tk.StringVar(value="Record captures presses, releases, and timing as you type.")
        self._build(); self.refresh()

    def _build(self):
        record=ttk.LabelFrame(self,text="Quick record",padding=9); record.pack(fill="x",pady=(0,5))
        self.record_button=ttk.Button(record,text="●  Start recording",command=self.toggle_recording)
        self.record_button.pack(side="left"); ttk.Label(record,textvariable=self.record_status).pack(side="left",padx=10)
        quick=ttk.LabelFrame(self,text="Quick add",padding=9); quick.pack(fill="x",pady=5)
        ttk.Combobox(quick,textvariable=self.preset_var,values=MACRO_PRESETS,state="readonly",width=17).pack(side="left")
        ttk.Button(quick,text="Add shortcut",command=self.add_preset).pack(side="left",padx=4)
        ttk.Button(quick,text="Type text…",command=self.add_text).pack(side="left",padx=8)
        ttk.Combobox(quick,textvariable=self.key_var,values=list(HID_KEYS),state="readonly",width=14).pack(side="left")
        ttk.Button(quick,text="Tap",command=self.add_tap).pack(side="left",padx=4)
        ttk.Entry(quick,textvariable=self.delay_var,width=6).pack(side="left",padx=(8,2))
        ttk.Label(quick,text="ms").pack(side="left"); ttk.Button(quick,text="Add delay",command=self.add_delay).pack(side="left",padx=4)
        body=ttk.Frame(self); body.pack(fill="both",expand=True,pady=5)
        self.tree=ttk.Treeview(body,columns=("n","event"),show="headings",selectmode="extended")
        self.tree.heading("n",text="#"); self.tree.column("n",width=45,anchor="center")
        self.tree.heading("event",text="Event"); self.tree.column("event",width=560)
        scroll=ttk.Scrollbar(body,orient="vertical",command=self.tree.yview); self.tree.configure(yscrollcommand=scroll.set)
        self.tree.grid(row=0,column=0,sticky="nsew"); scroll.grid(row=0,column=1,sticky="ns")
        body.rowconfigure(0,weight=1); body.columnconfigure(0,weight=1)
        self.tree.bind("<Control-a>",self.select_all); self.tree.bind("<Delete>",lambda e:self.delete())
        self.tree.bind("<Alt-Up>",lambda e:self.move(-1)); self.tree.bind("<Alt-Down>",lambda e:self.move(1))
        tools=ttk.Frame(self); tools.pack(fill="x")
        ttk.Button(tools,text="↑ Move",command=lambda:self.move(-1)).pack(side="left",padx=(0,4))
        ttk.Button(tools,text="↓ Move",command=lambda:self.move(1)).pack(side="left",padx=4)
        ttk.Button(tools,text="Delete selected",command=self.delete).pack(side="left",padx=4)
        ttk.Button(tools,text="Clear all",command=self.clear).pack(side="left",padx=4)
        self.info=tk.StringVar(); ttk.Label(self,textvariable=self.info).pack(fill="x",pady=(4,0))

    def refresh(self):
        self.tree.delete(*self.tree.get_children())
        for index,event in enumerate(self.events):
            if event["type"] in ("down","up"):
                name=HID_NAMES.get(event["usage"],f"0x{event['usage']:02X}")
                description=f"{name} {'DOWN' if event['type']=='down' else 'UP'}"
            else: description=f"Delay {event['ms']} ms"
            self.tree.insert("","end",iid=str(index),values=(index+1,description))
        try:
            raw,tokens=compile_macro(self.events) if self.events else (b"",0)
            self.info.set(f"{tokens} compiled tokens • {len(raw)} firmware bytes")
        except Exception as exc: self.info.set(str(exc))
    def add_preset(self):
        try: self.events.extend(macro_preset_events(self.preset_var.get())); self.refresh()
        except Exception as exc: messagebox.showerror("Preset",str(exc),parent=self)
    def add_text(self):
        value=simpledialog.askstring("Type text macro","US keyboard text to append (use \\n for Enter and \\t for Tab):",parent=self)
        if value is None: return
        try: self.events.extend(text_to_events(value.replace("\\n","\n").replace("\\t","\t"))); self.refresh()
        except Exception as exc: messagebox.showerror("Cannot encode text",str(exc),parent=self)
    def add_tap(self):
        usage=HID_KEYS[self.key_var.get()]; self.events.extend(({"type":"down","usage":usage},{"type":"up","usage":usage})); self.refresh()
    def add_delay(self):
        try: milliseconds=int(self.delay_var.get())
        except ValueError: messagebox.showerror("Invalid delay","Enter milliseconds as a whole number.",parent=self); return
        if milliseconds>=0: self.events.append({"type":"delay","ms":milliseconds}); self.refresh()
    def selected(self): return sorted(int(item) for item in self.tree.selection())
    def select_all(self,event=None): self.tree.selection_set(self.tree.get_children()); return "break"
    def delete(self):
        selected=self.selected()
        for index in reversed(selected): self.events.pop(index)
        if selected: self.refresh()
    def clear(self):
        if self.events and not messagebox.askyesno("Clear macro","Remove every event from this macro?",parent=self): return
        self.events.clear(); self.refresh()
    def move(self,direction):
        selected=set(self.selected())
        for index in (sorted(selected) if direction<0 else sorted(selected,reverse=True)):
            target=index+direction
            if 0<=target<len(self.events) and target not in selected:
                self.events[index],self.events[target]=self.events[target],self.events[index]
                selected.remove(index); selected.add(target)
        self.refresh()
        if selected: self.tree.selection_set(*(str(index) for index in sorted(selected)))
    def toggle_recording(self): self.stop_recording() if self.recording else self.start_recording()
    def start_recording(self):
        self.recording=True; self.recorded_down.clear(); self.last_record_time=None
        self.record_button.configure(text="■  Stop recording"); self.record_status.set("Recording… type now, then click Stop.")
        self._record_window=self.winfo_toplevel()
        self._press_binding=self._record_window.bind("<KeyPress>",lambda event:self.record_event(event,"down"),add="+")
        self._release_binding=self._record_window.bind("<KeyRelease>",lambda event:self.record_event(event,"up"),add="+")
        self._record_window.focus_force()
    def stop_recording(self):
        if not self.recording: return
        for usage in sorted(self.recorded_down): self.events.append({"type":"up","usage":usage})
        if self._record_window:
            self._record_window.unbind("<KeyPress>",self._press_binding); self._record_window.unbind("<KeyRelease>",self._release_binding)
        self.recording=False; self.recorded_down.clear(); self._record_window=None
        self.record_button.configure(text="●  Start recording"); self.record_status.set("Recording stopped."); self.refresh()
    def record_event(self,event,event_type):
        usage=tkinter_event_usage(event)
        if usage is None: self.record_status.set(f"Skipped unsupported key: {event.keysym}"); return "break"
        if event_type=="down" and usage in self.recorded_down: return "break"
        if event_type=="up" and usage not in self.recorded_down: return "break"
        now=time.monotonic()
        if self.last_record_time is not None:
            delay=round((now-self.last_record_time)*1000)
            if delay>=10: self.events.append({"type":"delay","ms":delay})
        self.events.append({"type":event_type,"usage":usage})
        self.recorded_down.add(usage) if event_type=="down" else self.recorded_down.discard(usage)
        self.last_record_time=now; self.refresh(); return "break"

class ActionEditor(tk.Toplevel):
    def __init__(self,parent,action,on_save,physical,profile):
        super().__init__(parent)
        self.physical=physical; self.action=json.loads(json.dumps(action)); self.on_save=on_save
        self.title(f"Assign key {physical} — {profile}"); self.geometry("920x720"); self.minsize(840,630)
        self.transient(parent); self.grab_set(); self.key_search=tk.StringVar(); self.key_names=list(HID_KEYS)
        self.capture_status=tk.StringVar(value="Focus here and press a key to assign it immediately")
        self.direct_groups=list(DIRECT_ACTION_GROUPS); self._build(); self.show_current(); self.protocol("WM_DELETE_WINDOW",self.cancel)
    @property
    def macro_tab(self): return 1+len(self.direct_groups)
    def _build(self):
        frame=ttk.Frame(self,padding=14); frame.pack(fill="both",expand=True)
        ttk.Label(frame,text=f"What should key {self.physical} do?",style="Title.TLabel").pack(anchor="w")
        ttk.Label(frame,text="Choose a group, then double-click an action to assign it.").pack(anchor="w",pady=(2,10))
        self.tabs=ttk.Notebook(frame); self.tabs.pack(fill="both",expand=True); self.tabs.bind("<<NotebookTabChanged>>",self.tab_changed)
        keyboard=ttk.Frame(self.tabs,padding=10); self.tabs.add(keyboard,text="Keyboard")
        capture=ttk.LabelFrame(keyboard,text="Press a key",padding=9); capture.pack(fill="x",pady=(0,9))
        self.capture=ttk.Label(capture,textvariable=self.capture_status,anchor="center",takefocus=True,padding=(10,12),relief="solid")
        self.capture.pack(fill="x"); self.capture.bind("<Button-1>",lambda e:self.capture.focus_set()); self.capture.bind("<KeyPress>",self.capture_key)
        ttk.Label(keyboard,text="Or search all supported keys:").pack(anchor="w")
        ttk.Entry(keyboard,textvariable=self.key_search).pack(fill="x",pady=5)
        self.key_list=tk.Listbox(keyboard,exportselection=False); self.key_list.pack(fill="both",expand=True)
        self.key_list.bind("<Double-1>",lambda e:self.apply()); self.key_search.trace_add("write",lambda *_:self.filter_keys())
        self.direct_lists={}
        for group in self.direct_groups:
            tab=ttk.Frame(self.tabs,padding=10); self.tabs.add(tab,text=group)
            listing=tk.Listbox(tab,exportselection=False); listing.pack(fill="both",expand=True)
            for action in DIRECT_ACTION_GROUPS[group]: listing.insert("end",DIRECT_ACTIONS[action]["label"])
            listing.bind("<Double-1>",lambda e:self.apply()); self.direct_lists[group]=listing
        macro=ttk.Frame(self.tabs,padding=10); self.tabs.add(macro,text="Macro")
        self.macro_editor=MacroEditor(macro,self.action.get("events",[])); self.macro_editor.pack(fill="both",expand=True)
        buttons=ttk.Frame(frame); buttons.pack(fill="x",pady=(10,0))
        ttk.Button(buttons,text="Cancel",command=self.cancel).pack(side="right",padx=4)
        ttk.Button(buttons,text="Assign",style="Accent.TButton",command=self.apply).pack(side="right",padx=4)
        self.filter_keys()
    def filter_keys(self):
        query=self.key_search.get().lower(); self.key_names=[name for name in HID_KEYS if query in name.lower()]
        self.key_list.delete(0,"end")
        for name in self.key_names: self.key_list.insert("end",name)
        if self.key_names: self.key_list.selection_set(0)
    def tab_changed(self,event=None):
        if not hasattr(self,"capture"): return
        tab=self.tabs.index(self.tabs.select())
        if self.macro_editor.recording and tab!=self.macro_tab: self.macro_editor.stop_recording()
        if tab==0: self.after_idle(self.capture.focus_set)
    def capture_key(self,event):
        usage=tkinter_event_usage(event)
        if usage is None: self.capture_status.set(f"{event.keysym} is not supported"); return "break"
        self.on_save({"type":"key","usage":usage}); self.cancel(); return "break"
    def show_current(self):
        kind=self.action.get("type")
        if kind=="key": self.tabs.select(0); self.after_idle(self.capture.focus_set)
        elif kind=="direct":
            for tab,group in enumerate(self.direct_groups,1):
                if self.action.get("action") in DIRECT_ACTION_GROUPS[group]:
                    self.tabs.select(tab); self.direct_lists[group].selection_set(DIRECT_ACTION_GROUPS[group].index(self.action["action"])); break
        else: self.tabs.select(self.macro_tab)
    def cancel(self):
        if self.macro_editor.recording: self.macro_editor.stop_recording()
        self.destroy()
    def apply(self):
        tab=self.tabs.index(self.tabs.select())
        if tab==0:
            selection=self.key_list.curselection()
            if not selection: return
            result={"type":"key","usage":HID_KEYS[self.key_names[selection[0]]]}
        elif tab<self.macro_tab:
            group=self.direct_groups[tab-1]; selection=self.direct_lists[group].curselection()
            if not selection: return
            result={"type":"direct","action":DIRECT_ACTION_GROUPS[group][selection[0]]}
        else:
            if self.macro_editor.recording: self.macro_editor.stop_recording()
            events=[dict(event) for event in self.macro_editor.events]
            try: compile_macro(events)
            except Exception as exc: messagebox.showerror("Macro",str(exc),parent=self); return
            result={"type":"macro","events":events}
        self.on_save(result); self.destroy()

class ProtocolLab(tk.Toplevel):
    """Read-only device checks and offline command-0x18 capture analysis."""
    def __init__(self,parent,session):
        super().__init__(parent)
        self.title("X806 Protocol Lab")
        self.geometry("900x680")
        self.transient(parent)
        self.session=session
        self.status=tk.StringVar(value="Read-only tools: these controls do not send feature reports.")
        self._build()

    def _build(self):
        top=ttk.Frame(self,padding=8); top.pack(fill="x")
        ttk.Button(top,text="Read Report 7",command=self.read_report).pack(side="left")
        ttk.Button(top,text="Analyze packets",command=self.analyze).pack(side="left",padx=6)
        ttk.Button(top,text="Load capture text…",command=self.load_text).pack(side="left")
        ttk.Button(top,text="Save analysis…",command=self.save_analysis).pack(side="right")
        ttk.Label(self,textvariable=self.status,padding=(10,0,10,6)).pack(fill="x")
        panes=ttk.Panedwindow(self,orient="vertical"); panes.pack(fill="both",expand=True,padx=8,pady=6)
        inf=ttk.LabelFrame(panes,text="Capture / packet text",padding=5)
        outf=ttk.LabelFrame(panes,text="Decoded committed memory writes",padding=5)
        self.input=tk.Text(inf,wrap="none",font=("Consolas",9),height=14)
        self.output=tk.Text(outf,wrap="none",font=("Consolas",9),height=14,state="disabled")
        self.input.pack(fill="both",expand=True); self.output.pack(fill="both",expand=True)
        panes.add(inf,weight=1); panes.add(outf,weight=1)
        ttk.Label(
            self,
            text="Paste one 8-byte HID packet per line, or a textual USBPcap/Wireshark export. "
                 "The analyzer reconstructs only capture-confirmed 0x18 write/commit sequences.",
            wraplength=860,padding=8
        ).pack(fill="x")

    def set_output(self,text):
        self.output.configure(state="normal"); self.output.delete("1.0","end")
        self.output.insert("1.0",text); self.output.configure(state="disabled")

    def read_report(self):
        try:
            if not self.session.report: self.session.connect()
            data=self.session.get_feature()
            line=f"READ-ONLY GetFeature: {hx(data)}\n"
            self.input.insert("end",line)
            self.status.set(line.strip())
        except Exception as e:
            messagebox.showerror("Read failed",str(e),parent=self)

    def analyze(self):
        packets=parse_feature_packets(self.input.get("1.0","end"))
        blocks,warnings=reconstruct_memory_writes(packets)
        lines=[f"Parsed {len(packets)} report-7 packet(s); reconstructed {len(blocks)} committed block(s)."]
        for address,data in blocks:
            lines.append(f"0x{address:04X}  len={len(data):3}  {hx(data)}")
        if warnings:
            lines += ["", "Warnings:"] + warnings
        self.set_output("\n".join(lines)+"\n")
        self.status.set(lines[0])

    def load_text(self):
        path=filedialog.askopenfilename(parent=self,filetypes=[("Capture text","*.txt *.log *.csv"),("All files","*.*")])
        if path:
            self.input.delete("1.0","end")
            self.input.insert("1.0",Path(path).read_text(encoding="utf-8",errors="replace"))
            self.status.set(f"Loaded {Path(path).name}")

    def save_analysis(self):
        path=filedialog.asksaveasfilename(parent=self,defaultextension=".txt",filetypes=[("Text","*.txt")])
        if path:
            Path(path).write_text(self.output.get("1.0","end"),encoding="utf-8")

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("X806 / A804F Configurator")
        self.geometry("980x720"); self.minsize(820,620)
        self.project=default_project()
        self.project_path=None
        self.session=HidSession()
        self.active_profile,self.vendor_config_path=detect_vendor_active_profile()
        self.profile_var=tk.StringVar(value="Game I")
        self.lighting_var=tk.StringVar(value=LIGHTING_MODES[self.project["lighting"]["mode"]]["label"])
        self.lighting_color_vars={
            name:tk.BooleanVar(value=self.project["lighting"]["colors"][name])
            for name in LIGHTING_COLOR_FIELDS
        }
        self.cycle_time_var=tk.IntVar(value=self.project["lighting"]["cycle_time"])
        self.usb_report_var=tk.StringVar(value=f"{self.project['usb_report_hz']} Hz")
        self.connected_var=tk.StringVar(value="Not connected")
        self.active_var=tk.StringVar()
        self.assignment_clipboard=None
        self.context_key=None
        self.status=tk.StringVar(value="Ready")
        self._build()
        self.protocol("WM_DELETE_WINDOW",self.close_app)
        self.refresh_buttons()
        if HID_IMPORT_ERROR:
            self.after(100,lambda:messagebox.showwarning(
                "pywinusb missing",
                "USB writing is unavailable until pywinusb is installed:\n\npy -m pip install pywinusb"
            ))

    def _build(self):
        menu=tk.Menu(self)
        fm=tk.Menu(menu,tearoff=0)
        fm.add_command(label="New",command=self.new_project)
        fm.add_command(label="Open…",command=self.open_project)
        fm.add_command(label="Save",command=self.save_project)
        fm.add_command(label="Save As…",command=self.save_project_as)
        fm.add_command(label="Export write plan…",command=self.export_write_plan)
        fm.add_separator(); fm.add_command(label="Exit",command=self.close_app)
        menu.add_cascade(label="File",menu=fm)
        tm=tk.Menu(menu,tearoff=0)
        tm.add_command(label="Validate all profiles",command=self.validate_all_profiles)
        tm.add_command(label="Protocol Lab…",command=lambda:ProtocolLab(self,self.session))
        menu.add_cascade(label="Tools",menu=tm)
        self.config(menu=menu)

        top=ttk.Frame(self,padding=8); top.pack(fill="x")
        ttk.Button(top,text="Connect X806",command=self.connect).pack(side="left")
        ttk.Button(top,text="Read Report 7",command=self.read_report).pack(side="left",padx=6)
        ttk.Button(top,text="Refresh active mode",command=self.refresh_active_profile).pack(side="left",padx=6)
        ttk.Label(top,textvariable=self.connected_var).pack(side="left",padx=8)
        ttk.Label(top,textvariable=self.active_var).pack(side="right",padx=8)

        pbar=ttk.Frame(self,padding=(8,0,8,8)); pbar.pack(fill="x")
        ttk.Label(pbar,text="Profile:").pack(side="left")
        cb=ttk.Combobox(pbar,textvariable=self.profile_var,values=PROFILE_NAMES,state="readonly",width=14)
        cb.pack(side="left",padx=6); cb.bind("<<ComboboxSelected>>",lambda e:self.refresh_buttons())
        self.profile_note=ttk.Label(pbar,text="")
        self.profile_note.pack(side="left",padx=10)
        ttk.Button(pbar,text="Preview profile writes",command=self.preview_profile).pack(side="right")
        ttk.Button(pbar,text="Reset profile",command=self.reset_profile).pack(side="right",padx=5)
        ttk.Button(pbar,text="Copy profile…",command=self.copy_profile).pack(side="right")

        keyframe=ttk.LabelFrame(self,text="Physical keys",padding=12); keyframe.pack(fill="x",padx=10,pady=5)
        self.key_buttons={}
        # 4 x 2 layout
        for i in range(1,9):
            r=0 if i<=4 else 1; c=(i-1)%4
            b=ttk.Button(keyframe,command=lambda n=i:self.edit_key(n))
            b.grid(row=r,column=c,padx=8,pady=8,sticky="nsew",ipady=12)
            self.key_buttons[i]=b
        for c in range(4): keyframe.columnconfigure(c,weight=1)

        write=ttk.LabelFrame(self,text="Device write",padding=10); write.pack(fill="x",padx=10,pady=7)
        ttk.Label(write,text="Writes require confirmation.").pack(side="left")
        self.upload_button=ttk.Button(write,text="UPLOAD selected profile",command=self.upload_profile)
        self.upload_button.pack(side="right")

        note=(
            "Vendor captures confirm three hardware profiles. Every upload rebuilds Game I, Game II and Game III together "
            "because their keyboard and macro objects share one packed memory region."
        )
        ttk.Label(self,text=note,wraplength=1000,padding=(12,2)).pack(fill="x")

        logf=ttk.LabelFrame(self,text="Protocol log",padding=6); logf.pack(fill="both",expand=True,padx=10,pady=7)
        self.log=tk.Text(logf,wrap="none",font=("Consolas",9))
        sy=ttk.Scrollbar(logf,orient="vertical",command=self.log.yview); sx=ttk.Scrollbar(logf,orient="horizontal",command=self.log.xview)
        self.log.configure(yscrollcommand=sy.set,xscrollcommand=sx.set)
        self.log.grid(row=0,column=0,sticky="nsew"); sy.grid(row=0,column=1,sticky="ns"); sx.grid(row=1,column=0,sticky="ew")
        logf.rowconfigure(0,weight=1); logf.columnconfigure(0,weight=1)

        bot=ttk.Frame(self,padding=(10,0,10,8)); bot.pack(fill="x")
        ttk.Label(bot,textvariable=self.status).pack(side="left")
        ttk.Button(bot,text="Clear log",command=lambda:self.log.delete("1.0","end")).pack(side="right")

    def _build(self):
        style=ttk.Style(self)
        style.configure("Title.TLabel",font=("Segoe UI",16,"bold"))
        style.configure("Profile.TLabel",font=("Segoe UI",18,"bold"))
        style.configure("Key.TButton",font=("Segoe UI",10,"bold"),padding=(10,16))
        style.configure("Accent.TButton",font=("Segoe UI",10,"bold"),padding=(12,7))
        menu=tk.Menu(self)
        file_menu=tk.Menu(menu,tearoff=0)
        file_menu.add_command(label="New",command=self.new_project); file_menu.add_command(label="Open…",command=self.open_project)
        file_menu.add_command(label="Save",command=self.save_project); file_menu.add_command(label="Save As…",command=self.save_project_as)
        file_menu.add_separator(); file_menu.add_command(label="Exit",command=self.close_app)
        menu.add_cascade(label="File",menu=file_menu)
        tools=tk.Menu(menu,tearoff=0); tools.add_command(label="Validate all profiles",command=self.validate_all_profiles)
        menu.add_cascade(label="Tools",menu=tools); self.config(menu=menu)

        header=ttk.Frame(self,padding=(16,14,16,8)); header.pack(fill="x")
        ttk.Label(header,text="X806 Configurator",style="Title.TLabel").pack(side="left")
        ttk.Label(header,textvariable=self.active_var).pack(side="right")
        profiles=ttk.LabelFrame(self,text="Profile",padding=(12,9)); profiles.pack(fill="x",padx=16,pady=(0,7))
        ttk.Button(profiles,text="◀",width=4,command=lambda:self.change_profile(-1)).pack(side="left")
        ttk.Label(profiles,textvariable=self.profile_var,style="Profile.TLabel",width=10,anchor="center").pack(side="left",padx=8)
        ttk.Button(profiles,text="▶",width=4,command=lambda:self.change_profile(1)).pack(side="left")
        self.profile_note=ttk.Label(profiles); self.profile_note.pack(side="left",padx=14)
        ttk.Button(profiles,text="Reset",command=self.reset_profile).pack(side="right",padx=(5,0))
        ttk.Button(profiles,text="Copy to…",command=self.copy_profile).pack(side="right")

        lighting=ttk.LabelFrame(self,text="Lighting",padding=(12,7)); lighting.pack(fill="x",padx=16,pady=7)
        for label in LIGHTING_LABELS:
            ttk.Radiobutton(lighting,text=label,value=label,variable=self.lighting_var,command=self.lighting_changed).pack(side="left",padx=(0,18))
        ttk.Label(lighting,text="Colors:").pack(side="left",padx=(12,4))
        for name,definition in LIGHTING_COLOR_FIELDS.items():
            ttk.Checkbutton(
                lighting,text=definition["label"],variable=self.lighting_color_vars[name],
                command=self.lighting_colors_changed,
            ).pack(side="left",padx=3)
        ttk.Label(lighting,text="Cycle time:").pack(side="left",padx=(14,4))
        cycle_box=ttk.Combobox(lighting,textvariable=self.cycle_time_var,values=list(range(1,7)),state="readonly",width=3)
        cycle_box.pack(side="left"); cycle_box.bind("<<ComboboxSelected>>",self.cycle_time_changed)
        ttk.Label(lighting,text="Applies to all profiles").pack(side="right")

        performance=ttk.LabelFrame(self,text="USB report rate",padding=(12,7)); performance.pack(fill="x",padx=16,pady=7)
        for rate in (125,250,500,1000):
            label=f"{rate} Hz"
            ttk.Radiobutton(performance,text=label,value=label,variable=self.usb_report_var,
                            command=self.usb_report_changed).pack(side="left",padx=(0,18))
        ttk.Label(performance,text="Applies to all profiles and key descriptors").pack(side="right")

        keys=ttk.LabelFrame(self,text="Keys — left-click to configure, right-click to copy, paste, or swap",padding=12)
        keys.pack(fill="x",padx=16,pady=7); self.key_buttons={}
        for physical in range(1,9):
            row=0 if physical<=4 else 1; column=(physical-1)%4
            button=ttk.Button(keys,style="Key.TButton",command=lambda key=physical:self.edit_key(key))
            button.grid(row=row,column=column,padx=6,pady=6,sticky="nsew")
            button.bind("<Button-3>",lambda event,key=physical:self.show_key_menu(event,key))
            self.key_buttons[physical]=button
        for column in range(4): keys.columnconfigure(column,weight=1)

        write=ttk.LabelFrame(self,text="Send to device",padding=10); write.pack(fill="x",padx=16,pady=7)
        ttk.Label(write,text="You will be asked to confirm before anything is written.").pack(side="left")
        self.upload_button=ttk.Button(write,style="Accent.TButton",text="Apply all profiles",command=self.upload_profile)
        self.upload_button.pack(side="right")
        activity=ttk.LabelFrame(self,text="Activity",padding=6); activity.pack(fill="both",expand=True,padx=16,pady=7)
        self.log=tk.Text(activity,wrap="word",font=("Segoe UI",9),height=5,state="disabled",relief="flat")
        scroll=ttk.Scrollbar(activity,orient="vertical",command=self.log.yview); self.log.configure(yscrollcommand=scroll.set)
        self.log.grid(row=0,column=0,sticky="nsew"); scroll.grid(row=0,column=1,sticky="ns")
        activity.rowconfigure(0,weight=1); activity.columnconfigure(0,weight=1)
        footer=ttk.Frame(self,padding=(16,0,16,10)); footer.pack(fill="x")
        ttk.Label(footer,textvariable=self.status).pack(side="left")
        self.bind_all("<Control-Prior>",lambda e:self.change_profile(-1)); self.bind_all("<Control-Next>",lambda e:self.change_profile(1))
        self.bind_all("<Control-c>",self.copy_selected_assignment); self.bind_all("<Control-v>",self.paste_selected_assignment)

    def logmsg(self,s):
        self.log.configure(state="normal")
        self.log.insert("end",f"[{datetime.now().strftime('%H:%M:%S')}] {s}\n"); self.log.see("end"); self.update_idletasks()
        self.log.configure(state="disabled")

    def current_profile(self):
        return self.project["profiles"][self.profile_var.get()]

    def lighting_changed(self):
        mode=LIGHTING_LABELS[self.lighting_var.get()]
        self.project["lighting"]["mode"]=mode; self.status.set(f"Lighting set to {self.lighting_var.get()}")

    def lighting_colors_changed(self):
        self.project["lighting"]["colors"]={
            name:variable.get() for name,variable in self.lighting_color_vars.items()
        }
        enabled=[definition["label"] for name,definition in LIGHTING_COLOR_FIELDS.items()
                 if self.project["lighting"]["colors"][name]]
        self.status.set("Lighting colors: "+(", ".join(enabled) if enabled else "none"))

    def cycle_time_changed(self,event=None):
        self.project["lighting"]["cycle_time"]=int(self.cycle_time_var.get())
        self.status.set(f"Lighting cycle time: {self.cycle_time_var.get()} of 6")

    def usb_report_changed(self):
        rate=int(self.usb_report_var.get().split()[0])
        self.project["usb_report_hz"]=rate
        self.status.set(f"USB report rate: {rate} Hz")

    def change_profile(self,direction):
        index=PROFILE_NAMES.index(self.profile_var.get())
        self.profile_var.set(PROFILE_NAMES[(index+direction)%len(PROFILE_NAMES)])
        self.context_key=None; self.refresh_buttons(); self.status.set(f"Editing {self.profile_var.get()}")

    def clone_action(self,action):
        return json.loads(json.dumps(action))

    def copy_key_assignment(self,physical):
        self.context_key=physical
        self.assignment_clipboard=self.clone_action(self.current_profile()[str(physical)])
        self.status.set(f"Copied {self.profile_var.get()} key {physical}: {action_label(self.assignment_clipboard)}")

    def paste_key_assignment(self,physical):
        if self.assignment_clipboard is None: return
        self.context_key=physical
        self.current_profile()[str(physical)]=self.clone_action(self.assignment_clipboard)
        self.refresh_buttons(); self.status.set(f"Pasted assignment onto {self.profile_var.get()} key {physical}")

    def swap_key_assignments(self,first,second):
        if first==second: return
        profile=self.current_profile(); profile[str(first)],profile[str(second)]=profile[str(second)],profile[str(first)]
        self.context_key=second; self.refresh_buttons()
        self.status.set(f"Swapped {self.profile_var.get()} keys {first} and {second}")

    def show_key_menu(self,event,physical):
        self.context_key=physical
        menu=tk.Menu(self,tearoff=0)
        menu.add_command(label="Copy assignment",command=lambda:self.copy_key_assignment(physical))
        menu.add_command(label="Paste assignment",state="normal" if self.assignment_clipboard else "disabled",command=lambda:self.paste_key_assignment(physical))
        swap=tk.Menu(menu,tearoff=0)
        for target in range(1,9):
            swap.add_command(label=f"Key {target}",state="disabled" if target==physical else "normal",command=lambda other=target:self.swap_key_assignments(physical,other))
        menu.add_cascade(label="Swap with",menu=swap)
        menu.tk_popup(event.x_root,event.y_root)

    def copy_selected_assignment(self,event=None):
        if event is not None and isinstance(event.widget,(tk.Entry,tk.Text,tk.Listbox,ttk.Entry,ttk.Combobox)):
            return None
        if self.context_key: self.copy_key_assignment(self.context_key)
        return "break"

    def paste_selected_assignment(self,event=None):
        if event is not None and isinstance(event.widget,(tk.Entry,tk.Text,tk.Listbox,ttk.Entry,ttk.Combobox)):
            return None
        if self.context_key: self.paste_key_assignment(self.context_key)
        return "break"

    def refresh_buttons(self):
        name=self.profile_var.get()
        prof=self.current_profile()
        mode=self.project.get("lighting",{}).get("mode","cycle_breath")
        self.lighting_var.set(LIGHTING_MODES[mode]["label"])
        self.cycle_time_var.set(int(self.project.get("lighting",{}).get("cycle_time",4)))
        self.usb_report_var.set(f"{int(self.project.get('usb_report_hz',500))} Hz")
        colors=self.project.get("lighting",{}).get("colors",{})
        for color,variable in self.lighting_color_vars.items():
            variable.set(bool(colors.get(color,True)))
        self.active_var.set("The keyboard's active profile cannot be read")
        self.profile_note.configure(text=f"Editing onboard profile {PROFILE_NAMES.index(name)+1} of {len(PROFILE_NAMES)}")
        self.upload_button.configure(state="normal",text="Apply all profiles")
        for i,b in self.key_buttons.items():
            a=prof[str(i)]
            b.configure(text=f"Key {i}\n{action_label(a)}")

    def refresh_active_profile(self):
        active,path=detect_vendor_active_profile()
        self.active_profile,self.vendor_config_path=active,path
        self.refresh_buttons()
        if active:
            self.status.set(f"Vendor configuration reports {active} active")
        else:
            self.status.set("Could not determine active profile from the vendor configuration")

    def edit_key(self,n):
        self.context_key=n
        prof=self.current_profile()
        def done(a):
            prof[str(n)]=a; self.refresh_buttons()
        ActionEditor(self,prof[str(n)],done,n,self.profile_var.get())

    def reset_profile(self):
        name=self.profile_var.get()
        if messagebox.askyesno("Reset profile",f"Reset {name} to keyboard keys 1–8?"):
            self.project["profiles"][name]=default_profile(); self.refresh_buttons()

    def copy_profile(self):
        source=self.profile_var.get()
        choices=[x for x in PROFILE_NAMES if x != source]
        target=simpledialog.askstring("Copy profile",f"Copy {source} to one of: {', '.join(choices)}",parent=self)
        if target is None: return
        target=next((x for x in choices if x.lower()==target.strip().lower()),None)
        if not target:
            messagebox.showerror("Unknown profile","Enter an exact profile name."); return
        self.project["profiles"][target]=json.loads(json.dumps(self.project["profiles"][source]))
        self.status.set(f"Copied {source} to {target}")

    def compile_hardware_profiles(self):
        """Compile the three real onboard profiles as one globally packed image."""
        ptr=OBJECT_START
        capacity=OBJECT_END-OBJECT_START+1
        report_code=USB_REPORT_CODES[int(self.project.get("usb_report_hz",500))]
        function_images={name:bytearray(9) for name in HARDWARE_PROFILE_NAMES}
        descriptor_images={name:bytearray(90) for name in HARDWARE_PROFILE_NAMES}
        object_writes=[]
        compiled_meta={}

        # Vendor Apply packs every extended object across Game I, II and III in
        # this exact order. A change in an early profile relocates later objects.
        for name in HARDWARE_PROFILE_NAMES:
            prof=self.project["profiles"][name]
            by_internal={PHYSICAL_TO_INTERNAL[p]:prof[str(p)] for p in range(1,9)}
            for internal in sorted(by_internal):
                action=by_internal[internal]
                if action["type"]=="key":
                    obj=single_key_object(action["usage"]); token_count=None; primary=ACTION_EXTENDED
                elif action["type"]=="macro":
                    obj,token_count=compile_macro(action.get("events",[])); primary=ACTION_MACRO
                elif action["type"]=="direct":
                    definition=DIRECT_ACTIONS[action["action"]]
                    obj=None; token_count=None; primary=definition["primary"]
                elif action["type"]=="game2":
                    obj=None; token_count=None; primary=ACTION_GAME_II
                elif action["type"]=="raw":
                    obj=None; token_count=None; primary=int(action["value"])
                else:
                    raise ValueError(f"{name} internal switch {internal}: unknown action")

                # Captures confirm nine primary bytes; byte 9 is switch 9 / physical key 5.
                function_images[name][internal-1]=primary
                descriptor_offset=(internal-1)*10
                if obj is not None:
                    if ptr+len(obj)-1>OBJECT_END:
                        raise ValueError(
                            f"Shared object region overflow at {name}, internal switch {internal}.\n"
                            f"Used before object: {ptr-OBJECT_START}; object: {len(obj)}; capacity: {capacity}."
                        )
                    descriptor_images[name][descriptor_offset:descriptor_offset+10]=make_descriptor(
                        action,ptr,token_count,report_code
                    )
                    object_writes.extend(eight_byte_writes(ptr,obj))
                    compiled_meta[(name,internal)]=(ptr,len(obj))
                    ptr+=len(obj)
                else:
                    compiled_meta[(name,internal)]=(None,0)
                    if action["type"]=="direct":
                        descriptor_images[name][descriptor_offset:descriptor_offset+10]=make_descriptor(
                            action,ptr,None,report_code
                        )

        function_writes=[]
        descriptor_writes=[]
        for name in HARDWARE_PROFILE_NAMES:
            bank=PROFILE_BANKS[name]
            image=function_images[name]
            # The ninth primary byte shares an 8-byte area with unrelated settings;
            # write it separately so those captured settings are preserved.
            function_writes.append((bank["function_addr"],bytes(image[:8])))
            function_writes.append((bank["function_addr"]+8,bytes(image[8:9])))
            descriptor_writes.extend(eight_byte_writes(bank["descriptor_base"],descriptor_images[name],pad=True))

        return {
            "function_writes":function_writes,
            "descriptor_writes":descriptor_writes,
            "object_writes":object_writes,
            "lighting_writes":[
                (LIGHTING_ADDRESS,bytes([
                    (LIGHTING_MODES[self.project.get("lighting",{}).get("mode","cycle_breath")]["value"] & 0xF8)
                    | (int(self.project.get("lighting",{}).get("cycle_time",4))-1)
                ])),
                (LIGHTING_COLOR_ADDRESS,bytes([sum(
                    1 << definition["shift"]
                    for color,definition in LIGHTING_COLOR_FIELDS.items()
                    if self.project.get("lighting",{}).get("colors",{}).get(color,True)
                )])),
            ],
            "usb_report_writes":[
                (USB_REPORT_ADDRESS,bytes([report_code])),
                *((address,bytes([report_code])) for address in USB_REPORT_MIRROR_ADDRESSES),
            ],
            "used_bytes":ptr-OBJECT_START,
            "capacity":capacity,
            "meta":compiled_meta,
        }

    def compile_profile(self,name):
        if name not in PROFILE_BANKS:
            raise ValueError(f"{name} is a legacy local profile and has no onboard bank.")
        return self.compile_hardware_profiles()

    def all_write_blocks(self,name):
        c=self.compile_profile(name)
        # Match vendor ordering and, critically, its maximum 8-byte memory blocks.
        blocks=(c["function_writes"]+c["lighting_writes"]+c["usb_report_writes"]+
                c["descriptor_writes"]+c["object_writes"])
        if any(len(data)>8 for _,data in blocks):
            raise AssertionError("Internal error: write plan contains a block larger than 8 bytes")
        return c,blocks

    def validate_local_profile(self,name):
        profile=self.project["profiles"][name]
        summary={"keys":8,"macros":0,"tokens":0}
        for physical in range(1,9):
            action=profile[str(physical)]
            if action["type"]=="key":
                if not 0 <= int(action["usage"]) <= 255: raise ValueError(f"{name} key {physical}: invalid usage")
            elif action["type"]=="macro":
                _,count=compile_macro(action.get("events",[])); summary["macros"]+=1; summary["tokens"]+=count
            elif action["type"]=="direct":
                if action.get("action") not in DIRECT_ACTIONS: raise ValueError(f"{name} key {physical}: unknown direct action")
            elif action["type"]=="raw":
                if not 0 <= int(action["value"]) <= 255: raise ValueError(f"{name} key {physical}: invalid raw action")
            elif action["type"]!="game2":
                raise ValueError(f"{name} key {physical}: unknown action")
        return summary

    def validate_all_profiles(self):
        try:
            lines=[]
            for name in PROFILE_NAMES:
                summary=self.validate_local_profile(name)
                detail=f"{summary['macros']} macro(s), {summary['tokens']} compiled token(s)"
                if name in PROFILE_BANKS:
                    compiled=self.compile_profile(name)
                    detail += f", objects {compiled['used_bytes']}/{compiled['capacity']} bytes"
                else:
                    detail += ", upload bank not verified"
                lines.append(f"{name}: OK — {detail}")
            for line in lines: self.logmsg(line)
            messagebox.showinfo("Validation passed","\n".join(lines))
        except Exception as e:
            self.logmsg("VALIDATION ERROR: "+str(e)); messagebox.showerror("Validation failed",str(e))

    def preview_profile(self):
        name=self.profile_var.get()
        try:
            c,blocks=self.all_write_blocks(name)
        except Exception as e:
            self.logmsg(f"PREVIEW ERROR: {e}"); messagebox.showerror("Cannot compile profile",str(e)); return
        self.logmsg(f"=== PREVIEW ALL HARDWARE PROFILES (requested from {name}) ===")
        self.logmsg(f"Shared object usage: {c['used_bytes']}/{c['capacity']} bytes")
        for addr,data in blocks:
            self.logmsg(f"WRITE 0x{addr:04X} len={len(data):3}: {hx(data)}")
        self.logmsg(f"FINAL APPLY: {hx(FINAL_APPLY_PACKETS[0])} then {hx(FINAL_APPLY_PACKETS[1])}")
        self.logmsg(f"Total memory blocks: {len(blocks)} plus 2 final-apply reports")

    def export_write_plan(self):
        name=self.profile_var.get()
        try:
            c,blocks=self.all_write_blocks(name)
        except Exception as e:
            messagebox.showerror("Cannot export",str(e)); return
        path=filedialog.asksaveasfilename(
            defaultextension=".json",initialfile=f"{name.lower().replace(' ','_')}_write_plan.json",
            filetypes=[("JSON","*.json")]
        )
        if not path:return
        payload={
            "format":"x806-write-plan","version":2,"profiles":HARDWARE_PROFILE_NAMES,
            "object_usage":{"used":c["used_bytes"],"capacity":c["capacity"]},
            "blocks":[{"address":f"0x{addr:04X}","data":data.hex(" "),
                       "packets":[bytes(p).hex(" ") for p in build_write_packets(addr,data)]}
                      for addr,data in blocks],
            "final_apply_packets":[packet.hex(" ") for packet in FINAL_APPLY_PACKETS],
        }
        Path(path).write_text(json.dumps(payload,indent=2),encoding="utf-8")
        self.status.set(f"Exported {Path(path).name}")

    def connect(self):
        try:
            p=self.session.connect(); self.connected_var.set("Connected"); self.logmsg("Connected: "+p)
        except Exception as e:
            self.logmsg("CONNECT ERROR: "+str(e)); messagebox.showerror("Connection failed",str(e))

    def read_report(self):
        try:
            if not self.session.report: self.connect()
            r=self.session.get_feature(); self.logmsg("GetFeature(07): "+hx(r))
        except Exception as e:
            self.logmsg("READ ERROR: "+str(e)); messagebox.showerror("Read failed",str(e))

    def upload_profile(self):
        name=self.profile_var.get()
        if name not in PROFILE_BANKS:
            messagebox.showerror(
                "Direct upload unavailable",
                f"{name} cannot be uploaded directly yet because its function, descriptor, and object addresses "
                "have not been captured from the vendor program. The profile remains editable and saveable."
            )
            return
        try:
            c,blocks=self.all_write_blocks(name)
        except Exception as e:
            messagebox.showerror("Cannot compile profile",str(e)); self.logmsg("COMPILE ERROR: "+str(e)); return

        self.preview_profile()
        if not messagebox.askyesno(
            "Write hardware configuration",
            "The vendor protocol packs Game I, Game II and Game III together, so all three profiles "
            "must be uploaded as one configuration.\n\n"
            f"Shared object usage: {c['used_bytes']}/{c['capacity']} bytes.\n\n"
            "Continue with the complete three-profile upload?"
        ): return
        try:
            if not self.session.report: self.connect()
            total_packets=sum(len(build_write_packets(a,d)) for a,d in blocks)
            n=0
            self.logmsg(f"BEGIN UPLOAD ALL HARDWARE PROFILES: {len(blocks)} blocks, {total_packets + 2} feature reports")
            for addr,data in blocks:
                self.logmsg(f"Block 0x{addr:04X}, {len(data)} bytes")
                self.session.write_memory_block(addr,data)
                n+=len(build_write_packets(addr,data))
                if n%20==0: self.status.set(f"Sending {n}/{total_packets + 2}")
            self.logmsg("Sending vendor final-apply command 0x10")
            self.session.apply_configuration()
            self.logmsg("UPLOAD COMPLETE: Game I / Game II / Game III")
            self.status.set("Upload complete")
            try: self.logmsg("Post-write Report 7: "+hx(self.session.get_feature()))
            except Exception as e: self.logmsg("Post-write read failed: "+str(e))
            try: persist_upload_record(build_upload_record(self.project,c,blocks,name,self.project_path))
            except Exception as e: self.logmsg("UPLOAD LOG ERROR: "+str(e))
            messagebox.showinfo("Upload complete","Game I, Game II and Game III were written and the vendor apply command was sent.")
        except Exception as e:
            self.logmsg("UPLOAD ERROR: "+str(e)); self.status.set("Upload failed"); messagebox.showerror("Upload failed",str(e))

    def new_project(self):
        if not messagebox.askyesno("New project","Discard the current in-memory configuration and start with 1–8 defaults?"): return
        self.project=default_project(); self.project_path=None; self.refresh_buttons()

    def open_project(self):
        p=filedialog.askopenfilename(filetypes=[("X806 project","*.json"),("All files","*.*")])
        if not p:return
        try:
            d=json.loads(Path(p).read_text(encoding="utf-8"))
            self.project=normalize_project(d); self.project_path=Path(p); self.refresh_buttons(); self.status.set(f"Opened {Path(p).name}")
        except Exception as e: messagebox.showerror("Open failed",str(e))

    def save_project(self):
        if not self.project_path: return self.save_project_as()
        self.project=normalize_project(self.project)
        if self.project_path.exists():
            backup=self.project_path.with_suffix(self.project_path.suffix+".bak")
            backup.write_bytes(self.project_path.read_bytes())
        self.project_path.write_text(json.dumps(self.project,indent=2),encoding="utf-8")
        self.status.set(f"Saved {self.project_path.name}")

    def save_project_as(self):
        p=filedialog.asksaveasfilename(defaultextension=".json",filetypes=[("X806 project","*.json")])
        if not p:return
        self.project_path=Path(p); self.save_project()

    def close_app(self):
        self.session.close(); self.destroy()

if __name__=="__main__":
    App().mainloop()
