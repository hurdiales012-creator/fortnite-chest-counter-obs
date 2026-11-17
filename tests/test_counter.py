import copy
import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import numpy as np
import pytest

import contador as app


@pytest.fixture(autouse=True)
def isolated_data(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "DATA_DIR", tmp_path)
    monkeypatch.setattr(app, "STATE_FILE", tmp_path / "cuenta.json")
    monkeypatch.setattr(app, "CONFIG_FILE", tmp_path / "config.local.json")
    state = copy.deepcopy(app.STATE)
    state.update(count=0, scene="", source="", scenes=["Gameplay"], sources=[], demo=False)
    monkeypatch.setattr(app, "STATE", state)
    monkeypatch.setattr(app, "CONFIG", copy.deepcopy(app.DEFAULT_CONFIG))


def row(text, x=100, y=100, score=0.99):
    return [[[x, y], [x + 80, y], [x + 80, y + 20], [x, y + 20]], text, score]


@pytest.mark.parametrize(
    "rows,expected",
    [
        ([row("COFRE"), row("+600 PE", y=70)], True),
        ([row("COFRE RARO"), row("+600 PE", y=70)], True),
        ([row("+600 PE COFRE ELEMENTAL")], True),
        ([row("RARE CHEST"), row("+1000 XP", y=70)], True),
        ([row("COFRE")], False),
        ([row("+600 PE")], False),
        ([row("SAQUEACOFRES"), row("+600 PE", y=70)], False),
        ([row("COFRE"), row("+600 PE", y=400)], False),
        ([row("COFRE", score=0.3), row("+600 PE", y=70)], False),
    ],
)
def test_reward_requires_chest_and_nearby_xp(rows, expected):
    detector = app.Detector.__new__(app.Detector)
    detector.ocr = lambda image: (rows, None)
    hit, _ = detector.read(np.zeros((1080, 1920, 3), dtype=np.uint8))
    assert hit is expected


def test_gate_blocks_repeated_visible_reward_and_brief_dropout():
    gate = app.Gate()
    assert gate.feed(True, 0)
    assert not gate.feed(True, 0.5)
    assert not gate.feed(False, 1)
    assert not gate.feed(True, 1.5)
    assert not gate.feed(True, 3)


def test_gate_rearms_after_clear_absence():
    gate = app.Gate()
    assert gate.feed(True, 0)
    assert not gate.feed(False, 2)
    assert gate.feed(True, 3)


def test_count_and_obs_text_are_saved_together(tmp_path):
    app.change_count(value=42)
    assert json.loads((tmp_path / "cuenta.json").read_text()) == {"count": 42}
    assert (tmp_path / "total-nativo.txt").read_text() == "42"
    app.change_count(delta=-100)
    assert app.STATE["count"] == 0


def test_config_rejects_invalid_crop_and_clamps_interval():
    config = app.validated_config({"crop": [0.8, 0.2, 0.1, 0.4], "interval": -100})
    assert config["crop"] == app.DEFAULT_CONFIG["crop"]
    assert config["interval"] == 0.2


@pytest.fixture
def server(monkeypatch):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
    monkeypatch.setattr(app, "PORT", httpd.server_address[1])
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{app.PORT}"
    httpd.shutdown()
    httpd.server_close()
    thread.join(timeout=2)


def post(base, payload, headers=None):
    request = urllib.request.Request(
        base + "/api/control",
        json.dumps(payload).encode(),
        {"Content-Type": "application/json", **(headers or {})},
        method="POST",
    )
    return json.load(urllib.request.urlopen(request, timeout=3))


def test_http_controls_and_generic_scene_selection(server, tmp_path):
    assert post(server, {"action": "add"})["ok"]
    assert app.STATE["count"] == 1
    assert post(server, {"action": "undo"})["ok"]
    assert app.STATE["count"] == 0
    post(server, {"action": "scene", "value": "Gameplay"})
    saved = json.loads((tmp_path / "config.local.json").read_text())
    assert saved["scene"] == "Gameplay"
    assert saved["source"] == ""


def test_foreign_origin_and_host_are_rejected(server):
    for headers in [{"Origin": "https://example.invalid"}, {"Host": "example.invalid"}]:
        with pytest.raises(urllib.error.HTTPError) as error:
            post(server, {"action": "reset"}, headers)
        assert error.value.code == 403


def test_invalid_scene_cannot_write_config(server, tmp_path):
    with pytest.raises(urllib.error.HTTPError) as error:
        post(server, {"action": "scene", "value": "Not in OBS"})
    assert error.value.code == 400
    assert not (tmp_path / "config.local.json").exists()


class FakeOBS:
    def __init__(self, width=1920, height=1080):
        self.width, self.height = width, height
        self.scenes = {"Gameplay": []}
        self.inputs = {}
        self.serial = 0

    def add_item(self, scene, name):
        self.serial += 1
        item = {
            "sceneItemId": self.serial,
            "sourceName": name,
            "sceneItemEnabled": True,
            "sceneItemTransform": {"positionX": 0, "positionY": 0},
        }
        self.scenes[scene].append(item)
        return item

    def send(self, method, data, raw=True):
        if method == "GetSceneList":
            return {"scenes": [{"sceneName": name} for name in self.scenes]}
        if method == "CreateScene":
            self.scenes[data["sceneName"]] = []
            return {}
        if method == "GetInputKindList":
            return {"inputKinds": ["image_source", "text_gdiplus_v3"]}
        if method == "GetInputList":
            return {"inputs": [{"inputName": name} for name in self.inputs]}
        if method == "GetVideoSettings":
            return {"baseWidth": self.width, "baseHeight": self.height}
        if method == "GetSceneItemList":
            return {"sceneItems": self.scenes[data["sceneName"]]}
        if method == "CreateInput":
            assert data["inputName"] not in self.inputs
            self.inputs[data["inputName"]] = data["inputSettings"].copy()
            return {
                "sceneItemId": self.add_item(data["sceneName"], data["inputName"])["sceneItemId"]
            }
        if method == "SetInputSettings":
            self.inputs[data["inputName"]].update(data["inputSettings"])
            return {}
        if method == "CreateSceneItem":
            return {
                "sceneItemId": self.add_item(data["sceneName"], data["sourceName"])["sceneItemId"]
            }
        if method in ("SetSceneItemTransform", "SetSceneItemEnabled", "SetSceneItemLocked"):
            item = next(
                i for i in self.scenes[data["sceneName"]] if i["sceneItemId"] == data["sceneItemId"]
            )
            key = {
                "SetSceneItemTransform": "sceneItemTransform",
                "SetSceneItemEnabled": "sceneItemEnabled",
                "SetSceneItemLocked": "sceneItemLocked",
            }[method]
            item[key] = data[key]
            return {}
        raise AssertionError(method)


@pytest.mark.parametrize("size", [(1920, 1080), (1280, 720), (854, 480)])
def test_native_overlay_install_is_repeatable_and_fits_canvas(size, tmp_path):
    client = FakeOBS(*size)
    app.install_overlay(client, "Gameplay")
    app.install_overlay(client, "Gameplay")
    assert len(client.inputs) == 3
    assert len(client.scenes["Gameplay"]) == 1
    assert len(client.scenes[app.NATIVE_SCENE]) == 3
    assert client.inputs[app.NATIVE_COUNT]["file"] == str(tmp_path / "total-nativo.txt")
    assert client.inputs[app.NATIVE_COUNT]["read_from_file"] is True
    transform = client.scenes["Gameplay"][0]["sceneItemTransform"]
    assert transform["cropRight"] >= 0
    assert transform["cropBottom"] >= 0
    assert client.scenes["Gameplay"][0]["sceneItemEnabled"] is True
