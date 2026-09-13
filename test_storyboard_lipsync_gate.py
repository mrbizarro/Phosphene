"""A board shot with a spoken line is scored and retaken the way a One Shot
part is, and only then.

The Storyboard rendered a spoken shot once and kept whatever came out; the
owner's twelve-shot sitcom shipped shots at -0.06 next to shots at +0.33 on the
panel's own scorer (2026-09-10). `sb_lipsync_retake_plan` is the pure decision;
this pins what it retakes, what it leaves alone, and the seed it hands out.
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent


class RetakePlan(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tmp = tempfile.mkdtemp(prefix="phos_lipsync_")
        os.environ["LTX_STATE_DIR"] = tmp
        os.environ["LTX_OUTPUT_DIR"] = os.path.join(tmp, "out")
        os.environ["LTX_UPLOADS_DIR"] = os.path.join(tmp, "up")
        os.environ["PHOSPHENE_ANALYTICS_DISABLED"] = "1"
        os.environ["PHOSPHENE_DISABLE_VERSION_CHECK"] = "1"
        os.makedirs(os.environ["LTX_OUTPUT_DIR"], exist_ok=True)
        os.makedirs(os.environ["LTX_UPLOADS_DIR"], exist_ok=True)
        sys.path.insert(0, str(ROOT))
        import mlx_ltx_panel as p
        cls.p = p

    def _shot(self, n, prompt, seed=100):
        return {"n": n, "prompt": prompt, "seed": seed}

    def test_a_spoken_shot_under_the_floor_is_retaken_with_a_fresh_seed(self):
        p = self.p
        shots = [self._shot(4, "George says: <d>[English] I thought it was ashwagandha!</d>", seed=4204)]
        plan = p.sb_lipsync_retake_plan(shots, {4: -0.01}, {})
        self.assertEqual(plan, [(4, 4204 + 211)])

    def test_a_shot_that_syncs_is_left_alone(self):
        p = self.p
        shots = [self._shot(2, "George says: <d>[English] I saw everything.</d>")]
        self.assertEqual(p.sb_lipsync_retake_plan(shots, {2: 0.32}, {}), [])

    def test_a_shot_with_no_line_is_never_retaken(self):
        p = self.p
        shots = [self._shot(12, "The door bursts open and a tall man slides in, arms out.")]
        self.assertEqual(p.sb_lipsync_retake_plan(shots, {12: -0.3}, {}), [])

    def test_no_score_means_nothing_to_judge(self):
        p = self.p
        shots = [self._shot(6, "He says: <d>[English] Hi.</d>")]
        self.assertEqual(p.sb_lipsync_retake_plan(shots, {6: None}, {}), [])

    def test_retakes_stop_at_the_limit_and_seeds_keep_moving(self):
        p = self.p
        shots = [self._shot(9, "He whispers: <d>[English] This is not ashwagandha.</d>", seed=10)]
        self.assertEqual(p.sb_lipsync_retake_plan(shots, {9: 0.05}, {9: 1}), [(9, 10 + 422)])
        self.assertEqual(p.sb_lipsync_retake_plan(shots, {9: 0.05}, {9: p.TAKE_LIPSYNC_RETAKES}), [])

    def test_the_ltx_quote_form_counts_as_a_line_too(self):
        p = self.p
        shots = [self._shot(3, "She looks up and says 'It was a tea ceremony, George.'", seed=7)]
        self.assertEqual(p.sb_lipsync_retake_plan(shots, {3: 0.1}, {}), [(3, 218)])


if __name__ == "__main__":
    unittest.main()


class KeptTakeSurvivesReconcile(unittest.TestCase):
    """The gate's choice must be the board's choice. The shot's job id and seed
    follow the KEPT take, so the next reconcile (which repoints outputs at the
    job the shot names) agrees with the gate instead of undoing it."""

    def test_kept_take_job_and_seed_travel_with_it(self):
        p = RetakePlan.p if hasattr(RetakePlan, "p") else None
        if p is None:
            import mlx_ltx_panel as p
        import storyboard
        tmp = Path(tempfile.mkdtemp(prefix="phos_gate_"))
        files = {}
        for name in ("a", "b", "c"):
            f = tmp / f"take_{name}.mp4"; f.write_bytes(b"x"); files[name] = str(f)
        board = {"id": "sb_test_gate", "title": "t", "shots": [
            {"n": 9, "mode": "text", "engine": "h3", "duration_s": 5, "seed": 100, "status": "done",
             "prompt": "He whispers: <d>[English] This is not ashwagandha.</d>",
             "final_job_id": "j1", "final_output": files["a"]}]}
        storyboard.save_storyboard(p.STATE_DIR, board)
        scores = {files["a"]: -0.35, files["b"]: -0.09, files["c"]: -0.27}
        queued, jobs = [], {"j1": {"status": "done", "output_path": files["a"]}}
        def fake_enqueue(form):
            jid = f"j{len(queued) + 2}"; queued.append((jid, int(form["seed"])))
            jobs[jid] = {"status": "done", "output_path": files["b" if jid == "j2" else "c"]}
            return jid
        saved = {k: getattr(p, k) for k in ("take_lipsync_score", "_sb_enqueue", "_sb_job_index", "set_hidden", "push")}
        try:
            p.take_lipsync_score = lambda path: scores.get(str(path))
            p._sb_enqueue = fake_enqueue
            p._sb_job_index = lambda: dict(jobs)
            p.set_hidden = lambda path, hidden: None
            p.push = lambda line: None
            p._sb_lipsync_gate("sb_test_gate", board["shots"], "final_job_id", "final_output",
                               {"quality": "standard", "width": 1024, "height": 576}, False, False,
                               lambda ids: True)
            after = storyboard.load_storyboard(p.STATE_DIR, "sb_test_gate")
            shot = after["shots"][0]
            self.assertEqual(shot["final_output"], files["b"])
            self.assertEqual(shot["final_job_id"], "j2")
            self.assertEqual(shot["seed"], queued[0][1])
            self.assertEqual(shot["lipsync"]["kept"], files["b"])
            self.assertEqual(shot["lipsync"]["attempts"], 2)
            # and the reconcile that runs on every status poll leaves it alone
            p._sb_reconcile(after)
            self.assertEqual(after["shots"][0]["final_output"], files["b"])
        finally:
            for k, v in saved.items():
                setattr(p, k, v)
