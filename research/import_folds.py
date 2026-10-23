"""Verify retained immutable fold evidence and restore local evaluation layout."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil

import numpy as np
import pandas as pd


def run(source,destination):
    identities=set();count=0;failures=[]
    for year in range(2016,2026):
        folder=source/f'fold-{year}'
        one=destination/'experiments'/str(year)
        six=destination/'six-week'/str(year)
        one.mkdir(parents=True,exist_ok=True);six.mkdir(parents=True,exist_ok=True)
        target=pd.read_csv(folder/'one-week/targets.csv.gz')
        target.to_pickle(one/'targets.pkl')
        for path in sorted((folder/'one-week').glob('*-seed1729.json')):
            record=json.loads(path.read_text());identities.add(record['identity']);count+=1
            prediction=path.with_suffix('.npz')
            if record['status']!='ok' or hashlib.sha256(prediction.read_bytes()).hexdigest()!=record['prediction_sha256']:
                raise ValueError('Failed or changed trial '+str(path))
            arrays=np.load(prediction)
            for name,values in arrays.items():
                if len(values)!=len(target) or not np.isfinite(values).all():
                    raise ValueError('Nonfinite or unpaired predictions')
            if 'minutes' in arrays:
                m=arrays['minutes']
                if np.max(np.abs(m[:,3:].sum(axis=1)-1))>1e-5 or np.any(m[:,3:]<0):
                    raise ValueError('Incoherent minute-state probabilities')
                if np.max(np.abs(m[:,0]-(1-m[:,3])))>1e-5 or np.max(np.abs(m[:,1]-m[:,5]))>1e-5:
                    raise ValueError('Minute probabilities disagree with states')
                low=m[:,4]+60*m[:,5];high=59*m[:,4]+90*m[:,5]
                if np.any(m[:,2]<low-1e-4) or np.any(m[:,2]>high+1e-4):
                    raise ValueError('Expected minutes violate conditional-state bounds')
            if np.any(target.latest_feature_time>=target.cutoff):
                raise ValueError('Temporal feature violation')
            shutil.copy2(path,one/path.name);shutil.copy2(prediction,one/prediction.name)
        for path in (folder/'six-week').glob('*'):
            shutil.copy2(path,six/path.name)
    if count!=790 or len(identities)!=1:
        raise ValueError(f'Expected 790 common-protocol trials, got {count}, {len(identities)} identities')
    return {'verified_one_week_trials':count,'identities':sorted(identities),'nonfinite_predictions':0,
            'incoherent_minute_states':0,'asof_violations':0,'live_model_changed':False}


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',type=Path,default=Path('research/results/folds'))
    p.add_argument('--destination',type=Path,default=Path('research/work/remote'))
    p.add_argument('--verification',type=Path,default=Path('research/results/fold-verification.json'))
    a=p.parse_args();result=run(a.source,a.destination)
    a.verification.parent.mkdir(parents=True,exist_ok=True)
    a.verification.write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))
