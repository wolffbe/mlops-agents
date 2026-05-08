"""`prepare-task` CLI entry point.

Replaces `scripts/prepare_task.sh`. Validates Kaggle creds, confirms the
competition is in MLE-bench's registry, runs `mlebench prepare`, surfaces a
clean error if rules aren't accepted, and patches the Git-LFS leaderboard.csv
pointer that ships with mlebench.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _force_kaggle_config() -> Path:
    """Strip stale env vars and prefer the project-local kaggle.json."""
    for stale in ("KAGGLE_USERNAME", "KAGGLE_KEY"):
        os.environ.pop(stale, None)
    if (ROOT / ".kaggle").is_dir():
        os.environ["KAGGLE_CONFIG_DIR"] = str(ROOT / ".kaggle")
    cfg_dir = Path(os.environ.get("KAGGLE_CONFIG_DIR", Path.home() / ".kaggle"))
    return cfg_dir


def cli_prepare_task() -> None:
    ap = argparse.ArgumentParser(
        description="Download and prepare one MLE-bench competition."
    )
    ap.add_argument("task", help="competition slug")
    args = ap.parse_args()
    task = args.task

    data_dir = Path(
        os.environ.get("MLE_BENCH_DATA_DIR", ROOT / ".local" / "mle-bench-data")
    )
    data_dir.mkdir(parents=True, exist_ok=True)

    cfg_dir = _force_kaggle_config()
    if not (cfg_dir / "kaggle.json").exists() and not (Path.home() / ".kaggle" / "kaggle.json").exists():
        sys.stderr.write(
            "Kaggle credentials missing.\n\n"
            "Place a kaggle.json (legacy format: {\"username\":\"...\",\"key\":\"...\"}) at one of:\n"
            f"  - {ROOT}/.kaggle/kaggle.json   (project-local, gitignored)\n"
            f"  - {Path.home()}/.kaggle/kaggle.json   (global)\n\n"
            "Get it from kaggle.com -> Settings -> API -> \"Create New Token\".\n"
            "chmod 600 the file after placing it.\n\n"
            f"Also accept the competition rules at:\n"
            f"  https://www.kaggle.com/c/{task}/rules\n"
        )
        sys.exit(1)

    prepared = data_dir / task / "prepared" / "public"
    if prepared.is_dir() and any(prepared.iterdir()):
        print(f"{task} already prepared at {prepared}, skipping download")
        _patch_lfs_leaderboard(task)
        print(f"{task} ready at {prepared}")
        return

    # Pre-flight: in MLE-bench's registry?
    import mlebench  # noqa: WPS433

    mle_dir = Path(mlebench.__file__).parent
    if not (mle_dir / "competitions" / task).is_dir():
        sys.stderr.write(
            f"ERROR: competition \"{task}\" is not in MLE-bench's registry.\n\n"
            f"Available competitions live under:\n  {mle_dir / 'competitions'}/\n\n"
            "If you need this slug, add a competition definition there or pick one\n"
            "from the existing list. A common subset is the 22-competition Lite split\n"
            "(see `make task-s`).\n"
        )
        sys.exit(1)

    print(f"preparing {task} with --data-dir={data_dir}")
    print(f"KAGGLE_CONFIG_DIR={cfg_dir}")

    # Defensive: detect half-baked state from a previous interrupted prepare.
    # mlebench's Kaggle CLI sees an existing zip and skips re-extraction, then
    # the prepare step fails because raw/ wasn't populated. If we see the
    # zip but no usable prepared/public content, wipe and start fresh.
    task_dir = data_dir / task
    zip_path = task_dir / f"{task}.zip"
    if task_dir.is_dir() and zip_path.exists():
        public_ok = prepared.is_dir() and any(prepared.iterdir())
        if not public_ok:
            print(
                f"warning: detected half-prepared state for {task} "
                f"(zip present, prepared/public empty); wiping and restarting"
            )
            import shutil as _shutil
            _shutil.rmtree(task_dir)

    # Run `mlebench prepare` with stdin closed so its interactive ToS prompt
    # can't deadlock. Capture output so we can detect rules-not-accepted.
    def _run_prepare() -> subprocess.CompletedProcess:
        return subprocess.run(
            ["uv", "run", "mlebench", "prepare", "-c", task, "--data-dir", str(data_dir)],
            cwd=str(ROOT), stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )

    proc = _run_prepare()
    sys.stdout.write(proc.stdout)
    sys.stdout.flush()

    # If prepare failed AND the failure looks like a stale-zip / extraction
    # problem (not ToS), wipe and try once more.
    if proc.returncode != 0 and "must accept this competition" not in proc.stdout:
        wipe_signals = (
            "Skipping, found more recently modified",
            "No such file or directory",
            "raw/train",
            "raw/test",
        )
        if any(sig in proc.stdout for sig in wipe_signals) and task_dir.exists():
            print(
                f"warning: prepare failed mid-extract; wiping {task_dir} and retrying once"
            )
            import shutil as _shutil
            _shutil.rmtree(task_dir, ignore_errors=True)
            proc = _run_prepare()
            sys.stdout.write(proc.stdout)
            sys.stdout.flush()

    if proc.returncode != 0:
        if "must accept this competition" in proc.stdout:
            sys.stderr.write(
                f"\nERROR: Kaggle competition rules for \"{task}\" have not been accepted.\n\n"
                f"Open the competition page, scroll to \"Rules\", and click \"I Understand and Accept\":\n"
                f"  https://www.kaggle.com/c/{task}/rules\n\n"
                f"Then re-run `make prepare-task TASK={task}`.\n"
            )
        sys.exit(1)

    if not prepared.is_dir():
        sys.stderr.write(f"prepared data not found at {prepared}\n")
        sys.exit(1)

    _patch_lfs_leaderboard(task)
    print(f"{task} ready at {prepared}")


def _patch_lfs_leaderboard(task: str) -> None:
    """Replace mlebench's git-lfs leaderboard.csv pointer with the real file
    from GitHub's media endpoint. `pip install git+...` doesn't fetch LFS
    objects, so the shipped file is sometimes a 130-byte pointer that breaks
    grading."""
    import mlebench  # noqa: WPS433

    leader_dir = Path(mlebench.__file__).parent / "competitions" / task
    leader = leader_dir / "leaderboard.csv"
    if not leader.exists():
        return
    if leader.read_bytes()[:50].find(b"git-lfs") == -1:
        return
    print("leaderboard.csv is an LFS pointer; downloading real file...")
    url = (
        "https://media.githubusercontent.com/media/openai/mle-bench/main/"
        f"mlebench/competitions/{task}/leaderboard.csv"
    )
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:
            leader.write_bytes(resp.read())
    except Exception as e:  # noqa: BLE001
        print(f"warning: could not patch leaderboard ({e}); grading may fail.")
