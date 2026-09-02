import{afterEach,describe,expect,it,vi}from'vitest';import{api,metricCatalog,subscribeLive}from'./client';

describe('API response adapters',()=>{
 it('reads events from a cursor-page response',async()=>{
  const fetchMock=vi.spyOn(globalThis,'fetch')
   .mockResolvedValueOnce(new Response(JSON.stringify({items:[{event_id:'event-1',timestamp:'2026-09-02T12:00:00Z',host_id:'host-1',signal_type:'lifecycle',name:'process.started'}],next_cursor:'event-cursor'}),{status:200,headers:{'Content-Type':'application/json'}}))
   .mockResolvedValueOnce(new Response(JSON.stringify({items:[],next_cursor:null}),{status:200,headers:{'Content-Type':'application/json'}}));

  const result=await api.events('2026-09-02T11:00:00Z','2026-09-02T13:00:00Z');

  expect(result.items).toHaveLength(1);
  expect(result.items[0]).toMatchObject({id:'event-1',entity_id:'host-1',message:'process.started'});
  expect(result.next_cursor).toBe('event-cursor');
  fetchMock.mockRestore();
 });
});
describe('fleet and catalog adapters',()=>{
 it('handles wrapped catalog responses and inactive hosts without inventing health',async()=>{
  const fetchMock=vi.spyOn(globalThis,'fetch').mockResolvedValueOnce(new Response(JSON.stringify({items:[{id:4,name:'system.cpu.utilization',unit:'%',series_count:2}]}),{status:200}));
  await expect(metricCatalog()).resolves.toEqual([{id:'4',name:'system.cpu.utilization',unit:'%',series_count:2}]);
  fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({hosts:[{id:'h-1',name:'offline-test',active:false}],services:[],processes:[],dependencies:[]}),{status:200}));
  await expect(api.entities('host')).resolves.toMatchObject([{id:'h-1',health:'offline',active:false}]);
 fetchMock.mockRestore();
 });
 it('queries every dependency series without applying the generic catalog cap',async()=>{
  const catalog=Array.from({length:170},(_,index)=>({id:index+1,name:`dependency.rows.metric_${index}`,unit:'1',attributes:{database:'workload'}}));
  const fetchMock=vi.spyOn(globalThis,'fetch').mockImplementation(async(input)=>{
   const url=new URL(String(input));
   if(url.pathname.endsWith('/metrics/series'))return new Response(JSON.stringify(catalog),{status:200});
   const ids=url.searchParams.get('dependency_id')==='dependency-1'?catalog.map(item=>String(item.id)):url.searchParams.getAll('series_id');
   return new Response(JSON.stringify({series:ids.map(id=>({series_id:id,points:[{timestamp:'2026-09-03T00:00:00Z',last:1,count:1}]}))}),{status:200});
  });
  const result=await api.metrics('dependency-1','2026-09-02T00:00:00Z','2026-09-03T00:00:00Z',300,undefined,undefined,'dependency');
  expect(result).toHaveLength(170);
  expect(new URL(String(fetchMock.mock.calls[1][0])).searchParams.get('dependency_id')).toBe('dependency-1');
  fetchMock.mockRestore();
 });
 it('stitches semantic container streams across UUID incarnations',async()=>{
  const catalog=[
   {id:11,name:'container.cpu.utilization',unit:'percent',source_type:'system',container_id:'old',attributes:{}},
   {id:12,name:'container.cpu.utilization',unit:'percent',source_type:'system',container_id:'current',attributes:{}},
  ];
  const fetchMock=vi.spyOn(globalThis,'fetch').mockImplementation(async(input)=>{
   const url=new URL(String(input));
   if(url.pathname.endsWith('/metrics/series'))return new Response(JSON.stringify(catalog),{status:200});
   return new Response(JSON.stringify({series:[
    {series_id:11,points:[{timestamp:'2026-09-03T00:00:00Z',last:1,average:1,count:1}]},
    {series_id:12,points:[{timestamp:'2026-09-03T00:01:00Z',last:2,average:2,count:1}]},
   ]}),{status:200});
  });
  const result=await api.metrics('current','2026-09-03T00:00:00Z','2026-09-03T00:02:00Z',300,undefined,undefined,'container');
  expect(result).toHaveLength(1);
  expect(result[0].buckets.map(point=>point.last)).toEqual([1,2]);
  const queryUrl=new URL(String(fetchMock.mock.calls[1][0]));
  expect(queryUrl.searchParams.getAll('series_id')).toEqual(['11','12']);
  expect(queryUrl.searchParams.has('container_id')).toBe(false);
  fetchMock.mockRestore();
 });
});
describe('live updates',()=>{afterEach(()=>{vi.useRealTimers();vi.unstubAllGlobals()});it('falls back to bounded polling and attempts to reconnect',()=>{vi.useFakeTimers();const instances:FakeSource[]=[];class FakeSource{onopen:(()=>void)|null=null;onmessage:((event:{data:string})=>void)|null=null;onerror:(()=>void)|null=null;constructor(public url:string){instances.push(this)}close=vi.fn()}vi.stubGlobal('EventSource',FakeSource);const poll=vi.fn(),state=vi.fn();const close=subscribeLive(vi.fn(),state,poll);instances[0].onerror?.();expect(state).toHaveBeenCalledWith(false);vi.advanceTimersByTime(5000);expect(poll).toHaveBeenCalledOnce();vi.advanceTimersByTime(10000);expect(instances).toHaveLength(2);close();});});
