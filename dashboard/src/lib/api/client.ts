import type { CollectorHealth, CursorPage, Entity, EntityKind, Experiment, FleetSummary, Health, LiveWatermark, MetricCatalogEntry, MetricSeries, TimelineEvent } from '$lib/types/api';

const API = '/api/v1';
export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message); }
}
async function get<T>(path: string, params: Record<string,string|number|boolean|(string|number)[]|undefined> = {}, signal?: AbortSignal): Promise<T> {
  const url = new URL(`${API}${path}`, location.origin);
  Object.entries(params).forEach(([key, value]) => { if (Array.isArray(value)) value.forEach(item=>url.searchParams.append(key,String(item))); else if(value!==undefined) url.searchParams.set(key,String(value)); });
  const response = await fetch(url, { signal, headers: { Accept: 'application/json' } });
  if (!response.ok) throw new ApiError(response.status, (await response.text()) || `Request failed (${response.status})`);
  return response.json() as Promise<T>;
}
type RawTopology = { hosts?: unknown[]; services?: unknown[]; processes?: unknown[]; dependencies?: unknown[]; containers?: unknown[]; service_dependencies?: unknown[] };
const rows = <T = any>(value: unknown, keys: string[] = []): T[] => {
  if (Array.isArray(value)) return value as T[];
  if (value && typeof value === 'object') {
    for (const key of keys) if (Array.isArray((value as Record<string, unknown>)[key])) return (value as Record<string, unknown>)[key] as T[];
    const items = (value as Record<string, unknown>).items;
    if (Array.isArray(items)) return items as T[];
  }
  return [];
};
const validDate = (value: unknown) => typeof value === 'string' && Number.isFinite(Date.parse(value));
const health = (row:any):Health => {
  if (row?.active === false) return 'offline';
  const stamp = row?.last_seen_at || row?.observed_at;
  if (!validDate(stamp)) return 'unknown';
  return Date.now() - Date.parse(stamp) > 45_000 ? 'degraded' : 'healthy';
};
const labels = (value: unknown): Record<string,string> => {
  if (!value || typeof value !== 'object') return {};
  return Object.fromEntries(Object.entries(value as Record<string, unknown>).map(([key, item]) => [key, String(item)]));
};
const entity = (row:any,kind:EntityKind):Entity => ({
  id:String(row?.id ?? row?.external_id ?? `${kind}-unknown`), name:row?.name||row?.external_id||`${kind} ${row?.id ?? 'unknown'}`,
  kind, health:health(row), host_id:row?.host_id?String(row.host_id):undefined, agent_id:row?.agent_id?String(row.agent_id):undefined,
  parent_id:row?.service_id?String(row.service_id):undefined, container_id:row?.container_id?String(row.container_id):undefined,
  dependency_type:kind==='dependency'&&row?.kind?String(row.kind):undefined,
  pid:Number.isFinite(Number(row?.pid))?Number(row.pid):undefined, active:row?.active!==false,
  last_seen:validDate(row?.last_seen_at||row?.observed_at)?(row.last_seen_at||row.observed_at):new Date(0).toISOString(),
  labels:labels(row?.attributes), attributes:row?.attributes&&typeof row.attributes==='object'?row.attributes:{}, command:row?.command||undefined,
});
async function topology(signal?:AbortSignal){ return get<RawTopology>('/topology',{},signal); }
function flattenTopology(t:RawTopology){
  const hosts=rows<any>(t.hosts), services=rows<any>(t.services), processes=rows<any>(t.processes), containers=rows<any>(t.containers), dependencies=rows<any>(t.dependencies), serviceDependencies=rows<any>(t.service_dependencies);
  const result:Entity[]=[];
  for(const h of hosts){
    const host=entity(h,'host');
    const hostServices=services.filter(x=>String(x.host_id)===host.id), hostProcesses=processes.filter(x=>String(x.host_id)===host.id), hostContainers=containers.filter(x=>String(x.host_id)===host.id), hostDependencies=dependencies.filter(x=>String(x.host_id)===host.id);
    const childEntities=[...hostServices.map(x=>entity(x,'service')),...hostProcesses.map(x=>entity(x,'process')),...hostContainers.map(x=>entity(x,'container')),...hostDependencies.map(x=>entity(x,'dependency'))];
    host.children=childEntities;
    result.push(host);
    for(const service of hostServices){ result.push(entity(service,'service')); result.push(...hostProcesses.filter(x=>String(x.service_id)===String(service.id)).map(x=>entity(x,'process'))); }
    result.push(...hostProcesses.filter(x=>!x.service_id).map(x=>entity(x,'process')));
    result.push(...hostContainers.map(x=>entity(x,'container')));
    result.push(...hostDependencies.map(x=>({ ...entity(x,'dependency'), related_ids: serviceDependencies.filter(link=>String(link.dependency_id)===String(x.id) && link.active!==false).map(link=>String(link.service_id)) })));
  }
  return result;
}
async function entities(kind?:string,signal?:AbortSignal){ const all=flattenTopology(await topology(signal)); return kind?all.filter(x=>x.kind===kind):all; }
async function fleet(signal?:AbortSignal):Promise<FleetSummary>{ const t=await topology(signal),all=flattenTopology(t),hosts=all.filter(x=>x.kind==='host');return{hosts:rows(t.hosts).length,services:rows(t.services).length,processes:rows(t.processes).length,containers:rows(t.containers).length,dependencies:rows(t.dependencies).length,healthy:hosts.filter(x=>x.health==='healthy').length,degraded:hosts.filter(x=>x.health==='degraded').length,offline:hosts.filter(x=>x.health==='offline').length,ingestion_delay_ms:Math.max(0,...hosts.map(x=>Math.max(0,Date.now()-Date.parse(x.last_seen))))}; }
async function entityDetail(kind:string,id:string,signal?:AbortSignal){ const plural:{[key:string]:string}={host:'hosts',service:'services',process:'processes',container:'containers',dependency:'dependencies'};const raw=await get<any>(`/${plural[kind]||`${kind}s`}/${id}`,{},signal); const result=entity(raw,kind as EntityKind); const childKinds:[string,EntityKind][]=[['services','service'],['processes','process'],['containers','container'],['dependencies','dependency']]; result.children=childKinds.flatMap(([key,childKind])=>rows<any>(raw?.[key]).map(item=>entity(item,childKind))); return result; }
export async function metricCatalog(signal?:AbortSignal):Promise<MetricCatalogEntry[]>{ return rows<any>(await get<any>('/metrics/catalog',{},signal)).map(item=>({id:String(item.id),name:String(item.name||''),unit:String(item.unit||''),series_count:Number(item.series_count||0)})); }
// First paint is deliberately limited to aggregate host streams. Dimensional
// device/interface/CPU streams remain discoverable through the catalog/detail
// views, but never fan out the overview query.
export const SYSTEM_METRIC_NAMES=['system.cpu.utilization','system.memory.available','system.memory.total','system.load1','system.network.rx_bytes.rate','system.network.tx_bytes.rate','system.disk.sectors_read.rate','system.disk.sectors_written.rate'];
export const PROCESS_METRIC_NAMES=['process.cpu.utilization','process.memory.rss','process.memory.virtual','process.io.read_bytes','process.io.write_bytes','process.threads','process.file_descriptors','process.faults.minor','process.faults.major'];
function selectSeries(catalog:any[], metricNames?:string[], maxSeries=160) {
  const filtered=metricNames?.length?catalog.filter(x=>metricNames.includes(String(x.name))):catalog;
  if (!metricNames?.length) return filtered.slice(0, maxSeries);
  const selected:any[]=[];
  for (const name of metricNames) {
    const matching=filtered.filter(x=>String(x.name)===name).sort((a,b)=>{
      const aa=a.attributes&&typeof a.attributes==='object'?Object.keys(a.attributes).length:0;
      const bb=b.attributes&&typeof b.attributes==='object'?Object.keys(b.attributes).length:0;
      const av=JSON.stringify(a.attributes||{}), bv=JSON.stringify(b.attributes||{});
      const loop=(value:string)=>/lo|loopback|docker|veth/i.test(value)?1:0;
      return aa-bb || loop(av)-loop(bv) || String(a.id).localeCompare(String(b.id));
    });
    // A fleet view needs representative streams, not a chart for every CPU/device.
    selected.push(...matching.slice(0, 1));
  }
  return selected.slice(0, 10);
}
async function metrics(entityId:string,start:string,end:string,maxPoints=800,signal?:AbortSignal,metricNames?:string[],entityKind?:EntityKind):Promise<MetricSeries[]>{
 const all=entityKind ? [] : await entities(undefined,signal), selected=all.find(x=>x.id===entityId), key=`${entityKind||selected?.kind||'host'}_id`; const catalog=rows<any>(await get<any>('/metrics/series',{[key]:entityId},signal));
 const filtered=selectSeries(catalog,metricNames,metricNames?.length?160:Number.POSITIVE_INFINITY);
 if(!filtered.length)return[];
 // Container catalogs may include older UUID incarnations with the same
 // host/name identity. Query the resolved IDs instead of filtering the data
 // endpoint back down to only the current UUID.
 const queryScope=metricNames?.length||entityKind==='container'?{series_id:filtered.map(x=>x.id)}:{[key]:entityId};
 const result=await get<any>('/metrics/query',{start,end,max_points:maxPoints,...queryScope},signal);
  const adapted=rows<any>(result?.series).map((s:any)=>{const meta=filtered.find(x=>String(x.id)===String(s.series_id));return{id:String(s.series_id),name:meta?.name||`series ${s.series_id}`,unit:meta?.unit||'',entity_id:entityId,source_type:meta?.source_type,attributes:meta?.attributes||{},buckets:rows<any>(s?.points).filter(p=>validDate(p.timestamp)).map(p=>({...p,timestamp:String(p.timestamp),count:Number(p.count||0)}))};}).filter(x=>x.buckets.length);
  if(entityKind!=='container')return adapted;
  const grouped=new Map<string,MetricSeries>();
  for(const item of adapted){
    const attributeKey=JSON.stringify(Object.entries(item.attributes||{}).sort(([a],[b])=>a.localeCompare(b)));
    const semanticKey=[item.name,item.unit,item.source_type||'',attributeKey].join('|');
    const existing=grouped.get(semanticKey);
    if(existing){
      const points=new Map(existing.buckets.map(point=>[point.timestamp,point]));
      for(const point of item.buckets)points.set(point.timestamp,point);
      existing.buckets=[...points.values()].sort((a,b)=>Date.parse(a.timestamp)-Date.parse(b.timestamp));
    }else grouped.set(semanticKey,{...item,id:`container-history:${item.id}`});
  }
  return [...grouped.values()];
}
/** Query one representative aggregate per metric across every host.  The
 * overview should describe the fleet as a whole, so it must not silently use
 * whichever host happens to sort first in topology.  Aggregation is done here
 * after the API returns dimensional streams: gauges/capacity are averaged and
 * rate streams are summed.
 */
async function metricsAggregate(start:string,end:string,maxPoints=240,signal?:AbortSignal,metricNames=SYSTEM_METRIC_NAMES):Promise<MetricSeries[]> {
  const discovered=(await Promise.all(metricNames.map(name=>get<any>('/metrics/series',{metric:name},signal)))).flatMap(value=>rows<any>(value));
  // Keep one representative dimensional stream per host and metric. Querying
  // every CPU core and block device makes a 24-hour Home request grow without
  // bound, while the fleet overview only needs one comparable value per node.
  const groups=new Map<string,any[]>();
  for(const item of discovered){const key=`${item.host_id||'unscoped'}:${item.name}`;const group=groups.get(key)||[];group.push(item);groups.set(key,group);}
  const catalogs=[...groups.values()].flatMap(group=>selectSeries(group,[String(group[0]?.name||'')],1));
  if(!catalogs.length)return[];
  const result=await get<any>('/metrics/query',{start,end,max_points:maxPoints,series_id:catalogs.map(x=>x.id)},signal);
  const byName=new Map<string,{meta:any; points:Map<string,any[]>}>();
  for(const series of rows<any>(result?.series)){
    const meta=catalogs.find(x=>String(x.id)===String(series.series_id));
    if(!meta)continue;
    const entry=byName.get(meta.name)||{meta,points:new Map<string,any[]>()}; byName.set(meta.name,entry);
    for(const point of rows<any>(series?.points)) if(validDate(point.timestamp)){
      const bucket=entry.points.get(String(point.timestamp))||[]; bucket.push(point); entry.points.set(String(point.timestamp),bucket);
    }
  }
  return [...byName.entries()].map(([name,entry])=>{
    const additive=/\.rate$/.test(name);
    const buckets=[...entry.points.entries()].sort(([a],[b])=>Date.parse(a)-Date.parse(b)).map(([timestamp,points])=>{
      const values=points.map(point=>point.average ?? point.last).filter((value):value is number=>value!=null&&Number.isFinite(Number(value))).map(Number);
      if(!values.length)return null;
      const minValues=points.map(point=>point.min).filter((value):value is number=>value!=null&&Number.isFinite(Number(value))).map(Number);
      const maxValues=points.map(point=>point.max).filter((value):value is number=>value!=null&&Number.isFinite(Number(value))).map(Number);
      const combine=(items:number[])=>additive?items.reduce((sum,value)=>sum+value,0):items.reduce((sum,value)=>sum+value,0)/items.length;
      const lastValues=points.map(point=>point.last).filter((value):value is number=>value!=null&&Number.isFinite(Number(value))).map(Number);
      return {timestamp,min:minValues.length?(additive?Math.min(...minValues):combine(minValues)):null,max:maxValues.length?(additive?Math.max(...maxValues):combine(maxValues)):null,average:combine(values),last:combine(lastValues.length?lastValues:values),count:points.reduce((sum,point)=>sum+Number(point.count||0),0)};
    }).filter(Boolean) as any[];
    return {id:`fleet:${name}`,name,unit:entry.meta.unit||'',entity_id:'',source_type:'aggregate',attributes:{scope:'fleet',aggregation:additive?'sum':'average'},buckets};
  }).filter(item=>item.buckets.length);
}
export const ACTIVITY_METRIC_NAMES=['demo_jobs_total','demo_jobs_in_flight','demo_queue_depth','demo_queue_wait_seconds_count','demo_events_total','demo_event_stream_depth','demo_cron_runs_total','demo_database_operations_total'];
async function activityMetrics(start:string,end:string,maxPoints=600,signal?:AbortSignal):Promise<MetricSeries[]>{
 const catalogs=(await Promise.all(ACTIVITY_METRIC_NAMES.map(name=>get<any>('/metrics/series',{metric:name},signal)))).flatMap(value=>rows<any>(value)).slice(0,240);
 if(!catalogs.length)return[];
 const result=await get<any>('/metrics/query',{start,end,max_points:maxPoints,series_id:catalogs.map(x=>x.id)},signal);
 return rows<any>(result?.series).map((s:any)=>{const meta=catalogs.find(x=>String(x.id)===String(s.series_id));return{id:String(s.series_id),name:meta?.name||`series ${s.series_id}`,unit:meta?.unit||'',entity_id:String(meta?.service_id||meta?.host_id||''),source_type:meta?.source_type,attributes:meta?.attributes||{},buckets:rows<any>(s?.points).filter(p=>validDate(p.timestamp)).map(p=>({...p,timestamp:String(p.timestamp),count:Number(p.count||0)}))};}).filter(x=>x.buckets.length);
}
async function events(start:string,end:string,filters='',cursor?:string,signal?:AbortSignal):Promise<CursorPage<TimelineEvent>>{
 const eventParams:any={start,end}; if(filters.startsWith('experiment_id:'))eventParams.experiment_id=filters.slice(14); const severity=filters.startsWith('severity:')?[filters.slice(9)]:undefined; const [eventPage,logs]=await Promise.all([get<any>('/events',eventParams,signal),get<any>('/logs',{start,end,search:filters&&!filters.includes(':')?filters:undefined,severity,cursor},signal)]);
 const timeline=rows<any>(eventPage);
 const map=(x:any):TimelineEvent=>({id:String(x.event_id||x.id),timestamp:x.timestamp,observed_timestamp:x.observed_timestamp,entity_id:String(x.process_id||x.service_id||x.dependency_id||x.host_id||''),signal_type:x.signal_type||'log',severity:x.severity?.toLowerCase(),message:x.message||x.name||x.event_type||'Recorded event',type:x.event_type,end_timestamp:x.end_timestamp||x.attributes?.end_timestamp,attributes:x.attributes});
 return {items:[...timeline.map(map),...rows<any>(logs).map(map)].sort((a,b)=>Date.parse(a.timestamp)-Date.parse(b.timestamp)),next_cursor:(logs&&typeof logs==='object'&&(logs as any).next_cursor)||((eventPage&&typeof eventPage==='object'&&(eventPage as any).next_cursor))||null};
}
const mapExperiment=(x:any):Experiment=>({id:String(x.id),name:x.name,status:x.status,started_at:x.started_at,ended_at:x.ended_at,scenario:x.configuration?.scenario||x.configuration?.type});
async function collectors(signal?:AbortSignal):Promise<CollectorHealth[]>{const reports=rows<any>(await get<any>('/collector-health',{},signal));return reports.flatMap(row=>{const common={agent_id:row.agent_id?String(row.agent_id):undefined,agent_version:row.agent_version,spool_bytes:Number(row.spool_bytes||0),spool_events:Number(row.spool_events||0),updated_at:validDate(row.observed_at)?row.observed_at:new Date(0).toISOString()};const list=rows<any>(row.collectors);return list.length?list.map((c:any)=>({name:c.name||c.collector||'collector',status:(c.status==='ok'?'healthy':c.status||'unknown') as Health,message:c.message||c.error,...common})): [{name:'agent heartbeat',status:'healthy' as Health,message:undefined,...common}];});}
export const api = {
  fleet, entities,
  entity: entityDetail,
  metrics, metricsAggregate, activityMetrics, events, metricCatalog,
  experiments: async (signal?:AbortSignal)=>(await get<any[]>('/experiments',{},signal)).map(mapExperiment),
  experiment: async (id:string,signal?:AbortSignal)=>mapExperiment(await get<any>(`/experiments/${id}`,{},signal)),
  collectors
};

export function subscribeLive(onWatermark: (value: LiveWatermark) => void, onState: (connected: boolean) => void, poll: () => void) {
  let source: EventSource | undefined;
  let timer: ReturnType<typeof setInterval> | undefined;
  let stopped=false,reconnect:ReturnType<typeof setTimeout>|undefined;
  const connect=()=>{
    if(stopped||typeof EventSource==='undefined') return;
    source=new EventSource(`${API}/live`);
    source.onopen=()=>{ onState(true); if(timer){ clearInterval(timer); timer=undefined; } };
    source.onmessage=(event)=>{ try { onWatermark(JSON.parse(event.data)); } catch { /* heartbeat */ } };
    source.onerror=()=>{
      onState(false);
      source?.close();
      if(!timer) timer=setInterval(poll,5000);
      reconnect=setTimeout(connect,15000);
    };
  };
  if (typeof EventSource === 'undefined') timer = setInterval(poll, 5000); else connect();
  return () => { stopped=true;source?.close();if(timer)clearInterval(timer);if(reconnect)clearTimeout(reconnect); };
}
