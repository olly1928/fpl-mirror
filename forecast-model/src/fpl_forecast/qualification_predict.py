"""Inference-only application of frozen qualification procedures to supplied cases.

This module reads trusted hashed fitted artifacts and named feature matrices. It
never reads a training panel, constructs targets, fits an estimator, fetches data
or changes a selection. The caller owns pre-origin feature construction.
"""
from __future__ import annotations
import hashlib
from importlib.metadata import version
import json
from pathlib import Path
import sys
import numpy as np
import pandas as pd
from .assembly import assemble_supported_score, probabilities, reference_map
from .qualification_models import POPULATIONS, POSITIONS, predict_model
VERSION = 'qualification-predict-v1'
PRIMARY_SEED = 20261028
SUBTOTAL = ('known_subtotal', 'assists', 'bonus')
FOOTBALL = ('minute_state', 'goals', 'clean_sheet_points', 'save_blocks', 'conceded_blocks', 'negative_cost', 'penalties_saved', 'assists', 'bonus')
DEFAULT_ARMS = ('rolling20', 'portable', 'selected_subtotal', 'direct_supported', 'football_assembly', 'blend_subtotal_direct_025', 'blend_subtotal_direct_050', 'blend_subtotal_direct_075', 'blend_direct_football_025', 'blend_direct_football_050', 'blend_direct_football_075')
DEPENDENCIES = ('qualification_predict.py', 'qualification_models.py', 'qualification_evaluation.py', 'modern_components.py')

def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(8 * 1024 ** 2), b''):
            h.update(block)
    return h.hexdigest()

def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), default=str).encode()).hexdigest()

def _hash_string(value):
    return isinstance(value, str) and len(value) == 64 and all((c in '0123456789abcdef' for c in value))

def _reference(value):
    if not isinstance(value, dict):
        raise ValueError('Model reference must be a mapping')
    model = value.get('model', value)
    if not isinstance(model, dict) or not {'recipe', 'component', 'feature_count', 'feature_columns', 'seed'} <= set(model):
        raise ValueError('Expected reusable qualification fitted wrapper')
    record = {**{k: v for k, v in value.items() if k != 'model'}, **value.get('record', {})}
    for key in ['training_index_sha256', 'training_max_available_at']:
        if key not in record and key in model:
            record[key] = model[key]
    if not _hash_string(record.get('training_index_sha256')):
        raise ValueError('Fitted model lacks a training-index hash')
    if not record.get('training_max_available_at'):
        raise ValueError('Fitted model lacks training availability bound')
    bound = pd.Timestamp(record['training_max_available_at'])
    if bound.tzinfo is None or pd.isna(bound):
        raise ValueError('Training availability must have a timezone')
    columns = model['feature_columns']
    if not isinstance(columns, list) or len(columns) != model['feature_count'] or len(set(columns)) != len(columns):
        raise ValueError('Fitted model has missing or ambiguous feature columns')
    return {'model': model, 'record': record}

def load_models(records, artifact_root=None, expected_seed=PRIMARY_SEED):
    """Load trusted model records after hash checks; reject absent provenance.

    Records require configuration_id, component, model_file, model_sha256,
    training_index_sha256 and training_max_available_at. The latter two can also
    be stored inside an envelope alongside its `model` wrapper.
    """
    import joblib
    root = Path(artifact_root).resolve() if artifact_root else None
    result = {}
    for record in records:
        path = Path(record['model_file'])
        if root is not None:
            path = (root / path).resolve()
            if not path.is_relative_to(root):
                raise ValueError('Model path escapes supplied artifact root')
        else:
            path = path.resolve()
        if not _hash_string(record.get('model_sha256')) or sha(path) != record['model_sha256']:
            raise ValueError('Fitted model artifact hash differs')
        loaded = joblib.load(path)
        envelope = loaded if isinstance(loaded, dict) and 'model' in loaded else {'model': loaded}
        ref = _reference({**envelope, 'record': {**envelope.get('record', {}), **record}})
        model = ref['model']
        cid = record['configuration_id']
        if cid in result:
            raise ValueError('Duplicate model configuration identity')
        if model['recipe']['configuration_id'] != cid or model['component'] != record['component']:
            raise ValueError('Fitted model configuration/target does not match record')
        if int(model['seed']) != int(expected_seed) or int(record.get('seed', expected_seed)) != int(expected_seed):
            raise ValueError('Fitted model seed differs from fixed requested seed')
        result[cid] = ref
    return result

def required_configurations(selection, recipes, arms=None, selected_diagnostics=True):
    """Small explicit model population needed by these preselected procedures."""
    chosen = selection['components']
    rolling = reference_map(recipes, 'rolling20')
    portable = reference_map(recipes, 'portable')
    arms = tuple(DEFAULT_ARMS if arms is None else arms)
    needed = {}

    def add(group, mapping, targets):
        for target in targets:
            if target not in mapping:
                raise ValueError(f'Missing {group} recipe for {target}')
            needed[group, target] = mapping[target]
    add('rolling20', rolling, ['minute_state', 'minutes'])
    if selected_diagnostics:
        add('selected', chosen, ['minute_state', 'minutes'])
    for arm in arms:
        if arm not in DEFAULT_ARMS:
            raise ValueError('Unknown complete-procedure arm ' + arm)
        if arm == 'rolling20':
            add('rolling20', rolling, SUBTOTAL)
        elif arm == 'portable':
            add('portable', portable, SUBTOTAL)
        elif arm == 'selected_subtotal' or arm.startswith('blend_subtotal_direct_'):
            add('selected', chosen, SUBTOTAL)
        if arm == 'direct_supported' or arm.startswith(('blend_subtotal_direct_', 'blend_direct_football_')):
            add('selected', chosen, ['supported_score'])
        if arm == 'football_assembly' or arm.startswith('blend_direct_football_'):
            add('selected', chosen, FOOTBALL)
    return needed

def _features(value, cases):
    if isinstance(value, pd.DataFrame):
        if not value.index.equals(cases.index):
            raise ValueError('Feature index differs from case order')
        columns = list(value.columns)
        matrix = value.to_numpy()
    elif isinstance(value, dict) and {'values', 'columns'} <= set(value):
        matrix = np.asarray(value['values'])
        columns = list(value['columns'])
        if 'observation_id' in value and list(value['observation_id']) != cases.observation_id.tolist():
            raise ValueError('Feature observation identities differ from cases')
    else:
        raise ValueError('Feature matrix must supply explicit ordered columns')
    if matrix.ndim != 2 or matrix.shape != (len(cases), len(columns)) or len(set(columns)) != len(columns):
        raise ValueError('Invalid feature shape or duplicate columns')
    if not np.issubdtype(matrix.dtype, np.number) or not np.isfinite(matrix).all():
        raise ValueError('Nonfinite or nonnumeric inference features')
    return (matrix, columns)

def _array_digest(array):
    h = hashlib.sha256()
    h.update(str(array.dtype).encode())
    h.update(str(array.shape).encode())
    for start in range(0, len(array), 4096):
        h.update(np.ascontiguousarray(array[start:start + 4096]).tobytes())
    return h.hexdigest()

class ProcedurePredictor:
    """Shared component cache, bound to full case/feature content, not object IDs."""

    def __init__(self, models, selection, recipes, arms=None, selected_diagnostics=True):
        self.arms = tuple(DEFAULT_ARMS if arms is None else arms)
        if not self.arms or len(set(self.arms)) != len(self.arms):
            raise ValueError('Expected unique procedure arms')
        self.selection = json.loads(json.dumps(selection))
        self.recipes = {r['configuration_id']: dict(r) for r in recipes}
        self.needed = required_configurations(selection, recipes, self.arms, selected_diagnostics)
        self.models = {}
        self.selected_diagnostics = selected_diagnostics
        self._cache_key = None
        self._cache = None
        seed = int(selection.get('seed', PRIMARY_SEED))
        for (group, target), cid in self.needed.items():
            if cid not in models or cid not in self.recipes:
                raise ValueError('Missing frozen fitted recipe ' + cid)
            ref = _reference(models[cid])
            model = ref['model']
            if model['component'] != target or model['recipe']['configuration_id'] != cid or int(model['seed']) != seed:
                raise ValueError('Selection and fitted model identity/seed differ')
            for key in ['family', 'feature_set', 'parameters', 'history_start_mode', 'recency_half_life_days']:
                if model['recipe'].get(key) != self.recipes[cid].get(key):
                    raise ValueError('Fitted recipe differs from frozen catalogue: ' + key)
            self.models[cid] = ref

    def _predict(self, cases, feature_sets):
        required = {'observation_id', 'fpl_position', 'origin'}
        if not required <= set(cases) or cases.observation_id.isna().any() or cases.observation_id.duplicated().any():
            raise ValueError('Unique case identities, positions and origins required')
        pos = cases.fpl_position.to_numpy()
        if not np.isin(pos, POSITIONS).all():
            raise ValueError('Unknown inference position')
        if any((pd.Timestamp(v).tzinfo is None for v in cases.origin if pd.notna(v))):
            raise ValueError('Inference origins must include an explicit timezone')
        origin = pd.to_datetime(cases.origin, utc=True)
        if origin.isna().any():
            raise ValueError('Missing inference origin')
        matrices = {}
        hashes = {}
        for ref in self.models.values():
            model = ref['model']
            name = model['recipe']['feature_set']
            if name not in feature_sets:
                raise ValueError('Missing required feature set ' + name)
            if name not in matrices:
                matrices[name] = _features(feature_sets[name], cases)
                hashes[name] = _array_digest(matrices[name][0])
            if matrices[name][1] != model['feature_columns']:
                raise ValueError('Named model/feature column order differs')
            if len(cases) and pd.Timestamp(ref['record']['training_max_available_at']) >= origin.min():
                raise ValueError('Fitted outcomes available at or after an inference origin')
        identity = digest(cases[['observation_id', 'fpl_position', 'origin']].to_dict('records'))
        key = digest({'cases': identity, 'features': hashes})
        if key == self._cache_key:
            return self._cache
        values = {}
        byconfig = {}
        for (group, target), cid in self.needed.items():
            if cid not in byconfig:
                model = self.models[cid]['model']
                population = POPULATIONS[target]
                mask = pos == 'GKP' if population == 'GKP' else np.isin(pos, ['GKP', 'DEF']) if population == 'GKP_DEF' else np.ones(len(cases), bool)
                shape = (len(cases), 3) if target == 'minute_state' else (len(cases),)
                result = np.zeros(shape, dtype=float)
                if mask.any():
                    x, columns = matrices[model['recipe']['feature_set']]
                    prediction = predict_model(model, pd.DataFrame(x[mask], columns=columns), pos[mask])
                    if np.asarray(prediction).shape != result[mask].shape:
                        raise ValueError('Model prediction shape differs from population')
                    result[mask] = prediction
                if not np.isfinite(result).all():
                    raise ValueError('Incomplete model prediction')
                byconfig[cid] = result
            values.setdefault(group, {})[target] = byconfig[cid]
        common = probabilities(values['rolling20']['minute_state'], len(cases))
        result = {'components': values, 'common_state': common, 'common_minutes': values['rolling20']['minutes'], 'case_identity_sha256': identity, 'feature_sha256': hashes, 'input_fingerprint': key}
        self._cache_key = key
        self._cache = result
        return result

    def predict(self, arm, cases, feature_sets):
        if arm not in self.arms:
            raise ValueError('Arm was not declared for this predictor')
        result = self._predict(cases, feature_sets)
        values = result['components']
        pos = cases.fpl_position.to_numpy()

        def assemble(group, mode='subtotal'):
            return assemble_supported_score(values[group], pos, mode)
        if arm in {'rolling20', 'portable'}:
            pred = assemble(arm)
            groups = [arm]
        elif arm == 'selected_subtotal':
            pred = assemble('selected')
            groups = ['selected']
        elif arm == 'direct_supported':
            pred = assemble('selected', 'direct')
            groups = ['selected']
        elif arm == 'football_assembly':
            pred = assemble('selected', 'football')
            groups = ['selected']
        else:
            weight = int(arm.rsplit('_', 1)[1]) / 100
            left = assemble('selected') if arm.startswith('blend_subtotal_direct_') else assemble('selected', 'direct')
            right = assemble('selected', 'direct') if arm.startswith('blend_subtotal_direct_') else assemble('selected', 'football')
            pred = (1 - weight) * left + weight * right
            groups = ['selected']
        state = result['common_state']
        frame = pd.DataFrame({'observation_id': cases.observation_id.to_numpy(), 'prediction': pred, 'p_appear': 1 - state[:, 0], 'p60': state[:, 2], 'expected_minutes': result['common_minutes'], 'minutes': result['common_minutes'], 'arm': arm, 'availability_procedure': 'common_rolling20'})
        if self.selected_diagnostics:
            selected = probabilities(values['selected']['minute_state'], len(cases))
            frame['selected_p_appear_diagnostic'] = 1 - selected[:, 0]
            frame['selected_p60_diagnostic'] = selected[:, 2]
            frame['selected_minutes_diagnostic'] = values['selected']['minutes']
        for group in groups:
            for target, prediction in values[group].items():
                if np.asarray(prediction).ndim == 2:
                    for j in range(3):
                        frame[f'component_{target}_p{j}'] = prediction[:, j]
                else:
                    frame['component_' + target] = prediction
        frame.attrs.update(version=VERSION, physical_fits=0, selection_sha256=digest(self.selection), case_identity_sha256=result['case_identity_sha256'], feature_sha256=result['feature_sha256'], input_fingerprint=result['input_fingerprint'], required_configurations=dict(((f'{g}:{t}', cid) for (g, t), cid in self.needed.items())), target='supported current-constant score with historical assists/bonus; defensive awards excluded', availability_policy='Protected rolling20 minutes and state shared by every arm; selected values are diagnostics only')
        return frame

def make_predictors(models, selection, recipes, arms=None, selected_diagnostics=True):
    shared = ProcedurePredictor(models, selection, recipes, arms, selected_diagnostics)
    return {arm: lambda cases, feature_sets, name=arm: shared.predict(name, cases, feature_sets) for arm in shared.arms}

def apply_current_status_diagnostic(cases, prediction, next_gameweek):
    """Optional next-GW source-status scenario; raw forecasts are always retained.

    A current injury/chance flag does not define availability for weeks2–6.
    No historical status is invented; only explicit snapshot fields are used.
    """
    if len(cases) != len(prediction) or cases.observation_id.tolist() != prediction.observation_id.tolist():
        raise ValueError('Current diagnostic case identities differ')
    if 'gameweek' not in cases:
        raise ValueError('Explicit forecast gameweek required')
    result = prediction.copy()
    factor = np.ones(len(cases))
    known = np.zeros(len(cases), bool)
    next_mask = cases.gameweek.to_numpy() == int(next_gameweek)
    status = cases.get('snapshot_status', pd.Series(None, index=cases.index)).astype(object)
    unavailable = next_mask & status.isin(['i', 's', 'u']).to_numpy()
    factor[unavailable] = 0.0
    known[unavailable] = True
    chance = pd.to_numeric(cases.get('chance_of_playing_next_round', pd.Series(np.nan, index=cases.index)), errors='coerce').to_numpy(float)
    reported = next_mask & ~unavailable & np.isfinite(chance)
    if np.any(reported & ((chance < 0) | (chance > 100))):
        raise ValueError('Reported next-round chance outside0–100')
    factor[reported] = chance[reported] / 100.0
    known[reported] = True
    result['raw_supported_score'] = result.prediction
    result['current_status_adjusted_score_diagnostic'] = result.prediction.to_numpy() * factor
    result['current_status_factor'] = factor
    result['current_status_adjustment_known'] = known
    result.attrs['current_status_policy'] = 'Next GW only: explicit i/s/u status→0, else reported chance_next/100; later weeks retain raw, unknown marked; diagnostic, not calibrated availability.'
    return result
