import json
import math
import os
from datetime import datetime
from pathlib import Path
from statistics import median


LEVELS = (0.05, 0.25, 0.50, 0.75, 0.95)
NORMAL = (-1.645, -0.674, 0.0, 0.674, 1.645)
CAP = math.log(1_000_000_000)


def log_count(value):
    return math.log(max(value, 1))


def timestamp(value):
    return datetime.fromisoformat(value).timestamp()


def hourly_counts(rows, origin):
    return {
        int((timestamp(row['from']) - origin) // 3600): row['viewers']
        for row in rows if row.get('viewers') is not None
    }


def summarize(counts):
    opening = log_count(counts[0])
    lead_in = sorted(log_count(counts[h]) for h in range(-12, 0) if h in counts)
    crest = median(lead_in[-3:]) if lead_in else opening
    early = [log_count(counts[h]) for h in range(-4, 0) if h in counts]
    recent = median(early) if early else opening
    previous = log_count(counts.get(-1, counts[0]))
    return opening, (opening - crest, opening - recent, opening - previous, opening)


def compatibility(launch):
    platforms = launch.get('platforms', '').lower()
    unlock = launch.get('unlock', '').lower()
    if 'not a launch' in unlock or 'tier: target-history' in unlock:
        return 0.0
    if 'early-access-contaminated' in unlock or 'multiplayer-f2p confounds' in unlock:
        return 0.045
    if 'pc' not in platforms and 'stadia' not in platforms:
        return 1.0
    if 'simultaneous' in unlock or 'pc only' in unlock or 'free-to-play' in unlock:
        return 0.035
    return 0.28 if 'midnight local' in unlock or 'rolling' in unlock else 0.10


def launch_examples(comparables):
    for launch in comparables:
        prior = compatibility(launch)
        if not prior:
            continue
        counts = hourly_counts(launch['hourly'], timestamp(launch['day_start']))
        if not counts.get(0) or sum(h in counts for h in range(-12, 0)) < 5:
            continue
        if sum(h in counts for h in range(1, 24)) != 23:
            continue
        opening, features = summarize(counts)
        yield prior, features, tuple(log_count(counts[h]) - opening for h in range(1, 24))


def dot(left, right):
    return sum(a * b for a, b in zip(left, right))


def solve(matrix, vector):
    rows = [list(row) + [value] for row, value in zip(matrix, vector)]
    width = len(vector)
    for i in range(width):
        scale = rows[i][i]
        rows[i] = [value / scale for value in rows[i]]
        for j in range(width):
            if i != j:
                factor = rows[j][i]
                rows[j] = [value - factor * pivot for value, pivot in zip(rows[j], rows[i])]
    return [row[-1] for row in rows]


def weighted_quantile(values, level):
    ordered = sorted(values)
    target = level * sum(weight for _, weight in ordered)
    previous_value = ordered[0][0]
    previous_center = 0.0
    cumulative = 0.0
    for value, weight in ordered:
        center = cumulative + weight / 2
        if center >= target:
            return previous_value + (value - previous_value) * (target - previous_center) / (center - previous_center)
        cumulative += weight
        previous_center, previous_value = center, value
    return ordered[-1][0]


def model(examples, target_features):
    design = []
    for prior, features, curve in examples:
        differences = [a - b for a, b in zip(features, target_features)]
        size_gap = differences[3] / 1.55
        distance = sum((d / scale) ** 2 for d, scale in zip(differences[:3], (0.67, 0.52, 0.43)))
        # Don't drive every weight to zero for an unprecedented audience. The
        # size coefficient then extrapolates only over a bounded range.
        weight = prior / (1.0 + (distance + size_gap * size_gap) / 10.0) ** 2
        regressors = [1.0] + [max(-2.0, min(2.0, d)) for d in differences[:3]]
        regressors.append(max(-1.65, min(1.65, size_gap)))
        design.append((weight, regressors, curve))

    total = sum(weight for weight, _, _ in design)
    design = [(weight / total, row, curve) for weight, row, curve in design]
    penalty = (0.0, 0.28, 0.42, 0.48, 0.46)
    matrix = [[sum(w * row[i] * row[j] for w, row, _ in design) + (penalty[i] if i == j else 0.0)
               for j in range(5)] for i in range(5)]

    forecast = []
    centers = []
    for h in range(23):
        rhs = [sum(w * row[i] * curve[h] for w, row, curve in design) for i in range(5)]
        coefficients = solve(matrix, rhs)
        centers.append(coefficients[0])
        sigma = 0.075 + 0.035 * math.sqrt(h + 1)
        scenarios = []
        for weight, row, curve in design:
            effect = dot(row[1:], coefficients[1:])
            adjusted = curve[h] - 0.85 * effect
            # Smooth the discrete comparable distribution with a small normal
            # kernel. Long horizons retain the full between-launch dispersion.
            for z, probability in zip((-1.6, -0.75, 0, 0.75, 1.6), (0.10, 0.22, 0.36, 0.22, 0.10)):
                scenarios.append((adjusted + sigma * z, weight * probability))
        forecast.append([weighted_quantile(scenarios, q) for q in LEVELS])
    return forecast, centers


def final_hour_signal(case, opening):
    cutoff = timestamp(case['checkpoint'])
    points = []
    for row in reversed(case.get('history') or []):
        minutes = (cutoff - timestamp(row['at'])) / 60
        if minutes > 60:
            break
        if 0 < minutes <= 60:
            points.append((minutes, log_count(row['viewers'])))
    if len(points) < 30:
        return None
    late = [value for minutes, value in points if minutes <= 15]
    middle = [value for minutes, value in points if 15 < minutes <= 40]
    if len(late) < 7 or len(middle) < 10:
        return None
    latest = median(late)
    slope = max(-0.75, min(0.50, 3.0 * (latest - median(middle))))
    return latest - opening, slope


def audience_headroom(case, origin, launch_counts):
    # Estimate how much audience Twitch had available during the first wave.
    # Additional viewers can arrive for a major launch, so this is a soft bound.
    target = case.get('game', {}).get('name', '').casefold()
    market = []
    for snapshot in case.get('categories_hourly') or []:
        hour = (timestamp(snapshot['from']) - origin) / 3600
        categories = snapshot.get('top') or []
        if -12 <= hour <= 0 and len(categories) >= 12:
            market.append(sum(row['viewers'] for row in categories if row['name'].casefold() != target))
    if len(market) < 6:
        return None
    peak = max(count for hour, count in launch_counts.items() if -12 <= hour <= 0)
    available = 1.22 * (peak + median(market))
    return log_count(available)


def predict(case):
    origin = timestamp(case['day_start'])
    counts = hourly_counts(case.get('history_hourly') or [], origin)
    known = case['known']
    first_hour = known if isinstance(known, dict) else next(row for row in known if row['hour'] == 1)
    counts[0] = first_hour['viewers']
    opening, features = summarize(counts)
    examples = list(launch_examples(case.get('comparables') or []))
    if examples:
        values, centers = model(examples, features)
    else:
        centers = [0.0] * 23
        values = [[z * 0.15 * math.sqrt(h) for z in NORMAL] for h in range(1, 24)]

    signal = final_hour_signal(case, opening)
    if signal is not None:
        latest, slope = signal
        amplitude = max(-0.65, min(0.50, latest + 0.46 * slope - centers[0]))
        for i, row in enumerate(values):
            correction = 0.76 * amplitude * math.exp(-i / 2.1)
            values[i] = [value + correction for value in row]

    headroom = audience_headroom(case, origin, counts)
    rows = []
    for hour, quantiles in enumerate(values, 2):
        estimates = [opening + q for q in quantiles]
        if headroom is not None:
            strength = 0.65 * (1.0 - math.exp(-(hour - 1) / 5.0))
            estimates = [value - strength * max(0.0, value - headroom) for value in estimates]
        viewers = [round(min(1_000_000_000, math.exp(max(0.0, min(CAP, value)))), 4) for value in estimates]
        rows.append({'hour': hour, 'q': viewers})
    return {'hours': rows}


def main():
    source = Path(os.environ.get('MAKEFUN_INPUT_DIR', '/input'))
    destination = Path(os.environ.get('MAKEFUN_OUTPUT_DIR', '/output'))
    destination.mkdir(parents=True, exist_ok=True)
    for path in sorted(source.glob('*.in')):
        case = json.loads(path.read_bytes())
        result = json.dumps(predict(case), separators=(',', ':'), allow_nan=False)
        (destination / f'{path.stem}.out').write_text(result + '\n', encoding='utf-8', newline='\n')


if __name__ == '__main__':
    main()
