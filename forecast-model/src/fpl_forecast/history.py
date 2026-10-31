"""Read bounded hashed partitions in their exact original historical row order."""
import numpy as np
import pandas as pd
from .utils import bound_file,read_json


def read_history(root,index_path,index_sha256):
    index=read_json(bound_file(root,index_path,index_sha256));pieces=[];seen=set()
    if index.get('version')!='lossless-history-partitions-v1'or not index.get('exact_concatenated_parity'):
        raise ValueError('Lossless historical partition manifest required')
    for part in index['parts']:
        if part['path']in seen:raise ValueError('Duplicate history partition')
        seen.add(part['path']);path=bound_file(root,part['path'],part['sha256'])
        if path.stat().st_size!=part['bytes']or path.stat().st_size>2*1024*1024:raise ValueError('Historical partition size changed')
        frame=pd.read_parquet(path)
        if len(frame)!=part['rows']or not frame.season.eq(part['season']).all():raise ValueError('History partition population changed')
        if list(frame.columns)!=index['columns']+['_package_row_index']:raise ValueError('History partition columns changed')
        pieces.append(frame)
    if not pieces:raise ValueError('Empty historical partition population')
    history=pd.concat(pieces,ignore_index=True).sort_values('_package_row_index',kind='stable')
    if len(history)!=index['rows']or not np.array_equal(history.pop('_package_row_index').to_numpy(),np.arange(index['rows'])):
        raise ValueError('History row alignment is missing or duplicated')
    history=history.reset_index(drop=True)
    if {c:str(t)for c,t in history.dtypes.items()}!=index['dtypes']:raise ValueError('Historical dtypes changed')
    return history
