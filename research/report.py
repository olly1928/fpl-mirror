"""Build comparison tables, an executed portable notebook and a repository report."""
from __future__ import annotations

import base64
from contextlib import redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import platform

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import nbformat
import numpy as np
import pandas as pd

from research.finalize import scalar_scores,paired_blocks
from research.experiment import dump,safe

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'research/results'
LABELS={'v1':'Live v1','learned_minutes':'Earlier learned minutes','selected_single':'Selected single model',
        'nested_ensemble':'Nested ensemble','calibrated_ensemble':'Calibrated ensemble','equal_top3':'Top-three mean',
        'rolling12':'Rolling 12','empirical_h8_p900':'Shrunk components','v1_ml_blend':'V1 + ensemble blend',
        'v1_new_minutes_raw':'V1 + boosted minutes','v1_new_minutes_calibrated':'V1 + calibrated minutes',
        'exact_components_selected':'Exact Poisson components'}


def read(name):return json.loads((OUT/name).read_text())


def markdown_table(df):
    rows=['| '+' | '.join(map(str,df.columns))+' |','| '+' | '.join(['---']*len(df.columns))+' |']
    for row in df.itertuples(index=False,name=None):
        rows.append('| '+' | '.join(f'{v:.3f}' if isinstance(v,(float,np.floating)) else str(v) for v in row)+' |')
    return '\n'.join(rows)


def consolidate():
    modern=pd.read_csv(OUT/'modern-matched-predictions.csv.gz')
    hybrid=pd.read_csv(OUT/'v1-new-minutes-historical.csv.gz')
    h=hybrid.pivot(index=['season','gw','player_id'],columns='variant',values='xp').reset_index()
    h=h.rename(columns={'raw':'v1_new_minutes_raw','calibrated':'v1_new_minutes_calibrated'})
    modern=modern.merge(h,on=['season','gw','player_id'],validate='one_to_one')
    exact=pd.read_csv(OUT/'exact-component-predictions.csv.gz')
    names=['poisson_components_01','poisson_components_02']
    ex=exact.groupby(['season','gw','player_id'],sort=False)[names].sum().reset_index()
    modern=modern.merge(ex,on=['season','gw','player_id'],validate='one_to_one')
    blend=read('historical-assessment.json')['v1_ml_blends']
    for record in blend:
        use=modern.season==record['season']
        modern.loc[use,'v1_ml_blend']=(1-record['ml_weight'])*modern.loc[use,'v1']+record['ml_weight']*modern.loc[use,'nested_ensemble']
    exact_selection={}
    for season,a in modern.groupby('season',sort=True):
        past=modern[modern.season<season]
        scores={}
        for name in names:
            values=[np.mean((p[name]-p.y_total_points)**2) for _,p in past.groupby('season')]
            scores[name]=float(np.mean(values)) if values else (0. if name==names[0] else 1.)
        name=min(scores,key=scores.get);exact_selection[season]={'selected':name,'scores':scores,'training_rows':len(past)}
        modern.loc[a.index,'exact_components_selected']=a[name]
    past_scores={name:float(np.mean([np.mean((p[name]-p.y_total_points)**2) for _,p in modern.groupby('season')])) for name in names}
    exact_current=min(past_scores,key=past_scores.get)
    current=pd.read_csv(OUT/'current-selected-predictions.csv.gz')
    probability_rows={}
    for variant in ('raw','calibrated'):
        p=pd.read_csv(OUT/f'v1-new-minutes-current-{variant}.csv')
        name='v1_new_minutes_'+variant
        probability_rows[name]=p
        current=current.merge(p[['gw','player_id','xp']].rename(columns={'xp':name}),on=['gw','player_id'],validate='one_to_one')
    current=current.merge(ex[ex.season=='2026-27'][['gw','player_id',exact_current]].rename(columns={exact_current:'exact_components_selected'}),on=['gw','player_id'],validate='one_to_one')
    models=[m for m in LABELS if m in modern and m in current]
    scores=[]
    for season,a in modern.groupby('season',sort=True):
        for model in models:
            score=scalar_scores(a.y_total_points.to_numpy(),a[model].to_numpy())
            baseline=scalar_scores(a.y_total_points.to_numpy(),a.v1.to_numpy())['rmse']
            scores.append({'period':season,'pool':'matched_modern','model':model,'label':LABELS[model],
                           'improvement_vs_v1_percent':(baseline-score['rmse'])/baseline*100,**score})
    for pool,use in [('fixed_candidates',current.likely_player==1),('owned15',current.owned==1),('all',np.ones(len(current),dtype=bool))]:
        a=current[use]
        baseline=scalar_scores(a.y_total_points.to_numpy(),a.v1.to_numpy())['rmse']
        for model in [m for m in LABELS if m in current]:
            score=scalar_scores(a.y_total_points.to_numpy(),a[model].to_numpy())
            scores.append({'period':'2026-27_GW1-7','pool':pool,'model':model,'label':LABELS[model],
                           'improvement_vs_v1_percent':(baseline-score['rmse'])/baseline*100,**score})
    table=pd.DataFrame(scores);table.to_csv(OUT/'model-comparison.csv',index=False)
    eligible=models
    historical_rank={m:float(np.mean([np.mean((a[m]-a.y_total_points)**2) for _,a in modern.groupby('season')])) for m in eligible}
    chosen=min(historical_rank,key=historical_rank.get)
    uncertainty=[]
    for model in eligible[1:]:
        uncertainty.append({'pool':'matched_modern','model':model,**paired_blocks(modern,'v1',model)})
    for model in ['nested_ensemble','v1_new_minutes_raw','v1_new_minutes_calibrated',chosen]:
        uncertainty.append({'pool':'current_fixed_candidates','model':model,**paired_blocks(current[current.likely_player==1],'v1',model)})
    dump(OUT/'extended-comparison.json',safe({'historical_procedure_choice':chosen,
        'choice_uses_current_outcomes':False,'historical_macro_mse':historical_rank,
        'exact_component_selection':exact_selection,'exact_component_current':exact_current,
        'exact_component_current_selection_scores':past_scores,'uncertainty':uncertainty}))
    modern.to_csv(OUT/'extended-modern-predictions.csv.gz',index=False,compression={'method':'gzip','mtime':0})
    current.to_csv(OUT/'extended-current-predictions.csv.gz',index=False,compression={'method':'gzip','mtime':0})
    return modern,current,table,chosen


def family_and_ablations():
    rows=[]
    for r in read('family-scores.json'):
        rows.append({'year':r['year'],'family':r['family'],'selected':r['selected'],**r['active']})
    families=pd.DataFrame(rows);families.to_csv(OUT/'family-comparison.csv',index=False)
    records=[]
    for year in range(2016,2026):
        for i in range(13,19):
            path=OUT/f'folds/fold-{year}/one-week/lightgbm_{i:02d}-seed1729.json'
            r=json.loads(path.read_text())
            records.append({'year':year,'model':f'lightgbm_{i:02d}',**r['metrics']})
    labels={'lightgbm_13':'Remove Understat','lightgbm_14':'No Understat / explicit team columns*',
            'lightgbm_15':'Remove price','lightgbm_16':'Compact feature set',
            'lightgbm_17':'Full features, last 3 seasons','lightgbm_18':'Full features, last 5 seasons'}
    rows=[{'year':r['year'],'model':r['model'],'label':labels[r['model']],**r['active']} for r in records]
    pd.DataFrame(rows).to_csv(OUT/'ablation-comparison.csv',index=False)


def notebook(table,chosen):
    nb=nbformat.v4.new_notebook()
    nb.metadata.update(kernelspec={'display_name':'Python 3','language':'python','name':'python3'},
                       language_info={'name':'python','version':platform.python_version()},
                       execution={'engine':'python-in-process','top_to_bottom':True,
                                  'reason':'Deterministic socket-free execution of plain Python cells; normal Jupyter kernel execution is not claimed.'})
    modern=table[table.pool=='matched_modern']
    gains=modern[modern.model==chosen].improvement_vs_v1_percent
    current=table[(table.pool=='fixed_candidates')&(table.model==chosen)].iloc[0]
    summary=(f'# FPL projection model comparison\n\n## tl;dr\n\n'
             f'The historically preferred procedure is **{LABELS[chosen]}**, chosen from the three recent complete seasons without current-season outcomes. '
             f'Its annual RMSE changes against v1 range from **{gains.min():+.2f}% to {gains.max():+.2f}%** improvement; '
             f'the seven-week current comparison is **{current.improvement_vs_v1_percent:+.2f}%**. Positive means lower RMSE. '
             'This is retrospective evidence, not proof of a perfect model or extra team points. The live advisor is unchanged.')
    cells=[nbformat.v4.new_markdown_cell(summary),nbformat.v4.new_markdown_cell(
        '## Context & Methods\n\n79 declared one-week configurations (12 points families plus minutes variants), ten OOF seasons, '
        'eight nested outer seasons, and a separate six-gameweek experiment. Family selection, ensemble weights and calibration use earlier OOF predictions only. '
        'Structural scoring/minutes refinements are separately identified. Source ablations have attribution limitations documented in REPORT.md.\n\n'
        '### Key Assumptions\n\nHistorical fixtures and positions are retrospectively reconstructed; complete deadline rosters/news are unavailable. '
        'All these outcomes had already been examined, so none is described as an untouched external holdout. '
        'Current previous-season statistics were captured later and correct six frozen-prior discrepancies. '
        'Current player observations are the same pinned snapshot/outcome intersection for every method. '
        'The Understat match archive ends in April 2025. Six-week windows require known future target coverage and use realised schedules.\n\n'
        'The notebook loads the committed prediction-derived tables. Run the workflows or `research/run.sh` to reproduce training from raw captures.'),
        nbformat.v4.new_code_cell("from pathlib import Path\nimport json\nimport pandas as pd\nimport numpy as np\nimport matplotlib.pyplot as plt\nbase=Path('research/results')\nif not base.exists(): base=Path('results')\nplt.rcParams.update({'font.size':11,'axes.spines.top':False,'axes.spines.right':False,'figure.figsize':(10,5),'axes.grid':True,'grid.alpha':.18})\ncolors={'v1':'#50545b','nested_ensemble':'#2764a5','v1_new_minutes_raw':'#c47a28','v1_new_minutes_calibrated':'#687c31','exact_components_selected':'#a85483'}\nlabels="+repr(LABELS)+"\ncomparison=pd.read_csv(base/'model-comparison.csv')\nprint('Research only; live model unchanged. Positive improvement = lower RMSE.')"),
        nbformat.v4.new_markdown_cell('## Data\n\n### 1. Verify the population\n\nThe core has 12 complete player-fixture seasons plus partial 2013/14 warm-up. '
            'Missing xG/provider data remains missing. Conflicted early SQLite targets are quarantined. Counts below are fixture rows; primary points scores aggregate DGWs to player-gameweek.'),
        nbformat.v4.new_code_cell("quality=json.loads((base/'data-quality.json').read_text())\nprint(pd.DataFrame([{k:v for k,v in quality[p].items() if k in ['rows','feature_count','duplicate_player_fixtures','asof_violations','unknown_points_or_minutes','understat_recent_365_rows']} for p in ['historical','current']],index=['Historical','Current']).to_string())"),
        nbformat.v4.new_markdown_cell('## Results\n\n### 2. Compare model families across eight outer seasons\n\nEach family’s configuration is chosen using preceding OOF seasons. '
            'The heatmap shows RMSE improvement against a common rolling-12 baseline on the lag-defined active pool, including DGWs.'),
        nbformat.v4.new_code_cell("families=pd.read_csv(base/'family-comparison.csv')\nraw=json.loads((base/'nested-scores.json').read_text())\nrolling={r['year']:r['active']['rmse'] for r in raw if r['model']=='rolling12'}\nfamilies['gain']=families.apply(lambda r:100*(rolling[r['year']]-r['rmse'])/rolling[r['year']],axis=1)\npivot=families.pivot(index='family',columns='year',values='gain')\npivot=pivot.loc[pivot.mean(axis=1).sort_values(ascending=False).index]\nfrom matplotlib.colors import LinearSegmentedColormap,TwoSlopeNorm\ncmap=LinearSegmentedColormap.from_list('gain',['#f0cf9e','#f6f6f3','#9dc3df'])\nlimit=max(1,float(np.abs(pivot.to_numpy()).max()))\nfig,ax=plt.subplots(figsize=(10.5,6.5))\nimage=ax.imshow(pivot.to_numpy(),aspect='auto',cmap=cmap,norm=TwoSlopeNorm(vmin=-limit,vcenter=0,vmax=limit))\nax.set_xticks(range(len(pivot.columns)),[f'{y%100:02d}/{(y+1)%100:02d}' for y in pivot.columns])\nax.set_yticks(range(len(pivot.index)),pivot.index)\nfor i in range(len(pivot)):\n for j in range(len(pivot.columns)):\n  ax.text(j,i,f'{pivot.iloc[i,j]:+.1f}',ha='center',va='center',fontsize=9,color='black')\nax.grid(False);ax.set_title('Points RMSE improvement against rolling 12 (%)')\nfig.colorbar(image,ax=ax,label='Improvement (%)');fig.tight_layout()\nplt.show()"),
        nbformat.v4.new_markdown_cell('### 3. Compare with the existing v1 on identical recent cases\n\nAll methods use exactly the existing v1 evaluation cases. '
            'These exclude DGWs and first-observed rows because the old benchmark did. The broad outer tests above include them.'),
        nbformat.v4.new_code_cell("recent=comparison[comparison.pool=='matched_modern']\nmethods=['nested_ensemble','v1_new_minutes_raw','v1_new_minutes_calibrated','exact_components_selected']\npivot=recent[recent.model.isin(methods)].pivot(index='period',columns='model',values='improvement_vs_v1_percent')[methods]\nfig,ax=plt.subplots(figsize=(10.5,5))\nx=np.arange(len(pivot));width=.18\nfor j,m in enumerate(methods): ax.bar(x+(j-1.5)*width,pivot[m],width,label=labels[m],color=colors[m])\nax.axhline(0,color='#25282d',linewidth=1);ax.set_xticks(x,pivot.index)\nax.set_ylabel('RMSE improvement against v1 (%)');ax.set_title('Matched player-gameweek cases, 2023/24–2025/26')\nax.legend(ncol=2,fontsize=9);fig.tight_layout();plt.show()\nprint(recent[recent.model.isin(['v1']+methods)].pivot(index='label',columns='period',values='rmse').round(4).to_string())"),
        nbformat.v4.new_markdown_cell('### 4. Inspect the seven current gameweeks\n\nThe fixed v1 candidate pool contains 1,205 player-gameweek predictions; owned squads contain 105. '
            'These seven outcomes did not fit or select a configuration. They were already known when the research was designed, and provide limited independent week-level evidence.'),
        nbformat.v4.new_code_cell("now=comparison[(comparison.pool=='fixed_candidates')&(comparison.model.isin(['v1','nested_ensemble','v1_new_minutes_raw','v1_new_minutes_calibrated','exact_components_selected']))]\nprint(now[['label','n','rmse','mae','bias_forecast_minus_actual','improvement_vs_v1_percent']].round(4).to_string(index=False))\ncurrent=pd.read_csv(base/'extended-current-predictions.csv.gz');active=current[current.likely_player==1]\nfig,ax=plt.subplots(figsize=(8,5))\nfor m in ['v1','nested_ensemble','v1_new_minutes_raw','v1_new_minutes_calibrated']:\n tmp=active.assign(band=pd.cut(active[m],[-np.inf,1,2,3,4,5,6,8,np.inf]))\n bins=tmp.groupby('band',observed=True).agg(pred=(m,'mean'),actual=('y_total_points','mean'),n=('gw','size'))\n bins=bins[bins.n>=10];ax.plot(bins.pred,bins.actual,marker='o',label=labels[m],color=colors[m])\nax.plot([0,9],[0,9],'--',color='#555555',label='Ideal mean calibration')\nax.set(xlim=(0,9),ylim=(0,9),xlabel='Mean predicted points',ylabel='Mean actual points',title='Current points calibration: bins with at least 10 observations')\nax.legend(fontsize=9);fig.tight_layout();plt.show()"),
        nbformat.v4.new_markdown_cell('### 5. Inspect appearance and ≥60-minute probabilities\n\nDisjoint state probabilities are calibrated on prior OOF cases, then captured availability flags are applied. '
            'A good average probability does not by itself establish good individual forecasts.'),
        nbformat.v4.new_code_cell("fig,ax=plt.subplots(figsize=(8,5))\nframes={'v1':pd.read_csv(base/'v1-new-minutes-current-raw.csv'),'v1_new_minutes_raw':pd.read_csv(base/'v1-new-minutes-current-raw.csv'),'v1_new_minutes_calibrated':pd.read_csv(base/'v1-new-minutes-current-calibrated.csv')}\nbase_pred=pd.read_csv(base/'extended-current-predictions.csv.gz')\nframes['v1']=base_pred.copy();frames['v1']['p60']=frames['v1']['v1_p60'];frames['v1']['actual_minutes']=frames['v1']['y_minutes']\nfor name,f in frames.items():\n f=f[f.likely_player==1].copy();f['actual_long']=(f.actual_minutes>=60).astype(int)\n f['band']=pd.cut(f.p60,np.linspace(0,1.000001,6),include_lowest=True)\n b=f.groupby('band',observed=True).agg(pred=('p60','mean'),actual=('actual_long','mean'),n=('gw','size'))\n ax.plot(b.pred,b.actual,marker='o',label=labels[name],color=colors[name])\nax.plot([0,1],[0,1],'--',color='#555555');ax.set(xlim=(0,1),ylim=(0,1),xlabel='Predicted probability',ylabel='Observed fraction reaching 60 minutes',title='Current ≥60-minute reliability, fixed candidate pool')\nax.legend(fontsize=9);fig.tight_layout();plt.show()"),
        nbformat.v4.new_markdown_cell('### 6. Inspect conditional squad decisions\n\nThese are legal XI/captain/bench choices on the actual owned 15 with actual autosubs. '
            'They are not a transfer, wildcard or season-rank backtest.'),
        nbformat.v4.new_code_cell("lineups=json.loads((base/'lineup-assessment.json').read_text())\nhybrids=json.loads((base/'v1-new-minutes-current.json').read_text())\ntotals={m:v for m,v in lineups['totals'].items() if m in ['v1','nested_ensemble']}\nfor key,v in hybrids.items():totals['v1_new_minutes_'+key]=v['conditional_squad_score']\nactual=lineups['actual_points']\nprint(pd.DataFrame([{'model':labels[k],'points':v,'difference_from_actual':v-actual} for k,v in totals.items()]).to_string(index=False))\nfig,ax=plt.subplots(figsize=(9,4))\nnames=list(totals);delta=[totals[n]-actual for n in names]\nax.barh([labels[n] for n in names],delta,color=[colors[n] for n in names]);ax.axvline(0,color='#333333')\nax.set_xlabel('Points above actual seven-week total');ax.set_title(f'Conditional lineup choices; actual total {actual:g}')\nfig.tight_layout();plt.show()"),
        nbformat.v4.new_markdown_cell('### 7. Test six-gameweek forecasts\n\nAll player features are frozen at the origin. Targets that cross season boundaries or have unknown future coverage are excluded. '
            'Realised fixture counts are an oracle schedule assumption. The uncertainty procedure uses eight-GW blocks because six-week labels overlap.'),
        nbformat.v4.new_code_cell("six=json.loads((base/'six-week-assessment.json').read_text())\ns=pd.DataFrame(six['scores']);p=s[s.year<2026].pivot(index='year',columns='model',values='rmse')\ngain=(p.rolling_six-p.selected_six)/p.rolling_six*100\nfig,ax=plt.subplots(figsize=(9,4.5));ax.plot(gain.index,gain,marker='o',color='#2764a5')\nax.axhline(0,color='#444444');ax.set_xticks(gain.index,[f'{v%100:02d}/{(v+1)%100:02d}' for v in gain.index])\nax.set_ylabel('RMSE improvement against rolling six (%)');ax.set_title('Six-gameweek diagnostic on known-coverage windows');fig.tight_layout();plt.show()\nprint(json.dumps(six['uncertainty'],indent=2))"),
        nbformat.v4.new_markdown_cell('## Takeaways\n\nChoose a procedure from historical evidence and confirm it prospectively. '
            'Assess RMSE, MAE, probability calibration, cold starts and decisions together. '
            'Keep sourced role/news overrides and preserve the Claude playbook entry points. '
            'Research artifacts are not a drop-in production replacement. See REPORT.md for comparisons, uncertainty and the promotion recommendation.')]
    nb.cells=cells
    namespace={};counter=0;figures=[]
    (OUT/'figures').mkdir(exist_ok=True)
    original=Path.cwd()
    import os
    os.chdir(ROOT)
    try:
        for cell in nb.cells:
            if cell.cell_type!='code':continue
            counter+=1;stream=io.StringIO()
            with redirect_stdout(stream):exec(compile(cell.source,f'comparison.ipynb cell {counter}','exec'),namespace)
            cell.execution_count=counter;cell.outputs=[]
            if stream.getvalue():cell.outputs.append(nbformat.v4.new_output('stream',name='stdout',text=stream.getvalue()))
            for number in plt.get_fignums():
                fig=plt.figure(number);buffer=io.BytesIO();fig.savefig(buffer,format='png',dpi=140,bbox_inches='tight')
                data=buffer.getvalue();path=OUT/'figures'/f'comparison-{len(figures)+1:02d}.png';path.write_bytes(data);figures.append(path.name)
                cell.outputs.append(nbformat.v4.new_output('display_data',data={'image/png':base64.b64encode(data).decode(),'text/plain':'Verified analytical figure'},metadata={}))
                plt.close(fig)
    finally:os.chdir(original)
    nbformat.validate(nb)
    nbformat.write(nb,ROOT/'research/comparison.ipynb')
    dump(OUT/'notebook-execution.json',{'code_cells':counter,'figures':figures,'errors':0,'engine':'python-in-process',
         'top_to_bottom':True,'normal_jupyter_kernel_execution_claimed':False})


def report(table,chosen):
    historical=table[table.pool=='matched_modern']
    current=table[(table.pool=='fixed_candidates')&(table.model==chosen)].iloc[0]
    recent=historical[historical.model==chosen]
    main=table[(table.pool=='fixed_candidates')&table.model.isin(['v1','learned_minutes','nested_ensemble','v1_new_minutes_raw','v1_new_minutes_calibrated','exact_components_selected'])]
    main=main[['label','n','rmse','mae','improvement_vs_v1_percent']].rename(columns={'label':'Procedure','n':'Cases','rmse':'RMSE','mae':'MAE','improvement_vs_v1_percent':'RMSE improvement %'})
    methods=historical[historical.model.isin(['v1','nested_ensemble','v1_new_minutes_raw','v1_new_minutes_calibrated','exact_components_selected'])].pivot(index='label',columns='period',values='rmse').reset_index()
    ext=read('extended-comparison.json');uncertainty=[r for r in ext['uncertainty'] if r['pool']=='matched_modern' and r['model']==chosen]
    lineups=read('lineup-assessment.json');hybrids=read('v1-new-minutes-current.json')
    totals={'Live v1':lineups['totals']['v1'],'Nested ensemble':lineups['totals']['nested_ensemble'],
            'V1 + boosted minutes':hybrids['raw']['conditional_squad_score'],
            'V1 + calibrated minutes':hybrids['calibrated']['conditional_squad_score']}
    families=pd.read_csv(OUT/'family-comparison.csv')
    family=families.groupby('family').agg(macro_mse=('mse','mean'),worst_rmse=('rmse','max')).reset_index()
    family['macro_rmse']=np.sqrt(family.macro_mse);family=family.sort_values('macro_rmse')
    coverage=read('data-quality.json')
    ci=uncertainty[0] if uncertainty else None
    ci_text=(f"The pooled matched-case RMSE improvement is {ci['rmse_improvement_percent']:.2f}%, with a descriptive 95% four-GW-block interval of [{ci['interval_95_percent'][0]:.2f}%, {ci['interval_95_percent'][1]:.2f}%]." if ci else '')
    source=read('workflow-run.json')
    text=f'''# FPL projection model research

The lowest recent historical error among the evaluated procedures came from **{LABELS[chosen]}**. The choice uses the three complete modern seasons, without the seven current outcomes. Its annual RMSE improvement against v1 ranges from **{recent.improvement_vs_v1_percent.min():+.2f}% to {recent.improvement_vs_v1_percent.max():+.2f}%**; the current fixed-pool comparison is **{current.improvement_vs_v1_percent:+.2f}%**. A positive value means lower error. These results do not establish perfect calibration or guaranteed extra team points. **No production model or playbook has been changed.**

## What was tested

The original broad run evaluated **67 points configurations across 12 families, plus 12 minutes configurations, over ten chronological folds: 790 candidate/fold evaluations**. Eight outer seasons (2018/19–2025/26) use only preceding OOF seasons for configuration/family selection, nonnegative ensemble weights and calibration. The first two seasons supply selection history. The library covers rolling/shrinkage baselines, ridge, elastic net, random forest, extra trees, histogram boosting, LightGBM, XGBoost, CatBoost, minute hurdles and Poisson scoring components.

Separate refinements test exact Poisson save/conceded blocks, replacing only v1 minutes, and calibrating the disjoint minute-state probabilities. Original source ablations are retained as diagnostic configurations, with two attribution limitations: there is no identical-parameter full-feature anchor, and removing explicit team columns leaves derived team-context components. They cannot establish an isolated causal benefit from an individual source. No further model search was added while recovering the report. The six-week experiment tests eight additional configurations, with labels purged at season boundaries. The 2025 outer ensemble is repeated across three seeds without selecting a seed.

The feature population contains **{coverage['historical']['rows']:,} historical player-fixture rows**, including 12 complete seasons and partial 2013/14 warm-up, and **{coverage['current']['rows']:,} current snapshot/outcome intersections**. There are zero duplicate player-fixtures, unknown point/minute targets or as-of boundary violations in that population. Primary points metrics sum DGWs to player-gameweek. The fixed current v1 pool contains 1,205 observations and the owned-squad pool 105. The larger raw outcome capture contains later registrations absent from earlier snapshots; these are not silently added to a deadline forecast.

## Comparison with the existing model

The following table uses exactly the existing v1 modern benchmark cases. It inherits that old benchmark's exclusions of DGWs and first-observed rows; the broader eight-season tests include those cases.

{markdown_table(methods)}

{ci_text} Intervals are conditional on this retrospective protocol and available archive population, and do not eliminate method-search or future-regime uncertainty. See [extended-comparison.json](results/extended-comparison.json) for all paired comparisons and [model-comparison.csv](results/model-comparison.csv) for MAE, bias and calibration slope/intercept as well as RMSE.

Current seven-week results on the same fixed candidate pool:

{markdown_table(main)}

The seven weeks are too few for a precise independent validation. They were already known during research design, although they did not fit weights, calibrators or hyperparameters. Current previous-season inputs are later-captured completed outcomes and include corrections to six frozen-v1 priors; none of those six players was owned in these weeks. This is not a pristine archived complete feature vintage.

## Decisions and probabilities

The actual team scored **{lineups['actual_points']:g}** points. Numerical policies on the actual owned 15, with legal formations, captain/vice and actual autosubs, scored:

{markdown_table(pd.DataFrame([{'Procedure':k,'Conditional points':v} for k,v in totals.items()]))}

This compares XI/captain/bench decisions conditional on the actual squads. It does not simulate transfers, hits, chips, changing budgets or season rank, and does not reconstruct missing Claude news overrides. All weekly selections are retained in the lineup JSON reports. Probability Brier scores, log loss and mean predicted/observed ≥60 frequencies are in [probability-calibration.json](results/probability-calibration.json). A correct aggregate mean alone is insufficient to establish good individual forecasts.

## Full family comparison

Each family selects its own configuration from preceding seasons. The macro RMSE below is the square root of mean seasonal MSE across eight outer seasons, with equal seasonal influence; the worst seasonal RMSE keeps failures visible.

{markdown_table(family[['family','macro_rmse','worst_rmse']])}

The notebook includes the full season-by-family heatmap, modern/current comparisons, points and minute calibration, conditional decisions and the six-week experiment. Raw and calibrated variants, source/recency controls, cold starts, positions and DGWs are retained rather than reporting only a winning aggregate.

Some convergence warnings were emitted in the broad parallel fits. Python warning capture is shared across threads, so warning text may be associated with a concurrent trial rather than its emitting estimator; the retained per-trial warning attribution is not definitive. Trials were not removed on this basis. Saved forecasts and trial metrics are independently reconciled by [independent-verification.json](results/independent-verification.json).

## Limits and recommendation

Use the historical choice as a **research candidate**, and review the complete set of accuracy and probability metrics before promotion. Preserve the existing Claude playbook URLs, bash/curl routing, sourced news/role overrides and decision provenance. The research minute hybrid currently rejects manual overrides, so it is not a production drop-in. Confirm a versioned candidate at future unseen deadlines before claiming a reliable edge; do not automatically switch the live team advisor on the basis of these results.

Historical fixtures, registrations, prices and position labels are not complete pre-deadline vintages. Fixture assignments are realised schedules; historical odds without provable deadline timestamps and post-match archive xP are excluded. Provider statistics stay missing when unobserved. Understat's match archive ends **2025-04-07**: none of the current rows has one-year Understat coverage, and only {coverage['current']['understat_recent_730_rows']:,} has two-year coverage. Source diagnostics therefore do not establish the benefit of a current fresh feed. Very old annual/team records are not falsely converted into per-GW player labels; conflicted early SQLite targets remain quarantined. Fourteen historical zero-minute/nonzero-point records remain in the signed points targets; the basic hurdle approximation does not model their rare bench-card mechanism explicitly.

Six-week labels overlap, require known future coverage and use realised fixture counts. Eight-GW block intervals address overlapping origins descriptively; the two current six-week windows are heavily overlapping and cannot supply strong independent validation. Source exclusions and coverage counts are retained in [six-week-assessment.json](results/six-week-assessment.json).

**Readiness: share with these caveats; research evidence is reviewable, production promotion remains separate.**

## Reproduce and inspect

Use Python 3.12 and `research/requirements.txt` in an isolated environment. `research/run.sh` reproduces the pipeline; the one-off GitHub workflows parallelise independent folds and have no schedule. [comparison.ipynb](comparison.ipynb) is an executed, portable companion with saved outputs. Its plain Python cells were executed top-to-bottom in a socket-free in-process engine, explicitly recorded in metadata; normal Jupyter kernel execution is not claimed. Training results and models live only under `research/`; production retains its stdlib-only gate and separate acceptance tests.

Raw inputs and immutable source hashes are under `history/extended` and the feature manifests. The broad fit run is [GitHub Actions {source['run_id']}](https://github.com/olly1928/fpl-mirror/actions/runs/{source['run_id']}), from commit `{source['source_commit']}`. Every declared fold's parameters, training seasons, runtime, warnings and forecast bytes are retained under [results/folds](results/folds). [artifacts/manifest.json](artifacts/manifest.json) records fitted finalist checksums. The model serialisations are research artifacts, not instructions to execute an unreviewed production rollout.

Method references: [scikit-learn data leakage guidance](https://scikit-learn.org/stable/common_pitfalls.html#data-leakage) and [LightGBM's documented objectives and deterministic settings](https://lightgbm.readthedocs.io/en/v4.6.0/Parameters.html).
'''
    (ROOT/'research/REPORT.md').write_text(text)
    dump(OUT/'report-summary.json',safe({'historical_choice':chosen,'label':LABELS[chosen],
        'annual_improvements_percent':recent[['period','improvement_vs_v1_percent']].to_dict('records'),
        'current_fixed_pool':current.to_dict(),'conditional_points':totals,'actual_points':lineups['actual_points'],
        'live_changed':False,'fresh_untouched_holdout':False}))


def main():
    modern,current,table,chosen=consolidate()
    family_and_ablations();notebook(table,chosen);report(table,chosen)
    # No report may silently turn an unknown or missing forecast into zero.
    if table[['rmse','mae']].isna().any().any():raise ValueError('Missing comparison metric')
    dump(OUT/'report-validation.json',{'prediction_tables_recomputed':True,'same_case_pools':True,
         'notebook_executed':True,'live_model_changed':False,'status':'Share with caveats'})


if __name__=='__main__':main()
