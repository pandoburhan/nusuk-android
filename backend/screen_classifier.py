import re
import xml.etree.ElementTree as ET
from typing import Dict, List, Optional, Tuple, Any
import logging

logger = logging.getLogger(__name__)

class ScreenState:
    SCREEN_LAUNCHER_OR_CLOSED = "SCREEN_LAUNCHER_OR_CLOSED"
    SCREEN_LOCATION_POPUP = "SCREEN_LOCATION_POPUP"
    SCREEN_DAILY_LIMIT_POPUP = "SCREEN_DAILY_LIMIT_POPUP"
    SCREEN_NUSUK_HOME = "SCREEN_NUSUK_HOME"
    SCREEN_CHOOSE_DOOR = "SCREEN_CHOOSE_DOOR"
    SCREEN_DOOR_CONFIRM_POPUP = "SCREEN_DOOR_CONFIRM_POPUP"
    SCREEN_QUIZ_QUESTION = "SCREEN_QUIZ_QUESTION"
    SCREEN_QUIZ_RESULT = "SCREEN_QUIZ_RESULT"
    SCREEN_REDEEM_KEYS = "SCREEN_REDEEM_KEYS"
    SCREEN_REDEEM_SUCCESS = "SCREEN_REDEEM_SUCCESS"
    SCREEN_UNKNOWN_OR_LOADING = "SCREEN_UNKNOWN_OR_LOADING"

def normalize_text(text: Optional[str]) -> str:
    if not text:
        return ""
    # Normalize curly apostrophes, quotes, and whitespace
    t = text.replace("’", "'").replace("‘", "'").replace("`", "'")
    return " ".join(t.split()).lower()

def parse_bounds(bounds_str: str) -> Optional[Tuple[int, int, int, int]]:
    # format: [x1,y1][x2,y2]
    match = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", bounds_str)
    if match:
        return int(match.group(1)), int(match.group(2)), int(match.group(3)), int(match.group(4))
    return None

def bounds_center(bounds: Optional[Tuple[int, int, int, int]]) -> Optional[Tuple[int, int]]:
    if not bounds:
        return None
    x1, y1, x2, y2 = bounds
    if x2 <= x1 or y2 <= y1:
        return None
    return (x1 + x2) // 2, (y1 + y2) // 2

def is_bounds_in_viewport(
    bounds: Optional[Tuple[int, int, int, int]],
    screen_w: int = 720,
    screen_h: int = 1280,
    margin_bottom: Optional[int] = None,
) -> bool:
    """True when bounds represent a non-trivial, visible clickable rectangle inside the screen viewport."""
    if not bounds:
        return False
    x1, y1, x2, y2 = bounds
    mb = max(10, int(screen_h * 0.015)) if margin_bottom is None else margin_bottom
    min_w = max(20, int(screen_w * 0.025))
    min_h = max(16, int(screen_h * 0.012))
    if (x2 - x1) < min_w or (y2 - y1) < min_h:
        return False
    if x1 < 0 or y1 < 0 or x2 > screen_w or y2 > (screen_h - mb):
        return False
    return True

def scale_ref_xy(ref_x: int, ref_y: int, screen_w: int = 720, screen_h: int = 1280) -> Tuple[int, int]:
    """Proportionally scales a fallback coordinate from 720x1280 reference to actual device resolution."""
    return max(1, int(round(ref_x * screen_w / 720.0))), max(1, int(round(ref_y * screen_h / 1280.0)))

_IME_PACKAGE_HINTS = ("inputmethod", "keyboard", "gboard", "latinime", "swiftkey", "ime")
_SYSTEM_CRASH_PHRASES = ("keeps stopping", "isn't responding", "has stopped", "app isn't responding")

class ScreenClassifier:
    """
    Parses uiautomator XML dump and extracts screen state, interactive elements,
    current keys count, question & options, result status, etc.
    """

    def parse_hierarchy(
        self,
        xml_content: str,
        current_package: Optional[str] = None,
        screen_w: Optional[int] = None,
        screen_h: Optional[int] = None,
    ) -> Dict[str, Any]:
        info: Dict[str, Any] = {
            "state": ScreenState.SCREEN_UNKNOWN_OR_LOADING,
            "current_app_keys": None,
            "screen_size": (screen_w or 720, screen_h or 1280),
            "location_popup_data": None, # {"later_bounds": tuple}
            "daily_limit_data": None,    # {"message": str, "close_bounds": tuple}
            "home_data": None,           # {"start_challenge_bounds": tuple}
            "choose_door_data": None,    # {"keys_tab_bounds": tuple}
            "door_confirm_data": None,   # {"open_door_bounds": tuple, "close_bounds": tuple}
            "door_cards": [],            # list of {"name": str, "bounds": tuple, "center": (x, y)}
            "question_data": None,       # {"question": str, "options": list, "confirm_bounds": tuple}
            "result_data": None,         # {"is_correct": bool, "result_text": str, "play_again_bounds": tuple}
            "redeem_data": None,         # {"has_input": bool, "input_bounds": tuple, "input_text": str, "input_has_value": bool, "check_code_bounds": tuple, ...}
            "success_data": None,        # {"play_again_bounds": tuple, "add_another_bounds": tuple, "play_tab_bounds": tuple}
            "xml_packages": [],
            "has_nusuk_package": False,
            "has_ime_overlay": False,
            "has_system_crash_dialog": False,
            "all_texts": []
        }

        if current_package and "com.moh.nusukapp" not in current_package:
            info["state"] = ScreenState.SCREEN_LAUNCHER_OR_CLOSED
            return info

        if not xml_content or "<hierarchy" not in xml_content:
            return info

        try:
            root = ET.fromstring(xml_content)
        except Exception as e:
            logger.error(f"Error parsing XML hierarchy: {e}")
            return info

        all_nodes = []
        xml_packages_set = set()
        max_x2 = 0
        max_y2 = 0
        for node in root.iter("node"):
            text = node.attrib.get("text", "")
            desc = node.attrib.get("content-desc", "")
            cls = node.attrib.get("class", "")
            pkg = node.attrib.get("package", "")
            bounds = parse_bounds(node.attrib.get("bounds", ""))
            if bounds:
                if bounds[2] > max_x2:
                    max_x2 = bounds[2]
                if bounds[3] > max_y2:
                    max_y2 = bounds[3]
            clickable = node.attrib.get("clickable", "false").lower() == "true"
            enabled = node.attrib.get("enabled", "true").lower() == "true"
            res_id = node.attrib.get("resource-id", "")

            if pkg:
                xml_packages_set.add(pkg)
            
            entry = {
                "text": text,
                "norm_text": normalize_text(text),
                "desc": desc,
                "norm_desc": normalize_text(desc),
                "class": cls,
                "package": pkg,
                "bounds": bounds,
                "clickable": clickable,
                "enabled": enabled,
                "resource_id": res_id,
                "node": node
            }
            all_nodes.append(entry)
            if text:
                info["all_texts"].append(text)
            if desc and desc != text:
                info["all_texts"].append(desc)

        # Resolve dynamic screen dimensions (prefer explicit device window_size, else infer from XML bounds)
        sw = int(screen_w) if (screen_w and screen_w >= 320) else (max_x2 if max_x2 >= 320 else 720)
        sh = int(screen_h) if (screen_h and screen_h >= 480) else (max_y2 if max_y2 >= 480 else 1280)
        info["screen_size"] = (sw, sh)

        # Proportional layout thresholds (works across 720x1280, 1080x2400, 1080x2340, 720x1600, etc.)
        header_y_max = int(sh * 0.16)
        header_x_min = int(sw * 0.60)
        bottom_nav_y_min = int(sh * 0.80)

        info["xml_packages"] = sorted(xml_packages_set)
        info["has_nusuk_package"] = any("com.moh.nusukapp" in p for p in xml_packages_set)
        info["has_ime_overlay"] = any(
            any(h in p.lower() for h in _IME_PACKAGE_HINTS)
            for p in xml_packages_set
        )

        combined_text = " ".join([n["norm_text"] + " " + n["norm_desc"] for n in all_nodes])

        # Detect Android crash / ANR dialog overlay (e.g. "Nusuk keeps stopping")
        if any(phrase in combined_text for phrase in _SYSTEM_CRASH_PHRASES):
            info["has_system_crash_dialog"] = True
            return info

        # 1. Check for current_app_keys in header (Top-right area proportional to screen size)
        for n in all_nodes:
            b = n["bounds"]
            if b and b[1] < header_y_max and b[0] > header_x_min:
                if n["text"].isdigit():
                    info["current_app_keys"] = int(n["text"])
                    break
                elif n["desc"].isdigit():
                    info["current_app_keys"] = int(n["desc"])
                    break

        # 1b. Check for SCREEN_DAILY_LIMIT_POPUP (HIGHEST modal priority!)
        # Nusuk dialog after clicking "Check your answer":
        # Title: "Something Went Wrong"
        # Body : "You've reached your daily limit. Please try again tomorrow."
        is_daily_limit = (
            "reached your daily limit" in combined_text
            or "try again tomorrow" in combined_text
            or (
                "daily limit" in combined_text
                and any(k in combined_text for k in ("something went wrong", "tomorrow", "reached", "exceeded", "try again"))
            )
        )
        if is_daily_limit:
            info["state"] = ScreenState.SCREEN_DAILY_LIMIT_POPUP
            popup_msg = "You've reached your daily limit. Please try again tomorrow."
            close_bounds = None
            close_labels = {"ok", "okay", "close", "dismiss", "got it", "done", "cancel", "back", "continue", "understand"}
            for n in all_nodes:
                nt = n["norm_text"]
                nd = n["norm_desc"]
                if "daily limit" in nt or "try again tomorrow" in nt:
                    popup_msg = n["text"] or popup_msg
                elif "daily limit" in nd or "try again tomorrow" in nd:
                    popup_msg = n["desc"] or popup_msg
                if n["bounds"] and (nt in close_labels or nd in close_labels):
                    close_bounds = n["bounds"]
            if close_bounds is None:
                # Fallback: look for a close icon / button via content-desc or short label
                for n in all_nodes:
                    if not n["bounds"]:
                        continue
                    nt = n["norm_text"] or n["norm_desc"]
                    if any(k in nt for k in ("close", "dismiss", "cancel")) and len(nt) <= 24:
                        close_bounds = n["bounds"]
                        break
            info["daily_limit_data"] = {
                "message": popup_msg,
                "close_bounds": close_bounds,
            }
            return info

        # 2. Check for SCREEN_LOCATION_POPUP ("Enable Location" / "Later")
        if "enable location" in combined_text and ("later" in combined_text or "prayer times" in combined_text):
            info["state"] = ScreenState.SCREEN_LOCATION_POPUP
            for n in all_nodes:
                if n["norm_text"] == "later" or "later" in n["norm_text"]:
                    info["location_popup_data"] = {"later_bounds": n["bounds"]}
                    break
            return info

        # 3. Check for SCREEN_DOOR_CONFIRM_POPUP ("Do you want to use your key?")
        if "do you want to use your key" in combined_text or "lose one key" in combined_text:
            info["state"] = ScreenState.SCREEN_DOOR_CONFIRM_POPUP
            open_door_bounds = None
            close_bounds = None
            for n in all_nodes:
                if not n["bounds"]:
                    continue
                if "open the door" in n["norm_text"] or "open the door" in n["norm_desc"]:
                    open_door_bounds = n["bounds"]
                elif n["norm_text"] == "close" or "close" in n["norm_text"]:
                    close_bounds = n["bounds"]
            info["door_confirm_data"] = {
                "open_door_bounds": open_door_bounds,
                "close_bounds": close_bounds,
            }
            return info

        # 4. Check for SCREEN_REDEEM_SUCCESS ("Congratulations")
        if "congratulations" in combined_text:
            info["state"] = ScreenState.SCREEN_REDEEM_SUCCESS
            success_data: Dict[str, Any] = {}
            for n in all_nodes:
                if not n["bounds"]:
                    continue
                if "play again" in n["norm_text"]:
                    success_data["play_again_bounds"] = n["bounds"]
                elif "add another code" in n["norm_text"]:
                    success_data["add_another_bounds"] = n["bounds"]
                elif n["norm_text"] == "play" and n["bounds"][1] > bottom_nav_y_min:
                    success_data["play_tab_bounds"] = n["bounds"]
            info["success_data"] = success_data or None
            return info

        # 5. Check for SCREEN_QUIZ_RESULT ("Play again")
        if "play again" in combined_text and any(k in combined_text for k in ["correct", "zamzam", "umrah", "incorrect", "wrong", "draw time", "share & get a chance"]):
            info["state"] = ScreenState.SCREEN_QUIZ_RESULT
            is_correct = "correct" in combined_text and "incorrect" not in combined_text
            play_again_bounds = None
            for n in all_nodes:
                if not n["bounds"]:
                    continue
                if n["norm_text"] == "play again" or n["norm_desc"] == "play again":
                    play_again_bounds = n["bounds"]
                    break
            if play_again_bounds is None:
                for n in all_nodes:
                    if not n["bounds"]:
                        continue
                    nt = n["norm_text"] or n["norm_desc"]
                    if "play again" in nt and len(nt) <= 24 and "share" not in nt:
                        play_again_bounds = n["bounds"]
                        break
            info["result_data"] = {
                "is_correct": is_correct,
                "result_text": "Correct" if is_correct else "Incorrect",
                "play_again_bounds": play_again_bounds
            }
            return info

        # 6. Check for SCREEN_REDEEM_KEYS (Tab Keys)
        if "challeng keys" in combined_text or "add code to get new key" in combined_text or "your keys" in combined_text or "check the code" in combined_text:
            info["state"] = ScreenState.SCREEN_REDEEM_KEYS
            input_bounds = None
            input_text = ""
            input_has_value = False
            check_code_bounds = None
            error_msg = ""
            is_already_used = False
            is_invalid = False

            placeholder_hints = {
                "add code to get new key",
                "enter code",
                "enter the code",
                "code",
                "add code",
            }
            already_used_patterns = [
                "already has been used",
                "already been used",
                "code already",
                "already used",
                "has already been used",
                "has been used",
                "already redeemed",
                "used before",
                "code is already used",
                "code already used",
            ]
            invalid_patterns = [
                "the entered code is not correct",
                "not correct",
                "ensure to enter the correct code",
                "is not correct",
                "incorrect",
                "wrong code",
                "wrong",
                "invalid",
                "not valid",
                "expired",
                "not exist",
                "does not exist",
                "failed",
                "not found",
            ]

            for n in all_nodes:
                norm_full = (n["norm_text"] + " " + n["norm_desc"]).strip()
                if n["class"] == "android.widget.EditText":
                    input_bounds = n["bounds"]
                    raw_val = (n["text"] or "").strip()
                    input_text = raw_val
                    norm_val = n["norm_text"].strip()
                    # Treat non-empty text that is not a known placeholder hint as existing typed value
                    input_has_value = bool(raw_val) and (norm_val not in placeholder_hints)
                elif input_bounds is None and "add code" in n["norm_text"]:
                    input_bounds = n["bounds"]
                if "check the code" in n["norm_text"]:
                    check_code_bounds = n["bounds"]

                # Check for explicit error message nodes
                if any(p in norm_full for p in already_used_patterns):
                    is_already_used = True
                    error_msg = n["text"] or n["desc"] or "The code already has been used, please enter a new code."
                elif any(p in norm_full for p in invalid_patterns):
                    is_invalid = True
                    error_msg = n["text"] or n["desc"] or "The entered code is not correct, please ensure to enter the correct code."

            # Fallback 1: check if error text was split across nodes in combined_text
            if not error_msg:
                if any(p in combined_text for p in already_used_patterns):
                    is_already_used = True
                    error_msg = "The code already has been used, please enter a new code."
                elif any(p in combined_text for p in invalid_patterns):
                    is_invalid = True
                    error_msg = "The entered code is not correct, please ensure to enter the correct code."

            # Fallback 2: structural detection of dynamic error banner below EditText
            if not error_msg and input_bounds:
                static_texts = {
                    "challeng keys", "your keys", "a key for every day",
                    "you get one key at the start of each day.",
                    "share the app and collect keys",
                    "share your link with family and friends, and everyone who downloads the app through it earns you another key.",
                    "share your link with family and friends",
                    "add code to get new key",
                    "one key will expire", "will expire in", "keys will expire",
                    "you do not have any key", "you don't have any key", "no keys available",
                    "check the code", "play", "winners", "keys", "share", "help"
                }
                for n in all_nodes:
                    norm_t = n["norm_text"].strip()
                    if not norm_t or len(norm_t) < 4:
                        continue
                    if any(st in norm_t or norm_t in st for st in static_texts):
                        continue
                    if norm_t.isdigit() or (n["desc"] and n["desc"].isdigit()):
                        continue
                    if n["class"] == "android.widget.EditText":
                        continue
                    b = n["bounds"]
                    err_above_margin = int(sh * 0.04)
                    err_below_margin = int(sh * 0.12)
                    if b and b[1] >= input_bounds[1] - err_above_margin:
                        if check_code_bounds and b[1] > check_code_bounds[3] + err_below_margin:
                            continue
                        # Captured unknown error banner below/around input
                        if any(p in norm_t for p in ["used", "redeem"]):
                            is_already_used = True
                        else:
                            is_invalid = True
                        error_msg = n["text"] or n["desc"] or norm_t
                        break

            info["redeem_data"] = {
                "has_input": input_bounds is not None,
                "input_bounds": input_bounds,
                "input_text": input_text,
                "input_has_value": input_has_value,
                "check_code_bounds": check_code_bounds,
                "error_msg": error_msg,
                "is_already_used": is_already_used,
                "is_invalid": is_invalid
            }
            return info

        # 7. Check for SCREEN_NUSUK_HOME BEFORE SCREEN_CHOOSE_DOOR!
        # Detect bottom navigation bar ONLY from exact short tab labels near the bottom of screen (y > 1050),
        # so quiz questions/options containing words like "Night Journey", "share", or "help" are NEVER
        # mistaken for navigation tabs.
        bottom_nav_labels = {
            n["norm_text"]
            for n in all_nodes
            if n["bounds"] and n["bounds"][1] > bottom_nav_y_min and n["norm_text"]
        }
        has_home_nav = bool(bottom_nav_labels & {"journey", "nusukai", "store"})
        if has_home_nav and (
            "100 umrah trips" in combined_text or "blessed zamzam" in combined_text
            or "take part today" in combined_text or "start the challenge" in combined_text
        ):
            info["state"] = ScreenState.SCREEN_NUSUK_HOME
            start_challenge_bounds = None
            for n in all_nodes:
                if ("start the challenge" in n["norm_text"] or "start the challenge" in n["norm_desc"]) and n["bounds"]:
                    start_challenge_bounds = n["bounds"]
                    break
            info["home_data"] = {"start_challenge_bounds": start_challenge_bounds}
            return info

        # 8. Check for SCREEN_CHOOSE_DOOR (Tab Play / Doors inside Challenge)
        if not has_home_nav and (
            "choose a door" in combined_text
            or ("start the challenge" in combined_text and any(d in combined_text for d in ["prophet", "quran", "makkah", "madinah"]))
        ):
            info["state"] = ScreenState.SCREEN_CHOOSE_DOOR
            doors = []
            door_keywords = [
                ("The Prophet's", ["prophet"]),
                ("The Quran", ["quran"]),
                ("Makkah and the", ["makkah"]),
                ("Madinah and the", ["madinah"])
            ]
            for standard_name, keywords in door_keywords:
                matched_node = None
                for n in all_nodes:
                    norm = n["norm_text"] + " " + n["norm_desc"]
                    if any(kw in norm for kw in keywords):
                        matched_node = n
                        break
                
                if matched_node and matched_node["bounds"]:
                    b = matched_node["bounds"]
                    center_x = (b[0] + b[2]) // 2
                    min_door_y = int(sh * 0.3125)
                    door_shift_y = int(sh * 0.0625)
                    center_y = max(min_door_y, b[1] - door_shift_y) if b[1] > int(sh * 0.47) else (b[1] + b[3]) // 2
                    doors.append({
                        "name": standard_name,
                        "bounds": b,
                        "center": (center_x, center_y)
                    })

            if len(doors) < 4:
                default_doors = [
                    {"name": "The Prophet's", "center": scale_ref_xy(183, 600, sw, sh)},
                    {"name": "The Quran", "center": scale_ref_xy(536, 600, sw, sh)},
                    {"name": "Makkah and the", "center": scale_ref_xy(183, 983, sw, sh)},
                    {"name": "Madinah and the", "center": scale_ref_xy(536, 983, sw, sh)},
                ]
                for def_door in default_doors:
                    if not any(d["name"] == def_door["name"] for d in doors):
                        doors.append(def_door)

            keys_tab_bounds = None
            for n in all_nodes:
                if n["norm_text"] == "keys" and n["bounds"]:
                    if n["bounds"][1] > bottom_nav_y_min or keys_tab_bounds is None:
                        keys_tab_bounds = n["bounds"]
            info["choose_door_data"] = {"keys_tab_bounds": keys_tab_bounds}
            info["door_cards"] = doors
            return info

        # 9. Check for SCREEN_QUIZ_QUESTION
        has_bottom_nav = bool(bottom_nav_labels & {"winners", "share", "help", "journey", "nusukai", "content", "store"})
        non_quiz_markers = (
            "downloading the mushaf",
            "download the quran",
            "please stay in the app until the download is complete",
            "nusuk market",
            "order zamzam water",
        )
        if any(m in combined_text for m in non_quiz_markers):
            return info

        confirm_bounds = None
        for n in all_nodes:
            if "check your answer" in n["norm_text"]:
                confirm_bounds = n["bounds"]
                break

        exact_nav_labels = {
            "play", "winners", "keys", "share", "help",
            "home", "journey", "content", "store", "nusukai",
        }
        phrase_ignore_list = (
            "start the challenge", "play again", "check your answer",
            "your key can unlock the door", "choose the right answer",
        )
        quiz_top_y = int(sh * 0.115)
        quiz_bottom_y = confirm_bounds[1] if confirm_bounds else int(sh * 0.96)
        candidate_texts = []
        for n in all_nodes:
            b = n["bounds"]
            t = n["text"].strip()
            if not b or not t:
                continue
            if b[1] < quiz_top_y or b[1] > quiz_bottom_y:
                continue
            if n["norm_text"] in exact_nav_labels:
                continue
            if any(ign in n["norm_text"] for ign in phrase_ignore_list):
                continue
            candidate_texts.append(n)

        is_quiz = False
        if any(q_kw in combined_text for q_kw in [
            "check your answer", "choose the right answer", "unlock the door and win",
            "your key can unlock the door", "doors of knowledge"
        ]):
            is_quiz = True
        elif not has_bottom_nav and 2 <= len(candidate_texts) <= 8:
            is_quiz = True

        if is_quiz and candidate_texts:
            info["state"] = ScreenState.SCREEN_QUIZ_QUESTION
            question_text = candidate_texts[0]["text"]
            options = []
            for opt in candidate_texts[1:]:
                if opt["text"] != question_text and opt["text"] not in [o["text"] for o in options]:
                    b = opt["bounds"]
                    center_x = (b[0] + b[2]) // 2
                    center_y = (b[1] + b[3]) // 2
                    options.append({
                        "text": opt["text"],
                        "bounds": b,
                        "center": (center_x, center_y)
                    })

            info["question_data"] = {
                "question": question_text,
                "options": options,
                "confirm_bounds": confirm_bounds
            }
            return info

        # Fallback unknown or loading
        return info

screen_classifier = ScreenClassifier()
