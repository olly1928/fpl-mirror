"""Declared candidate library. All fit methods see training seasons only."""
from __future__ import annotations

from dataclasses import dataclass, asdict
from itertools import product
import numpy as np
import pandas as pd
from scipy.stats import poisson
from sklearn.impute import SimpleImputer
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import make_pipeline
from sklearn.linear_model import Ridge, ElasticNet
from sklearn.ensemble import ExtraTreesRegressor, RandomForestRegressor, HistGradientBoostingRegressor
from lightgbm import LGBMRegressor, LGBMClassifier
from xgboost import XGBRegressor
from catboost import CatBoostRegressor


@dataclass(frozen=True)
class Candidate:
    name: str
    family: str
    params: dict
    feature_set: str = 'full'
    train_years: int = 99


def candidates():
    out = []
    def add(family, params, feature_set='full', train_years=99):
        name = family + '_' + str(sum(c.family == family for c in out) + 1).zfill(2)
        out.append(Candidate(name,family,params,feature_set,train_years))
    for w in (3,6,12,38):
        add('rolling',{'window':w})
    for half, strength in product((4,8,16),(450,900,1800)):
        add('empirical',{'half':half,'prior_strength':strength})
    for alpha in (.1,1,10,100,1000):
        add('ridge',{'alpha':alpha})
    for alpha,ratio in product((.001,.01,.1),(.1,.5)):
        add('elasticnet',{'alpha':alpha,'l1_ratio':ratio})
    for leaf, fraction in product((20,80),(.7,1.)):
        add('extra_trees',{'min_samples_leaf':leaf,'max_features':fraction,'n_estimators':64,'max_depth':14},train_years=5)
    for leaf in (30,100):
        add('random_forest',{'min_samples_leaf':leaf,'max_features':.7,'n_estimators':64,'max_samples':.75,'max_depth':14},train_years=5)
    for leaves, iterations in product((7,15),(180,400)):
        add('hist_boost',{'max_leaf_nodes':leaves,'max_iter':iterations,'l2_regularization':20,'learning_rate':.04,'min_samples_leaf':80})
    add('hist_boost',{'loss':'absolute_error','max_leaf_nodes':15,'max_iter':300,'l2_regularization':20,'learning_rate':.04,'min_samples_leaf':80})
    for leaves, leaf_n, iterations in product((7,15,31),(80,250),(250,550)):
        add('lightgbm',{'num_leaves':leaves,'min_child_samples':leaf_n,'n_estimators':iterations,'reg_lambda':30,'learning_rate':.035})
    for depth, iterations in product((3,5),(300,650)):
        add('xgboost',{'max_depth':depth,'n_estimators':iterations,'reg_lambda':30,'learning_rate':.035,'min_child_weight':60,'subsample':.9,'colsample_bytree':.9})
    for depth, iterations in product((4,6),(350,750)):
        add('catboost',{'depth':depth,'iterations':iterations,'l2_leaf_reg':30,'learning_rate':.04})
    for leaves, strength in product((7,15),(30,150)):
        add('hurdle',{'num_leaves':leaves,'min_child_samples':150,'n_estimators':350,'reg_lambda':strength,'learning_rate':.035})
    for leaves in (7,15):
        add('poisson_components',{'num_leaves':leaves,'min_child_samples':150,'n_estimators':300,'reg_lambda':50,'learning_rate':.035})
    # Identical hyperparameters, explicit source/recency ablations, selected only
    # with preceding OOF seasons. No post-test feature selection.
    anchor={'num_leaves':15,'min_child_samples':150,'n_estimators':400,'reg_lambda':50,'learning_rate':.035}
    for feature_set in ('no_understat','fpl_only','no_price','compact'):
        add('lightgbm',anchor,feature_set)
    for years in (3,5):
        add('lightgbm',anchor,'full',years)
    return out


def minutes_candidates():
    return [Candidate(f'minutes_{i:02d}','minutes',{
        'num_leaves':leaves,'min_child_samples':n,'n_estimators':iterations,
        'reg_lambda':50,'learning_rate':.035})
        for i,(leaves,n,iterations) in enumerate(product((7,15,31),(150,500),(250,500)),1)]


def select_features(df, name):
    cols=[c for c in df if c.startswith('f_')]
    if name in ('no_understat','fpl_only'):
        cols=[c for c in cols if not c.startswith('f_us_')]
    if name=='fpl_only':
        cols=[c for c in cols if not any(s in c for s in ('f_team_','f_opp_team_','goals_proxy','f_opponent_missing'))]
    if name=='no_price':
        cols=[c for c in cols if c!='f_lag_price']
    if name=='compact':
        cols=[c for c in cols if any(s in c for s in ('f_pos_','f_prior_','f_minutes_','f_appear_','f_long_',
               'f_points_','goals_proxy','f_home','f_zero_streak','f_last_minutes','f_gw','f_dc_rules','base_'))]
    return cols


def lgbm(params, seed=1729, classifier=False, **kwargs):
    kind=LGBMClassifier if classifier else LGBMRegressor
    return kind(**params,random_state=seed,n_jobs=2,verbosity=-1,
                deterministic=True,force_col_wise=True,**kwargs)


def empirical(df, half=8, prior_strength=900):
    def x(c,default=0):
        return df[c].to_numpy(dtype=float) if c in df else np.full(len(df),default,dtype=float)
    def filled(c,default=0):
        return np.nan_to_num(x(c,default),nan=default)
    mins=filled('f_long_6',.4)*filled('f_long_minutes_6',85)+(filled('f_appear_6',.5)-filled('f_long_6',.4))*filled('f_short_minutes_6',20)
    exposure=filled(f'f_ew_exposure_h{half}')
    prior_m=np.minimum(filled('f_prior_minutes'),prior_strength)
    defaults={
        'goals_scored':[.01,.04,.16,.34], 'assists':[.01,.06,.15,.12],
        'expected_goals':[.01,.04,.16,.34], 'expected_assists':[.01,.05,.13,.1],
        'bonus':[.32,.3,.35,.5], 'saves':[3,0,0,0],
        'yellow_cards':[.06,.17,.13,.12], 'red_cards':[.002,.006,.004,.003],
        'defensive_contribution':[0,7.2,7.,3.5]}
    pos=np.stack([filled('f_pos_'+p) for p in ['GKP','DEF','MID','FWD']],axis=1)
    def rate(stat, fallback=None):
        default=pos@np.array(defaults.get(stat,[0,0,0,0]))
        observed=x(f'f_ew_{stat}_90_h{half}')
        old=x('f_prior_'+stat+'_90')
        if fallback:
            observed=np.where(np.isfinite(observed),observed,x(f'f_ew_{fallback}_90_h{half}'))
            old=np.where(np.isfinite(old),old,x('f_prior_'+fallback+'_90'))
        current_exp=np.where(np.isfinite(observed),exposure,0)
        pm=np.where(np.isfinite(old),prior_m,0)
        return (np.nan_to_num(observed)*current_exp+np.nan_to_num(old)*pm+180*default)/(current_exp+pm+180)
    g=rate('expected_goals','goals_scored')
    a=rate('expected_assists','assists')+.025*(1-pos[:,0])
    attack=np.clip(filled('f_team_goals_proxy',1.4)/np.maximum(.5,filled('f_team_gf_12',1.4)),.4,2.5)
    opp=filled('f_opp_goals_proxy',1.4)
    papp=filled('f_appear_6',.5); p60=filled('f_long_6',.4)
    goalpoints=pos@np.array([6,6,5,4])+4*pos[:,0]*filled('f_keeper_ten_goal')
    cs=pos@np.array([4,4,1,0])
    dc_rate=rate('defensive_contribution')
    threshold=np.where(pos[:,1]>0,10,12)
    dc=2*filled('f_dc_rules')*(1-pos[:,0])*p60*poisson.sf(threshold-1,dc_rate*filled('f_long_minutes_6',85)/90)
    components={'appearance':papp+p60,'goals':goalpoints*g*mins/90*attack,
                'assists':3*a*mins/90*attack,'clean_sheet':cs*p60*np.exp(-opp*.92),
                'bonus':rate('bonus')*mins/90,'dc':dc,
                'saves':pos[:,0]*rate('saves')*mins/270,
                'conceded':-(pos[:,0]+pos[:,1])*opp*mins/180,
                'cards':-(rate('yellow_cards')+3*rate('red_cards'))*mins/90}
    return np.sum(list(components.values()),axis=0),papp,p60,mins,components


def augment(df):
    df=df.copy()
    pred,app,long,mins,parts=empirical(df)
    for k,v in parts.items():
        df['f_base_'+k]=v
    df['f_base_xp']=pred
    df['f_base_minutes']=mins
    return df


def scoring_target(df):
    """Train signed points with the new DC rule removed, add its forecast back.

    Otherwise a model trained before 2025 cannot infer an unseen scoring rule.
    Actual DC is used only to define the training label, never for inference.
    """
    threshold=np.where(df.pos.to_numpy()=='DEF',10,12)
    hit=(np.nan_to_num(df.y_defensive_contribution.to_numpy())>=threshold)&(df.pos.to_numpy()!='GKP')&(df.year.to_numpy()>=2025)
    return df.y_total_points.to_numpy()-2*hit


class MinutesModel:
    def __init__(self, params, seed=1729):
        self.params=params;self.seed=seed

    def fit(self, df, cols):
        self.cols=cols
        x=df[cols].to_numpy(dtype=np.float32)
        m=df.y_minutes.to_numpy()
        state=np.where(m<=0,0,np.where(m<60,1,2))
        self.classifier=lgbm(self.params,self.seed,classifier=True)
        self.classifier.fit(x,state)
        self.models={}
        for s in (1,2):
            use=state==s
            model=lgbm({**self.params,'n_estimators':min(250,self.params['n_estimators'])},self.seed)
            model.fit(x[use],m[use])
            self.models[s]=model
        return self

    def predict(self, df):
        x=df[self.cols].to_numpy(dtype=np.float32)
        p=self.classifier.predict_proba(x)
        short=np.clip(self.models[1].predict(x),1,59)
        long=np.clip(self.models[2].predict(x),60,90)
        mins=p[:,1]*short+p[:,2]*long
        return np.column_stack([1-p[:,0],p[:,2],mins,p[:,0],p[:,1],p[:,2]])


class HurdleModel:
    def __init__(self,params,seed=1729,components=False):
        self.params=params;self.seed=seed;self.components=components

    def fit(self,df,cols):
        self.cols=cols
        self.minutes=MinutesModel(self.params,self.seed).fit(df,cols)
        m=df.y_minutes.to_numpy();x=df[cols].to_numpy(dtype=np.float32)
        if not self.components:
            self.models={}
            for state,use in [(1,(m>0)&(m<60)),(2,m>=60)]:
                model=lgbm(self.params,self.seed)
                model.fit(x[use],scoring_target(df)[use])
                self.models[state]=model
        else:
            self.models={};use=m>0;exposure=m[use]/90
            for stat in ('goals_scored','assists','saves','yellow_cards','red_cards','own_goals','penalties_saved','penalties_missed'):
                target=df['y_'+stat].to_numpy()[use]
                good=np.isfinite(target)
                if np.sum(target[good])<=0:
                    self.models[stat]=0.
                    continue
                model=lgbm(self.params,self.seed,objective='poisson')
                model.fit(x[use][good],target[good]/exposure[good],sample_weight=exposure[good])
                self.models[stat]=model
            model=lgbm(self.params,self.seed,classifier=True)
            model.fit(x[m>=60],(df.y_clean_sheets.to_numpy()[m>=60]>0).astype(int))
            self.models['cs']=model
            model=lgbm(self.params,self.seed)
            # Bonus target may be zero for everyone in a small training subset;
            # LightGBM's constant output is still well-defined.
            model.fit(x[use],df.y_bonus.to_numpy()[use])
            self.models['bonus']=model
        return self

    def predict(self,df):
        x=df[self.cols].to_numpy(dtype=np.float32)
        minute=self.minutes.predict(df)
        app,p60,mins=minute[:,:3].T
        if not self.components:
            short=np.clip(self.models[1].predict(x),-3,15)
            long=np.clip(self.models[2].predict(x),-3,25)
            dc=df.f_base_dc.to_numpy()*p60/np.maximum(.05,df.f_long_6.to_numpy())
            return minute[:,4]*short+p60*long+dc,minute
        def rate(k):
            obj=self.models[k]
            return obj.predict(x) if hasattr(obj,'predict') else np.full(len(df),obj)
        pos=np.stack([df['f_pos_'+p].to_numpy() for p in ['GKP','DEF','MID','FWD']],axis=1)
        gp=pos@np.array([6,6,5,4])+4*pos[:,0]*df.f_keeper_ten_goal.to_numpy()
        cs=pos@np.array([4,4,1,0])
        longcs=self.models['cs'].predict_proba(x)[:,1]
        goals=gp*rate('goals_scored')*mins/90
        assists=3*rate('assists')*mins/90
        saves=pos[:,0]*rate('saves')*mins/270
        cards=(rate('yellow_cards')+3*rate('red_cards')+2*rate('own_goals'))*mins/90
        pens=(5*rate('penalties_saved')-2*rate('penalties_missed'))*mins/90
        # Learn provider-independent scoring counts. DC and conceded blocks are
        # explicit rules, not Poisson regression on signed total points.
        dc=df.f_base_dc.to_numpy()*p60/np.maximum(.05,df.f_long_6.to_numpy())
        conceded=(pos[:,0]+pos[:,1])*df.f_opp_goals_proxy.to_numpy()*mins/180
        score=app+p60+goals+assists+cs*p60*longcs+saves-cards+pens-conceded+app*np.clip(self.models['bonus'].predict(x),0,3)+dc
        return score,minute


def fit_candidate(candidate,train,seed=1729):
    cols=select_features(train,candidate.feature_set)
    if candidate.family in ('rolling','empirical'):
        return None,cols
    x=train[cols].to_numpy(dtype=np.float32);y=scoring_target(train)
    params=candidate.params
    if candidate.family=='ridge':
        x=x.astype(np.float64)
        model=make_pipeline(SimpleImputer(add_indicator=True,keep_empty_features=True),StandardScaler(),Ridge(**params,solver='lsqr',tol=1e-6))
    elif candidate.family=='elasticnet':
        x=x.astype(np.float64)
        model=make_pipeline(SimpleImputer(add_indicator=True,keep_empty_features=True),StandardScaler(),ElasticNet(**params,max_iter=3000,tol=.0002,selection='cyclic',random_state=seed))
    elif candidate.family in ('random_forest','extra_trees'):
        kind=RandomForestRegressor if candidate.family=='random_forest' else ExtraTreesRegressor
        model=make_pipeline(SimpleImputer(add_indicator=True,keep_empty_features=True),kind(**params,random_state=seed,n_jobs=2))
    elif candidate.family=='hist_boost':
        model=HistGradientBoostingRegressor(**params,early_stopping=False,random_state=seed)
    elif candidate.family=='lightgbm':
        model=lgbm(params,seed)
    elif candidate.family=='xgboost':
        model=XGBRegressor(**params,tree_method='hist',n_jobs=2,random_state=seed,objective='reg:squarederror')
    elif candidate.family=='catboost':
        model=CatBoostRegressor(**params,random_seed=seed,thread_count=2,verbose=False,allow_writing_files=False,loss_function='RMSE',has_time=True)
        # CatBoost does not allow infinities; it handles NaN numerical features.
    elif candidate.family in ('hurdle','poisson_components'):
        return HurdleModel(params,seed,components=candidate.family=='poisson_components').fit(train,cols),cols
    else:
        raise ValueError(candidate.family)
    model.fit(x,y)
    return model,cols


def predict_candidate(candidate,model,cols,df):
    if candidate.family=='rolling':
        pred=df[f'f_points_{candidate.params["window"]}'].to_numpy()
        return pred,None
    if candidate.family=='empirical':
        p,app,long,mins,_=empirical(df,candidate.params['half'],candidate.params['prior_strength'])
        return p,np.column_stack([app,long,mins,1-app,app-long,long])
    if candidate.family in ('hurdle','poisson_components'):
        return model.predict(df)
    return model.predict(df[cols].to_numpy(dtype=np.float32))+df.f_base_dc.to_numpy(),None
