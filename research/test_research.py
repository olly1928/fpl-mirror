"""Adversarial, small-data tests for consequential research boundaries."""
import argparse
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from research import prepare
from research.estimators import candidates,minutes_candidates,scoring_target
from research.selection import select,fit_weights
from research.finalize import paired_blocks


class ChronologyTests(unittest.TestCase):
    def make_data(self,folder):
        rows=[]
        for season,gw,day,fixture in [('2013-14',1,'2013-08-17',1),
                  ('2014-15',1,'2014-08-16',1),('2014-15',2,'2014-08-23',2),
                  ('2014-15',2,'2014-08-26',3),('2014-15',3,'2014-08-30',4)]:
            row={s:0. for s in prepare.STATS}
            row.update(season=season,gw=gw,player_id=1,code=11,pos='DEF',name='Test',
                       fixture_id=fixture,kickoff_time=day+'T14:00:00Z',team_id=1,opponent_team=2,
                       was_home=True,value=50,minutes=90,total_points=6,clean_sheets=1)
            rows.append(row)
        frame=pd.DataFrame(rows)
        frame[frame.season=='2013-14'].to_csv(folder/'fpl-2013-14.csv.gz',index=False)
        frame[frame.season!='2013-14'].to_csv(folder/'fpl-complete-seasons.csv.gz',index=False)
        pd.DataFrame([dict(season='2013-14',code=11,minutes=1800,total_points=80)]).to_csv(folder/'annual-history.csv.gz',index=False)
        fields=['fpl_code','core_source_conflict','date','usable_time','time','xG','xA','shots','key_passes','npxG','xGChain','xGBuildup']
        pd.DataFrame(columns=fields).to_csv(folder/'understat-player-matches.csv.gz',index=False)
        football=[]
        for r in rows:
            dt=pd.Timestamp(r['kickoff_time'])
            football.append(dict(season=r['season'],Date=dt.strftime('%d/%m/%Y'),HomeTeam='Arsenal',AwayTeam='Chelsea',FTHG=2,FTAG=0))
        pd.DataFrame(football).to_csv(folder/'team-results-and-odds.csv.gz',index=False)

    def run_prepare(self,folder,output):
        a=argparse.Namespace(source=folder/'source',output=output,reuse_season_cache=False)
        with patch.object(prepare,'DATA',folder):
            # Test sources live outside ROOT; provenance paths are just display
            # strings, so temporarily set the common root to their parent.
            with patch.object(prepare,'ROOT',folder.parent):
                (folder.parent/'research').mkdir(exist_ok=True)
                (folder.parent/'research/protocol.json').write_text('{}')
                return prepare.historical(a)[0]

    def test_future_and_same_gw_outcomes_cannot_change_forecasts(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);data=root/'data';data.mkdir();self.make_data(data)
            a=self.run_prepare(data,root/'a')
            p=data/'fpl-complete-seasons.csv.gz';raw=pd.read_csv(p)
            # Poison both the earlier match in a DGW and the future GW.
            raw.loc[raw.gw>=2,['total_points','minutes','expected_goals']]=[99,1,50]
            raw.to_csv(p,index=False)
            b=self.run_prepare(data,root/'b')
            use=(a.season=='2014-15')&(a.gw==2)
            cols=[c for c in a if c.startswith('f_')]
            # Inference explicitly casts all features to float32. Pandas may
            # widen an entire column when a future row introduces a fraction.
            pd.testing.assert_frame_equal(a.loc[use,cols].reset_index(drop=True),b.loc[use,cols].reset_index(drop=True),check_dtype=False)
            self.assertTrue((a.latest_feature_time<a.cutoff).all())

    def test_date_only_understat_requires_next_calendar_day(self):
        with tempfile.TemporaryDirectory() as t:
            path=Path(t)/'us.csv'
            pd.DataFrame([dict(fpl_code=11,core_source_conflict=False,date='2014-08-16',time=90,
                              xG=1,xA=.2,shots=3,key_passes=2,npxG=1,xGChain=1,xGBuildup=.3)]).to_csv(path,index=False)
            us=prepare.Understat(path)
            self.assertEqual(us.features(11,prepare.stamp('2014-08-16T23:59:00Z'))['us_n_365'],0)
            self.assertEqual(us.features(11,prepare.stamp('2014-08-17T00:01:00Z'))['us_n_365'],1)

    def test_missing_xg_is_missing_not_zero_and_exposure_is_retained(self):
        row={s:np.nan for s in prepare.STATS};row.update(minutes=90,total_points=4,goals_scored=1,gw=1,_time=0,value=50)
        f=prepare.player_features([row],{},'FWD',2,2014,100000)
        self.assertTrue(np.isnan(f['expected_goals_90_3']))
        self.assertGreater(f['ew_exposure_h8'],0)
        self.assertEqual(prepare.team_name('Man Utd'),'Man United')


class SelectionTests(unittest.TestCase):
    def test_future_results_do_not_select_current_model(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t)
            for year in (2016,2017):
                folder=root/str(year);folder.mkdir()
                target=pd.DataFrame({'season':[f'{year}-{str(year+1)[-2:]}']*4,'gw':[1,1,2,2],
                     'player_id':[1,2,1,2],'active':[1]*4,'y_total_points':[1.,4.,2.,5.]})
                target.to_pickle(folder/'targets.pkl')
                for name,pred,mse,family in [('rolling_01',np.array([2.,4.,3.,5.]),.5,'rolling'),
                                             ('ridge_01',np.array([0.,4.,1.,5.]),.5,'ridge')]:
                    np.savez_compressed(folder/f'{name}-seed1729.npz',points=pred)
                    (folder/f'{name}-seed1729.json').write_text(json.dumps({'status':'ok','metrics':{'active':{'mse':mse}}}))
            before=select(root,2018)
            future=root/'2018';future.mkdir()
            (future/'rolling_01-seed1729.json').write_text(json.dumps({'status':'ok','metrics':{'active':{'mse':100000}}}))
            self.assertEqual(before,select(root,2018))
            self.assertAlmostEqual(sum(before['ensemble_weights']),1)
            self.assertTrue(all(w>=0 for w in before['ensemble_weights']))
            self.assertLess(max(before['selected_using_years']),2018)

    def test_ensemble_and_paired_uncertainty_invariants(self):
        x=np.array([[1,2],[3,1],[2,4],[0,2]],dtype=float);y=np.array([1,2,3,1.])
        w=fit_weights(x,y,np.array([1,1,2,2]))
        self.assertAlmostEqual(float(w.sum()),1)
        self.assertTrue((w>=0).all())
        d=pd.DataFrame({'season':['a']*4,'gw':[1,2,3,4],'y_total_points':y,'a':x[:,0],'b':x[:,0]})
        out=paired_blocks(d,'a','b',draws=100)
        self.assertEqual(out['rmse_improvement_percent'],0)
        self.assertEqual(out['interval_95_percent'],[0,0])

    def test_signed_points_are_not_passed_to_poisson(self):
        d=pd.DataFrame({'year':[2024,2025,2025],'pos':['DEF','DEF','GKP'],
                        'y_defensive_contribution':[20,10,30],'y_total_points':[-1,4,-2]})
        np.testing.assert_equal(scoring_target(d),[-1,2,-2])
        self.assertGreaterEqual(len(candidates()),60)
        self.assertGreaterEqual(len(set(c.family for c in candidates())),10)
        self.assertEqual(len(minutes_candidates()),12)


if __name__=='__main__':
    unittest.main()
