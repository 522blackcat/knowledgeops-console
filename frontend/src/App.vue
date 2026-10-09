<script setup>
import { ref, computed, nextTick, onMounted, onUnmounted, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
// 所有请求走同源 /api，浏览器不直接访问容器内部地址。
const token=ref(sessionStorage.getItem('agent_token')||'')
const username=ref('admin'), password=ref(''), user=ref(null), page=ref('chat'), busy=ref(false)
const historyOpen=ref(false)
const historyPos=ref({right:18,bottom:118})
let historyDrag=null
const agents=ref([]), bases=ref([]), approvals=ref([]), messages=ref([])
const approvalView=ref('pending')
const pendingUserMessage=ref(null)
const pendingBeforeMessageCount=ref(0)
const messagesEl=ref(null), autoScrollMessages=ref(true)
const ingestJobs=ref([]), jobsLoading=ref(false)
const adminOverview=ref({tables:[],queues:[],users:[]}), adminLoading=ref(false)
const auditLogs=ref([]), auditLoading=ref(false)
const auditFilters=ref({action:'',resource_type:'',actor:''})
const evalReports=ref([]), evalTrend=ref([]), evalRetestHistory=ref([]), evalLoading=ref(false)
const selectedEvalReportFile=ref('')
const evalCaseView=ref('failures')
const evalFailureReason=ref('')
const evalAggregateFilter=ref(null)
const evalRetest=ref({})
const evalBatchRetest=ref({loading:false,result:null,error:''})
const selectedAdminTable=ref(''), tableRows=ref([]), tableMeta=ref({read_only:true,editable_fields:[]}), tableLoading=ref(false), editDialogOpen=ref(false), createUserOpen=ref(false), agentCreateOpen=ref(false), editRow=ref(null), editJson=ref('')
const conversations=ref([]), conversationsLoading=ref(false)
const tableColumns=computed(()=>{
  const visibleColumns={
    users:['username','role','is_active','created_at'],
    agent_definitions:['name','status','description','agent_type','created_at'],
    conversations:['title','status','created_at'],
    conversation_messages:['role','content','sequence_no','created_at'],
    conversation_summaries:['version','summary_text','covered_until_sequence','created_at'],
    long_term_memories:['memory_type','content','importance','created_at'],
    agent_runs:['status','question','answer','retry_count','created_at'],
    run_events:['event_type','payload_json','created_at'],
    approval_requests:['tool_name','status','review_reason','created_at','reviewed_at'],
    tool_executions:['tool_name','status','result_json','created_at'],
    knowledge_bases:['name','scope','description','collection_name','created_at'],
    knowledge_documents:['filename','status','current_version','vector_cleanup_status','created_at'],
    document_chunks:['chunk_index','text','token_count','source_page'],
    ingest_jobs:['status','filename','completed_chunks','total_chunks','retry_count','last_error','created_at','updated_at'],
    ingest_batches:['batch_index','status','retry_count','start_chunk_index','end_chunk_index'],
  }
  if(visibleColumns[selectedAdminTable.value])return visibleColumns[selectedAdminTable.value]
  const priority=['status','title','name','username','role','filename','question','answer','description','completed_chunks','total_chunks','retry_count','created_at','updated_at','is_active']
  const hidden=new Set(['tenant_id','user_id','agent_id','conversation_id','knowledge_base_id','document_id','lease_owner','lease_until','checkpoint_thread_id','idempotency_key'])
  const keys=new Set()
  for(const row of tableRows.value)Object.keys(row||{}).forEach(key=>{if(key!=='id'&&!hidden.has(key))keys.add(key)})
  return [...keys].sort((a,b)=>{
    const ai=priority.indexOf(a), bi=priority.indexOf(b)
    if(ai!==-1||bi!==-1)return (ai===-1?99:ai)-(bi===-1?99:bi)
    return a.localeCompare(b)
  }).slice(0,5)
})
const currentAgentConversations=computed(()=>conversations.value.filter(item=>item.agent_id===selectedAgent.value))
const currentRunApproval=computed(()=>run.value?approvals.value.find(item=>item.run_id===run.value.id&&item.status==='pending'):null)
const agentCards=computed(()=>agents.value.map((agent,index)=>({
  ...agent,
  tone:['green','amber','blue','violet'][index%4],
  specialty:agent.description||'General AI Specialist',
  checks:conversations.value.filter(item=>item.agent_id===agent.id).length,
})))
const activeAgentCount=computed(()=>agents.value.filter(agent=>agent.status!=='disabled').length)
const selectedAgent=ref(''), conversation=ref(null), question=ref(''), run=ref(null), events=ref([])
let runEventsCursor=0
const editingAgent=ref(null)
const agentName=ref(''), agentDescription=ref(''), agentRagMode=ref('none'), agentKnowledgeBaseIds=ref([]), baseName=ref(''), baseDescription=ref(''), baseScope=ref('general'), selectedBase=ref(''), uploadFile=ref(null), job=ref(null)
const newUsername=ref(''), newPassword=ref(''), newRole=ref('viewer'), newUser启用=ref(true)
const reason=ref(''), error=ref(''); let poll=null
const canManageAgents=computed(()=>['admin','operator'].includes(user.value?.role))
const canManageKnowledge=computed(()=>['admin','operator'].includes(user.value?.role))
const canReviewApprovals=computed(()=>['admin','operator'].includes(user.value?.role))
const canCreateRuns=computed(()=>['admin','operator'].includes(user.value?.role))
const canManageUsers=computed(()=>user.value?.role==='admin')
function detail(e){return e?.message||String(e)}
async function api(path, options={}) {
  const headers={...(options.headers||{})}; if(token.value)headers.Authorization='Bearer '+token.value
  if(options.body && !(options.body instanceof FormData))headers['Content-Type']='application/json'
  const res=await fetch(path,{...options,headers}); let data; const raw=await res.text()
  try{data=raw?JSON.parse(raw):null}catch{data=raw}
  if(!res.ok){const msg=typeof data?.detail==='string'?data.detail:JSON.stringify(data?.detail||data||res.status);if(res.status===401)logout();throw new Error(msg)}
  return data
}
async function login(){try{busy.value=true;const r=await api('/api/auth/login',{method:'POST',body:JSON.stringify({username:username.value,password:password.value})});token.value=r.access_token;sessionStorage.setItem('agent_token',token.value);password.value='';await initialize();ElMessage.success('登录成功')}catch(e){ElMessage.error(detail(e))}finally{busy.value=false}}
function resetRunEvents(){events.value=[];runEventsCursor=0}
function clearPendingMessage(){pendingUserMessage.value=null;pendingBeforeMessageCount.value=0}
function isMessagesNearBottom(){
  const el=messagesEl.value||document.querySelector('.chat-dock .messages')
  if(!el)return true
  return el.scrollHeight-el.scrollTop-el.clientHeight<80
}
function scrollMessagesToBottom(force=false){
  if(force)autoScrollMessages.value=true
  if(!autoScrollMessages.value)return
  nextTick(()=>{const el=messagesEl.value||document.querySelector('.chat-dock .messages');if(el)el.scrollTop=el.scrollHeight})
}
function handleMessagesScroll(){autoScrollMessages.value=isMessagesNearBottom()}
function handleMessagesWheel(event){if(event.deltaY<0)autoScrollMessages.value=false}
function attachMessagesScrollHandlers(){
  nextTick(()=>{
    const el=document.querySelector('.chat-dock .messages')
    if(!el||el.dataset.scrollBound==='1')return
    messagesEl.value=el
    el.dataset.scrollBound='1'
    el.addEventListener('scroll',handleMessagesScroll,{passive:true})
    el.addEventListener('wheel',handleMessagesWheel,{passive:true})
  })
}
function logout(){sessionStorage.removeItem('agent_token');token.value='';user.value=null;conversation.value=null;messages.value=[];conversations.value=[];run.value=null;clearPendingMessage();resetRunEvents();busy.value=false;stopPoll()}
async function initialize(){try{user.value=await api('/api/auth/me');await Promise.all([loadAgents(),loadBases(),loadApprovals(),loadConversations(),loadIngestJobs()])}catch(e){ElMessage.error(detail(e))}}
async function loadAgents(){try{agents.value=await api('/api/agents');if(!selectedAgent.value&&agents.value.length)selectedAgent.value=agents.value[0].id}catch(e){ElMessage.error(detail(e))}}
function buildAgentConfiguration(){
  const config={}
  if(agentRagMode.value==='global'){
    config.knowledge_scope='global'
    config.allowed_tools=['search_knowledge']
  }else if(agentRagMode.value==='custom'&&agentKnowledgeBaseIds.value.length){
    config.knowledge_scope='custom'
    config.knowledge_base_ids=agentKnowledgeBaseIds.value
    config.allowed_tools=['search_knowledge']
  }
  return config
}
function resetAgentForm(){editingAgent.value=null;agentName.value='';agentDescription.value='';agentRagMode.value='none';agentKnowledgeBaseIds.value=[]}
function openCreateAgent(){resetAgentForm();agentCreateOpen.value=true}
function openEditAgent(agent){editingAgent.value=agent;agentName.value=agent.name||'';agentDescription.value=agent.description||'';const config=agent.configuration||{};agentRagMode.value=config.knowledge_scope==='global'?'global':config.knowledge_base_ids?.length?'custom':'none';agentKnowledgeBaseIds.value=[...(config.knowledge_base_ids||[])];agentCreateOpen.value=true}
async function addAgent(){try{const wasEditing=!!editingAgent.value;const body={name:agentName.value,description:agentDescription.value,agent_type:'general',configuration:buildAgentConfiguration()};const a=wasEditing?await api('/api/agents/'+editingAgent.value.id,{method:'PATCH',body:JSON.stringify(body)}):await api('/api/agents',{method:'POST',body:JSON.stringify(body)});resetAgentForm();agentCreateOpen.value=false;await loadAgents();selectedAgent.value=a.id;ElMessage.success(wasEditing?'Agent 已更新':'Agent 已创建')}catch(e){ElMessage.error(detail(e))}}
async function loadConversations(){
  conversationsLoading.value=true
  try{conversations.value=await api('/api/conversations')}
  catch(e){ElMessage.error('加载历史会话失败：'+detail(e))}
  finally{conversationsLoading.value=false}
}
function changeAgent(){
  if(busy.value)return ElMessage.warning('当前任务执行中，请稍后切换 Agent')
  stopPoll();conversation.value=null;messages.value=[];run.value=null;clearPendingMessage();resetRunEvents()
}
async function selectConversation(item){
  if(busy.value)return ElMessage.warning('当前任务执行中，请稍后切换会话')
  stopPoll();conversation.value=item;messages.value=[];run.value=null;clearPendingMessage();resetRunEvents()
  try{await loadMessages()}catch(e){ElMessage.error('加载会话消息失败：'+detail(e))}
  historyOpen.value=false
}
async function newConversation(){
  if(!canCreateRuns.value){ElMessage.warning('当前角色不能新建对话任务');return false}
  if(!selectedAgent.value){ElMessage.warning('先选择 Agent');return false}
  if(busy.value){ElMessage.warning('当前任务执行中，请稍后新建会话');return false}
  try{
    const created=await api('/api/conversations',{method:'POST',body:JSON.stringify({agent_id:selectedAgent.value,title:'新会话'})})
    stopPoll();conversation.value=created;messages.value=[];run.value=null;clearPendingMessage();resetRunEvents()
    await loadConversations();ElMessage.success('新会话已创建');return true
  }catch(e){ElMessage.error(detail(e));return false}
}
async function loadMessages(forceScroll=false){
  if(!conversation.value)return
  const loaded=await api('/api/conversations/'+conversation.value.id+'/messages')
  if(pendingUserMessage.value){
    const lastUser=[...loaded].reverse().find(item=>item.role==='user')
    const pendingPersisted=loaded.length>pendingBeforeMessageCount.value&&lastUser?.content===pendingUserMessage.value.content
    if(pendingPersisted){
      clearPendingMessage()
      messages.value=loaded
    }else{
      messages.value=[...loaded,pendingUserMessage.value]
    }
  }else{
    messages.value=loaded
  }
  scrollMessagesToBottom(forceScroll)
}
function stopPoll(){if(poll){clearInterval(poll);poll=null}}
async function refreshRun(){if(!run.value)return;try{run.value=await api('/api/runs/'+run.value.id);await Promise.all([loadMessages(true),readEventsSnapshot()]);if(run.value.status==='waiting_approval')await loadApprovals();if(['completed','failed','cancelled','timed_out'].includes(run.value.status)){await readEventsSnapshot();stopPoll();busy.value=false;await loadMessages(true);await loadConversations();if(conversation.value){const latest=conversations.value.find(item=>item.id===conversation.value.id);if(latest)conversation.value=latest}}}catch(e){stopPoll();ElMessage.error(detail(e))}}
async function send(){if(!canCreateRuns.value)return ElMessage.warning('当前角色不能发送 Agent 任务');if(!question.value.trim())return;if(!selectedAgent.value)return ElMessage.warning('先创建或选择 Agent');try{busy.value=true;if(!conversation.value){busy.value=false;const created=await newConversation();if(!created)return;busy.value=true}if(!conversation.value){busy.value=false;return}const q=question.value.trim();question.value='';resetRunEvents();pendingBeforeMessageCount.value=messages.value.length;pendingUserMessage.value={id:'local-'+Date.now(),role:'user',content:q,metadata_json:null};messages.value=[...messages.value,pendingUserMessage.value];scrollMessagesToBottom(true);events.value.push({id:'local-submit',type:'run.submitted',payload:{message:'问题已发送，等待服务接收'}});run.value=await api('/api/runs',{method:'POST',body:JSON.stringify({agent_id:selectedAgent.value,conversation_id:conversation.value.id,question:q})});await Promise.all([loadMessages(true),readEventsSnapshot()]);stopPoll();poll=setInterval(refreshRun,1500)}catch(e){busy.value=false;ElMessage.error(detail(e))}}
async function cancelRun(){if(!run.value)return;try{run.value=await api('/api/runs/'+run.value.id+'/cancel',{method:'POST'});await refreshRun()}catch(e){ElMessage.error(detail(e))}}
function pushRunEvent(chunk){const idLine=chunk.split('\n').find(line=>line.startsWith('id:')),typeLine=chunk.split('\n').find(line=>line.startsWith('event:')),data=chunk.split('\n').filter(line=>line.startsWith('data:')).map(line=>line.slice(5).trim()).join('\n');if(!data)return;const id=Number(idLine?.slice(3).trim()||0);if(id&&id<=runEventsCursor)return;let payload;try{payload=JSON.parse(data)}catch{payload={message:data}};events.value.push({id,type:typeLine?.slice(6).trim()||'event',payload});if(id)runEventsCursor=Math.max(runEventsCursor,id);scrollMessagesToBottom()}
async function readEventsSnapshot(){if(!run.value)return;const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),900);try{const res=await fetch('/api/runs/'+run.value.id+'/events',{headers:{Authorization:'Bearer '+token.value,'Last-Event-ID':String(runEventsCursor)},signal:controller.signal});if(!res.ok)throw new Error('事件流 HTTP '+res.status);const reader=res.body.getReader(),decoder=new TextDecoder();let buffer='';while(true){const {done,value}=await reader.read();if(done)break;buffer+=decoder.decode(value,{stream:true}).replace(/\r\n/g,'\n');const chunks=buffer.split('\n\n');buffer=chunks.pop();for(const chunk of chunks)pushRunEvent(chunk)}}catch(e){if(e.name!=='AbortError')throw e}finally{clearTimeout(timer)}}
async function loadBases(){try{bases.value=await api('/api/knowledge/bases');if(!selectedBase.value&&bases.value.length)selectedBase.value=bases.value[0].id}catch(e){ElMessage.error(detail(e))}}
function scopeLabel(scope){return ({general:'全局通用',interview:'面试资料',policy:'制度文档',project:'项目资料'})[scope]||'全局通用'}
function baseScopeOf(base){return scopeLabel(base?.scope||'general')}
function cleanBaseDescription(base){return String(base?.description||'')||'-'}
function knowledgeSummary(agent){
  const ids=agent?.configuration?.knowledge_base_ids||[]
  if(agent?.configuration?.knowledge_scope==='global')return `全局知识库 · 当前 ${bases.value.length} 个`
  if(!ids.length)return '未启用'
  return `指定知识库 · ${ids.length} 个`
}
function knowledgeScopeDetail(agent){
  const config=agent?.configuration||{}
  if(config.knowledge_scope==='global')return `范围：全局通用 + 新增知识库自动纳入`
  const ids=config.knowledge_base_ids||[]
  if(!ids.length)return '范围：不检索知识库'
  const selected=bases.value.filter(base=>ids.includes(base.id))
  const scopes=[...new Set(selected.map(base=>baseScopeOf(base)))]
  return `范围：${scopes.length?scopes.join(' / '):'指定知识库'}`
}
async function addBase(){try{const b=await api('/api/knowledge/bases',{method:'POST',body:JSON.stringify({name:baseName.value,description:baseDescription.value,scope:baseScope.value})});baseName.value='';baseDescription.value='';baseScope.value='general';await loadBases();selectedBase.value=b.id;ElMessage.success('知识库已创建')}catch(e){ElMessage.error(detail(e))}}
async function loadIngestJobs(){jobsLoading.value=true;try{ingestJobs.value=await api('/api/knowledge/jobs')}catch(e){ElMessage.error('加载入库任务失败：'+detail(e))}finally{jobsLoading.value=false}}
function jobPercent(item){return item.total_chunks?Math.round(item.completed_chunks*100/item.total_chunks):0}
function jobStatusType(status){return ({completed:'success',failed:'danger',processing:'warning',queued:'info',cancelled:'info'})[status]||'info'}
function versionState(item){const job=Number(item.document_version||0), current=Number(item.current_version||0);if(!job||!current)return{type:'info',label:'版本未记录',hint:'当前任务没有完整版本信息'};if(job===current)return{type:'success',label:`当前检索 v${current}`,hint:'检索已使用该版本'};if(job>current&&['queued','processing'].includes(item.status))return{type:'warning',label:`待发布 v${job}`,hint:`当前检索仍使用 v${current}，新版本完成后自动切换`};if(job>current)return{type:'warning',label:`未发布 v${job}`,hint:`当前检索仍使用 v${current}`};return{type:'info',label:`历史 v${job}`,hint:`当前检索使用 v${current}`}}
async function upload(){if(!selectedBase.value||!uploadFile.value)return ElMessage.warning('请选择知识库和文件');try{const form=new FormData();form.append('file',uploadFile.value);job.value=await api('/api/knowledge/bases/'+selectedBase.value+'/documents',{method:'POST',body:form});uploadFile.value=null;await loadIngestJobs();if(job.value?.status==='duplicate')ElMessage.warning(job.value.message||'相同内容已存在，未重复入库');else ElMessage.success('已提交文档处理任务')}catch(e){ElMessage.error(detail(e))}}
async function refreshJob(){await loadIngestJobs()}
function canReindexDocument(item){return canManageKnowledge.value&&item.document_status!=='deleted'&&!['queued','processing'].includes(item.status)}
async function reindexDocument(item){try{await ElMessageBox.confirm('将使用该文档当前文件重新解析、切片和向量化。新版本完成前，旧版本仍保持可检索。确定重建？','重建索引',{type:'warning'});const result=await api('/api/knowledge/documents/'+item.document_id+'/reindex',{method:'POST'});ElMessage.success('已提交重建任务 v'+result.document_version);await loadIngestJobs()}catch(e){if(e!=='cancel')ElMessage.error(detail(e))}}
async function deleteDocument(item){try{await ElMessageBox.confirm('删除后该文档会立即从检索中排除，正在处理的入库任务也会取消。确定删除？','删除文档',{type:'warning'});await api('/api/knowledge/documents/'+item.document_id,{method:'DELETE'});ElMessage.success('文档已删除');await Promise.all([loadBases(),loadIngestJobs()])}catch(e){if(e!=='cancel')ElMessage.error(detail(e))}}
async function loadAdminOverview(){adminLoading.value=true;try{adminOverview.value=await api('/api/admin/overview')}catch(e){ElMessage.error('加载管理员数据失败：'+detail(e))}finally{adminLoading.value=false}}
function auditQuery(){const params=new URLSearchParams();for(const [key,value] of Object.entries(auditFilters.value)){if(value)params.set(key,value)}return params.toString()?'/api/audit/logs?'+params.toString():'/api/audit/logs'}
async function loadAuditLogs(){auditLoading.value=true;try{auditLogs.value=await api(auditQuery())}catch(e){ElMessage.error('加载审计日志失败：'+detail(e))}finally{auditLoading.value=false}}
function resetAuditFilters(){auditFilters.value={action:'',resource_type:'',actor:''};loadAuditLogs()}
async function loadEvalReports(){evalLoading.value=true;try{const data=await api('/api/rag/eval/reports');evalReports.value=data.reports||[];evalTrend.value=data.trend||[];evalRetestHistory.value=data.retest_history||[];if(!evalReports.value.some(item=>item.file===selectedEvalReportFile.value)){selectedEvalReportFile.value=evalReports.value[0]?.file||'';evalFailureReason.value='';evalAggregateFilter.value=null}}catch(e){ElMessage.error('加载评测报告失败：'+detail(e))}finally{evalLoading.value=false}}
async function createUser(){if(!newUsername.value.trim()||!newPassword.value)return ElMessage.warning('请填写用户名和密码');try{await api('/api/admin/users',{method:'POST',body:JSON.stringify({username:newUsername.value.trim(),password:newPassword.value,role:newRole.value,is_active:newUser启用.value})});newUsername.value='';newPassword.value='';newRole.value='viewer';newUser启用.value=true;createUserOpen.value=false;await loadAdminOverview();ElMessage.success('用户已创建')}catch(e){ElMessage.error(detail(e))}}
async function loadTableRows(table){selectedAdminTable.value=table;tableLoading.value=true;try{const data=await api('/api/admin/tables/'+table+'/rows');tableRows.value=data.rows;tableMeta.value={read_only:data.read_only,editable_fields:data.editable_fields}}catch(e){ElMessage.error('加载表数据失败：'+detail(e))}finally{tableLoading.value=false}}
async function openQueue(queue){await loadTableRows(queue.name)}
function formatCell(value){
  if(value===null||value===undefined||value==='')return '-'
  if(typeof value==='boolean')return value?'是':'否'
  if(typeof value==='object')return JSON.stringify(value)
  return String(value)
}
function columnWidth(column){
  if(['status','role','is_active','retry_count'].includes(column))return 120
  if(['created_at','updated_at'].includes(column))return 180
  if(['completed_chunks','total_chunks'].includes(column))return 150
  if(['question','answer','description','filename','title'].includes(column))return 230
  return 170
}
function eventTitle(event){
  const type=event?.type||''
  if(type==='run.stage'){
    const stage=event?.payload?.stage||''
    const stages={submitted:'问题已提交',queued:'任务已进入队列',started:'开始处理',recovering:'恢复运行',retrieving:'知识库检索',generating:'生成回答',low_confidence:'低置信度拒答',completed:'回答完成',failed:'运行失败',cancelled:'已取消',timed_out:'运行超时',waiting_approval:'等待人工审批'}
    return stages[stage]||'运行阶段'
  }
  const map={'run.submitted':'问题已提交','run.queued':'任务已进入队列','run.started':'开始处理','rag.retrieved':'知识库检索结果','tool.started':'调用工具','tool.completed':'工具完成','tool.failed':'工具失败','run.completed':'回答完成','run.failed':'运行失败','run.cancelled':'已取消','run.timed_out':'运行超时','approval.required':'等待人工审批'}
  return map[type]||type||'运行事件'
}
function eventDetail(event){
  const payload=event?.payload||{}
  const runTiming=payload.run_timing||payload.metadata?.run_timing||{}
  const runTimingText=[runTiming.total_run_ms!==undefined?`总耗时 ${Math.round(runTiming.total_run_ms)}ms`:'',runTiming.generation_ms!==undefined?`生成耗时 ${Math.round(runTiming.generation_ms)}ms`:''].filter(Boolean).join(' · ')
  const queueTimingText=[payload.queue_wait_ms!==undefined?`排队等待 ${Math.round(payload.queue_wait_ms)}ms`:'',payload.recovery_lag_ms!==undefined&&payload.recovery_lag_ms!==null?`恢复滞后 ${Math.round(payload.recovery_lag_ms)}ms`:''].filter(Boolean).join(' · ')
  if(payload.message){
    const timing=payload.retrieval_stats?.retrieval_total_ms!==undefined?` · 检索耗时 ${Math.round(payload.retrieval_stats.retrieval_total_ms)}ms`:''
    const category=payload.error_category_label?` · ${payload.error_category_label}`:payload.error_category?` · ${errorCategoryLabel(payload.error_category)}`:''
    return payload.message+category+timing+(queueTimingText?` · ${queueTimingText}`:'')+(runTimingText?` · ${runTimingText}`:'')
  }
  if(queueTimingText)return queueTimingText
  if(runTimingText)return runTimingText
  if(payload.retrieval_stats?.retrieval_total_ms!==undefined)return `检索耗时 ${Math.round(payload.retrieval_stats.retrieval_total_ms)}ms`
  if(payload.approval_wait_ms!==undefined)return `审批等待 ${Math.round(payload.approval_wait_ms)}ms`
  if(payload.hit_count!==undefined)return `命中 ${payload.hit_count} 条`
  return payload.message||payload.tool_name||payload.status||payload.error||payload.reason||''
}
function eventClass(event){const stage=event?.payload?.stage||event?.type||'';return {done:['completed','run.completed'].includes(stage),warn:['low_confidence','waiting_approval','approval.required','recovering'].includes(stage),error:['failed','timed_out','cancelled','run.failed','run.timed_out','run.cancelled'].includes(stage)}}
function errorCategoryLabel(category){
  const labels={run_timeout:'运行超时',cancelled_by_user:'用户取消',approval_error:'审批异常',rag_error:'知识库检索异常',model_error:'模型调用异常',tool_error:'工具调用异常',state_store_error:'状态存储异常',system_error:'系统异常'}
  return labels[category]||category||'系统异常'
}
function pct(value){return value===undefined||value===null?'-':(Number(value)*100).toFixed(1)+'%'}
function num(value,digits=3){return value===undefined||value===null?'-':Number(value).toFixed(digits)}
function selectedEvalReport(){return evalReports.value.find(item=>item.file===selectedEvalReportFile.value)||evalReports.value[0]||null}
function evalMetric(report,key){return report?.summary?.rank_metrics?.[key]}
function evalDelta(report,key){const delta=report?.comparison?.[key]?.delta;if(delta===undefined||delta===null)return'';const sign=delta>0?'+':'';return `${sign}${(delta*100).toFixed(1)}% vs 上份`}
function aggregateValueOf(row,group){const keys=row.failure_aggregate_keys||{};if(group==='category')return keys.category||row.failure_categories||[];if(group==='file')return keys.file||row.top_file||'未记录';if(group==='scope')return keys.scope||row.top_scope||'未记录';return''}
function matchesAggregate(row){const filter=evalAggregateFilter.value;if(!filter)return true;const value=aggregateValueOf(row,filter.group);return Array.isArray(value)?value.includes(filter.name):value===filter.name}
function evalRows(){const report=selectedEvalReport();if(!report)return[];let rows=evalCaseView.value==='all'?(report.cases||[]):(report.failures||[]);if(evalFailureReason.value)rows=rows.filter(item=>(item.failures||[]).includes(evalFailureReason.value));return rows.filter(matchesAggregate)}
function toggleAggregateFilter(group,item){const current=evalAggregateFilter.value;if(current&&current.group===group&&current.name===item.name){evalAggregateFilter.value=null;return}evalAggregateFilter.value={group,name:item.name,label:aggregateLabel(group,item)}}
function retestOf(row){return evalRetest.value[row.id]||null}
async function retestEvalCase(row){evalRetest.value={...evalRetest.value,[row.id]:{loading:true}};try{const result=await api('/api/rag/eval/case',{method:'POST',body:JSON.stringify({case:row,retrieval_mode:selectedEvalReport()?.summary?.retrieval_mode||'lexical',use_reranker:!!selectedEvalReport()?.summary?.use_reranker})});evalRetest.value={...evalRetest.value,[row.id]:{loading:false,result}};await loadEvalReports();ElMessage.success((result.passed?'复测通过':'复测失败')+(result.retest_report?'，已保存报告':''))}catch(e){evalRetest.value={...evalRetest.value,[row.id]:{loading:false,error:detail(e)}};ElMessage.error('复测失败：'+detail(e))}}
async function retestVisibleEvalCases(){const rows=evalRows();if(!rows.length)return ElMessage.warning('当前筛选没有可复测用例');evalBatchRetest.value={loading:true,result:null,error:''};try{const result=await api('/api/rag/eval/retest',{method:'POST',body:JSON.stringify({cases:rows,retrieval_mode:selectedEvalReport()?.summary?.retrieval_mode||'lexical',use_reranker:!!selectedEvalReport()?.summary?.use_reranker})});evalBatchRetest.value={loading:false,result,error:''};await loadEvalReports();ElMessage.success('批量复测完成，已保存报告 '+result.report)}catch(e){evalBatchRetest.value={loading:false,result:null,error:detail(e)};ElMessage.error('批量复测失败：'+detail(e))}}
function rankBars(){const histogram=selectedEvalReport()?.rank_histogram||{};const entries=Object.entries(histogram).map(([rank,count])=>({rank,count:Number(count)||0}));const max=Math.max(1,...entries.map(item=>item.count));return entries.map(item=>({...item,width:Math.max(4,Math.round(item.count*100/max))}))}
function trendBars(metric){return evalTrend.value.map(item=>({file:item.file,value:item[metric],label:item[metric]===undefined||item[metric]===null?'-':metric==='mrr'?num(item[metric]):pct(item[metric]),width:item[metric]===undefined||item[metric]===null?4:Math.max(4,Math.round(Number(item[metric])*100))}))}
function reportTypeLabel(type){
  const labels={eval:'正式评测',single_case_retest:'单条复测',batch_retest:'批量复测'}
  return labels[type||'eval']||type||'正式评测'
}
function failureAggregate(group){return selectedEvalReport()?.failure_aggregates?.[group]||[]}
function aggregateScopeLabel(scope){return scope&&scope!=='未记录'?scopeLabel(scope):'未记录'}
function aggregateLabel(group,item){if(group==='scope')return 'Scope: '+aggregateScopeLabel(item.name);if(group==='file')return '文件: '+item.name;return '分类: '+item.name}
function aggregateMeta(item){const total=item.total||0, rate=item.fail_rate===undefined?'-':(Number(item.fail_rate)*100).toFixed(1)+'%';return `${item.count}/${total} · ${rate}`}
function messageBlocks(content){
  const blocks=[], lines=String(content||'').split(/\r?\n/)
  let code=[], inCode=false
  for(const raw of lines){
    const line=raw.trimEnd()
    if(line.trim().startsWith('```')){
      if(inCode){blocks.push({type:'code',text:code.join('\n')});code=[];inCode=false}
      else inCode=true
      continue
    }
    if(inCode){code.push(line);continue}
    const clean=line.trim()
    if(!clean){continue}
    if(clean.startsWith('###'))blocks.push({type:'heading',text:clean.replace(/^#+\s*/,'').replace(/\*\*/g,'')})
    else if(/^[-*]\s+/.test(clean))blocks.push({type:'bullet',text:clean.replace(/^[-*]\s+/,'').replace(/\*\*/g,'')})
    else blocks.push({type:'paragraph',text:clean.replace(/\*\*/g,'')})
  }
  if(code.length)blocks.push({type:'code',text:code.join('\n')})
  return blocks.length?blocks:[{type:'paragraph',text:String(content||'')}]
}
function citations(message){return message?.metadata_json?.citations||[]}
function ragMeta(message){return message?.metadata_json?.rag||null}
function rejectedEvidence(message){return ragMeta(message)?.evidence_decision?.rejected||[]}
function ragSuggestions(message){return ragMeta(message)?.suggestions||[]}
function ragPolicyLine(message){
  const policy=ragMeta(message)?.reliability_policy
  if(!policy)return''
  const threshold=policy.threshold
  const top=policy.top_score
  const min=policy.min_score
  const ratio=policy.relative_score_ratio
  if(threshold===undefined||threshold===null)return'命中无可用分数，按检索排序展示'
  return `可靠阈值 ${num(threshold)} = max(最低 ${num(min)}, Top ${num(top)} × ${num(ratio,2)})`
}
function ragEvidenceLine(message){
  const decision=ragMeta(message)?.evidence_decision
  if(!decision)return''
  return `${decision.message||decision.decision}；原始 ${decision.raw_hit_count||0}，有分数 ${decision.scored_hit_count||0}，过滤 ${decision.rejected_hit_count||0}`
}
function retrievalStatsLine(message){
  const stats=ragMeta(message)?.retrieval_stats
  if(!stats?.retrieval_total_ms)return''
  const stage=stats.stage_ms||{}
  const parts=Object.entries(stage).map(([key,value])=>`${key} ${Math.round(value)}ms`)
  return `检索耗时 ${Math.round(stats.retrieval_total_ms)}ms${parts.length?'；'+parts.join(' / '):''}`
}
function citationMeta(hit){
  const parts=[]
  if(hit.knowledge_base_name)parts.push(hit.knowledge_base_name)
  if(hit.knowledge_base_scope)parts.push(scopeLabel(hit.knowledge_base_scope))
  if(hit.document_version!==undefined&&hit.current_version!==undefined){const doc=Number(hit.document_version), current=Number(hit.current_version);parts.push(doc===current?`当前版本 v${current}`:doc<current?`历史版本 v${doc}，当前 v${current}`:`待发布版本 v${doc}，当前 v${current}`)}
  if(hit.location_label)parts.push(hit.location_label)
  else if(hit.section_label)parts.push(hit.section_label)
  else if(hit.source_page)parts.push(`第 ${hit.source_page} 页`)
  else parts.push('定位未记录')
  return parts.join(' · ')
}
function evidenceReason(reason){
  const reasons={missing_score:'缺少分数',below_reliable_threshold:'低于可靠阈值',not_selected_for_context:'超过上下文展示上限'}
  return reasons[reason]||reason||'未采用'
}
function beginHistoryDrag(event){
  historyDrag={startX:event.clientX,startY:event.clientY,right:historyPos.value.right,bottom:historyPos.value.bottom,moved:false}
  window.addEventListener('pointermove',moveHistoryDrag)
  window.addEventListener('pointerup',endHistoryDrag,{once:true})
}
function moveHistoryDrag(event){
  if(!historyDrag)return
  const dx=event.clientX-historyDrag.startX, dy=event.clientY-historyDrag.startY
  if(Math.abs(dx)+Math.abs(dy)>4)historyDrag.moved=true
  historyPos.value={
    right:Math.max(14,Math.min(260,historyDrag.right-dx)),
    bottom:Math.max(96,Math.min(260,historyDrag.bottom-dy)),
  }
}
function endHistoryDrag(){
  window.removeEventListener('pointermove',moveHistoryDrag)
  if(historyDrag&&!historyDrag.moved)historyOpen.value=!historyOpen.value
  historyDrag=null
}
function openEditRow(row){editRow.value=row;const values={};for(const field of tableMeta.value.editable_fields){values[field]=row[field]}editJson.value=JSON.stringify(values,null,2);editDialogOpen.value=true}
async function saveEditRow(){try{const values=JSON.parse(editJson.value);await api('/api/admin/tables/'+selectedAdminTable.value+'/rows/'+editRow.value.id,{method:'PATCH',body:JSON.stringify({values})});editDialogOpen.value=false;await Promise.all([loadTableRows(selectedAdminTable.value),loadAdminOverview()]);ElMessage.success('记录已更新')}catch(e){ElMessage.error(detail(e))}}
async function deleteTableRow(row){try{await ElMessageBox.confirm('确定删除这条记录？相关会话、消息、运行事件等关联数据也会一起清理。','删除记录',{type:'warning'});const result=await api('/api/admin/tables/'+selectedAdminTable.value+'/rows/'+row.id,{method:'DELETE'});await Promise.all([loadTableRows(selectedAdminTable.value),loadAdminOverview()]);ElMessage.success('已删除 '+(result.deleted_count||1)+' 条相关数据')}catch(e){if(e!=='cancel')ElMessage.error(detail(e))}}
async function loadApprovals(){try{approvals.value=await api('/api/approvals/'+(approvalView.value==='recent'?'recent':'pending'))}catch(e){if(token.value)ElMessage.error(detail(e))}}
async function decide(item,action){try{await ElMessageBox.confirm('确定'+(action==='approve'?'批准':'拒绝')+'此工具调用？','人工审批');await api('/api/approvals/'+item.id+'/'+action,{method:'POST',body:JSON.stringify({reason:reason.value})});ElMessage.success('处理完成');await loadApprovals();if(run.value?.id===item.run_id)await refreshRun()}catch(e){if(e!=='cancel')ElMessage.error(detail(e))}}
function approvalStatusType(status){return ({pending:'warning',approved:'success',rejected:'danger',expired:'info'})[status]||'info'}
watch([messages,events],()=>scrollMessagesToBottom(),{deep:true})
watch(page,()=>{if(page.value==='chat')attachMessagesScrollHandlers()})
onMounted(()=>{attachMessagesScrollHandlers();if(token.value)initialize()});onUnmounted(stopPoll)
</script>
<template>
  <div v-if="!token" class="login panel"><h2>知识运营 Agent 控制台</h2><p class="muted">使用后端初始化的管理员账号登录</p><el-form @submit.prevent="login"><el-form-item label="用户名"><el-input v-model="username" autocomplete="username"/></el-form-item><el-form-item label="密码"><el-input v-model="password" type="password" show-password autocomplete="current-password" @keyup.enter="login"/></el-form-item><el-button type="primary" :loading="busy" style="width:100%" @click="login">登录</el-button></el-form></div>
  <div v-else class="console"><aside class="rail"><button class="rail-logo" @click="page='chat'">A</button><button :class="{active:page==='chat'}" @click="page='chat'">⌘</button><button :class="{active:page==='agents'}" @click="page='agents'">◎</button><button :class="{active:page==='knowledge'}" @click="page='knowledge'">▣</button><button :class="{active:page==='eval'}" @click="page='eval';loadEvalReports()">≋</button><button :class="{active:page==='approvals'}" @click="page='approvals'">◇</button><button v-if="canManageUsers" :class="{active:page==='audit'}" @click="page='audit';loadAuditLogs()">§</button><button v-if="canManageUsers" :class="{active:page==='admin'}" @click="page='admin';loadAdminOverview()">⚙</button><span></span><button @click="logout">↩</button></aside><section class="workspace"><header class="console-top"><div class="brand">KnowledgeOps Console</div><div class="top-actions"><el-button type="primary" size="small" :disabled="!canCreateRuns" @click="newConversation">新建测试</el-button><el-input placeholder="搜索 Agent..." style="width:250px" disabled/><button class="bell">●</button><span class="user-pill">{{user?.username}} · {{user?.role}}</span></div></header><main class="main">
  <template v-if="page==='chat'"><div class="ops-shell"><section class="ops-board"><div class="section-head"><div class="title-row"><span class="section-icon">⌘</span><h2>KnowledgeOps Console</h2><span class="stat-pill blue">{{agents.length}} 总数</span><span class="stat-pill green">{{activeAgentCount}} 启用</span><span class="stat-pill amber">{{busy?1:0}} 忙碌</span><span class="stat-pill slate">{{Math.max(0,agents.length-activeAgentCount)}} 空闲</span></div><div class="filters"><el-select v-model="selectedAgent" placeholder="全部 Agent" style="width:180px" :disabled="busy" @change="changeAgent"><el-option v-for="a in agents" :key="a.id" :label="a.name" :value="a.id"/></el-select><el-button :loading="conversationsLoading" @click="loadConversations">刷新</el-button></div></div><div class="agent-grid"><article v-for="agent in agentCards" :key="agent.id" class="agent-card" :class="agent.tone" @click="selectedAgent=agent.id;changeAgent()"><div class="card-top"><div class="avatar">{{agent.name?.slice(0,1)||'A'}}</div><div><h3>{{agent.name}}</h3><p>{{agent.specialty}}</p></div><div class="card-controls"><span></span><span></span><span></span></div></div><strong>{{agent.status==='disabled'?'Paused':'Monitoring questions'}}</strong><p class="task-line">{{currentAgentConversations.find(c=>c.agent_id===agent.id)?.title||'Ready for new intake'}}</p><footer><span>{{agent.checks}} 个历史会话</span><span>{{knowledgeSummary(agent)}}</span><span>{{knowledgeScopeDetail(agent)}}</span></footer></article><article v-if="!agentCards.length" class="agent-card empty-card"><h3>暂无 Agent</h3><p>先到 Agent 管理里创建一个 Agent，然后这里会显示工作状态。</p></article></div><section class="workflow-strip"><div><h3>工作流监控 <span>LIVE</span></h3><p>实时展示 Agent 任务、检索和审批状态</p></div><div class="legend"><b class="success">Success</b><b class="warning">Warning</b><b class="error">Error</b><b class="info">Info</b></div></section></section><aside class="chat-dock"><div class="chat-dock-head"><div><p class="eyebrow">对话</p><h2>{{conversation?.title||agents.find(a=>a.id===selectedAgent)?.name||'Select Agent'}}</h2></div><div class="chat-head-actions"><el-button :disabled="busy||!canCreateRuns" @click="newConversation">新建会话</el-button></div></div><div class="messages"><div v-if="!messages.length&&!events.length" class="empty-chat"><h3>开始对话</h3><p>选中 Agent 后输入问题。</p></div><div v-for="m in messages" :key="m.id" class="msg" :class="m.role"><b>{{m.role==='user'?'你':m.role==='assistant'?'Agent':m.role}}</b><div class="message-body"><template v-for="(block,idx) in messageBlocks(m.content)" :key="idx"><h4 v-if="block.type==='heading'">{{block.text}}</h4><p v-else-if="block.type==='bullet'" class="message-bullet">{{block.text}}</p><pre v-else-if="block.type==='code'">{{block.text}}</pre><p v-else>{{block.text}}</p></template></div><div v-if="m.role==='assistant'&&ragMeta(m)" class="rag-status" :class="{warn:ragMeta(m).low_confidence}"><strong>{{ragMeta(m).low_confidence?'低置信度':'知识库状态'}}</strong><span>{{ragMeta(m).message||('可靠命中 '+(ragMeta(m).reliable_hit_count||0)+' 条，展示 '+(ragMeta(m).display_hit_count||0)+' 条')}}</span><small v-if="ragEvidenceLine(m)">{{ragEvidenceLine(m)}}</small><small v-if="retrievalStatsLine(m)">{{retrievalStatsLine(m)}}</small><small v-if="ragPolicyLine(m)">{{ragPolicyLine(m)}}</small></div><div v-if="m.role==='assistant'&&citations(m).length" class="citation-panel"><div class="citation-title">知识库命中 {{citations(m).length}} 条</div><article v-for="hit in citations(m)" :key="hit.chunk_id" class="citation-card"><strong>{{hit.filename}}</strong><span>score {{Number(hit.score||0).toFixed(3)}} · chunk {{String(hit.chunk_id||'').slice(0,8)}}<template v-if="citationMeta(hit)"> · {{citationMeta(hit)}}</template></span><p>{{hit.preview}}</p></article></div><div v-else-if="m.role==='assistant'&&rejectedEvidence(m).length" class="citation-panel muted-evidence"><div class="citation-title">被过滤的知识库片段 {{rejectedEvidence(m).length}} 条</div><article v-for="hit in rejectedEvidence(m)" :key="hit.chunk_id" class="citation-card"><strong>{{hit.filename||'知识库文档'}}</strong><span>score {{hit.score===null||hit.score===undefined?'-':Number(hit.score).toFixed(3)}} · chunk {{String(hit.chunk_id||'').slice(0,8)}}<template v-if="citationMeta(hit)"> · {{citationMeta(hit)}}</template> · {{evidenceReason(hit.reason)}}</span></article></div></div><div v-if="events.length&&busy" class="run-trace"><div class="trace-head"><strong>本次运行过程</strong><span>{{run?.status||'running'}}</span></div><ol><li v-for="event in events" :key="event.id||event.type" :class="eventClass(event)"><b>{{eventTitle(event)}}</b><small v-if="eventDetail(event)">{{eventDetail(event)}}</small></li></ol><article v-if="currentRunApproval" class="inline-approval"><strong>需要人工审批</strong><span>{{currentRunApproval.tool_name}}</span><pre>{{JSON.stringify(currentRunApproval.arguments_json,null,2)}}</pre><div><el-button size="small" type="success" :disabled="!canReviewApprovals" @click="decide(currentRunApproval,'approve')">批准</el-button><el-button size="small" type="danger" :disabled="!canReviewApprovals" @click="decide(currentRunApproval,'reject')">拒绝</el-button><em v-if="!canReviewApprovals">当前角色只能查看审批请求</em></div></article></div></div><div class="composer"><p v-if="!canCreateRuns" class="readonly-note chat-readonly">当前角色只能查看历史对话，不能新建或发送 Agent 任务。</p><div class="composer-box"><el-input v-model="question" type="textarea" :rows="3" resize="none" placeholder="输入问题..." @keyup.ctrl.enter="send"/><el-button class="send-button" type="primary" :loading="busy" :disabled="!canCreateRuns" @click="send">发送</el-button></div><div class="composer-toolbar"><span>{{run?'运行状态：'+run.status:'Ctrl + Enter 发送'}}</span><div class="tool-buttons"><button :disabled="!run" @click="refreshRun">刷新状态</button><button v-if="busy" class="danger-tool" :disabled="!run" @click="cancelRun">取消</button></div></div></div><div v-if="historyOpen" class="history-backdrop" @click="historyOpen=false"></div><div class="history-burst" :class="{open:historyOpen}" :style="{right:historyPos.right+'px',bottom:historyPos.bottom+'px'}"><button class="history-fab" title="拖动或点击查看历史" @pointerdown.prevent="beginHistoryDrag"><span>🎉</span></button><button v-for="(item,index) in currentAgentConversations.slice(0,6)" :key="item.id" class="burst-card" :class="{active:conversation?.id===item.id}" :style="{transform:historyOpen?`translate(${[-64,-64,-64,-64,-64,-64][index]||-64}px, ${[-4,-62,-120,-178,-236,-294][index]||-120}px) rotate(${[0,0,0,0,0,0][index]||0}deg)`:''}" @click="selectConversation(item)"><strong>{{item.title||'未命名会话'}}</strong><span>{{conversation?.id===item.id?'当前':'打开'}}</span></button><button v-if="historyOpen" class="burst-refresh" @click="loadConversations">刷新</button></div></aside></div><el-alert v-if="run?.error_message" type="error" :title="run.error_message" style="margin-top:12px"/></template>
  <template v-else-if="page==='agents'">
    <div class="agent-manage">
      <section class="panel">
        <div class="panel-head">
          <div><h3>Agent 列表</h3><p class="muted">查看已创建的 Agent，确认是否启用了知识库。</p></div>
          <div class="panel-actions"><el-button v-if="canManageAgents" type="primary" @click="openCreateAgent">创建 Agent</el-button><el-button @click="loadAgents">刷新列表</el-button></div>
        </div>
        <div v-if="!agents.length" class="empty-state compact-empty"><strong>暂无 Agent</strong><span>先创建一个 Agent，再进入对话测试。</span></div>
        <el-table v-else :data="agents" class="data-table page-table" empty-text="暂无 Agent">
          <el-table-column prop="name" label="名称" min-width="180"/>
          <el-table-column prop="description" label="描述" min-width="220" show-overflow-tooltip/>
          <el-table-column prop="agent_type" label="类型" width="120"/>
          <el-table-column prop="status" label="状态" width="120"/>
          <el-table-column label="知识库" min-width="180"><template #default="s"><span>{{knowledgeSummary(s.row)}}</span></template></el-table-column>
          <el-table-column label="操作" width="150"><template #default="s"><el-button v-if="canManageAgents" link type="primary" @click="openEditAgent(s.row)">修改</el-button><el-button link type="primary" :disabled="busy" @click="selectedAgent=s.row.id;page='chat';changeAgent()">对话</el-button></template></el-table-column>
        </el-table>
      </section>
      <el-dialog v-model="agentCreateOpen" :title="editingAgent?'修改 Agent':'创建 Agent'" width="640px" class="agent-dialog">
        <div class="agent-create-form dialog-form">
          <label><span>名称</span><el-input v-model="agentName" placeholder="例如：Python 面试助手"/></label>
          <label><span>描述</span><el-input v-model="agentDescription" placeholder="说明这个 Agent 负责什么"/></label>
          <label><span>知识库策略</span><el-select v-model="agentRagMode" placeholder="选择知识库策略"><el-option label="不使用知识库" value="none"/><el-option :label="'全局知识库（全部 '+bases.length+' 个）'" value="global"/><el-option label="指定知识库（可多选）" value="custom"/></el-select></label>
          <label v-if="agentRagMode==='custom'" class="full-field"><span>指定知识库</span><el-select v-model="agentKnowledgeBaseIds" multiple placeholder="可多选知识库"><el-option v-for="b in bases" :key="b.id" :label="b.name+' · '+baseScopeOf(b)" :value="b.id"/></el-select></label>
          <p class="rag-hint full-field">{{agentRagMode==='global'?'会关联当前租户下所有知识库，包括之后新增的全局资料。':agentRagMode==='custom'?'可同时选择多个知识库，Agent 只检索选中的范围。':'该 Agent 不会检索知识库，只按模型自身能力回答。'}}</p>
        </div>
        <template #footer>
          <el-button @click="agentCreateOpen=false;resetAgentForm()">取消</el-button>
          <el-button type="primary" :disabled="!agentName.trim()" @click="addAgent">{{editingAgent?'保存修改':'创建 Agent'}}</el-button>
        </template>
      </el-dialog>
    </div>
  </template>
  <template v-else-if="page==='knowledge'">
    <div class="panel">
      <h2>知识库</h2>
      <div class="command-panel kb-create-panel">
        <label class="command-field"><span>名称</span><el-input v-model="baseName" placeholder="知识库名称"/></label>
        <label class="command-field scope-field"><span>范围</span><el-select v-model="baseScope" placeholder="范围">
          <el-option label="全局通用" value="general"/>
          <el-option label="面试资料" value="interview"/>
          <el-option label="制度文档" value="policy"/>
          <el-option label="项目资料" value="project"/>
        </el-select></label>
        <label class="command-field description-field"><span>描述</span><el-input v-model="baseDescription" placeholder="简单说明这批资料的用途"/></label>
        <div class="command-actions">
          <el-button v-if="canManageKnowledge" type="primary" :disabled="!baseName.trim()" @click="addBase">创建知识库</el-button>
          <el-button @click="loadBases">刷新</el-button>
        </div>
      </div>
      <p v-if="!canManageKnowledge" class="readonly-note">当前角色仅可查看知识库，不能创建或上传文档。</p>
      <div v-if="!bases.length" class="empty-state compact-empty"><strong>暂无知识库</strong><span>先创建知识库，再上传文档。</span></div>
      <el-table v-else :data="bases" class="data-table page-table">
        <el-table-column prop="name" label="名称" min-width="180"/>
        <el-table-column label="范围" width="130"><template #default="s"><el-tag type="info">{{baseScopeOf(s.row)}}</el-tag></template></el-table-column>
        <el-table-column label="描述" min-width="260"><template #default="s">{{cleanBaseDescription(s.row)}}</template></el-table-column>
        <el-table-column prop="id" label="ID"/>
      </el-table>
    </div>
    <div class="panel">
      <div class="panel-head">
        <div>
          <h3>上传文档</h3>
          <p class="muted">提交后会在下方任务表中显示入库进度。</p>
        </div>
        <el-button :loading="jobsLoading" @click="loadIngestJobs">刷新任务</el-button>
      </div>
      <div class="command-panel upload-panel">
        <label class="command-field upload-base-field"><span>知识库</span><el-select v-model="selectedBase" placeholder="选择知识库">
          <el-option v-for="b in bases" :key="b.id" :label="b.name" :value="b.id"/>
        </el-select></label>
        <label class="command-field file-field"><span>文档</span>
        <label class="file-picker">
          <input type="file" @change="uploadFile=$event.target.files?.[0]||null"/>
          <span>{{uploadFile?.name||'选择文件'}}</span>
        </label>
        </label>
        <div class="command-actions">
          <el-button v-if="canManageKnowledge" type="primary" :disabled="!selectedBase||!uploadFile" @click="upload">提交入库</el-button>
        </div>
      </div>
      <div v-if="!ingestJobs.length&&!jobsLoading" class="empty-state compact-empty"><strong>暂无入库任务</strong><span>选择知识库和文件后，任务进度会显示在这里。</span></div>
      <el-table v-else :data="ingestJobs" v-loading="jobsLoading" class="task-table data-table" empty-text="暂无入库任务">
        <el-table-column prop="filename" label="文件" min-width="220" show-overflow-tooltip/>
        <el-table-column prop="status" label="任务状态" width="120">
          <template #default="s">
            <el-tag :type="jobStatusType(s.row.status)">{{s.row.status}}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="document_status" label="文档状态" width="120">
          <template #default="s">
            <el-tag :type="s.row.document_status==='deleted'?'danger':'info'">{{s.row.document_status}}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="检索版本" width="180">
          <template #default="s">
            <el-tooltip :content="versionState(s.row).hint" placement="top">
              <el-tag :type="versionState(s.row).type">{{versionState(s.row).label}}</el-tag>
            </el-tooltip>
          </template>
        </el-table-column>
        <el-table-column label="分片进度" min-width="220">
          <template #default="s">
            <el-progress :percentage="jobPercent(s.row)" :status="s.row.status==='failed'?'exception':s.row.status==='completed'?'success':undefined"/>
            <span class="chunk-count">{{s.row.completed_chunks}} / {{s.row.total_chunks||'-'}}</span>
          </template>
        </el-table-column>
        <el-table-column prop="retry_count" label="重试" width="80"/>
        <el-table-column prop="last_error" label="错误信息" min-width="220" show-overflow-tooltip>
          <template #default="s">
            <span class="error-text">{{s.row.last_error||'-'}}</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="210">
          <template #default="s">
            <el-button link type="primary" @click="refreshJob">刷新</el-button>
            <el-button v-if="canManageKnowledge" link type="primary" :disabled="!canReindexDocument(s.row)" @click="reindexDocument(s.row)">重建</el-button>
            <el-button v-if="canManageKnowledge" link type="danger" :disabled="s.row.document_status==='deleted'" @click="deleteDocument(s.row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>
  </template>
  <template v-else-if="page==='eval'">
    <div class="eval-console">
      <section class="panel eval-head">
        <div>
          <p class="eyebrow">RAG Evaluation</p>
          <h2>评测结果</h2>
          <p class="muted">读取 evaluation/results 下最近的报告，关注检索排序、失败样本和回归趋势。</p>
        </div>
        <el-button :loading="evalLoading" @click="loadEvalReports">刷新报告</el-button>
      </section>
      <section v-if="selectedEvalReport()" class="eval-metrics">
        <div class="metric-card" :title="selectedEvalReport().metric_notes?.pass_rate"><span>通过率</span><strong>{{pct(selectedEvalReport().summary?.pass_rate)}}</strong><small>{{evalDelta(selectedEvalReport(),'pass_rate')}}</small></div>
        <div class="metric-card"><span>通过 / 总数</span><strong>{{selectedEvalReport().summary?.passed}} / {{selectedEvalReport().summary?.total}}</strong></div>
        <div class="metric-card" :title="selectedEvalReport().metric_notes?.['hit@1']"><span>Hit@1</span><strong>{{pct(evalMetric(selectedEvalReport(),'hit@1'))}}</strong><small>{{evalDelta(selectedEvalReport(),'hit@1')}}</small></div>
        <div class="metric-card" :title="selectedEvalReport().metric_notes?.mrr"><span>MRR</span><strong>{{num(evalMetric(selectedEvalReport(),'mrr'))}}</strong><small>{{selectedEvalReport().comparison?.mrr?.delta===undefined||selectedEvalReport().comparison?.mrr?.delta===null?'':(selectedEvalReport().comparison.mrr.delta>0?'+':'')+num(selectedEvalReport().comparison.mrr.delta)+' vs 上份'}}</small></div>
      </section>
      <section class="eval-layout">
        <aside class="panel eval-report-list" v-loading="evalLoading">
          <h3>最近报告</h3>
          <article v-for="report in evalReports" :key="report.file" class="eval-report-card" :class="{active:report.file===selectedEvalReportFile}" @click="selectedEvalReportFile=report.file;evalFailureReason=''">
            <strong>{{report.file}}</strong>
            <span>{{reportTypeLabel(report.report_type)}}<template v-if="report.case_id"> · {{report.case_id}}</template></span>
            <span>{{report.summary?.retrieval_mode||'-'}} · reranker {{report.summary?.use_reranker?'on':'off'}}</span>
            <b>{{pct(report.summary?.pass_rate)}} · {{report.summary?.passed}}/{{report.summary?.total}}</b>
          </article>
          <div v-if="!evalReports.length&&!evalLoading" class="empty-state compact-empty"><strong>暂无报告</strong><span>运行 evaluation/rag_eval_runner.py 后会显示在这里。</span></div>
        </aside>
        <section class="panel eval-detail">
          <div class="panel-head">
            <div>
              <h3>{{selectedEvalReport()?.file||'暂无评测报告'}}</h3>
              <p class="muted">失败用例用于定位切片、召回、排序和同义表达问题。</p>
            </div>
          </div>
          <div v-if="selectedEvalReport()" class="rank-grid">
            <span :title="selectedEvalReport().metric_notes?.['hit@3']">Hit@3 <b>{{pct(evalMetric(selectedEvalReport(),'hit@3'))}}</b></span>
            <span :title="selectedEvalReport().metric_notes?.['hit@5']">Hit@5 <b>{{pct(evalMetric(selectedEvalReport(),'hit@5'))}}</b></span>
            <span>失败数 <b>{{selectedEvalReport().summary?.failed}}</b></span>
            <span>模式 <b>{{selectedEvalReport().summary?.retrieval_mode}}</b></span>
          </div>
          <div v-if="selectedEvalReport()" class="metric-notes">
            <strong>这些参数怎么来的</strong>
            <p>通过率来自该报告的 <code>summary.pass_rate</code>；Hit@1/3/5 和 MRR 来自 <code>summary.rank_metrics</code>，由评测脚本根据每条 case 的标准锚点在返回结果里的排名计算。</p>
            <p>报告类型：{{reportTypeLabel(selectedEvalReport().report_type)}}<template v-if="selectedEvalReport().case_id">；复测用例：{{selectedEvalReport().case_id}}</template></p>
            <p v-if="selectedEvalReport().comparison?.previous_file">对比对象：{{selectedEvalReport().comparison.previous_file}}</p>
          </div>
          <div v-if="evalRetestHistory.length" class="eval-retest-history">
            <h4>最近复测历史</h4>
            <button v-for="item in evalRetestHistory" :key="item.file" :class="{active:item.file===selectedEvalReportFile}" @click="selectedEvalReportFile=item.file;evalFailureReason='';evalAggregateFilter=null">
              <strong>{{reportTypeLabel(item.report_type)}}</strong>
              <span>{{item.case_id||'批量'}} · {{pct(item.summary?.pass_rate)}} · {{item.summary?.passed}}/{{item.summary?.total}}</span>
              <small>{{item.file}}</small>
            </button>
          </div>
          <div v-if="evalTrend.length" class="eval-trend">
            <section>
              <h4>通过率趋势</h4>
              <div v-for="item in trendBars('pass_rate')" :key="'pass-'+item.file" class="trend-row"><span>{{item.file}}</span><i><b :style="{width:item.width+'%'}"></b></i><strong>{{item.label}}</strong></div>
            </section>
            <section>
              <h4>Hit@1 趋势</h4>
              <div v-for="item in trendBars('hit@1')" :key="'hit1-'+item.file" class="trend-row"><span>{{item.file}}</span><i><b :style="{width:item.width+'%'}"></b></i><strong>{{item.label}}</strong></div>
            </section>
            <section>
              <h4>MRR 趋势</h4>
              <div v-for="item in trendBars('mrr')" :key="'mrr-'+item.file" class="trend-row"><span>{{item.file}}</span><i><b :style="{width:item.width+'%'}"></b></i><strong>{{item.label}}</strong></div>
            </section>
          </div>
          <div v-if="selectedEvalReport()" class="eval-insights">
            <section>
              <h4>Rank 分布</h4>
              <div v-if="rankBars().length" class="rank-bars">
                <div v-for="item in rankBars()" :key="item.rank" class="rank-bar-row">
                  <span>#{{item.rank}}</span>
                  <i><b :style="{width:item.width+'%'}"></b></i>
                  <strong>{{item.count}}</strong>
                </div>
              </div>
              <p v-else class="muted">本报告没有 rank histogram。</p>
            </section>
            <section>
              <h4>失败原因</h4>
              <div v-if="selectedEvalReport().failure_groups?.length" class="failure-groups">
                <button v-for="item in selectedEvalReport().failure_groups" :key="item.reason" :class="{active:evalFailureReason===item.reason}" @click="evalFailureReason=evalFailureReason===item.reason?'':item.reason">{{item.reason}} <b>{{item.count}}</b></button>
              </div>
              <p v-else class="muted">本报告暂无失败原因。</p>
            </section>
            <section>
              <h4>失败聚合</h4>
              <div class="failure-groups">
                <button v-for="item in failureAggregate('scope')" :key="'scope-'+item.name" :class="{active:evalAggregateFilter?.group==='scope'&&evalAggregateFilter?.name===item.name}" :title="'样例：'+(item.examples||[]).join('，')" @click="toggleAggregateFilter('scope',item)">Scope: {{aggregateScopeLabel(item.name)}} <b>{{aggregateMeta(item)}}</b></button>
                <button v-for="item in failureAggregate('file')" :key="'file-'+item.name" :class="{active:evalAggregateFilter?.group==='file'&&evalAggregateFilter?.name===item.name}" :title="'样例：'+(item.examples||[]).join('，')" @click="toggleAggregateFilter('file',item)">文件: {{item.name}} <b>{{aggregateMeta(item)}}</b></button>
                <button v-for="item in failureAggregate('category')" :key="'category-'+item.name" :class="{active:evalAggregateFilter?.group==='category'&&evalAggregateFilter?.name===item.name}" :title="'样例：'+(item.examples||[]).join('，')" @click="toggleAggregateFilter('category',item)">分类: {{item.name}} <b>{{aggregateMeta(item)}}</b></button>
              </div>
              <p v-if="!failureAggregate('scope').length&&!failureAggregate('file').length&&!failureAggregate('category').length" class="muted">本报告暂无失败聚合。</p>
            </section>
          </div>
          <div v-if="selectedEvalReport()" class="case-toolbar">
            <el-segmented v-model="evalCaseView" :options="[{label:'失败用例',value:'failures'},{label:'全部用例',value:'all'}]"/>
            <span>{{evalRows().length}} 条<template v-if="evalAggregateFilter"> · {{evalAggregateFilter.label}}</template></span>
            <el-button v-if="evalAggregateFilter" size="small" @click="evalAggregateFilter=null">清除聚合筛选</el-button>
            <el-button size="small" type="primary" :loading="evalBatchRetest.loading" @click="retestVisibleEvalCases">批量复测当前筛选</el-button>
            <span v-if="evalBatchRetest.result">报告 {{evalBatchRetest.result.report}} · {{evalBatchRetest.result.summary?.passed}}/{{evalBatchRetest.result.summary?.total}}</span>
          </div>
          <el-table v-if="selectedEvalReport()" :data="evalRows()" class="data-table page-table" empty-text="暂无用例">
            <el-table-column type="expand" width="42">
              <template #default="s">
                <div class="eval-hit-list" v-if="s.row.top_results?.length">
                  <article v-for="(hit,index) in s.row.top_results" :key="hit.chunk_id||index">
                    <strong>#{{index+1}} {{hit.filename||'未知文件'}}</strong>
                    <span>{{hit.knowledge_base_name||'知识库'}}<template v-if="hit.knowledge_base_scope"> · {{scopeLabel(hit.knowledge_base_scope)}}</template><template v-if="hit.document_version!==undefined&&hit.current_version!==undefined"> · {{citationMeta(hit).split(' · ').find(part=>part.includes('版本'))}}</template> · score {{hit.score===undefined||hit.score===null?'-':Number(hit.score).toFixed(3)}} · chunk {{String(hit.chunk_id||'-').slice(0,8)}}<template v-if="hit.section_label"> · {{hit.section_label}}</template><template v-if="hit.source_page"> · 第 {{hit.source_page}} 页</template><template v-if="!hit.section_label&&!hit.source_page"> · 定位未记录</template></span>
                    <p>{{hit.preview||'无预览'}}</p>
                  </article>
                </div>
                <p v-else class="muted">本用例没有返回 top chunk 明细。</p>
                <div v-if="retestOf(s.row)?.result" class="eval-retest">
                  <strong>复测结果：{{retestOf(s.row).result.passed?'通过':'失败'}}</strong>
                  <span>耗时 {{retestOf(s.row).result.elapsed_ms}}ms · Hits {{retestOf(s.row).result.hit_count}} · Rank {{retestOf(s.row).result.anchor_rank||'-'}}</span>
                  <span v-if="retestOf(s.row).result.retest_report">报告 {{retestOf(s.row).result.retest_report}}</span>
                  <p v-if="retestOf(s.row).result.failures?.length">{{retestOf(s.row).result.failures.join('；')}}</p>
                  <article v-for="(hit,index) in retestOf(s.row).result.top_results" :key="hit.chunk_id||index">
                    <strong>#{{index+1}} {{hit.filename||'未知文件'}}</strong>
                    <span>score {{hit.score===undefined||hit.score===null?'-':Number(hit.score).toFixed(3)}} · chunk {{String(hit.chunk_id||'-').slice(0,8)}}<template v-if="hit.section_label"> · {{hit.section_label}}</template><template v-if="hit.source_page"> · 第 {{hit.source_page}} 页</template><template v-if="!hit.section_label&&!hit.source_page"> · 定位未记录</template></span>
                    <p>{{hit.preview||hit.text||'无预览'}}</p>
                  </article>
                </div>
                <p v-else-if="retestOf(s.row)?.error" class="muted">复测错误：{{retestOf(s.row).error}}</p>
              </template>
            </el-table-column>
            <el-table-column v-if="evalCaseView==='all'" label="结果" width="90">
              <template #default="s"><el-tag :type="s.row.passed?'success':'danger'">{{s.row.passed?'通过':'失败'}}</el-tag></template>
            </el-table-column>
            <el-table-column prop="id" label="ID" width="170" show-overflow-tooltip/>
            <el-table-column prop="query" label="问题" min-width="260" show-overflow-tooltip/>
            <el-table-column prop="rank" label="Rank" width="90"/>
            <el-table-column prop="hit_count" label="Hits" width="90"/>
            <el-table-column prop="top_score" label="Top score" width="110">
              <template #default="s">{{s.row.top_score===undefined||s.row.top_score===null?'-':Number(s.row.top_score).toFixed(3)}}</template>
            </el-table-column>
            <el-table-column prop="elapsed_ms" label="耗时" width="100">
              <template #default="s">{{s.row.elapsed_ms===undefined||s.row.elapsed_ms===null?'-':Math.round(s.row.elapsed_ms)+'ms'}}</template>
            </el-table-column>
            <el-table-column prop="top_file" label="Top file" min-width="200" show-overflow-tooltip/>
            <el-table-column label="Scope" width="120">
              <template #default="s">{{aggregateScopeLabel(s.row.top_scope||s.row.failure_aggregate_keys?.scope)}}</template>
            </el-table-column>
            <el-table-column label="分类" min-width="160" show-overflow-tooltip>
              <template #default="s">{{(s.row.failure_categories||[]).join(' / ')||'-'}}</template>
            </el-table-column>
            <el-table-column label="失败原因" min-width="260" show-overflow-tooltip>
              <template #default="s">{{(s.row.failures||[]).join('；')||'-'}}</template>
            </el-table-column>
            <el-table-column label="操作" width="110">
              <template #default="s"><el-button link type="primary" :loading="!!retestOf(s.row)?.loading" @click="retestEvalCase(s.row)">复测</el-button></template>
            </el-table-column>
          </el-table>
        </section>
      </section>
    </div>
  </template>
  <template v-else-if="page==='approvals'">
    <div class="panel">
      <div class="panel-head">
        <div>
          <h2>人工审批</h2>
          <p class="muted">在这里处理待审批工具调用，也可以回看最近审批结果。</p>
        </div>
        <el-radio-group v-model="approvalView" @change="loadApprovals">
          <el-radio-button label="pending">待审批</el-radio-button>
          <el-radio-button label="recent">最近记录</el-radio-button>
        </el-radio-group>
      </div>
      <div class="command-panel approval-toolbar">
        <label class="command-field approval-reason">
          <span>审批备注</span>
          <el-input v-model="reason" :disabled="!canReviewApprovals" placeholder="审批原因（可选）"/>
        </label>
        <div class="command-actions"><el-button @click="loadApprovals">刷新</el-button></div>
      </div>
      <p v-if="!canReviewApprovals" class="readonly-note">当前角色仅可查看审批任务，不能批准或拒绝。</p>
      <div v-if="!approvals.length" class="empty-state compact-empty">
        <strong>{{approvalView==='recent'?'暂无审批记录':'暂无待审批任务'}}</strong>
        <span>{{approvalView==='recent'?'审批通过或拒绝后会保留在这里。':'需要人工确认的工具调用会出现在这里。'}}</span>
      </div>
      <el-table v-else :data="approvals" class="data-table page-table" empty-text="暂无审批任务">
        <el-table-column prop="tool_name" label="工具" width="170"/>
        <el-table-column label="状态" width="120">
          <template #default="s"><el-tag :type="approvalStatusType(s.row.status)">{{s.row.status}}</el-tag></template>
        </el-table-column>
        <el-table-column prop="run_id" label="运行 ID" width="260" show-overflow-tooltip/>
        <el-table-column label="参数" min-width="260">
          <template #default="s"><pre class="json">{{JSON.stringify(s.row.arguments_json,null,2)}}</pre></template>
        </el-table-column>
        <el-table-column prop="review_reason" label="备注" min-width="160" show-overflow-tooltip/>
        <el-table-column label="等待耗时" width="120">
          <template #default="s">{{s.row.approval_wait_ms===undefined||s.row.approval_wait_ms===null?'-':Math.round(s.row.approval_wait_ms)+'ms'}}</template>
        </el-table-column>
        <el-table-column prop="reviewed_at" label="处理时间" width="190" show-overflow-tooltip/>
        <el-table-column label="操作" width="170">
          <template #default="s">
            <el-button v-if="canReviewApprovals&&s.row.status==='pending'" link type="success" @click="decide(s.row,'approve')">批准</el-button>
            <el-button v-if="canReviewApprovals&&s.row.status==='pending'" link type="danger" @click="decide(s.row,'reject')">拒绝</el-button>
            <span v-if="s.row.status!=='pending'||!canReviewApprovals" class="muted">{{s.row.status==='pending'?'只读':'已处理'}}</span>
          </template>
        </el-table-column>
      </el-table>
    </div>
  </template>
  <template v-else-if="page==='audit'">
    <div class="panel">
      <div class="panel-head">
        <div>
          <p class="eyebrow">Audit Trail</p>
          <h2>审计日志</h2>
          <p class="muted">记录 Agent、知识库、文档和审批等关键操作。</p>
        </div>
        <el-button :loading="auditLoading" @click="loadAuditLogs">刷新日志</el-button>
      </div>
      <div class="command-panel audit-toolbar">
        <label class="command-field"><span>动作</span><el-select v-model="auditFilters.action" clearable placeholder="全部动作">
          <el-option label="创建 Agent" value="agent.create"/>
          <el-option label="修改 Agent" value="agent.update"/>
          <el-option label="创建知识库" value="knowledge_base.create"/>
          <el-option label="上传文档" value="document.upload"/>
          <el-option label="重复上传" value="document.upload_duplicate"/>
          <el-option label="重建索引" value="document.reindex"/>
          <el-option label="删除文档" value="document.delete"/>
          <el-option label="审批通过" value="approval.approved"/>
          <el-option label="审批拒绝" value="approval.rejected"/>
        </el-select></label>
        <label class="command-field"><span>资源</span><el-select v-model="auditFilters.resource_type" clearable placeholder="全部资源">
          <el-option label="Agent" value="agent"/>
          <el-option label="知识库" value="knowledge_base"/>
          <el-option label="知识文档" value="knowledge_document"/>
          <el-option label="审批请求" value="approval_request"/>
        </el-select></label>
        <label class="command-field"><span>操作者</span><el-input v-model="auditFilters.actor" clearable placeholder="用户名"/></label>
        <div class="command-actions">
          <el-button type="primary" :loading="auditLoading" @click="loadAuditLogs">查询</el-button>
          <el-button @click="resetAuditFilters">重置</el-button>
        </div>
      </div>
      <el-table :data="auditLogs" v-loading="auditLoading" class="data-table page-table" empty-text="暂无审计日志">
        <el-table-column prop="created_at" label="时间" width="190" show-overflow-tooltip/>
        <el-table-column prop="actor_username" label="操作者" width="130"/>
        <el-table-column prop="action" label="动作" width="190"/>
        <el-table-column prop="resource_type" label="资源" width="150"/>
        <el-table-column prop="summary" label="摘要" min-width="260" show-overflow-tooltip/>
        <el-table-column label="详情" min-width="260" show-overflow-tooltip>
          <template #default="s">{{JSON.stringify(s.row.metadata_json||{})}}</template>
        </el-table-column>
      </el-table>
    </div>
  </template>
  <template v-else-if="page==='admin'">
    <div class="admin-console">
      <section class="admin-hero">
        <div>
          <p class="eyebrow">Tenant Console</p>
          <h2>管理员工作台</h2>
          <p>数据表、运行队列和用户管理分区展示，避免把不同类型的信息混成一张表。</p>
        </div>
        <div class="admin-hero-actions">
          <el-button v-if="canManageUsers" type="primary" @click="createUserOpen=true">新增用户</el-button>
          <el-button :loading="adminLoading" @click="loadAdminOverview">刷新控制台</el-button>
        </div>
      </section>
      <section class="admin-metrics">
        <div class="metric-card"><span>数据表</span><strong>{{adminOverview.tables.length}}</strong></div>
        <div class="metric-card"><span>总记录</span><strong>{{adminOverview.tables.reduce((sum,item)=>sum+item.rows,0)}}</strong></div>
        <div class="metric-card"><span>用户</span><strong>{{adminOverview.users.length}}</strong></div>
        <div class="metric-card"><span>队列</span><strong>{{adminOverview.queues.length}}</strong></div>
      </section>
      <section class="queue-cards" v-loading="adminLoading">
        <article v-for="queue in adminOverview.queues" :key="queue.name" class="queue-card" :class="{active:selectedAdminTable===queue.name}" @click="openQueue(queue)">
          <p>{{queue.description}}</p>
          <h3>{{queue.name}}</h3>
          <div class="status-chips">
            <el-tag v-for="item in queue.statuses" :key="item.status" type="info">{{item.status}}: {{item.count}}</el-tag>
            <span v-if="!queue.statuses.length" class="muted">暂无任务</span>
          </div>
          <span class="queue-open">查看队列数据</span>
        </article>
      </section>
      <section class="admin-browser">
        <aside class="admin-sidebar">
          <div class="sidebar-card">
            <h3>数据表</h3>
            <button v-for="table in adminOverview.tables" :key="table.name" class="table-nav-item" :class="{active:selectedAdminTable===table.name}" @click="loadTableRows(table.name)">
              <span>{{table.name}}</span>
              <b>{{table.rows}}</b>
            </button>
          </div>
          <div class="sidebar-card users-card">
            <div class="sidebar-title-row">
              <h3>用户</h3>
              <button v-if="canManageUsers" @click="createUserOpen=true">新增</button>
            </div>
            <div class="user-mini-list">
              <article v-for="item in adminOverview.users" :key="item.id" class="user-mini-card">
                <div>
                  <strong>{{item.username}}</strong>
                  <span>{{item.role}}</span>
                </div>
                <b :class="{off:!item.is_active}">{{item.is_active?'启用':'停用'}}</b>
              </article>
              <p v-if="!adminOverview.users.length" class="muted">暂无用户</p>
            </div>
          </div>
        </aside>
        <section class="data-workbench">
          <div class="panel-head">
            <div>
              <p class="eyebrow">Data Browser</p>
              <h3>{{selectedAdminTable||'选择一张表'}}</h3>
              <p class="muted">右侧展示最近 100 条记录。队列状态在上方独立展示，因为队列是运行状态，不是数据表本身。</p>
            </div>
            <el-button :disabled="!selectedAdminTable" :loading="tableLoading" @click="loadTableRows(selectedAdminTable)">刷新数据</el-button>
          </div>
          <el-table :data="tableRows" v-loading="tableLoading" class="task-table data-table" empty-text="请从左侧选择表" height="560">
            <el-table-column prop="id" label="ID" width="190" show-overflow-tooltip/>
            <el-table-column v-for="column in tableColumns" :key="column" :prop="column" :label="column" :min-width="columnWidth(column)" show-overflow-tooltip>
              <template #default="s">
                <span class="cell-value" :class="{status:column==='status'}">{{formatCell(s.row[column])}}</span>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="150" align="center" class-name="action-column">
              <template #default="s">
                <div class="table-actions">
                  <el-button link type="primary" :disabled="tableMeta.read_only||!tableMeta.editable_fields.length" @click="openEditRow(s.row)">更新</el-button>
                  <el-button link type="danger" :disabled="tableMeta.read_only" @click="deleteTableRow(s.row)">删除</el-button>
                </div>
              </template>
            </el-table-column>
          </el-table>
        </section>
      </section>
    </div>
    <el-dialog v-model="editDialogOpen" title="更新记录" width="680px">
      <p class="muted">只允许修改该表的安全字段：{{tableMeta.editable_fields.join(', ')||'无'}}</p>
      <el-input v-model="editJson" type="textarea" :rows="12"/>
      <template #footer>
        <el-button @click="editDialogOpen=false">取消</el-button>
        <el-button type="primary" @click="saveEditRow">保存</el-button>
      </template>
    </el-dialog>
    <el-dialog v-model="createUserOpen" title="新增用户" width="520px" class="user-dialog">
      <div class="create-user-dialog">
        <label><span>用户名</span><el-input v-model="newUsername" placeholder="例如：operator_01"/></label>
        <label><span>初始密码</span><el-input v-model="newPassword" type="password" show-password placeholder="至少 12 位，建议包含字母和数字"/></label>
        <label><span>角色</span><el-select v-model="newRole" placeholder="选择角色">
          <el-option label="管理员 admin" value="admin"/>
          <el-option label="操作员 operator" value="operator"/>
          <el-option label="只读 viewer" value="viewer"/>
        </el-select></label>
        <div class="role-hint">
          <strong>{{newRole}}</strong>
          <span>{{newRole==='admin'?'可管理用户、数据和系统配置':newRole==='operator'?'可处理 Agent、知识库和审批任务':'仅查看数据与运行状态'}}</span>
        </div>
        <label class="switch-row"><span>账号状态</span><el-switch v-model="newUser启用" active-text="启用" inactive-text="停用"/></label>
      </div>
      <template #footer>
        <el-button @click="createUserOpen=false">取消</el-button>
        <el-button type="primary" :disabled="!newUsername.trim()||!newPassword" @click="createUser">创建用户</el-button>
      </template>
    </el-dialog>
  </template>
  </main></section></div>
</template>

<style scoped>
.console{display:grid;grid-template-columns:58px minmax(0,1fr);min-height:100vh;background:#080d17;color:#d8e3f7}
.rail{display:grid;grid-template-rows:auto repeat(4,44px) 1fr 44px;gap:8px;padding:18px 10px;background:#07101f;border-right:1px solid #1c2b46}
.rail button{width:38px;height:38px;border:1px solid transparent;border-radius:8px;background:transparent;color:#7790b8;cursor:pointer}
.rail button:hover,.rail button.active{background:#1d68ff;color:#fff}
.rail-logo{background:#226bff!important;color:#fff!important;font-weight:800}
.workspace{min-width:0;max-height:100vh;overflow:hidden;background:#111a2a}
.console-top{height:64px;display:flex;align-items:center;justify-content:space-between;padding:0 24px;border-bottom:1px solid #22324d;background:#101827}
.brand{font-size:18px;font-weight:800;color:#f4f7ff}
.top-actions{display:flex;align-items:center;gap:12px}
.bell{width:34px;height:34px;border:1px solid #273856;border-radius:8px;background:#162238;color:#ff4d5d}
.user-pill{color:#8fa4c7;font-size:13px}
.main{max-width:none;height:calc(100vh - 64px);margin:0;padding:22px;overflow:auto;background:#111a2a}
.ops-shell{display:grid;grid-template-columns:minmax(300px,360px) minmax(620px,1fr);gap:18px;height:calc(100vh - 108px);min-height:0}
.ops-board,.chat-dock,.workflow-strip{border:1px solid #2b3b59;border-radius:8px;background:#1b2638;box-shadow:0 12px 30px #02071133}
.ops-board{display:flex;flex-direction:column;min-width:0;min-height:0;padding:18px;overflow:hidden}
.section-head{display:grid;gap:14px;margin-bottom:18px}
.title-row,.filters{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.section-icon{display:grid;place-items:center;width:30px;height:30px;border-radius:8px;background:#14284f;color:#5b91ff}
.section-head h2{margin:0;color:#e9f1ff;font-size:18px}
.stat-pill{padding:5px 10px;border-radius:999px;font-size:12px;font-weight:700}
.stat-pill.blue{background:#21447f;color:#9dc0ff}.stat-pill.green{background:#125833;color:#7ff0aa}.stat-pill.amber{background:#674410;color:#ffd36a}.stat-pill.slate{background:#24344f;color:#9fb3d3}
.agent-grid{display:grid;grid-template-columns:1fr;gap:14px;min-height:0;overflow:auto;padding-right:2px}
.agent-card{min-height:166px;padding:14px;border:1px solid #394b69;border-radius:8px;background:#202b3d;cursor:pointer;transition:.18s ease}
.agent-card:hover{border-color:#4f83ff;transform:translateY(-2px);box-shadow:0 14px 28px #050b1880}
.card-top{display:grid;grid-template-columns:34px minmax(0,1fr) auto;gap:10px;align-items:start;margin-bottom:12px}
.avatar{display:grid;place-items:center;width:34px;height:34px;border-radius:50%;background:#2d6cff;color:#fff;font-weight:800}
.agent-card h3{margin:0;color:#f5f8ff;font-size:14px}
.agent-card p{margin:3px 0 0;color:#8fa4c7;font-size:12px}
.agent-card strong{display:block;color:#48df8b;font-size:13px}
.agent-card.amber strong{color:#ffc85a}.agent-card.blue strong{color:#61a5ff}.agent-card.violet strong{color:#b98cff}
.task-line{min-height:34px}
.card-controls{display:flex;gap:6px}.card-controls span{width:7px;height:7px;border-radius:50%;background:#23d47f}.card-controls span:nth-child(2){background:#ffc72c}.card-controls span:nth-child(3){background:#ff4d4f}
.progress{height:4px;margin:12px 0 8px;border-radius:999px;background:#101827;overflow:hidden}.progress i{display:block;height:100%;border-radius:inherit;background:#4f83ff}
.agent-card footer{display:grid;gap:5px;color:#a5b7d4;font-size:12px}
.agent-card footer span{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.empty-card{cursor:default}
.workflow-strip{display:grid;gap:12px;margin-top:auto;padding:16px}
.workflow-strip h3{margin:0;color:#f2f6ff}.workflow-strip h3 span{color:#34e585;font-size:12px}.workflow-strip p{margin:5px 0 0;color:#8fa4c7}
.legend{display:flex;gap:12px;flex-wrap:wrap;color:#aebcD4;font-size:12px}.legend b:before{content:"";display:inline-block;width:8px;height:8px;margin-right:6px;border-radius:50%}.success:before{background:#24d37b}.warning:before{background:#f6c945}.error:before{background:#ff5a64}.info:before{background:#4c8dff}
.chat-dock{display:flex;min-width:0;min-height:0;height:100%;flex-direction:column;overflow:hidden}
.chat-dock{position:relative}
.chat-dock-head{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:16px;border-bottom:1px solid #2b3b59}
.chat-head-actions{display:flex;align-items:center;gap:10px}
.bookmark-button{height:32px;padding:0 12px;border:1px solid #44618d;border-radius:8px;background:#132846;color:#8fb9ff;cursor:pointer}
.bookmark-button:hover{border-color:#6fa1ff;background:#183967;color:#fff}
.chat-dock h2{margin:2px 0 0;color:#f5f8ff;font-size:18px}
.eyebrow{margin:0;color:#7f93b8;font-size:12px;font-weight:700;letter-spacing:0;text-transform:uppercase}
.history-list{max-height:150px;overflow:auto;padding:12px 14px;border-bottom:1px solid #2b3b59}
.drawer-actions{margin-bottom:14px}
.history-bookmarks{display:grid;gap:10px}
.bookmark-item{display:flex;align-items:center;justify-content:space-between;gap:12px;width:100%;padding:13px 14px;border:1px solid #344563;border-left:4px solid #4f83ff;border-radius:8px;background:#152238;color:#dce8ff;text-align:left;cursor:pointer}
.bookmark-item span{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-weight:700}
.bookmark-item small{flex:0 0 auto;color:#8fb9ff}
.bookmark-item:hover,.bookmark-item.active{border-color:#6fa1ff;border-left-color:#9a6cff;background:#1b3154}
:deep(.history-drawer){background:#101827;color:#dce8ff}
:deep(.history-drawer .el-drawer__header){margin-bottom:0;padding:18px;border-bottom:1px solid #2b3b59;color:#f5f8ff}
:deep(.history-drawer .el-drawer__body){padding:18px}
.history-backdrop{position:fixed;inset:0;background:transparent;z-index:30}
.history-burst{position:absolute;z-index:32;width:52px;height:52px}
.history-fab{position:absolute;right:0;bottom:0;z-index:8;display:grid;place-items:center;width:52px;height:52px;border:1px solid #ff7180;border-radius:50%;background:linear-gradient(145deg,#ff4d6a,#7c4dff);color:#fff;box-shadow:0 0 0 4px #ff435826,0 14px 32px #020713cc;cursor:grab;touch-action:none}
.history-fab:active{cursor:grabbing}
.history-fab span{font-size:24px;filter:drop-shadow(0 2px 4px #0008)}
.history-burst.open .history-fab{box-shadow:0 0 0 6px #ffcf3333,0 18px 42px #020713cc}
.burst-card{position:absolute;right:0;bottom:0;width:220px;min-height:50px;padding:10px 12px;border:1px solid #506f9f;border-left:4px solid #ffcf33;border-radius:8px;background:#17263df5;color:#dce8ff;text-align:left;opacity:0;pointer-events:none;box-shadow:0 16px 34px #02071399;transition:transform .28s cubic-bezier(.2,.9,.2,1),opacity .2s ease,border-color .2s ease;background-clip:padding-box;backdrop-filter:blur(10px)}
.history-burst.open .burst-card{opacity:1;pointer-events:auto}
.burst-card strong{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:13px}
.burst-card span{display:block;margin-top:6px;color:#8fb9ff;font-size:12px}
.burst-card:hover,.burst-card.active{border-color:#7eaaff;background:#20365a}
.burst-refresh{position:absolute;right:2px;bottom:60px;width:66px;height:28px;border:1px solid #44618d;border-radius:999px;background:#101b2c;color:#9ec0ff;cursor:pointer;opacity:0;pointer-events:none;transition:.2s ease}
.history-burst.open .burst-refresh{opacity:1;pointer-events:auto}
.conversation-item{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:10px 12px;margin-bottom:8px;border:1px solid #334563;border-radius:8px;cursor:pointer;background:#162234;color:#d8e3f7}
.conversation-item span{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.conversation-item small{flex:0 0 auto;color:#69a1ff;font-size:12px}
.conversation-item:hover{border-color:#4d6388;background:#1c2b42}
.conversation-item.active{border-color:#4f83ff;background:#172d54}
.conversation-item.disabled{cursor:not-allowed;opacity:.65}
.messages{display:flex;flex-direction:column;flex:1;min-height:0;overflow:auto;background:#121c2b;border:0;border-radius:0;padding:16px 72px 14px 18px;margin:0;scrollbar-gutter:stable}
.empty-chat{display:grid;place-items:center;align-content:center;height:100%;color:#8fa4c7;text-align:center}
.empty-chat h3{margin:0 0 8px;color:#edf4ff;font-size:20px}
.empty-chat p{margin:0;max-width:360px;line-height:1.7}
.msg{order:1;width:fit-content;max-width:min(820px,86%);padding:14px 16px;border:1px solid #344765;border-radius:8px;background:#1d2a3f;color:#dfe8f9;margin:0 0 12px;overflow-wrap:anywhere}
.msg b{display:block;margin-bottom:8px;color:#f4f8ff;font-size:13px}
.message-body{display:grid;gap:9px}
.message-body h4{margin:8px 0 2px;color:#f5f8ff;font-size:15px}
.message-body p{margin:0;line-height:1.75}
.message-body pre{margin:4px 0;padding:10px 12px;border:1px solid #324967;border-radius:8px;background:#101827;color:#dce8ff;white-space:pre-wrap;font-size:12px;line-height:1.6}
.message-bullet{position:relative;padding-left:16px}
.message-bullet:before{content:"";position:absolute;left:0;top:.72em;width:6px;height:6px;border-radius:50%;background:#5b91ff}
.rag-status{display:grid;gap:4px;margin-top:14px;padding:10px 12px;border:1px solid #365071;border-radius:8px;background:#142238;color:#bfd0eb}
.rag-status strong{margin:0;color:#9ec0ff;font-size:12px}
.rag-status span{font-size:12px;line-height:1.6}
.rag-status small{color:#8fa4c7;font-size:11px;line-height:1.5}
.rag-status.warn{border-color:#6b4f25;background:#241f16;color:#ffdca8}
.rag-status.warn strong{color:#ffcb6b}
.citation-panel{display:grid;gap:8px;margin-top:14px;padding-top:12px;border-top:1px solid #344765}
.citation-title{color:#8fb9ff;font-size:12px;font-weight:800}
.citation-card{padding:10px 12px;border:1px solid #365071;border-radius:8px;background:#152238}
.citation-card strong{display:block;margin:0 0 4px;color:#f4f8ff}
.citation-card span{display:block;color:#8fa4c7;font-size:12px}
.citation-card p{margin:7px 0 0;color:#bfd0eb;font-size:12px;line-height:1.6}
.run-trace{order:2;width:min(680px,86%);margin:0 0 12px;padding:12px 14px;border:1px solid #304968;border-radius:8px;background:#101b2a;color:#dce8ff}
.trace-head{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:10px}
.trace-head strong{font-size:13px;color:#f4f8ff}
.trace-head span{padding:3px 8px;border-radius:999px;background:#142f54;color:#8fb9ff;font-size:12px}
.run-trace ol{display:grid;gap:8px;margin:0;padding:0;list-style:none}
.run-trace li{position:relative;padding-left:18px;color:#8fa4c7;font-size:13px}
.run-trace li:before{content:"";position:absolute;left:2px;top:7px;width:7px;height:7px;border-radius:50%;background:#5b91ff;box-shadow:0 0 0 4px #5b91ff22}
.run-trace li.done:before{background:#44d483;box-shadow:0 0 0 4px #44d48322}
.run-trace li.warn:before{background:#ffcb6b;box-shadow:0 0 0 4px #ffcb6b22}
.run-trace li.error:before{background:#ff6b7a;box-shadow:0 0 0 4px #ff6b7a22}
.run-trace li b{display:inline;color:#dce8ff}
.run-trace li small{display:block;margin-top:3px;color:#7f93b8;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.inline-approval{display:grid;gap:8px;margin-top:12px;padding:12px;border:1px solid #4b6488;border-radius:8px;background:#152238}
.inline-approval strong{color:#f5f8ff;font-size:13px}
.inline-approval span{color:#9ec0ff;font-size:12px}
.inline-approval pre{max-height:120px;margin:0;padding:9px 10px;border:1px solid #2d4264;border-radius:8px;background:#101827;color:#bfd0eb;white-space:pre-wrap;font-size:12px;line-height:1.5;overflow:auto}
.inline-approval div{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.inline-approval em{color:#8fa4c7;font-style:normal;font-size:12px}
.msg.user{margin-left:auto;background:#153b74;border-color:#2f6fd1}
.msg.assistant{margin-right:auto;background:#1d2a3f}
.composer{flex:0 0 auto;padding:14px 16px 16px;border-top:1px solid #2b3b59;background:#162234}
.composer-box{position:relative;border:1px solid #365071;border-radius:8px;background:#101827;overflow:hidden}
.composer-box :deep(.el-textarea__inner){min-height:96px!important;padding:13px 96px 13px 14px;background:#101827!important;border:0!important;box-shadow:none!important;color:#dce8ff;line-height:1.7}
.composer-box :deep(.el-textarea__inner::placeholder){color:#6f83a5}
.send-button{position:absolute;right:10px;bottom:10px}
.composer-toolbar{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-top:10px;color:#7f93b8;font-size:13px}
.tool-buttons{display:flex;align-items:center;gap:10px}
.tool-buttons button{border:0;background:transparent;color:#8fb9ff;cursor:pointer;font-size:13px}
.tool-buttons button:disabled{color:#50627f;cursor:not-allowed}
.tool-buttons .danger-tool{color:#ff9aa2}
.events{padding:16px 20px 18px;background:#1b2638;border:1px solid #2b3b59;border-radius:8px;color:#d8e3f7}
.panel{background:#1b2638!important;border:1px solid #2b3b59;color:#d8e3f7;box-shadow:none}
.agent-manage{display:grid;gap:18px}
.agent-create-form{display:grid;grid-template-columns:1fr 1fr;gap:14px;padding:16px;border:1px solid #2d4161;border-radius:8px;background:#121c2b}
.dialog-form{padding:0;border:0;background:transparent}
.agent-create-form label{display:grid;gap:7px}
.agent-create-form label span{color:#8fa4c7;font-size:13px;font-weight:700}
.agent-create-form :deep(.el-input__wrapper),.agent-create-form :deep(.el-select__wrapper){min-height:36px;background:#f8fbff;box-shadow:none}
.agent-create-form :deep(.el-select){width:100%}
.full-field{grid-column:1/-1}
.rag-hint{margin:0;padding:10px 12px;border:1px solid #2d4264;border-radius:8px;background:#101b2a;color:#8fa4c7;font-size:13px;line-height:1.6}
.readonly-note{margin:10px 0 0;padding:10px 12px;border:1px solid #365071;border-radius:8px;background:#101b2a;color:#8fa4c7;font-size:13px}
.chat-readonly{margin:0 0 10px}
.panel-actions{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.panel-head{display:flex;align-items:flex-start;justify-content:space-between;gap:16px;margin-bottom:16px}
.panel-head h3{margin:0 0 6px;color:#f5f8ff}
.command-panel{display:grid;align-items:end;gap:12px;width:fit-content;max-width:100%;padding:14px;border:1px solid #2d4264;border-radius:8px;background:#121c2b;margin:14px 0 14px}
.kb-create-panel{grid-template-columns:180px 140px 320px auto}
.upload-panel{grid-template-columns:240px 300px auto}
.approval-toolbar{grid-template-columns:minmax(260px,420px) auto;margin:14px 0}
.audit-toolbar{grid-template-columns:190px 190px 220px auto;margin:14px 0}
.command-field{display:grid;gap:7px}
.command-field span{color:#8fa4c7;font-size:12px;font-weight:800}
.command-field :deep(.el-input__wrapper),.command-field :deep(.el-select__wrapper){min-height:38px;border:1px solid #365071;background:#0f1a29;box-shadow:none}
.command-field :deep(.el-input__inner),.command-field :deep(.el-select__placeholder),.command-field :deep(.el-select__selected-item){color:#dce8ff}
.command-field :deep(.el-input__inner::placeholder){color:#637897}
.command-field :deep(.el-select){width:100%}
.command-actions{display:flex;align-items:center;gap:10px;white-space:nowrap}
.command-actions :deep(.el-button){height:38px}
.file-picker{position:relative;display:flex;align-items:center;height:38px;padding:0 12px;border:1px solid #365071;border-radius:8px;background:#0f1a29;color:#d8e3f7;cursor:pointer}
.file-picker input{position:absolute;inset:0;opacity:0;cursor:pointer}
.file-picker span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.task-table{margin-top:10px}
.empty-state{display:grid;align-content:center;justify-items:center;gap:6px;border:1px dashed #365071;border-radius:8px;background:#101b2a;color:#8fa4c7;text-align:center}
.empty-state strong{color:#dce8ff;font-size:15px}
.compact-empty{width:min(460px,100%);min-height:92px;margin-top:16px;padding:18px}
.chunk-count{display:block;margin-top:4px;color:#8fa4c7;font-size:12px}
.error-text{color:#ff9aa2}
.admin-console{display:grid;gap:18px}
.eval-console{display:grid;gap:18px}
.eval-head{display:flex;align-items:flex-end;justify-content:space-between;gap:18px;padding:22px}
.eval-head h2{margin:4px 0 8px;color:#f5f8ff;font-size:26px}
.eval-head p{margin:0}
.eval-metrics{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px}
.eval-layout{display:grid;grid-template-columns:360px minmax(0,1fr);gap:18px;align-items:start}
.eval-report-list{display:grid;gap:10px;padding:16px}
.eval-report-list h3{margin:0 0 6px;color:#f5f8ff}
.eval-report-card{display:grid;gap:6px;padding:12px;border:1px solid #344765;border-radius:8px;background:#121c2b;color:#d8e3f7;cursor:pointer;transition:.16s ease}
.eval-report-card:hover,.eval-report-card.active{border-color:#6fa4ff;background:#173058;box-shadow:0 10px 26px #02071366}
.eval-report-card strong{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:#f5f8ff}
.eval-report-card span{color:#8fa4c7;font-size:12px}
.eval-report-card b{color:#9ec0ff;font-size:13px}
.eval-detail{padding:16px}
.rank-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px;margin:8px 0 14px}
.rank-grid span{padding:11px 12px;border:1px solid #2d4264;border-radius:8px;background:#101b2a;color:#8fa4c7}
.rank-grid b{float:right;color:#f5f8ff}
.metric-notes{margin:0 0 14px;padding:12px;border:1px solid #2d4264;border-radius:8px;background:#101b2a;color:#8fa4c7;line-height:1.7}
.metric-notes strong{display:block;margin-bottom:4px;color:#f5f8ff}
.metric-notes p{margin:0}
.metric-notes code{color:#9ec0ff}
.eval-trend{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;margin:0 0 14px}
.eval-trend section{min-width:0;padding:13px;border:1px solid #2d4264;border-radius:8px;background:#101b2a}
.eval-trend h4{margin:0 0 10px;color:#f5f8ff;font-size:14px}
.trend-row{display:grid;grid-template-columns:minmax(90px,1fr) minmax(80px,1.2fr) 54px;align-items:center;gap:8px;margin:7px 0;color:#8fa4c7;font-size:12px}
.trend-row span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.trend-row i{height:8px;border-radius:999px;background:#17263d;overflow:hidden}
.trend-row i b{display:block;height:100%;border-radius:inherit;background:#5b91ff}
.trend-row strong{text-align:right;color:#dce8ff}
.eval-insights{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin:0 0 14px}
.eval-insights section{min-width:0;padding:13px;border:1px solid #2d4264;border-radius:8px;background:#101b2a}
.eval-insights h4{margin:0 0 10px;color:#f5f8ff;font-size:14px}
.rank-bars{display:grid;gap:8px}
.rank-bar-row{display:grid;grid-template-columns:42px minmax(0,1fr) 42px;align-items:center;gap:8px;color:#8fa4c7;font-size:12px}
.rank-bar-row i{height:8px;border-radius:999px;background:#17263d;overflow:hidden}
.rank-bar-row i b{display:block;height:100%;border-radius:inherit;background:#5b91ff}
.rank-bar-row strong{text-align:right;color:#dce8ff}
.failure-groups{display:flex;gap:8px;flex-wrap:wrap}
.failure-groups span,.failure-groups button{max-width:100%;padding:7px 9px;border:1px solid #365071;border-radius:999px;background:#17263d;color:#bfd0eb;font-size:12px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.failure-groups button{cursor:pointer}
.failure-groups button.active{border-color:#6fa4ff;background:#173058;color:#fff}
.failure-groups b{margin-left:6px;color:#ffb4bd}
.case-toolbar{display:flex;align-items:center;justify-content:space-between;gap:12px;margin:0 0 12px}
.case-toolbar span{color:#8fa4c7;font-size:13px}
.case-toolbar :deep(.el-segmented){--el-segmented-bg-color:#101b2a;--el-segmented-item-selected-bg-color:#1d4f8f;--el-segmented-item-selected-color:#fff;--el-segmented-item-hover-bg-color:#173058;color:#9ec0ff}
.eval-hit-list{display:grid;gap:8px;padding:8px 4px}
.eval-hit-list article{display:grid;gap:5px;padding:10px;border:1px solid #2d4264;border-radius:8px;background:#101b2a}
.eval-hit-list strong{color:#eaf2ff;font-size:13px}
.eval-hit-list span{color:#8fa4c7;font-size:12px}
.eval-hit-list p{margin:0;color:#bfd0eb;line-height:1.55;white-space:pre-wrap}
.eval-retest{display:grid;gap:8px;margin-top:10px;padding:10px;border:1px solid #365071;border-radius:8px;background:#13233a}
.eval-retest strong{color:#f5f8ff}
.eval-retest span{color:#9ec0ff;font-size:12px}
.eval-retest article{display:grid;gap:5px;padding:9px;border:1px solid #2d4264;border-radius:8px;background:#101b2a}
.eval-retest p{margin:0;color:#bfd0eb;line-height:1.55}
.eval-retest-history{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:10px;margin:14px 0}
.eval-retest-history h4{grid-column:1/-1;margin:0;color:#f5f8ff}
.eval-retest-history button{display:grid;gap:4px;text-align:left;border:1px solid #324866;border-radius:8px;background:#101b2a;color:#dbe8ff;padding:10px;cursor:pointer}
.eval-retest-history button.active{border-color:#5a9dff;background:#173058}
.eval-retest-history strong{color:#f5f8ff}
.eval-retest-history span,.eval-retest-history small{color:#8fa4c7;font-size:12px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.admin-hero{display:flex;align-items:flex-end;justify-content:space-between;gap:18px;padding:22px;border:1px solid #2b3b59;border-radius:8px;background:#1b2638}
.admin-hero h2{margin:4px 0 8px;color:#f5f8ff;font-size:26px}
.admin-hero p{margin:0;color:#8fa4c7}
.admin-hero-actions{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.admin-metrics{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px}
.metric-card{padding:16px;border:1px solid #2b3b59;border-radius:8px;background:#162234}
.metric-card span{display:block;color:#8fa4c7;font-size:13px}
.metric-card strong{display:block;margin-top:8px;color:#f5f8ff;font-size:28px}
.queue-cards{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px}
.queue-card{position:relative;padding:16px;border:1px solid #2b3b59;border-radius:8px;background:#1b2638;cursor:pointer;transition:.18s ease}
.queue-card:hover,.queue-card.active{border-color:#5b91ff;background:#1f2d43;transform:translateY(-2px);box-shadow:0 16px 34px #02071366}
.queue-card p{margin:0 0 6px;color:#8fa4c7}
.queue-card h3{margin:0 0 14px;color:#f5f8ff}
.queue-open{display:block;margin-top:12px;color:#83b2ff;font-size:12px;font-weight:700}
.admin-browser{display:grid;grid-template-columns:320px minmax(0,1fr);gap:18px;align-items:start}
.admin-sidebar{display:grid;gap:14px}
.sidebar-card,.data-workbench{border:1px solid #2b3b59;border-radius:8px;background:#1b2638;padding:16px}
.sidebar-card h3{margin:0 0 12px;color:#f5f8ff}
.sidebar-title-row{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:12px}
.sidebar-title-row h3{margin:0}
.sidebar-title-row button{border:1px solid #3d5f8e;border-radius:7px;background:#10213a;color:#8fb9ff;font-weight:700;padding:6px 10px;cursor:pointer}
.sidebar-title-row button:hover{border-color:#65a2ff;background:#173058;color:#dce8ff}
.table-nav-item{display:flex;align-items:center;justify-content:space-between;gap:10px;width:100%;padding:11px 12px;margin-bottom:8px;border:1px solid #344765;border-radius:8px;background:#121c2b;color:#d8e3f7;text-align:left;cursor:pointer}
.table-nav-item:hover,.table-nav-item.active{border-color:#5b91ff;background:#172d54}
.table-nav-item span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.table-nav-item b{color:#9ec0ff}
.user-mini-list{display:grid;gap:8px}
.user-mini-card{display:flex;align-items:center;justify-content:space-between;gap:10px;padding:10px 11px;border:1px solid #2d4264;border-radius:8px;background:#101b2a}
.user-mini-card strong{display:block;color:#f5f8ff;font-size:14px}
.user-mini-card span{display:block;margin-top:3px;color:#8fa4c7;font-size:12px}
.user-mini-card b{padding:3px 8px;border-radius:999px;background:#123927;color:#63e79b;font-size:12px}
.user-mini-card b.off{background:#3a2530;color:#ff9aa2}
.create-user-dialog{display:grid;gap:14px}
.create-user-dialog label{display:grid;gap:7px}
.create-user-dialog label span{color:#8fa4c7;font-size:13px;font-weight:700}
.create-user-dialog :deep(.el-input__wrapper),.create-user-dialog :deep(.el-select__wrapper){min-height:38px}
.role-hint{display:flex;align-items:center;gap:12px;padding:12px;border:1px solid #2d4264;border-radius:8px;background:#101b2a;color:#8fa4c7}
.role-hint strong{min-width:72px;color:#dce8ff}
.switch-row{display:flex!important;align-items:center;justify-content:space-between;padding:12px;border:1px solid #2d4264;border-radius:8px;background:#101b2a}
.status-chips{display:flex;gap:8px;flex-wrap:wrap}
.user-form{display:grid;grid-template-columns:minmax(180px,1fr) minmax(220px,1.2fr) 160px auto auto;gap:12px;align-items:center;margin:14px 0 18px}
.compact-json{max-height:180px;margin:0;overflow:auto;white-space:pre-wrap;word-break:break-word;color:#233047;font-family:monospace;font-size:12px;line-height:1.55}
.data-table{border:1px solid #2d4264;border-radius:8px;overflow:hidden}
.page-table{margin-top:18px}
.cell-value{display:block;max-width:340px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:#dce8ff}
.cell-value.status{display:inline-block;width:auto;padding:3px 8px;border:1px solid #365071;border-radius:999px;background:#142238;color:#9ec0ff;font-size:12px}
.table-actions{display:flex;align-items:center;justify-content:center;gap:10px;white-space:nowrap}
:deep(.data-table.el-table){--el-table-bg-color:#111c2b;--el-table-tr-bg-color:#111c2b;--el-table-header-bg-color:#16243a;--el-table-row-hover-bg-color:#20324c;--el-table-border-color:#2c405f;--el-table-text-color:#d8e3f7;--el-table-header-text-color:#94a8c8;color:#d8e3f7;background:#111c2b}
:deep(.data-table .el-table__cell){background:transparent!important;border-bottom-color:#2c405f!important}
:deep(.data-table .el-table__body tr:hover>td.el-table__cell){background:#20324c!important}
:deep(.data-table .el-table__fixed-right),:deep(.data-table .el-table-fixed-column--right){background:#111c2b!important;box-shadow:-10px 0 18px #02071366}
:deep(.data-table .action-column){background:#111c2b!important}
:deep(.data-table .el-table__empty-block){background:#111c2b;color:#8fa4c7}
@media (max-width: 900px){
  .console{grid-template-columns:1fr}.rail{display:none}.console-top{height:auto;align-items:flex-start;gap:14px;flex-direction:column;padding:16px}.ops-shell{grid-template-columns:1fr}.agent-grid{grid-template-columns:1fr}.chat-dock{min-height:640px}
  .user-form{grid-template-columns:1fr}
  .admin-metrics,.queue-cards,.admin-browser,.agent-create-form,.eval-metrics,.eval-layout,.rank-grid,.eval-insights{grid-template-columns:1fr}
  .kb-create-panel,.upload-panel,.approval-toolbar,.audit-toolbar{grid-template-columns:1fr;width:100%}
  .command-actions{justify-content:flex-start}
}
</style>























