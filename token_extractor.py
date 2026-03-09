"""Extract FlowUS JWT tokens from the desktop app's LevelDB local storage."""

from __future__ import annotations

import base64
import json
import re
import subprocess
from pathlib import Path


DEFAULT_LEVELDB_PATH = Path.home() / "Library/Application Support/FlowUs/Partitions/main/Local Storage/leveldb"


def extract_tokens(leveldb_path: Path | None = None) -> list[dict]:
    """Extract JWT tokens from FlowUS LevelDB files.

    Returns a list of dicts with keys: token, iat, exp, nickname, uuid.
    Sorted by iat descending (most recent first).
    """
    path = leveldb_path or DEFAULT_LEVELDB_PATH
    if not path.exists():
        raise FileNotFoundError(
            f"FlowUS LevelDB not found at {path}. "
            "Make sure the FlowUS desktop app is installed and has been opened at least once."
        )

    # Extract raw strings from all LevelDB files
    raw = ""
    for f in path.iterdir():
        if f.is_file():
            try:
                result = subprocess.run(
                    ["strings", str(f)],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                raw += result.stdout
            except (subprocess.TimeoutExpired, OSError):
                continue

    # Find JWT tokens
    jwt_pattern = re.compile(r"eyJ[A-Za-z0-9_-]+\.eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+")
    raw_tokens = set(jwt_pattern.findall(raw))

    tokens = []
    for token in raw_tokens:
        try:
            # Decode payload (second segment)
            payload_b64 = token.split(".")[1]
            # Add padding
            padding = 4 - len(payload_b64) % 4
            if padding != 4:
                payload_b64 += "=" * padding
            payload = json.loads(base64.urlsafe_b64decode(payload_b64))

            tokens.append({
                "token": token,
                "iat": payload.get("iat", 0),
                "exp": payload.get("exp", 0),
                "nickname": payload.get("nickname", ""),
                "uuid": payload.get("uuid", ""),
            })
        except (json.JSONDecodeError, ValueError, IndexError):
            continue

    tokens.sort(key=lambda t: t["iat"], reverse=True)
    return tokens


def get_best_token(leveldb_path: Path | None = None) -> str | None:
    """Get the most recently issued valid JWT token."""
    import time

    tokens = extract_tokens(leveldb_path)
    now = int(time.time())

    for t in tokens:
        if t["exp"] > now:
            return t["token"]

    # If no unexpired token, return most recent anyway
    return tokens[0]["token"] if tokens else None


if __name__ == "__main__":
    import time

    tokens = extract_tokens()
    now = int(time.time())

    if not tokens:
        print("No FlowUS JWT tokens found.")
    else:
        print(f"Found {len(tokens)} token(s):\n")
        for i, t in enumerate(tokens, 1):
            expired = "EXPIRED" if t["exp"] < now else "valid"
            print(f"  [{i}] nickname={t['nickname']}, iat={t['iat']}, exp={t['exp']} ({expired})")
            print(f"      {t['token'][:60]}...")
        print(f"\nBest token: #{1} (most recent, {'valid' if tokens[0]['exp'] > now else 'EXPIRED'})")
