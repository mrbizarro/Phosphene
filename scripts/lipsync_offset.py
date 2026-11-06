"""Inspect FaceMesh mouth/envelope timing without modifying media.

Usage: python scripts/lipsync_offset.py measurement.npz [measurement.npz ...]
The NPZ contains mouth, band and fps from the FaceMesh diagnostic. Only the
CLI needs numpy. Correlation measures envelope timing, not phoneme accuracy.
Positive lag means the mouth is late; a negative lag suggests delaying picture.
"""
import argparse
import json
import math


def _correlation(a, b):
    ma, mb = sum(a) / len(a), sum(b) / len(b)
    aa, bb = [x - ma for x in a], [x - mb for x in b]
    denominator = math.sqrt(sum(x * x for x in aa) * sum(x * x for x in bb))
    return sum(x * y for x, y in zip(aa, bb)) / denominator if denominator else None


def measure(mouth, envelope, fps, max_lag=6, edge=6):
    """Use the v2 diagnostic's three-frame smoothing and fixed frame clock.

    Return full and half-clip lag curves so a single maximum cannot hide
    timing changes within the shot. No result here authorizes a global delay.
    """
    mouth, envelope = list(map(float, mouth)), list(map(float, envelope))
    n = len(mouth)
    if (len(envelope) != n or not math.isfinite(fps) or fps <= 0
            or not isinstance(max_lag, int) or max_lag < 0
            or not isinstance(edge, int) or edge < 0
            or n < 2 * (edge + max_lag + 3)
            or not all(math.isfinite(x) for x in mouth + envelope)):
        raise ValueError("Need equal, finite frame series, positive fps and enough frames")

    def smooth(values):
        padded = [0.0] + values + [0.0]
        return [sum(padded[i:i + 3]) / 3 for i in range(n)]

    m, e = smooth(mouth), smooth(envelope)
    flat = max(mouth) == min(mouth) or max(envelope) == min(envelope)

    def search(lo, hi):
        curve = []
        for lag in range(-max_lag, max_lag + 1):
            indices = range(max(lo, -lag), min(hi, n - lag))
            r = None if flat else _correlation([m[i + lag] for i in indices], [e[i] for i in indices])
            curve.append(dict(lag_frames=lag, lag_ms=1000 * lag / fps, r=r))
        valid = [p for p in curve if p['r'] is not None]
        return dict(zero=curve[max_lag], best=max(valid, key=lambda p: p['r']) if valid else None,
                    curve=curve)

    return dict(fps=fps, frames=n, sign="positive = mouth late", band=search(edge, n - edge),
                halves=[search(edge, n // 2), search(n // 2, n - edge)])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('measurements', nargs='+')
    args = parser.parse_args()
    import numpy as np
    for path in args.measurements:
        with np.load(path, allow_pickle=False) as data:
            result = measure(data['mouth'], data['band'], float(data['fps']))
        print(json.dumps(dict(source=path, **result), allow_nan=False))


if __name__ == '__main__':
    main()
