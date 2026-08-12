# NOTICE — Phosphene's LTX-2.5 weight packs

These files are a **Derivative of LTX-2.x** as defined in section 1.5 of the
**LTX-2.x Community License Agreement**, and they are distributed **exclusively
under that Agreement**. A complete copy of the Agreement — including Attachment
A, the acceptable-use policy — is published alongside these weights as
`LICENSE-LTX-2.x-Community-License.md` and is installed into the same directory
as the weights themselves. Read it before you use them.

## Attribution

| | |
|---|---|
| Original model | **LTX-2.5** |
| Licensor / copyright | **Lightricks Ltd.** |
| Upstream source | <https://huggingface.co/Lightricks/LTX-2.5> (bf16 release) |
| Upstream project | <https://github.com/Lightricks/LTX-2> |
| MLX inference port | `dgrauet/ltx-2-mlx` (MIT) — the runtime these packs are laid out for |
| This derivative | Phosphene — <https://github.com/mrbizarro/Phosphene> |

All copyright, patent, trademark and attribution notices carried by the upstream
release are retained. The safetensors metadata of every converted file preserves
the upstream `__metadata__` block verbatim.

## Statement of changes (License §3.3)

**Every weight file in these packs has been modified.** Nothing here is a
byte-for-byte copy of a Lightricks file. What was done, and nothing else:

1. **Layout conversion.** The official PyTorch-layout checkpoint was rewritten
   into the flat, single-file-per-component MLX layout the `ltx-2-mlx` runtime
   loads (`scripts/convert_ltx_mlx.py`). Tensor **names** change; tensor values
   do not.
2. **Quantisation.** The diffusion transformer's block linears were quantised
   from bf16 to **4-bit** (`ltx-2.5-mlx-q4`) or **8-bit** (`ltx-2.5-mlx-q8`),
   group size 64, by `scripts/quantize_ltx.py`. AdaLN modulation, patch
   embedding and output projections stay bf16 — the same recipe the community
   2.3 packs use. The text encoder pack (`gemma4-12b-ltx25-q4`) is the LTX-2.5
   Gemma 4 text tower quantised the same way.
3. **Provenance metadata added.** Each converted file carries an extra
   `phosphene_quant` key in its safetensors metadata recording the tool,
   version, recipe and bit width. No timestamps, so two builds are byte-identical.
4. **Sidecar config files generated.** `quantize_config.json`,
   `split_model.json`, `model.safetensors.index.json` and the per-upscaler
   configs are written by our tooling to describe the layout above.
5. **Sharding for transport only.** Files larger than 1.9 GB are published as
   ordered shards because a GitHub release asset is capped at 2 GiB. Shards are
   a pure byte split; `scripts/fetch_pack_release.py` reassembles them and
   verifies the sha256 of every shard **and** of every reassembled file against
   the published manifest. The reassembled bytes are exactly the bytes we built.

No training, fine-tuning, distillation or capability change was performed. No
safety, watermarking, provenance or use restriction implemented by the Licensor
has been removed, disabled or circumvented.

## Terms passed on to you (License §3.1, §3.2)

By downloading or using these weights you accept the LTX-2.x Community License
Agreement in full, **including the use restrictions in Section 4 and Attachment
A**, which apply to you exactly as they apply to us and which you must pass on
in turn to anyone you distribute these weights — or any derivative of them — to.
Any derivative you make of these weights must itself be distributed exclusively
under that Agreement, with a complete copy of it included.

## Commercial Entities (License §2.1, §3.5)

The Agreement defines a **Commercial Entity** as an Entity with annual revenues
of at least **US $10,000,000** (counted across subsidiaries, affiliates and
companies under common control).

**A Commercial Entity must obtain a paid licence from Lightricks Ltd. before any
use of LTX-2.x or any Derivative of it**, including these packs — except use
solely for a Non-Commercial Purpose as defined in Section 2.2 (personal
research, learning, hobby use; or testing/evaluation in a non-production
environment). Receiving these files from us grants no such licence and no right
beyond the Agreement. Contact <ltxv-licensing@lightricks.com>.

If you pass these weights on to anyone else, you must tell them the same two
things in writing: that their use is subject to this Agreement, and that a
Commercial Entity needs a separate paid licence from Lightricks.

## Phosphene's own contribution

The conversion and quantisation tooling, the sharding and reassembly format, the
manifest, and the panel that loads these packs are Phosphene's own work and are
covered by Phosphene's licence. That is an **additive** term under §3.6: it does
not modify, waive or conflict with any term of the LTX-2.x Community License
Agreement, which governs the weights and prevails in the event of any conflict.
