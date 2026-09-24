#!/usr/bin/env python3
"""The storyboard render dispatcher: what it retries, what Stop stops, and what
a poll is allowed to change.

  * SB5-08  a shot whose job could not be queued (or failed) was picked again
            on the very next round, forever: a tight loop rewriting the board
            and spamming the log, or the same render re-spent indefinitely.
  * SB5-09  Stop Film collected draft/final job ids only, so anchor-still
            image jobs stayed queued (and a running one kept the GPU).
  * SB5-10  the reconciler turned a shot the user had CUT back into "done"
            because its old draft job was still in the queue history, which
            put it back into the delivery pass and the export.

Everything runs against a scratch STATE_DIR and an in-memory queue; nothing
is enqueued to a real panel and no engine runs.
"""
from __future__ import annotations

import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import mlx_ltx_panel as panel                                        # noqa: E402
import storyboard                                                    # noqa: E402
from test_storyboard_editor_api import FakeHandler                   # noqa: E402


def _shot(n, **kw):
    s = {"n": n, "mode": "text", "engine": "ltx", "prompt": f"shot {n} happens",
         "duration_s": 5.0, "seed": 100 + n, "refs": [], "status": "pending"}
    s.update(kw)
    return s


def _board(shots, bid="sb_20231115_d15bad", **kw):
    b = {"schema": 1, "id": bid, "title": "Dispatch", "created_at": 1_700_050_000,
         "policy": storyboard.default_policy(), "cast": [], "engine_mode": "ltx",
         "shots": shots}
    b.update(kw)
    return b


class Sandbox(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = Path(self.tmp.name) / "state"
        self.state.mkdir()
        self.queue_state = {"queue": [], "current": None, "history": []}
        for p in (mock.patch.object(panel, "STATE_DIR", self.state),
                  mock.patch.object(panel, "STATE", self.queue_state),
                  mock.patch.object(panel, "persist_queue", lambda: None),
                  mock.patch.object(panel, "push", lambda *a, **k: None)):
            p.start()
            self.addCleanup(p.stop)
        panel._SB_RENDERS.clear()
        self.addCleanup(panel._SB_RENDERS.clear)

    def save(self, board):
        storyboard.save_storyboard(self.state, board)
        return board

    def load(self, bid):
        return storyboard.load_storyboard(self.state, bid)


class FailedShotsAreNotRetriedForever(Sandbox):
    """SB5-08."""

    def _dispatch(self, board, *, enqueue, index):
        self.save(board)
        bid = board["id"]
        panel._SB_RENDERS[bid] = {"stop": False, "queued": 0}
        th = threading.Thread(target=panel._sb_render_thread,
                              args=(bid, "draft", None), daemon=True)
        with mock.patch.object(panel, "_sb_enqueue", side_effect=enqueue), \
             mock.patch.object(panel, "_sb_job_index", side_effect=index), \
             mock.patch.object(panel, "_sb_h3_available", return_value=False), \
             mock.patch.object(panel, "h3_supports_chain_prompts", return_value=False), \
             mock.patch.object(panel, "h3_supports_first_frame", return_value=False), \
             mock.patch.object(panel, "_sb_lipsync_gate", lambda *a, **k: None), \
             mock.patch.object(panel, "_sb_sweep_stage_a", lambda *a, **k: None), \
             mock.patch.object(storyboard, "shot_to_job",
                               lambda shot, *a, **k: {"mode": "t2v",
                                                      "prompt": shot["prompt"]}):
            th.start()
            th.join(timeout=10)
            finished = not th.is_alive()
            if not finished:                 # never leave a spinning thread behind
                panel._SB_RENDERS.setdefault(bid, {})["stop"] = True
                th.join(timeout=10)
        return finished

    def test_a_shot_that_cannot_be_queued_is_tried_once_per_render(self):
        calls = []

        def refuse(form):
            calls.append(form["prompt"])
            raise RuntimeError("the character sheet is missing")

        board = _board([_shot(1), _shot(2)])
        finished = self._dispatch(board, enqueue=refuse, index=lambda: {})
        self.assertTrue(finished, "the dispatcher kept retrying a refused shot")
        self.assertEqual(sorted(calls), ["shot 1 happens", "shot 2 happens"])
        after = self.load(board["id"])
        self.assertEqual({s["status"] for s in after["shots"]}, {"failed"})
        self.assertIn("character sheet", after["shots"][0]["error"])
        # ...and the film's render slot is released, so Render can be pressed
        # again once the cause is fixed.
        self.assertNotIn(board["id"], panel._SB_RENDERS)

    def test_a_shot_whose_job_fails_is_not_re_rendered_by_the_same_run(self):
        calls = []

        def ok(form):
            calls.append(form["prompt"])
            return f"job-{len(calls)}"

        def index():
            return {f"job-{i + 1}": {"status": "failed", "error": "engine died"}
                    for i in range(len(calls))}

        board = _board([_shot(1)])
        finished = self._dispatch(board, enqueue=ok, index=index)
        self.assertTrue(finished, "the dispatcher kept re-rendering a failing shot")
        self.assertEqual(calls, ["shot 1 happens"])
        self.assertEqual(self.load(board["id"])["shots"][0]["status"], "failed")


class StopFilmStopsItsStills(Sandbox):
    """SB5-09."""

    def test_stop_withdraws_queued_stills_and_cancels_the_running_one(self):
        board = self.save(_board([
            _shot(1, still_job_id="img-running", status="pending"),
            _shot(2, still_job_id="img-queued-1", status="pending"),
            _shot(3, still_job_id="img-queued-2", status="pending"),
            _shot(4, still_job_id="skipped", still_error="no sheet"),
        ]))
        self.queue_state["queue"] = [{"id": "img-queued-1"}, {"id": "other-job"},
                                     {"id": "img-queued-2"}]
        self.queue_state["current"] = {"id": "img-running"}
        panel._SB_RENDERS[board["id"]] = {"stop": False}
        with mock.patch.object(panel, "stop_current_job",
                               return_value=True) as stop:
            h = FakeHandler().post("stop", {"id": [board["id"]]})
        self.assertTrue(h.payload["ok"])
        self.assertEqual([j["id"] for j in self.queue_state["queue"]], ["other-job"])
        self.assertEqual(h.payload["removed"], 2)
        self.assertEqual(h.payload["cancelled"], "img-running")
        stop.assert_called_once()
        # A still that was withdrawn is owed again: the next Render makes it
        # instead of rendering the shot unanchored.
        after = {s["n"]: s for s in self.load(board["id"])["shots"]}
        for n in (1, 2, 3):
            self.assertFalse(after[n].get("still_job_id"), n)
        # A still that was never going to exist is left as it was said.
        self.assertEqual(after[4]["still_job_id"], "skipped")

    def test_an_unrelated_running_job_is_not_this_films_to_kill(self):
        board = self.save(_board([_shot(1, still_job_id="img-queued")]))
        self.queue_state["queue"] = [{"id": "img-queued"}]
        self.queue_state["current"] = {"id": "somebody-elses-render"}
        with mock.patch.object(panel, "stop_current_job") as stop:
            h = FakeHandler().post("stop", {"id": [board["id"]]})
        stop.assert_not_called()
        self.assertIsNone(h.payload["cancelled"])
        self.assertEqual(self.queue_state["queue"], [])

    def test_the_dispatcher_queues_no_still_after_stop(self):
        # Stop can land while the still loop is running: it must not go on
        # queueing the rest of the batch.
        board = self.save(_board([_shot(n, still_prompt="x") for n in (1, 2, 3)],
                                 anchor_stills=True))
        bid = board["id"]
        panel._SB_RENDERS[bid] = {"stop": False}
        queued = []

        def enqueue(form):
            queued.append(form)
            panel._SB_RENDERS[bid]["stop"] = True     # Stop pressed now
            return f"img-{len(queued)}"

        with mock.patch.object(panel, "_sb_enqueue", side_effect=enqueue), \
             mock.patch.object(panel, "_sb_still_job_form",
                               lambda shot, b, p: {"mode": "image"}), \
             mock.patch.object(storyboard, "shot_wants_still", return_value=True), \
             mock.patch.object(panel, "_sb_job_index", return_value={}), \
             mock.patch.object(panel, "_sb_h3_available", return_value=False), \
             mock.patch.object(panel, "_sb_sweep_stage_a", lambda *a, **k: None):
            panel._sb_render_thread(bid, "draft", None)
        self.assertEqual(len(queued), 1)


class ACutShotStaysCut(Sandbox):
    """SB5-10."""

    def _reconcile(self, board, index):
        with mock.patch.object(panel, "_sb_job_index", return_value=index):
            return panel._sb_reconcile(board)

    def test_a_poll_does_not_revive_a_cut_shot(self):
        board = _board([_shot(1, status="skipped", grade="cut",
                              draft_job_id="j1", draft_output="/o/s1.mp4"),
                        _shot(2, status="done", draft_job_id="j2",
                              draft_output="/o/s2.mp4")])
        index = {"j1": {"status": "done", "output_path": "/o/s1.mp4"},
                 "j2": {"status": "done", "output_path": "/o/s2.mp4"}}
        for _ in range(3):
            self._reconcile(board, index)
        self.assertEqual(board["shots"][0]["status"], "skipped")
        self.assertEqual([s["n"] for s in
                          storyboard.shots_pending(board["shots"], "final")], [2])

    def test_a_job_that_is_still_running_does_not_uncut_it_either(self):
        board = _board([_shot(1, status="skipped", grade="cut",
                              final_job_id="j9", draft_output="/o/s1.mp4")])
        self._reconcile(board, {"j9": {"status": "running"}})
        self.assertEqual(board["shots"][0]["status"], "skipped")

    def test_a_board_already_revived_by_the_old_poll_is_put_right(self):
        board = _board([_shot(1, status="done", grade="cut",
                              draft_job_id="j1", draft_output="/o/s1.mp4")])
        self.assertTrue(self._reconcile(
            board, {"j1": {"status": "done", "output_path": "/o/s1.mp4"}}))
        self.assertEqual(board["shots"][0]["status"], "skipped")

    def test_uncut_is_still_one_click(self):
        board = self.save(_board([_shot(1, status="skipped", grade="cut",
                                        draft_output="/o/s1.mp4")]))
        with mock.patch.object(panel, "_sb_sweep_stage_a", lambda *a, **k: None):
            FakeHandler().post("grade", {"id": [board["id"]], "n": ["1"],
                                         "grade": [""]})
        after = self.load(board["id"])["shots"][0]
        self.assertEqual(after["status"], "done")
        with mock.patch.object(panel, "_sb_job_index", return_value={}):
            panel._sb_reconcile(after and self.load(board["id"]))
        self.assertEqual(self.load(board["id"])["shots"][0]["status"], "done")


if __name__ == "__main__":
    unittest.main()
