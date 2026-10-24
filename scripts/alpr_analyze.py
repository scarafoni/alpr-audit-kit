#!/usr/bin/env python3
"""Deterministic screening, run after alpr_screen.py ingest. No misconduct classification."""
import csv, json, sys
from datetime import datetime, timezone
from pathlib import Path
import duckdb
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'analysis/alpr'
if not (OUT/'manifest.json').exists() and '--partial' not in sys.argv:
    raise SystemExit('Import is incomplete. Use --partial only for explicitly provisional analysis.')
REPORT=OUT/('partial-results' if '--partial' in sys.argv else 'results')
REPORT.mkdir(parents=True,exist_ok=True)
(OUT/'tmp').mkdir(parents=True,exist_ok=True)
c=duckdb.connect()
c.execute("SET memory_limit='2GB'")
c.execute('SET threads=2')
c.execute(f"SET temp_directory='{OUT}/tmp'")
c.execute(f"CREATE VIEW raw AS SELECT * FROM read_parquet('{OUT}/data/**/*.parquet')")
started_at=datetime.now(timezone.utc).isoformat()
(REPORT/'run.json').write_text(json.dumps({'status':'running','started_at':started_at}))
if '--partial' not in sys.argv:
    manifests=json.loads((OUT/'manifest.json').read_text())
    expected=sum(sum(n for k,n in sheet['counts'].items() if k not in ('header_rows','empty_rows')) for book in manifests for sheet in book['sheets'])
    actual=c.execute('SELECT count(*) FROM raw').fetchone()[0]
    if expected!=actual:
        raise RuntimeError(f'Manifest row-count mismatch: expected {expected}, extracted {actual}')
def save(name,sql):
    cur=c.execute(sql)
    with (REPORT/(name+'.csv')).open('w') as f:
        w=csv.writer(f); w.writerow([d[0] for d in cur.description])
        while rows:=cur.fetchmany(10000): w.writerows(rows)
    print(name,flush=True)
# Prefer revised exports without collapsing repeated searches within any export.
c.execute("""CREATE VIEW base AS SELECT * FROM raw WHERE source_file NOT IN
 ('2026 NETWORK AUDITS_REDACTED.xlsx','2026 ORGANIZATION AUDITS_REDACTED.xlsx','2026 EVENT LOG AUDITS_REDACTED.xlsx')""")
save('coverage',"SELECT source_file,sheet,kind,row_kind,count(*) records FROM raw GROUP BY ALL ORDER BY 1,2,4")
c.execute("""CREATE TEMP TABLE version_counts AS
 SELECT kind,sheet,row_hash,
 count(*) FILTER(WHERE source_file NOT LIKE '%(1)%') old_n,
 count(*) FILTER(WHERE source_file LIKE '%(1)%') new_n
 FROM raw WHERE source_file LIKE '2026%' AND kind IN ('network','organization','event')
 GROUP BY kind,sheet,row_hash""")
save('reconciliation',"""SELECT kind,sheet,sum(old_n) old_rows,sum(new_n) revised_rows,
 sum(least(old_n,new_n)) matching_rows,
 sum(greatest(old_n-new_n,0)) old_only_rows,
 sum(greatest(new_n-old_n,0)) revised_only_rows FROM version_counts GROUP BY 1,2 ORDER BY 1,2""")
c.execute("CREATE TEMP TABLE old_surplus AS SELECT * FROM version_counts WHERE old_n>new_n")
c.execute("DROP TABLE version_counts")
save('old_only_examples',"""WITH missing AS (
 SELECT a.*,b.new_n,row_number() OVER(PARTITION BY a.source_file,a.sheet,a.row_hash ORDER BY a.excel_row) occurrence
 FROM raw a JOIN old_surplus b USING(kind,sheet,row_hash)
 WHERE a.source_file LIKE '2026%' AND a.source_file NOT LIKE '%(1)%')
 SELECT source_file,sheet,excel_row,kind,search_time,timestamp,user_name,reason,case_number,event_type,entity_type
 FROM missing WHERE occurrence>new_n
 QUALIFY row_number() OVER(PARTITION BY kind,sheet ORDER BY excel_row)<=25""")
# A source layout anomaly places the timestamp one column right in some rows.
# Keep original Parquet values; repair only the screening view and expose provenance.
c.execute("""CREATE VIEW queries AS WITH classified AS (
 SELECT *,coalesce(try_strptime(search_time,'%m/%d/%Y, %I:%M:%S %p UTC'),try_cast(search_time AS TIMESTAMP)) IS NULL AND try_strptime(search_type,'%m/%d/%Y, %I:%M:%S %p UTC') IS NOT NULL shifted_timestamp
 FROM base WHERE kind IN ('organization','network')),
 normalized AS (SELECT * EXCLUDE(search_time,search_type,text_prompt,moderation),
 CASE WHEN shifted_timestamp THEN search_type ELSE search_time END search_time,
 CASE WHEN shifted_timestamp THEN text_prompt ELSE search_type END search_type,
 CASE WHEN shifted_timestamp THEN moderation ELSE text_prompt END text_prompt,
 CASE WHEN shifted_timestamp THEN '' ELSE moderation END moderation,
 CASE WHEN shifted_timestamp THEN 'timestamp_shifted_one_column_in_source' ELSE '' END layout_repair
 FROM classified WHERE row_kind='data' OR shifted_timestamp)
 SELECT *,coalesce(try_strptime(search_time,'%m/%d/%Y, %I:%M:%S %p UTC'),try_cast(search_time AS TIMESTAMP)) ts,
 lower(trim(reason)) reason_norm,lower(trim(case_number)) case_norm,lower(trim(plate)) plate_norm
 FROM normalized""")
save('layout_anomalies',"""SELECT source_file,sheet,excel_row,row_kind,search_time,search_type,text_prompt,moderation,extra_json,
 CASE WHEN coalesce(try_strptime(search_time,'%m/%d/%Y, %I:%M:%S %p UTC'),try_cast(search_time AS TIMESTAMP)) IS NULL AND try_strptime(search_type,'%m/%d/%Y, %I:%M:%S %p UTC') IS NOT NULL THEN 'timestamp_shifted_one_column_in_source' ELSE 'unresolved' END screening_action
 FROM base WHERE kind IN ('organization','network') AND (row_kind<>'data' OR (coalesce(try_strptime(search_time,'%m/%d/%Y, %I:%M:%S %p UTC'),try_cast(search_time AS TIMESTAMP)) IS NULL AND try_strptime(search_type,'%m/%d/%Y, %I:%M:%S %p UTC') IS NOT NULL))""")
save('query_quality',"""SELECT source_file,sheet,kind,count(*) records,min(ts) first_utc,max(ts) last_utc,
 count(*) FILTER(WHERE ts IS NULL) unparsed_time,
 count(*) FILTER(WHERE layout_repair<>'') repaired_layout_rows,
 count(*) FILTER(WHERE plate_norm='') blank_plate,
 count(*) FILTER(WHERE plate_norm LIKE '%redact%' OR regexp_full_match(plate_norm,'[*xX#-]+')) redacted_plate,
 count(*) FILTER(WHERE plate_norm='' OR plate_norm LIKE '%redact%' OR regexp_full_match(plate_norm,'[*xX#-]+')) unusable_plate,
 count(*) FILTER(WHERE reason_norm='') blank_reason,
 count(*) FILTER(WHERE reason_norm IN ('n/a','na','none','0','-','tbd','unknown','pending')) placeholder_reason,
 count(*) FILTER(WHERE case_norm='') blank_case,
 count(*) FILTER(WHERE case_norm LIKE '%redact%' OR regexp_full_match(case_norm,'[*xX#-]+')) redacted_case,
 count(*) FILTER(WHERE case_norm='' OR case_norm LIKE '%redact%' OR regexp_full_match(case_norm,'[*xX#-]+') OR case_norm IN ('n/a','na','none','0','-','test','tbd','unknown','pending')) unusable_case,
 count(*) FILTER(WHERE case_norm IN ('n/a','na','none','0','-','test','tbd','unknown','pending')) placeholder_case,
 count(*) FILTER(WHERE user_name='' OR lower(user_name) LIKE '%redact%' OR regexp_full_match(user_name,'[*xX#-]+')) unusable_user
 FROM queries GROUP BY 1,2,3 ORDER BY 1,2""")
save('protest_crosswalk',"""SELECT o.source_file organization_file,o.sheet organization_sheet,o.excel_row organization_excel_row,
 n.source_file network_file,n.sheet network_sheet,n.excel_row network_excel_row,o.search_time,o.user_name,
 o.organization,o.reason,o.case_number organization_case_number,n.networks network_count,n.devices device_count
 FROM queries o JOIN queries n ON o.search_time=n.search_time AND o.reason_norm=n.reason_norm
 AND o.organization=n.organization
 WHERE o.kind='organization' AND o.organization<>''
 AND regexp_matches(o.reason_norm,'(^|[^a-z])(protest|protester|rally|demonstration)([^a-z]|$)')
 AND n.kind='network' ORDER BY o.organization,o.excel_row""")
save('personal_test_candidates',"""SELECT source_file,sheet,excel_row,organization,search_time,reason,case_number,filters,search_type,networks,devices
 FROM queries WHERE kind='network' AND regexp_matches(lower(reason),'my own personal tag|my personal (tag|plate|vehicle)|test.{0,30}my own|my own.{0,30}(tag|plate|vehicle)')
 ORDER BY source_file,sheet,excel_row""")
save('assembly_clusters',"""SELECT source_file,sheet,organization,reason,count(*) records,
 min(search_time) first_utc,max(search_time) last_utc,
 min(try_cast(networks AS BIGINT)) minimum_networks,max(try_cast(networks AS BIGINT)) maximum_networks,
 count(*) FILTER(WHERE case_norm='') blank_case,count(*) FILTER(WHERE case_norm LIKE '%redact%' OR regexp_full_match(case_norm,'[*xX#-]+')) redacted_case,
 min(excel_row) first_excel_row
 FROM queries WHERE kind='network' AND regexp_matches(reason_norm,'(^|[^a-z])(protest|protester|rally|demonstration)([^a-z]|$)')
 GROUP BY 1,2,3,4 ORDER BY records DESC""")
save('long_time_windows',"""WITH windows AS (SELECT *,
 try_strptime(split_part(time_frame,chr(10),1),'%m/%d/%Y, %I:%M:%S %p UTC') start_ts,
 try_strptime(split_part(time_frame,chr(10),2),'%m/%d/%Y, %I:%M:%S %p UTC') end_ts FROM queries)
 SELECT source_file,sheet,excel_row,kind,organization,user_name,search_time,time_frame,reason,case_number,end_ts-start_ts duration
 FROM windows WHERE end_ts-start_ts>INTERVAL '5 years' OR end_ts<start_ts
 ORDER BY duration DESC NULLS LAST LIMIT 100""")
save('reason_frequency',"SELECT kind,reason_norm,count(*) records FROM queries GROUP BY 1,2 ORDER BY 1,3 DESC")
save('organization_frequency',"SELECT kind,organization,count(*) records FROM queries GROUP BY 1,2 ORDER BY 1,3 DESC")
save('federal_agency_context',"""SELECT organization,reason_norm,count(*) records,min(source_file) example_file,min_by(sheet,source_file||sheet||lpad(cast(excel_row AS VARCHAR),8,'0')) example_sheet,min_by(excel_row,source_file||sheet||lpad(cast(excel_row AS VARCHAR),8,'0')) example_row
 FROM queries WHERE regexp_matches(lower(organization),'immigration|customs|border|homeland|(^|[^a-z])(ice|cbp|hsi)([^a-z]|$)') GROUP BY 1,2 ORDER BY 3 DESC""")
save('search_types',"SELECT kind,search_type,count(*) records FROM queries GROUP BY 1,2 ORDER BY 1,3 DESC")
save('broad_search_examples',"""(SELECT source_file,sheet,excel_row,kind,organization,user_name,search_time,networks,devices,time_frame,reason,case_number FROM queries WHERE kind='network' ORDER BY try_cast(networks AS BIGINT) DESC NULLS LAST,source_file,sheet,excel_row LIMIT 100)
 UNION ALL (SELECT source_file,sheet,excel_row,kind,organization,user_name,search_time,networks,devices,time_frame,reason,case_number FROM queries WHERE kind='organization' ORDER BY try_cast(networks AS BIGINT) DESC NULLS LAST,source_file,sheet,excel_row LIMIT 100)""")
save('time_window_summary',"""WITH windows AS (SELECT *,
 try_strptime(split_part(time_frame,chr(10),1),'%m/%d/%Y, %I:%M:%S %p UTC') start_ts,
 try_strptime(split_part(time_frame,chr(10),2),'%m/%d/%Y, %I:%M:%S %p UTC') end_ts FROM queries)
 SELECT kind,count(*) records,count(*) FILTER(WHERE start_ts IS NULL OR end_ts IS NULL) unparsed_windows,
 count(*) FILTER(WHERE end_ts<start_ts) reversed_windows,
 count(*) FILTER(WHERE end_ts-start_ts>INTERVAL '30 days') over_30_days,
 count(*) FILTER(WHERE end_ts-start_ts>INTERVAL '5 years') over_5_years,
 max(end_ts-start_ts) max_window FROM windows GROUP BY 1""")
save('user_summary',"""SELECT kind,organization,user_name,count(*) records,count(DISTINCT cast(ts AS DATE)) active_utc_days,
 count(*) FILTER(WHERE reason_norm='') blank_reason,count(*) FILTER(WHERE case_norm='') blank_case,
 max(try_cast(networks AS BIGINT)) maximum_networks FROM queries GROUP BY 1,2,3 ORDER BY 1,4 DESC""")
save('query_candidates',"""WITH flags AS (SELECT *,
 concat_ws('; ',
 CASE WHEN regexp_matches(reason_norm,'(^|[^a-z])(wife|husband|girlfriend|boyfriend|ex-wife|ex-husband|my car|my vehicle|personal)([^a-z]|$)') THEN 'relationship_or_personal_wording' END,
 CASE WHEN regexp_matches(reason_norm,'(^|[^a-z])(test|testing|training|demo|practice)([^a-z]|$)') THEN 'training_or_test_context' END,
 CASE WHEN regexp_matches(reason_norm,'(^|[^a-z])(ice|immigration|deport|border patrol)([^a-z]|$)') THEN 'immigration_context' END,
 CASE WHEN regexp_matches(reason_norm,'(on behalf|requested by|request from|per request|for another)') THEN 'requester_context' END,
 CASE WHEN regexp_matches(reason_norm,'(^|[^a-z])(protest|protester|rally|demonstration)([^a-z]|$)') THEN 'assembly_context' END,
 CASE WHEN reason_norm IN ('n/a','na','none','0','-','tbd','unknown','pending') THEN 'placeholder_reason' END,
 CASE WHEN reason_norm='' AND case_norm='' THEN 'both_purpose_fields_blank' WHEN reason_norm='' THEN 'blank_reason' END) flags
 FROM queries)
 SELECT source_file,sheet,excel_row,kind,organization,user_name,search_time,reason,case_number,layout_repair,flags
 FROM flags WHERE flags<>'' ORDER BY kind,source_file,sheet,excel_row""")
save('burst_candidates',"""SELECT kind,organization,user_name,date_trunc('minute',ts) minute_utc,count(*) records,
 min(source_file) source_file,min(sheet) sheet,min(excel_row) first_excel_row,max(excel_row) last_excel_row
 FROM queries WHERE ts IS NOT NULL AND user_name<>'' AND lower(user_name) NOT LIKE '%redact%' AND NOT regexp_full_match(user_name,'[*xX#-]+')
 GROUP BY 1,2,3,4 HAVING count(*)>=20 ORDER BY records DESC""")
save('repeat_plate_candidates',"""SELECT kind,organization,user_name,plate,count(*) records,count(DISTINCT cast(ts AS DATE)) active_days,
 min(ts) first_utc,max(ts) last_utc FROM queries
 WHERE plate_norm NOT IN ('','n/a','na','none','unknown','*') AND plate_norm NOT LIKE '%redact%' AND NOT regexp_full_match(plate_norm,'[*xX#-]+')
 AND user_name<>'' AND lower(user_name) NOT LIKE '%redact%' AND NOT regexp_full_match(user_name,'[*xX#-]+')
 GROUP BY 1,2,3,4 HAVING count(*)>=20 AND count(DISTINCT cast(ts AS DATE))>=3 ORDER BY records DESC""")
save('repeat_plate_records',"""WITH recurring AS (
 SELECT organization,user_name,plate FROM queries
 WHERE kind='network' AND plate_norm NOT IN ('','n/a','na','none','unknown','*')
 AND plate_norm NOT LIKE '%redact%' AND NOT regexp_full_match(plate_norm,'[*xX#-]+')
 AND user_name<>'' AND lower(user_name) NOT LIKE '%redact%' AND NOT regexp_full_match(user_name,'[*xX#-]+')
 GROUP BY 1,2,3 HAVING count(*)>=20 AND count(DISTINCT cast(ts AS DATE))>=3)
 SELECT q.source_file,q.sheet,q.excel_row,q.organization,q.search_time,q.reason,q.case_number,q.networks
 FROM queries q JOIN recurring r USING(organization,user_name,plate) WHERE q.kind='network'
 ORDER BY q.organization,q.search_time,q.excel_row""")
save('event_summary',"SELECT source_file,sheet,event_type,entity_type,count(*) records FROM base WHERE kind='event' GROUP BY 1,2,3,4 ORDER BY 1,2,5 DESC")
save('event_access_candidates',"""SELECT source_file,sheet,excel_row,timestamp,user_name,event_type,entity_type,entity_details FROM base WHERE kind='event' AND regexp_matches(lower(entity_type||' '||event_type),'user|role|permission|sharing|network|export|login')""")
save('hotlist_review',"""SELECT source_file,sheet,excel_row,hotlist_name,owner,organization,created_at,expires,status,case_number,topic,
 concat_ws('; ',CASE WHEN case_number='' THEN 'blank_case' END,
 CASE WHEN expires='' THEN 'blank_expiration' END,
 CASE WHEN lower(hotlist_name) LIKE '%personal%' THEN 'personal_label_requires_context' END,
 CASE WHEN lower(status)='active' AND try_cast(expires AS TIMESTAMP)<try_strptime(sheet,'%m-%d-%Y') THEN 'active_past_snapshot_date' END) flags
 FROM base WHERE kind='hotlist' ORDER BY sheet,excel_row""")
save('sharing',"SELECT source_file,sheet,excel_row,organization,networks_shared_with_me,networks_im_sharing FROM base WHERE kind='sharing'")
save('annotations',"SELECT source_file,sheet,excel_row,notes,extra_json FROM base WHERE notes<>'' OR extra_json<>''")
save('export_summary',"SELECT source_file,sheet,excel_row,timestamp,user_name,entity_details FROM base WHERE kind='event' AND entity_type='export'")
save('unmapped_rows',"SELECT source_file,sheet,excel_row,row_kind,extra_json FROM base WHERE row_kind<>'data'")
(REPORT/'method.json').write_text(json.dumps({'baseline':'all imported workbook versions; revised exports with (1) in the filename are preferred when forming the analysis baseline','reconciliation':'multiset SHA256 of nonempty normalized named fields excluding query_id and notes; within-export repetitions retained; old-only records reported separately','warning':'Candidates are contextual review leads, not findings of misuse. Redacted plates excluded from plate recurrence. Times UTC; no duty roster inference. Network query does not establish a returned hit.','duckdb':duckdb.__version__},indent=2))

(REPORT/'run.json').write_text(json.dumps({'status':'complete','partial':'--partial' in sys.argv,'started_at':started_at,'completed_at':datetime.now(timezone.utc).isoformat()},indent=2))
