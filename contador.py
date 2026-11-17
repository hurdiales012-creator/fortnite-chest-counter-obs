"""Contador local de cofres: OBS WebSocket -> OCR -> overlay transparente."""

from __future__ import annotations
import argparse, base64, ctypes, json, logging, os, re, sys, threading, time, unicodedata, webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
import cv2
import numpy as np
from rapidocr_onnxruntime import RapidOCR
import obsws_python as obs

ROOT = Path(__file__).resolve().parent
ASSET_DIR = ROOT / "assets"
DATA_DIR = Path(os.environ.get("COFRES_DATA_DIR", str(ROOT / "datos"))).resolve()
PORT = 18746
OVERLAY_NAME = "Contador de cofres - Navegador"
NATIVE_SCENE = "Contador de cofres - Overlay"
NATIVE_COUNT = "Contador de cofres - Total"
LOCK = threading.RLock()
STOP = threading.Event()
STATE_FILE = DATA_DIR / "cuenta.json"
CONFIG_FILE = DATA_DIR / "config.local.json"
LOG = logging.getLogger("cofres")
DEFAULT_CONFIG = {
    "scene": "",
    "source": "",
    "interval": 0.4,
    "crop": [0.48, 0.20, 0.82, 0.64],
    "obs_config_path": "",
}


def load_json(path, fallback):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return fallback


def validated_config(data):
    result = dict(DEFAULT_CONFIG)
    if not isinstance(data, dict):
        return result
    for key in ("scene", "source", "obs_config_path"):
        if isinstance(data.get(key), str):
            result[key] = data[key].strip()
    try:
        result["interval"] = max(0.2, min(2.0, float(data.get("interval", 0.4))))
        crop = list(map(float, data.get("crop", result["crop"])))
        if len(crop) == 4 and 0 <= crop[0] < crop[2] <= 1 and 0 <= crop[1] < crop[3] <= 1:
            result["crop"] = crop
    except (TypeError, ValueError):
        pass
    return result


CONFIG = validated_config(load_json(CONFIG_FILE, {}))
SCENE = CONFIG["scene"]
try:
    starting_count = max(0, min(9999999, int(load_json(STATE_FILE, {}).get("count", 0))))
except (TypeError, ValueError, AttributeError):
    starting_count = 0
STATE = {
    "count": starting_count,
    "status": "Iniciando detector...",
    "connected": False,
    "paused": False,
    "source": CONFIG["source"],
    "scenes": [],
    "sources": [],
    "last_event": "",
    "last_text": "",
    "fps": 0,
    "installed": False,
    "scene": SCENE,
    "last_frame": "",
    "demo": False,
}


def save_config():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    temp = CONFIG_FILE.with_suffix(".tmp")
    temp.write_text(json.dumps(CONFIG, indent=2, ensure_ascii=False), encoding="utf-8")
    temp.replace(CONFIG_FILE)


def save_count():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    temp = STATE_FILE.with_suffix(".tmp")
    temp.write_text(json.dumps({"count": STATE["count"]}, indent=2), encoding="utf-8")
    temp.replace(STATE_FILE)
    write_native_count()


def change_count(delta=None, value=None, automatic=False):
    with LOCK:
        STATE["count"] = max(0, int(value if value is not None else STATE["count"] + delta))
        if automatic:
            STATE["last_event"] = datetime.now().strftime("%H:%M:%S")
        save_count()
        LOG.info("%s: %s", "Cofre detectado" if automatic else "Ajuste manual", STATE["count"])


def normal(text):
    return "".join(
        c for c in unicodedata.normalize("NFKD", text.upper()) if not unicodedata.combining(c)
    )


class Gate:
    """One count per visible reward, rearmed only after confirmed absence."""

    def __init__(self):
        self.armed = True
        self.last_seen = -1e9
        self.last_count = -1e9

    def feed(self, hit, now):
        if not hit:
            if now - self.last_seen >= 1.25:
                self.armed = True
            return False
        self.last_seen = now
        if self.armed and now - self.last_count >= 2.8:
            self.last_count = now
            self.armed = False
            return True
        return False


class Detector:
    def __init__(self):
        self.ocr = RapidOCR(
            intra_op_num_threads=2,
            inter_op_num_threads=1,
            det_limit_side_len=640,
            det_limit_type="max",
            use_cls=False,
        )

    def read(self, image):
        h, w = image.shape[:2]
        x1, y1, x2, y2 = CONFIG.get("crop", [0.48, 0.20, 0.82, 0.64])
        crop = image[int(h * y1) : int(h * y2), int(w * x1) : int(w * x2)]
        if crop.size == 0:
            return False, []
        result, _ = self.ocr(crop)
        rows = [(a[0], normal(a[1]), float(a[2])) for a in result or [] if float(a[2]) >= 0.65]
        chests = [r for r in rows if re.search(r"\b(?:COFRE|CHEST)\b", r[1])]
        xp = [r for r in rows if re.search(r"\+?\s*\d+\s*(?:PE|XP)\b", r[1])]
        # Both the chest label and XP must appear next to each other, not elsewhere in the HUD.
        for box, text, score in chests:
            center = np.array(box).mean(axis=0)
            for xp_box, _, _ in xp:
                other = np.array(xp_box).mean(axis=0)
                if abs(center[0] - other[0]) < w * 0.12 and abs(center[1] - other[1]) < h * 0.09:
                    return True, [r[1] for r in rows]
        return False, [r[1] for r in rows]


def websocket_config():
    configured = os.environ.get("COFRES_OBS_CONFIG") or CONFIG.get("obs_config_path")
    path = (
        Path(configured)
        if configured
        else Path(os.environ.get("APPDATA", ""))
        / "obs-studio/plugin_config/obs-websocket/config.json"
    )
    cfg = load_json(path, {})
    password = os.environ.get("COFRES_OBS_PASSWORD")
    if password is None:
        password = cfg.get("server_password", "") if cfg.get("auth_required", True) else ""
    return {
        "host": "127.0.0.1",
        "port": int(os.environ.get("COFRES_OBS_PORT", cfg.get("server_port", 4455))),
        "password": password,
        "timeout": 4,
    }


def req(client, name, data=None):
    return client.send(name, data or {}, raw=True) or {}


def scene_sources(client, scene, seen=None):
    seen = seen or set()
    if scene in seen:
        return []
    seen.add(scene)
    out = []
    data = req(client, "GetSceneItemList", {"sceneName": scene})
    for item in data.get("sceneItems", []):
        if not item.get("sceneItemEnabled", True):
            continue
        if item.get("inputKind") in ("game_capture", "monitor_capture", "window_capture"):
            out.append((item["sourceName"], item["inputKind"]))
        elif item.get("isGroup"):
            group = req(client, "GetGroupSceneItemList", {"sceneName": item["sourceName"]})
            for g in group.get("sceneItems", []):
                if g.get("sceneItemEnabled") and g.get("inputKind") in (
                    "game_capture",
                    "monitor_capture",
                    "window_capture",
                ):
                    out.append((g["sourceName"], g["inputKind"]))
        elif item.get("sourceType") == "OBS_SOURCE_TYPE_SCENE":
            out.extend(scene_sources(client, item["sourceName"], seen))
    return list(dict.fromkeys(out))


def write_native_count():
    # OBS reads a plain-text total; the last value stays visible if the server closes.
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        temp = DATA_DIR / "total-nativo.tmp"
        temp.write_text(str(STATE["count"]), encoding="utf-8")
        temp.replace(DATA_DIR / "total-nativo.txt")
    except OSError:
        LOG.warning("No se pudo actualizar el archivo de texto de OBS")


def install_overlay(client, scene_name):
    scenes = req(client, "GetSceneList").get("scenes", [])
    names = [s["sceneName"] for s in scenes]
    if scene_name not in names:
        raise RuntimeError("No existe la escena " + scene_name)
    if NATIVE_SCENE not in names:
        req(client, "CreateScene", {"sceneName": NATIVE_SCENE})
    kinds = req(client, "GetInputKindList").get("inputKinds", [])
    text_kind = next(
        k for k in ("text_gdiplus_v3", "text_gdiplus_v2", "text_gdiplus") if k in kinds
    )
    inputs = {i["inputName"]: i for i in req(client, "GetInputList").get("inputs", [])}

    def ensure_input(name, kind, settings, transform):
        if name not in inputs:
            item = req(
                client,
                "CreateInput",
                {
                    "sceneName": NATIVE_SCENE,
                    "inputName": name,
                    "inputKind": kind,
                    "inputSettings": settings,
                    "sceneItemEnabled": True,
                },
            )["sceneItemId"]
        else:
            req(
                client,
                "SetInputSettings",
                {"inputName": name, "inputSettings": settings, "overlay": True},
            )
            found = next(
                (
                    i
                    for i in req(client, "GetSceneItemList", {"sceneName": NATIVE_SCENE})[
                        "sceneItems"
                    ]
                    if i["sourceName"] == name
                ),
                None,
            )
            item = (
                found["sceneItemId"]
                if found
                else req(
                    client,
                    "CreateSceneItem",
                    {"sceneName": NATIVE_SCENE, "sourceName": name, "sceneItemEnabled": True},
                )["sceneItemId"]
            )
        req(
            client,
            "SetSceneItemTransform",
            {
                "sceneName": NATIVE_SCENE,
                "sceneItemId": item,
                "sceneItemTransform": dict(alignment=5, **transform),
            },
        )
        req(
            client,
            "SetSceneItemEnabled",
            {"sceneName": NATIVE_SCENE, "sceneItemId": item, "sceneItemEnabled": True},
        )

    write_native_count()
    video = req(client, "GetVideoSettings")
    layout_width = min(1000, video["baseWidth"])
    layout_height = round(layout_width * 334 / 1000)
    layout_scale = layout_width / 1000
    ensure_input(
        "Contador de cofres - Marco",
        "image_source",
        {"file": str(ASSET_DIR / "marco.png"), "unload": False},
        {
            "positionX": 0.0,
            "positionY": 0.0,
            "scaleX": layout_width / 2172,
            "scaleY": layout_width / 2172,
        },
    )
    common = {
        "color": 16777215,
        "opacity": 100,
        "bk_opacity": 0,
        "align": "center",
        "valign": "center",
        "extents": True,
        "extents_wrap": False,
        "extents_cx": 430,
        "outline": True,
        "outline_color": 0x009FFF,
        "outline_opacity": 100,
        "outline_size": 2,
        "antialiasing": True,
    }
    title = dict(
        common,
        text="COFRES ABIERTOS",
        read_from_file=False,
        font={"face": "Impact", "size": 32, "flags": 2, "style": "Italic"},
        extents_cy=44,
    )
    count = dict(
        common,
        read_from_file=True,
        file=str(DATA_DIR / "total-nativo.txt"),
        font={"face": "Impact", "size": 90, "flags": 2, "style": "Italic"},
        extents_cy=110,
    )
    ensure_input(
        "Contador de cofres - Titulo",
        text_kind,
        title,
        {
            "positionX": 330.0 * layout_scale,
            "positionY": 87.0 * layout_scale,
            "scaleX": layout_scale,
            "scaleY": layout_scale,
        },
    )
    ensure_input(
        NATIVE_COUNT,
        text_kind,
        count,
        {
            "positionX": 330.0 * layout_scale,
            "positionY": 127.0 * layout_scale,
            "scaleX": layout_scale,
            "scaleY": layout_scale,
        },
    )
    items = req(client, "GetSceneItemList", {"sceneName": scene_name})["sceneItems"]
    native = next((i for i in items if i["sourceName"] == NATIVE_SCENE), None)
    if native is None:
        item_id = req(
            client,
            "CreateSceneItem",
            {"sceneName": scene_name, "sourceName": NATIVE_SCENE, "sceneItemEnabled": True},
        )["sceneItemId"]
        previous = next(
            (
                i
                for i in items
                if i["sourceName"] == OVERLAY_NAME and i["sceneItemTransform"]["positionX"] > 0
            ),
            None,
        )
        video = req(client, "GetVideoSettings")
        width = video.get("baseWidth", 1920)
        visible_width = min(650, width * 0.38)
        old = previous["sceneItemTransform"] if previous else {}
        transform = {
            "positionX": old.get("positionX", (width - visible_width) / 2),
            "positionY": old.get("positionY", 24.0),
            "scaleX": old.get("scaleX", visible_width / layout_width),
            "scaleY": old.get("scaleY", visible_width / layout_width),
            "alignment": 5,
            "cropRight": int(video["baseWidth"] - layout_width),
            "cropBottom": max(0, int(video["baseHeight"] - layout_height)),
        }
        req(
            client,
            "SetSceneItemTransform",
            {"sceneName": scene_name, "sceneItemId": item_id, "sceneItemTransform": transform},
        )
        req(
            client,
            "SetSceneItemLocked",
            {"sceneName": scene_name, "sceneItemId": item_id, "sceneItemLocked": True},
        )
    else:
        item_id = native["sceneItemId"]
    req(
        client,
        "SetSceneItemEnabled",
        {"sceneName": scene_name, "sceneItemId": item_id, "sceneItemEnabled": True},
    )
    for item in items:
        if item["sourceName"] == OVERLAY_NAME:
            req(
                client,
                "SetSceneItemEnabled",
                {
                    "sceneName": scene_name,
                    "sceneItemId": item["sceneItemId"],
                    "sceneItemEnabled": False,
                },
            )
    with LOCK:
        STATE["installed"] = True


def worker():
    detector = Detector()
    gate = Gate()
    client = None
    refresh_at = 0
    installed_scene = ""
    last_source = ""
    while not STOP.is_set():
        start = time.monotonic()
        try:
            if client is None:
                with LOCK:
                    STATE.update(status="Conectando con OBS...", connected=False)
                client = obs.ReqClient(**websocket_config())
                installed_scene = ""
                refresh_at = 0
            with LOCK:
                selected_scene = STATE["scene"]
            if time.monotonic() >= refresh_at:
                scene_names = [
                    s["sceneName"]
                    for s in req(client, "GetSceneList").get("scenes", [])
                    if s["sceneName"] != NATIVE_SCENE
                ]
                with LOCK:
                    STATE.update(scenes=scene_names, connected=True)
                refresh_at = time.monotonic() + 5
            with LOCK:
                scene_names = STATE["scenes"]
            if not selected_scene or selected_scene not in scene_names:
                with LOCK:
                    STATE.update(
                        status="Selecciona tu escena de OBS en el panel",
                        installed=False,
                        sources=[],
                    )
                STOP.wait(0.4)
                continue
            if installed_scene != selected_scene:
                install_overlay(client, selected_scene)
                installed_scene = selected_scene
                gate = Gate()
                last_source = ""
            candidates = scene_sources(client, selected_scene)
            with LOCK:
                STATE["sources"] = [{"name": n, "kind": k} for n, k in candidates]
                if STATE["source"] not in [n for n, _ in candidates]:
                    STATE["source"] = next(
                        (n for n, k in candidates if k == "game_capture"),
                        candidates[0][0] if candidates else "",
                    )
                source = STATE["source"]
                paused = STATE["paused"]
            if source != last_source:
                gate = Gate()
                last_source = source
            if not source:
                with LOCK:
                    STATE["status"] = "Activa una captura de juego visible en la escena elegida"
                STOP.wait(0.8)
                continue
            if paused:
                with LOCK:
                    STATE["status"] = "Deteccion pausada"
                STOP.wait(0.4)
                continue
            data = req(
                client,
                "GetSourceScreenshot",
                {
                    "sourceName": source,
                    "imageFormat": "jpg",
                    "imageWidth": 1920,
                    "imageCompressionQuality": 85,
                },
            )
            image = cv2.imdecode(
                np.frombuffer(base64.b64decode(data["imageData"].split(",", 1)[1]), np.uint8),
                cv2.IMREAD_COLOR,
            )
            if image is None:
                raise RuntimeError("Imagen vacia")
            if np.std(image) < 2:
                with LOCK:
                    STATE["status"] = "Captura negra: abre Fortnite y revisa la fuente de juego"
                STOP.wait(0.8)
                continue
            hit, texts = detector.read(image)
            if gate.feed(hit, time.monotonic()):
                change_count(delta=1, automatic=True)
            elapsed = time.monotonic() - start
            with LOCK:
                STATE.update(
                    status="Detectando aperturas",
                    last_text=" | ".join(texts),
                    fps=round(1 / max(elapsed, CONFIG["interval"]), 1),
                    last_frame=datetime.now().strftime("%H:%M:%S"),
                )
        except Exception as error:
            with LOCK:
                STATE.update(
                    connected=False,
                    installed=False,
                    status="Revisa WebSocket, la contrasena y la escena de OBS",
                )
            LOG.warning("Conexion o captura: %s", type(error).__name__)
            if client:
                try:
                    client.disconnect()
                except Exception:
                    pass
            client = None
            STOP.wait(3)
        STOP.wait(max(0.01, CONFIG["interval"] - (time.monotonic() - start)))
    if client:
        try:
            client.disconnect()
        except Exception:
            pass


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send_data(self, data, kind="application/json", code=200):
        if not isinstance(data, bytes):
            data = data.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def local_request(self):
        host = self.headers.get("Host", "")
        origin = self.headers.get("Origin")
        return host in (f"127.0.0.1:{PORT}", f"localhost:{PORT}") and (
            not origin or origin in (f"http://127.0.0.1:{PORT}", f"http://localhost:{PORT}")
        )

    def do_GET(self):
        if not self.local_request():
            return self.send_data("Forbidden", "text/plain", 403)
        path = urlsplit(self.path).path
        if path == "/api/state":
            with LOCK:
                data = json.dumps(STATE, ensure_ascii=False)
            return self.send_data(data)
        if path == "/api/frame":
            if STATE["demo"]:
                return self.send_data(
                    "Modo de demostracion: no captura el juego", "text/plain", 404
                )
            with LOCK:
                source = STATE["source"]
            if not source:
                return self.send_data("Sin fuente", "text/plain", 503)
            try:
                with obs.ReqClient(**websocket_config()) as client:
                    d = req(
                        client,
                        "GetSourceScreenshot",
                        {
                            "sourceName": source,
                            "imageFormat": "jpg",
                            "imageWidth": 960,
                            "imageCompressionQuality": 75,
                        },
                    )
                return self.send_data(
                    base64.b64decode(d["imageData"].split(",", 1)[1]), "image/jpeg"
                )
            except Exception:
                return self.send_data("Sin captura", "text/plain", 503)
        files = {
            "/": ("panel.html", "text/html; charset=utf-8"),
            "/overlay": ("overlay.html", "text/html; charset=utf-8"),
            "/marco.png": ("assets/marco.png", "image/png"),
        }
        if path not in files:
            return self.send_data("Not found", "text/plain", 404)
        name, kind = files[path]
        return self.send_data((ROOT / name).read_bytes(), kind)

    def do_POST(self):
        if not self.local_request():
            return self.send_data("Forbidden", "text/plain", 403)
        if urlsplit(self.path).path != "/api/control":
            return self.send_data("Not found", "text/plain", 404)
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 <= size <= 4096:
                raise ValueError()
            data = json.loads(self.rfile.read(size))
            action = data.get("action")
            if action == "add":
                change_count(delta=1)
            elif action == "undo":
                change_count(delta=-1)
            elif action == "reset":
                change_count(value=0)
            elif action == "set":
                change_count(value=max(0, min(9999999, int(data["value"]))))
            elif action == "pause":
                with LOCK:
                    STATE["paused"] = not STATE["paused"]
            elif action == "stop":
                self.send_data('{"ok":true}')
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return
            elif action == "scene":
                if STATE["demo"]:
                    return self.send_data('{"ok":false}', code=400)
                with LOCK:
                    if data.get("value") not in STATE["scenes"]:
                        raise ValueError()
                    STATE.update(scene=data["value"], source="", installed=False)
                    CONFIG.update(scene=data["value"], source="")
                    save_config()
            elif action == "source":
                with LOCK:
                    if data.get("value") not in [s["name"] for s in STATE["sources"]]:
                        raise ValueError()
                    STATE["source"] = data["value"]
                    CONFIG["source"] = data["value"]
                    save_config()
            else:
                raise ValueError()
            self.send_data('{"ok":true}')
        except (ValueError, KeyError, TypeError):
            self.send_data('{"ok":false}', code=400)


_MUTEX = None


def single_instance():
    global _MUTEX
    _MUTEX = ctypes.windll.kernel32.CreateMutexW(
        None, False, f"Local\\FortniteChestCounterOBS{PORT}"
    )
    return ctypes.windll.kernel32.GetLastError() != 183


def test_videos(paths):
    import av

    detector = Detector()
    report = []
    for filename in paths:
        c = av.open(filename)
        stream = c.streams.video[0]
        gate = Gate()
        events = []
        next_at = 0.0
        frames = 0
        for frame in c.decode(stream):
            t = float(frame.time or 0)
            if t < next_at:
                continue
            next_at = t + 0.4
            frames += 1
            hit, texts = detector.read(frame.to_ndarray(format="bgr24"))
            if gate.feed(hit, t):
                events.append(round(t, 2))
        c.close()
        row = {
            "file": Path(filename).name,
            "count": len(events),
            "events_seconds": events,
            "frames_analyzed": frames,
        }
        report.append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)
    (DATA_DIR / "validacion-clips.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return report


def main():
    global PORT, DATA_DIR, STATE_FILE, CONFIG_FILE
    parser = argparse.ArgumentParser(description="Contador visual de cofres para OBS (Windows)")
    parser.add_argument("--test-video", nargs="+", help="Analizar videos locales; requiere av")
    parser.add_argument(
        "--no-browser", action="store_true", help="No abrir el panel automaticamente"
    )
    parser.add_argument(
        "--demo", action="store_true", help="Demostracion sin conectarse a OBS ni leer el juego"
    )
    parser.add_argument(
        "--port", type=int, default=18746, help="Puerto local del panel (predeterminado: 18746)"
    )
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("El puerto debe estar entre 1024 y 65535")
    PORT = args.port
    if args.demo:
        if not os.environ.get("COFRES_DATA_DIR"):
            DATA_DIR = ROOT / ".demo-data"
        STATE_FILE = DATA_DIR / "cuenta.json"
        CONFIG_FILE = DATA_DIR / "config.local.json"
        STATE.update(
            count=42,
            status="Demostracion - no lee el juego",
            demo=True,
            scene="Gameplay",
            source="Fortnite",
            scenes=["Gameplay"],
            sources=[{"name": "Fortnite", "kind": "game_capture"}],
        )
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if args.test_video:
        test_videos(args.test_video)
        return
    if sys.platform != "win32":
        parser.error("La integracion nativa de OBS requiere Windows 10 u 11")
    if not single_instance():
        if not args.no_browser:
            webbrowser.open(f"http://127.0.0.1:{PORT}/")
        return
    logging.basicConfig(
        filename=DATA_DIR / "detector.log",
        level=logging.INFO,
        encoding="utf-8",
        format="%(asctime)s %(message)s",
    )
    logging.getLogger("obsws_python").setLevel(logging.CRITICAL)
    try:
        server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    except OSError:
        print(
            f"No se pudo abrir el puerto {PORT}. Puede haber otro contador activo. Usa --port para elegir otro.",
            file=sys.stderr,
        )
        return 1
    write_native_count()
    if not args.demo:
        threading.Thread(target=worker, daemon=True).start()
    if not args.no_browser:
        threading.Timer(1, lambda: webbrowser.open(f"http://127.0.0.1:{PORT}/")).start()
    print(f"Panel: http://127.0.0.1:{PORT}/", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        STOP.set()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
