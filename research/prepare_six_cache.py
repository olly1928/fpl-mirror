"""Build the same six-GW labels one season at a time to bound local memory."""
import argparse
from concurrent.futures import ThreadPoolExecutor,as_completed
from dataclasses import asdict
import gc
import hashlib
from pathlib import Path

import pandas as pd
from threadpoolctl import threadpool_limits

from research.estimators import augment
from research.experiment import dump
from research.six_week import build_labels,fit_six,choices,ROOT


def main(args):
    threadpool_limits(limits=1,user_api='blas')
    frames=[];audit=[]
    for name in ('historical.pkl','current.pkl'):
        df=pd.read_pickle(args.features/name)
        for season,a in df.groupby('season',sort=True):
            labels,checks=build_labels(augment(a))
            frames.append(labels);audit.extend(checks)
            print('six labels',season,len(labels),flush=True)
        del df;gc.collect()
    labels=pd.concat(frames,ignore_index=True)
    args.work.mkdir(parents=True,exist_ok=True)
    labels.to_pickle(args.work/'labels.pkl')
    dump(args.work/'coverage.json',audit)
    identity=hashlib.sha256((ROOT/'research/six_week.py').read_bytes()+(ROOT/'research/protocol-six.json').read_bytes()+(args.features/'feature-manifest.json').read_bytes()).hexdigest()
    dump(args.work/'experiment.json',{'identity':identity,'candidates':[asdict(c) for c in choices()],'coverage':audit})
    if args.fit_current:
        with ThreadPoolExecutor(max_workers=2) as pool:
            for f in as_completed([pool.submit(fit_six,c,labels,2026,args.work,identity) for c in choices()]):
                f.result()


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--features',type=Path,default=ROOT/'research/work/canonical-features')
    p.add_argument('--work',type=Path,default=ROOT/'research/work/remote/six-week')
    p.add_argument('--fit-current',action='store_true')
    main(p.parse_args())
