module.exports = {
  // On-demand Q8 download — the optional pack trained characters and voices
  // need. The panel auto-detects when it lands and unlocks the surfaces that
  // depend on it.
  //
  // v4.0: this fetched dgrauet/ltx-2.3-mlx-q8 and advertised "~37 GB". Both
  // were 2.3 facts, and 2.5 is the generation the panel serves now. The body
  // moved to scripts/pinokio/q8_weights.sh (see its header for why the LANE had
  // to move with the copy, and why the 2.5 packs come from a GitHub release
  // rather than `hf download`).
  //
  // The size claim is the measured one: required_files.json → q8_25 is
  // 30.02 GB. The High add-on is a SEPARATE 29.50 GB download and is not
  // fetched here — it is one more click in Settings → Models, and folding it in
  // would hold a complete pack hostage to twice the wait.
  run: [
    {
      method: "notify",
      params: {
        html: "<b>Downloading the LTX-2.5 Q8 weights (~30 GB)…</b><br>8-bit weights instead of 4-bit. This is what trained characters and voices need — on the base pack most of a trained face is lost before the first frame. Resumable if interrupted."
      }
    },
    {
      method: "shell.run",
      params: {
        message: "bash scripts/pinokio/q8_weights.sh"
      }
    },
    {
      method: "notify",
      params: {
        html: "<b>Q8 ready.</b><br>Characters and voices now render at full strength. The High tier needs one more download — the High add-on (~29.5 GB) — in Settings → Models."
      }
    }
  ]
}
