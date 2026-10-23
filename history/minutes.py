"""Small, inspectable empirical model for disjoint FPL minute states.

The hierarchy learns transition frequencies from earlier seasons. It needs
minutes, not historical starting-lineup labels, and uses no post-match xP.
"""
import math


def state(minutes):
    return 0 if minutes <= 0 else 1 if minutes < 60 else 2


def prior_bucket(minutes):
    if minutes is None:
        return 'missing'
    for limit in (0, 450, 1350, 2250, 3000):
        if minutes <= limit:
            return str(limit)
    return '3000+'


def frequency_bucket(value):
    return min(4, int(value*4))


def features(pos, prior_minutes, lag, window):
    """lag is chronological and already bounded by the prediction cutoff."""
    recent = [float(r['minutes']) for r in lag[-window:]]
    states = [state(m) for m in recent]
    n = len(states)
    last = states[-1] if n else -1
    appear = frequency_bucket(sum(s > 0 for s in states)/n) if n else -1
    long = frequency_bucket(sum(s == 2 for s in states)/n) if n else -1
    zeros = 0
    for value in reversed(states):
        if value:
            break
        zeros += 1
    prior = prior_bucket(prior_minutes)
    return [str(pos), f'{pos}|{prior}', f'{pos}|{prior}|{last}',
            f'{pos}|{prior}|{last}|{appear}|{long}',
            f'{pos}|{prior}|{last}|{appear}|{long}|{min(3, zeros)}']


class Model:
    def __init__(self, window=6, strength=50, nodes=None):
        self.window = int(window); self.strength = float(strength)
        self.nodes = nodes if nodes is not None else {}

    def add(self, keys, minutes):
        s = state(minutes)
        for key in keys:
            row = self.nodes.setdefault(key, [0.0]*6)
            row[s] += 1; row[3+s] += minutes

    def predict(self, keys):
        probability = [.5, .1, .4]; means = [0.0, 20.0, 85.0]
        for key in keys:
            node = self.nodes.get(key)
            if not node:
                continue
            total = sum(node[:3]); k = self.strength
            probability = [(node[s]+k*probability[s])/(total+k) for s in range(3)]
            means = [(node[3+s]+k*means[s])/(node[s]+k) for s in range(3)]
        means[0] = 0.0; means[1] = max(1.0, min(59.0, means[1])); means[2] = max(60.0, min(90.0, means[2]))
        return probability, means

    def to_dict(self):
        return {'window': self.window, 'strength': self.strength, 'nodes': self.nodes}

    @classmethod
    def from_dict(cls, value):
        return cls(value['window'], value['strength'], value['nodes'])


def fit(rows, window, strength):
    model = Model(window, strength)
    for row in rows:
        model.add(row['keys'][window], row['minutes'])
    return model


def metrics(rows, predictions):
    if len(rows) != len(predictions):
        raise ValueError('Prediction count differs from target count')
    if not rows:
        return {'n': 0}
    logloss = appearance = p60 = mae = squared = 0.0
    for row, (probs, means) in zip(rows, predictions):
        target = state(row['minutes']); expected = sum(p*m for p,m in zip(probs, means))
        logloss -= math.log(max(1e-9, probs[target]))
        appearance += (1-probs[0]-(target > 0))**2
        p60 += (probs[2]-(target == 2))**2
        error = expected-row['minutes']; mae += abs(error); squared += error*error
    n = len(rows)
    return {'n': n, 'multiclass_logloss': logloss/n, 'appearance_brier': appearance/n,
            'p60_brier': p60/n, 'minutes_mae': mae/n, 'minutes_rmse': math.sqrt(squared/n)}
