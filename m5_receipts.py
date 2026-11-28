"""Closed-vocabulary execution receipts; never accept request-provided labels."""
ITEMS = ("h3_m5_a1", "h3_m5_b1", "h3_m4_c1")
PREFIXES = {"PHOS_M5_ACCEL": "m5_accel", "PHOS_M5_ATTEMPT": "m5_accel_attempted",
            "PHOS_M5_FALLBACK": "m5_accel_fallback"}


def clean(value):
    return sorted({x for x in value if isinstance(x, str) and x in ITEMS}) if isinstance(value, (list, tuple)) else []


def consume(job, line):
    parts = line.strip().split()
    if len(parts) != 2 or parts[0] not in PREFIXES or parts[1] not in ITEMS:
        return False
    key = PREFIXES[parts[0]]
    job[key] = clean(clean(job.get(key)) + [parts[1]])
    return True


def properties(job):
    return {key: clean(job.get(key)) for key in PREFIXES.values()}


def timing_properties(job, chip, engine):
    """Five-second wall times for the H3 speed trial, including its off arm.

    M5/M6 (items a1/b1) and M4 Max (item c1). Other chips stay coarse."""
    import math
    import re
    if engine != "h3" or not re.fullmatch(r"M[56](?: (?:Pro|Max|Ultra))?|M4 Max", chip or ""):
        return {}
    result = {"m5_trial_schema": 1}
    value = job.get("elapsed_sec")
    if isinstance(value, bool):
        return result
    try:
        seconds = float(value)
    except (ValueError, TypeError):
        return result
    if math.isfinite(seconds) and 0 < seconds <= 86400:
        result["wall_sec_fine"] = max(5, int(math.floor(seconds / 5 + .5)) * 5)
    return result
