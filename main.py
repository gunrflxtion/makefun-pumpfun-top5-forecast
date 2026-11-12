#!/usr/bin/env python3
"""Forecast the top 5 new pump.fun coins by market cap at the answer reads.

Reads the house 10-minute snapshots at $MAKEFUN_INPUT_DIR (default /input):
  task.json + history.ndjson
Writes one file $MAKEFUN_OUTPUT_DIR/forecast.json (default /output/forecast.json).

Method (stdlib only, deterministic: fixed seed, no clock, no network):
  1. Build every mint's log10 market-cap series over the marks.
  2. Estimate the coins' lifecycle from the history itself:
     - peak age (when a coin's cap typically stops rising) and the post-peak
       decay rate in log10 per hour;
     - the completion level (the cap at which a bonding-curve coin
       graduates to its pool) and the completion rate;
     - the dispersion of log-cap changes at each lag (how unpredictable a
       coin's cap is that far out);
     - the cap distribution of young top coins: the newcomers that are
       launched after the cut and take places at the reads.
  3. Project every candidate coin (top caps + top recent growers) forward to
     each answer read with a Monte Carlo over its lifecycle (rise, then
     decay; curve coins may graduate and jump to the completion level).
     Each sample also draws the top newcomer caps at that read from the
     young-top distribution, so the candidates compete against the coins
     born after the cut, and a place's named share is exactly the chance a
     coin from the history holds it; the rest is left unnamed, which is
     what scores a newcomer that takes the place.
  4. Emit exactly {probabilities, top5, sizes}.
"""

import bisect
import json
import math
import os
import random
import sys
from datetime import datetime, timezone

SEED = 20261112
N_SAMPLES = 3000
MAX_CANDIDATES = 120
MAX_ROWS_PER_PLACE = 100
MIN_ROW_P = 0.0002
SIZE_TARGET_SUM = 0.93
P_FLOOR = 1e-4


def parse_iso(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def clamp(x, lo, hi):
    return lo if x < lo else hi if x > hi else x


def median(vals):
    if not vals:
        return None
    v = sorted(vals)
    n = len(v)
    mid = n // 2
    if n % 2:
        return v[mid]
    return 0.5 * (v[mid - 1] + v[mid])


def robust_sd(vals):
    """Robust sd from the 16th/84th percentiles (falls back to stdev)."""
    if len(vals) < 5:
        return None
    v = sorted(vals)
    n = len(v)

    def pct(p):
        i = p * (n - 1)
        lo = int(i)
        frac = i - lo
        return v[lo] * (1 - frac) + v[min(lo + 1, n - 1)] * frac

    sd = 0.5 * (pct(0.84) - pct(0.16))
    return sd if sd > 0 else None


def bin_of(value, edges):
    """Bin of a log10 cap under strictly ascending edges (bin 0 .. len(edges))."""
    return bisect.bisect_right(edges, value)


def main():
    in_dir = os.environ.get("MAKEFUN_INPUT_DIR", "/input")
    out_dir = os.environ.get("MAKEFUN_OUTPUT_DIR", "/output")

    with open(os.path.join(in_dir, "task.json")) as f:
        task = json.load(f)
    places = int(task.get("places", 5))
    edges = [float(e) for e in task["size"]["edges"]]
    n_bins = len(edges) + 1
    reads = [parse_iso(r) for r in task["reads"]]
    cut = parse_iso(task["history_until"])
    t0 = parse_iso(task["t0"])

    marks = []
    with open(os.path.join(in_dir, "history.ndjson")) as f:
        for line in f:
            line = line.strip()
            if line:
                marks.append(json.loads(line))
    marks.sort(key=lambda m: parse_iso(m["at"]))

    # ---- per-mint series: mint -> list of (time, log10 cap in SOL, venue, complete)
    series = {}
    created = {}
    last_mark_time = None
    last_rows = []
    for m in marks:
        t = parse_iso(m["at"])
        last_mark_time = t
        rows = m.get("rows", [])
        if rows:
            last_rows = rows
        for r in rows:
            mint = r["mint"]
            cap_sol = int(r["market_cap_lamports"]) / 1e9
            if cap_sol <= 0:
                continue
            series.setdefault(mint, []).append(
                (t, math.log10(cap_sol), r.get("venue", ""), bool(r.get("complete")))
            )
            if mint not in created:
                created[mint] = parse_iso(r["created_at"])
    if last_mark_time is None:
        raise SystemExit("no marks in history")
    cut = min(cut, last_mark_time)
    horizons = sorted(set(max(0.0, (r - cut) / 3600.0) for r in reads))
    if not horizons:
        horizons = [7.0]

    # ---- lifecycle estimates from the history itself ----
    # peak age: per coin, the age at its max log-cap point.
    peak_ages = []
    for mint, pts in series.items():
        if len(pts) < 4:
            continue
        peak_pt = max(pts, key=lambda p: p[1])
        age_peak = (peak_pt[0] - created.get(mint, peak_pt[0])) / 3600.0
        if 0.0 < age_peak < 12.0:
            peak_ages.append(age_peak)
    peak_age = median(peak_ages)
    peak_age = clamp(peak_age, 0.5, 3.0) if peak_age is not None else 1.0
    # Rise and decay slopes from each coin's RECENT window (its last three
    # points, at most 30 minutes): the birth phase makes a whole-life slope
    # meaningless for a young coin, so only the last few marks count.
    # A window is "rise" when the coin is younger than the peak age at its
    # start, "decay" when it is older. A weak measured decay is not trusted:
    # a coin past its peak always decays, so the floor is -0.10/hour.
    rise_slopes = []
    decay_slopes = []
    for mint, pts in series.items():
        if len(pts) < 3:
            continue
        win = pts[-3:]
        if win[-1][0] - win[0][0] < 0.15 * 3600.0:
            win = pts[-2:]
        dt = (win[-1][0] - win[0][0]) / 3600.0
        if dt < 0.15:
            continue
        slope = (win[-1][1] - win[0][1]) / dt
        age_start = (win[0][0] - created.get(mint, win[0][0])) / 3600.0
        if age_start < peak_age:
            rise_slopes.append(slope)
        else:
            decay_slopes.append(slope)
    rise = median(rise_slopes)
    rise = clamp(rise, 0.05, 0.60) if rise is not None else 0.35
    decay = median(decay_slopes)
    decay = clamp(decay, -0.60, -0.10) if decay is not None else -0.12

    # completion level: median log-cap at the first mark a coin is complete.
    comp_levels = []
    for mint, pts in series.items():
        for p in pts:
            if p[3]:
                comp_levels.append(p[1])
                break
    comp_level = median(comp_levels)
    comp_level = clamp(comp_level, 2.4, 3.4) if comp_level is not None else 2.92

    # completion rate: among curve coins near the completion level, the share
    # that are complete one hour later.
    comp_rate = None
    mark_times = [parse_iso(m["at"]) for m in marks]
    by_time = {}
    for mint, pts in series.items():
        for p in pts:
            by_time.setdefault(p[0], {})[mint] = p
    done = 0
    total = 0
    for i, t in enumerate(mark_times):
        j = None
        for k in range(i + 1, len(mark_times)):
            if mark_times[k] - t >= 3600.0 - 1.0:
                j = k
                break
        if j is None:
            continue
        later = by_time.get(mark_times[j], {})
        for mint, p in by_time.get(t, {}).items():
            if p[2] == "curve" and not p[3] and p[1] >= comp_level - 0.6:
                total += 1
                q = later.get(mint)
                if q is not None and q[3]:
                    done += 1
    if total >= 5:
        comp_rate = done / total
    lam_complete = clamp(-math.log(max(1e-6, 1.0 - (comp_rate or 0.15))), 0.02, 1.0)

    # dispersion of log-cap changes at each lag (robust sd).
    lag_hours = [1.0 / 6.0, 0.5, 1.0, 2.0, 3.0, 5.0, 7.0, 9.0]
    span_h = (mark_times[-1] - mark_times[0]) / 3600.0 if len(mark_times) > 1 else 0.0
    disp = {}          # lag -> robust sd of log-cap change for top-200 coins
    for lag in lag_hours:
        if lag > span_h + 1e-9:
            continue
        changes = []
        for i, t in enumerate(mark_times):
            target = t + lag * 3600.0
            j = min(range(len(mark_times)), key=lambda k: abs(mark_times[k] - target))
            if abs(mark_times[j] - target) > 301.0:
                continue
            m_now = marks[i].get("rows", [])
            m_later = marks[j].get("rows", [])
            if not m_now or not m_later:
                continue
            now_map = {r["mint"]: math.log10(int(r["market_cap_lamports"]) / 1e9)
                       for r in m_now}
            for r in m_later:
                mint = r["mint"]
                if mint in now_map:
                    changes.append(
                        math.log10(int(r["market_cap_lamports"]) / 1e9) - now_map[mint])
        if len(changes) >= 10:
            sd = robust_sd(changes)
            if sd is not None:
                disp[lag] = clamp(sd, 0.05, 1.2)

    def interp_lag(table, h, default):
        """Interpolate a lag-keyed table in log-lag; hold the ends flat."""
        if not table:
            return default
        ks = sorted(table)
        if h <= ks[0]:
            return table[ks[0]]
        if h >= ks[-1]:
            return table[ks[-1]]
        for a, b in zip(ks, ks[1:]):
            if a <= h <= b:
                w = (math.log(h) - math.log(a)) / (math.log(b) - math.log(a))
                return table[a] * (1 - w) + table[b] * w
        return table[ks[-1]]

    def sd_at(h):
        if not disp:
            return clamp(0.18 * math.sqrt(max(h, 0.05) / (1.0 / 6.0)) * 0.35, 0.15, 0.9)
        ks = sorted(disp)
        if h <= ks[-1]:
            return interp_lag(disp, h, 0.3)
        # past the measured span dispersion keeps growing like sqrt(h).
        return clamp(disp[ks[-1]] * math.sqrt(h / ks[-1]), 0.15, 1.0)

    # young top coins: the cap distribution of the newcomers that take places.
    young_caps = []
    for m in marks:
        t = parse_iso(m["at"])
        for r in m.get("rows", [])[:5]:
            if (t - parse_iso(r["created_at"])) < 3600.0:
                young_caps.append(math.log10(int(r["market_cap_lamports"]) / 1e9))
    young_mu = median(young_caps)
    young_mu = young_mu if young_mu is not None else 3.12
    young_sd = robust_sd(young_caps) if len(young_caps) >= 5 else None
    young_sd = clamp(young_sd, 0.25, 0.8) if young_sd is not None else 0.45

    # ---- candidates: top caps + top recent growers at the cut ----
    last = last_rows
    scored = []
    for idx, r in enumerate(last):
        mint = r["mint"]
        pts = series[mint]
        l0 = pts[-1][1]
        age0 = max(0.0, (cut - created.get(mint, pts[-1][0])) / 3600.0)
        # recent slope over the coin's last two observations (10-20 min),
        # shrunk toward the cohort rise rate; the birth phase is excluded.
        recent = 0.0
        if len(pts) >= 2:
            dt = (pts[-1][0] - pts[-2][0]) / 3600.0
            if dt >= 0.05:
                recent = clamp((pts[-1][1] - pts[-2][1]) / dt, -1.0, 1.0)
        g = 0.5 * recent + 0.5 * rise
        # per-coin volatility from its own 10-minute changes, shrunk to 0.12.
        ch = [pts[i][1] - pts[i - 1][1] for i in range(1, len(pts))]
        sig10 = 0.12
        if len(ch) >= 3:
            sd = robust_sd(ch)
            if sd is not None:
                sig10 = clamp(0.5 * sd + 0.5 * 0.12, 0.02, 0.30)
        scored.append({
            "mint": mint, "l0": l0, "age0": age0, "g": g, "sig10": sig10,
            "venue": pts[-1][2], "complete": pts[-1][3],
            "rank": idx + 1,
            "recent": recent,
        })
    by_cap = sorted(scored, key=lambda c: -c["l0"])[:100]
    growers = sorted(scored, key=lambda c: -c["recent"])[:25]
    cand_map = {c["mint"]: c for c in by_cap}
    for c in growers:
        cand_map.setdefault(c["mint"], c)
    cands = sorted(cand_map.values(), key=lambda c: -c["l0"])
    cands = cands[:MAX_CANDIDATES]

    def mean_at(c, h):
        """Expected log10 cap of candidate c at h hours after the cut."""
        if c["complete"]:
            if c["age0"] < peak_age:
                rise_t = min(h, peak_age - c["age0"])
                mu = c["l0"] + clamp(c["g"], -0.5, 1.0) * rise_t + decay * max(0.0, h - rise_t)
            else:
                mu = c["l0"] + decay * h
            # a coin's expected peak is bounded: about 10x the level at
            # which coins graduate to their pool (never below its own cap).
            return min(mu, max(c["l0"], comp_level + 1.0))
        # on the curve: capped by the completion level while it stays there.
        return min(c["l0"] + clamp(c["g"], -0.5, 1.0) * h, comp_level)

    def sd_of(c, h):
        base = sd_at(h)
        extra = c["sig10"] * math.sqrt(max(h, 0.05) / (1.0 / 6.0)) * 0.35
        return clamp(math.sqrt(base * base + extra * extra), 0.12, 1.0)

    # ---- Monte Carlo over the reads' horizons ----
    if os.environ.get("MAKEFUN_DEBUG"):
        print(f"DEBUG peak_age={peak_age:.2f} rise={rise:.3f} decay={decay:.3f} "
              f"comp_level={comp_level:.2f} lam_complete={lam_complete:.3f} "
              f"young_mu={young_mu:.2f} young_sd={young_sd:.2f} disp={disp} "
              f"horizons={['%.2f' % h for h in horizons]}",
              file=sys.stderr)
        for c in cands[:5]:
            print(f"DEBUG cand {c['mint'][:8]} l0={c['l0']:.2f} age0={c['age0']:.2f} "
                  f"g={c['g']:.2f} sig10={c['sig10']:.3f} complete={c['complete']} "
                  f"mu7={mean_at(c, 7.0):.2f} mu13={mean_at(c, 13.0):.2f} "
                  f"sd7={sd_of(c, 7.0):.2f} sd13={sd_of(c, 13.0):.2f}",
                  file=sys.stderr)
    # Each sample draws every candidate's cap at that horizon and the top
    # newcomer caps at that read (coins born after the cut), then ranks
    # them together: a newcomer that beats the candidates takes the place,
    # and that sample contributes nothing to any named row at that place.
    rng = random.Random(SEED)
    counts = {}   # horizon index -> {mint: [counts per place]}
    size_hist = {}  # horizon index -> place -> [bin counts]
    for hi, h in enumerate(horizons):
        counts[hi] = {}
        size_hist[hi] = {k: [0] * n_bins for k in range(1, places + 1)}
        sd_h = {c["mint"]: sd_of(c, h) for c in cands}
        mu_h = {c["mint"]: mean_at(c, h) for c in cands}
        p_comp = 1.0 - math.exp(-lam_complete * h)
        comp_mu = comp_level + 0.15 + 0.30 * min(h, 1.0) + decay * max(0.0, h - 1.0)
        for _ in range(N_SAMPLES):
            draws = []
            for c in cands:
                if not c["complete"] and c["venue"] == "curve":
                    if rng.random() < p_comp:
                        draws.append((comp_mu + rng.gauss(0.0, 0.45), c["mint"]))
                        continue
                draws.append((mu_h[c["mint"]] + rng.gauss(0.0, sd_h[c["mint"]]), c["mint"]))
            for _ in range(places):
                draws.append((rng.gauss(young_mu, young_sd), None))
            draws.sort(key=lambda d: (-d[0], d[1] or ""))
            for k in range(places):
                cap, mint = draws[k]
                if mint is not None:
                    counts[hi].setdefault(mint, [0] * places)[k] += 1
                size_hist[hi][k + 1][bin_of(cap, edges)] += 1

    # ---- combine horizons: the Monte Carlo frequencies already include
    # the newcomer competition, so a place's named sum is exactly the
    # chance a coin from the history holds it; the rest stays unnamed. ----
    n_h = len(horizons)
    prob = {}  # (mint, place) -> p
    for c in cands:
        mint = c["mint"]
        for k in range(1, places + 1):
            total = 0.0
            for hi in range(n_h):
                total += counts[hi].get(mint, [0] * places)[k - 1] / N_SAMPLES
            p = total / n_h
            if p >= MIN_ROW_P:
                prob[(mint, k)] = p

    # sizes: the Monte Carlo caps at each place, averaged over horizons.
    size_prob = {}  # (place, bin) -> p
    for k in range(1, places + 1):
        bins = [0.0] * n_bins
        for hi in range(n_h):
            hist = size_hist[hi][k]
            for b in range(n_bins):
                bins[b] += hist[b] / N_SAMPLES
        bins = [b / n_h for b in bins]
        # trim to the target sum, keeping the largest bins.
        order = sorted(range(n_bins), key=lambda b: -bins[b])
        keep = []
        acc = 0.0
        for b in order:
            if bins[b] < 0.0008:
                continue
            if acc + bins[b] > SIZE_TARGET_SUM:
                continue
            keep.append(b)
            acc += bins[b]
        if acc <= 0:
            continue
        for b in keep:
            p = bins[b]
            if p >= 0.0008:
                size_prob[(k, b)] = p

    # ---- emit, rounding to 6 decimals like the house starter ----
    def r6(x):
        r = math.floor(x * 1e6 + 0.5) / 1e6
        return 0.0 if r == 0 else r

    probs = []
    for k in range(1, places + 1):
        rows = sorted(
            ((mint, p) for (mint, kk), p in prob.items() if kk == k),
            key=lambda mp: (-mp[1], mp[0]),
        )[:MAX_ROWS_PER_PLACE]
        rows = [(mint, r6(p)) for mint, p in rows if r6(p) > 0]
        tot = sum(p for _, p in rows)
        if tot > 1.0:
            # trim the largest row by the excess so the place sums to <= 1.
            excess = tot - 1.0
            mint0, p0 = rows[0]
            rows[0] = (mint0, r6(p0 - excess))
            rows = [(m, p) for m, p in rows if p > 0]
        for mint, p in rows:
            probs.append({"mint": mint, "place": k, "p": p})

    sizes = []
    for k in range(1, places + 1):
        rows = sorted(
            ((b, p) for (kk, b), p in size_prob.items() if kk == k),
            key=lambda bp: (-bp[1], bp[0]),
        )
        rows = [(b, r6(p)) for b, p in rows if r6(p) > 0]
        tot = sum(p for _, p in rows)
        if tot > 1.0:
            excess = tot - 1.0
            b0, p0 = rows[0]
            rows[0] = (b0, r6(p0 - excess))
            rows = [(b, p) for b, p in rows if p > 0]
        for b, p in rows:
            sizes.append({"place": k, "bin": b, "p": p})

    score5 = {}
    for c in cands:
        s = 0.0
        for k in range(1, places + 1):
            s += (places + 1 - k) * prob.get((c["mint"], k), 0.0)
        score5[c["mint"]] = s
    top5 = [m for m, _ in sorted(score5.items(), key=lambda kv: (-kv[1], kv[0]))[:5]]
    if len(top5) < 5:  # last-resort fallback: the cut's largest caps.
        for r in last:
            if r["mint"] not in top5:
                top5.append(r["mint"])
            if len(top5) == 5:
                break

    doc = {"probabilities": probs, "top5": top5, "sizes": sizes}
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "forecast.json")
    blob = json.dumps(doc, separators=(",", ":"), sort_keys=True).encode("utf-8")
    with open(out_path, "wb") as f:
        f.write(blob)
    return 0


if __name__ == "__main__":
    sys.exit(main())
