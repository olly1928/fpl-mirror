"""Repeat the 2025 outer ensemble with alternative seeds, never choose a seed."""
import argparse
from pathlib import Path
import json

import numpy as np
import pandas as pd
from threadpoolctl import threadpool_limits

from research.estimators import candidates,augment,fit_candidate,predict_candidate
from research.experiment import point_metrics,dump,safe


def main(args):
    threadpool_limits(limits=1,user_api='blas')
    choices={c.name:c for c in candidates()}
    selected=json.loads((args.output/'nested-selection.json').read_text())['2025']
    df=augment(pd.read_pickle(args.features/'historical.pkl'))
    test=df[df.year==2025];scores=[]
    for seed in (2718,3141):
        values=[]
        for name in selected['ensemble_names']:
            c=choices[name]
            train=df[(df.year<2025)&(df.year>=2025-c.train_years)]
            model,cols=fit_candidate(c,train,seed)
            p,_=predict_candidate(c,model,cols,test);values.append(p)
            print('seed repeat',seed,name,flush=True)
        pred=np.stack(values,axis=1)@np.array(selected['ensemble_weights'])
        scores.append({'year':2025,'seed':seed,**point_metrics(test,pred)})
    primary=json.loads((args.output/'nested-scores.json').read_text())
    primary=next(r for r in primary if r['year']==2025 and r['model']=='nested_ensemble')
    scores.insert(0,{'year':2025,'seed':1729,'active':primary['active'],'all':primary['all']})
    dump(args.output/'seed-stability.json',safe({'scores':scores,'seed_selection':False,
          'configuration_and_weights_selected_before_2025_outcomes':True,
          'caveat':'Three seeds diagnose numerical/algorithmic variance, not all future uncertainty. Some boosting configurations are deterministic across seeds.'}))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--features',type=Path,default=Path('research/work/canonical-features'))
    p.add_argument('--output',type=Path,default=Path('research/results'))
    main(p.parse_args())
