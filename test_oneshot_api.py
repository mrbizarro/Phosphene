"""One Shot is its own door: the request document, the routes, the planner seam."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

os.environ.setdefault("LTX_STATE_DIR", str(Path(tempfile.mkdtemp(prefix="phos-oneshot-"))))
os.environ["PHOSPHENE_ANALYTICS_DISABLED"] = "1"
os.environ["PHOSPHENE_DISABLE_VERSION_CHECK"] = "1"

import mlx_ltx_panel as p  # noqa: E402


def _one(form: dict, k: str) -> str:
    v = form.get(k)
    return v[0] if isinstance(v, list) else v


def test_routes_are_registered_once():
    from panel import routes_oneshot  # noqa: F401
    from panel.routes import GET_ROUTES, POST_ROUTES
    for r in ("/oneshot/options", "/oneshot/estimate", "/oneshot/status"):
        assert r in GET_ROUTES, r
    for r in ("/oneshot", "/oneshot/plan"):
        assert r in POST_ROUTES, r


def test_a_prompt_alone_is_a_complete_request():
    form, err = p.oneshot_form({"prompt": "A tram rolls through a rainy night street."})
    assert err is None
    assert _one(form, "take_seconds") == "60" and _one(form, "engine") == "ltx" and _one(form, "mode") == "t2v"
    assert _one(form, "beats") == "" and _one(form, "take_handoff") == "last"
    assert _one(form, "quality") == p.LTX_QUALITY_DEFAULT
    j = p.make_job(form)["params"]
    assert j["take"]["seconds"] == 60 and len(j["take"]["parts"]) == 6
    assert all(b.startswith("A tram rolls") for b in j["take"]["beat_prompts"])   # the prompt carries every beat


def test_beats_list_camera_and_switches_map_onto_the_take():
    form, err = p.oneshot_form({"prompt": "x", "seconds": 30, "beats": ["a", "", "c"], "camera": "a slow push in",
                                "light_lock": "off", "retake": False, "handoff": "speech", "seed": 7, "label": "L"})
    assert err is None
    assert json.loads(_one(form, "beats")) == ["a", "", "c"]
    assert _one(form, "take_camera") == "a slow push in" and _one(form, "take_light_lock") == "off"
    assert _one(form, "take_retake") == "off" and _one(form, "take_handoff") == "speech"
    assert _one(form, "seed") == "7" and _one(form, "preset_label") == "L"
    j = p.make_job(form)["params"]
    assert j["take"]["handoff"] == "speech" and j["take"]["camera"] == "a slow push in"
    assert j["take"]["beat_prompts"][0].startswith("a") and j["take"]["beat_prompts"][1] == ""


def test_a_character_defaults_to_pro_and_a_speech_handoff():
    form, err = p.oneshot_form({"prompt": "x", "character_id": "someone"})
    assert err is None
    assert _one(form, "character_id") == "someone" and _one(form, "quality_choice") == "pro"
    assert _one(form, "take_handoff") == "speech" and _one(form, "upscale") == "fit_720p"
    assert _one(form, "upscale_method") == "pipersr"


def test_the_refusals_name_the_field():
    assert "prompt" in p.oneshot_form({})[1]
    assert "seconds" in p.oneshot_form({"prompt": "x", "seconds": 75})[1]
    assert "engine" in p.oneshot_form({"prompt": "x", "engine": "wan"})[1]
    assert "handoff" in p.oneshot_form({"prompt": "x", "handoff": "sideways"})[1]
    assert "start frame" in p.oneshot_form({"prompt": "x", "image": "/nowhere/at/all.png"})[1]


def test_h3_gets_its_own_axes(monkeypatch):
    monkeypatch.setattr(p, "h3_available", lambda: True)
    form, err = p.oneshot_form({"prompt": "x", "engine": "h3", "quality": "high", "turbo": "off"})
    assert err is None
    assert _one(form, "h3_quality") == "high" and _one(form, "h3_length") == p.TAKE_H3_PART_LENGTH
    assert _one(form, "h3_turbo") == "off" and _one(form, "h3_upscale") == "fit_720p"
    monkeypatch.setattr(p, "h3_available", lambda: False)
    assert "not installed" in p.oneshot_form({"prompt": "x", "engine": "h3"})[1]


def test_options_describe_this_mac():
    o = p.oneshot_options()
    assert o["ok"] and o["seconds"] == list(p.TAKE_SECONDS) and o["beat_seconds"] == 5
    assert o["engines"]["ltx"]["part_seconds"] == 10 and o["engines"]["h3"]["part_seconds"] == 15
    assert [q["key"] for q in o["engines"]["ltx"]["qualities"]][:2] == ["quick", "balanced"]
    assert isinstance(o["characters"], list) and o["handoff"] == ["last", "speech"]


def test_plan_beats_folds_the_planner_answer_into_beats(monkeypatch):
    def fake_plan(concept, n_shots, **kw):
        assert "ONE SHOT" in concept and n_shots == 6
        return {"shots": [{"n": i + 1, "prompt": f"beat {i + 1}", "mode": "text"} for i in range(6)]}
    monkeypatch.setattr(p.storyboard_planner, "plan_film", fake_plan)
    res = p.oneshot_plan_beats("a hen skates", 30, "ltx")
    assert res["ok"] and res["beats"] == [f"beat {i}" for i in range(1, 7)]
    monkeypatch.setattr(p.storyboard_planner, "plan_film", lambda *a, **k: {"error": {"kind": "timeout", "message": "took too long"}})
    res = p.oneshot_plan_beats("a hen skates", 30, "ltx")
    assert not res["ok"] and "took too long" in res["error"]
    assert not p.oneshot_plan_beats("", 30)["ok"] and not p.oneshot_plan_beats("x", 75)["ok"]


def test_status_reports_only_takes():
    s = p.oneshot_status()
    assert s["ok"] and "current" in s and isinstance(s["log"], list)
