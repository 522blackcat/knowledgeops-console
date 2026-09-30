<script setup>
import { ref, computed, nextTick, onMounted, onUnmounted, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
// 所有请求走同源 /api，浏览器不直接访问容器内部地址。
const token=ref(sessionStorage.getItem('agent_token')||'')
const username=ref('admin'), password=ref(''), user=ref(null), page=ref('chat'), busy=ref(false)
const historyOpen=ref(false)
const historyPos=ref({right:20,bottom:210})
let historyDrag=null
const agents=ref([]), bases=ref([]), approvals=ref([]), messages=ref([])
const pendingUserMessage=ref(null)
const pendingBeforeMessageCount=ref(0)
const messagesEl=ref(null), autoScrollMessages=ref(true)
const ingestJobs=ref([]), jobsLoading=ref(false)
const adminOverview=ref({tables:[],queues:[],users:[]}), adminLoading=ref(false)
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
async function refreshRun(){if(!run.value)return;try{run.value=await api('/api/runs/'+run.value.id);await Promise.all([loadMessages(true),readEventsSnapshot()]);if(['completed','failed','cancelled','timed_out'].includes(run.value.status)){await readEventsSnapshot();stopPoll();busy.value=false;await loadMessages(true);await loadConversations();if(conversation.value){const latest=conversations.value.find(item=>item.id===conversation.value.id);if(latest)conversation.value=latest}}}catch(e){stopPoll();ElMessage.error(detail(e))}}
async function send(){if(!question.value.trim())return;if(!selectedAgent.value)return ElMessage.warning('先创建或选择 Agent');try{busy.value=true;if(!conversation.value){busy.value=false;const created=await newConversation();if(!created)return;busy.value=true}if(!conversation.value){busy.value=false;return}const q=question.value.trim();question.value='';resetRunEvents();pendingBeforeMessageCount.value=messages.value.length;pendingUserMessage.value={id:'local-'+Date.now(),role:'user',content:q,metadata_json:null};messages.value=[...messages.value,pendingUserMessage.value];scrollMessagesToBottom(true);events.value.push({id:'local-submit',type:'run.submitted',payload:{message:'问题已发送，等待服务接收'}});run.value=await api('/api/runs',{method:'POST',body:JSON.stringify({agent_id:selectedAgent.value,conversation_id:conversation.value.id,question:q})});await Promise.all([loadMessages(true),readEventsSnapshot()]);stopPoll();poll=setInterval(refreshRun,1500)}catch(e){busy.value=false;ElMessage.error(detail(e))}}
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
async function upload(){if(!selectedBase.value||!uploadFile.value)return ElMessage.warning('请选择知识库和文件');try{const form=new FormData();form.append('file',uploadFile.value);job.value=await api('/api/knowledge/bases/'+selectedBase.value+'/documents',{method:'POST',body:form});uploadFile.value=null;await loadIngestJobs();if(job.value?.status==='duplicate')ElMessage.warning(job.value.message||'相同内容已存在，未重复入库');else ElMessage.success('已提交文档处理任务')}catch(e){ElMessage.error(detail(e))}}
async function refreshJob(){await loadIngestJobs()}
async function deleteDocument(item){try{await ElMessageBox.confirm('删除后该文档会立即从检索中排除，正在处理的入库任务也会取消。确定删除？','删除文档',{type:'warning'});await api('/api/knowledge/documents/'+item.document_id,{method:'DELETE'});ElMessage.success('文档已删除');await Promise.all([loadBases(),loadIngestJobs()])}catch(e){if(e!=='cancel')ElMessage.error(detail(e))}}
async function loadAdminOverview(){adminLoading.value=true;try{adminOverview.value=await api('/api/admin/overview')}catch(e){ElMessage.error('加载管理员数据失败：'+detail(e))}finally{adminLoading.value=false}}
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
  const map={'run.submitted':'问题已提交','run.queued':'任务已进入队列','run.started':'开始处理','rag.retrieved':'知识库检索命中','tool.started':'调用工具','tool.completed':'工具完成','tool.failed':'工具失败','run.completed':'回答完成','run.failed':'运行失败','run.cancelled':'已取消','run.timed_out':'运行超时'}
  return map[type]||type||'运行事件'
}
function eventDetail(event){
  const payload=event?.payload||{}
  if(payload.hit_count!==undefined)return `命中 ${payload.hit_count} 条`
  return payload.message||payload.tool_name||payload.status||payload.error||payload.reason||''
}
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
    bottom:Math.max(190,Math.min(360,historyDrag.bottom-dy)),
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
async function loadApprovals(){try{approvals.value=await api('/api/approvals/pending')}catch(e){if(token.value)ElMessage.error(detail(e))}}
async function decide(item,action){try{await ElMessageBox.confirm('确定'+(action==='approve'?'批准':'拒绝')+'此工具调用？','人工审批');await api('/api/approvals/'+item.id+'/'+action,{method:'POST',body:JSON.stringify({reason:reason.value})});ElMessage.success('处理完成');await loadApprovals()}catch(e){if(e!=='cancel')ElMessage.error(detail(e))}}
watch([messages,events],()=>scrollMessagesToBottom(),{deep:true})
watch(page,()=>{if(page.value==='chat')attachMessagesScrollHandlers()})
onMounted(()=>{attachMessagesScrollHandlers();if(token.value)initialize()});onUnmounted(stopPoll)
</script>
<template>
  <div v-if="!token" class="login panel"><h2>知识运营 Agent 控制台</h2><p class="muted">使用后端初始化的管理员账号登录</p><el-form @submit.prevent="login"><el-form-item label="用户名"><el-input v-model="username" autocomplete="username"/></el-form-item><el-form-item label="密码"><el-input v-model="password" type="password" show-password autocomplete="current-password" @keyup.enter="login"/></el-form-item><el-button type="primary" :loading="busy" style="width:100%" @click="login">登录</el-button></el-form></div>
  <div v-else class="console"><aside class="rail"><button class="rail-logo" @click="page='chat'">A</button><button :class="{active:page==='chat'}" @click="page='chat'">⌘</button><button :class="{active:page==='agents'}" @click="page='agents'">◎</button><button :class="{active:page==='knowledge'}" @click="page='knowledge'">▣</button><button :class="{active:page==='approvals'}" @click="page='approvals'">◇</button><button v-if="user?.role==='admin'" :class="{active:page==='admin'}" @click="page='admin';loadAdminOverview()">⚙</button><span></span><button @click="logout">↩</button></aside><section class="workspace"><header class="console-top"><div class="brand">KnowledgeOps Console</div><div class="top-actions"><el-button type="primary" size="small" @click="newConversation">新建测试</el-button><el-input placeholder="搜索 Agent..." style="width:250px" disabled/><button class="bell">●</button><span class="user-pill">{{user?.username}} · {{user?.role}}</span></div></header><main class="main">
  <template v-if="page==='chat'"><div class="ops-shell"><section class="ops-board"><div class="section-head"><div class="title-row"><span class="section-icon">⌘</span><h2>KnowledgeOps Console</h2><span class="stat-pill blue">{{agents.length}} 总数</span><span class="stat-pill green">{{activeAgentCount}} 启用</span><span class="stat-pill amber">{{busy?1:0}} 忙碌</span><span class="stat-pill slate">{{Math.max(0,agents.length-activeAgentCount)}} 空闲</span></div><div class="filters"><el-select v-model="selectedAgent" placeholder="全部 Agent" style="width:180px" :disabled="busy" @change="changeAgent"><el-option v-for="a in agents" :key="a.id" :label="a.name" :value="a.id"/></el-select><el-button :loading="conversationsLoading" @click="loadConversations">刷新</el-button></div></div><div class="agent-grid"><article v-for="agent in agentCards" :key="agent.id" class="agent-card" :class="agent.tone" @click="selectedAgent=agent.id;changeAgent()"><div class="card-top"><div class="avatar">{{agent.name?.slice(0,1)||'A'}}</div><div><h3>{{agent.name}}</h3><p>{{agent.specialty}}</p></div><div class="card-controls"><span></span><span></span><span></span></div></div><strong>{{agent.status==='disabled'?'Paused':'Monitoring questions'}}</strong><p class="task-line">{{currentAgentConversations.find(c=>c.agent_id===agent.id)?.title||'Ready for new intake'}}</p><footer><span>{{agent.checks}} 个历史会话</span><span>{{knowledgeSummary(agent)}}</span><span>{{knowledgeScopeDetail(agent)}}</span></footer></article><article v-if="!agentCards.length" class="agent-card empty-card"><h3>暂无 Agent</h3><p>先到 Agent 管理里创建一个 Agent，然后这里会显示工作状态。</p></article></div><section class="workflow-strip"><div><h3>工作流监控 <span>LIVE</span></h3><p>实时展示 Agent 任务、检索和审批状态</p></div><div class="legend"><b class="success">Success</b><b class="warning">Warning</b><b class="error">Error</b><b class="info">Info</b></div></section></section><aside class="chat-dock"><div class="chat-dock-head"><div><p class="eyebrow">对话</p><h2>{{conversation?.title||agents.find(a=>a.id===selectedAgent)?.name||'Select Agent'}}</h2></div><div class="chat-head-actions"><el-button :disabled="busy" @click="newConversation">新建会话</el-button></div></div><div class="messages"><div v-if="!messages.length&&!events.length" class="empty-chat"><h3>开始对话</h3><p>选中 Agent 后输入问题。</p></div><div v-for="m in messages" :key="m.id" class="msg" :class="m.role"><b>{{m.role==='user'?'你':m.role==='assistant'?'Agent':m.role}}</b><div class="message-body"><template v-for="(block,idx) in messageBlocks(m.content)" :key="idx"><h4 v-if="block.type==='heading'">{{block.text}}</h4><p v-else-if="block.type==='bullet'" class="message-bullet">{{block.text}}</p><pre v-else-if="block.type==='code'">{{block.text}}</pre><p v-else>{{block.text}}</p></template></div><div v-if="m.role==='assistant'&&citations(m).length" class="citation-panel"><div class="citation-title">知识库命中 {{citations(m).length}} 条</div><article v-for="hit in citations(m)" :key="hit.chunk_id" class="citation-card"><strong>{{hit.filename}}</strong><span>score {{Number(hit.score||0).toFixed(3)}} · chunk {{String(hit.chunk_id||'').slice(0,8)}}</span><p>{{hit.preview}}</p></article></div></div><div v-if="events.length&&busy" class="run-trace"><div class="trace-head"><strong>本次运行过程</strong><span>{{run?.status||'running'}}</span></div><ol><li v-for="event in events" :key="event.id||event.type"><b>{{eventTitle(event)}}</b><small v-if="eventDetail(event)">{{eventDetail(event)}}</small></li></ol></div></div><div class="composer"><div class="composer-box"><el-input v-model="question" type="textarea" :rows="3" resize="none" placeholder="输入问题..." @keyup.ctrl.enter="send"/><el-button class="send-button" type="primary" :loading="busy" @click="send">发送</el-button></div><div class="composer-toolbar"><span>{{run?'运行状态：'+run.status:'Ctrl + Enter 发送'}}</span><div class="tool-buttons"><button :disabled="!run" @click="refreshRun">刷新状态</button><button v-if="busy" class="danger-tool" :disabled="!run" @click="cancelRun">取消</button></div></div></div><div v-if="historyOpen" class="history-backdrop" @click="historyOpen=false"></div><div class="history-burst" :class="{open:historyOpen}" :style="{right:historyPos.right+'px',bottom:historyPos.bottom+'px'}"><button class="history-fab" title="拖动或点击查看历史" @pointerdown.prevent="beginHistoryDrag"><span>🎉</span></button><button v-for="(item,index) in currentAgentConversations.slice(0,6)" :key="item.id" class="burst-card" :class="{active:conversation?.id===item.id}" :style="{transform:historyOpen?`translate(${[-64,-64,-64,-64,-64,-64][index]||-64}px, ${[-4,-62,-120,-178,-236,-294][index]||-120}px) rotate(${[0,0,0,0,0,0][index]||0}deg)`:''}" @click="selectConversation(item)"><strong>{{item.title||'未命名会话'}}</strong><span>{{conversation?.id===item.id?'当前':'打开'}}</span></button><button v-if="historyOpen" class="burst-refresh" @click="loadConversations">刷新</button></div></aside></div><el-alert v-if="run?.error_message" type="error" :title="run.error_message" style="margin-top:12px"/></template>
  <template v-else-if="page==='agents'">
    <div class="agent-manage">
      <section class="panel">
        <div class="panel-head">
          <div><h3>Agent 列表</h3><p class="muted">查看已创建的 Agent，确认是否启用了知识库。</p></div>
          <div class="panel-actions"><el-button type="primary" @click="openCreateAgent">创建 Agent</el-button><el-button @click="loadAgents">刷新列表</el-button></div>
        </div>
        <div v-if="!agents.length" class="empty-state compact-empty"><strong>暂无 Agent</strong><span>先创建一个 Agent，再进入对话测试。</span></div>
        <el-table v-else :data="agents" class="data-table page-table" empty-text="暂无 Agent">
          <el-table-column prop="name" label="名称" min-width="180"/>
          <el-table-column prop="description" label="描述" min-width="220" show-overflow-tooltip/>
          <el-table-column prop="agent_type" label="类型" width="120"/>
          <el-table-column prop="status" label="状态" width="120"/>
          <el-table-column label="知识库" min-width="180"><template #default="s"><span>{{knowledgeSummary(s.row)}}</span></template></el-table-column>
          <el-table-column label="操作" width="150"><template #default="s"><el-button link type="primary" @click="openEditAgent(s.row)">修改</el-button><el-button link type="primary" :disabled="busy" @click="selectedAgent=s.row.id;page='chat';changeAgent()">对话</el-button></template></el-table-column>
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
          <el-button type="primary" :disabled="!baseName.trim()" @click="addBase">创建知识库</el-button>
          <el-button @click="loadBases">刷新</el-button>
        </div>
      </div>
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
          <el-button type="primary" :disabled="!selectedBase||!uploadFile" @click="upload">提交入库</el-button>
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
        <el-table-column label="操作" width="140">
          <template #default="s">
            <el-button link type="primary" @click="refreshJob">刷新</el-button>
            <el-button link type="danger" :disabled="s.row.document_status==='deleted'" @click="deleteDocument(s.row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>
  </template>
  <template v-else-if="page==='approvals'"><div class="panel"><h2>人工审批</h2><div class="command-panel approval-toolbar"><label class="command-field approval-reason"><span>审批备注</span><el-input v-model="reason" placeholder="审批原因（可选）"/></label><div class="command-actions"><el-button @click="loadApprovals">刷新待审批</el-button></div></div><div v-if="!approvals.length" class="empty-state compact-empty"><strong>暂无待审批任务</strong><span>需要人工确认的工具调用会出现在这里。</span></div><el-table v-else :data="approvals" class="data-table page-table" empty-text="暂无待审批任务"><el-table-column prop="tool_name" label="工具" width="170"/><el-table-column prop="run_id" label="运行 ID" width="260"/><el-table-column label="参数"><template #default="s"><pre class="json">{{JSON.stringify(s.row.arguments_json,null,2)}}</pre></template></el-table-column><el-table-column label="操作" width="170"><template #default="s"><el-button link type="success" @click="decide(s.row,'approve')">批准</el-button><el-button link type="danger" @click="decide(s.row,'reject')">拒绝</el-button></template></el-table-column></el-table></div></template>
  <template v-else-if="page==='admin'">
    <div class="admin-console">
      <section class="admin-hero">
        <div>
          <p class="eyebrow">Tenant Console</p>
          <h2>管理员工作台</h2>
          <p>数据表、运行队列和用户管理分区展示，避免把不同类型的信息混成一张表。</p>
        </div>
        <div class="admin-hero-actions">
          <el-button type="primary" @click="createUserOpen=true">新增用户</el-button>
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
              <button @click="createUserOpen=true">新增</button>
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
.history-burst{position:absolute;z-index:40;width:52px;height:52px}
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
.messages{display:flex;flex-direction:column;flex:1;min-height:0;overflow:auto;background:#121c2b;border:0;border-radius:0;padding:18px;margin:0}
.empty-chat{display:grid;place-items:center;align-content:center;height:100%;color:#8fa4c7;text-align:center}
.empty-chat h3{margin:0 0 8px;color:#edf4ff;font-size:20px}
.empty-chat p{margin:0;max-width:360px;line-height:1.7}
.msg{order:1;width:fit-content;max-width:min(860px,88%);padding:16px 18px;border:1px solid #344765;border-radius:8px;background:#1d2a3f;color:#dfe8f9;margin:0 0 16px;overflow-wrap:anywhere}
.msg b{display:block;margin-bottom:8px;color:#f4f8ff;font-size:13px}
.message-body{display:grid;gap:9px}
.message-body h4{margin:8px 0 2px;color:#f5f8ff;font-size:15px}
.message-body p{margin:0;line-height:1.75}
.message-body pre{margin:4px 0;padding:10px 12px;border:1px solid #324967;border-radius:8px;background:#101827;color:#dce8ff;white-space:pre-wrap;font-size:12px;line-height:1.6}
.message-bullet{position:relative;padding-left:16px}
.message-bullet:before{content:"";position:absolute;left:0;top:.72em;width:6px;height:6px;border-radius:50%;background:#5b91ff}
.citation-panel{display:grid;gap:8px;margin-top:14px;padding-top:12px;border-top:1px solid #344765}
.citation-title{color:#8fb9ff;font-size:12px;font-weight:800}
.citation-card{padding:10px 12px;border:1px solid #365071;border-radius:8px;background:#152238}
.citation-card strong{display:block;margin:0 0 4px;color:#f4f8ff}
.citation-card span{display:block;color:#8fa4c7;font-size:12px}
.citation-card p{margin:7px 0 0;color:#bfd0eb;font-size:12px;line-height:1.6}
.run-trace{order:2;width:min(760px,92%);margin:0 0 16px;padding:14px 16px;border:1px solid #304968;border-radius:8px;background:#101b2a;color:#dce8ff}
.trace-head{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:10px}
.trace-head strong{font-size:13px;color:#f4f8ff}
.trace-head span{padding:3px 8px;border-radius:999px;background:#142f54;color:#8fb9ff;font-size:12px}
.run-trace ol{display:grid;gap:8px;margin:0;padding:0;list-style:none}
.run-trace li{position:relative;padding-left:18px;color:#8fa4c7;font-size:13px}
.run-trace li:before{content:"";position:absolute;left:2px;top:7px;width:7px;height:7px;border-radius:50%;background:#5b91ff;box-shadow:0 0 0 4px #5b91ff22}
.run-trace li b{display:inline;color:#dce8ff}
.run-trace li small{display:block;margin-top:3px;color:#7f93b8;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
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
.panel-actions{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.panel-head{display:flex;align-items:flex-start;justify-content:space-between;gap:16px;margin-bottom:16px}
.panel-head h3{margin:0 0 6px;color:#f5f8ff}
.command-panel{display:grid;align-items:end;gap:12px;width:fit-content;max-width:100%;padding:14px;border:1px solid #2d4264;border-radius:8px;background:#121c2b;margin:14px 0 14px}
.kb-create-panel{grid-template-columns:180px 140px 320px auto}
.upload-panel{grid-template-columns:240px 300px auto}
.approval-toolbar{grid-template-columns:minmax(260px,420px) auto;margin:14px 0}
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
  .admin-metrics,.queue-cards,.admin-browser,.agent-create-form{grid-template-columns:1fr}
  .kb-create-panel,.upload-panel,.approval-toolbar{grid-template-columns:1fr;width:100%}
  .command-actions{justify-content:flex-start}
}
</style>










