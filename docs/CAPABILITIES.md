# Capabilities: what every model and variant can do (5.0)

`model_capabilities.py` is the one table of what each engine and each variant
(H3 Best, Fast, Fast HD, Flex, Flex Turbo; LTX-2.5 / 2.3; YuE2) can render.

| Who reads it | How |
|---|---|
| The page | `BOOT.capabilities` (first paint) and `GET /capabilities`. `webapp/js/capabilities.js` greys every mode chip, canvas/length chip and option the picked variant does not declare, with the row's own one-line reason as the tooltip; a click on a greyed control only shows the reason. |
| The server | `model_capabilities.check(variant_id, mode=, cell=, options=, inputs=, pro_active=)` returns each unsupported axis with its reason and the row's `fallback` (another variant, or `None` = refuse). |
| The build | `test_model_capabilities.py` binds every row to the constant or predicate the render path uses today (`H3_FLEX_MODES`, `h3_cell_takes_flex`, `h3_cell_takes_tristep`, the engine registry), so a row and its constant cannot drift apart. |
| A port | `~/.claude/skills/phosphene-model-port` stage 5 writes the row. |

## A row

```python
"h3.flex_turbo": {
    "engine": "h3", "label": "H3 Flex Turbo",
    "pick": {"speed": "flex_turbo"}, "rank": 2,   # how the form selects it
    "inherits": "h3.flex",                        # everything Flex declares...
    "status": "preview",
    "pro": True, "ledger": "h3-flex-turbo",       # ...plus Pro, tied to pro/ledger.json
    "steps": 4,
    "fallback": "h3.flex",                        # what renders instead when it cannot
    "reasons": {"modes": "H3 Flex Turbo makes videos from text or an image. Switch to H3 for this.", ...},
},
```

Axes: `modes`, `qualities`, `lengths` (None = every one the engine has),
`cells.exclude_flags` (`dense` = the single long pass, `fast_hd` = the Fast HD
recipe), `inputs`, `outputs`, `options`. Anything a row leaves out it inherits
from its engine row (or from `inherits`). Anything not declared is greyed.

`pick` + `rank`: the page works out the live variant from what the form has
picked (speed, quality, generation). The highest rank wins: Flex (2) over a Fast
HD cell (1) over Fast/Best (0).

## Adding a variant (a port)

1. Add the row. Declare only what was rendered and measured.
2. `python3 model_capabilities.py`: prints the table, exits 1 on a bad row
   (unknown axis value, a Pro row without its ledger id, a fallback to another
   engine, a reason over one line).
3. Bind it in `test_model_capabilities.py::TestBoundToThePanel` to the predicate
   its render path uses.
4. If the picker selects it through a new control, add the control to
   `CAP_OPTION_CONTROLS` / `capPickNow()` in `webapp/js/capabilities.js` (DOM
   facts only; never a mode list or a sentence).

## Status (31 Oct 2026)

The table, the route, BOOT, the generic greying and the binding tests are in.
The H3 Flex specific greying (`wt/flex-gray-50`, `fix/flex-gray-50`) lands
separately; once it does, its `h3FlexServesMode` / `h3CellTakesFlex` read these
rows and `H3_FLEX_MODES` / `h3_cell_takes_flex` derive from them, so the binding
test becomes an identity.
