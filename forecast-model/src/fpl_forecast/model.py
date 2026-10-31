"""Frozen feature and prediction arithmetic, copied from validated research.

Only inference definitions are included. No fitting/search/evaluation functions.
"""
import numpy as np
import pandas as pd
from scipy.stats import beta
from threadpoolctl import threadpool_limits

VERSION='modern-components-v1'

LAG_HOURS=48

WINDOWS=(6,20)

BASE_LAG_VALUES=('minutes','goals_scored','assists','saves','goals_conceded','clean_sheets','bonus','yellow_cards','red_cards','own_goals','penalties_missed','penalties_saved','total_points','starts')

LAG_VALUES=BASE_LAG_VALUES+('negative_subtotal','known_score_subtotal','appeared','sixty')

DEFAULTS={'negative_subtotal':-.15,'known_score_subtotal':2.,'appeared':.7,'sixty':.5,'minutes':45.,'goals_scored':.07,'assists':.06,'saves':0.,'goals_conceded':1.35,'clean_sheets':.2,'bonus':.1,'yellow_cards':.1,'red_cards':.005,'own_goals':.002,'penalties_missed':.002,'penalties_saved':.002,'total_points':2.,'starts':.5}

COMPONENTS={
 'fpl_goals':('goals_scored','count','all','actual archived football goals, including penalties; FPL observed-fixture opportunity population'),
 'fpl_assists':('assists','count','all','historical FPL-assist definition; pre2025 is not current adjudication'),
 'fpl_saves':('saves','count','GKP','official archived shot saves; outfield emergency-keeper cases outside main population'),
 'fpl_yellow_cards':('yellow_cards','count','all','yellow-card count, including zero-minute events'),
 'fpl_red_cards':('red_cards','count','all','red-card count, including zero-minute events'),
 'fpl_own_goals':('own_goals','count','all','own-goal count'),
 'fpl_penalties_missed':('penalties_missed','count','all','penalty miss count'),
 'fpl_penalties_saved':('penalties_saved','count','GKP','penalty saves; explicit goalkeeper population'),
 'fpl_bonus_historical':('bonus','bounded_count','all','bonus under contemporaneous BPS; current-rule bonus bridge is unresolved'),
 'fpl_minutes':('minutes','continuous','all','official FPL minutes'),
 'fpl_state':('minute_state','categorical','all','0 minutes / 1-59 / 60+; does not suppress zero-minute scoring events'),
 'fpl_points_historical':('total_points','signed','all','actual archived points under their historical rules; secondary benchmark'),
 'fpl_negative_subtotal':('negative_subtotal','signed','all','current-constant card/own-goal/penalty-miss subtotal; red includes yellow'),
 'fpl_known_score_subtotal':('known_score_subtotal','signed','all','current constants under historical position; excludes assists, bonus and defensive contribution'),
 'fpl_starts':('starts','binary','all','masked before 2022-23 GW16; separate late-coverage track'),
 'fpl_defensive_contribution':('defensive_contribution','count','outfield','2025-26 only; no preconfirmation evaluation supported'),
}

def feature_columns():
 return [f'{v}_mean{w}' for w in WINDOWS for v in LAG_VALUES]+[f'{v}_observed{w}' for w in WINDOWS for v in LAG_VALUES]+['history_n','days_since','is_home','is_home_missing']+[f'position_{p}' for p in ('GKP','DEF','MID','FWD')]+['rule_goalkeeper10','rule_defcon','rule_bps2026']

def build_features(history,origins):
 """Features from outcomes available before each shared GW origin.

 Required history: player_id,event_time,season,gameweek,observation_id plus
 LAG_VALUES. Required origins: player_id,origin,fpl_position,season,gameweek,
 was_home. Counts of observed lag values keep missingness distinct from zero.
 The interface also supports future/current origins with no target columns.
 """
 if history.observation_id.duplicated().any():raise ValueError('Duplicate historical observation ID')
 if not pd.api.types.is_datetime64_any_dtype(history.event_time):history=history.assign(event_time=pd.to_datetime(history.event_time,utc=True))
 if history.event_time.dt.tz is None:raise ValueError('UTC-aware event time required')
 history=history.assign(event_time=history.event_time.dt.as_unit('ns'))
 h=history.sort_values(['event_time','observation_id'],kind='stable');groups={p:g for p,g in h.groupby('player_id',sort=False)}
 n=len(origins);nv=len(LAG_VALUES);nw=len(WINDOWS);x=np.zeros((n,len(feature_columns())),dtype=np.float32)
 times=pd.to_datetime(origins.origin,utc=True).dt.as_unit('ns').astype('int64').to_numpy();fallback=np.array([DEFAULTS[v] for v in LAG_VALUES],dtype=float)
 for player,ix in origins.groupby('player_id',sort=False).indices.items():
  g=groups.get(player)
  if g is None:
   x[ix,:nv*nw]=np.tile(fallback,nw);x[ix,2*nv*nw+1]=3650;continue
  ts=g.event_time.astype('int64').to_numpy();values=g[list(LAG_VALUES)].to_numpy(dtype=float);ok=np.isfinite(values)
  sums=np.vstack([np.zeros(nv),np.cumsum(np.where(ok,values,0),axis=0)]);counts=np.vstack([np.zeros(nv),np.cumsum(ok,axis=0)])
  ends=np.searchsorted(ts+int(pd.Timedelta(hours=LAG_HOURS).value),times[ix],side='left')
  # Shared origin must exclude every target-GW observation, not only the target fixture.
  seasons=g.season.to_numpy();gws=g.gameweek.to_numpy();pseason=origins.iloc[ix].season.to_numpy();pgw=origins.iloc[ix].gameweek.to_numpy()
  for season,gw,end in zip(pseason,pgw,ends):
   if np.any((seasons[:end]==season)&(gws[:end]==gw)):raise ValueError('Target GW entered feature history')
  for wi,w in enumerate(WINDOWS):
   begins=np.maximum(0,ends-w);den=counts[ends]-counts[begins];num=sums[ends]-sums[begins]
   x[ix,wi*nv:(wi+1)*nv]=np.divide(num,den,out=np.broadcast_to(fallback,num.shape).copy(),where=den>0)
   x[ix,nv*nw+wi*nv:nv*nw+(wi+1)*nv]=den
  x[ix,2*nv*nw]=ends;x[ix,2*nv*nw+1]=np.where(ends>0,np.minimum((times[ix]-ts[np.maximum(ends-1,0)])/86400e9,3650),3650)
 offset=2*nv*nw+2
 x[:,offset]=origins.was_home.fillna(False).astype(float);x[:,offset+1]=origins.was_home.isna().astype(float)
 for j,p in enumerate(('GKP','DEF','MID','FWD')):x[:,offset+2+j]=(origins.fpl_position==p).astype(float)
 x[:,offset+6]=(origins.season>='2025-26').astype(float);x[:,offset+7]=(origins.season>='2025-26').astype(float);x[:,offset+8]=(origins.season>='2026-27').astype(float)
 if not np.isfinite(x).all():raise ValueError('Nonfinite deterministic features')
 return pd.DataFrame(x,columns=feature_columns(),index=origins.index)

def masks_and_targets(panel):
 p=panel.copy();unknown=~p.fpl_position.isin(['GKP','DEF','MID','FWD'])
 if unknown.any():raise ValueError('Unknown archived FPL position')
 unavailable=(p.season<'2022-23')|((p.season=='2022-23')&(p.gameweek<16))
 p.loc[unavailable,'starts']=np.nan
 p.loc[p.season<'2025-26','defensive_contribution']=np.nan
 p['appeared']=np.where(p.minutes.notna(),(p.minutes>0).astype(float),np.nan);p['sixty']=np.where(p.minutes.notna(),(p.minutes>=60).astype(float),np.nan)
 p['defensive_hit']=np.where(p.defensive_contribution.notna()&(p.fpl_position!='GKP'),(p.defensive_contribution>=np.where(p.fpl_position=='DEF',10,12)).astype(float),np.nan)
 p['save_blocks']=p.saves//3;p['conceded_blocks']=p.goals_conceded//2
 p['minute_state']=np.where(p.minutes.isna(),np.nan,np.where(p.minutes==0,0,np.where(p.minutes<60,1,2)))
 negative=p[['yellow_cards','red_cards','own_goals','penalties_missed']].notna().all(axis=1)
 p['negative_subtotal']=np.where(negative,-np.where(p.red_cards>0,3*p.red_cards,p.yellow_cards)-2*p.own_goals-2*p.penalties_missed,np.nan)
 goalscore=p.fpl_position.map({'GKP':10,'DEF':6,'MID':5,'FWD':4});csscore=p.fpl_position.map({'GKP':4,'DEF':4,'MID':1,'FWD':0})
 known=negative & p[['minutes','goals_scored','clean_sheets','goals_conceded','saves','penalties_saved']].notna().all(axis=1)
 # Archived player-scoped clean sheets/GC preserve official substitution treatment.
 basic=(p.minutes>0).astype(int)+(p.minutes>=60).astype(int)+goalscore*p.goals_scored+csscore*p.clean_sheets+(p.saves//3)+5*p.penalties_saved-p.fpl_position.isin(['GKP','DEF']).astype(int)*(p.goals_conceded//2)+p.negative_subtotal
 p['known_score_subtotal']=np.where(known,basic,np.nan)
 for component,(target,kind,population,definition) in COMPONENTS.items():
  mask=p[target].notna()
  if kind not in ('signed','continuous'):mask&=p[target]>=0
  if component=='fpl_minutes':mask&=p.minutes.between(0,90)
  if population=='GKP':mask&=p.fpl_position=='GKP'
  elif population=='outfield':mask&=p.fpl_position!='GKP'
  p['mask_'+component]=mask
 return p

def predict_model(model,features,origins,component):
 target,kind,_,_=COMPONENTS[component];x=np.asarray(features)
 if isinstance(model,dict):
  if model['family']=='constant':return np.full(len(origins),model['value'])
  prior=np.array([model['by_position'].get(p,model['global']) for p in origins.fpl_position])
  if model['family']=='prior':return prior
  # Rolling forecast uses only observed lag means and training-only positional shrinkage.
  cols=feature_columns()
  if kind=='categorical':
   # Explicit earlier minute-state frequencies; no division of mean minutes into states.
   n=x[:,cols.index('minutes_observed6')];appear=x[:,cols.index('appeared_mean6')];long=np.minimum(x[:,cols.index('sixty_mean6')],appear)
   empirical=np.column_stack([1-appear,appear-long,long]);return (n[:,None]*empirical+6*prior)/(n[:,None]+6)
  if target in LAG_VALUES:
   mean=x[:,cols.index(target+'_mean6')];n=x[:,cols.index(target+'_observed6')];return (n*mean+6*prior)/(n+6)
  # The derived signed subtotal has no direct lag field; keep its positional prior
  # rather than pretend an arbitrary algebra on means preserves nonlinear blocks.
  return prior
 with threadpool_limits(limits=1):
  return model.predict_proba(x) if kind in ('categorical','binary') else model.predict(x)

def defensive_prior(history,origins):
    valid=history[(history.season>='2025-26')&history.defensive_contribution.notna()&(history.fpl_position!='GKP')].copy();valid=valid[(valid.event_time+pd.Timedelta(hours=48))<origins.origin.iloc[0]]
    valid['hit']=(valid.defensive_contribution>=np.where(valid.fpl_position=='DEF',10,12)).astype(int);pos=valid.groupby('fpl_position').hit.mean().to_dict();g=valid.groupby(['player_id','fpl_position']).hit.agg(['sum','count']);rows=[]
    for row in origins.itertuples():
        if row.fpl_position=='GKP':rows.append((0.,0.,0.,0));continue
        prior=float(pos.get(row.fpl_position,.2));key=(row.player_id,row.fpl_position);hits,n=(float(g.loc[key,'sum']),int(g.loc[key,'count'])) if key in g.index else (0.,0);alpha=max(.01,hits+20*prior);b=max(.01,n-hits+20*(1-prior));rows.append((alpha/(alpha+b),float(beta.ppf(.025,alpha,b)),float(beta.ppf(.975,alpha,b)),n))
    return pd.DataFrame(rows,columns=['defensive_hit_prior','defensive_prior_low','defensive_prior_high','defensive_prior_observations'],index=origins.index)

def normalize_prediction(predicted,component):
    """Apply the same declared bounds used in evaluation, preserving signed values."""
    if not component.startswith('fpl_'):return np.asarray(predicted)
    kind=COMPONENTS[component][1];predicted=np.asarray(predicted,float)
    if kind=='categorical':
        predicted=np.clip(predicted,1e-9,1);return predicted/predicted.sum(axis=1,keepdims=True)
    predicted=predicted.reshape(-1)
    if kind in ['count','bounded_count']:predicted=np.maximum(predicted,1e-9)
    if component=='fpl_bonus_historical':predicted=np.minimum(predicted,3)
    if component=='fpl_minutes':predicted=np.clip(predicted,0,90)
    return predicted

def assemble_approximate(frame):
    """Addition of expectations needs no independence; bridge validity is separate."""
    d=frame.copy();required=['fpl_known_score_subtotal','fpl_assists','fpl_bonus_historical','defensive_hit_prior']
    if not set(required)<=set(d.columns):raise ValueError('missing approximate scoring component')
    d['expected_known_score_subtotal']=d.fpl_known_score_subtotal
    d['approximate_current_rule_expected_total']=d.fpl_known_score_subtotal+3*d.fpl_assists+d.fpl_bonus_historical+2*d.defensive_hit_prior
    d['current_rule_expected_total']=np.nan
    # Uncalibrated sensitivity to explicit bridge means, not an outcome interval.
    bridge=3*d.fpl_assists+d.fpl_bonus_historical
    d['bridge_sensitivity_low']=d.fpl_known_score_subtotal+.75*bridge+2*d.defensive_prior_low
    d['bridge_sensitivity_high']=d.fpl_known_score_subtotal+1.25*bridge+2*d.defensive_prior_high
    return d
