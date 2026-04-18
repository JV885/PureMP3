"""
╔══════════════════════════════════════════════════════════════════════╗
║            JV PureMP3  —  v1.1.0                               ║
║          Your Pure Audio Pipeline. | JV Labs                   ║
╚══════════════════════════════════════════════════════════════════════╝
Dependencies:
    pip install yt-dlp customtkinter mutagen requests"""

import os
import re
import sys
import time
import threading
import pygame
import queue
import shutil
import subprocess
import webbrowser
from datetime import datetime
from pathlib import Path
import io
import traceback
import urllib.request
import zipfile
import ctypes
try:
    # Fix for Windows high-DPI scaling issues
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
except Exception:
    pass


import customtkinter as ctk
from tkinter import filedialog, messagebox

# Initialize Audio Engine
try:
    pygame.mixer.init()
except:
    print("Audio device not found. Player will be disabled.")

# ─────────────────────────────────────────────────────────────────────────────
# SELF-HEALING BOOTLOADER (Auto-install missing dependencies)
# ─────────────────────────────────────────────────────────────────────────────
def self_heal():
    """Silently ensures all required packages are present before the UI starts."""
    required = ["yt-dlp", "customtkinter", "mutagen", "musicbrainzngs", "requests"]
    missing = []
    
    for pkg in required:
        try:
            # Map package names to import names if they differ
            import_name = pkg.replace("-", "_")
            __import__(import_name)
        except ImportError:
            missing.append(pkg)
            
    if missing:
        # We use a temporary simple tkinter window instead of a print statement
        # as it's a GUI app.
        try:
            import tkinter as tk
            from tkinter import messagebox
            root = tk.Tk(); root.withdraw()
            subprocess.check_call([sys.executable, "-m", "pip", "install", *missing], 
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            root.destroy()
        except:
            pass # Fallback to silent attempt
            try:
                subprocess.check_call([sys.executable, "-m", "pip", "install", *missing], 
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except:
                pass

# Run self-heal immediately
self_heal()

# ─────────────────────────────────────────────────────────────────────────────
# AUTO-UPDATE ENGINE  (GitHub Releases — Zero-Cost Hosting)
# ─────────────────────────────────────────────────────────────────────────────

# ❗ CONFIGURE THESE TWO CONSTANTS TO MATCH YOUR REPOSITORY
GITHUB_REPO       = "JV885/PureMP3"
APP_VERSION_TAG   = "v1.1.0"                         # Must match the git tag pushed
_PLATFORM         = sys.platform                     # "win32" | "android" | "linux"
_GITHUB_API       = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
_IS_FROZEN        = getattr(sys, 'frozen', False)    # True when bundled by PyInstaller


def _parse_version(tag: str) -> tuple:
    """Convert 'v1.2.3' → (1, 2, 3) for numeric comparison."""
    clean = tag.lstrip('v').split('-')[0]  # strip pre-release labels
    try:
        return tuple(int(x) for x in clean.split('.'))
    except ValueError:
        return (0, 0, 0)


def _download_asset(url: str, dest_path: str, on_progress=None) -> bool:
    """Stream-download an asset from GitHub to dest_path."""
    try:
        import requests
        with requests.get(url, stream=True, timeout=60,
                          headers={"Accept": "application/octet-stream"}) as r:
            r.raise_for_status()
            total = int(r.headers.get('content-length', 0))
            downloaded = 0
            with open(dest_path, 'wb') as f:
                for chunk in r.iter_content(chunk_size=65536):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        if on_progress and total:
                            on_progress(downloaded / total)
        return True
    except Exception as e:
        print(f"[AutoUpdate] Download failed: {e}")
        return False


def check_for_updates(on_update_available=None, on_no_update=None, on_error=None):
    """
    Non-blocking update check.  Fires the appropriate callback on the main thread.
    Call this once from __init__ after the UI is built; it spawns a daemon thread
    and exits immediately, so the UI remains fully responsive.

    Callbacks receive a single dict argument:
        on_update_available(info)  — info keys: latest_tag, download_url, asset_name
        on_no_update(info)         — info keys: current, latest
        on_error(info)             — info keys: error (str)
    """
    def _check():
        try:
            import requests
            resp = requests.get(_GITHUB_API, timeout=10,
                                headers={"User-Agent": "JV-PureMP3-Updater/1.0"})
            resp.raise_for_status()
            data        = resp.json()
            latest_tag  = data.get("tag_name", "")
            assets      = data.get("assets", [])

            if not latest_tag:
                if on_error:
                    on_error({"error": "No release tag found in GitHub API response."})
                return

            local_ver  = _parse_version(APP_VERSION_TAG)
            remote_ver = _parse_version(latest_tag)

            if remote_ver <= local_ver:
                if on_no_update:
                    on_no_update({"current": APP_VERSION_TAG, "latest": latest_tag})
                return

            # --- A newer version exists.  Find the correct asset for this OS ---
            ext_map = {
                "win32":   ".exe",
                "android": ".apk",
                # Linux / macOS would fall here too; extend as needed
            }
            target_ext = ext_map.get(_PLATFORM, ".exe")

            matched_asset = None
            for asset in assets:
                name = asset.get("name", "")
                if name.lower().endswith(target_ext):
                    matched_asset = asset
                    break

            if not matched_asset:
                if on_error:
                    on_error({"error": f"No '{target_ext}' asset found in release {latest_tag}."})
                return

            if on_update_available:
                on_update_available({
                    "latest_tag":    latest_tag,
                    "download_url":  matched_asset["browser_download_url"],
                    "asset_name":    matched_asset["name"],
                    "asset_size":    matched_asset.get("size", 0),
                    "release_notes": data.get("body", ""),
                })

        except Exception as e:
            if on_error:
                on_error({"error": str(e)})

    t = threading.Thread(target=_check, daemon=True, name="AutoUpdateCheck")
    t.start()


def _apply_update_windows(download_url: str, asset_name: str, log_fn=print):
    """
    Windows update flow:
      1. Download the new .exe to a temp location.
      2. Spawn the detached updater.py subprocess.
      3. Exit the current process so the updater can overwrite the binary.
    """
    import tempfile

    tmp_dir     = tempfile.mkdtemp(prefix="jvmp3_update_")
    new_exe     = os.path.join(tmp_dir, asset_name)
    updater_py  = os.path.join(os.path.dirname(os.path.abspath(__file__)), "updater.py")

    log_fn(f"[AutoUpdate] Downloading {asset_name}...")
    ok = _download_asset(download_url, new_exe)
    if not ok:
        log_fn("[AutoUpdate] Download failed — keeping current version.")
        return False

    # Resolve the path to the *current running binary*
    if _IS_FROZEN:
        current_exe = sys.executable          # PyInstaller sets this to the .exe
    else:
        current_exe = os.path.abspath(__file__)  # dev mode: just the .py script

    old_pid = os.getpid()
    log_fn(f"[AutoUpdate] Launching updater (PID target: {old_pid})...")

    # Prefer a standalone updater.exe bundled inside the frozen app if available
    updater_exe = os.path.join(os.path.dirname(sys.executable), "updater.exe") \
        if _IS_FROZEN else None

    if updater_exe and os.path.exists(updater_exe):
        cmd = [updater_exe, str(old_pid), new_exe, current_exe]
    else:
        cmd = [sys.executable, updater_py, str(old_pid), new_exe, current_exe]

    subprocess.Popen(
        cmd,
        creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
        close_fds=True,
    )
    return True  # Caller should then call app.quit() / sys.exit()


def _apply_update_android(download_url: str, asset_name: str, log_fn=print):
    """
    Android update flow:
      1. Download the .apk to the app's public Downloads directory.
      2. Use pyjnius to fire an ACTION_VIEW Intent with the APK MIME type.
         The Android package manager handles the in-place install UI.
    """
    try:
        from android.storage import app_storage_path  # type: ignore  # noqa
        save_dir = app_storage_path()
    except ImportError:
        save_dir = "/sdcard/Download"

    apk_path = os.path.join(save_dir, asset_name)
    log_fn(f"[AutoUpdate] Downloading APK → {apk_path}")
    ok = _download_asset(download_url, apk_path)
    if not ok:
        log_fn("[AutoUpdate] APK download failed.")
        return False

    try:
        from jnius import autoclass  # type: ignore  # noqa
        Intent         = autoclass('android.content.Intent')
        Uri            = autoclass('android.net.Uri')
        PythonActivity = autoclass('org.kivy.android.PythonActivity')
        File           = autoclass('java.io.File')

        intent = Intent(Intent.ACTION_VIEW)
        apk_file = File(apk_path)
        uri = Uri.fromFile(apk_file)
        intent.setDataAndType(uri, "application/vnd.android.package-archive")
        intent.setFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        PythonActivity.mActivity.startActivity(intent)
        log_fn("[AutoUpdate] APK install intent fired.")
        return True
    except Exception as e:
        log_fn(f"[AutoUpdate] Intent failed: {e}")
        return False

try:
    import yt_dlp
except ImportError:
    yt_dlp = None

try:
    from mutagen.id3 import ID3, TIT2, TPE1, ID3NoHeaderError
    from mutagen.mp3 import MP3
    MUTAGEN_OK = True
except ImportError:
    MUTAGEN_OK = False

try:
    import musicbrainzngs
    MB_AVAILABLE = True
    musicbrainzngs.set_useragent(f"PureMP3-Downloader", "1.1.0", "contact: jvcb885@fb")
except ImportError:
    MB_AVAILABLE = False

# ─────────────────────────────────────────────────────────────────────────────
# THEME / PALETTE
# ─────────────────────────────────────────────────────────────────────────────
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

ACCENT_GREEN  = "#00E5A0"
ACCENT_BLUE   = "#4D9EFF"
ACCENT_TEAL   = "#00C4CC"
BG_DARK       = "#0D0F14"
BG_PANEL      = "#12161E"
BG_CARD       = "#1A1F2C"
BG_INPUT      = "#1E2433"
TEXT_PRIMARY  = "#E8EDF5"
TEXT_MUTED    = "#6E7A8A"
TEXT_SUCCESS  = "#00E5A0"
TEXT_ERROR    = "#FF5A72"
TEXT_WARNING  = "#FFB347"
TEXT_INFO     = "#4D9EFF"
BORDER_COLOR  = "#2A3145"

# ─────────────────────────────────────────────────────────────────────────────
# REGEX PATTERNS FOR FILENAME CLEANING
# ─────────────────────────────────────────────────────────────────────────────
CLEAN_PATTERNS = [
    r'\(Official.*?\)',
    r'\[Official.*?\]',
    r'【Official.*?】',
    r'\(Lyrics.*?\)',
    r'\[Lyrics.*?\]',
    r'【Lyrics.*?】',
    r'\(Audio.*?\)',
    r'\[Audio.*?\]',
    r'【Audio.*?】',
    r'\(Video.*?\)',
    r'\[Video.*?\]',
    r'【Video.*?】',
    r'\(Music Video.*?\)',
    r'\[Music Video.*?\]',
    r'【Music Video.*?】',
    r'\(MV\)',
    r'\[MV\]',
    r'\bHD\b',
    r'\b1080p\b',
    r'\b720p\b',
    r'\b4[kK]\b',
    r'Official Audio',
    r'Official Video',
    r'Official Music Video',
    r'Lyric Video',
    r'Full Song',
    r'High Quality',
    r'\bHQ\b',
    r'\s{2,}',  # collapse multiple spaces
]

URL_REGEX = re.compile(
    r'(?:https?://)?(?:www\.)?(?:youtube\.com/watch\?v=|youtu\.be/)(?!XXXXX)([a-zA-Z0-9_-]{11})',
    re.IGNORECASE
)

# ─────────────────────────────────────────────────────────────────────────────
# UTILITY HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def parse_urls(raw_text: str, unique: bool = True) -> list[str]:
    """Extract YouTube URLs from any messy text blob."""
    found = URL_REGEX.findall(raw_text)
    if not unique:
        return [f.rstrip('.,;\'\")') for f in found]
    
    seen = set()
    res = []
    for url in found:
        url = url.rstrip('.,;\'\")')
        if url not in seen:
            seen.add(url); res.append(url)
    return res


def clean_filename(title: str) -> str:
    """Strip clutter words / tags from a YouTube video title."""
    result = title
    for pattern in CLEAN_PATTERNS:
        result = re.sub(pattern, '', result, flags=re.IGNORECASE)
    result = result.strip(' -–—|_')
    result = re.sub(r'\s{2,}', ' ', result)
    return result


def strip_non_ascii(text: str) -> str:
    """Removes emojis, symbols, and non-ASCII characters from text."""
    # Encode to ASCII and ignore errors to strip non-ascii characters
    # Then decode back to string
    return text.encode('ascii', 'ignore').decode('ascii').strip()


def safe_filename(name: str) -> str:
    """Rigorous sanitization for Windows and hardware firmware compatibility."""
    # Length Clipping to 60 chars (hardware display limit)
    res = name
    if len(name) > 60:
        res = name[:57].strip() + "..."
    
    # Case Normalization (Title Case)
    res = res.title()

    # Strip illegal: : * ? " < > | / \
    s = re.compile(r'[:*?"<>|/\\+]').sub('', res)
    # Strip leading/trailing dots and spaces
    s = s.strip('. ')
    return s if s else "Track"


def fmt_bytes(n: float) -> str:
    if n < 0:
        return "—"
    for unit in ('B', 'KB', 'MB', 'GB'):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"
    
def fmt_views(n: int) -> str:
    """Format large numbers into 1.2B, 300M, etc."""
    if n < 0: return "—"
    if n >= 1_000_000_000:
        return f"{n / 1_000_000_000:.1f}B".replace(".0B", "B")
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M".replace(".0M", "M")
    if n >= 1_000:
        return f"{n / 1_000:.1f}K".replace(".0K", "K")
    return str(n)


def fmt_time(seconds: float) -> str:
    if seconds < 0 or seconds != seconds:  # NaN guard
        return "—"
    seconds = int(seconds)
    h, r = divmod(seconds, 3600)
    m, s = divmod(r, 60)
    if h:
        return f"{h}h {m:02d}m {s:02d}s"
    if m:
        return f"{m}m {s:02d}s"
    return f"{s}s"


def inject_metadata(filepath: str, title: str, artist: str):
    """Write ID3 Title + Artist tags to an MP3 file via mutagen."""
    if not MUTAGEN_OK:
        return
    try:
        try:
            tags = ID3(filepath)
        except ID3NoHeaderError:
            tags = ID3()
        tags["TIT2"] = TIT2(encoding=3, text=title)
        tags["TPE1"] = TPE1(encoding=3, text=artist)
        tags.save(filepath)
    except Exception:
        pass  # metadata failure is non-fatal


def parse_artist_from_title(title: str) -> str:
    """Best-effort: pull artist from 'Artist - Song' format."""
    if ' - ' in title:
        return title.split(' - ', 1)[0].strip()
    return "Unknown Artist"


# ─────────────────────────────────────────────────────────────────────────────
# DOWNLOAD ENGINE
# ─────────────────────────────────────────────────────────────────────────────

class DownloadEngine:
    """Wraps yt-dlp and posts progress events to a queue consumed by the UI."""

    MAX_RETRIES = 3

    def __init__(self, urls: list[str], save_folder: str,
                 clean_names: bool, skip_dupes: bool, use_prefix: bool, strip_symbols: bool,
                 author_first: bool,
                 log_queue: queue.Queue, ffmpeg_path: str = ""):
        self.urls        = urls
        self.save_folder = save_folder
        self.clean_names = clean_names
        self.skip_dupes  = skip_dupes
        self.use_prefix  = use_prefix
        self.strip_symbols = strip_symbols
        self.author_first = author_first
        self.ffmpeg_path = ffmpeg_path
        self.q           = log_queue
        self._stop       = threading.Event()

        self.results     = []   # list of dicts: {url, status, title, size, elapsed}
        self.total_bytes = 0.0
        self._dl_logged  = False # Tracking flag for once-per-file logging

    def stop(self):
        self._stop.set()

    # ── internal helpers ──────────────────────────────────────────────────────

    def _post(self, mtype: str, **kwargs):
        self.q.put({"type": mtype, **kwargs})

    def _progress_hook(self, d):
        if self._stop.is_set():
            raise yt_dlp.utils.DownloadCancelled("User cancelled")

        status = d.get("status")
        if status == "downloading":
            if not self._dl_logged:
                self._post("log", level="info", msg="📥  Downloading: Receiving audio stream...")
                self._dl_logged = True

            pct_str   = d.get("_percent_str", "0%").strip()
            speed     = d.get("speed") or 0
            eta       = d.get("eta") or -1
            down      = d.get("downloaded_bytes") or 0
            total     = d.get("total_bytes") or d.get("total_bytes_estimate") or -1

            # Parse percentage
            try:
                pct = float(pct_str.replace('%', ''))
            except ValueError:
                pct = 0.0

            self._post("file_progress",
                       pct=pct,
                       speed=speed,
                       eta=eta,
                       downloaded=down,
                       total=total)

        elif status == "finished":
            # Post-processor handles the next log
            self._post("file_done")

    def _postprocessor_hook(self, d):
        if d['status'] == 'started':
            if d['postprocessor'] == 'FFmpegExtractAudio':
                self._post("log", level="info", msg="🎼  Converting: Extracting high-quality MP3...")
            else:
                self._post("log", level="info", msg=f"⚙️  Processing: Running {d['postprocessor']}...")
        elif d['status'] == 'finished':
            if d['postprocessor'] == 'FFmpegExtractAudio':
                 self._post("log", level="info", msg="✅  Conversion: Done!")

    def _build_ydl_opts(self) -> dict:
        opts = {
            "format": "bestaudio/best",
            "noplaylist": True,  # Don't download entire playlists
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }],
            "ignoreerrors": False,
            "quiet": True,
            "no_warnings": True,
            "nocheckcertificate": True,
            "socket_timeout": 15,
            "progress_hooks": [self._progress_hook],
            "postprocessor_hooks": [self._postprocessor_hook],
            "retries": self.MAX_RETRIES,
            "fragment_retries": self.MAX_RETRIES,
        }
        
        # Priority 1: User-selected FFmpeg path
        if self.ffmpeg_path and os.path.exists(self.ffmpeg_path):
            # If path points to folder, find ffmpeg.exe inside, else use as is
            if os.path.isdir(self.ffmpeg_path):
                exe = os.path.join(self.ffmpeg_path, "ffmpeg.exe")
                opts["ffmpeg_location"] = exe if os.path.exists(exe) else self.ffmpeg_path
            else:
                opts["ffmpeg_location"] = self.ffmpeg_path
        # Priority 2: local ffmpeg.exe in script folder or bin subfolder
        script_dir = os.path.dirname(os.path.abspath(__file__))
        checks = [
            os.path.join(script_dir, "ffmpeg.exe"),
            os.path.join(script_dir, "bin", "ffmpeg.exe")
        ]
        for path in checks:
            if os.path.exists(path):
                opts["ffmpeg_location"] = path
                break
            
        return opts

    # ── main batch loop ───────────────────────────────────────────────────────

    def _get_start_index(self) -> int:
        """Finds the highest existing 00 - prefix in the folder to continue numbering."""
        if not self.use_prefix or not os.path.exists(self.save_folder):
            return 1
        
        highest = 0
        pattern = re.compile(r'^(\d{2,}) - ')
        try:
            for f in os.listdir(self.save_folder):
                match = pattern.match(f)
                if match:
                    num = int(match.group(1))
                    if num > highest:
                        highest = num
        except:
            pass
        return highest + 1

    def run(self):
        total = len(self.urls)
        success = 0; failed = 0; skipped = 0
        times = []
        start_batch = time.time()
        self._post("batch_start", total=total)

        # START INDEX FROM FOLDER DISCOVERY
        next_prefix = self._get_start_index()

        for i, url in enumerate(self.urls, 0):
            display_idx = i + 1 # For batch progress display
            
            if self._stop.is_set(): break
            self._post("log", level="info", msg=f"🔎 Engine: Resolving Identity for '{url}'...")
            # 1. FETCH METADATA PRE-DOWNLOAD
            t0 = time.time()
            core_title = "Unknown"
            artist_meta = "Unknown Artist"
            track_meta = "Unknown"
            try:
                with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "extract_flat": True}) as ydl:
                    info = ydl.extract_info(url, download=False)
                    title_raw = info.get("title", "Unknown Title")
                    
                    # Enhanced Metadata Extraction
                    # 1. Get raw fields
                    artist_raw = info.get("artist") or info.get("uploader", "Unknown Artist").replace(" - Topic", "")
                    track_raw = info.get("track") or clean_filename(title_raw)
                    
                    # 2. Prevent duplication: if track_raw already includes the artist, strip it
                    a_clean = artist_raw.strip()
                    t_clean = track_raw.strip()
                    
                    # Check if artist is already part of the track title string
                    if a_clean.lower() in t_clean.lower():
                        # Specifically look for 'Artist - Title' or 'Title - Artist' patterns
                        # to cleanly remove the artist part.
                        pattern1 = re.compile(re.escape(a_clean) + r'\s*[-–—|_]\s*', re.IGNORECASE)
                        pattern2 = re.compile(r'\s*[-–—|_]\s*' + re.escape(a_clean), re.IGNORECASE)
                        t_clean = pattern1.sub('', t_clean)
                        t_clean = pattern2.sub('', t_clean)
                        
                    # Fallback if stripping made it empty
                    if not t_clean.strip(): t_clean = clean_filename(title_raw)
                    
                    artist_meta = a_clean
                    track_meta = t_clean
                    
                    # 3. Compose final name based on convention
                    if self.author_first:
                        composed = f"{artist_meta} - {track_meta}"
                    else:
                        composed = f"{track_meta} - {artist_meta}"
                        
                    core_title = safe_filename(strip_non_ascii(clean_filename(composed)))
                    self._post("log", level="header", msg=f"📊 Identity: {core_title}")
            except:
                core_title = safe_filename(strip_non_ascii(url))

            # 2. DUPLICATE DETECTION
            found_dup = False
            if os.path.exists(self.save_folder):
                core_lower = core_title.lower()
                for f in os.listdir(self.save_folder):
                    if f.lower().endswith(".mp3"):
                        local_clean = re.sub(r'^(\d+\s*-\s*)+', '', f.lower())
                        if core_lower in local_clean:
                            found_dup = True
                            break
            
            if found_dup:
                reason = "Already exists in target folder"
                self._post("log", level="warning", msg=f"  ⏭ Already Archived: '{core_title}' (Skipping)")
                skipped += 1
                self._post("track_skip", idx=display_idx, total=total, success=success, failed=failed, skipped=skipped, title=core_title, reason=reason)
                continue

            self._dl_logged = False # Reset for new track
            self._post("log", level="info", msg="🚀  Initializing Search and Extraction...")
            
            # 3. START DOWNLOAD (Only if it's new)
            ydl_opts = self._build_ydl_opts()
            ydl_opts["outtmpl"] = os.path.join(self.save_folder, "%(title)s.%(ext)s")
            
            dl_success = False; last_err = ""; final_title = "Unknown"
            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(url, download=True)
                    final_title = info.get("title", "Unknown")
                    dl_success = True
            except Exception as e:
                last_err = str(e)

            if dl_success:
                # 4. Clean up and Tag
                mp3_file = ""
                for f in os.listdir(self.save_folder):
                    if f.endswith(".mp3") and final_title[:10] in f:
                        mp3_file = os.path.join(self.save_folder, f)
                        break
                
                if mp3_file and os.path.exists(mp3_file):
                    # Start with clean base name
                    base_name = os.path.basename(mp3_file).replace(".mp3", "")
                    
                    # SAFETY: Strip any existing prefixes to prevent "02 - 01 - Song.mp3"
                    base_name = re.sub(r'^(\d+\s*-\s*)+', '', base_name)

                    if self.clean_names:
                        base_name = clean_filename(base_name)
                    if self.strip_symbols:
                        base_name = strip_non_ascii(base_name)
                    
                    self._post("log", level="info", msg="✨  Metadata: Scrubbing and Injecting ID3 Tags...")
                    # Tag with CLEAN metadata
                    inject_metadata(mp3_file, track_meta, artist_meta)

                    self._post("log", level="info", msg="📁  Finalizing: Organizing folder and renaming...")
                    # 5. Rename with prefix for Filename order
                    final_filename = core_title
                    if self.use_prefix:
                        final_filename = f"{next_prefix:02d} - {core_title}"
                        next_prefix += 1 # ONLY INCREMENT ON ACTIVE NEW DOWNLOAD
                        
                    new_path = os.path.join(self.save_folder, f"{safe_filename(final_filename)}.mp3")
                    if not os.path.exists(new_path) or new_path == mp3_file:
                        if os.path.exists(new_path) and new_path != mp3_file:
                            os.remove(new_path) # Overwrite if logic creates same name
                        os.rename(mp3_file, new_path)
                        mp3_file = new_path
                    
                    fsize = os.path.getsize(mp3_file)
                    self.total_bytes += fsize
                else: fsize = 0

                success += 1; elapsed = time.time() - t0; times.append(elapsed)
                self._post("log", level="success", msg=f"  ✔ New Download: {final_title}")
                self._post("track_success", idx=display_idx, total=total, success=success, failed=failed, skipped=skipped, 
                           batch_eta=(sum(times)/len(times))*(total-i-1),
                           fsize=fsize, track_elapsed=elapsed, file_path=new_path, title=final_title)
            else:
                failed += 1
                err_msg = last_err if last_err else "Extraction or Network Error"
                self._post("log", level="error", msg=f"  ✖ Failed: {url[:30]}...")
                self._post("track_fail", idx=display_idx, total=total, success=success, failed=failed, skipped=skipped, title=core_title, error=err_msg)

        self._post("batch_done", total=total, success=success, failed=failed, skipped=skipped, 
                   total_bytes=self.total_bytes, elapsed=time.time()-start_batch)

        elapsed_total = time.time() - start_batch
        self._post("batch_done",
                   total=total, success=success, failed=failed, skipped=skipped,
                   total_bytes=self.total_bytes, elapsed=elapsed_total)


# ─────────────────────────────────────────────────────────────────────────────
# MAIN APPLICATION WINDOW
# ─────────────────────────────────────────────────────────────────────────────

class BisayaMusicHubApp(ctk.CTk):

    APP_TITLE   = "JV PureMP3"
    APP_VERSION = APP_VERSION_TAG          # Keep in sync with the global tag constant
    MIN_W, MIN_H = 900, 780

    def __init__(self):
        super().__init__()

        self.title(f"{self.APP_TITLE}  {self.APP_VERSION}")
        self.geometry("980x800")
        self.minsize(self.MIN_W, self.MIN_H)
        self.configure(fg_color=BG_DARK)
        
        # Center main window
        self._center_window(self, 980, 800)
        
        # Windows Taskbar Icon Fix (Set AppUserModelID)
        try:
            myappid = f'jvlabs.puremp3.downloader.{self.APP_VERSION}'
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(myappid)
        except Exception:
            pass

        self._set_icon()

        # State
        self._engine: DownloadEngine | None = None
        self._dl_thread: threading.Thread | None = None
        self._running = False
        self._log_queue: queue.Queue = queue.Queue()
        self._batch_start_time: float = 0
        self._library_modal = None
        self._naming_var = ctk.BooleanVar(value=False)

        # Build UI
        self._build_ui()

        # Start queue poller
        self.after(50, self._poll_queue)

        # ── Kick off non-blocking update check on startup ─────────────────────
        self.after(3000, self._start_update_check)   # 3 s delay so UI is fully visible

    def _center_window(self, window, width, height):
        """Calculates and sets the geometry to center the window on the screen."""
        window.update_idletasks()
        try:
            screen_width = window.winfo_screenwidth()
            screen_height = window.winfo_screenheight()
            x = (screen_width // 2) - (width // 2)
            y = (screen_height // 2) - (height // 2)
            # Ensure window is not off-screen on the bottom
            y = max(10, min(y, screen_height - height - 40))
            window.geometry(f"{width}x{height}+{x}+{y}")
            # Set modal icon to match main app instead of generic blue icon
            self._set_modal_icon(window)
        except:
             window.geometry(f"{width}x{height}")

    def _set_modal_icon(self, window):
        """Helper to apply the app icon to Toplevel windows."""
        try:
            script_dir = os.path.dirname(os.path.abspath(__file__))
            icon_path = os.path.join(script_dir, "asset", "icon.ico")
            if os.path.exists(icon_path):
                window.after(200, lambda: window.iconbitmap(icon_path))
        except:
            pass

    # ── icon (graceful fail) ──────────────────────────────────────────────────

    def _set_icon(self):
        try:
            from PIL import Image, ImageTk
            script_dir = os.path.dirname(os.path.abspath(__file__))
            ico_path = os.path.join(script_dir, "asset", "icon.ico")
            png_path = os.path.join(script_dir, "asset", "icon.png")
            
            # Windows Native Icon (Crisp Taskbar)
            if os.name == 'nt' and os.path.exists(ico_path):
                # iconbitmap uses the .ico file with standard Windows scaling
                self.after(200, lambda: self.iconbitmap(ico_path))
            
            # High-Res Window Icon (Manual Resampling to prevent blur)
            if os.path.exists(png_path):
                img = Image.open(png_path).convert("RGBA")
                # Pre-calculate a high-quality 48x48 version for Tkinter
                # This prevents Tkinter from using its blurry internal scaler
                img_std = img.resize((48, 48), Image.Resampling.LANCZOS)
                photo = ImageTk.PhotoImage(img_std)
                
                # Apply to all windows
                self.after(500, lambda: self.iconphoto(True, photo))
                self._icon_image = photo 
        except Exception:
            pass

    # ─────────────────────────────────────────────────────────────────────────
    # UI CONSTRUCTION
    # ─────────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        self._build_header()
        
        body = ctk.CTkFrame(self, fg_color=BG_DARK, corner_radius=0)
        body.grid(row=1, column=0, sticky="nsew", padx=2, pady=2)
        self._build_body(body)

        self._build_footer() # ADD FOOTER

    # ── Header ────────────────────────────────────────────────────────────────

    def _build_header(self):
        hdr = ctk.CTkFrame(self, fg_color=BG_PANEL, corner_radius=0, height=80,
                           border_width=0)
        hdr.grid(row=0, column=0, sticky="ew")
        hdr.grid_columnconfigure(1, weight=1)
        hdr.grid_propagate(False)

        # Gradient-like title using nested labels
        title_wrapper = ctk.CTkFrame(hdr, fg_color="transparent")
        title_wrapper.grid(row=0, column=0, padx=24, pady=12, sticky="w")

        # Try to load logo image
        try:
            from PIL import Image
            script_dir = os.path.dirname(os.path.abspath(__file__))
            logo_path = os.path.join(script_dir, "asset", "icon.png")
            if os.path.exists(logo_path):
                raw_img = Image.open(logo_path)
                logo_img = ctk.CTkImage(light_image=raw_img, dark_image=raw_img, size=(48, 48))
                logo_lbl = ctk.CTkLabel(title_wrapper, image=logo_img, text="")
                logo_lbl.grid(row=0, column=0, rowspan=2, padx=(0, 15))
        except:
            pass

        title_frame = ctk.CTkFrame(title_wrapper, fg_color="transparent")
        title_frame.grid(row=0, column=1, rowspan=2, sticky="w")

        ctk.CTkLabel(
            title_frame,
            text="JV PureMP3",
            font=ctk.CTkFont(family="Segoe UI", size=26, weight="bold"),
            text_color=ACCENT_GREEN,
        ).grid(row=0, column=0, sticky="w")

        ctk.CTkLabel(
            title_frame,
            text=f"   {self.APP_VERSION}  —  Your Pure Audio Pipeline.  |  JV Labs",
            font=ctk.CTkFont(family="Segoe UI", size=11),
            text_color=TEXT_MUTED,
        ).grid(row=1, column=0, sticky="w")

        # SUPPORT THE DEVELOPER (Top Right)
        ctk.CTkButton(
            hdr,
            text="👑  SUPPORT THE DEVELOPER",
            font=ctk.CTkFont(size=13, weight="bold"),
            fg_color=ACCENT_BLUE,
            hover_color="#2A74D4",
            height=40,
            corner_radius=10,
            command=lambda: webbrowser.open("https://ko-fi.com/jvlabs2026")
        ).grid(row=0, column=1, sticky="e", padx=24)

        # Dependency warning if missing
        warns = []
        if yt_dlp is None:
            warns.append("yt-dlp MISSING")
        if not MUTAGEN_OK:
            warns.append("mutagen MISSING")
        if warns:
            ctk.CTkLabel(
                hdr, text="  ⚠  " + ", ".join(warns) + "  —  run: pip install yt-dlp mutagen",
                font=ctk.CTkFont(size=11), text_color=TEXT_WARNING,
            ).grid(row=0, column=1, padx=24, sticky="e")

    def _build_footer(self):
        ftr = ctk.CTkFrame(self, fg_color=BG_PANEL, height=36, corner_radius=0)
        ftr.grid(row=2, column=0, sticky="ew")
        ftr.grid_columnconfigure(1, weight=1)
        
        # Left: Legal Links
        links_frame = ctk.CTkFrame(ftr, fg_color="transparent")
        links_frame.grid(row=0, column=0, padx=20)
        
        for i, (text, tag) in enumerate([("About", "about"), ("Terms", "terms"), ("Privacy", "privacy")]):
            btn = ctk.CTkButton(
                links_frame, text=text, width=60, height=20,
                fg_color="transparent", text_color=TEXT_MUTED,
                hover_color=BG_DARK, font=ctk.CTkFont(size=11),
                command=lambda t=tag: self._show_legal_modal(t)
            )
            btn.grid(row=0, column=i, padx=5)

        # Center: Made with Heart & Socials
        center_frame = ctk.CTkFrame(ftr, fg_color="transparent")
        center_frame.grid(row=0, column=1)

        ctk.CTkLabel(
            center_frame, text="Made with ❤️ by JV Labs  | ",
            font=ctk.CTkFont(size=10), text_color=TEXT_MUTED
        ).grid(row=0, column=0)

        socials = [
            ("FB", "https://www.facebook.com/jvcb885"),
            ("LI", "https://www.linkedin.com/in/jvcb885/"),
            ("PF", "https://portfolio-jvlabs.pages.dev/")
        ]
        for i, (name, url) in enumerate(socials):
            btn = ctk.CTkButton(
                center_frame, text=name, width=28, height=18,
                fg_color="transparent", text_color=ACCENT_BLUE,
                hover_color=BG_DARK, font=ctk.CTkFont(size=10, weight="bold"),
                command=lambda u=url: webbrowser.open(u)
            )
            btn.grid(row=0, column=i+1, padx=2)

        # Center-Right: Update Core Engine Button
        ctk.CTkButton(
            ftr,
            text="🔄  REFRESH CORE ENGINE",
            width=140, height=22,
            font=ctk.CTkFont(size=10, weight="bold"),
            fg_color=BG_INPUT,
            hover_color="#2A74D4",
            text_color=ACCENT_BLUE,
            corner_radius=6,
            command=self._update_core_engine
        ).grid(row=0, column=1, sticky="e", padx=(0, 110))

        # Right: Status Placeholder
        self._status_lbl = ctk.CTkLabel(
            ftr, text="   Ready   ",
            font=ctk.CTkFont(size=10, weight="bold"), text_color=ACCENT_GREEN
        )
        self._status_lbl.grid(row=0, column=2, padx=20)

    def _show_legal_modal(self, category):
        """Displays legal and ethical information in a modal window."""
        titles = {"about": "📘 About JV PureMP3", "terms": "🛡️ Terms of Use", "privacy": "🔒 Privacy Policy"}
        contents = {
            "about": "JV PureMP3 is a premium, open-source 'Audio Pipeline' engineered for precision and speed. "
                     "It serves as a bridge between the vast digital world of streaming and the timeless reliability of offline media.\n\n"
                     "🚀 OUR MISSION\n"
                     "To provide a seamless, high-performance tool for audiophiles and car enthusiasts to curate high-quality "
                     "audio libraries for offline hardware, ensuring that music is accessible even in the most remote locations.\n\n"
                     "🌐 OUR VISION\n"
                     "A future where digital media management is completely private, local, and user-centric, empowering "
                     "individuals to own and manage their personal archives without reliance on proprietary cloud systems.\n\n"
                     "💡 NOTE: This application is provided completely FREE of charge for personal use.",
            "terms": "1. PERSONAL ARCHIVAL ONLY: This software is strictly for personal, non-commercial archival purposes. "
                     "Users are authorized to maintain copies for private use only. Commercial redistribution is strictly prohibited.\n\n"
                     "2. RESPECT COPYRIGHT: You are legally responsible for the content you process. Ensure you possess "
                     "appropriate licenses or permissions from copyright holders before extraction.\n\n"
                     "3. SOFTWARE FIDELITY: Provided 'as is'. While we strive for perfection, developers are not liable "
                     "for technical issues, platform-specific limits, or misuse of this tool.\n\n"
                     "4. NO REDISTRIBUTION: Do not sell or charge others for access to this software.",
            "privacy": "1. ABSOLUTE PRIVACY: Your habits are your business. JV PureMP3 does not collect, store, or transmit any user data.\n\n"
                       "2. TRUE LOCAL PROCESSING: Every extraction and metadata wash happens directly on your machine. "
                       "No external servers are involved in the processing of your media.\n\n"
                       "3. NO TRACKING: We do not use cookies, pixels, or telemetry. Our app is a silent utility that stays "
                       "within your local environment.\n\n"
                       "4. OPEN INTEGRITY: What you see in the source is exactly what runs on your machine."
        }
        
        modal = ctk.CTkToplevel(self)
        modal.title(titles.get(category))
        self._center_window(modal, 550, 500)
        modal.update_idletasks()
        modal.lift()
        modal.attributes("-topmost", True)
        
        # Click outside to close implementation (with delay to prevent "flash")
        def bind_focus():
            if modal.winfo_exists():
                 modal.bind("<FocusOut>", lambda e: self._on_modal_focus_out(e, modal))
        modal.after(500, bind_focus)
        
        ctk.CTkLabel(modal, text=titles.get(category), font=ctk.CTkFont(size=18, weight="bold"), 
                     text_color=ACCENT_GREEN).pack(pady=(20, 10))
        
        txt = ctk.CTkTextbox(modal, width=480, height=350, font=ctk.CTkFont(size=12))
        txt.insert("0.0", contents.get(category))
        txt.configure(state="disabled")
        txt.pack(pady=10, padx=20)
        
        ctk.CTkButton(modal, text="Close", command=modal.destroy, fg_color=BG_PANEL, 
                      hover_color=BG_DARK, text_color=TEXT_PRIMARY).pack(pady=10)
        
        modal.after(100, modal.focus_force)

    def _on_modal_focus_out(self, event, modal):
        """Safely closes the modal if focus moves to another window."""
        if not modal.winfo_exists(): return
        # Small delay to let focus stabilize
        modal.after(100, lambda: self._check_should_close(modal))

    def _check_should_close(self, modal):
        if modal.winfo_exists():
            focused = modal.focus_get()
            # If focus is now outside this modal (or on nothing)
            if not focused or not str(focused).startswith(str(modal)):
                modal.destroy()

    # ── Body (two-column layout) ──────────────────────────────────────────────

    def _build_body(self, parent):
        parent.grid_columnconfigure(0, weight=2, minsize=340)
        parent.grid_columnconfigure(1, weight=3)
        parent.grid_rowconfigure(0, weight=1)

        self._build_left_panel(parent)
        self._build_right_panel(parent)

    # ── LEFT PANEL ────────────────────────────────────────────────────────────

    def _build_left_panel(self, parent):
        # Container frame for the left side
        left_container = ctk.CTkFrame(parent, fg_color="transparent")
        left_container.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        left_container.grid_columnconfigure(0, weight=1)
        left_container.grid_rowconfigure(0, weight=1) # Scroll area expands

        # ── Scrollable Body ───────────────────────────────────────────────────
        left = ctk.CTkScrollableFrame(left_container, fg_color=BG_PANEL,
                            corner_radius=14, border_width=1,
                            border_color=BORDER_COLOR, label_text="")
        left.grid(row=0, column=0, sticky="nsew")
        left.grid_columnconfigure(0, weight=1)
        # Session Tracking
        self._current_session_files = [] 
        self._is_shuffle = False

        # Analytics Tracking
        self._done_titles = []
        self._failed_titles = []
        self._skipped_titles = []
        # We don't need grid_rowconfigure weight here because the scrollable frame 
        # manages its own children. But for the internal textbox, we'll give it a min_height.

        row = 0

        # ── Save Folder ───────────────────────────────────────────────────────
        ctk.CTkLabel(left, text="SAVE FOLDER",
                     font=ctk.CTkFont(size=10, weight="bold"),
                     text_color=ACCENT_BLUE).grid(
            row=row, column=0, sticky="w", padx=18, pady=(18, 2))
        row += 1

        folder_frame = ctk.CTkFrame(left, fg_color=BG_INPUT, corner_radius=8)
        folder_frame.grid(row=row, column=0, sticky="ew", padx=14, pady=(0, 12))
        folder_frame.grid_columnconfigure(0, weight=1)

        default_music = str(Path.home() / "Music")
        self._folder_var = ctk.StringVar(value=default_music)

        self._folder_entry = ctk.CTkEntry(
            folder_frame,
            textvariable=self._folder_var,
            font=ctk.CTkFont(size=12),
            fg_color="transparent",
            border_width=0,
            text_color=TEXT_PRIMARY,
        )
        self._folder_entry.grid(row=0, column=0, sticky="ew", padx=8, pady=6)

        ctk.CTkButton(
            folder_frame,
            text="📁",
            width=36, height=30,
            fg_color=ACCENT_BLUE,
            hover_color="#2A74D4",
            corner_radius=6,
            command=self._pick_folder,
        ).grid(row=0, column=1, padx=(0, 4), pady=4)
        row += 1

        row += 1

        # ── YouTube Links ─────────────────────────────────────────────────────
        ctk.CTkLabel(left, text="YOUTUBE LINKS",
                     font=ctk.CTkFont(size=10, weight="bold"),
                     text_color=ACCENT_BLUE).grid(
            row=row, column=0, sticky="w", padx=18, pady=(4, 2))
        row += 1

        ctk.CTkLabel(left,
                     text="Paste links (comma-separated, quoted, or one per line)",
                     font=ctk.CTkFont(size=10), text_color=TEXT_MUTED).grid(
            row=row, column=0, sticky="w", padx=18, pady=(0, 4))
        row += 1

        self._url_textbox = ctk.CTkTextbox(
            left,
            fg_color=BG_INPUT,
            border_color=BORDER_COLOR,
            border_width=1,
            text_color=TEXT_PRIMARY,
            font=ctk.CTkFont(family="Consolas", size=11),
            corner_radius=8,
            height=250, # Explicit height for scrollable context
        )
        self._url_textbox.grid(row=row, column=0, sticky="nsew", padx=14, pady=(0, 10))
        self._url_textbox.insert("0.0",
            "# Paste YouTube links here.\n"
            "# Supports:\n"
            "#   https://youtu.be/XXXXX\n"
            "#   https://www.youtube.com/watch?v=XXXXX\n"
            "# Comma-separated, newline, or mixed formats OK.\n"
        )
        row += 1

        # Result count and View Button
        self._last_recordings = [] 
        self._url_count_lbl = ctk.CTkLabel(left, text="0 URLs detected", font=ctk.CTkFont(size=11), text_color=TEXT_MUTED)
        self._url_count_lbl.grid(row=row, column=0, sticky="w", padx=18, pady=(4, 0))
        
        self._view_results_btn = ctk.CTkButton(
            left, text="📋 VIEW DISCOVERY RESULTS", width=160, height=22,
            font=ctk.CTkFont(size=10, weight="bold"), 
            fg_color=ACCENT_BLUE, text_color=BG_DARK, 
            hover_color=ACCENT_TEAL,
            command=self._show_selection_dashboard,
            state="disabled"
        )
        self._view_results_btn.grid(row=row, column=0, sticky="e", padx=16, pady=(4, 0))
        row += 1

        self._url_textbox.bind("<KeyRelease>", self._on_url_change)

        # Paste from clipboard helper
        paste_btn = ctk.CTkButton(
            left, text="📋  Paste from Clipboard",
            height=30, fg_color=BG_CARD,
            border_color=BORDER_COLOR, border_width=1,
            hover_color="#252D40",
            text_color=TEXT_MUTED,
            font=ctk.CTkFont(size=11),
            command=self._paste_clipboard,
        )
        paste_btn.grid(row=row, column=0, sticky="ew", padx=14, pady=(0, 10))
        row += 1

        # ── MODULAR SEARCH ENGINE ─────────────────────────────────────────────
        ctk.CTkLabel(left, text="CUMULATIVE SEARCH ENGINE",
                     font=ctk.CTkFont(size=10, weight="bold"),
                     text_color=ACCENT_TEAL).grid(
            row=row, column=0, sticky="w", padx=18, pady=(4, 2))
        row += 1

        engine_frame = ctk.CTkFrame(left, fg_color=BG_PANEL, corner_radius=12, border_width=1, border_color=BORDER_COLOR)
        engine_frame.grid(row=row, column=0, sticky="ew", padx=12, pady=(0, 10))
        engine_frame.grid_columnconfigure((0, 1), weight=1)

        self._GENRES_MASTER = ["Pop", "Rock", "Hip-Hop", "R&B", "Soul", "Funk", "Reggae", "Dancehall", "Afrobeat", "Latin", "Reggaeton", "Salsa", "Bachata", "Country", "Bluegrass", "Jazz", "Blues", "Classical", "Opera", "EDM", "House", "Techno", "Trance", "Dubstep", "Drum and Bass", "Phonk", "PluggnB", "Hyperpop", "Lo-fi", "Synthwave", "Vaporwave", "Indie", "Alternative", "Punk", "Metal", "Grunge", "Shoegaze", "Ambient", "New Age", "Gospel", "K-Pop", "J-Pop", "P-Pop", "Folk", "World", "Acoustic", "Instrumental", "Soundtrack", "Children's Music", "Holiday"]
        
        # Initialize 6 Modular Variables
        self._title_var  = ctk.StringVar()
        self._author_var = ctk.StringVar()
        self._year_var   = ctk.StringVar()
        self._genre_var  = ctk.StringVar(value="--")
        self._tag_var    = ctk.StringVar(value="[SELECT VERSION]") 
        self._query_var  = ctk.StringVar()

        # Add traces for conditional validation
        for v in [self._title_var, self._author_var, self._year_var, self._genre_var, self._tag_var]:
            v.trace_add("write", lambda *args: self._validate_search_state())

        # UI for first 3 fields (Title, Author, Year)
        field_setup = [
            ("Title (Optional)",  self._title_var,  "e.g. Magellan"),
            ("Author (Optional)", self._author_var, "e.g. Max Surban"),
            ("Year (Optional)",   self._year_var,   "e.g. 1982"),
        ]

        for i, (label, var, hint) in enumerate(field_setup):
            # i=0 (r0c0), i=1 (r0c1), i=2 (r1c0)
            r, c = divmod(i, 2)
            f = ctk.CTkFrame(engine_frame, fg_color="transparent")
            f.grid(row=r, column=c, padx=8, pady=6, sticky="ew")
            f.grid_columnconfigure(0, weight=1)
            ctk.CTkLabel(f, text=label.upper(), font=ctk.CTkFont(size=9, weight="bold"), text_color=TEXT_MUTED).grid(row=0, column=0, sticky="sw", padx=2)
            entry = ctk.CTkEntry(f, textvariable=var, placeholder_text=hint, font=ctk.CTkFont(size=11), height=28, fg_color=BG_INPUT, border_width=0)
            entry.grid(row=1, column=0, sticky="ew")

        # Row 1/2 Column 2: GENRE
        genre_f = ctk.CTkFrame(engine_frame, fg_color="transparent")
        genre_f.grid(row=1, column=1, padx=8, pady=6, sticky="ew")
        genre_f.grid_columnconfigure(0, weight=1)
        
        gl_f = ctk.CTkFrame(genre_f, fg_color="transparent")
        gl_f.grid(row=0, column=0, sticky="sw")
        self._genre_lbl_main = ctk.CTkLabel(gl_f, text="GENRE ", font=ctk.CTkFont(size=9, weight="bold"), text_color=TEXT_MUTED)
        self._genre_lbl_main.pack(side="left")
        self._genre_lbl_req = ctk.CTkLabel(gl_f, text="(REQUIRED)", font=ctk.CTkFont(size=9, weight="bold"), text_color="red")
        self._genre_lbl_req.pack(side="left")
        
        self._genre_menu = ctk.CTkOptionMenu(genre_f, variable=self._genre_var, values=self._GENRES_MASTER, height=28, fg_color=BG_INPUT, font=ctk.CTkFont(size=11), button_color=BG_DARK, dropdown_fg_color=BG_DARK)
        self._genre_menu.grid(row=1, column=0, sticky="ew")

        # Row 3: Tag Selector (Kill-Switch)
        tag_f = ctk.CTkFrame(engine_frame, fg_color="transparent")
        tag_f.grid(row=2, column=0, padx=8, pady=6, sticky="ew")
        tag_f.grid_columnconfigure(0, weight=1)
        
        tl_f = ctk.CTkFrame(tag_f, fg_color="transparent")
        tl_f.grid(row=0, column=0, sticky="sw")
        ctk.CTkLabel(tl_f, text="VERSION TAG ", font=ctk.CTkFont(size=9, weight="bold"), text_color=TEXT_MUTED).pack(side="left")
        ctk.CTkLabel(tl_f, text="(OPTIONAL)", font=ctk.CTkFont(size=9, weight="bold"), text_color=TEXT_MUTED).pack(side="left")

        tag_options = ["Original", "Remix", "Cover", "AI Version"]
        self._tag_menu = ctk.CTkOptionMenu(
            tag_f, variable=self._tag_var, values=tag_options,
            height=28, fg_color=BG_INPUT, button_color=BG_DARK,
            dropdown_fg_color=BG_DARK, font=ctk.CTkFont(size=11),
            command=lambda v: self._validate_search_state()
        )
        self._tag_menu.grid(row=1, column=0, sticky="ew")

        # DISCOVERY COUNT
        self._count_var = ctk.StringVar(value="100") 
        count_f = ctk.CTkFrame(engine_frame, fg_color="transparent")
        count_f.grid(row=2, column=1, padx=8, pady=6, sticky="ew")
        count_f.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(count_f, text="DISCOVERY BATCH", font=ctk.CTkFont(size=9, weight="bold"), text_color=TEXT_MUTED).grid(row=0, column=0, sticky="sw", padx=2)
        
        # Extended batch options (100 to 1000)
        batch_opts = [str(i) for i in range(100, 1100, 100)]
        self._count_menu = ctk.CTkComboBox(
            count_f, variable=self._count_var, values=batch_opts,
            height=28, fg_color=BG_INPUT, button_color=BG_DARK,
            dropdown_fg_color=BG_DARK, font=ctk.CTkFont(size=11),
            border_width=0
        )
        self._count_menu.grid(row=1, column=0, sticky="ew")

        self._search_btn = ctk.CTkButton(
            engine_frame,
            text="🚀   TAG REQUIRED",
            height=34,
            fg_color=BG_INPUT,
            state="disabled",
            text_color=TEXT_MUTED,
            font=ctk.CTkFont(size=12, weight="bold"),
            command=self._on_ai_search,
        )
        self._search_btn.grid(row=3, column=0, columnspan=2, padx=10, pady=(10, 12), sticky="ew")
        row += 1

        # ── Options Toggles ───────────────────────────────────────────────────
        ctk.CTkLabel(left, text="OPTIONS",
                     font=ctk.CTkFont(size=10, weight="bold"),
                     text_color=ACCENT_BLUE).grid(
            row=row, column=0, sticky="w", padx=18, pady=(4, 4))
        row += 1

        opts_frame = ctk.CTkFrame(left, fg_color=BG_CARD, corner_radius=10)
        opts_frame.grid(row=row, column=0, sticky="ew", padx=14, pady=(0, 14))
        opts_frame.grid_columnconfigure(0, weight=1)

        self._clean_var  = ctk.BooleanVar(value=True)
        self._skip_var   = ctk.BooleanVar(value=True)
        self._prefix_var = ctk.BooleanVar(value=False) # Changed to False by default
        self._strip_var  = ctk.BooleanVar(value=True)
        self._autoplay_var = ctk.BooleanVar(value=False)

        for i, (text, var, tip) in enumerate([
            ("🧹  YouTube Metadata Scrub", self._clean_var,
             "Removes [HD], (Official Video), 【Audio】 etc"),
            ("⏭  Skip Duplicates",      self._skip_var,
             "Skip if MP3 with same name already exists"),
            ("🔢  Sequential Numbering", self._prefix_var,
             "Add 01 -, 02 - etc to preserve playlist order"),
            ("✨  Strip Symbols/Emoji", self._strip_var,
             "ASCII-only names for hardware compatibility"),
            ("📏  Length & Case Normalization", ctk.BooleanVar(value=True),
             "Cap at 60 chars + use 'Title Case'"),
            ("🛡️  Character Sanitization", ctk.BooleanVar(value=True),
             "Strict removal of restricted characters (: * ? etc)"),
            ("📻  Autoplay after Download", self._autoplay_var,
             "Automatically play tracks as soon as they finish"),
            ("🏷️  Author - Title Convention", self._naming_var,
             "ON: Artist - Song | OFF: Song - Artist"),
        ]):
            sw = ctk.CTkSwitch(
                opts_frame, text=text,
                variable=var,
                font=ctk.CTkFont(size=12),
                text_color=TEXT_PRIMARY,
                progress_color=ACCENT_GREEN,
                button_color=ACCENT_GREEN,
                button_hover_color="#00BF87",
            )
            sw.grid(row=i, column=0, sticky="w", padx=16, pady=6)

            ctk.CTkLabel(
                opts_frame, text=tip,
                font=ctk.CTkFont(size=10), text_color=TEXT_MUTED,
            ).grid(row=i, column=1, sticky="w", padx=(0, 10), pady=6)
        row += 1



        # ── Action Buttons (Pinned to Bottom) ─────────────────────────────────
        btn_frame = ctk.CTkFrame(left_container, fg_color=BG_PANEL, 
                                 corner_radius=14, border_width=1, 
                                 border_color=BORDER_COLOR)
        btn_frame.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        btn_frame.grid_columnconfigure(0, weight=1)

        self._start_btn = ctk.CTkButton(
            btn_frame,
            text="▶   START DOWNLOAD",
            height=48,
            font=ctk.CTkFont(size=14, weight="bold"),
            fg_color=ACCENT_GREEN,
            hover_color="#00BF87",
            text_color="#001A0F",
            corner_radius=12,
            command=self._on_start,
            state="disabled" # Initial state
        )
        self._start_btn.grid(row=0, column=0, sticky="ew", padx=12, pady=(12, 6))

        self._stop_btn = ctk.CTkButton(
            btn_frame,
            text="⏹   STOP DOWNLOAD",
            height=36,
            font=ctk.CTkFont(size=11, weight="bold"),
            fg_color=BG_DARK,
            hover_color="#2A1F1F",
            text_color=TEXT_ERROR,
            corner_radius=10,
            border_width=1,
            border_color=TEXT_ERROR,
            state="disabled",
            command=self._on_stop,
        )
        self._stop_btn.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 12))

    # ── RIGHT PANEL ───────────────────────────────────────────────────────────

    def _build_right_panel(self, parent):
        right = ctk.CTkFrame(parent, fg_color="transparent")
        right.grid(row=0, column=1, sticky="nsew")
        right.grid_columnconfigure(0, weight=1)
        right.grid_rowconfigure(3, weight=1) # CONSOLE takes the remaining space

        self._build_stats_dashboard(right)
        self._build_progress_section(right)
        self._build_player_section(right)
        self._build_console(right)

    # ── Stats Dashboard ───────────────────────────────────────────────────────

    def _build_stats_dashboard(self, parent):
        dash = ctk.CTkFrame(parent, fg_color=BG_PANEL,
                            corner_radius=14, border_width=1,
                            border_color=BORDER_COLOR)
        dash.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        dash.grid_columnconfigure((0, 1, 2, 3, 4), weight=1)

        stats = [
            ("✔ Done",    "done_lbl",     TEXT_SUCCESS),
            ("✖ Failed",  "fail_lbl",     TEXT_ERROR),
            ("⏭ Skipped", "skip_lbl",     TEXT_WARNING),
            ("⏱ Elapsed", "elapsed_lbl",  ACCENT_BLUE),
            ("⬇ Data",    "data_lbl",     ACCENT_TEAL),
        ]

        for col, (label, attr, color) in enumerate(stats):
            is_clickable = label in ["✔ Done", "✖ Failed", "⏭ Skipped"]
            card = ctk.CTkFrame(dash, fg_color=BG_CARD, corner_radius=10, cursor="hand2" if is_clickable else "")
            card.grid(row=0, column=col, padx=6, pady=10, sticky="nsew")

            val_lbl = ctk.CTkLabel(
                card, text="0",
                font=ctk.CTkFont(size=22, weight="bold"),
                text_color=color,
            )
            val_lbl.grid(row=0, column=0, padx=12, pady=(10, 2))
            setattr(self, f"_{attr}", val_lbl)

            desc_lbl = ctk.CTkLabel(
                card, text=label,
                font=ctk.CTkFont(size=10),
                text_color=TEXT_MUTED,
            )
            desc_lbl.grid(row=1, column=0, padx=12, pady=(0, 10))

            if is_clickable:
                # Bind the entire card AND its children to the click handler
                cmd = lambda _, l=label: self._show_batch_breakdown(l)
                card.bind("<Button-1>", cmd)
                val_lbl.bind("<Button-1>", cmd)
                desc_lbl.bind("<Button-1>", cmd)

        # Second row: ETA / current track
        row2 = ctk.CTkFrame(dash, fg_color="transparent")
        row2.grid(row=1, column=0, columnspan=5, sticky="ew", padx=10, pady=(0, 8))
        row2.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(row2, text="Now:",
                     font=ctk.CTkFont(size=11, weight="bold"),
                     text_color=TEXT_MUTED).grid(row=0, column=0, sticky="w")
        self._now_lbl = ctk.CTkLabel(
            row2, text="—",
            font=ctk.CTkFont(size=11),
            text_color=TEXT_PRIMARY,
            anchor="w",
        )
        self._now_lbl.grid(row=0, column=1, sticky="ew", padx=(6, 16))

        ctk.CTkLabel(row2, text="Batch ETA:",
                     font=ctk.CTkFont(size=11, weight="bold"),
                     text_color=TEXT_MUTED).grid(row=0, column=2, sticky="w")
        self._batch_eta_lbl = ctk.CTkLabel(
            row2, text="—",
            font=ctk.CTkFont(size=11),
            text_color=ACCENT_BLUE,
        )
        self._batch_eta_lbl.grid(row=0, column=3, sticky="w", padx=(6, 0))

    def _show_batch_breakdown(self, category):
        title_map = {
            "✔ Done": ("Successfully Downloaded", self._done_titles, TEXT_SUCCESS),
            "✖ Failed": ("Failed Extractions", self._failed_titles, TEXT_ERROR),
            "⏭ Skipped": ("Already Archived", self._skipped_titles, TEXT_WARNING)
        }
        if category not in title_map: return
        title, tracks, color = title_map[category]

        top = ctk.CTkToplevel(self)
        top.title(f"{category} Overview")
        self._center_window(top, 600, 650)
        top.attributes("-topmost", True)
        top.configure(fg_color=BG_DARK)
        
        # Click outside to close implementation (with delay to prevent "flash")
        def bind_focus():
            if top.winfo_exists():
                 top.bind("<FocusOut>", lambda e: self._on_modal_focus_out(e, top))
        top.after(500, bind_focus)
        
        ctk.CTkLabel(top, text=f"📊 {title.upper()}", font=ctk.CTkFont(size=16, weight="bold"), text_color=color).pack(pady=(20, 5))
        ctk.CTkLabel(top, text=f"{len(tracks)} items in this session", font=ctk.CTkFont(size=11), text_color=TEXT_MUTED).pack(pady=(0, 15))
        
        frame = ctk.CTkScrollableFrame(top, fg_color=BG_PANEL, corner_radius=12, border_width=1, border_color=BORDER_COLOR)
        frame.pack(fill="both", expand=True, padx=20, pady=10)
        
        if not tracks:
            ctk.CTkLabel(frame, text="No tracks recorded in this category yet.", font=ctk.CTkFont(size=12), text_color=TEXT_MUTED).pack(pady=60)
        else:
            for item in tracks:
                item_frame = ctk.CTkFrame(frame, fg_color=BG_CARD, corner_radius=8)
                item_frame.pack(fill="x", pady=4, padx=5)
                
                # Title Column
                t_lbl = ctk.CTkLabel(item_frame, text=item['title'], font=ctk.CTkFont(size=12, weight="bold"), 
                                     text_color=TEXT_PRIMARY, anchor="w", justify="left", wraplength=350)
                t_lbl.pack(side="left", padx=12, pady=10, fill="x", expand=True)
                
                # Detail Column (Reason/Size)
                detail_text = ""
                if category == "✔ Done":
                    detail_text = fmt_bytes(item.get('fsize', 0))
                elif category == "✖ Failed":
                    detail_text = item.get('error', 'Unknown Error')
                elif category == "⏭ Skipped":
                    detail_text = "Already Exists"
                
                d_lbl = ctk.CTkLabel(item_frame, text=detail_text, font=ctk.CTkFont(size=10), 
                                     text_color=color, anchor="e")
                d_lbl.pack(side="right", padx=12, pady=10)

        ctk.CTkButton(top, text="CLOSE", command=top.destroy, fg_color=BG_INPUT, 
                      hover_color=BG_DARK, text_color=TEXT_PRIMARY, height=35).pack(pady=15)
        
        top.after(100, top.focus_force)

    # ── Progress Section ──────────────────────────────────────────────────────

    def _build_progress_section(self, parent):
        prog = ctk.CTkFrame(parent, fg_color=BG_PANEL,
                            corner_radius=14, border_width=1,
                            border_color=BORDER_COLOR)
        prog.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        prog.grid_columnconfigure(0, weight=1)

        # Current file progress
        file_hdr = ctk.CTkFrame(prog, fg_color="transparent")
        file_hdr.grid(row=0, column=0, sticky="ew", padx=16, pady=(12, 2))
        file_hdr.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(file_hdr, text="CURRENT FILE",
                     font=ctk.CTkFont(size=10, weight="bold"),
                     text_color=ACCENT_BLUE).grid(row=0, column=0, sticky="w")

        self._file_pct_lbl = ctk.CTkLabel(
            file_hdr, text="0%",
            font=ctk.CTkFont(size=10), text_color=TEXT_MUTED)
        self._file_pct_lbl.grid(row=0, column=1, sticky="e")

        self._file_speed_lbl = ctk.CTkLabel(
            file_hdr, text="",
            font=ctk.CTkFont(size=10), text_color=TEXT_MUTED)
        self._file_speed_lbl.grid(row=0, column=2, sticky="e", padx=(12, 0))

        self._file_eta_lbl = ctk.CTkLabel(
            file_hdr, text="",
            font=ctk.CTkFont(size=10), text_color=TEXT_MUTED)
        self._file_eta_lbl.grid(row=0, column=3, sticky="e", padx=(12, 0))

        self._file_progress = ctk.CTkProgressBar(
            prog,
            progress_color=ACCENT_GREEN,
            fg_color=BG_INPUT,
            height=10,
            corner_radius=5,
        )
        self._file_progress.set(0)
        self._file_progress.grid(row=1, column=0, sticky="ew", padx=16, pady=(4, 10))

        # Batch progress
        batch_hdr = ctk.CTkFrame(prog, fg_color="transparent")
        batch_hdr.grid(row=2, column=0, sticky="ew", padx=16, pady=(0, 2))
        batch_hdr.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(batch_hdr, text="BATCH PROGRESS",
                     font=ctk.CTkFont(size=10, weight="bold"),
                     text_color=ACCENT_BLUE).grid(row=0, column=0, sticky="w")

        self._batch_pct_lbl = ctk.CTkLabel(
            batch_hdr, text="0 / 0",
            font=ctk.CTkFont(size=10), text_color=TEXT_MUTED)
        self._batch_pct_lbl.grid(row=0, column=1, sticky="e")

        self._batch_progress = ctk.CTkProgressBar(
            prog,
            progress_color=ACCENT_BLUE,
            fg_color=BG_INPUT,
            height=10,
            corner_radius=5,
        )
        self._batch_progress.set(0)
        self._batch_progress.grid(row=3, column=0, sticky="ew", padx=16, pady=(4, 14))

    # ── Player Section ────────────────────────────────────────────────────────
    def _build_player_section(self, parent):
        self._player_frame = ctk.CTkFrame(parent, fg_color=BG_PANEL,
                                         corner_radius=14, border_width=1,
                                         border_color=BORDER_COLOR)
        self._player_frame.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        self._player_frame.grid_columnconfigure(1, weight=1)

        # 1. Album Art / Icon area
        self._art_box = ctk.CTkFrame(self._player_frame, fg_color=BG_DARK, width=80, height=80, corner_radius=12)
        self._art_box.grid(row=0, column=0, rowspan=2, padx=12, pady=12)
        self._art_box.grid_propagate(False)
        ctk.CTkLabel(self._art_box, text="♪", font=ctk.CTkFont(size=40), text_color=ACCENT_BLUE).place(relx=0.5, rely=0.5, anchor="center")

        # 2. Info Area
        info_frame = ctk.CTkFrame(self._player_frame, fg_color="transparent")
        info_frame.grid(row=0, column=1, sticky="w", pady=(12, 0))
        
        ctk.CTkLabel(info_frame, text="NOW PLAYING", font=ctk.CTkFont(size=9, weight="bold"), text_color=ACCENT_BLUE).pack(anchor="w")
        self._player_title = ctk.CTkLabel(info_frame, text="Select track to play", font=ctk.CTkFont(size=14, weight="bold"), text_color=TEXT_PRIMARY, anchor="w")
        self._player_title.pack(anchor="w")
        
        self._track_index_lbl = ctk.CTkLabel(info_frame, text="Library: 0 tracks", font=ctk.CTkFont(size=10), text_color=TEXT_MUTED)
        self._track_index_lbl.pack(anchor="w")

        # 3. Controls Area
        ctrl_frame = ctk.CTkFrame(self._player_frame, fg_color="transparent")
        ctrl_frame.grid(row=1, column=1, sticky="ew", pady=(0, 12))
        ctrl_frame.grid_columnconfigure(1, weight=1)

        # Transport Buttons
        trans_frame = ctk.CTkFrame(ctrl_frame, fg_color="transparent")
        trans_frame.grid(row=0, column=1, pady=(0, 5))
        
        # Rewind
        ctk.CTkButton(trans_frame, text="↺ 10", width=34, height=32, fg_color="transparent", 
                      text_color=ACCENT_BLUE, hover_color=BG_DARK, command=lambda: self._jump_time(-10)).pack(side="left", padx=2)

        ctk.CTkButton(trans_frame, text="⏮", width=32, height=32, fg_color="transparent", text_color=ACCENT_BLUE, 
                      hover_color=BG_DARK, command=self._on_prev).pack(side="left", padx=5)
        
        self._play_btn = ctk.CTkButton(trans_frame, text="▶", width=44, height=44, corner_radius=22, 
                                     fg_color=ACCENT_BLUE, text_color="white", command=self._toggle_playback)
        self._play_btn.pack(side="left", padx=10)

        ctk.CTkButton(trans_frame, text="⏭", width=32, height=32, fg_color="transparent", text_color=ACCENT_BLUE, 
                      hover_color=BG_DARK, command=self._on_next).pack(side="left", padx=5)

        # Fast Forward
        ctk.CTkButton(trans_frame, text="10 ↻", width=34, height=32, fg_color="transparent", 
                      text_color=ACCENT_BLUE, hover_color=BG_DARK, command=lambda: self._jump_time(10)).pack(side="left", padx=2)

        # 4. Library Search / Actions
        act_frame = ctk.CTkFrame(self._player_frame, fg_color="transparent")
        act_frame.grid(row=0, column=2, padx=12, sticky="ne", pady=10)
        
        self._shuffle_btn = ctk.CTkButton(act_frame, text="SHUFFLE: OFF", font=ctk.CTkFont(size=9, weight="bold"), 
                      fg_color=BG_DARK, text_color=TEXT_MUTED, width=80, height=22, border_width=1, 
                      border_color=BORDER_COLOR, corner_radius=6, command=self._toggle_shuffle)
        self._shuffle_btn.pack(pady=(0, 5))

        ctk.CTkButton(act_frame, text="🎵 BROWSE FOLDER", font=ctk.CTkFont(size=10, weight="bold"), 
                      fg_color=BG_DARK, text_color=ACCENT_TEAL, width=120, height=28, border_width=1, 
                      border_color=ACCENT_TEAL, corner_radius=8, command=self._open_library).pack()

        # Seek Bar & Time
        seek_frame = ctk.CTkFrame(ctrl_frame, fg_color="transparent")
        seek_frame.grid(row=1, column=0, columnspan=3, sticky="ew", padx=10)
        seek_frame.grid_columnconfigure(1, weight=1)

        self._current_time_lbl = ctk.CTkLabel(seek_frame, text="0:00", font=ctk.CTkFont(size=10), text_color=TEXT_MUTED)
        self._current_time_lbl.grid(row=0, column=0)

        self._seek_slider = ctk.CTkSlider(seek_frame, from_=0, to=100, height=14, progress_color=ACCENT_BLUE, 
                                        command=self._on_seek)
        self._seek_slider.grid(row=0, column=1, padx=10, sticky="ew")
        self._seek_slider.set(0)

        self._total_time_lbl = ctk.CTkLabel(seek_frame, text="0:00", font=ctk.CTkFont(size=10), text_color=TEXT_MUTED)
        self._total_time_lbl.grid(row=0, column=2)

        # 5. Volume Slider (Right Side)
        vol_frame = ctk.CTkFrame(self._player_frame, fg_color="transparent")
        vol_frame.grid(row=0, column=3, rowspan=2, padx=(0, 20), sticky="ns")
        ctk.CTkLabel(vol_frame, text="VOL", font=ctk.CTkFont(size=9, weight="bold"), text_color=TEXT_MUTED).pack(pady=(12, 0))
        self._vol_slider = ctk.CTkSlider(vol_frame, from_=0, to=1, orientation="vertical", width=14, height=60, 
                                       progress_color=ACCENT_BLUE, command=self._set_volume)
        self._vol_slider.pack(pady=10)
        self._vol_slider.set(0.7)
        self._is_playing = False
        self._current_song_path = None
        self._song_duration = 0
        self._playlist = []
        self._playlist_idx = -1
        self._seek_offset = 0 # TRACKS THE ABSOLUTE START POSITION

    def _refresh_playlist(self):
        folder = self._folder_var.get()
        if not os.path.exists(folder): return
        self._playlist = [os.path.join(folder, f) for f in os.listdir(folder) if f.lower().endswith(".mp3")]
        self._playlist.sort() 
        self._track_index_lbl.configure(text=f"Library: {len(self._playlist)} tracks")

    def _on_prev(self):
        self._refresh_playlist()
        if not self._playlist: return
        self._playlist_idx = (self._playlist_idx - 1) % len(self._playlist)
        self._load_track(self._playlist[self._playlist_idx], start_now=True)

    def _on_next(self):
        self._refresh_playlist()
        if not self._playlist: return
        
        if self._is_shuffle and len(self._playlist) > 1:
            import random
            old_idx = self._playlist_idx
            while self._playlist_idx == old_idx:
                self._playlist_idx = random.randint(0, len(self._playlist) - 1)
        else:
            self._playlist_idx = (self._playlist_idx + 1) % len(self._playlist)
            
        self._load_track(self._playlist[self._playlist_idx], start_now=True)

    def _toggle_shuffle(self):
        self._is_shuffle = not self._is_shuffle
        text = "SHUFFLE: ON" if self._is_shuffle else "SHUFFLE: OFF"
        color = ACCENT_TEAL if self._is_shuffle else TEXT_MUTED
        self._shuffle_btn.configure(text=text, text_color=color, border_color=color)

    def _jump_time(self, delta):
        if not self._current_song_path: return
        # Get current pos from sync logic or pygame
        elapsed = pygame.mixer.music.get_pos() / 1000.0
        curr = self._seek_offset + elapsed
        new_pos = max(0, min(self._song_duration, curr + delta))
        self._on_seek((new_pos / self._song_duration) * 100)

    def _open_library(self):
        # Singleton Pattern: Re-lift instead of re-opening
        if self._library_modal and self._library_modal.winfo_exists():
            self._library_modal.lift()
            self._library_modal.focus_force()
            return

        self._refresh_playlist()
        top = ctk.CTkToplevel(self)
        self._library_modal = top
        top.title("Smart Music Library")
        self._center_window(top, 600, 700)
        
        # Click outside to close implementation (with delay to prevent "flash")
        def bind_focus():
            if top.winfo_exists():
                 top.bind("<FocusOut>", lambda e: self._on_modal_focus_out(e, top))
        top.after(500, bind_focus)
        
        top.after(200, lambda: top.focus_force())
        
        ctk.CTkLabel(top, text="🎵 SMART LIBRARY", font=ctk.CTkFont(size=18, weight="bold"), text_color=ACCENT_BLUE).pack(pady=(15, 5))
        
        # SEARCH INTERFACE
        search_frame = ctk.CTkFrame(top, fg_color="transparent")
        search_frame.pack(fill="x", padx=25, pady=(5, 0))
        search_frame.grid_columnconfigure(0, weight=1)
        
        search_var = ctk.StringVar()
        search_entry = ctk.CTkEntry(
            search_frame, 
            textvariable=search_var,
            placeholder_text="🔍 Search songs by title or artist...",
            height=40,
            fg_color=BG_INPUT,
            border_color=BORDER_COLOR,
            font=ctk.CTkFont(size=13)
        )
        search_entry.grid(row=0, column=0, sticky="ew")
        
        def clear_search():
            search_var.set("")
            search_entry.focus_set()

        clear_btn = ctk.CTkButton(search_frame, text="✖", width=35, height=35, corner_radius=8,
                                 fg_color=BG_INPUT, hover_color=TEXT_ERROR, 
                                 text_color=TEXT_PRIMARY, command=clear_search)
        clear_btn.grid(row=0, column=1, padx=(10, 0))

        # Result info label
        search_info_lbl = ctk.CTkLabel(top, text="Showing all tracks", font=ctk.CTkFont(size=10), text_color=TEXT_MUTED)
        search_info_lbl.pack(pady=(2, 8))
        
        frame = ctk.CTkScrollableFrame(top, fg_color=BG_PANEL)
        frame.pack(fill="both", expand=True, padx=20, pady=(0, 15))

        def populate_results(query=""):
            for widget in frame.winfo_children():
                widget.destroy()
            
            q = query.lower().strip()
            # Multi-term support (e.g. "Artist Title")
            q_parts = q.split()
            
            def is_match(path):
                if not q_parts: return True
                name = os.path.basename(path).lower()
                return all(part in name for part in q_parts)

            session_hits = [p for p in self._playlist if p in self._current_session_files and is_match(p)]
            local_hits = [p for p in self._playlist if p not in self._current_session_files and is_match(p)]
            
            # Update status info
            total = len(session_hits) + len(local_hits)
            if not q:
                search_info_lbl.configure(text=f"Showing all {total} library tracks", text_color=TEXT_MUTED)
            else:
                search_info_lbl.configure(text=f"Found {total} matches for '{query}'", text_color=ACCENT_TEAL)

            def add_section(title, tracks, icon="♪", color=ACCENT_GREEN):
                if not tracks: return
                ctk.CTkLabel(frame, text=f"{title} ({len(tracks)})", font=ctk.CTkFont(size=11, weight="bold"), text_color=color).pack(anchor="w", padx=10, pady=(15, 5))
                for p in tracks:
                    name = os.path.basename(p)
                    def play_now(path=p):
                        self._load_track(path, True)
                        top.destroy()
                    btn = ctk.CTkButton(frame, text=f"{icon}  {name}", anchor="w", fg_color="transparent", text_color=TEXT_PRIMARY, hover_color=BG_CARD, height=35, command=play_now)
                    btn.pack(fill="x", pady=2)

            add_section("✨ SEARCH RESULTS / RECENT", session_hits, "✨", ACCENT_TEAL)
            add_section("📂 ARCHIVED TRACKS", local_hits, "♪", TEXT_MUTED)

        search_var.trace_add("write", lambda *args: populate_results(search_var.get()))
        # Initial population
        populate_results()
        
        # Auto-focus the search bar immediately on open
        search_entry.after(600, lambda: search_entry.focus_set())




    def _toggle_playback(self):
        if not self._current_song_path: 
            self._on_next()
            return
        
        if self._is_playing:
            pygame.mixer.music.pause()
            self._play_btn.configure(text="▶")
            self._is_playing = False
        else:
            # Proper Resume logic
            pygame.mixer.music.unpause()
            self._play_btn.configure(text="⏸")
            self._is_playing = True
            self._update_player_sync()

    def _on_seek(self, value):
        if not self._current_song_path: return
        self._seek_offset = (value / 100) * self._song_duration
        pygame.mixer.music.play(start=self._seek_offset)
        if not self._is_playing:
            pygame.mixer.music.pause()
        else:
            self._update_player_sync()

    def _set_volume(self, val):
        pygame.mixer.music.set_volume(float(val))

    def _load_track(self, file_path, start_now=False):
        """Loads a track into the player UI."""
        if not os.path.exists(file_path): return
        self._current_song_path = file_path
        self._seek_offset = 0 # Reset offset for new song
        
        # Sync index
        self._refresh_playlist()
        try: self._playlist_idx = self._playlist.index(file_path)
        except: self._playlist_idx = -1

        try:
            from mutagen.mp3 import MP3
            audio = MP3(file_path)
            self._song_duration = audio.info.length
            name = os.path.basename(file_path).replace(".mp3", "")
            self._player_title.configure(text=name[:40] + ("..." if len(name) > 40 else ""))
            self._total_time_lbl.configure(text=self._format_seconds(self._song_duration))
            self._seek_slider.set(0)
            self._current_time_lbl.configure(text="0:00")
            self._track_index_lbl.configure(text=f"Track {self._playlist_idx+1} of {len(self._playlist)}")
            
            if start_now or self._is_playing:
                pygame.mixer.music.load(file_path)
                pygame.mixer.music.play()
                self._is_playing = True
                self._play_btn.configure(text="⏸")
                self._update_player_sync()
        except:
            pass

    def _update_player_sync(self):
        if self._is_playing:
            try:
                # TRUE POSITION = OFFSET + ELAPSED
                elapsed = pygame.mixer.music.get_pos() / 1000.0
                curr = self._seek_offset + elapsed
                
                if curr >= 0:
                    self._current_time_lbl.configure(text=self._format_seconds(curr))
                    if self._song_duration > 0:
                        self._seek_slider.set((curr / self._song_duration) * 100)
                
                if not pygame.mixer.music.get_busy() and self._is_playing:
                   self._play_btn.configure(text="▶")
                   self._is_playing = False
                   self._seek_offset = 0
                   # AUTO-NEXT TRIGGER
                   self.after(500, self._on_next)
                   return
            except:
                pass
            self.after(500, self._update_player_sync)

    def _format_seconds(self, s):
        m, s = divmod(int(s), 60)
        return f"{m}:{s:02d}"

    # ── Console Output ────────────────────────────────────────────────────────

    def _build_console(self, parent):
        con_frame = ctk.CTkFrame(parent, fg_color=BG_PANEL,
                                 corner_radius=14, border_width=1,
                                 border_color=BORDER_COLOR)
        con_frame.grid(row=3, column=0, sticky="nsew", pady=(0, 0))
        con_frame.grid_columnconfigure(0, weight=1)
        con_frame.grid_rowconfigure(1, weight=1)

        hdr = ctk.CTkFrame(con_frame, fg_color="transparent")
        hdr.grid(row=0, column=0, sticky="ew", padx=16, pady=(10, 4))
        hdr.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(hdr, text="CONSOLE OUTPUT",
                     font=ctk.CTkFont(size=10, weight="bold"),
                     text_color=ACCENT_BLUE).grid(row=0, column=0, sticky="w")

        ctk.CTkButton(
            hdr, text="Clear", width=60, height=22,
            font=ctk.CTkFont(size=10),
            fg_color=BG_CARD, hover_color="#252D40",
            text_color=TEXT_MUTED, corner_radius=6,
            command=self._clear_console,
        ).grid(row=0, column=1, sticky="e")

        self._console = ctk.CTkTextbox(
            con_frame,
            fg_color=BG_DARK,
            border_color=BORDER_COLOR,
            border_width=1,
            text_color=TEXT_PRIMARY,
            font=ctk.CTkFont(family="Consolas", size=11),
            corner_radius=8,
            state="disabled",
            wrap="none",  # Allow horizontal scrolling
        )
        self._console.grid(row=1, column=0, sticky="nsew", padx=14, pady=(0, 14))

        # Tag colors (we'll mimic them via prefix inspection)

    # ── Status bar ────────────────────────────────────────────────────────────

    def _build_statusbar(self):
        bar = ctk.CTkFrame(self, fg_color=BG_PANEL, corner_radius=0,
                           height=28, border_width=0)
        bar.grid(row=2, column=0, sticky="ew")
        bar.grid_propagate(False)
        bar.grid_columnconfigure(0, weight=1)

        self._status_lbl = ctk.CTkLabel(
            bar, text="  Ready — paste links and press START",
            font=ctk.CTkFont(size=11), text_color=TEXT_MUTED, anchor="w")
        self._status_lbl.grid(row=0, column=0, sticky="ew", padx=8)

        ctk.CTkLabel(
            bar, text=f"yt-dlp {'✔' if yt_dlp else '✘'}   mutagen {'✔' if MUTAGEN_OK else '✘'}   ",
            font=ctk.CTkFont(size=10),
            text_color=ACCENT_GREEN if (yt_dlp and MUTAGEN_OK) else TEXT_WARNING,
        ).grid(row=0, column=1, sticky="e", padx=8)

    # ─────────────────────────────────────────────────────────────────────────
    # CONTROLS
    # ─────────────────────────────────────────────────────────────────────────

    def _pick_folder(self):
        path = filedialog.askdirectory(title="Select save folder",
                                       initialdir=self._folder_var.get())
        if path:
            self._folder_var.set(path)

    def _paste_clipboard(self):
        try:
            text = self.clipboard_get()
            self._url_textbox.insert("end", "\n" + text)
            self._on_url_change()
        except Exception:
            pass

    def _validate_search_state(self):
        """Rules: Tag OPTIONAL. Genre REQ only if Title, Author, and Year are blank."""
        tag = self._tag_var.get()
        title = self._title_var.get().strip()
        author = self._author_var.get().strip()
        year = self._year_var.get().strip()
        genre = self._genre_var.get()

        is_target = any([title, author, year])
        genre_set = genre not in ["--", "[SELECT GENRE]", "None", ""]

        # Dynamic Genre Menu Updating
        if is_target:
            current_vals = self._GENRES_MASTER.copy()
            if "--" not in current_vals:
                current_vals.insert(0, "--")
            self._genre_menu.configure(values=current_vals)
            
            genre_ok = True
            self._genre_lbl_req.configure(text="(OPTIONAL)", text_color=TEXT_MUTED)
        else:
            current_vals = [g for g in self._GENRES_MASTER if g != "--"]
            self._genre_menu.configure(values=current_vals)
            if genre == "--":
                self._genre_var.set("[SELECT GENRE]")
                
            genre_ok = genre_set
            self._genre_lbl_req.configure(text="(REQUIRED)", text_color="red")

        if genre_ok:
            self._search_btn.configure(state="normal", text="🚀   FIND & QUEUE TRACKS", fg_color=ACCENT_TEAL, text_color=BG_DARK)
        else:
            self._search_btn.configure(state="disabled", text="🚀  GENRE REQUIRED", fg_color=BG_INPUT, text_color=TEXT_MUTED)

    def _on_ai_search(self):
        if not MB_AVAILABLE:
            messagebox.showerror("Error", "MusicBrainz library NOT installed.")
            return

        t = self._title_var.get().strip()
        a = self._author_var.get().strip()
        y = self._year_var.get().strip()
        g = self._genre_var.get()
        if g == "--" or g == "[SELECT GENRE]": g = "" 
        tg = self._tag_var.get().strip()
        if tg == "[SELECT VERSION]": tg = ""
        
        # User defined batch size
        try:
            target_count = int(self._count_var.get())
        except:
            target_count = 50

        if not any([t, a, y, g, tg]): return 

        is_natural = any([t, a])
        
        # Build a resilient fuzzy query
        main_terms = []
        if t: main_terms.append(f'"{t}"')
        if a: main_terms.append(f'"{a}"')
        
        # 1. FIELD GROUNDING: Map terms to specific DB fields (e.g., artist vs title)
        # This prevents "Songs about Taylor Swift" by forcing the Artist record.
        main_terms = []
        if t: main_terms.append(f'recording:"{t}"')
        if a: main_terms.append(f'artist:"{a}"')
        
        primary_q = " AND ".join(main_terms) if main_terms else ""
        
        parts = []
        if primary_q: parts.append(f"({primary_q})")
        if y: parts.append(f'date:{y}')
        if g and g != "": parts.append(f'tag:"{g}"')

        query_str = " AND ".join(parts)
        
        # 2. SMART INTENT: Filter for Original/Remix/Cover
        if tg == "Original":
            # Extra strict filtering for original recordings
            query_str += ' AND NOT (remix OR cover OR tribute OR AI OR "8-bit" OR "8 bit" OR instrumental OR karaoke OR medley OR parody OR commentary OR "behind the scenes" OR bts OR live OR session OR concert OR mashup)'
        elif tg == "Remix":
            query_str += ' AND (remix OR edit OR "re-work" OR shuffle)'
        elif tg == "Cover":
            query_str += ' AND (cover OR tribute OR "re-recording")'
        elif tg == "AI Version":
            query_str += ' AND (AI OR "voice-model" OR rvc OR "ai cover")'

        mode_desc = "[Natural]" if is_natural else f"[Batch: {target_count}]"
        self._log(f"🔎 Engine: {mode_desc} (Strict Identity Guard) Searching...", level="info")
        self._search_btn.configure(state="disabled", text="⌛ Searching...")
        
        threading.Thread(target=self._run_discovery, args=(query_str, is_natural, target_count), daemon=True).start()

    def _run_discovery(self, query_str, is_natural, target_count):
        try:
            import random, re
            all_recordings = []
            seen_keys = set()
            
            # Helper for resilient searching
            def resilient_search(q, limit=100, offset=0):
                for attempt in range(3):
                    try:
                        return musicbrainzngs.search_recordings(query=q, limit=limit, offset=offset)
                    except Exception as e:
                        if attempt < 2:
                            self._log(f"🔄 Network Hiccup ({attempt+1}/3): Retrying in 2s...", level="info")
                            time.sleep(2)
                            continue
                        raise e

            # Diagnostic Initial Search
            res = resilient_search(query_str, limit=1)
            total_matches = res.get('recording-count', 0)
            self._log(f"📊 Dashboard: found {total_matches} entries (scrubbing versions...)", level="info")
            
            if total_matches == 0:
                self._log("❌ No metadata found for this filter.", level="warning")
                self._search_btn.configure(state="normal", text="🚀   FIND & QUEUE TRACKS")
                return

            current_offset = random.randint(0, min(100, max(0, total_matches - 50))) if not is_natural else 0
            
            # Fetch a bit extra to allow for better popularity sorting
            overage_target = int(target_count * 1.2)

            while len(all_recordings) < overage_target:
                limit = min(100, overage_target - len(all_recordings) + 50)
                if len(all_recordings) > 0:
                    self._log(f"🔎 Ranking: {len(all_recordings)} items processed...", level="info")
                
                result = resilient_search(query_str, limit=min(100, limit), offset=current_offset)
                new_found = result.get('recording-list', [])
                if not new_found: break
                
                for rec in new_found:
                    if len(all_recordings) >= overage_target: break
                    
                    title = rec.get('title', 'Unknown').strip()
                    artist = 'Unknown'
                    icrew = rec.get('artist-credit', [])
                    if icrew:
                        artist = icrew[0].get('artist', {}).get('name', 'Unknown').strip()
                    
                    # 3. SECOND-LAYER IDENTITY GUARD (The Local 'AI')
                    # If user provided an author, ensure the result artist actually matches.
                    # This catches things like "Song Title: Taylor Swift" by artist "Other Guy"
                    search_author = self._author_var.get().strip().lower()
                    if search_author and search_author not in artist.lower():
                        continue 

                    clean_title = re.sub(r'[\(\[].*?[\)\]]', '', title).strip().lower()
                    identity = f"{clean_title} - {artist.lower()}"
                    
                    if identity not in seen_keys and clean_title != "":
                        # Assign Weight for popularity sorting
                        # More release info + higher ext:score = more likely to be a hit
                        score = int(rec.get('ext:score', 0))
                        release_count = len(rec.get('release-list', []))
                        rec['_rank'] = score + (release_count * 10) 
                        rec['yt_url'] = None # Placeholder
                        rec['views'] = 0     # Placeholder
                        
                        all_recordings.append(rec)
                        seen_keys.add(identity)
                
                current_offset += len(new_found)
                if len(new_found) < 10: break

            if not all_recordings:
                self._log("❌ No unique tracks found.", level="warning")
                self.after(0, lambda: self._search_btn.configure(state="normal", text="🚀   FIND & QUEUE TRACKS"))
                return

            # CRITICAL: SORT BY RANK (POPULARITY PROXY)
            all_recordings.sort(key=lambda x: x.get('_rank', 0), reverse=True)
            
            # Truncate to the EXACT count requested by user
            all_recordings = all_recordings[:target_count]

            if not all_recordings:
                self._log("❌ No metadata found for this filter.", level="warning")
                self.after(0, lambda: self._search_btn.configure(state="normal", text="🚀   FIND & QUEUE TRACKS"))
                return

            self._last_recordings = all_recordings
            
            # --- NEW: PARALLEL VIEW RESOLUTION (PRE-FETCH) ---
            self._log(f"⚡ Discovery complete. Now ranking {len(all_recordings)} tracks by views...", level="info")
            
            from concurrent.futures import ThreadPoolExecutor
            
            def resolve_one(rec):
                query = f"{rec['artist-credit'][0]['artist']['name']} - {rec['title']}"
                res = self._resolve_yt_link(query)
                if res:
                    rec['yt_url'], rec['views'] = res
                return rec

            # Resolve in parallel (max 5 threads to avoid YouTube rate limits)
            with ThreadPoolExecutor(max_workers=5) as executor:
                list(executor.map(resolve_one, all_recordings))

            # CRITICAL: STRICT SORT BY VIEWS (Highest First)
            all_recordings.sort(key=lambda x: x.get('views', 0), reverse=True)
            self._last_recordings = all_recordings

            self.after(0, lambda: self._view_results_btn.configure(state="normal", fg_color=ACCENT_BLUE))
            self.after(0, self._show_selection_dashboard) 
            self._log(f"✅ Success: {len(all_recordings)} tracks ranked and ready!", level="success")
            
        except Exception as e:
            self._log(f"⚠ Engine Error: {e}", level="error")
        finally:
            self.after(0, lambda: self._search_btn.configure(state="normal", text="🚀   FIND & QUEUE TRACKS"))

    def _show_selection_dashboard(self):
        """Opens a modern modal to select tracks with real-time view resolution."""
        if not self._last_recordings: return

        top = ctk.CTkToplevel(self)
        top.title("DISCOVERY SELECTION DASHBOARD")
        top.geometry("700x750")
        top.attributes("-topmost", True)
        top.configure(fg_color=BG_DARK)
        self._center_window(top, 700, 750)

        # Header Section
        header = ctk.CTkFrame(top, fg_color=BG_PANEL, corner_radius=0, height=100)
        header.pack(fill="x", side="top")
        header.pack_propagate(False)

        ctk.CTkLabel(header, text="DISCOVERY SELECTION", font=ctk.CTkFont(size=20, weight="bold"), text_color=ACCENT_TEAL).pack(pady=(15, 2))
        
        info_frame = ctk.CTkFrame(header, fg_color="transparent")
        info_frame.pack()
        
        total_count = len(self._last_recordings)
        counter_lbl = ctk.CTkLabel(info_frame, text=f"{total_count}/{total_count} Selected", font=ctk.CTkFont(size=12, weight="bold"), text_color=ACCENT_BLUE)
        counter_lbl.pack(side="left", padx=10)

        ctk.CTkLabel(info_frame, text="✅ Rank by views complete", font=ctk.CTkFont(size=11), text_color=ACCENT_GREEN).pack(side="left", padx=10)

        # Scrollable area
        f = ctk.CTkScrollableFrame(top, fg_color=BG_DARK, corner_radius=0)
        f.pack(expand=True, fill="both", padx=5, pady=5)

        check_vars = []
        view_labels = {} # Store label widgets to update them

        for i, rec in enumerate(self._last_recordings):
            title = rec.get('title', 'Unknown')
            artist = 'Unknown Artist'
            if rec.get('artist-credit'):
                artist = rec['artist-credit'][0].get('artist', {}).get('name', 'Unknown')
            
            # Card Container
            card = ctk.CTkFrame(f, fg_color=BG_PANEL, corner_radius=10, height=50)
            card.pack(fill="x", padx=15, pady=4)
            card.pack_propagate(False)

            var = ctk.BooleanVar(value=True)
            def update_counter(*args):
                sel_count = sum(1 for v, _ in check_vars if v.get())
                counter_lbl.configure(text=f"{sel_count}/{total_count} Selected")
            var.trace_add("write", update_counter) 
            check_vars.append((var, f"{artist} - {title}", rec))
            
            cb = ctk.CTkCheckBox(card, text="", variable=var, width=20, fg_color=ACCENT_TEAL, hover_color="#00A6B2")
            cb.pack(side="left", padx=(15, 5))

            # Metadata Info
            info = ctk.CTkFrame(card, fg_color="transparent")
            info.pack(side="left", fill="both", expand=True, padx=5)
            
            ctk.CTkLabel(info, text=title, font=ctk.CTkFont(size=13, weight="bold"), text_color=TEXT_PRIMARY, anchor="w").pack(side="top", anchor="w", pady=(8, 0))
            ctk.CTkLabel(info, text=artist, font=ctk.CTkFont(size=11), text_color=TEXT_MUTED, anchor="w").pack(side="top", anchor="w", pady=(0, 8))

            # View Count Badge
            v_badge = ctk.CTkFrame(card, fg_color=BG_INPUT, corner_radius=6, width=100, height=28)
            v_badge.pack(side="right", padx=15)
            v_badge.pack_propagate(False)
            
            views = rec.get('views', 0)
            v_lbl = ctk.CTkLabel(v_badge, text=f"{fmt_views(views)} views", font=ctk.CTkFont(size=10, weight="bold"), text_color=ACCENT_GREEN if views > 0 else TEXT_MUTED)
            v_lbl.pack(expand=True)

        # Actions
        btn_frame = ctk.CTkFrame(top, fg_color=BG_PANEL, height=80, corner_radius=0)
        btn_frame.pack(fill="x", side="bottom")

        def select_all(val):
            for v, n, r in check_vars: v.set(val)
            update_counter()

        def resolve_selected():
            selected_items = [(rec, name) for var, name, rec in check_vars if var.get()]
            if not selected_items: return
            
            top.destroy()
            self._log(f"⚡ Queueing {len(selected_items)} selected tracks...", level="info")
            threading.Thread(target=self._finalize_selection, args=(selected_items,), daemon=True).start()

        ctk.CTkButton(btn_frame, text="CHECK ALL", width=120, height=36, fg_color=BG_INPUT, font=ctk.CTkFont(size=11), command=lambda: select_all(True)).pack(side="left", padx=20, pady=20)
        ctk.CTkButton(btn_frame, text="UNCHECK ALL", width=120, height=36, fg_color=BG_INPUT, font=ctk.CTkFont(size=11), command=lambda: select_all(False)).pack(side="left", padx=0, pady=20)
        ctk.CTkButton(btn_frame, text="✅  QUEUE SELECTED", fg_color=ACCENT_BLUE, text_color="white", font=ctk.CTkFont(size=13, weight="bold"), height=40,
                      command=resolve_selected).pack(side="right", padx=20, pady=20, expand=True, fill="x")

    def _finalize_selection(self, items):
        """Processes the final selection, resolving any links that weren't caught yet."""
        found_data = []
        for i, (rec, query) in enumerate(items):
            if rec.get('yt_url'):
                found_data.append((rec['yt_url'], rec['views']))
            else:
                self._log(f"  🔍 Resolving ({i+1}/{len(items)}): {query}", level="info")
                res = self._resolve_yt_link(query)
                if res: found_data.append(res)
        
        if found_data:
            # Rank strictly by overall views (highest first)
            found_data.sort(key=lambda x: x[1], reverse=True)
            found_urls = [x[0] for x in found_data]
            
            def update_ui():
                self._url_textbox.delete("0.0", "end") 
                self._url_textbox.insert("end", "\n".join(found_urls) + "\n")
                self._on_url_change()
                self._log(f"✨ Successfully added {len(found_urls)} tracks ranked by views!", level="header")
            self.after(0, update_ui)
        else:
            self._log("⚠ No matches found on YouTube for selected items.", level="warning")

    def _resolve_yt_link(self, query):
        if not yt_dlp: return None
        try:
            # We search for TOP 5 candidates to perform strict filtering
            ydl_opts = {
                'format': 'bestaudio/best',
                'quiet': True,
                'no_warnings': True,
                'extract_flat': True,
                'skip_download': True
            }
            
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                # Append "Official" to query to lean the algorithm toward better results
                strict_query = f"{query} Official"
                info = ydl.extract_info(f"ytsearch5:{strict_query}", download=False)
                
                if 'entries' not in info or not info['entries']:
                    return None

                candidates = []
                
                # Strict Filtering Keywords
                whitelist = ['official audio', 'official music video', 'official video', 'music video', 'mv', 'topic']
                blacklist = ['commentary', 'behind the scenes', 'bts', '8-bit', '8 bit', 'instrumental', 'karaoke', 'reaction', 'parody', 'interview', 'live stream', 'tutorial', 'how to', 'review', 'vlog']

                for entry in info['entries']:
                    title = entry.get('title', '').lower()
                    uploader = entry.get('uploader', '').lower()
                    views = entry.get('view_count', 0) or 0
                    
                    # 1. Skip Blacklisted
                    if any(word in title for word in blacklist) or any(word in uploader for word in blacklist):
                        continue

                    # 2. Identification: Official vs Clean
                    is_official = any(word in title for word in whitelist) or (' - topic' in uploader)
                    
                    # A "Clean" title has no brackets or metadata clutter (official, live, lyric, etc.)
                    has_clutter = any(word in title for word in (whitelist + blacklist + ['official', 'lyrics', 'lyric', 'audio', 'video', 'video']))
                    has_brackets = any(char in title for char in "()[]【】")
                    is_clean = not has_clutter and not has_brackets
                    
                    # 3. Hierarchical Scoring: 
                    # Priority 1: Official (~2B points)
                    # Priority 2: Clean Title - Author (~1B points)
                    # Priority 3: Views (Tie-breaker)
                    score = views
                    if is_official:
                        score += 2_000_000_000
                    elif is_clean:
                        score += 1_000_000_000
                    
                    candidates.append({
                        'id': entry.get('id'),
                        'score': score,
                        'views': views
                    })

                # Sort by highest score
                candidates.sort(key=lambda x: x['score'], reverse=True)
                
                if candidates and candidates[0]['score'] > -1000:
                    best_match = candidates[0]
                    return (f"https://www.youtube.com/watch?v={best_match['id']}", best_match['views'])
                
        except Exception:
            pass
        return None

    def _update_core_engine(self):
        """Silently updates yt-dlp to ensure extraction never breaks."""
        if self._running:
            messagebox.showwarning("Busy", "Cannot update while a download is active.")
            return
            
        self._log("🛠️ Maintenance: Refreshing extraction engine (yt-dlp)...", level="info")
        self._status_lbl.configure(text="  🛠️ Updating Engine... (Please Wait)")
        
        def run_update():
            try:
                # Silently update yt-dlp
                subprocess.check_call([sys.executable, "-m", "pip", "install", "-U", "yt-dlp"], 
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                self._log("✨ Core engine successfully refreshed to latest version!", level="header")
                self._status_lbl.configure(text="  ✨ Engine Updated Successfully!")
            except Exception as e:
                self._log(f"✖ Update failed: {e}", level="error")
                self._status_lbl.configure(text="  ✖ Update Failed")
        
        threading.Thread(target=run_update, daemon=True).start()

    # ── AUTO-UPDATE UI HANDLERS ───────────────────────────────────────────────

    def _start_update_check(self):
        """Wire the module-level check_for_updates() to UI callbacks."""
        def _on_available(info):
            # Schedule on the main thread (tkinter is not thread-safe)
            self.after(0, lambda: self._show_update_prompt(info))

        def _on_no_update(info):
            self._log(
                f"✔ App is up to date  ({info['current']})", level="info"
            )

        def _on_error(info):
            # Silently swallow network errors during the background check
            self._log(f"ℹ️ Update check skipped: {info['error']}", level="info")

        check_for_updates(
            on_update_available=_on_available,
            on_no_update=_on_no_update,
            on_error=_on_error,
        )

    def _show_update_prompt(self, info: dict):
        """
        Show a non-blocking modal that presents the changelog and a download
        progress bar.  The user can accept or dismiss without blocking the UI.
        """
        latest_tag   = info["latest_tag"]
        asset_name   = info["asset_name"]
        download_url = info["download_url"]
        asset_size   = info.get("asset_size", 0)
        notes        = info.get("release_notes", "No changelog provided.")

        modal = ctk.CTkToplevel(self)
        modal.title(f"🚀 Update Available — {latest_tag}")
        modal.geometry("540x420")
        modal.resizable(False, False)
        modal.configure(fg_color=BG_DARK)
        modal.attributes("-topmost", True)
        modal.after(200, modal.lift)

        # ── Header ────────────────────────────────────────────────────────────
        ctk.CTkLabel(
            modal,
            text=f"🎉  JV PureMP3  {latest_tag}  is available!",
            font=ctk.CTkFont(size=16, weight="bold"),
            text_color=ACCENT_GREEN,
        ).pack(pady=(20, 4))

        ctk.CTkLabel(
            modal,
            text=f"You have  {APP_VERSION_TAG}  →  latest is  {latest_tag}",
            font=ctk.CTkFont(size=11),
            text_color=TEXT_MUTED,
        ).pack()

        # ── Changelog ─────────────────────────────────────────────────────────
        notes_box = ctk.CTkTextbox(
            modal, width=480, height=160,
            font=ctk.CTkFont(family="Consolas", size=11),
            fg_color=BG_PANEL, border_width=1, border_color=BORDER_COLOR,
            corner_radius=8,
        )
        notes_box.insert("0.0", notes[:2000])
        notes_box.configure(state="disabled")
        notes_box.pack(pady=12, padx=28)

        # ── Progress bar (hidden until download starts) ───────────────────────
        size_lbl = ctk.CTkLabel(
            modal,
            text=f"Size: {fmt_bytes(asset_size)}",
            font=ctk.CTkFont(size=10),
            text_color=TEXT_MUTED,
        )
        size_lbl.pack()

        progress_bar = ctk.CTkProgressBar(
            modal, progress_color=ACCENT_GREEN, fg_color=BG_INPUT,
            height=10, corner_radius=5,
        )
        progress_bar.set(0)
        progress_bar.pack(fill="x", padx=28, pady=(4, 2))
        progress_bar.pack_forget()   # hidden until needed

        status_lbl = ctk.CTkLabel(modal, text="", font=ctk.CTkFont(size=10),
                                  text_color=TEXT_MUTED)
        status_lbl.pack()

        # ── Buttons ───────────────────────────────────────────────────────────
        btn_row = ctk.CTkFrame(modal, fg_color="transparent")
        btn_row.pack(pady=10)

        dismiss_btn = ctk.CTkButton(
            btn_row, text="Remind Me Later",
            fg_color=BG_INPUT, hover_color=BG_CARD,
            text_color=TEXT_MUTED, width=140,
            command=modal.destroy,
        )
        dismiss_btn.pack(side="left", padx=8)

        def _do_update():
            """Called when user clicks 'Update Now'."""
            update_btn.configure(state="disabled", text="Downloading…")
            dismiss_btn.configure(state="disabled")
            progress_bar.pack(fill="x", padx=28, pady=(4, 2))

            def _progress_cb(frac: float):
                self.after(0, lambda f=frac: (
                    progress_bar.set(f),
                    status_lbl.configure(
                        text=f"Downloading… {int(f * 100)}%  ({fmt_bytes(f * asset_size)} / {fmt_bytes(asset_size)})"
                    )
                ))

            def _run():
                if _PLATFORM == "win32":
                    # Monkey-patch _download_asset to push progress updates
                    import tempfile
                    tmp_dir  = tempfile.mkdtemp(prefix="jvmp3_update_")
                    new_exe  = os.path.join(tmp_dir, asset_name)
                    ok = _download_asset(download_url, new_exe, on_progress=_progress_cb)
                    if not ok:
                        self.after(0, lambda: (
                            status_lbl.configure(text="❌ Download failed.", text_color=TEXT_ERROR),
                            update_btn.configure(state="normal", text="Retry"),
                            dismiss_btn.configure(state="normal"),
                        ))
                        return

                    # Launch the detached updater then quit
                    if _IS_FROZEN:
                        current_exe = sys.executable
                    else:
                        current_exe = os.path.abspath(__file__)

                    updater_py  = os.path.join(
                        os.path.dirname(os.path.abspath(__file__)), "updater.py"
                    )
                    updater_exe = os.path.join(
                        os.path.dirname(sys.executable), "updater.exe"
                    ) if _IS_FROZEN else None

                    if updater_exe and os.path.exists(updater_exe):
                        cmd = [updater_exe, str(os.getpid()), new_exe, current_exe]
                    else:
                        cmd = [sys.executable, updater_py,
                               str(os.getpid()), new_exe, current_exe]

                    subprocess.Popen(
                        cmd,
                        creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
                        close_fds=True,
                    )
                    self.after(0, lambda: (
                        status_lbl.configure(
                            text="✔ Update downloaded! Relaunching…",
                            text_color=TEXT_SUCCESS,
                        ),
                        self.after(1500, self.quit),
                    ))

                elif _PLATFORM == "android":
                    _apply_update_android(
                        download_url, asset_name,
                        log_fn=lambda m: self.after(0, lambda: status_lbl.configure(text=m))
                    )

            threading.Thread(target=_run, daemon=True, name="ApplyUpdate").start()

        update_btn = ctk.CTkButton(
            btn_row,
            text="⬇  Update Now",
            fg_color=ACCENT_GREEN, hover_color="#00BF87",
            text_color="#001A0F", width=160,
            font=ctk.CTkFont(size=13, weight="bold"),
            command=_do_update,
        )
        update_btn.pack(side="left", padx=8)

    def _on_url_change(self, _event=None):
        text = self._url_textbox.get("0.0", "end")
        urls = parse_urls(text, unique=False)
        count = len(urls)
        self._url_count_lbl.configure(
            text=f"{count} URL{'s' if count != 1 else ''} detected",
            text_color=ACCENT_GREEN if count > 0 else TEXT_MUTED,
        )
        # Toggle Start Button
        if count > 0:
            self._start_btn.configure(state="normal", fg_color=ACCENT_GREEN)
        else:
            self._start_btn.configure(state="disabled", fg_color=BG_DARK)

    def _on_start(self):
        if self._running:
            return

        if yt_dlp is None:
            messagebox.showerror("Missing Dependency",
                                 "yt-dlp is not installed.\nRun: pip install yt-dlp")
            return

        raw_text = self._url_textbox.get("0.0", "end")
        urls = parse_urls(raw_text, unique=False) # Keep duplicates for processing
        if not urls:
            messagebox.showwarning("No Links", "No valid YouTube URLs found.\nPlease paste at least one link.")
            return

        folder = self._folder_var.get().strip()
        if not folder:
            messagebox.showwarning("No Folder", "Please select a save folder first.")
            return

        os.makedirs(folder, exist_ok=True)

        # Reset UI
        self._reset_stats()
        self._console.configure(state="normal")
        self._console.delete("0.0", "end")
        self._console.configure(state="disabled")

        self._running = True
        self._batch_start_time = time.time()
        self._start_btn.configure(state="disabled", text="⏳  Downloading…")
        self._stop_btn.configure(state="normal")
        self._status_lbl.configure(text=f"  Downloading {len(urls)} tracks → {folder}")

        self._log(f"━━━ {self.APP_TITLE}  {self.APP_VERSION} ━━━", level="header")
        self._log(f"⟶  {len(urls)} tracks queued → {folder}", level="info")
        if self._clean_var.get():
            self._log("  🧹 Clean filenames: ON", level="info")
        if self._skip_var.get():
            self._log("  ⏭ Skip duplicates: ON", level="info")

        self._engine = DownloadEngine(
            urls=urls,
            save_folder=folder,
            clean_names=self._clean_var.get(),
            skip_dupes=self._skip_var.get(),
            use_prefix=self._prefix_var.get(),
            strip_symbols=self._strip_var.get(),
            author_first=self._naming_var.get(),
            log_queue=self._log_queue
        )

        self._dl_thread = threading.Thread(
            target=self._engine.run, daemon=True)
        self._dl_thread.start()

    def _on_stop(self):
        if not self._running: return
        if messagebox.askyesno("Confirm Stop", "Forcefully stop all downloads?\n(Partial files will be deleted)"):
            if self._engine:
                self._engine.stop()
            
            # FORCE CLEANUP OF .PART FILES
            folder = self._folder_var.get()
            if os.path.exists(folder):
                for f in os.listdir(folder):
                    if f.endswith(".part") or f.endswith(".temp"):
                        try: os.remove(os.path.join(folder, f))
                        except: pass
            
            self._log("⏹  FORCE STOPPED! Cleaned up partial files.", level="error")
            self._status_lbl.configure(text="  ⏹ Stopped by user.")
            self._running = False
            self._start_btn.configure(state="normal", text="▶   START DOWNLOAD")
            self._stop_btn.configure(state="disabled")

    # ─────────────────────────────────────────────────────────────────────────
    # QUEUE POLLER  (runs every 100 ms on the main thread)
    # ─────────────────────────────────────────────────────────────────────────

    def _poll_queue(self):
        try:
            while True:
                msg = self._log_queue.get_nowait()
                self._handle_message(msg)
        except queue.Empty:
            pass
        self.after(50, self._poll_queue)

    def _handle_message(self, msg: dict):
        mtype = msg["type"]

        if mtype == "log":
            self._log(msg["msg"], level=msg.get("level", "info"))

        elif mtype == "batch_start":
            self._done_titles = []
            self._failed_titles = []
            self._skipped_titles = []
            self._done_lbl.configure(text="0")
            self._fail_lbl.configure(text="0")
            self._skip_lbl.configure(text="0")
            self._data_lbl.configure(text="0 B")
            self._elapsed_lbl.configure(text="0s")
            self._batch_progress.set(0)
            self._batch_pct_lbl.configure(text=f"0 / {msg['total']}")
            self._current_session_bytes = 0

        elif mtype == "track_start":
            self._file_progress.set(0)
            self._file_pct_lbl.configure(text="0%")
            self._file_speed_lbl.configure(text="")
            self._file_eta_lbl.configure(text="")

        elif mtype == "track_name":
            self._now_lbl.configure(text=msg.get("name", "Unknown"))

        elif mtype == "file_progress":
            p = msg.get("pct", 0) / 100.0
            self._file_progress.set(min(p, 1.0))
            self._file_pct_lbl.configure(text=f"{msg.get('pct',0):.1f}%")
            self._file_speed_lbl.configure(text=f"⚡ {fmt_bytes(msg.get('speed',0))}/s")
            self._file_eta_lbl.configure(text=f"ETA {fmt_time(msg.get('eta',0))}")

        elif mtype == "track_success":
            title = msg.get("title", "Unknown Track")
            record = {
                "title": title,
                "fsize": msg.get("fsize", 0),
                "path": msg.get("file_path", ""),
                "time": msg.get("track_elapsed", 0)
            }
            if not any(r['title'] == title for r in self._done_titles):
                self._done_titles.append(record)
            
            self._done_lbl.configure(text=str(len(self._done_titles)))
            
            # Progress counters
            idx = msg.get("idx", 0); total = msg.get("total", 1)
            self._batch_progress.set(min(idx / total, 1.0))
            self._batch_pct_lbl.configure(text=f"{idx} / {total}")
            
            # Data stats
            if "fsize" in msg:
                self._current_session_bytes += msg["fsize"]
                self._data_lbl.configure(text=fmt_bytes(self._current_session_bytes))
            
            # Player integration
            if "file_path" in msg:
                fp = msg["file_path"]
                if fp not in self._current_session_files:
                    self._current_session_files.append(fp)
                
                if self._autoplay_var.get():
                    self._load_track(fp, start_now=True)
                else:
                    # Sync library but don't interrupt playback
                    self._refresh_playlist()
                
            if "track_elapsed" in msg:
                self._elapsed_lbl.configure(text=fmt_time(msg["track_elapsed"]))

        elif mtype == "track_fail":
            title = msg.get("title", "Unknown Failure")
            record = {
                "title": title,
                "error": msg.get("error", "General Extraction Error")
            }
            if not any(r['title'] == title for r in self._failed_titles):
                self._failed_titles.append(record)
            self._fail_lbl.configure(text=str(len(self._failed_titles)))

        elif mtype == "track_skip":
            title = msg.get("title", "Already Exists")
            record = {
                "title": title,
                "reason": msg.get("reason", "File with same name found in folder")
            }
            if not any(r['title'] == title for r in self._skipped_titles):
                self._skipped_titles.append(record)
            
            self._skip_lbl.configure(text=str(len(self._skipped_titles)))
            idx = msg["idx"]; total = msg["total"]
            self._batch_progress.set(min(idx / total, 1.0))
            self._batch_pct_lbl.configure(text=f"{idx} / {total}")
            
            if "batch_eta" in msg:
                self._batch_eta_lbl.configure(text=fmt_time(msg["batch_eta"]))

        elif mtype == "batch_done":
            self._on_batch_complete(msg)

    def _update_rolling_total(self, folder: str):
        """Calculates total size of all MP3s in the folder and updates UI."""
        total_bytes = 0
        try:
            if os.path.exists(folder):
                for f in os.listdir(folder):
                    if f.lower().endswith(".mp3"):
                        total_bytes += os.path.getsize(os.path.join(folder, f))
            self._data_lbl.configure(text=fmt_bytes(total_bytes))
        except:
            pass

    def _clear_console(self):
        self._console.configure(state="normal")
        self._console.delete("0.0", "end")
        self._console.configure(state="disabled")

    def _on_batch_complete(self, msg: dict):
        self._running = False
        self._start_btn.configure(state="normal", text="▶   START BATCH DOWNLOAD")
        self._stop_btn.configure(state="disabled")

        total   = msg["total"]
        success = msg["success"]
        failed  = msg["failed"]
        skipped = msg["skipped"]
        elapsed = msg["elapsed"]
        tbytes  = msg["total_bytes"]

        self._done_lbl.configure(text=str(success))
        self._fail_lbl.configure(text=str(failed))
        self._skip_lbl.configure(text=str(skipped))
        self._elapsed_lbl.configure(text=fmt_time(elapsed))
        self._data_lbl.configure(text=fmt_bytes(tbytes))
        self._batch_progress.set(1.0 if total else 0)
        self._batch_eta_lbl.configure(text="Done")

        self._log(
            f"\n━━━ BATCH COMPLETE ━━━\n"
            f"✔ Success: {success}\n"
            f"✖ Failed: {failed}\n"
            f"⏭ Skipped: {skipped}\n"
            f"⬇ Data: {fmt_bytes(tbytes)}\n"
            f"⏱ Time: {fmt_time(elapsed)}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━",
            level="header",
        )

        self._status_lbl.configure(text="   Finished   ")

    def _write_summary_log(self, results: list, elapsed: float, total_bytes: float, folder: str):
        try:
            ts        = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            log_path  = os.path.join(folder, "summary.txt")
            succ  = [r for r in results if r["status"] == "success"]
            fail  = [r for r in results if r["status"] == "failed"]
            skip  = [r for r in results if r["status"] == "skipped"]

            lines = [
                "=" * 62,
                f"  {self.APP_TITLE}  —  Download Summary",
                f"  Generated: {ts}",
                "=" * 62,
                f"  Total tracks : {len(results)}",
                f"  ✔ Success    : {len(succ)}",
                f"  ✖ Failed     : {len(fail)}",
                f"  ⏭ Skipped    : {len(skip)}",
                f"  ⬇ Data usage : {fmt_bytes(total_bytes)}",
                f"  ⏱ Time taken : {fmt_time(elapsed)}",
                "=" * 62,
                "",
                "✔ SUCCESS",
                "-" * 62,
            ]
            for r in succ:
                lines.append(f"  {r['title']}  ({fmt_bytes(r['size'])}, {fmt_time(r['elapsed'])})")
            lines += ["", "✖ FAILED", "-" * 62]
            for r in fail:
                lines.append(f"  {r['title']}")
                lines.append(f"  URL: {r['url']}")
            lines += ["", "⏭ SKIPPED", "-" * 62]
            for r in skip:
                lines.append(f"  {r['title']}")
            lines.append("")
            lines.append("=" * 62)

            with open(log_path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines))

            self._log(f"  📄 Summary log saved → {log_path}", level="success")
        except Exception as e:
            self._log(f"  ⚠ Could not write log: {e}", level="warning")

    # ─────────────────────────────────────────────────────────────────────────
    # HELPER: CONSOLE LOGGING
    # ─────────────────────────────────────────────────────────────────────────

    def _log(self, msg: str, level: str = "info"):
        color_map = {
            "header":  ACCENT_GREEN,
            "success": TEXT_SUCCESS,
            "error":   TEXT_ERROR,
            "warning": TEXT_WARNING,
            "info":    TEXT_PRIMARY,
        }
        self._console.configure(state="normal")
        self._console.insert("end", msg + "\n")
        self._console.configure(state="disabled")
        self._console.see("end")

    def _reset_stats(self):
        self._done_titles = []
        self._failed_titles = []
        self._skipped_titles = []
        self._done_lbl.configure(text="0")
        self._fail_lbl.configure(text="0")
        self._skip_lbl.configure(text="0")
        self._elapsed_lbl.configure(text="0s")
        self._data_lbl.configure(text="0 B")
        self._now_lbl.configure(text="—")
        self._batch_eta_lbl.configure(text="—")
        self._file_progress.set(0)
        self._batch_progress.set(0)
        self._file_pct_lbl.configure(text="0%")
        self._file_speed_lbl.configure(text="")
        self._file_eta_lbl.configure(text="")
        self._batch_pct_lbl.configure(text="0 / 0")
        self._current_session_bytes = 0 


# ─────────────────────────────────────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app = BisayaMusicHubApp()
    app.mainloop()
