"""
JV PureMP3 — Windows Detached Updater
======================================
This script is spawned as a DETACHED subprocess by the main application when a
new version is detected.  It waits for the old process to terminate, overwrites
the executable on disk, and relaunches the application.

Usage (called internally by mp3_downloader.py):
    python updater.py <old_pid> <new_exe_path> <target_exe_path>
"""

import os
import sys
import time
import shutil
import ctypes
import subprocess

def is_process_running(pid: int) -> bool:
    """Check if a process with the given PID is still alive on Windows."""
    try:
        import psutil  # optional; fall back to tasklist if unavailable
        return psutil.pid_exists(pid)
    except ImportError:
        # Fallback: use tasklist
        output = subprocess.check_output(
            ["tasklist", "/FI", f"PID eq {pid}"],
            stderr=subprocess.DEVNULL
        ).decode(errors="ignore")
        return str(pid) in output


def wait_for_process_exit(pid: int, timeout: int = 30) -> bool:
    """Block until the target PID exits or timeout (seconds) is reached."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not is_process_running(pid):
            return True
        time.sleep(0.5)
    return False


def main():
    if len(sys.argv) < 4:
        print("Usage: updater.py <old_pid> <new_exe_path> <target_exe_path>")
        sys.exit(1)

    old_pid        = int(sys.argv[1])
    new_exe_path   = sys.argv[2]   # Freshly downloaded .exe (temp location)
    target_exe_path = sys.argv[3]  # Final installed path to overwrite

    print(f"[Updater] Waiting for PID {old_pid} to exit...")
    exited = wait_for_process_exit(old_pid, timeout=60)

    if not exited:
        print(f"[Updater] WARNING: PID {old_pid} did not exit in time. Forcing update anyway.")

    # Give a small grace period after process exit
    time.sleep(1.0)

    # Back up the old binary just in case
    backup_path = target_exe_path + ".bak"
    try:
        if os.path.exists(backup_path):
            os.remove(backup_path)
        if os.path.exists(target_exe_path):
            shutil.copy2(target_exe_path, backup_path)
            print(f"[Updater] Backup saved → {backup_path}")
    except Exception as e:
        print(f"[Updater] WARNING: Could not create backup: {e}")

    # Overwrite the old binary
    try:
        shutil.move(new_exe_path, target_exe_path)
        print(f"[Updater] Update applied → {target_exe_path}")
    except Exception as e:
        print(f"[Updater] ERROR: Failed to replace binary: {e}")
        # Attempt to restore from backup
        if os.path.exists(backup_path):
            shutil.copy2(backup_path, target_exe_path)
            print("[Updater] Restored from backup.")
        sys.exit(1)

    # Remove backup on success
    try:
        if os.path.exists(backup_path):
            os.remove(backup_path)
    except Exception:
        pass

    # Relaunch the updated application detached
    try:
        subprocess.Popen(
            [target_exe_path],
            creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
            close_fds=True
        )
        print("[Updater] Application relaunched successfully.")
    except Exception as e:
        print(f"[Updater] WARNING: Could not relaunch: {e}")


if __name__ == "__main__":
    main()
