"""Rebuild the report's three figures from public aggregate evidence only.

Usage: python build_figures.py
Requires matplotlib and numpy; no model, research database, API, or account.
"""
from pathlib import Path
import json
import time
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np

START=time.perf_counter()
ROOT=Path(__file__).resolve().parent
DATA=json.loads((ROOT/'aggregates.json').read_text())
OUT=ROOT/'figures';OUT.mkdir(exist_ok=True)
COLORS={'frozen_v1':'#334155','corrected_data_v1':'#8491A3','selected_final':'#235C96',
        'selected_final_availability_diagnostic':'#B6802E','portable':'#B6802E'}
plt.rcParams.update({'font.family':'DejaVu Sans','font.size':11,'axes.titlesize':13,
    'axes.labelsize':11,'figure.facecolor':'white','axes.facecolor':'white',
    'axes.spines.top':False,'axes.spines.right':False,'axes.edgecolor':'#8290A0',
    'text.color':'#182534','axes.labelcolor':'#182534','xtick.color':'#455467',
    'ytick.color':'#455467','svg.fonttype':'none','savefig.facecolor':'white'})

def save(fig,name):
    fig.savefig(OUT/(name+'.png'),dpi=190,bbox_inches='tight')
    fig.savefig(OUT/(name+'.svg'),bbox_inches='tight')
    plt.close(fig)

def signed(value):
    return '0.00' if value == 0 else f'{value:+.2f}'

variants=['frozen_v1','corrected_data_v1','selected_final','selected_final_availability_diagnostic']
labels=['Existing advisor','Corrected-data advisor','Final selected blend','Blend + status diagnostic']
fig,axes=plt.subplots(1,3,figsize=(13.5,4.8),sharex=True,sharey=True)
for ax,pool,title in zip(axes,['full','likely','owned'],['Full roster · 4,517 rows','Likely players · 1,205 rows','Owned players · 105 rows']):
    values=[DATA['current']['scores'][v][pool]['rmse'] for v in variants]
    y=np.arange(4)
    ax.barh(y,values,height=.58,color=[COLORS[v] for v in variants])
    for row,val in zip(y,values):ax.text(val+.04,row,f'{val:.3f}',va='center',fontsize=11)
    ax.set(xlim=(0,3.75),yticks=y,yticklabels=labels,xlabel='Point RMSE · lower is better')
    ax.set_title(title,pad=15);ax.xaxis.grid(True,color='#E4E9EE');ax.set_axisbelow(True)
axes[0].invert_yaxis()
fig.suptitle('The final blend has higher error than the advisor in the matched current replay',fontsize=15,x=.04,ha='left',y=1.01)
fig.subplots_adjust(left=.19,right=.98,bottom=.22,wspace=.13)
fig.text(.04,.055,'2026/27 GW1–7, already exposed. Identical roster, targets and pool masks for every method.\nFull actual FPL points; candidate totals use historical assist/bonus and defensive-prior approximations.',fontsize=10)
save(fig,'current-matched-error')

rows=[r for r in DATA['historical'] if r['season'] not in DATA['study']['screen_seasons']]
x=np.arange(len(rows));width=.34
fig,ax=plt.subplots(figsize=(10.8,4.8))
for offset,key,label,color in [(-width/2,'selected','Chronologically selected procedure','#235C96'),(width/2,'portable','Protected portable procedure','#B6802E')]:
    values=[100*(1-r['arms'][r['selected_arm'] if key=='selected' else 'portable']['balanced_relative_mse']) for r in rows]
    bars=ax.bar(x+offset,values,width=width,color=color,label=label)
    for bar,val in zip(bars,values):ax.text(bar.get_x()+bar.get_width()/2,val+.13,f'{val:.2f}%',ha='center',fontsize=10)
ax.set_xticks(x,[r['season'].replace('-','/')+'\n'+('Exposed confirmation' if r['season']=='2025-26' else 'Outer evaluation') for r in rows])
ax.set(ylim=(0,7.3),ylabel='Balanced one-GW MSE reduction vs rolling20 (%)')
ax.yaxis.grid(True,color='#E4E9EE');ax.set_axisbelow(True)
ax.set_title('Historical selection matched the protected portable model in every outer season',loc='left',pad=18)
ax.legend(loc='upper left',frameon=False,ncol=2,fontsize=10)
fig.subplots_adjust(bottom=.29,left=.09,right=.98)
fig.text(.09,.045,'Four outer seasons: the earlier-data selection chose portable, so the bars are equal by construction.\nOnly the exposed 2025/26 check uses the final 50:50 direct/football blend. Supported score excludes defensive awards.',fontsize=10)
save(fig,'historical-selection')

names=['Outer 2021/22–2024/25\nvs rolling20','Outer 2021/22–2024/25\nvs portable',
       'Exposed 2025/26\nvs rolling20','Exposed 2025/26\nvs portable']
keys=[('outer','rolling20'),('outer','portable'),('exposed_confirmation','rolling20'),('exposed_confirmation','portable')]
fig,axes=plt.subplots(1,2,figsize=(13.2,5.6),sharey=True)
for ax,kind,title,xlabel in zip(axes,['paired','decision'],['Six-GW forecast accuracy','Six-GW squad decisions'],
    ['MSE reduction (%) · higher is better','Supported-point difference per six-GW plan · higher is better']):
    for i,(period,baseline) in enumerate(keys):
        value=DATA['horizon_intervals'][period+'_six_week_'+kind+'_uncertainty'][baseline]
        if kind=='paired':point=-100*value['point_estimate'];lo,hi=[-100*v for v in value['interval95'][::-1]]
        else:point=value['point_estimate'];lo,hi=value['interval95']
        color='#235C96' if period=='outer' else '#B6802E'
        ax.errorbar(point,i,xerr=[[point-lo],[hi-point]],fmt='o',color=color,capsize=4,markersize=7,lw=1.6)
        ax.text(point,i+.22,f'{signed(point)} [{signed(lo)}, {signed(hi)}]',ha='center',va='top',fontsize=9)
    ax.axvline(0,color='#455467',lw=1,linestyle='--')
    ax.set(yticks=np.arange(4),yticklabels=names,xlabel=xlabel,ylim=(3.65,-.55));ax.set_title(title,pad=16)
    ax.xaxis.grid(True,color='#E4E9EE');ax.set_axisbelow(True)
axes[0].set_xlim(-3.5,20);axes[1].set_xlim(-13,6)
fig.suptitle('Lower forecast error did not establish better squad decisions',fontsize=15,x=.03,ha='left',y=1.015)
fig.subplots_adjust(left=.19,right=.98,bottom=.25,wspace=.21)
fig.text(.03,.055,'Dots and 95% descriptive paired calendar-block intervals; 2,000 resamples in six-GW blocks, equal season weight.\nIntervals are conditional on the finite search and reconstructed cohorts, not prospective or selection-adjusted.\nOuter procedure equals portable; confirmation uses the final blend. Scores condition on complete future outcomes and fixed legal squads.',fontsize=10)
save(fig,'six-week-uncertainty')
print(json.dumps({'seconds':time.perf_counter()-START,'figures':3,'formats':['png','svg'],
    'matplotlib_version':matplotlib.__version__,'numpy_version':np.__version__}))
