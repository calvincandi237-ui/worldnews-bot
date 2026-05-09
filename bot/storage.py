import json
import os
from typing import Dict, Set

STORAGE_FILE = "bot/data/users.json"


def _load() -> dict:
    os.makedirs(os.path.dirname(STORAGE_FILE), exist_ok=True)
    if not os.path.exists(STORAGE_FILE):
        return {}
    try:
        with open(STORAGE_FILE, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return {}


def _save(data: dict) -> None:
    os.makedirs(os.path.dirname(STORAGE_FILE), exist_ok=True)
    with open(STORAGE_FILE, "w") as f:
        json.dump(data, f, indent=2)


def get_user(user_id: int) -> dict:
    data = _load()
    uid = str(user_id)
    if uid not in data:
        data[uid] = {
            "subscriptions": [],
            "digest_hour": None,
            "digest_enabled": False,
        }
        _save(data)
    return data[uid]


def get_subscriptions(user_id: int) -> list:
    return get_user(user_id).get("subscriptions", [])


def add_subscription(user_id: int, category: str) -> bool:
    data = _load()
    uid = str(user_id)
    if uid not in data:
        data[uid] = {"subscriptions": [], "digest_hour": None, "digest_enabled": False}
    subs = data[uid].get("subscriptions", [])
    if category in subs:
        return False
    subs.append(category)
    data[uid]["subscriptions"] = subs
    _save(data)
    return True


def remove_subscription(user_id: int, category: str) -> bool:
    data = _load()
    uid = str(user_id)
    if uid not in data:
        return False
    subs = data[uid].get("subscriptions", [])
    if category not in subs:
        return False
    subs.remove(category)
    data[uid]["subscriptions"] = subs
    _save(data)
    return True


def set_digest(user_id: int, hour: int, enabled: bool) -> None:
    data = _load()
    uid = str(user_id)
    if uid not in data:
        data[uid] = {"subscriptions": [], "digest_hour": None, "digest_enabled": False}
    data[uid]["digest_hour"] = hour
    data[uid]["digest_enabled"] = enabled
    _save(data)


def get_all_digest_users() -> Dict[int, dict]:
    data = _load()
    result = {}
    for uid, info in data.items():
        if info.get("digest_enabled") and info.get("digest_hour") is not None:
            result[int(uid)] = info
    return result
