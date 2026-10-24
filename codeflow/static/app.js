"use strict";
const $ = (id) => document.getElementById(id);
const escapeHtml = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const LABELS = {planner:"需求规划师",coder:"编码工程师",reviewer:"代码审查师",repair:"编码工程师 · 修复",recheck:"代码审查师 · 复审"};
const STATUS = {queued:"准备开始",running:"正在协作",paused:"已暂停",error:"执行失败",completed:"审查通过",needs_review:"需人工复核"};
const DEMO_TASK = "修复列表去重函数，要求保留原始顺序，支持空列表，不修改输入，并补充边界测试。";
const DEMO_CODE = "def unique_items(items):\n    return list(set(items))\n";
const LANGUAGE = {python:"Python",javascript:"JavaScript",typescript:"TypeScript"};
let current = null, activeTab = "code", selectedFile = "", history = [], config = {}, renderKey = "", polling = false, submitting = false, stepStarted = Date.now(), lastStep = "", toastTimer, draftTimer;

async function api(path, method = "GET", body) {
  const response = await fetch(path, {method, headers: body === undefined ? {} : {"Content-Type":"application/json"}, body:body === undefined ? undefined : JSON.stringify(body), signal:AbortSignal.timeout(15000)});
  let data;
  try { data = await response.json(); } catch { throw new Error("本地服务没有返回 JSON，请确认服务仍在运行。"); }
  if (!response.ok) throw new Error(data.error || `请求失败（${response.status}）`);
  return data;
}
function notify(message) { $("notification").textContent = message; $("notification").hidden = !message; }
function toast(message) { clearTimeout(toastTimer); $("toast").textContent = message; $("toast").hidden = false; toastTimer = setTimeout(() => $("toast").hidden = true, 3000); }
function guard(fn) { return async (event) => { try { await fn(event); } catch(error) { notify(error.message || "操作失败，请检查本地服务。"); } }; }
function localGet(key) { try { return localStorage.getItem(key); } catch { return null; } }
function localSet(key,value) { try { if (value === null) localStorage.removeItem(key); else localStorage.setItem(key,value); } catch { $("save-state").textContent = "浏览器偏好未保存；运行记录仍由本机服务保存。"; } }
function getForm() { return {task:$("task").value, code:$("source-code").value, language:$("language").value, mode:document.querySelector('input[name="mode"]:checked').value, provider:document.querySelector('input[name="provider"]:checked').value}; }
function setForm(input) {
  $("task").value = input.task || ""; $("source-code").value = input.code || ""; $("language").value = LANGUAGE[input.language] ? input.language : "python";
  document.querySelectorAll('input[name="mode"]').forEach(el => el.checked = el.value === (input.mode || "generate"));
  document.querySelectorAll('input[name="provider"]').forEach(el => el.checked = el.value === (input.provider || "online"));
  updateFormHint();
}
function updateFormHint() {
  $("task-count").textContent = `${$("task").value.length} / 3000`;
  const demo = document.querySelector('input[name="provider"]:checked').value === "demo";
  $("provider-note").textContent = demo ? "离线只运行固定案例，明确展示一次审查退回与修复；自定义需求请使用 DeepSeek。" : "模型调用会消耗你的 DeepSeek 额度；任务和代码会发送给 DeepSeek，密钥仅在服务端读取。";
  $("start-button").disabled = submitting || !$("task").value.trim() || (!demo && !config.configured);
}
function syncDraft() { updateFormHint(); clearTimeout(draftTimer); if (!current) draftTimer = setTimeout(() => localSet("codeflow:draft", JSON.stringify(getForm())), 350); }
function latestDraft() { const records = current?.records || []; return records.find(r => r.step === "repair")?.output || records.find(r => r.step === "coder")?.output; }
function latestReview() { const records = current?.records || []; return records.find(r => r.step === "recheck")?.output || records.find(r => r.step === "reviewer")?.output; }
function isRunning() { return ["running","queued"].includes(current?.status); }
function dateLabel(value) { const date = new Date(value); return Number.isNaN(date.getTime()) ? "" : date.toLocaleString("zh-CN", {month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit",hour12:false}); }
function seconds(ms) { return (ms / 1000).toFixed(1) + " 秒"; }
function recordsOf(role) { return (current?.records || []).filter(r => r.role === role); }

function renderWorkflow() {
  const definitions = [{role:"planner",icon:"◎",title:"需求规划师",detail:"拆解需求与验收标准"},{role:"coder",icon:"&lt;/&gt;",title:"编码工程师",detail:"实现代码与边界测试"},{role:"reviewer",icon:"◇",title:"代码审查师",detail:"独立检查，反馈修复"}];
  const step = current?.active_step || "";
  if (step !== lastStep) { lastStep = step; stepStarted = Date.now(); }
  $("agent-path").innerHTML = definitions.map((d,index) => {
    const records = recordsOf(d.role), active = isRunning() && (step === d.role || step === "repair" && d.role === "coder" || step === "recheck" && d.role === "reviewer");
    const metric = records.length ? `${seconds(records.reduce((s,r)=>s+r.metrics.duration_ms,0))} · ${records.reduce((s,r)=>s+r.metrics.tokens,0).toLocaleString()} tokens` : active ? "正在独立执行…" : ["需求解析 · 方案规划","实现 · 测试文件","静态审查 · 反馈"][index];
    return (index ? '<span class="path-arrow" aria-hidden="true">→</span>' : "") + `<article class="agent-card ${active ? "active" : records.length ? "done" : ""}"><div class="agent-top"><span class="agent-icon" aria-hidden="true">${d.icon}</span><small>0${index+1}</small></div><h4>${d.title}${records.length ? '<b aria-label="已有完成结果">✓</b>' : ""}</h4><p>${d.detail}</p><div class="agent-metrics">${metric}</div></article>`;
  }).join("");
  $("workflow-status").textContent = current ? STATUS[current.status] || current.status : "等待开始";
  $("progress-area").hidden = !current;
  if (current) {
    const repair = current.records.some(r => r.step === "reviewer" && !r.output.approved), total = repair ? 5 : 3;
    $("progress").max = total; $("progress").value = current.records.length;
    $("progress-label").textContent = ({running:"Agent 正在协作",queued:"准备开始协作",paused:"进度已保留，可继续",error:"节点失败，成功结果已保留",completed:"静态审查通过，等待本地测试",needs_review:"已达到修复上限，需要人工复核"})[current.status] || "";
    $("progress-number").textContent = `已完成 ${current.records.length} / ${total} 步`;
    $("current-step").textContent = step && isRunning() ? `${LABELS[step]} · 当前页面已等待 ${Math.floor((Date.now()-stepStarted)/1000)} 秒。模型调用完成后保存检查点。` : current.input.provider === "demo" ? "离线固定案例 · 没有调用真实模型" : "逐节点独立请求 · 仅成功节点写入检查点";
  }
}
function codeLines(content, diff = false) {
  return '<div class="code-view" aria-label="' + (diff ? "代码差异" : "源码") + '">' + content.replace(/\n$/,"").split("\n").map((line,i) => {
    const cls = diff ? line.startsWith("+++") || line.startsWith("---") || line.startsWith("@@") ? "diff-header" : line.startsWith("+") ? "add" : line.startsWith("-") ? "remove" : "" : "";
    return `<div class="code-line ${cls}"><span class="line-number" aria-hidden="true">${i+1}</span><code>${escapeHtml(line) || " "}</code></div>`;
  }).join("") + "</div>";
}
function empty(title,detail) { return `<div class="empty-state"><div class="empty-symbol" aria-hidden="true">{ }</div><h3>${escapeHtml(title)}</h3><p>${escapeHtml(detail)}</p><div class="empty-features"><span>明确的验收标准</span><span>可下载的实现与测试</span><span>独立的质量审查</span></div></div>`; }
function planPreview() {
  const plan = current?.records.find(r=>r.step==="planner")?.output;
  if (!plan) return empty(isRunning()?"正在梳理你的需求…":"准备好，开始新的实现。", "在左侧描述一个具体需求，三个角色将依次完成规划、编码和审查。也可以载入离线演示体验完整闭环。");
  return `<div class="plan-preview"><p class="eyebrow">PLAN / ACCEPTANCE CRITERIA</p><h3>实现路线已规划好</h3><p>${escapeHtml(plan.summary)}</p><h4>验收标准</h4><ul>${plan.acceptance.map(item=>`<li>${escapeHtml(item)}</li>`).join("")}</ul><h4>实现方案</h4><ol>${plan.approach.map(item=>`<li>${escapeHtml(item)}</li>`).join("")}</ol>${plan.risks.length?`<h4>需要留意</h4><ul>${plan.risks.map(item=>`<li>${escapeHtml(item)}</li>`).join("")}</ul>`:""}</div>`;
}
function renderCode() {
  const draft = latestDraft(), review = latestReview();
  if (!draft) return planPreview();
  if (!draft.files.some(f=>f.path===selectedFile)) selectedFile=draft.files[0].path;
  const file = draft.files.find(f=>f.path===selectedFile);
  return `<div class="delivery-heading"><p>你的代码交付 <span>· ${LANGUAGE[current.input.language]}</span></p><button class="download-button" id="download-bundle">↓ 下载实现与测试</button></div><div class="review-strip"><strong>${review ? `${review.approved ? "静态审查通过" : "审查发现问题"} · ${review.score} 分` : "实现已生成，等待独立审查"}</strong><span>${current.input.provider === "demo" ? "离线固定演示 · " : ""}测试文件已生成，尚未在服务端执行；请下载后在本地运行。</span></div><div class="file-tabs" role="group" aria-label="产物文件">${draft.files.map(f=>`<button class="${f.path===selectedFile?"active":""}" data-file="${escapeHtml(f.path)}" aria-pressed="${f.path===selectedFile}">${escapeHtml(f.path)}</button>`).join("")}</div><div class="code-toolbar"><span>${escapeHtml(file.path)} · ${file.content.trimEnd().split("\n").length} 行</span><button class="copy-button" id="copy-code">复制文件</button></div>${codeLines(file.content)}<div class="notes"><h4>实现说明</h4><p>${escapeHtml(draft.summary)}</p>${draft.notes.length?`<ul>${draft.notes.map(n=>`<li>${escapeHtml(n)}</li>`).join("")}</ul>`:""}<h4>本地验证命令（尚未执行）</h4>${draft.commands.map(c=>`<p><code>${escapeHtml(c)}</code></p>`).join("")}</div>`;
}
function renderReview() {
  const review=latestReview();
  if (!review) return empty("等待独立审查", "编码结果会与验收标准一起交给审查师。审查不会跳过前置节点，也不会直接采信编码师自己的评价。");
  const checks=current.static_checks || [];
  return `<div class="score-heading"><div class="score-number">${review.score}<small> /100</small></div><div><h3>${review.approved?"静态审查通过":"需要修复或人工复核"}</h3><p>模型质量判断 · 测试尚未执行</p></div></div><p class="scope-note">${escapeHtml(review.summary)}</p><section class="review-section"><h4>真实静态检查</h4>${checks.map(c=>`<div class="check-row"><span class="check-status">${c.analysis.parse_error?"需修复":current.input.language==="python"?"语法通过":"文本检查"}</span><div><strong>${escapeHtml(c.file)}</strong><p>${escapeHtml(c.scope)} · 规则发现 ${c.analysis.findings.length} 项。${escapeHtml(c.analysis.parse_error || "")}</p></div></div>`).join("")}<p class="scope-note">Python 使用作业 1 的 AST 分析器。JavaScript / TypeScript 只检查通用文本规则，未编译、未执行。</p><h4>验收清单（模型静态判断）</h4>${review.checklist.map(c=>`<div class="check-row"><span class="check-status">${{pass:"符合",fail:"未通过",unknown:"待验证"}[c.status]}</span><div><strong>${escapeHtml(c.item)}</strong><p>${escapeHtml(c.detail)}</p></div></div>`).join("")}<h4>审查发现 · ${review.findings.length}</h4>${review.findings.length?review.findings.map(f=>`<article class="finding"><span class="finding-location">${{high:"高",medium:"中",low:"低"}[f.severity]} · ${escapeHtml(f.file)}:${f.line}</span><p>${escapeHtml(f.message)}</p><p class="suggestion">建议：${escapeHtml(f.suggestion)}</p></article>`).join(""):'<p class="scope-note">本轮模型审查未报告问题，仍需运行测试和人工核对。</p>'}</section>`;
}
function renderLogs() {
  if (!current?.logs.length) return empty("协作过程将记录在这里", "记录节点开始、工具调用、检查点保存、失败和修复分支；不会展示或要求模型的隐藏思维过程。");
  return `<div class="state-heading"><h3>执行轨迹</h3><span>${current.logs.length} 条</span></div><p class="state-note">成功节点持久化到本机。暂停在当前请求结束后生效，继续时只运行缺失的节点。</p><ol class="logs">${current.logs.map(l=>`<li class="${escapeHtml(l.kind)}"><span class="log-marker" aria-hidden="true"></span><time>${escapeHtml(dateLabel(l.time))}</time><p>${escapeHtml(l.message)}</p></li>`).join("")}</ol>`;
}
function renderState() {
  if (!current) return empty("共享状态尚未创建", "规划结果成为编码节点的输入，代码和验收标准共同传给审查节点；修复时再加入独立审查反馈。");
  return `<div class="state-heading"><h3>Agent 之间传递了什么</h3><span>${current.records.length} 个成功检查点</span></div><p class="state-note">每个角色有独立的系统提示词与短期对话。工作流通过结构化 JSON 传递任务、规划、实现和审查反馈，不把失败输出写成成功结果。导出的记录不包含服务端配置。</p><details class="state-block"><summary>用户需求与输入</summary><pre>${escapeHtml(JSON.stringify(current.input,null,2))}</pre></details>${current.records.map(r=>`<details class="state-block"><summary>${LABELS[r.step]} · ${seconds(r.metrics.duration_ms)} · ${r.metrics.calls} 次模型请求 · ${r.metrics.tokens.toLocaleString()} tokens</summary><pre>${escapeHtml(JSON.stringify({output:r.output,metrics:r.metrics,tool_events:r.tool_events},null,2))}</pre></details>`).join("")}`;
}
function renderOutput(force=false) {
  const key=JSON.stringify([current?.id,current?.status,current?.records.length,current?.logs.length,current?.active_step,current?.error,activeTab,selectedFile]);
  if (!force && key===renderKey) return;
  renderKey=key;
  let html;
  if (activeTab==="code") html=renderCode();
  else if(activeTab==="diff") html=current?.diff?`<div class="state-heading"><h3>原始代码 → 当前最新实现</h3><span>Unified diff</span></div><p class="state-note">依据实际文本计算差异。生成任务没有原始代码，因此显示新增行。</p>${codeLines(current.diff,true)}`:empty("暂无代码差异","编码完成后展示原始输入与最新实现之间的真实差异，修复结果会替换首稿。");
  else if(activeTab==="review") html=renderReview();
  else if(activeTab==="logs") html=renderLogs();
  else html=renderState();
  $("result-content").innerHTML=html;
  $("result-content").setAttribute("aria-labelledby","tab-"+activeTab);
  document.querySelectorAll("[data-file]").forEach(button=>button.addEventListener("click",()=>{selectedFile=button.dataset.file;renderOutput(true);}));
  $("copy-code")?.addEventListener("click",guard(async()=>{const file=latestDraft().files.find(f=>f.path===selectedFile);await navigator.clipboard.writeText(file.content);toast("文件内容已复制");}));
  $("download-bundle")?.addEventListener("click",()=>download("bundle.zip"));
}
function render() {
  const locked=!!current;
  $("task-form").querySelectorAll("input,textarea,select,button").forEach(el=>el.disabled=locked);
  $("start-button").hidden=locked;
  $("resume-button").hidden=!current || !["paused","error"].includes(current.status);
  $("resume-button").disabled=false;
  $("pause-button").hidden=!isRunning(); $("pause-button").disabled=false;
  $("new-run").disabled=isRunning();
  $("export-json").disabled=!current; $("export-report").disabled=!current;
  if (!locked) updateFormHint();
  renderWorkflow(); renderOutput();
  const metrics=(current?.records || []).map(r=>r.metrics);
  $("total-time").textContent=metrics.length?seconds(metrics.reduce((s,m)=>s+m.duration_ms,0)):"";
  $("total-tokens").textContent=metrics.length?metrics.reduce((s,m)=>s+m.tokens,0).toLocaleString()+" tokens":"";
  $("result-model").textContent=current?.input.provider==="demo"?"OFFLINE DEMO":config.model || "DEEPSEEK";
  $("result-message").textContent=current?({running:"协作进行中，每个成功节点都会保存",queued:"准备启动",paused:"已暂停，检查点已保留",error:"节点失败，可重试缺失步骤",completed:"代码与测试已交付，等待本地验证",needs_review:"修复后仍有问题，需要人工复核"}[current.status] || "") : "准备好，开始一个新的编码任务";
  if(current?.error) notify(current.error);
}
async function selectRun(identifier) {
  current=await api("/api/runs/"+identifier);setForm(current.input);localSet("codeflow:current",identifier);selectedFile="";renderKey="";notify("");render();
}
function newTask(input={task:"",code:"",mode:"generate",language:"python",provider:"online"}) {
  if(isRunning()){notify("请先暂停后续节点，等待当前节点保存后再新建任务。");return;}
  current=null;renderKey="";selectedFile="";activeTab="code";localSet("codeflow:current",null);setForm(input);syncTabs();notify("");render();syncDraft();
}
function syncTabs(){document.querySelectorAll("[data-tab]").forEach(b=>{const selected=b.dataset.tab===activeTab;b.setAttribute("aria-selected",String(selected));b.tabIndex=selected?0:-1;});}
function download(name){if(current)window.location.assign(`/api/runs/${current.id}/${name}`);}
async function refreshHistory(){history=(await api("/api/runs")).runs;$("history-count").textContent=history.length || "";renderHistory();}
function renderHistory(){const query=$("history-search").value.trim().toLowerCase();const list=history.filter(r=>(r.input.task+" "+r.input.language+" "+STATUS[r.status]).toLowerCase().includes(query));$("history-list").innerHTML=list.length?list.map(r=>`<button class="history-entry" data-run="${r.id}"><strong>${escapeHtml(r.input.task)}</strong><small>${escapeHtml(dateLabel(r.created_at))} · ${LANGUAGE[r.input.language]} · ${r.input.provider==="demo"?"离线演示":"DeepSeek"}</small><span>${STATUS[r.status]} · 已完成 ${r.completed_steps} 步</span></button>`).join(""):'<p class="history-note">没有匹配的运行记录。</p>';document.querySelectorAll("[data-run]").forEach(b=>b.addEventListener("click",guard(async()=>{await selectRun(b.dataset.run);$("history-dialog").close();})));}

$("task-form").addEventListener("submit",guard(async(event)=>{event.preventDefault();if(submitting || current)return;submitting=true;updateFormHint();notify("");try{const created=await api("/api/runs","POST",getForm());await selectRun(created.id);await refreshHistory();}finally{submitting=false;render();}}));
$("task-form").addEventListener("input",syncDraft);
$("task-form").addEventListener("change",syncDraft);
$("new-run").addEventListener("click",()=>newTask());
$("load-demo").addEventListener("click",()=>newTask({task:DEMO_TASK,code:DEMO_CODE,mode:"repair",language:"python",provider:"demo"}));
$("example-generate").addEventListener("click",()=>newTask({task:"用 Python 实现升序数组的二分查找，找到时返回索引，找不到返回 -1；处理空列表、单元素和边界值，并使用 unittest 编写测试。",code:"",mode:"generate",language:"python",provider:"online"}));
$("example-repair").addEventListener("click",()=>newTask({task:"修复平均值函数：空列表返回 None；非空列表返回算术平均值；不修改输入。请编写正常、空列表、负数和单元素的 unittest 测试。",code:"def average(numbers):\n    return sum(numbers) / len(numbers)\n",mode:"repair",language:"python",provider:"online"}));
$("pause-button").addEventListener("click",guard(async()=>{await api(`/api/runs/${current.id}/pause`,"POST",{});$("pause-button").disabled=true;notify("已请求暂停。当前模型调用结束并保存后停止后续节点，不会立即取消上游已接收的请求。");}));
$("resume-button").addEventListener("click",guard(async()=>{await api(`/api/runs/${current.id}/resume`,"POST",{});notify("");await selectRun(current.id);}));
document.querySelectorAll("[data-tab]").forEach(button=>{button.addEventListener("click",()=>{activeTab=button.dataset.tab;syncTabs();renderOutput();});button.addEventListener("keydown",event=>{const tabs=[...document.querySelectorAll("[data-tab]")],i=tabs.indexOf(button);let next;if(event.key==="ArrowRight")next=(i+1)%tabs.length;else if(event.key==="ArrowLeft")next=(i+tabs.length-1)%tabs.length;else if(event.key==="Home")next=0;else if(event.key==="End")next=tabs.length-1;else return;event.preventDefault();tabs[next].click();tabs[next].focus();});});
$("export-json").addEventListener("click",()=>download("export.json"));
$("export-report").addEventListener("click",()=>download("report.md"));
$("upload-code").addEventListener("click",()=>$("code-file").click());
$("code-file").addEventListener("change",guard(async event=>{const file=event.target.files[0];if(!file)return;try{if(file.size>80000)throw new Error("文件过大，请使用小于 80 KB 的单文件。");const content=await file.text();if(content.length>20000 || content.includes("\0"))throw new Error("代码需要是最多 20,000 字符的纯文本。");$("source-code").value=content;const ext=file.name.split(".").pop().toLowerCase();if({py:1,js:1,ts:1}[ext])$("language").value={py:"python",js:"javascript",ts:"typescript"}[ext];document.querySelector('input[name="mode"][value="repair"]').checked=true;syncDraft();toast("代码已载入，点击开始才会发送任务");}finally{event.target.value="";}}));
$("history-open").addEventListener("click",guard(async()=>{$("history-feedback").textContent="";await refreshHistory();$("history-dialog").showModal();$("history-search").focus();}));
$("history-close").addEventListener("click",()=>$("history-dialog").close());
$("history-search").addEventListener("input",renderHistory);
$("import-run").addEventListener("click",()=>$("import-file").click());
$("import-file").addEventListener("change",async event=>{const file=event.target.files[0];if(!file)return;try{if(file.size>500000)throw new Error("运行记录文件不能超过 500 KB。");const value=JSON.parse(await file.text()), created=await api("/api/runs/import","POST",value);await selectRun(created.id);await refreshHistory();$("history-dialog").close();toast("记录已导入；未完成节点需要点击继续才会执行");}catch(error){$("history-feedback").textContent=error.message || "无法导入该文件。";}finally{event.target.value="";}});
window.addEventListener("pagehide",()=>{if(!current)localSet("codeflow:draft",JSON.stringify(getForm()));});

async function poll(){if(polling || !current || !isRunning())return;polling=true;const identifier=current.id;try{const before=current.status;const fresh=await api("/api/runs/"+identifier);if(current?.id!==identifier)return;current=fresh;render();if(before!==current.status)await refreshHistory();}catch(error){notify("与本地服务的连接中断。重新启动后，运行记录可从最近检查点恢复。" );}finally{polling=false;}}
async function init(){
  try{
    config=await api("/api/status");$("connection").textContent=config.configured?"DeepSeek 已配置":"未配置 · 可离线演示";
    let draft;try{draft=JSON.parse(localGet("codeflow:draft") || "null");}catch{}
    if(draft && typeof draft==="object" && typeof draft.task==="string" && typeof draft.code==="string")setForm(draft);
    const saved=localGet("codeflow:current") || config.active_run;
    if(saved && /^[a-f0-9]{32}$/.test(saved)){try{await selectRun(saved);}catch{localSet("codeflow:current",null);}}
    await refreshHistory();render();
    if(!config.configured && !current)notify("尚未配置 DeepSeek。可以点击“载入离线演示”，或在项目 .env 中配置密钥后重启服务。");
  }catch(error){notify("无法连接本地服务，请运行 python workflow.py 后刷新。" );render();}
}
init();setInterval(()=>{poll();if(current && isRunning())renderWorkflow();},1000);
