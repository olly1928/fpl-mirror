"""No-network hosted parity against a tiny frozen lab inference fixture."""
import argparse
import json
from pathlib import Path
import time

import numpy as np
import pandas as pd

from .inference import verify_package,apply_saved
from .utils import atomic_json,bound_file,read_json,sha256

ATOL=1e-10
RTOL=1e-10


def check(package_root,output=None):
    started=time.perf_counter();root=Path(package_root).resolve();package=verify_package(root)
    ref=package.get('offline_fixture')
    if not ref:raise ValueError('Final actual-model at-origin parity fixture is missing')
    path=bound_file(root,ref['path'],ref['sha256']);fixture=read_json(path);base=path.parent
    if fixture.get('version')!='selected-procedure-offline-v1'or fixture.get('physical_fits')!=0:raise ValueError('Wrong offline fixture contract')
    if fixture['selection_sha256']!=package['selection_sha256']or fixture['primary_seed']!=package['primary_seed']:
        raise ValueError('Offline fixture belongs to a different selection/seed')
    if fixture['model_hashes']!={r['configuration_id']:r['model_sha256']for r in package['models']}:
        raise ValueError('Offline fixture fitted model population changed')
    if fixture['tolerance']!={'absolute':ATOL,'relative':RTOL}:raise ValueError('Offline parity tolerance changed')
    def frame(name):
        info=fixture['files'][name];return pd.read_parquet(bound_file(base,info['path'],info['sha256']))
    cases=frame('cases');expected=frame('expected');defense=frame('defensive_prior')
    features={name:frame(name)for name in fixture['feature_sets']}
    if not 1<=len(cases)<=32 or len(expected)!=len(cases)or len(defense)!=len(cases):raise ValueError('Invalid tiny fixture population')
    actual=apply_saved(root,package,cases,features)
    if list(actual.columns)!=list(expected.columns)or actual.observation_id.tolist()!=expected.observation_id.tolist():raise ValueError('Offline complete-output columns/identity changed')
    checks=[]
    for column in expected:
        a,b=actual[column].to_numpy(),expected[column].to_numpy()
        if pd.api.types.is_numeric_dtype(expected[column]):
            if not np.isfinite(a).all()or not np.isfinite(b).all()or not np.allclose(a,b,rtol=RTOL,atol=ATOL):
                raise ValueError('Hosted numeric parity failed: '+column)
            checks.append({'column':column,'maximum_absolute_difference':float(np.max(np.abs(a-b)))})
        elif actual[column].tolist()!=expected[column].tolist():raise ValueError('Hosted identity/metadata parity failed: '+column)
    if not defense.defensive_hit_prior.between(0,1).all():raise ValueError('Invalid frozen defensive prior')
    approximate=actual.prediction.to_numpy()+2*defense.defensive_hit_prior.to_numpy()
    expected_total=frame('approximate_total').approximate_current_rule_expected_total.to_numpy()
    if not np.isfinite(expected_total).all()or not np.allclose(approximate,expected_total,rtol=RTOL,atol=ATOL):raise ValueError('Complete approximate-total parity failed')
    checks.append({'column':'approximate_current_rule_expected_total','maximum_absolute_difference':float(np.max(np.abs(approximate-expected_total)))})
    receipt={'complete':True,'physical_fits':0,'network_requests':0,'rows':len(cases),'checks':checks,
        'absolute_tolerance':ATOL,'relative_tolerance':RTOL,'package_manifest_sha256':sha256(root/'manifest.json'),
        'offline_fixture_sha256':sha256(path),'compute_seconds':time.perf_counter()-started,
        'scope':'Actual saved models, preprocessing, complete selected-score assembly, common availability and fixed defensive bridge. Feature-builder semantics tested separately.'}
    if output:atomic_json(output,receipt)
    return receipt


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--package-root',required=True);p.add_argument('--output');a=p.parse_args();print(json.dumps(check(a.package_root,a.output)))
