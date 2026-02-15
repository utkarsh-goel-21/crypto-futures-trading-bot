import json
import os
import threading
from datetime import datetime
from binance.client import Client

FOLLOWERS_FILE = os.path.join(os.path.dirname(__file__), "followers.json")
LOCK = threading.Lock()


def _read_followers_file():
    if not os.path.exists(FOLLOWERS_FILE):
        return {"followers": []}
    with open(FOLLOWERS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def _write_followers_file(data):
    with open(FOLLOWERS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def _create_testnet_client(api_key, secret_key):
    client = Client(api_key, secret_key, testnet=True)
    client.API_URL = "https://testnet.binance.vision/api"
    client.FUTURES_URL = "https://testnet.binancefuture.com/fapi"
    return client


def ensure_followers_file():
    """Create followers.json if missing."""
    with LOCK:
        if not os.path.exists(FOLLOWERS_FILE):
            _write_followers_file({"followers": []})


def add_follower(label, api_key, secret_key):
    """Add follower and validate credentials by calling futures_account()."""
    with LOCK:
        # Validate first
        test_client = _create_testnet_client(api_key, secret_key)
        test_client.futures_account()

        data = _read_followers_file()
        fid = f"f_{int(datetime.utcnow().timestamp() * 1000)}"
        follower = {
            "id": fid,
            "label": label.strip() if label else fid,
            "api_key": api_key.strip(),
            "secret_key": secret_key.strip(),
            "active": True,
            "created_at": datetime.utcnow().isoformat()
        }
        data["followers"].append(follower)
        _write_followers_file(data)
        return follower


def list_followers(mask_keys=True):
    with LOCK:
        data = _read_followers_file()
        out = []
        for f in data.get("followers", []):
            item = dict(f)
            if mask_keys:
                key = item.get("api_key", "")
                item["api_key"] = (key[:6] + "..." + key[-4:]) if len(key) > 10 else "***"
                item.pop("secret_key", None)
            out.append(item)
        return out


def toggle_follower(fid):
    with LOCK:
        data = _read_followers_file()
        for f in data.get("followers", []):
            if f["id"] == fid:
                f["active"] = not f.get("active", True)
                _write_followers_file(data)
                return f
    raise ValueError("Follower not found")


def delete_follower(fid):
    with LOCK:
        data = _read_followers_file()
        before = len(data.get("followers", []))
        data["followers"] = [f for f in data.get("followers", []) if f["id"] != fid]
        if len(data["followers"]) == before:
            raise ValueError("Follower not found")
        _write_followers_file(data)
        return True


def _active_followers():
    with LOCK:
        data = _read_followers_file()
        return [f for f in data.get("followers", []) if f.get("active", True)]


def copy_open_to_followers(symbol, side, quantity):
    """
    Mirror entry trade to all active followers.

    side: 'BUY' (long entry) or 'SELL' (short entry)
    quantity: same quantity as master
    """
    results = []
    followers = _active_followers()

    for f in followers:
        try:
            c = _create_testnet_client(f["api_key"], f["secret_key"])
            order = c.futures_create_order(
                symbol=symbol,
                side=side,
                type="MARKET",
                quantity=quantity
            )
            results.append({"id": f["id"], "ok": True, "orderId": order.get("orderId")})
        except Exception as e:
            results.append({"id": f["id"], "ok": False, "error": str(e)[:200]})

    return results


def copy_close_to_followers(symbol, side, quantity):
    """
    Mirror close trade to all active followers.

    side: for closing long use SELL, for closing short use BUY
    quantity: same quantity as master position
    """
    results = []
    followers = _active_followers()

    for f in followers:
        try:
            c = _create_testnet_client(f["api_key"], f["secret_key"])
            order = c.futures_create_order(
                symbol=symbol,
                side=side,
                type="MARKET",
                quantity=quantity,
                reduceOnly=True
            )
            results.append({"id": f["id"], "ok": True, "orderId": order.get("orderId")})
        except Exception as e:
            results.append({"id": f["id"], "ok": False, "error": str(e)[:200]})

    return results
