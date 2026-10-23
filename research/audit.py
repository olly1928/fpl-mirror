"""Bounded, source-specific quality checks for the actual feature population."""
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd


def run(features,output):
    historical=pd.read_pickle(features/'historical.pkl')
    current=pd.read_pickle(features/'current.pkl')
    findings={}
    for label,df in [('historical',historical),('current',current)]:
        f=[c for c in df if c.startswith('f_')]
        result={'rows':len(df),'feature_count':len(f),
                'duplicate_player_fixtures':int(df.duplicated(['season','fixture_id','player_id']).sum()),
                'asof_violations':int((df.latest_feature_time>=df.cutoff).sum()),
                'unknown_points_or_minutes':int(df[['y_total_points','y_minutes']].isna().any(axis=1).sum()),
                'invalid_fixture_minutes':int((~df.y_minutes.between(0,90)).sum()),
                'zero_minutes_with_nonzero_points':int(((df.y_minutes==0)&(df.y_total_points!=0)).sum()),
                'understat_recent_365_rows':int((df.f_us_n_365>0).sum()),
                'understat_recent_730_rows':int((df.f_us_n_730>0).sum()),
                'team_history_missing_rows':int((df.f_team_missing>0).sum()),
                'prior_missing_rows':int((df.f_prior_missing>0).sum()),
                'per_season':df.groupby('season').agg(rows=('gw','size'),active_fixture_rows=('active','sum'),
                    cold_fixture_rows=('cold','sum'),dgw_fixture_rows=('dgw','sum')).reset_index().to_dict('records'),
                'feature_missingness':df[f].isna().mean().to_dict()}
        if any(result[k] for k in ['duplicate_player_fixtures','asof_violations','unknown_points_or_minutes','invalid_fixture_minutes']):
            raise AssertionError(result)
        findings[label]=result
    findings['interpretation']=[
        'Full player-fixture outcomes are not complete historical deadline roster/schedule/news vintages.',
        'Partial early SQLite records with identity conflicts or unknown targets are quarantined.',
        'Understat appearances have explicit-code mapping coverage and provider-specific missingness; no fake zero appearances are added.',
        'Understat archive ends 2025-04-07; current one-year coverage is absent and two-year coverage is partial.',
        'Very old annual/team records are retained but are not falsely treated as per-GW modern player labels.',
        'Current previous-season inputs use later-captured completed outcomes, including corrections to six frozen-v1 priors; not a pristine complete archived feature vintage.'
    ]
    findings['feature_manifest_sha256']=hashlib.sha256((features/'feature-manifest.json').read_bytes()).hexdigest()
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(findings,indent=2)+'\n')
    print(json.dumps({k:{c:v for c,v in a.items() if c not in ['feature_missingness','per_season']} for k,a in findings.items() if isinstance(a,dict)}))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--features',type=Path,default=Path('research/work/canonical-features'))
    p.add_argument('--output',type=Path,default=Path('research/results/data-quality.json'))
    a=p.parse_args();run(a.features,a.output)
