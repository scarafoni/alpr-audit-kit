#!/usr/bin/env python3
"""Local, resumable ALPR workbook import. Raw records stay in analysis/alpr/data.

Run with .venv/bin/python scripts/alpr_screen.py ingest.
Requires lxml, pyarrow, duckdb. Never changes source workbooks.
Shared strings use an indexed mmap rather than millions of Python strings.
"""
from __future__ import annotations
import argparse
from concurrent.futures import ProcessPoolExecutor
from array import array
from collections import Counter
from datetime import datetime, timedelta
from functools import lru_cache
import hashlib
import json
import mmap
import os
from pathlib import Path
import posixpath
import re
import tempfile
import time
from zipfile import ZipFile

from lxml import etree as ET
import pyarrow as pa
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'analysis/alpr'
VERSION = 1
NS = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
REL = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id'
FIELDS = '''query_id user_name organization networks devices time_frame plate reason
case_number filters search_time search_type text_prompt moderation timestamp event_type
entity_type entity_details event_id hotlist_name org_hotlist created_at deconfliction owner
state topic expires status networks_shared_with_me networks_im_sharing notes'''.split()
ALIASES = dict(zip(
    ['id','orgname','organizationname','totalnetworkssearched','totaldevicessearched',
     'timeframe','licenseplate','reason','case','casenumber','filter','filters',
     'searchtime','searchtype','textprompt','moderation','timestamp','user','eventtype',
     'entitytype','entitydetails','eventid','orghotlist','createdat','deconfliction',
     'owner','plate','state','topic','expires','status','networkssharedwithme',
     'networksimsharing','notes'],
    ['query_id','organization','organization','networks','devices',
     'time_frame','plate','reason','case_number','case_number','filters','filters',
     'search_time','search_type','text_prompt','moderation','timestamp','user_name','event_type',
     'entity_type','entity_details','event_id','org_hotlist','created_at','deconfliction',
     'owner','plate','state','topic','expires','status','networks_shared_with_me',
     'networks_im_sharing','notes']))
SCHEMA = pa.schema([(x, pa.string()) for x in ['source_file','sheet','kind','row_kind']+FIELDS+['extra_json']]
                   + [('excel_row',pa.int64()),('row_hash',pa.binary(32))])

def clear(e):
    e.clear()
    while e.getprevious() is not None:
        del e.getparent()[0]

class Strings:
    def __init__(self,z):
        self.offsets=array('Q',[0]); self.tmp=tempfile.TemporaryFile(dir=OUT)
        if 'xl/sharedStrings.xml' in z.namelist():
            with z.open('xl/sharedStrings.xml') as stream:
                for _,e in ET.iterparse(stream,events=('end',),tag=NS+'si'):
                    # phonetic/rPr text is excluded: cell content is t nodes in si/r.
                    parts=[]
                    for child in e:
                        if child.tag==NS+'t': parts.append(child.text or '')
                        elif child.tag==NS+'r':
                            parts.extend(t.text or '' for t in child if t.tag==NS+'t')
                    s=''.join(parts)
                    b=s.encode('utf-8'); self.tmp.write(b)
                    self.offsets.append(self.offsets[-1]+len(b)); clear(e)
                    if (len(self.offsets)-1)%2000000==0:
                        print(f'  shared strings: {len(self.offsets)-1:,}',flush=True)
        self.tmp.flush()
        self.mm=mmap.mmap(self.tmp.fileno(),0,access=mmap.ACCESS_READ) if self.offsets[-1] else None
    @lru_cache(maxsize=32768)
    def get(self,i):
        if not 0 <= i < len(self.offsets)-1: raise ValueError(f'Invalid shared-string reference {i}')
        return self.mm[self.offsets[i]:self.offsets[i+1]].decode('utf-8') if self.mm else ''
    def close(self):
        self.get.cache_clear()
        if self.mm: self.mm.close()
        self.tmp.close()

def sheet_map(z):
    rels={e.get('Id'):e.get('Target') for e in ET.fromstring(z.read('xl/_rels/workbook.xml.rels'))}
    book=ET.fromstring(z.read('xl/workbook.xml'))
    props=book.find(NS+'workbookPr')
    epoch=datetime(1904,1,1) if props is not None and props.get('date1904') in ('1','true') else datetime(1899,12,30)
    sheets=[]
    for s in book.find(NS+'sheets'):
        target=rels[s.get(REL)].lstrip('/')
        if not target.startswith('xl/'): target=posixpath.normpath('xl/'+target)
        sheets.append((s.get('name'),target))
    return sheets,epoch

def cell_value(c,strings):
    typ=c.get('t'); v=c.find(NS+'v')
    if typ=='inlineStr': return ''.join(c.itertext()),False
    if v is None or v.text is None: return '',False
    if typ=='s': return strings.get(int(v.text)),False
    return v.text,typ in (None,'n')

def col_number(ref):
    n=0
    for ch in ref:
        if not ch.isalpha(): break
        n=n*26+ord(ch.upper())-64
    return n-1

def kind_of(name):
    upper = name.upper()
    for key,kind in [('NETWORK AUDITS','network'),('ORGANIZATION AUDITS','organization'),
                     ('EVENT LOG','event'),('HOTLIST','hotlist')]:
        if key in upper: return kind
    if 'SHAREDNETWORK' in upper or 'SHARED NETWORK' in upper: return 'sharing'
    raise ValueError(f"Cannot identify workbook type from filename: {name}. Include NETWORK AUDITS, ORGANIZATION AUDITS, EVENT LOG, HOTLIST, or SharedNetworks in the filename.")

def normalize_header(value,kind):
    key=re.sub('[^a-z0-9]','',value.lower())
    if key=='name': return 'hotlist_name' if kind=='hotlist' else 'user_name'
    return ALIASES.get(key)

def file_hash(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda:f.read(4*1024*1024),b''): h.update(chunk)
    return h.hexdigest()

def import_book(path):
    slug=re.sub('[^a-z0-9]+','_',path.stem.lower()).strip('_')
    dest=OUT/'data'/slug; dest.mkdir(parents=True,exist_ok=True)
    manifest_path=dest/'manifest.json'
    sha=file_hash(path)
    if manifest_path.exists():
        old=json.loads(manifest_path.read_text())
        if old.get('sha256')==sha and old.get('parser_version')==VERSION and all((dest/s['parquet']).exists() for s in old['sheets']):
            print('CACHED',path.name,flush=True); return old
    print('IMPORT',path.name,flush=True)
    manifest={'source_file':path.name,'sha256':sha,'size':path.stat().st_size,
              'parser_version':VERSION,'sheets':[],'kind':kind_of(path.name)}
    with ZipFile(path) as z:
        strings=Strings(z); sheets,epoch=sheet_map(z)
        try:
            for index,(name,member) in enumerate(sheets):
                fn=f'{index:02d}.parquet'; temporary=dest/(fn+'.partial')
                writer=pq.ParquetWriter(temporary,SCHEMA,compression='zstd')
                batch=[]; header=None; mapping={}; counts=Counter(); raw_headers=[]; dimension=None
                with z.open(member) as stream:
                    for _,row in ET.iterparse(stream,events=('end',),tag=(NS+'row',NS+'dimension')):
                        if row.tag==NS+'dimension': dimension=row.get('ref'); clear(row); continue
                        cells={col_number(c.get('r')):cell_value(c,strings) for c in row if c.tag==NS+'c'}
                        if not any(v.strip() for v,_ in cells.values()): counts['empty_rows']+=1; clear(row); continue
                        if header is None:
                            header={i:v.strip() for i,(v,_) in cells.items()}
                            mapping={i:normalize_header(v,manifest['kind']) for i,v in header.items()}
                            raw_headers=list(header.values()); counts['header_rows']+=1
                            # No recognized header: retain all rows as unmapped evidence.
                            if sum(v is not None for v in mapping.values())>=2:
                                clear(row); continue
                            mapping={}
                        rec={f:'' for f in FIELDS}; extra={}
                        for i,(v,numeric) in cells.items():
                            key=mapping.get(i)
                            if key:
                                if numeric and key in ('timestamp','search_time','created_at','expires'):
                                    try:
                                        number=float(v)
                                        if 20000<number<100000:
                                            extra[f'raw_serial_{key}']=v
                                            v=(epoch+timedelta(days=number)).isoformat(sep=' ',timespec='microseconds')
                                    except ValueError: pass
                                rec[key]=v.strip()
                            elif v: extra[header.get(i,f'column_{i+1}') or f'column_{i+1}']=v
                        if not mapping: row_kind='unmapped'
                        elif all(cells.get(i,('',False))[0].strip()==v for i,v in header.items()): row_kind='repeated_header'
                        elif manifest['kind'] in ('network','organization'):
                            row_kind='data' if rec['search_time'] else 'incomplete'
                        elif manifest['kind']=='event': row_kind='data' if rec['timestamp'] else 'incomplete'
                        else: row_kind='data'
                        # Compare semantic fields consistently across column order and added IDs.
                        fingerprint={k:v for k,v in rec.items() if k not in ('query_id','notes') and v!=''}
                        digest=hashlib.sha256(json.dumps(fingerprint,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).digest()
                        rec.update(source_file=path.name,sheet=name,kind=manifest['kind'],row_kind=row_kind,
                                   excel_row=int(row.get('r')),row_hash=digest,
                                   extra_json=json.dumps(extra,ensure_ascii=False) if extra else '')
                        batch.append(rec); counts[row_kind]+=1; clear(row)
                        if len(batch)>=10000:
                            writer.write_table(pa.Table.from_pylist(batch,schema=SCHEMA)); batch.clear()
                            if sum(counts.values())//10000%20==0: print(f'  {name}: {sum(counts.values()):,} rows',flush=True)
                if batch: writer.write_table(pa.Table.from_pylist(batch,schema=SCHEMA))
                writer.close(); temporary.replace(dest/fn)
                stat={'sheet':name,'parquet':fn,'dimension':dimension,'headers':raw_headers,
                      'unknown_headers':[v for i,v in header.items() if mapping.get(i) is None] if header else [],
                      'counts':dict(counts)}
                manifest['sheets'].append(stat)
                print('  DONE',name,dict(counts),flush=True)
        finally: strings.close()
    manifest_path.write_text(json.dumps(manifest,indent=2))
    return manifest

def ingest(input_dir: Path):
    OUT.mkdir(parents=True,exist_ok=True); os.chmod(OUT,0o700)
    paths=sorted(input_dir.glob('*.xlsx'),key=lambda p:p.stat().st_size)
    if not paths:
        raise SystemExit(f"No .xlsx workbooks found in {input_dir}. Put the downloaded Flock audit workbooks there or pass --input-dir.")
    with ProcessPoolExecutor(max_workers=3) as pool:
        manifests=list(pool.map(import_book,paths))
    (OUT/'manifest.json').write_text(json.dumps(manifests,indent=2))

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['ingest'])
    parser.add_argument('--input-dir',type=Path,default=ROOT/'flock_audit_logs',help='Folder containing downloaded .xlsx audit workbooks')
    args=parser.parse_args(); ingest(args.input_dir.expanduser().resolve())
