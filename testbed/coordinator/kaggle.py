"""Kaggle pre-flight checks for the coordinator.

Probes the Kaggle data-download endpoint with a `Range: bytes=0-0` GET, which
returns 206 if the user has accepted that competition's ToS and 403 with
"must accept" otherwise. Used by `make task-xs` / `make task-s` to bail out
upfront if any required competition is unaccepted.
"""
from __future__ import annotations

import argparse
import base64
import os
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _authenticate() -> tuple[str, str]:
    """Return (username, key) using the project's .kaggle/kaggle.json. Strips
    any stale KAGGLE_USERNAME / KAGGLE_KEY env vars so the legacy file always
    wins (the access-token format isn't supported by mlebench's pinned
    kaggle 1.6.17)."""
    for stale in ("KAGGLE_USERNAME", "KAGGLE_KEY"):
        os.environ.pop(stale, None)
    if (ROOT / ".kaggle").is_dir():
        os.environ["KAGGLE_CONFIG_DIR"] = str(ROOT / ".kaggle")
    from kaggle.api.kaggle_api_extended import KaggleApi  # noqa: WPS433

    api = KaggleApi()
    api.authenticate()
    cfg = api.config_values
    return cfg["username"], cfg["key"]


def probe(slug: str, auth: str) -> tuple[str, bool, str]:
    url = f"https://www.kaggle.com/api/v1/competitions/data/download-all/{slug}"
    req = urllib.request.Request(
        url,
        headers={"Authorization": f"Basic {auth}", "Range": "bytes=0-0"},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            resp.read(1)
            return slug, True, f"HTTP {resp.status}"
    except urllib.error.HTTPError as e:
        body = (e.read() or b"")[:300].decode("utf-8", errors="replace")
        accepted = "must accept" not in body.lower()
        return slug, accepted, f"HTTP {e.code} {body[:120]}"
    except Exception as e:  # noqa: BLE001
        return slug, False, f"{type(e).__name__}: {e}"


def cli_check_kaggle_rules() -> None:
    ap = argparse.ArgumentParser(
        description="Pre-flight check that every Kaggle competition slug has "
        "its ToS accepted. Exits 0 on all-accepted; 2 with a remediation "
        "list otherwise."
    )
    ap.add_argument("slugs", nargs="*", help="competition slugs (or pass via stdin)")
    args = ap.parse_args()

    slugs = args.slugs
    if not slugs and not sys.stdin.isatty():
        slugs = [s.strip() for s in sys.stdin.read().split() if s.strip()]
    if not slugs:
        ap.error("provide at least one competition slug")

    username, key = _authenticate()
    auth = base64.b64encode(f"{username}:{key}".encode()).decode()

    not_accepted: list[str] = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        futs = {pool.submit(probe, s, auth): s for s in slugs}
        for fut in as_completed(futs):
            slug, ok, info = fut.result()
            if ok:
                print(f"  ok      {slug}")
            else:
                print(f"  REJECT  {slug}  ({info})")
                not_accepted.append(slug)

    if not_accepted:
        print()
        print("ERROR: the following competitions need their rules accepted:")
        for s in sorted(not_accepted):
            print(f"  https://www.kaggle.com/c/{s}/rules")
        print()
        print("Accept each, then re-run the make target.")
        sys.exit(2)
