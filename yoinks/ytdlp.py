from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional

from .fmt import format_bytes

IS_WINDOWS = sys.platform == "win32"
YOINKS_DIR = Path.home() / ".yoinks" / "bin"
RELEASE_BASE = "https://github.com/yt-dlp/yt-dlp/releases/latest/download"
FFMPEG_BASE = "https://github.com/yt-dlp/FFmpeg-Builds/releases/download/latest"
USER_AGENT = "yoinks"


class Cancelled(Exception):
    pass


class Abort:
    """Cancellation token that also kills every child process registered with it."""

    def __init__(self) -> None:
        self._event = threading.Event()
        self._lock = threading.Lock()
        self._procs: List[subprocess.Popen] = []

    def is_set(self) -> bool:
        return self._event.is_set()

    def set(self, wait: bool = False) -> None:
        with self._lock:
            if self._event.is_set():
                return
            self._event.set()
            procs = list(self._procs)
        for proc in procs:
            if wait:
                _kill_tree(proc)
            else:
                threading.Thread(target=_kill_tree, args=(proc,), daemon=True).start()

    def register(self, proc: subprocess.Popen) -> None:
        with self._lock:
            if not self._event.is_set():
                self._procs.append(proc)
                return
        _kill_tree(proc)

    def unregister(self, proc: subprocess.Popen) -> None:
        with self._lock:
            if proc in self._procs:
                self._procs.remove(proc)


def _kill_tree(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    if IS_WINDOWS:
        # yt-dlp spawns ffmpeg — take the whole tree down, not just the parent
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
            )
        except (OSError, subprocess.SubprocessError):
            pass
    try:
        proc.kill()
    except OSError:
        pass


def _child_env() -> dict:
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"
    return env


def _ytdlp_asset_name() -> str:
    if IS_WINDOWS:
        return "yt-dlp.exe"
    if sys.platform == "darwin":
        return "yt-dlp_macos"
    return "yt-dlp_linux_aarch64" if platform.machine().lower() in ("arm64", "aarch64") else "yt-dlp_linux"


def _command_works(cmd: str, args: List[str]) -> bool:
    try:
        result = subprocess.run(
            [cmd, *args],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def _fetch(url: str, dest: Path, abort: Optional[Abort], what: str,
           on_progress: Optional[Callable[[int, Optional[int]], None]] = None) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        response = urllib.request.urlopen(request, timeout=30)
    except urllib.error.HTTPError as error:
        raise RuntimeError(f"Could not download {what} ({error.code}). Check your connection and try again.")
    except (urllib.error.URLError, OSError):
        raise RuntimeError(f"Could not download {what}. Check your connection and try again.")
    total = response.headers.get("Content-Length")
    total_bytes = int(total) if total and total.isdigit() else None
    done = 0
    try:
        with response, open(dest, "wb") as out:
            while True:
                if abort and abort.is_set():
                    raise Cancelled()
                chunk = response.read(256 * 1024)
                if not chunk:
                    break
                out.write(chunk)
                done += len(chunk)
                if on_progress:
                    on_progress(done, total_bytes)
    except BaseException:
        try:
            dest.unlink()
        except OSError:
            pass
        raise


def _command_output(cmd: str, args: List[str]) -> Optional[str]:
    """stdout of a command that exited 0, else None."""
    try:
        result = subprocess.run(
            [cmd, *args],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.decode("utf-8", "replace").strip()


def managed_ytdlp_path() -> Path:
    """Where yoinks keeps its own copy of yt-dlp."""
    return YOINKS_DIR / ("yt-dlp.exe" if IS_WINDOWS else "yt-dlp")


@dataclass
class YtDlpInfo:
    path: str
    version: str
    # 'system' = found on PATH, 'yoinks' = our copy in ~/.yoinks/bin
    source: str


def find_ytdlp() -> Optional[YtDlpInfo]:
    """The yt-dlp yoinks would use — system install first, then its own copy. Never downloads."""
    system = shutil.which("yt-dlp")
    if system:
        version = _command_output(system, ["--version"])
        if version is not None:
            return YtDlpInfo(system, version, "system")
    local = managed_ytdlp_path()
    if local.exists():
        version = _command_output(str(local), ["--version"])
        if version is not None:
            return YtDlpInfo(str(local), version, "yoinks")
    return None


def ensure_ytdlp(on_status: Callable[[str], None], abort: Optional[Abort] = None) -> str:
    """System yt-dlp first, then a previously downloaded copy, then fetch the standalone binary."""
    found = find_ytdlp()
    if found:
        return found.path

    local = managed_ytdlp_path()
    on_status("first run: fetching yt-dlp…")
    YOINKS_DIR.mkdir(parents=True, exist_ok=True)
    tmp = local.with_name(local.name + ".download")
    _fetch(f"{RELEASE_BASE}/{_ytdlp_asset_name()}", tmp, abort, "yt-dlp")
    os.chmod(tmp, 0o755)
    os.replace(tmp, local)
    return str(local)


def _ffmpeg_asset() -> Optional[str]:
    machine = platform.machine().lower()
    arm = machine in ("arm64", "aarch64")
    if IS_WINDOWS:
        return f"ffmpeg-master-latest-{'winarm64' if arm else 'win64'}-gpl.zip"
    if sys.platform.startswith("linux") and machine in ("x86_64", "amd64", "arm64", "aarch64"):
        return f"ffmpeg-master-latest-{'linuxarm64' if arm else 'linux64'}-gpl.tar.xz"
    return None  # macOS: `brew install ffmpeg`


def _extract_ffmpeg(archive: Path, names: List[str]) -> None:
    wanted = {f"bin/{name}": name for name in names}

    def target_for(member_name: str) -> Optional[str]:
        for suffix, name in wanted.items():
            if member_name.replace("\\", "/").endswith("/" + suffix):
                return name
        return None

    found = set()
    if archive.name.endswith(".zip"):
        with zipfile.ZipFile(archive) as zf:
            for info in zf.infolist():
                name = target_for(info.filename)
                if name:
                    with zf.open(info) as src, open(YOINKS_DIR / name, "wb") as dst:
                        shutil.copyfileobj(src, dst)
                    found.add(name)
    else:
        with tarfile.open(archive, "r:xz") as tf:
            for member in tf:
                name = target_for(member.name) if member.isfile() else None
                if name:
                    src = tf.extractfile(member)
                    if src:
                        with src, open(YOINKS_DIR / name, "wb") as dst:
                            shutil.copyfileobj(src, dst)
                        os.chmod(YOINKS_DIR / name, 0o755)
                        found.add(name)
    if found != set(names):
        raise RuntimeError("ffmpeg archive is missing binaries")


def find_ffmpeg(on_status: Callable[[str], None], abort: Optional[Abort] = None) -> Optional[str]:
    """
    ffmpeg for merging / mp3 extraction: system install first, then ~/.yoinks/bin,
    then a one-time download. Returns the --ffmpeg-location to pass, or None when
    yt-dlp should find it itself (or it isn't available — single-file formats still work).
    """
    if _command_works("ffmpeg", ["-version"]):
        return None  # on PATH, yt-dlp finds it itself
    exe = ".exe" if IS_WINDOWS else ""
    local = YOINKS_DIR / f"ffmpeg{exe}"
    if local.exists() and (YOINKS_DIR / f"ffprobe{exe}").exists() and _command_works(str(local), ["-version"]):
        return str(YOINKS_DIR)
    asset = _ffmpeg_asset()
    if not asset:
        return None
    YOINKS_DIR.mkdir(parents=True, exist_ok=True)
    download_path = YOINKS_DIR / f"{asset}.download"
    archive = YOINKS_DIR / asset  # extension decides zip vs tar.xz
    on_status("first run: fetching ffmpeg…")

    last = [-1]

    def progress(done: int, total: Optional[int]) -> None:
        if total:
            pct = done * 100 // total
            if pct != last[0]:
                last[0] = pct
                on_status(f"first run: fetching ffmpeg… {pct}%")

    try:
        _fetch(f"{FFMPEG_BASE}/{asset}", download_path, abort, "ffmpeg", progress)
        os.replace(download_path, archive)
        on_status("first run: unpacking ffmpeg…")
        _extract_ffmpeg(archive, [f"ffmpeg{exe}", f"ffprobe{exe}"])
    except Cancelled:
        raise
    except Exception:
        return None
    finally:
        for leftover in (download_path, archive):
            try:
                leftover.unlink()
            except OSError:
                pass
    return str(YOINKS_DIR) if _command_works(str(local), ["-version"]) else None


@dataclass
class ProbeResult:
    info: dict
    # raw -J output saved to disk so downloads can skip re-extraction via --load-info-json
    info_json_path: str


def _clean_error(stderr: str) -> str:
    lines = [line.strip() for line in stderr.splitlines()]
    errors = [line for line in lines if line.startswith("ERROR:")]
    if not errors:
        return ""
    return re.sub(r"^ERROR:\s*(\[[^\]]+\]\s*)?", "", errors[-1])


def probe(ytdlp: str, url: str, abort: Optional[Abort] = None) -> ProbeResult:
    proc = subprocess.Popen(
        [ytdlp, "-J", "--no-playlist", "--no-warnings", "--encoding", "utf-8", url],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=_child_env(),
    )
    if abort:
        abort.register(proc)
    try:
        out, err = proc.communicate()
    finally:
        if abort:
            abort.unregister(proc)
    if abort and abort.is_set():
        raise Cancelled()
    if proc.returncode != 0:
        raise RuntimeError(_clean_error(err.decode("utf-8", "replace")) or f"yt-dlp exited with code {proc.returncode}")
    try:
        info = json.loads(out.decode("utf-8", "replace"))
    except ValueError:
        raise RuntimeError("Could not parse video info from yt-dlp.")
    if not isinstance(info, dict):
        raise RuntimeError("Could not parse video info from yt-dlp.")
    path = os.path.join(tempfile.gettempdir(), f"yoinks-info-{os.getpid()}-{int(time.time() * 1000)}.json")
    with open(path, "wb") as fh:
        fh.write(out)
    return ProbeResult(info, path)


@dataclass
class DownloadChoice:
    label: str
    kind: str  # 'video' | 'audio'
    args: List[str] = field(default_factory=list)


MAX_VIDEO_CHOICES = 8


def _num(value) -> Optional[float]:
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _first(*values) -> Optional[float]:
    """JS `a ?? b ?? c` — first value that isn't missing (0 counts as present)."""
    for value in values:
        if _num(value) is not None:
            return value
    return None


def _has_codec(value) -> bool:
    return bool(value) and value != "none"


def _score_video(fmt: dict) -> float:
    score = _first(fmt.get("tbr")) or 0
    if fmt.get("ext") == "mp4":
        score += 10_000
    if str(fmt.get("vcodec") or "").startswith("avc"):
        score += 5_000
    return score


def _height_label(height: float) -> str:
    return str(int(height)) if float(height).is_integer() else str(height)


IMAGE_EXTS = {"jpg", "jpeg", "png", "gif", "webp", "avif", "bmp", "heic", "heif", "tif", "tiff", "jfif"}


def _image_choice(info: dict, formats: List[dict]) -> Optional[DownloadChoice]:
    """Direct image links (and image-only posts) — there is nothing to convert, save it as-is."""
    exts = {str(f.get("ext") or "").lower() for f in formats} if formats else {str(info.get("ext") or "").lower()}
    if not exts or not exts <= IMAGE_EXTS:
        return None
    best = formats[-1] if formats else info  # yt-dlp sorts formats worst → best
    ext = str(best.get("ext") or info.get("ext")).lower()
    size = _first(best.get("filesize"), best.get("filesize_approx"))
    size_label = f" · ~{format_bytes(size)}" if size else ""
    return DownloadChoice(kind="image", label=f"image · {ext}{size_label}", args=[])


def build_choices(info: dict) -> List[DownloadChoice]:
    formats = [f for f in (info.get("formats") or []) if isinstance(f, dict)]
    image = _image_choice(info, formats)
    if image:
        return [image]
    choices: List[DownloadChoice] = []

    audio_only = [f for f in formats if _has_codec(f.get("acodec")) and not _has_codec(f.get("vcodec"))]
    best_audio = (
        sorted(audio_only, key=lambda f: -(_first(f.get("abr"), f.get("tbr")) or 0))[0] if audio_only else None
    )
    audio_size = _first(best_audio.get("filesize"), best_audio.get("filesize_approx")) if best_audio else None

    videos = [f for f in formats if _has_codec(f.get("vcodec")) and _num(f.get("height"))]
    heights = sorted({f["height"] for f in videos}, reverse=True)

    for height in heights[:MAX_VIDEO_CHOICES]:
        candidates = [f for f in videos if f["height"] == height]
        best = sorted(candidates, key=lambda f: -_score_video(f))[0]
        muxed = _has_codec(best.get("acodec"))
        size = (_first(best.get("filesize"), best.get("filesize_approx")) or 0) + (0 if muxed else (audio_size or 0))
        size_label = f" · ~{format_bytes(size)}" if size > 0 else ""
        h = _height_label(height)
        choices.append(DownloadChoice(
            kind="video",
            label=f"{h}p · mp4{size_label}",
            args=["-f", f"bv*[height={h}]+ba/b[height={h}]/bv*[height<={h}]+ba/b", "--merge-output-format", "mp4"],
        ))

    if not choices:
        choices.append(DownloadChoice(
            kind="video", label="best available · mp4", args=["-f", "bv*+ba/b", "--merge-output-format", "mp4"],
        ))

    audio_size_label = f" · ~{format_bytes(audio_size)}" if audio_size else ""
    choices.append(DownloadChoice(
        kind="audio",
        label=f"audio only · mp3{audio_size_label}",
        args=["-f", "ba/b", "-x", "--audio-format", "mp3", "--audio-quality", "0"],
    ))
    return choices


@dataclass
class DownloadProgress:
    downloaded_bytes: float
    total_bytes: Optional[float]
    speed: Optional[float]
    eta: Optional[float]
    part: int
    # how many files this download resolves to (video+audio merges are 2)
    total_parts: int


PROGRESS_PREFIX = "YOINK|"
PROGRESS_TEMPLATE = (
    f"{PROGRESS_PREFIX}%(progress.downloaded_bytes)s|%(progress.total_bytes)s|"
    "%(progress.total_bytes_estimate)s|%(progress.speed)s|%(progress.eta)s"
)


def _to_number(value: Optional[str]) -> Optional[float]:
    if not value or value in ("NA", "None"):
        return None
    match = re.match(r"\s*[-+]?(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?", value)
    return float(match.group(0)) if match else None


CONCURRENT_FRAGMENTS = 4


def download(
    *,
    ytdlp: str,
    url: str,
    choice: DownloadChoice,
    out_dir: str,
    on_progress: Callable[[DownloadProgress], None],
    on_processing: Callable[[], None],
    ffmpeg_location: Optional[str] = None,
    info_json_path: Optional[str] = None,
    abort: Optional[Abort] = None,
) -> str:
    args = [
        *(["--load-info-json", info_json_path] if info_json_path else [url]),
        *choice.args,
        # YouTube and HLS sites serve fragmented media; four fragments at once
        # gets around per-connection throttling (no-op for single-file sites)
        "--concurrent-fragments",
        str(CONCURRENT_FRAGMENTS),
        "--no-playlist",
        "--no-warnings",
        "--newline",
        # --print implies --quiet, which suppresses progress and the
        # [Merger]/[ExtractAudio] lines the processing phase is detected from
        "--no-quiet",
        "--progress",
        "--progress-template",
        f"download:{PROGRESS_TEMPLATE}",
        "--print",
        "after_move:filepath",
        "--no-simulate",
        "--encoding",
        "utf-8",
        "-o",
        os.path.join(out_dir, "%(title).60s.%(ext)s"),
    ]
    if ffmpeg_location:
        args += ["--ffmpeg-location", ffmpeg_location]

    proc = subprocess.Popen(
        [ytdlp, *args],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=_child_env(),
    )
    if abort:
        abort.register(proc)

    stderr_chunks: List[bytes] = []
    reader = threading.Thread(target=lambda: stderr_chunks.append(proc.stderr.read()), daemon=True)
    reader.start()

    filepath = ""
    part = 0
    total_parts = 1
    last_downloaded = 0.0
    # every file yt-dlp writes this run, so a cancel can clean up after itself
    destinations: List[str] = []

    try:
        for raw in proc.stdout:
            line = raw.decode("utf-8", "replace").strip()
            if not line:
                continue
            if line.startswith(PROGRESS_PREFIX):
                fields = line[len(PROGRESS_PREFIX):].split("|") + [""] * 5
                downloaded, total, total_estimate, speed, eta = fields[:5]
                downloaded_bytes = _to_number(downloaded) or 0
                if downloaded_bytes < last_downloaded:
                    part += 1
                last_downloaded = downloaded_bytes
                total_bytes = _to_number(total)
                on_progress(DownloadProgress(
                    downloaded_bytes=downloaded_bytes,
                    total_bytes=total_bytes if total_bytes is not None else _to_number(total_estimate),
                    speed=_to_number(speed),
                    eta=_to_number(eta),
                    part=part,
                    total_parts=total_parts,
                ))
            elif "Downloading 1 format(s):" in line:
                # "[info] xxx: Downloading 1 format(s): 395+251" — each id is one file
                total_parts = len(line.split("format(s):", 1)[1].strip().split("+"))
            elif "[Merger]" in line or "[ExtractAudio]" in line:
                merging = re.match(r'^\[Merger\] Merging formats into "(.+)"$', line)
                extracting = re.match(r"^\[ExtractAudio\] Destination: (.+)$", line)
                target = (merging or extracting)
                if target:
                    destinations.append(target.group(1))
                on_processing()
            elif line.startswith("[download] Destination: "):
                destinations.append(line[len("[download] Destination: "):])
            elif os.path.isabs(line):
                filepath = line
        proc.wait()
    finally:
        if abort:
            abort.unregister(proc)
    reader.join(timeout=5)

    if abort and abort.is_set():
        # cancelled on purpose — don't leave half-written files behind
        _remove_partials(destinations)
        raise Cancelled("Download cancelled.")
    if proc.returncode == 0 and filepath:
        return filepath
    stderr = b"".join(stderr_chunks).decode("utf-8", "replace")
    raise RuntimeError(_clean_error(stderr) or f"Download failed (yt-dlp exit code {proc.returncode}).")


def _remove_partials(destinations: List[str]) -> None:
    for dest in destinations:
        for path in (dest, f"{dest}.part", f"{dest}.ytdl"):
            for _ in range(10):  # windows keeps files locked for a moment after a kill
                try:
                    os.remove(path)
                    break
                except FileNotFoundError:
                    break
                except OSError:
                    time.sleep(0.2)
