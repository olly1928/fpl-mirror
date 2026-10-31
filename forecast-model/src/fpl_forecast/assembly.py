"""Vendored frozen inference arithmetic; no training/search/DB routines."""
import numpy as np

GOAL_WEIGHTS = {'GKP': 10.0, 'DEF': 6.0, 'MID': 5.0, 'FWD': 4.0}

def probabilities(prediction, n):
    p = np.asarray(prediction, float)
    if p.shape != (n, 3) or not np.isfinite(p).all() or (p < 0).any() or (p > 1).any() or (not np.allclose(p.sum(axis=1), 1, rtol=0, atol=1e-07)):
        raise ValueError('State probabilities must be ordered valid 0/1/2 distributions')
    return p

def reference_map(recipes, role):
    answer = {}
    for recipe in recipes:
        match = recipe.get('reference_role') == role or (role == 'rolling20' and recipe.get('family') == 'rolling20')
        if match:
            target = recipe['component']
            if target in answer:
                raise ValueError('Ambiguous protected reference for ' + target)
            answer[target] = recipe['configuration_id']
    return answer

def assemble_supported_score(components, positions, mode='subtotal'):
    """Sum expectations of observed scoring events; no independence/mean thresholds."""
    positions = np.asarray(positions)
    n = len(positions)
    if not set(positions) <= set(GOAL_WEIGHTS):
        raise ValueError('Unknown FPL position')

    def value(name):
        a = np.asarray(components[name], float).reshape(-1)
        if a.shape != (n,) or not np.isfinite(a).all():
            raise ValueError('Incomplete scoring component ' + name)
        return a
    if mode == 'direct':
        return value('supported_score')
    if mode == 'historical':
        return value('historical_points')
    bridge = 3 * value('assists') + value('bonus')
    if mode == 'subtotal':
        return value('known_subtotal') + bridge
    if mode != 'football':
        raise ValueError('Unknown assembly')
    state = probabilities(components['minute_state'], n)
    gkp = positions == 'GKP'
    defender = np.isin(positions, ['GKP', 'DEF'])
    for key in ['goals', 'clean_sheet_points', 'save_blocks', 'conceded_blocks', 'negative_cost', 'penalties_saved', 'assists', 'bonus']:
        if (value(key) < -1e-10).any():
            raise ValueError('Nonnegative scoring component violated ' + key)
    weights = np.array([GOAL_WEIGHTS[p] for p in positions])
    return state[:, 1] + 2 * state[:, 2] + weights * value('goals') + value('clean_sheet_points') + gkp * value('save_blocks') - defender * value('conceded_blocks') - value('negative_cost') + 5 * gkp * value('penalties_saved') + bridge
