#!/usr/bin/env python3
"""
NVR – multi-kamera, kruhový buffer v RAM + detekce průletu přes MQTT
=====================================================================
Konfigurace: conf.yaml
"""

import sys, time, json, shutil, signal, logging, threading, tempfile
from datetime import datetime, timezone
from pathlib import Path
import subprocess
import paho.mqtt.client as mqtt
import yaml

# ─── Načtení konfigurace ──────────────────────────────────────────────────────
CONFIG_FILE = Path("conf.yaml")

def load_config() -> dict:
    with open(CONFIG_FILE) as f:
        return yaml.safe_load(f)

# ─── Globální nastavení ───────────────────────────────────────────────────────
MQTT_BROKER   = "ip"
MQTT_PORT     = 1883
MQTT_USERNAME = "user"
MQTT_PASSWORD = "pass"

SEGMENT_DURATION   = 3
PRE_BUFFER_SEC     = 15
POST_DETECTION_SEC = 15

RAM_BASE    = Path("/dev/shm/nvr_buffer")
OUTPUT_BASE = Path("./nvr")

LOG_LEVEL = logging.INFO
# ──────────────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("nvr")

_shutdown = threading.Event()


# ─── Pomocné funkce ───────────────────────────────────────────────────────────
def sorted_segments(directory: Path) -> list[Path]:
    return sorted(directory.glob("buffer_*_*.ts"))


def get_segment_duration(seg: Path) -> float:
    try:
        result = subprocess.run([
            "ffprobe", "-v", "quiet",
            "-show_entries", "format=duration",
            "-of", "csv=p=0",
            str(seg)
        ], capture_output=True, timeout=10)
        return round(float(result.stdout.decode().strip()), 3)
    except Exception:
        return float(SEGMENT_DURATION)


def write_m3u8(path: Path, segments: list[Path]):
    durations = [get_segment_duration(s) for s in segments]
    max_dur = max(durations) if durations else SEGMENT_DURATION
    total = sum(durations)
    log.info("M3U8 celkova delka: %.1fs, segmentu: %d", total, len(segments))
    with open(path, "w") as f:
        f.write("#EXTM3U\n")
        f.write("#EXT-X-VERSION:3\n")
        f.write(f"#EXT-X-TARGETDURATION:{int(max_dur) + 1}\n")
        f.write("#EXT-X-PLAYLIST-TYPE:VOD\n")
        for i, (seg, dur) in enumerate(zip(segments, durations)):
            # seg je absolutní cesta v out_ts (did/stream_type/buffer_*.ts)
            # z m3u8 ve out_m3u8 potřebujeme relativní cestu ts/did/stream_type/název
            rel = Path("ts") / seg.parent.parent.name / seg.parent.name / seg.name
            if i > 0:
                f.write("#EXT-X-DISCONTINUITY\n")
            f.write(f"#EXTINF:{dur:.3f},\n")
            f.write(f"{rel}\n")
        f.write("#EXT-X-ENDLIST\n")


def write_meta(path: Path, did: str, stream_type: str, detection_ts: str):
    """Uloží .meta JSON soubor vedle m3u8/mp4."""
    dt = datetime.strptime(detection_ts, "%Y%m%d_%H%M%S").replace(tzinfo=timezone.utc)
    meta = {
        "did": did,
        "stream_type": stream_type,
        "datetime": dt.isoformat(),
        "timestamp": int(dt.timestamp()),
        "date": dt.strftime("%Y-%m-%d"),
        "time": dt.strftime("%H:%M:%S"),
    }
    with open(path, "w") as f:
        json.dump(meta, f, indent=2)
    log.debug("Meta: %s", path)


def create_thumbnail(segments: list[Path], thumb_path: Path):
    """Vygeneruje JPEG thumbnail z přibližné půlky záznamu."""
    durations = [get_segment_duration(s) for s in segments]
    total = sum(durations)
    target = total / 2  # střed záznamu

    # Najdi segment a offset kde je střed
    acc = 0.0
    thumb_seg = segments[0]
    seg_offset = 0.0
    for seg, dur in zip(segments, durations):
        if acc + dur >= target:
            thumb_seg = seg
            seg_offset = target - acc
            break
        acc += dur

    cmd = [
        "ffmpeg", "-y",
        "-loglevel", "warning",
        "-ss", f"{seg_offset:.3f}",
        "-i", str(thumb_seg),
        "-frames:v", "1",
        "-q:v", "2",
        str(thumb_path),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=30)
        if result.returncode != 0:
            log.error("Thumbnail chyba: %s", result.stderr.decode(errors="replace"))
        else:
            log.info("Thumbnail: %s", thumb_path)
    except Exception as e:
        log.error("Thumbnail chyba: %s", e)


def create_mp4_concat(segments: list[Path], mp4_path: Path):
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt",
                                     delete=False, dir="/tmp") as f:
        concat_list = Path(f.name)
        for seg in segments:
            f.write(f"file '{seg.absolute()}'\n")
    log.info("Vytvarim MP4 (concat): %s", mp4_path)
    cmd = [
        "ffmpeg", "-y",
        "-loglevel", "warning",
        "-f", "concat",
        "-safe", "0",
        "-i", str(concat_list),
        "-c", "copy",
        "-movflags", "+faststart",
        str(mp4_path),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=120)
        if result.returncode != 0:
            log.error("ffmpeg MP4 chyba:\n%s", result.stderr.decode(errors="replace"))
        else:
            log.info("MP4 ulozen: %s (%.1f MB)", mp4_path,
                     mp4_path.stat().st_size / 1e6)
    except subprocess.TimeoutExpired:
        log.error("Timeout pri vytvareni MP4!")
    except Exception as e:
        log.error("Chyba: %s", e)
    finally:
        concat_list.unlink(missing_ok=True)


# ─── Třída jedné kamery ───────────────────────────────────────────────────────
class CameraRecorder:
    """
    Jeden CameraRecorder = jeden RTSP stream jednoho typu (indoor/outdoor).
    Má vlastní RAM buffer, stavový automat a finalizační thread.
    """

    IDLE       = "IDLE"
    RECORDING  = "RECORDING"
    FINALIZING = "FINALIZING"

    def __init__(self, did: str, stream_type: str, rtsp_url: str, extra_args: list = None):
        self.did         = did
        self.stream_type = stream_type
        self.rtsp_url    = rtsp_url
        self.extra_args  = [str(a) for a in (extra_args or [])]
        self.name        = f"{did}/{stream_type}"

        self.ram_dir     = RAM_BASE / did / stream_type
        self.out_ts      = OUTPUT_BASE / "m3u8" / "ts" / did / stream_type
        self.out_m3u8    = OUTPUT_BASE / "m3u8"
        self.out_mp4     = OUTPUT_BASE

        self._lock           = threading.Lock()
        self._state          = self.IDLE
        self._last_det_time  = None
        self._finalize_event = threading.Event()

        self.ram_dir.mkdir(parents=True, exist_ok=True)

        self._pre_buffer_segments = max(1, round(PRE_BUFFER_SEC / SEGMENT_DURATION))

    # ── Stavový automat ───────────────────────────────────────────────────────
    def trigger_detection(self):
        now = time.time()
        with self._lock:
            self._last_det_time = now
            if self._state == self.IDLE:
                self._state = self.RECORDING
                log.info("[%s] ▶ Nahravani zahajeno (%s UTC)",
                         self.name,
                         datetime.fromtimestamp(now, tz=timezone.utc).strftime("%H:%M:%S"))
            elif self._state == self.RECORDING:
                log.info("[%s] ↺ Post-window prodlouzen (%s UTC)",
                         self.name,
                         datetime.fromtimestamp(now, tz=timezone.utc).strftime("%H:%M:%S"))
            elif self._state == self.FINALIZING:
                self._state = self.RECORDING
                log.info("[%s] ↺ Nova detekce behem finalizace", self.name)

    def _try_end_recording(self) -> bool:
        with self._lock:
            if self._state != self.RECORDING:
                return False
            if self._last_det_time is None:
                return False
            if (time.time() - self._last_det_time) >= POST_DETECTION_SEC:
                self._state = self.FINALIZING
                log.info("[%s] Post-window vyprselo, finalizuji...", self.name)
                return True
        return False

    def _end_finalizing(self):
        with self._lock:
            self._state = self.IDLE
        log.info("[%s] IDLE", self.name)

    def _get_state(self):
        with self._lock:
            return self._state, self._last_det_time

    # ── Buffer ────────────────────────────────────────────────────────────────
    def _prune_buffer(self):
        segs = sorted_segments(self.ram_dir)
        durations = [get_segment_duration(s) for s in segs]
        total = sum(durations)
        while total > PRE_BUFFER_SEC and len(segs) > 1:
            oldest = segs.pop(0)
            total -= durations.pop(0)
            try:
                oldest.unlink()
                log.debug("[%s] Odstranen segment: %s (buffer: %.1fs)",
                          self.name, oldest.name, total)
            except FileNotFoundError:
                pass

    # ── Segment watcher ───────────────────────────────────────────────────────
    def _segment_watcher(self):
        known: set[str] = set()
        while not _shutdown.is_set():
            current = {p.name for p in self.ram_dir.glob("buffer_*_*.ts")}
            new_files = current - known
            if new_files:
                time.sleep(0.3)
                for name in sorted(new_files):
                    log.debug("[%s] Novy segment: %s", self.name, name)
                known = current
                st, _ = self._get_state()
                if st == self.IDLE:
                    self._prune_buffer()

            if self._try_end_recording():
                self._finalize_event.set()

            time.sleep(0.5)

    # ── Finalizační thread ────────────────────────────────────────────────────
    def _finalizer(self):
        while not _shutdown.is_set():
            triggered = self._finalize_event.wait(timeout=2.0)
            if _shutdown.is_set():
                break
            if not triggered:
                continue
            self._finalize_event.clear()

            st, last_det = self._get_state()
            if st != self.FINALIZING:
                continue

            time.sleep(SEGMENT_DURATION + 0.5)

            st, last_det = self._get_state()
            if st != self.FINALIZING:
                continue

            detection_ts = datetime.fromtimestamp(
                last_det, tz=timezone.utc).strftime("%Y%m%d_%H%M%S")
            self._finalize(detection_ts)
            self._end_finalizing()

    # ── Finalizace ────────────────────────────────────────────────────────────
    def _finalize(self, detection_ts: str):
        segs = sorted_segments(self.ram_dir)
        if not segs:
            log.warning("[%s] Zadne segmenty!", self.name)
            return

        log.info("[%s] Finalizuji %d segmentu (%s)", self.name, len(segs), detection_ts)
        for s in segs:
            log.info("[%s]   %s", self.name, s.name)

        self.out_ts.mkdir(parents=True, exist_ok=True)
        self.out_m3u8.mkdir(parents=True, exist_ok=True)
        self.out_mp4.mkdir(parents=True, exist_ok=True)

        # Prefix pro soubory: did_streamtype_timestamp
        prefix = f"{self.did}_{self.stream_type}_{detection_ts}"

        # 1) Kopíruj segmenty
        copied: list[Path] = []
        for seg in segs:
            dest = self.out_ts / seg.name
            try:
                shutil.copy2(seg, dest)
                copied.append(dest)
            except Exception as e:
                log.error("[%s] Kopie %s: %s", self.name, seg.name, e)

        if not copied:
            return

        # 2) M3U8
        m3u8_path = self.out_m3u8 / f"detection_{prefix}.m3u8"
        write_m3u8(m3u8_path, copied)
        log.info("[%s] M3U8: %s", self.name, m3u8_path)

        # 3) M3U8 meta
        write_meta(
            self.out_m3u8 / f"detection_{prefix}.m3u8.meta",
            self.did, self.stream_type, detection_ts
        )

        # 3b) Thumbnail z půlky videa
        create_thumbnail(copied, self.out_m3u8 / f"detection_{prefix}.m3u8.jpg")

        # 4) MP4
        mp4_path = self.out_mp4 / f"detection_{prefix}.mp4"
        create_mp4_concat(copied, mp4_path)

        # 5) MP4 meta
        write_meta(
            self.out_mp4 / f"detection_{prefix}.mp4.meta",
            self.did, self.stream_type, detection_ts
        )

        # 6) Vyčisti RAM
        for seg in segs:
            try:
                seg.unlink()
            except FileNotFoundError:
                pass

    # ── FFmpeg s auto-restartem ───────────────────────────────────────────────
    def _run_segmenter(self):
        segment_pattern = str(self.ram_dir / "buffer_%Y%m%d_%H%M%S.ts")
        cmd = [
            "ffmpeg",
            "-loglevel", "warning",
            "-rtsp_transport", "tcp",
        ] + self.extra_args + [
            "-i", self.rtsp_url,
            "-c:v", "copy",
            "-c:a", "aac",
            "-b:a", "128k",
            "-f", "segment",
            "-segment_time", str(SEGMENT_DURATION),
            "-strftime", "1",
            "-reset_timestamps", "1",
            "-segment_format", "mpegts",
            segment_pattern,
        ]

        retry_delay = 5
        while not _shutdown.is_set():
            log.info("[%s] Spoustim ffmpeg...", self.name)
            proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.PIPE)
            for line in proc.stderr:
                if _shutdown.is_set():
                    proc.kill()
                    break
                txt = line.decode(errors="replace").strip()
                if txt:
                    log.debug("[%s][ffmpeg] %s", self.name, txt)
            proc.wait()
            if _shutdown.is_set():
                break
            log.warning("[%s] ffmpeg skoncil (kod %d), restart za %ds...",
                        self.name, proc.returncode, retry_delay)
            time.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, 60)

    def start(self):
        """Spustí všechny thready pro tuto kameru."""
        threading.Thread(target=self._segment_watcher, daemon=True,
                         name=f"watcher-{self.name}").start()
        threading.Thread(target=self._finalizer, daemon=True,
                         name=f"finalizer-{self.name}").start()
        threading.Thread(target=self._run_segmenter, daemon=True,
                         name=f"ffmpeg-{self.name}").start()
        log.info("[%s] Kamera spustena (RTSP: %s)", self.name, self.rtsp_url)


# ─── MQTT ─────────────────────────────────────────────────────────────────────
def build_topic_map(cameras: dict) -> dict[str, list[CameraRecorder]]:
    """Vrátí mapping topic → [CameraRecorder, ...] a spustí recordery."""
    topic_map: dict[str, list[CameraRecorder]] = {}
    for did, cfg in cameras.items():
        topic = cfg["topic"]
        recorders = []
        for stream_type, stream_cfg in cfg["streams"].items():
            # stream může být jen string (url) nebo dict s url + ffmpeg_extra_args
            if isinstance(stream_cfg, str):
                rtsp_url = stream_cfg
                extra = []
            else:
                rtsp_url = stream_cfg["url"]
                extra = stream_cfg.get("ffmpeg_extra_args", [])
            rec = CameraRecorder(did, stream_type, rtsp_url, extra_args=extra)
            rec.start()
            recorders.append(rec)
        topic_map[topic] = recorders
        log.info("Topic '%s' → %d stream(u): %s",
                 topic, len(recorders), list(cfg["streams"].keys()))
    return topic_map


def start_mqtt(topic_map: dict[str, list[CameraRecorder]]) -> mqtt.Client:
    def on_connect(client, userdata, flags, rc, *args):
        if rc == 0:
            log.info("MQTT pripojeno → %s:%d", MQTT_BROKER, MQTT_PORT)
            for topic in topic_map:
                client.subscribe(topic)
                log.info("Subscribed: %s", topic)
        else:
            log.error("MQTT pripojeni selhalo (rc=%d)", rc)

    def on_disconnect(client, userdata, rc, *args):
        log.warning("MQTT odpojeno (rc=%d)", rc)

    def on_message(client, userdata, msg):
        topic = msg.topic
        try:
            data = json.loads(msg.payload.decode())
        except Exception:
            log.warning("MQTT: nelze parsovat payload z '%s'", topic)
            return

        inner = data.get("payload", data)
        log.info("MQTT <- '%s'  timestamp=%s", topic, inner.get("timestamp", "?"))

        recorders = topic_map.get(topic, [])
        if not recorders:
            log.debug("Zadny recorder pro topic: %s", topic)
            return
        for rec in recorders:
            rec.trigger_detection()

    try:
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    except AttributeError:
        client = mqtt.Client()

    client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)
    client.on_connect    = on_connect
    client.on_disconnect = on_disconnect
    client.on_message    = on_message

    try:
        client.connect(MQTT_BROKER, MQTT_PORT, keepalive=60)
    except Exception as e:
        log.error("Nelze se pripojit k MQTT: %s", e)

    client.loop_start()
    return client


# ─── Main ─────────────────────────────────────────────────────────────────────
def main():
    def handle_signal(sig, frame):
        log.info("Ukoncuji NVR...")
        _shutdown.set()
        sys.exit(0)

    signal.signal(signal.SIGINT,  handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    cfg = load_config()
    cameras = cfg.get("cameras", {})
    if not cameras:
        log.error("Zadne kamery v conf.yaml!")
        sys.exit(1)

    log.info("=== NVR start === (%d kamer)", len(cameras))

    topic_map = build_topic_map(cameras)
    mqtt_client = start_mqtt(topic_map)

    # Hlavní thread jen čeká na shutdown
    try:
        while not _shutdown.is_set():
            time.sleep(1)
    except KeyboardInterrupt:
        pass

    mqtt_client.loop_stop()
    log.info("NVR ukoncen.")


if __name__ == "__main__":
    main()

