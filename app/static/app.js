let people = [];
let entries = [];
let periods = [];
let calendarSubscriptions = [];
let selectedQuick = null;
let selectedModal = null;
let selectedBatch = null;
let batchPreviewKey = null;
let exportPeople = [];
let exportPeopleAllSelected = true;
const YEAR_MONTH_NAMES = ["Januar","Februar","März","April","Mai","Juni","Juli","August","September","Oktober","November","Dezember"];
let yearMonths = Array.from({length:12},(_,i)=>i+1);
let editingId = null;

const qs = s => document.querySelector(s);
const qsa = s => [...document.querySelectorAll(s)];

async function api(url, options={}) {
  const method=(options.method||"GET").toUpperCase();
  const write=method!=="GET" && method!=="HEAD" && method!=="OPTIONS";
  if(write && !serverWriteAvailable()) throw new Error(navigator.onLine ? "Datenserver nicht erreichbar - Änderungen sind nicht möglich" : "Offline - Änderungen sind nicht möglich");
  let res;
  if(write) beginPwaWrite();
  try {
    res = await fetch(url, {
      headers: {"Content-Type":"application/json", ...(options.headers||{})},
      ...options
    });
    setBackendReachable(res.headers.get("X-PWA-Offline-Cache")!=="1");
  } catch (err) {
    setBackendReachable(false);
    throw new Error(navigator.onLine ? "Server nicht erreichbar" : "Offline - noch keine lokal gespeicherten Daten verfügbar");
  } finally {
    if(write) endPwaWrite();
  }
  if (res.status === 401) {
    if(navigator.onLine) location.href="/login";
    throw new Error("Anmeldung erforderlich");
  }
  const type = res.headers.get("content-type") || "";
  const body = type.includes("json") ? await res.json() : await res.text();
  if (!res.ok) throw new Error(body.error || body || `HTTP ${res.status}`);
  return body;
}
function esc(s){ return String(s ?? "").replace(/[&<>"']/g, m=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[m])); }
function isoToday(){ const d=new Date(); return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,"0")}-${String(d.getDate()).padStart(2,"0")}`; }
function formatDateValue(value){
  if(!value) return "Datum wählen";
  const m=String(value).match(/^(\d{4})-(\d{2})-(\d{2})$/);
  return m ? `${m[3]}.${m[2]}.${m[1]}` : value;
}
function syncDateShell(input){
  const shell=input?.closest?.(".date-shell");
  const display=shell?.querySelector(".date-display");
  if(display) display.textContent=formatDateValue(input.value);
}
function syncAllDateShells(){ qsa('.date-shell input[type="date"]').forEach(syncDateShell); }
function openNativeDatePicker(input){
  if(!input || input.disabled) return;
  try{
    if(typeof input.showPicker==="function"){
      input.showPicker();
      return;
    }
  }catch(_e){}
  try{ input.focus({preventScroll:true}); }catch(_e){ try{ input.focus(); }catch(__e){} }
  try{ input.click(); }catch(_e){}
}
function ensureDatePickerButton(input){
  const shell=input?.closest?.(".date-shell");
  if(!shell || shell.querySelector(".date-picker-btn")) return;
  shell.classList.add("has-picker-button");
  const button=document.createElement("button");
  button.type="button";
  button.className="date-picker-btn";
  button.setAttribute("aria-label","Datum auswählen");
  button.setAttribute("title","Datum auswählen");
  button.addEventListener("click",event=>{
    event.preventDefault();
    event.stopPropagation();
    openNativeDatePicker(input);
  });
  shell.appendChild(button);
}
function upgradeDateInputs(){
  qsa('input[type="date"].input').forEach(input=>{
    if(input.closest(".date-shell")){
      syncDateShell(input);
      ensureDatePickerButton(input);
      return;
    }
    const shell=document.createElement("div");
    shell.className="date-shell";
    input.parentNode.insertBefore(shell,input);
    shell.appendChild(input);
    input.classList.add("date-native");
    const display=document.createElement("span");
    display.className="date-display";
    shell.appendChild(display);
    input.addEventListener("input",()=>syncDateShell(input));
    input.addEventListener("change",()=>syncDateShell(input));
    ensureDatePickerButton(input);
    syncDateShell(input);
  });
}
function formatTimeValue(value){
  return /^\d{2}:\d{2}$/.test(String(value||"")) ? String(value) : "--:--";
}
function timingPrefixFromInput(input){
  return String(input?.id||"").replace(/(?:Start|End)Time$/,"");
}
function syncTimeShell(input){
  const shell=input?.closest?.(".time-shell");
  const display=shell?.querySelector(".time-display");
  if(display) display.textContent=formatTimeValue(input.value);
  const prefix=timingPrefixFromInput(input);
  if(prefix) syncOvernight(prefix);
}
function upgradeTimeInputs(){
  qsa('input[type="time"].time-input').forEach(input=>{
    if(input.closest(".time-shell")){ syncTimeShell(input); return; }
    const shell=document.createElement("div");
    shell.className="time-shell";
    input.parentNode.insertBefore(shell,input);
    shell.appendChild(input);
    input.classList.add("time-native");
    const display=document.createElement("span");
    display.className="time-display";
    shell.appendChild(display);
    input.addEventListener("input",()=>syncTimeShell(input));
    input.addEventListener("change",()=>syncTimeShell(input));
    syncTimeShell(input);
  });
}
function monthShort(d){ return d.toLocaleDateString("de-CH",{month:"short"}).replace(".",""); }
function weekdayShort(d){ return d.toLocaleDateString("de-CH",{weekday:"short"}).replace(".",""); }
function personById(id){ return people.find(p=>Number(p.id)===Number(id)); }
function personColor(name){ return (people.find(p=>p.name===name)||{color:"#ececec"}).color; }
function entryEndDay(e){
  if(!e) return "";
  if(e.end_day) return e.end_day;
  if(Number(e.all_day)===0 && e.start_time && e.end_time && e.end_time < e.start_time) return addDaysIso(e.day,1);
  return e.day || "";
}
function entrySpansDays(e){
  return Boolean(e && Number(e.all_day)===0 && entryEndDay(e) && entryEndDay(e)>e.day);
}
function entryTimeLabel(e){
  if(!e || Number(e.all_day)!==0) return "";
  if(!e.start_time || !e.end_time) return "";
  const endDay=entryEndDay(e);
  if(!endDay || endDay===e.day) return `${e.start_time}–${e.end_time}`;
  return `${e.start_time} → ${formatDateValue(endDay)} ${e.end_time}`;
}
function syncOvernight(prefix){
  const range=qs(`#${prefix}TimeRange`);
  const start=qs(`#${prefix}StartTime`)?.value || "";
  const end=qs(`#${prefix}EndTime`)?.value || "";
  const startDay=qs(`#${prefix}Date`)?.value || "";
  const endDate=qs(`#${prefix}EndDate`);
  if(endDate){
    if(startDay){
      endDate.min=startDay;
      if(!endDate.value || endDate.value<startDay){ endDate.value=startDay; syncDateShell(endDate); }
    }
    const spans=Boolean(startDay && endDate.value && endDate.value>startDay);
    range?.classList.toggle("overnight",false);
    range?.classList.toggle("spans-days",spans);
    const hint=qs(`#${prefix}RangeHint`);
    if(hint){
      hint.hidden=!spans;
      hint.textContent=spans?`Mehrtägig: ${formatDateValue(startDay)} bis ${formatDateValue(endDate.value)}`:"";
    }
    return;
  }
  range?.classList.toggle("overnight",Boolean(start && end && end < start));
}
function autoAdvanceEndDate(prefix){
  const startDay=qs(`#${prefix}Date`)?.value || "";
  const endDate=qs(`#${prefix}EndDate`);
  const start=qs(`#${prefix}StartTime`)?.value || "";
  const end=qs(`#${prefix}EndTime`)?.value || "";
  if(!startDay || !endDate || !start || !end) return;
  if((!endDate.value || endDate.value===startDay) && end<start){
    endDate.value=addDaysIso(startDay,1);
    syncDateShell(endDate);
  }
  syncOvernight(prefix);
}
function syncTiming(prefix){
  const allDay=qs(`#${prefix}AllDay`);
  const range=qs(`#${prefix}TimeRange`);
  if(!allDay || !range) return;
  range.hidden=allDay.checked;
  const dateLabel=qs(`#${prefix}DateLabel`);
  if(dateLabel) dateLabel.textContent=allDay.checked?"Datum":"Von · Datum";
  const endDate=qs(`#${prefix}EndDate`);
  if(!allDay.checked && endDate){
    const startDay=qs(`#${prefix}Date`)?.value || "";
    if(startDay && (!endDate.value || endDate.value<startDay)){ endDate.value=startDay; syncDateShell(endDate); }
  }
  const hint=qs(`#${prefix}RangeHint`);
  if(hint && allDay.checked) hint.hidden=true;
  syncOvernight(prefix);
}
function setTiming(prefix, entry=null){
  const allDay=qs(`#${prefix}AllDay`);
  const start=qs(`#${prefix}StartTime`);
  const end=qs(`#${prefix}EndTime`);
  if(!allDay || !start || !end) return;
  allDay.checked=entry ? Number(entry.all_day)!==0 : true;
  start.value=entry?.start_time || "";
  end.value=entry?.end_time || "";
  const endDate=qs(`#${prefix}EndDate`);
  if(endDate){
    endDate.value=entry ? entryEndDay(entry) : (qs(`#${prefix}Date`)?.value || "");
    syncDateShell(endDate);
  }
  syncTimeShell(start);
  syncTimeShell(end);
  syncTiming(prefix);
}
function timingPayload(prefix){
  const allDay=qs(`#${prefix}AllDay`)?.checked ?? true;
  const payload={
    all_day:allDay,
    start_time:allDay ? "" : (qs(`#${prefix}StartTime`)?.value || ""),
    end_time:allDay ? "" : (qs(`#${prefix}EndTime`)?.value || ""),
  };
  const endDate=qs(`#${prefix}EndDate`);
  if(!allDay && endDate) payload.end_day=endDate.value || "";
  return payload;
}

async function loadPeople(){
  people = await api("/api/people");
  if (!selectedQuick && people.length) selectedQuick = people[0].id;
  if (!selectedModal && people.length) selectedModal = people[0].id;
  if (!selectedBatch && people.length) selectedBatch = people[0].id;
  renderPersonButtons("#quickPeople","quick");
  renderPersonButtons("#modalPeople","modal");
  renderBatchPersonSelect();
  renderPersonFilter();
  renderPeopleSettings();
}
async function loadEntries(){
  entries = await api("/api/entries");
  renderAll();
}
async function loadPeriods(){
  periods = await api("/api/periods");
  renderPeriodSettings();
  renderYear();
  updateDateContext("quick");
  updateDateContext("modal");
}
async function loadCalendarSubscriptions(){
  calendarSubscriptions = await api("/api/calendar-subscriptions");
  renderCalendarSubscriptions();
}

function renderPersonButtons(target, mode){
  const container=qs(target);
  if(!container) return;
  const selected = mode==="quick" ? selectedQuick : mode==="batch" ? selectedBatch : selectedModal;
  container.innerHTML = people.map(p=>`
    <button class="person-btn ${Number(selected)===Number(p.id)?"selected":""}"
      style="background:${esc(p.color)}" onclick="selectPerson('${mode}',${p.id})">${esc(p.name)}</button>
  `).join("");
}
function renderBatchPersonSelect(){
  const select=qs("#batchPerson");
  if(!select) return;
  const selected=Number(selectedBatch || people[0]?.id || 0);
  select.innerHTML=people.map(p=>`<option value="${p.id}">${esc(p.name)}</option>`).join("");
  if(selected) select.value=String(selected);
  if(select.value) selectedBatch=Number(select.value);
}
function selectPerson(mode,id){
  if(mode==="quick") {
    selectedQuick=id;
    renderPersonButtons("#quickPeople",mode);
  } else if(mode==="batch") {
    selectedBatch=id;
    renderBatchPersonSelect();
    resetBatchPreview();
  } else {
    selectedModal=id;
    renderPersonButtons("#modalPeople",mode);
  }
}

function yearOptions(select){
  const now = new Date().getFullYear();
  const years = [];
  for(let y=now-1;y<=now+2;y++) years.push(y);
  select.innerHTML=years.map(y=>`<option ${y===now?"selected":""}>${y}</option>`).join("");
}
function renderPersonFilter(){
  if(exportPeopleAllSelected || !exportPeople.length){
    exportPeople=people.map(p=>p.name);
    exportPeopleAllSelected=true;
  }else{
    exportPeople=exportPeople.filter(name=>people.some(p=>p.name===name));
    if(!exportPeople.length){
      exportPeople=people.map(p=>p.name);
      exportPeopleAllSelected=true;
    }
  }
  updateExportControls();
}
const PWA_PAGE_KEY = "betreuung-current-page";
function rememberPage(name){try{sessionStorage.setItem(PWA_PAGE_KEY,name);}catch(_e){}}
function rememberedPage(){try{return sessionStorage.getItem(PWA_PAGE_KEY)||"home";}catch(_e){return "home";}}
function showPage(name,{persist=true,scroll=true}={}){
  const page=qs("#"+name+"Page");
  if(!page) name="home";
  qsa(".page").forEach(p=>p.classList.remove("active"));
  qs("#"+name+"Page")?.classList.add("active");
  qsa(".navbtn").forEach(b=>b.classList.toggle("active",b.dataset.page===name));
  if(persist) rememberPage(name);
  if(name==="list") renderList();
  if(name==="year"){ renderYear(); renderStatsByPerson(); }
  if(name==="settings"){ loadConfig(); renderPeriodSettings(); }
  if(scroll) window.scrollTo({top:0,behavior:"smooth"});
}
function renderNext(){
  const today=isoToday();
  const end=addDaysIso(today,6);
  const rows=[];
  for(const e of entries){
    if(e.day>=today && e.day<=end) rows.push({day:e.day,kind:1,html:itemHtml(e)});
    for(const day of entryContinuationDays(e)){
      if(day>=today && day<=end) rows.push({day,kind:0,html:continuationItemHtml(e,day)});
    }
  }
  rows.sort((a,b)=>a.day.localeCompare(b.day)||a.kind-b.kind);
  qs("#nextList").innerHTML = rows.length ? rows.map(r=>r.html).join("") : '<div class="small">Keine kommenden Einträge.</div>';
}
function itemHtml(e){
  const d=new Date(e.day+"T12:00:00");
  return `<div class="item" onclick="openModal(${e.id})">
    <div class="datebox"><b>${d.getDate()}</b><span>${monthShort(d)}</span></div>
    <div><div class="who"><span class="dot" style="background:${esc(e.color)}"></span>${esc(e.person)}</div>
    <div class="note">${weekdayShort(d)}${entryTimeLabel(e)?" · "+esc(entryTimeLabel(e)):""}${e.note?" · "+esc(e.note):""}</div></div>
    <button class="kebab">›</button>
  </div>`;
}
function addDaysIso(day, amount=1){
  const d=new Date(day+"T12:00:00");
  d.setDate(d.getDate()+amount);
  return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,"0")}-${String(d.getDate()).padStart(2,"0")}`;
}
function entryContinuationDays(e){
  if(!entrySpansDays(e)) return [];
  const days=[];
  const finalDay=entryEndDay(e);
  let current=addDaysIso(e.day,1);
  while(current<=finalDay && days.length<3662){
    days.push(current);
    current=addDaysIso(current,1);
  }
  return days;
}
function continuationItemHtml(e, day){
  const d=new Date(day+"T12:00:00");
  const from=formatIsoDate(e.day);
  const finalDay=entryEndDay(e);
  const continuationText=day===finalDay?`bis ${e.end_time}`:"ganzer Tag";
  return `<div class="item continuation-item" onclick="openModal(${e.id})">
    <div class="datebox continuation-date"><b>${d.getDate()}</b><span>${monthShort(d)}</span></div>
    <div><div class="who"><span class="dot" style="background:${esc(e.color)}"></span>${esc(e.person)} <span class="continuation-badge">Fortsetzung</span></div>
    <div class="note">${weekdayShort(d)} · ${esc(continuationText)} · vom ${esc(from)}${e.note?" · "+esc(e.note):""}</div></div>
    <button class="kebab" aria-label="Ursprünglichen Termin öffnen">›</button>
  </div>`;
}
function exportPeopleAreAll(){
  return people.length>0 && exportPeople.length===people.length && people.every(p=>exportPeople.includes(p.name));
}
function exportParams(){
  const params=new URLSearchParams();
  const year=qs("#filterYear").value;
  const search=qs("#filterSearch").value.toLowerCase().trim();
  if(year) params.set("year",year);
  if(search) params.set("q",search);
  if(!exportPeopleAreAll()) exportPeople.forEach(name=>params.append("person",name));
  return params;
}
function calendarRangeParams(){
  const params=new URLSearchParams();
  const from=qs("#listRangeFrom")?.value || "";
  const to=qs("#listRangeTo")?.value || "";
  const search=qs("#filterSearch").value.toLowerCase().trim();
  if(from) params.set("from",from);
  if(to) params.set("to",to);
  if(search) params.set("q",search);
  if(!exportPeopleAreAll()) exportPeople.forEach(name=>params.append("person",name));
  return params;
}
function setCalendarRangeForYear(year){
  if(!year) return;
  const from=qs("#listRangeFrom"), to=qs("#listRangeTo");
  if(from){from.value=`${year}-01-01`;syncDateShell(from);}
  if(to){to.value=`${year}-12-31`;syncDateShell(to);}
}
function updateExportControls(){
  const params=exportParams();
  qs("#listCsvLink").href=`/export.csv?${params.toString()}`;
  qs("#listPdfButton").dataset.url=`/export.pdf?${params.toString()}`;
  const rangeQuery=calendarRangeParams().toString();
  const icsButton=qs("#listIcsButton");
  if(icsButton) icsButton.dataset.url=`/export.ics?${rangeQuery}`;
  const appleButton=qs("#listAppleCalendarButton");
  if(appleButton) appleButton.dataset.url=`/export.ics?${rangeQuery}${rangeQuery?"&":""}open=1`;
  const button=qs("#listExportPeople");
  if(button){
    if(exportPeopleAreAll()) button.textContent="Personen: Alle";
    else if(exportPeople.length===1) button.textContent=`Person: ${exportPeople[0]}`;
    else button.textContent=`Personen: ${exportPeople.length}`;
  }
}
function openExportPeopleModal(){
  const grid=qs("#exportPeopleGrid");
  grid.innerHTML=people.map(p=>`<label class="export-person-option"><input type="checkbox" value="${esc(p.name)}" ${exportPeople.includes(p.name)?"checked":""}><span class="dot" style="background:${esc(p.color)}"></span><span>${esc(p.name)}</span></label>`).join("");
  qs("#exportPeopleModalBack").classList.add("open");
  document.body.classList.add("modal-open");
}
function closeExportPeopleModal(){
  qs("#exportPeopleModalBack").classList.remove("open");
  if(!qs("#modalBack")?.classList.contains("open")&&!qs("#batchModalBack")?.classList.contains("open")) document.body.classList.remove("modal-open");
}
function setExportPeopleChecks(mode){
  const boxes=qsa('#exportPeopleGrid input[type="checkbox"]');
  if(mode==="all") boxes.forEach(b=>b.checked=true);
}
function applyExportPeopleSelection(){
  const chosen=qsa('#exportPeopleGrid input[type="checkbox"]:checked').map(b=>b.value);
  if(!chosen.length) return toast("Mindestens eine Person wählen");
  exportPeople=chosen;
  exportPeopleAllSelected=exportPeopleAreAll();
  closeExportPeopleModal();
  renderList();
  toast(exportPeopleAreAll()?"Alle Personen ausgewählt":chosen.length===1?chosen[0]:`${chosen.length} Personen ausgewählt`);
}
function yearMonthsAreAll(){
  return yearMonths.length===12 && Array.from({length:12},(_,i)=>i+1).every(m=>yearMonths.includes(m));
}
function updateYearMonthButton(){
  const button=qs("#yearMonthSelect");
  if(!button) return;
  if(yearMonthsAreAll()) button.textContent="Monate: Alle";
  else if(yearMonths.length===1) button.textContent=`Monat: ${YEAR_MONTH_NAMES[yearMonths[0]-1]}`;
  else button.textContent=`Monate: ${yearMonths.length}`;
}
function openYearMonthsModal(){
  const grid=qs("#yearMonthsGrid");
  grid.innerHTML=YEAR_MONTH_NAMES.map((name,i)=>`<label class="export-person-option month-option"><input type="checkbox" value="${i+1}" ${yearMonths.includes(i+1)?"checked":""}><span>${esc(name)}</span></label>`).join("");
  qs("#yearMonthsModalBack").classList.add("open");
  document.body.classList.add("modal-open");
}
function closeYearMonthsModal(){
  qs("#yearMonthsModalBack").classList.remove("open");
  if(!qs("#modalBack")?.classList.contains("open")&&!qs("#batchModalBack")?.classList.contains("open")&&!qs("#exportPeopleModalBack")?.classList.contains("open")) document.body.classList.remove("modal-open");
}
function setYearMonthChecks(mode){
  const boxes=qsa('#yearMonthsGrid input[type="checkbox"]');
  if(mode==="all") boxes.forEach(b=>b.checked=true);
}
function applyYearMonthSelection(){
  const chosen=qsa('#yearMonthsGrid input[type="checkbox"]:checked').map(b=>Number(b.value)).sort((a,b)=>a-b);
  if(!chosen.length) return toast("Mindestens einen Monat wählen");
  yearMonths=chosen;
  closeYearMonthsModal();
  updateYearMonthButton();
  renderYear();
  toast(yearMonthsAreAll()?"Alle Monate ausgewählt":chosen.length===1?YEAR_MONTH_NAMES[chosen[0]-1]:`${chosen.length} Monate ausgewählt`);
}
function yearMonthQuery(){
  const params=new URLSearchParams();
  if(!yearMonthsAreAll()) yearMonths.forEach(m=>params.append("month",String(m)));
  return params;
}
function renderList(){
  const year=qs("#filterYear").value;
  const search=qs("#filterSearch").value.toLowerCase().trim();
  let source=entries.slice();
  if(!exportPeopleAreAll()) source=source.filter(e=>exportPeople.includes(e.person));
  if(search) source=source.filter(e=>(e.note||"").toLowerCase().includes(search)||e.person.toLowerCase().includes(search));

  const rows=[];
  for(const e of source){
    if(e.day.startsWith(year)) rows.push({day:e.day, kind:1, html:itemHtml(e)});
    for(const continuationDay of entryContinuationDays(e)){
      if(continuationDay.startsWith(year)) rows.push({day:continuationDay, kind:0, html:continuationItemHtml(e,continuationDay)});
    }
  }
  rows.sort((a,b)=>a.day.localeCompare(b.day)||a.kind-b.kind);
  qs("#fullList").innerHTML=rows.length?rows.map(r=>r.html).join(""):'<div class="small">Keine Einträge.</div>';

  updateExportControls();
  const exportLabel=exportPeopleAreAll()?"alle Personen":exportPeople.length===1?exportPeople[0]:`${exportPeople.length} Personen`;
  qs("#listExportHint").textContent=`${year} · ${exportLabel}`;
}
function renderStats(){
  const today=isoToday();
  const year=String(new Date().getFullYear());
  qs("#statUpcoming").textContent=entries.filter(e=>entryEndDay(e)>=today).length;
  qs("#statPeople").textContent=new Set(entries.map(e=>e.person)).size;
  qs("#statYear").textContent=entries.filter(e=>e.day.startsWith(year)).length;
}
function periodsForDay(day){
  if(!day) return [];
  return periods.filter(p=>p.start_day<=day && p.end_day>=day);
}
function updateDateContext(prefix){
  const input=qs(`#${prefix}Date`);
  const box=qs(`#${prefix}DateContext`);
  if(!input || !box) return;
  const matches=periodsForDay(input.value);
  if(!matches.length){box.innerHTML="";box.classList.remove("visible");return;}
  box.innerHTML=matches.map(p=>`<div class="date-context-item"><i class="bar-swatch" style="background:${esc(p.color)}"></i><span><b>${esc(periodKindName(p.kind))}:</b> ${esc(p.label)}</span></div>`).join("");
  box.classList.add("visible");
}
function periodKindName(kind){
  return kind==="vacation" ? "Ferien" : kind==="holiday" ? "Feiertag" : "Zeitraum";
}
function periodMapForYear(year){
  const map=new Map();
  const first=new Date(year,0,1,12);
  const last=new Date(year,11,31,12);
  for(const p of periods){
    let start=new Date(p.start_day+"T12:00:00");
    let end=new Date(p.end_day+"T12:00:00");
    if(end<first || start>last) continue;
    if(start<first) start=new Date(first);
    if(end>last) end=new Date(last);
    for(let d=new Date(start);d<=end;d.setDate(d.getDate()+1)){
      const iso=`${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,"0")}-${String(d.getDate()).padStart(2,"0")}`;
      if(!map.has(iso)) map.set(iso,[]);
      map.get(iso).push(p);
    }
  }
  return map;
}
function renderYear(){
  const year=Number(qs("#yearSelect").value);
  const visibleMonths=yearMonths.map(m=>m-1);
  const byDay=new Map(entries.filter(e=>e.day.startsWith(String(year))).map(e=>[e.day,e]));
  const continuationByDay=new Map();
  for(const e of entries){
    for(const continuationDay of entryContinuationDays(e)){
      if(continuationDay.startsWith(String(year))) continuationByDay.set(continuationDay,e);
    }
  }
  const marks=periodMapForYear(year);
  const tableClass=visibleMonths.length===1?"year month-view":"year";
  let out=`<table class="${tableClass}"><colgroup><col class="day-col">${visibleMonths.map(()=>'<col class="month-col">').join("")}<col class="day-col"></colgroup><thead><tr><th>Tag</th>${visibleMonths.map(i=>`<th>${YEAR_MONTH_NAMES[i]}</th>`).join("")}<th>Tag</th></tr></thead><tbody>`;
  for(let day=1;day<=31;day++){
    out+=`<tr><td>${day}</td>`;
    for(const month of visibleMonths){
      const d=new Date(year,month,day,12);
      const valid=d.getFullYear()===year&&d.getMonth()===month&&d.getDate()===day;
      if(!valid){out+='<td class="invalid"></td>';continue;}
      const iso=`${year}-${String(month+1).padStart(2,"0")}-${String(day).padStart(2,"0")}`;
      const e=byDay.get(iso);
      const continuation=continuationByDay.get(iso);
      const dayMarks=marks.get(iso)||[];
      const weekend=d.getDay()===0||d.getDay()===6;
      const fill=e?`<div class="entry-fill" style="background:${esc(e.color)}"><span>${esc(e.person)}</span></div>`:"";
      const continuationFinal=continuation?entryEndDay(continuation):"";
      const continuationText=continuation?(iso===continuationFinal?`bis ${continuation.end_time}`:"ganzer Tag"):"";
      const continuationTitle=continuation?`Fortsetzung ${continuation.person} vom ${formatDateValue(continuation.day)} · ${continuationText}`:"";
      const continuationFill=continuation?`<button type="button" class="year-continuation" style="background:${esc(continuation.color)}" title="${esc(continuationTitle)}" aria-label="${esc(continuationTitle)}" onclick="event.stopPropagation();openModal(${continuation.id})"><span>↳ ${esc(continuation.person)} · ${esc(continuationText)}</span></button>`:"";
      const markTitle=dayMarks.map(p=>`${periodKindName(p.kind)}: ${p.label}`).join(" · ");
      const rail=dayMarks.length?`<div class="period-rail" title="${esc(markTitle)}">${dayMarks.map(p=>`<span class="period-segment" style="background:${esc(p.color)}"></span>`).join("")}</div>`:"";
      const cellClass=[weekend?"weekend":"",dayMarks.length?"has-period":"",continuation?"has-continuation":""].filter(Boolean).join(" ");
      out+=`<td class="${cellClass}" onclick="${e?`openModal(${e.id})`:`prefillDate('${iso}')`}"><div class="year-cell-content">${fill}${continuationFill}${rail}</div></td>`;
    }
    out+=`<td class="day-repeat">${day}</td></tr>`;
  }
  out+='</tbody></table>';
  qs("#yearTableWrap").innerHTML=out;
  const periodLegend=[];
  const seen=new Set();
  for(const p of periods.filter(p=>p.start_day<=`${year}-12-31`&&p.end_day>=`${year}-01-01`)){
    const key=`${p.kind}|${p.color}`;
    if(seen.has(key)) continue;
    seen.add(key);
    periodLegend.push(`<span class="period-legend"><i class="bar-swatch" style="background:${esc(p.color)}"></i>${periodKindName(p.kind)}</span>`);
  }
  qs("#legend").innerHTML='<span class="legend-title">Farblegende:</span>'+people.map(p=>`<span><i class="dot" style="background:${esc(p.color)}"></i>${esc(p.name)}</span>`).join("")+`<span><i class="dot" style="background:var(--weekend)"></i>Wochenende</span><span class="continuation-legend">↳ mehrtägiger Eintrag</span>`+periodLegend.join("");
  const monthParams=yearMonthQuery();
  const monthQuery=monthParams.toString();
  qs("#csvLink").href=`/export.csv?year=${year}${monthQuery?`&${monthQuery}`:""}`;
  updateYearMonthButton();
}

async function renderStatsByPerson(){
  const year=qs("#yearSelect").value;
  const data=await api(`/api/stats?year=${encodeURIComponent(year)}`);
  qs("#personStats").innerHTML=data.per_person.length?data.per_person.map(x=>{
    const base=`year=${encodeURIComponent(year)}&person=${encodeURIComponent(x.name)}`;
    return `<span class="stat-chip"><i class="dot" style="background:${esc(x.color)}"></i>${esc(x.name)}: ${x.count} <a class="stat-chip-link server-export" href="/export.csv?${base}" title="CSV exportieren">CSV</a> <button class="stat-chip-link pdf-share-button server-export" type="button" data-url="/export.pdf?${base}" title="PDF teilen oder drucken">PDF</button></span>`;
  }).join(""):'<span class="small">Noch keine Einträge.</span>';
  applyOnlineState();
}
function renderAll(){ renderStats(); renderNext(); renderList(); renderYear(); renderStatsByPerson(); }

function openModal(id=null){
  editingId=id;
  const e=id?entries.find(x=>Number(x.id)===Number(id)):null;
  qs("#modalTitle").textContent=e?"Eintrag bearbeiten":"Betreuung eintragen";
  qs("#modalDate").value=e?e.day:(qs("#quickDate").value||isoToday());
  syncDateShell(qs("#modalDate"));
  updateDateContext("modal");
  qs("#modalNote").value=e?e.note:"";
  setTiming("modal",e);
  selectedModal=e?e.person_id:(people[0]?.id||null);
  renderPersonButtons("#modalPeople","modal");
  qs("#modalDuplicate").style.display=e?"block":"none";
  qs("#modalShare").style.display=e?"block":"none";
  qs("#modalDelete").style.display=e?"block":"none";
  qs("#modalBack").classList.add("open");
}
function closeModal(){qs("#modalBack").classList.remove("open");editingId=null;}
function prefillDate(day){openModal();qs("#modalDate").value=day;syncDateShell(qs("#modalDate"));updateDateContext("modal");}

function formatIsoDate(day){
  if(!day) return "";
  const d=new Date(day+"T12:00:00");
  return d.toLocaleDateString("de-CH",{day:"2-digit",month:"2-digit",year:"numeric"});
}
function resetBatchPreview(){
  batchPreviewKey=null;
  const box=qs("#batchPreviewBox");
  if(box){ box.hidden=true; box.innerHTML=""; }
  const create=qs("#batchCreate");
  if(create) create.disabled=true;
}
function batchPayload(){
  return {
    person_id:Number(selectedBatch),
    weekday:Number(qs("#batchWeekday").value),
    start_day:qs("#batchStart").value,
    end_day:qs("#batchEnd").value,
    note:qs("#batchNote").value.trim(),
    skip_vacations:qs("#batchSkipVacations")?.checked||false,
    skip_holidays:qs("#batchSkipHolidays")?.checked||false,
    ...timingPayload("batch"),
  };
}
function openBatchModal(){
  if(!navigator.onLine) return toast("Batch-Erstellung benötigt eine Serververbindung");
  selectedBatch=selectedQuick || people[0]?.id || null;
  renderBatchPersonSelect();
  const now=new Date();
  qs("#batchStart").value=isoToday();
  qs("#batchEnd").value=`${now.getFullYear()}-12-31`;
  syncDateShell(qs("#batchStart"));
  syncDateShell(qs("#batchEnd"));
  qs("#batchWeekday").value=String((now.getDay()+6)%7);
  qs("#batchNote").value="";
  if(qs("#batchSkipVacations")) qs("#batchSkipVacations").checked=false;
  if(qs("#batchSkipHolidays")) qs("#batchSkipHolidays").checked=false;
  setTiming("batch");
  resetBatchPreview();
  qs("#batchModalBack").classList.add("open");
  document.body.classList.add("modal-open");
  applyOnlineState();
}
function closeBatchModal(){
  qs("#batchModalBack").classList.remove("open");
  if(!qs("#modalBack")?.classList.contains("open")) document.body.classList.remove("modal-open");
  resetBatchPreview();
}
async function previewBatch(){
  if(!selectedBatch) return toast("Betreuung wählen");
  const payload=batchPayload();
  if(!payload.start_day||!payload.end_day) return toast("Von und Bis wählen");
  try{
    const data=await api("/api/entries/batch/preview",{method:"POST",body:JSON.stringify(payload)});
    const weekday=qs("#batchWeekday").selectedOptions[0]?.textContent || "Wochentag";
    const occupiedText=(data.occupied||[]).map(x=>`${formatIsoDate(x.day)} (${x.person})`).join(" · ");
    qs("#batchPreviewBox").title=occupiedText ? `Bereits belegt: ${occupiedText}` : "";
    const skipped=[]; if(data.occupied?.length) skipped.push(`${data.occupied.length} belegt`); if(data.vacation_count) skipped.push(`${data.vacation_count} Ferien`); if(data.holiday_count) skipped.push(`${data.holiday_count} Feiertag${data.holiday_count===1?"":"e"}`);
    qs("#batchPreviewBox").innerHTML=`<b>${data.matched_count} ${esc(weekday)}</b><span>${data.create_count} neu${skipped.length?" · "+skipped.join(" · "):""}</span>`;
    qs("#batchPreviewBox").hidden=false;
    batchPreviewKey=JSON.stringify(payload);
    qs("#batchCreate").disabled=data.create_count===0;
  }catch(e){ resetBatchPreview(); toast(e.message); }
}
async function createBatch(){
  const payload=batchPayload();
  if(batchPreviewKey!==JSON.stringify(payload)){
    resetBatchPreview();
    return toast("Bitte Vorschau nochmals berechnen");
  }
  try{
    const data=await api("/api/entries/batch",{method:"POST",body:JSON.stringify(payload)});
    closeBatchModal();
    await loadEntries();
    toast(`${data.created_count} Einträge erstellt · ${data.skipped_count} übersprungen`);
  }catch(e){toast(e.message);}
}

function toast(msg){
  const t=qs("#toast");t.textContent=msg;t.classList.add("show");setTimeout(()=>t.classList.remove("show"),1800);
}
async function saveEntry(day,personId,note,timing,id=null){
  const payload={day,person_id:Number(personId),note,...timing};
  if(id) await api(`/api/entries/${id}`,{method:"PUT",body:JSON.stringify(payload)});
  else await api("/api/entries",{method:"POST",body:JSON.stringify(payload)});
  await loadEntries();
}

function renderPeopleSettings(){
  qs("#peopleSettings").innerHTML=people.map(p=>{
    const subtitle=p.ical_title?esc(p.ical_title):'<span class="person-title-default">globaler Kalendertitel</span>';
    return `<div class="person-compact-card">
      <button class="person-compact-main" type="button" onclick="openPersonEditor(${p.id})" aria-label="${esc(p.name)} bearbeiten">
        <span class="person-color-dot" style="background:${esc(p.color)}"></span>
        <span class="person-compact-text"><strong>${esc(p.name)}</strong><small>${subtitle}</small></span>
        <span class="person-edit-mark">Bearbeiten</span>
      </button>
      <button class="person-delete-btn" type="button" onclick="event.stopPropagation();deletePerson(${p.id})" aria-label="${esc(p.name)} löschen">×</button>
    </div>`;
  }).join("");
}
function openPersonEditor(id){
  const p=people.find(x=>Number(x.id)===Number(id));
  if(!p)return;
  qs("#personEditId").value=p.id;
  qs("#personEditName").value=p.name||"";
  qs("#personEditColor").value=p.color||"#ececec";
  qs("#personEditIcalTitle").value=p.ical_title||"";
  qs("#personEditorBack").classList.add("open");
  document.body.classList.add("modal-open");
}
function closePersonEditor(){
  qs("#personEditorBack").classList.remove("open");
  document.body.classList.remove("modal-open");
}
async function savePersonEditor(){
  const id=Number(qs("#personEditId").value);
  const name=qs("#personEditName").value.trim();
  if(!name)return toast("Name fehlt");
  try{
    await api(`/api/people/${id}`,{method:"PUT",body:JSON.stringify({name,color:qs("#personEditColor").value,ical_title:qs("#personEditIcalTitle").value.trim()})});
    closePersonEditor();
    await loadPeople(); await loadEntries(); toast("Person aktualisiert");
  }catch(e){toast(e.message);}
}
async function deletePerson(id){
  if(!confirm("Person wirklich löschen?"))return;
  try{await api(`/api/people/${id}`,{method:"DELETE"});await loadPeople();await loadEntries();toast("Person gelöscht");}
  catch(e){toast(e.message);}
}
function renderPeriodSettings(){
  const box=qs("#periodList");
  if(!box) return;
  const listedPeriods=periods.filter(p=>!String(p.source||"").startsWith("subscription:"));
  box.innerHTML=listedPeriods.length?listedPeriods.map(p=>{
    const same=p.start_day===p.end_day;
    const range=same?p.start_day:`${p.start_day} - ${p.end_day}`;
    return `<div class="period-item"><span class="bar-swatch large" style="background:${esc(p.color)}"></span><div><b>${esc(p.label)}</b><div class="small">${periodKindName(p.kind)} · ${esc(range)}${p.source==="ics"?" · ICS":""}</div></div><button class="mini danger" onclick="deletePeriod(${p.id})">×</button></div>`;
  }).join(""):'<div class="small">Noch keine Ferien oder Feiertage erfasst.</div>';
  applyOnlineState();
}
async function addPeriod(){
  const start=qs("#periodStart").value;
  const end=qs("#periodEnd").value;
  const kind=qs("#periodKind").value;
  const label=qs("#periodLabel").value.trim();
  const color=qs("#periodColor").value;
  if(!start||!end) return toast("Von und Bis wählen");
  try{
    await api("/api/periods",{method:"POST",body:JSON.stringify({start_day:start,end_day:end,kind,label,color})});
    qs("#periodLabel").value="";
    await loadPeriods();
    toast("Zeitraum gespeichert");
  }catch(e){toast(e.message);}
}
async function deletePeriod(id){
  if(!confirm("Zeitraum wirklich löschen?")) return;
  try{await api(`/api/periods/${id}`,{method:"DELETE"});await loadPeriods();toast("Zeitraum gelöscht");}
  catch(e){toast(e.message);}
}

function webcalUrl(url){
  return String(url||"").replace(/^https?:/i,"webcal:");
}
async function copyCalendarUrl(url, label="Kalenderlink"){
  try{
    await navigator.clipboard.writeText(url);
    toast(`${label} kopiert`);
  }catch(_e){
    toast("Link markieren und kopieren");
  }
}
function renderCalendarSubscriptions(){
  const box=qs("#subscriptionList"); if(!box) return;
  box.innerHTML=calendarSubscriptions.length?calendarSubscriptions.map(sub=>{
    const ok=(sub.last_status||"").startsWith("OK");
    return `<div class="subscription-item">
      <div class="subscription-item-head"><i class="bar-swatch large" style="background:${esc(sub.color)}"></i><div class="spacer"><b>${esc(sub.name)}</b><div class="subscription-url">${esc(sub.url)}</div></div><label class="toggle-row"><input class="sub-enabled" type="checkbox" data-id="${sub.id}" ${Number(sub.enabled)?"checked":""}> aktiv</label></div>
      <div class="subscription-status ${ok?"":"danger"}">${esc(sub.last_status||"Noch nicht synchronisiert")}${sub.last_sync_at?` · ${esc(new Date(sub.last_sync_at).toLocaleString("de-CH"))}`:""}</div>
      <div class="subscription-item-actions"><button class="secondary sub-sync" data-id="${sub.id}" type="button">Jetzt aktualisieren</button><button class="secondary danger sub-delete" data-id="${sub.id}" type="button">Löschen</button></div>
    </div>`;
  }).join(""):'<div class="small">Noch keine Kalender-Abos eingerichtet.</div>';
  qsa(".sub-sync").forEach(btn=>btn.addEventListener("click",()=>syncSubscription(Number(btn.dataset.id))));
  qsa(".sub-delete").forEach(btn=>btn.addEventListener("click",()=>deleteSubscription(Number(btn.dataset.id))));
  qsa(".sub-enabled").forEach(chk=>chk.addEventListener("change",()=>toggleSubscription(Number(chk.dataset.id),chk.checked)));
  applyOnlineState();
}
async function addSubscription(){
  const payload={name:qs("#subName").value.trim(),url:qs("#subUrl").value.trim(),kind:qs("#subKind").value,color:qs("#subColor").value};
  if(!payload.name||!payload.url) return toast("Name und Kalender-URL eingeben");
  try{
    const result=await api("/api/calendar-subscriptions",{method:"POST",body:JSON.stringify(payload)});
    qs("#subName").value="";qs("#subUrl").value="";
    await loadCalendarSubscriptions(); await loadPeriods();
    toast(result.warning?`Abo gespeichert · Sync: ${result.warning}`:`Abo gespeichert · ${result.imported||0} Termine`);
  }catch(e){toast(e.message);}
}
async function syncSubscription(id){
  try{const r=await api(`/api/calendar-subscriptions/${id}/sync`,{method:"POST",body:"{}"});await loadCalendarSubscriptions();await loadPeriods();toast(`${r.imported||0} Termine aktualisiert`);}catch(e){await loadCalendarSubscriptions();toast(e.message);}
}
async function toggleSubscription(id,enabled){
  const sub=calendarSubscriptions.find(x=>Number(x.id)===Number(id)); if(!sub) return;
  try{await api(`/api/calendar-subscriptions/${id}`,{method:"PUT",body:JSON.stringify({...sub,enabled})});await loadCalendarSubscriptions();await loadPeriods();toast(enabled?"Abo aktiviert":"Abo deaktiviert");}catch(e){toast(e.message);}
}
async function deleteSubscription(id){
  if(!confirm("Kalender-Abo und seine synchronisierten Markierungen löschen?")) return;
  try{await api(`/api/calendar-subscriptions/${id}`,{method:"DELETE"});await loadCalendarSubscriptions();await loadPeriods();toast("Kalender-Abo gelöscht");}catch(e){toast(e.message);}
}
function toggleQrBox(boxId,imgId,url){
  const box=qs(boxId), img=qs(imgId); if(!box||!img) return;
  const show=box.style.display==="none"||!box.style.display;
  box.style.display=show?"block":"none";
  if(show && !img.src) img.src=url+`?v=${Date.now()}`;
}

function renderPersonIcalFeeds(items=[]){
  const box=qs("#icalPersonList");
  if(!box) return;
  if(!items.length){box.innerHTML="";return;}
  box.innerHTML=`<div class="ical-section-label">Pro Person</div>`+items.map(item=>`
    <details class="ical-feed">
      <summary class="ical-feed-head"><span class="dot" style="background:${esc(item.color||"#ececec")}"></span><b>${esc(item.name)}</b></summary>
      <div class="ical-feed-body">
        <div class="pathbox calendar-url-display">${esc(item.url)}</div>
        <div class="ical-feed-actions">
          <button class="secondary ical-copy" type="button" data-url="${esc(item.url)}" data-name="${esc(item.name)}">Kopieren</button>
          <a class="secondary link-button" href="${esc(webcalUrl(item.url))}">Abonnieren</a>
          <button class="secondary person-qr" type="button" data-id="${item.id}">QR-Code</button>
          <button class="secondary danger person-revoke" type="button" data-id="${item.id}" data-name="${esc(item.name)}">Freigabe widerrufen</button>
        </div>
        <div id="personQrBox-${item.id}" class="qr-box" style="display:none"><img id="personQrImage-${item.id}" alt="QR-Code ${esc(item.name)}"></div>
      </div>
    </details>`).join("");
  qsa(".ical-copy").forEach(btn=>btn.addEventListener("click",()=>copyCalendarUrl(btn.dataset.url,`${btn.dataset.name}-Link`)));
  qsa(".person-qr").forEach(btn=>btn.addEventListener("click",()=>toggleQrBox(`#personQrBox-${btn.dataset.id}`,`#personQrImage-${btn.dataset.id}`,`/api/people/${btn.dataset.id}/calendar-qr.png`)));
  qsa(".person-revoke").forEach(btn=>btn.addEventListener("click",async()=>{
    if(!confirm(`Freigabe für ${btn.dataset.name} widerrufen? Der bisherige Kalender-Link funktioniert danach sofort nicht mehr.`)) return;
    try{await api(`/api/people/${btn.dataset.id}/calendar-token/reset`,{method:"POST",body:"{}"});await loadConfig();toast("Freigabe widerrufen · neuen Link weitergeben");}catch(e){toast(e.message);}
  }));
}
async function loadConfig(){
  const c=await api("/api/config");
  qs("#dataFile").textContent=c.data_file+"  |  Backups: "+c.backup_dir;
  if(c.ical_enabled){
    qs("#icalBox").textContent=c.ical_url;
    qs("#copyIcal").style.display="";
    qs("#copyIcal").dataset.url=c.ical_url;
    qs("#showGlobalQr").style.display="";
    qs("#showGlobalQr").dataset.url=c.ical_qr_url||"/api/calendar-qr.png";
    qs("#subscribeIcal").style.display="";
    qs("#subscribeIcal").href=webcalUrl(c.ical_url);
    qs("#icalSecurityHint").style.display="";
    renderPersonIcalFeeds(c.ical_person_urls||[]);
  }else{
    qs("#icalBox").textContent="Nicht aktiviert. In Portainer ICAL_TOKEN setzen.";
    qs("#copyIcal").style.display="none";
    qs("#showGlobalQr").style.display="none";
    qs("#globalQrBox").style.display="none";
    qs("#subscribeIcal").style.display="none";
    qs("#icalSecurityHint").style.display="none";
    renderPersonIcalFeeds([]);
  }
  if(qs("#icalTitleTemplate")) qs("#icalTitleTemplate").value=c.ical_title_template||"{person}";
  loadHistory();
}

function compactHistoryText(value, maxLen=120){
  const text=String(value||"").replace(/<[^>]*>/g," ").replace(/\s+/g," ").trim();
  return text.length>maxLen?text.slice(0,maxLen-1)+"…":text;
}

async function loadHistory(){
  const box=qs("#historyList"); if(!box) return;
  try{
    const rows=await api("/api/history");
    const labels={created:"Erstellt",updated:"Geändert",deleted:"Gelöscht",restored:"Wiederhergestellt"};
    box.innerHTML=rows.length?rows.map(h=>{const x=h.snapshot||{};return `<div class="history-item"><div><b>${labels[h.action]||esc(h.action)}</b> · ${esc(formatDateValue(x.day||""))} · ${esc(x.person||"")}<div class="small">${esc(new Date(h.created_at).toLocaleString("de-CH"))}${x.note?" · "+esc(compactHistoryText(x.note)):""}</div></div>${h.action==="deleted"?`<button class="secondary history-restore" data-id="${h.id}">Wiederherstellen</button>`:""}</div>`}).join(""):'<div class="small">Noch keine Änderungen protokolliert.</div>';
    qsa(".history-restore").forEach(b=>b.addEventListener("click",async()=>{try{await api(`/api/history/${b.dataset.id}/restore`,{method:"POST",body:"{}"});await loadEntries();await loadHistory();toast("Wiederhergestellt");}catch(e){toast(e.message);}}));
  }catch(e){
    console.error("Änderungsverlauf konnte nicht geladen werden", e);
    box.innerHTML='<div class="small danger">Änderungsverlauf konnte nicht geladen werden.</div>';
  }
}



function filenameFromDisposition(value, fallback){
  if(!value) return fallback;
  const utf=value.match(/filename\*=UTF-8''([^;]+)/i);
  if(utf){try{return decodeURIComponent(utf[1].replace(/["']/g,""));}catch(_e){}}
  const plain=value.match(/filename="?([^";]+)"?/i);
  return plain?.[1] || fallback;
}

async function shareServerPdf(url, fallbackName="betreuungsplan.pdf"){
  if(!navigator.onLine){toast("PDF benötigt eine Serververbindung");return;}
  try{
    toast("PDF wird erstellt …");
    const response=await trackedPwaFetch(url,{credentials:"same-origin",cache:"no-store"});
    if(response.status===401){location.href="/login";return;}
    if(!response.ok) throw new Error(`PDF konnte nicht erstellt werden (HTTP ${response.status})`);
    const blob=await response.blob();
    const filename=filenameFromDisposition(response.headers.get("content-disposition"),fallbackName);
    const file=new File([blob],filename,{type:"application/pdf"});

    if(navigator.share){
      const canShareFiles=!navigator.canShare || navigator.canShare({files:[file]});
      if(canShareFiles){
        try{
          await navigator.share({title:filename,files:[file]});
          return;
        }catch(error){
          if(error?.name==="AbortError") return;
          console.warn("Datei-Teilen fehlgeschlagen, verwende Download-Fallback",error);
        }
      }
    }

    const blobUrl=URL.createObjectURL(blob);
    const link=document.createElement("a");
    link.href=blobUrl;
    link.download=filename;
    link.rel="noopener";
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(()=>URL.revokeObjectURL(blobUrl),60000);
    toast("PDF geöffnet");
  }catch(error){
    console.error(error);
    toast(error.message || "PDF-Export fehlgeschlagen");
  }
}

async function shareServerFile(url, fallbackName, mimeType, preparing="Datei wird erstellt …"){
  if(!navigator.onLine){toast("Teilen benötigt eine Serververbindung");return;}
  try{
    toast(preparing);
    const response=await trackedPwaFetch(url,{credentials:"same-origin",cache:"no-store"});
    if(response.status===401){location.href="/login";return;}
    if(!response.ok) throw new Error(`Export fehlgeschlagen (HTTP ${response.status})`);
    const blob=await response.blob();
    const filename=filenameFromDisposition(response.headers.get("content-disposition"),fallbackName);
    const file=new File([blob],filename,{type:mimeType || blob.type || "application/octet-stream"});
    if(navigator.share){
      const canShareFiles=!navigator.canShare || navigator.canShare({files:[file]});
      if(canShareFiles){
        try{await navigator.share({title:filename,files:[file]});return;}
        catch(error){if(error?.name==="AbortError")return;}
      }
    }
    const blobUrl=URL.createObjectURL(blob);
    const link=document.createElement("a");
    link.href=blobUrl; link.download=filename; link.rel="noopener";
    document.body.appendChild(link); link.click(); link.remove();
    setTimeout(()=>URL.revokeObjectURL(blobUrl),60000);
    toast("Datei bereit");
  }catch(error){console.error(error);toast(error.message || "Export fehlgeschlagen");}
}


const PWA_APP_VERSION = "51";
const PWA_UPDATE_RELOAD_KEY = "betreuung-pwa-update-reload";
let pwaRegistration = null;
let pwaWaitingWorker = null;
let pwaWriteOperations = 0;
let pwaDirtySinceLoad = false;
let pwaLastUpdateCheck = 0;
let pwaReloadTimer = null;
let pwaReloadIssued = false;
let pwaBackendReachable = true;

function setBackendReachable(value){
  const next=Boolean(value);
  if(pwaBackendReachable===next) return;
  pwaBackendReachable=next;
  try{applyOnlineState();}catch(_e){}
}

function serverWriteAvailable(){return navigator.onLine && pwaBackendReachable;}

function setPwaUpdateStatus(text, updateAvailable=false){
  const versionEl=qs("#pwaAppVersion");
  const statusEl=qs("#pwaUpdateStatus");
  const button=qs("#pwaApplyUpdate");
  if(versionEl) versionEl.textContent=`v${PWA_APP_VERSION}`;
  if(statusEl) statusEl.textContent=text||"";
  if(button) button.hidden=!updateAvailable;
}

function beginPwaWrite(){ pwaWriteOperations += 1; }
function endPwaWrite(){ pwaWriteOperations = Math.max(0, pwaWriteOperations-1); }

function markPotentialUnsavedInput(event){
  const target=event.target;
  if(!(target instanceof HTMLElement)) return;
  if(!target.matches("input,textarea,select")) return;
  if(target.matches("#filterSearch,#filterYear,#yearSelect,#listRangeFrom,#listRangeTo")) return;
  pwaDirtySinceLoad=true;
}

function pwaCriticalUpdateReason(){
  if(pwaWriteOperations>0) return "Es läuft gerade ein Schreib-, Import- oder Restore-Vorgang.";
  if(document.querySelector(".modalback.open")) return "Bitte zuerst den geöffneten Dialog schließen oder speichern.";
  const selectedFile=[...document.querySelectorAll('input[type="file"]')].some(input=>input.files?.length);
  if(selectedFile) return "Bitte zuerst den ausgewählten Datei-Import abschließen oder die Datei entfernen.";
  return "";
}

function queryWorkerVersion(worker){
  return new Promise(resolve=>{
    if(!worker) return resolve("");
    const channel=new MessageChannel();
    const timer=setTimeout(()=>resolve(""),1200);
    channel.port1.onmessage=event=>{clearTimeout(timer);resolve(String(event.data?.version||""));};
    try{worker.postMessage({type:"GET_VERSION"},[channel.port2]);}
    catch(_error){clearTimeout(timer);resolve("");}
  });
}

async function announceWaitingWorker(worker){
  if(!worker) return;
  pwaWaitingWorker=worker;
  const version=await queryWorkerVersion(worker);
  const label=version?`v${version}`:"eine neue Version";
  setPwaUpdateStatus(`Neue Version verfügbar: ${label}. Sie ist bereits lokal geladen.`,true);
}

async function checkPwaUpdate(force=false){
  if(!pwaRegistration || !navigator.onLine) return;
  const now=Date.now();
  if(!force && now-pwaLastUpdateCheck<15*60*1000) return;
  pwaLastUpdateCheck=now;
  try{await pwaRegistration.update();}catch(_error){}
}

function reloadOnceForPwaUpdate(){
  if(pwaReloadIssued) return;
  let requested=false;
  try{requested=sessionStorage.getItem(PWA_UPDATE_RELOAD_KEY)==="1";}catch(_e){}
  if(!requested) return;
  pwaReloadIssued=true;
  try{sessionStorage.removeItem(PWA_UPDATE_RELOAD_KEY);}catch(_e){}
  clearTimeout(pwaReloadTimer);
  location.reload();
}

function activateWaitingWorker(worker,{startup=false}={}){
  if(!worker) return false;
  pwaWaitingWorker=worker;
  try{sessionStorage.setItem(PWA_UPDATE_RELOAD_KEY,"1");}catch(_e){}
  const onState=()=>{
    if(worker.state==="activated") reloadOnceForPwaUpdate();
  };
  worker.addEventListener("statechange",onState);
  setPwaUpdateStatus(startup?"Bereits geladene neue Version wird sicher aktiviert …":"Update wird sicher aktiviert …",false);
  try{worker.postMessage({type:"ACTIVATE_UPDATE",userInitiated:true});}
  catch(_e){return false;}
  if(worker.state==="activated") reloadOnceForPwaUpdate();
  clearTimeout(pwaReloadTimer);
  pwaReloadTimer=setTimeout(()=>{
    // No forced loop: if iOS delays activation, keep the current version stable.
    try{sessionStorage.removeItem(PWA_UPDATE_RELOAD_KEY);}catch(_e){}
    setPwaUpdateStatus("Update ist vollständig geladen und wird beim nächsten sicheren Start übernommen.",false);
  },10000);
  return true;
}

async function applyPwaUpdate(){
  if(!pwaWaitingWorker){setPwaUpdateStatus("Keine wartende Version gefunden.",false);return;}
  const reason=pwaCriticalUpdateReason();
  if(reason){toast(reason);return;}
  if(pwaDirtySinceLoad && !confirm("Seit dem Start wurden Eingaben geändert. Jetzt aktualisieren? Nicht gespeicherte Eingaben können verloren gehen.")) return;
  activateWaitingWorker(pwaWaitingWorker,{startup:false});
}

function wirePwaUpdateUi(){
  setPwaUpdateStatus("Updates werden im Hintergrund geprüft.",false);
  qs("#pwaApplyUpdate")?.addEventListener("click",applyPwaUpdate);
  document.addEventListener("input",markPotentialUnsavedInput,true);
  document.addEventListener("change",markPotentialUnsavedInput,true);
  navigator.serviceWorker?.addEventListener("controllerchange",()=>{
    // Some browsers switch the controller immediately, others only after navigation.
    // The one-shot guard prevents duplicate reloads if statechange fires as well.
    reloadOnceForPwaUpdate();
  });
  document.addEventListener("visibilitychange",()=>{if(!document.hidden)checkPwaUpdate(false);});
}

async function trackedPwaFetch(url,options={}){
  const method=String(options.method||"GET").toUpperCase();
  const write=!(["GET","HEAD","OPTIONS"].includes(method));
  if(write) beginPwaWrite();
  try{
    const response=await fetch(url,options);
    setBackendReachable(response.headers.get("X-PWA-Offline-Cache")!=="1");
    return response;
  }catch(error){setBackendReachable(false);throw error;}
  finally{if(write) endPwaWrite();}
}

function applyOnlineState(){
  const online=navigator.onLine;
  const serverReady=online && pwaBackendReachable;
  document.body.classList.toggle("is-offline",!serverReady);
  const banner=qs("#offlineBanner");
  if(banner){
    banner.hidden=serverReady;
    banner.textContent=!online?"Offline – App-Oberfläche läuft lokal. Änderungen sind deaktiviert.":"Datenserver nicht erreichbar – letzter lesbarer Stand. Änderungen sind deaktiviert.";
  }
  ["#quickSave","#modalSave","#modalDuplicate","#modalShare","#modalDelete","#openBatch","#batchPreview","#addPerson","#addPeriod"].forEach(sel=>{
    const el=qs(sel); if(el) el.disabled=!serverReady;
  });
  qsa("#importForm input,#importForm button,#icsImportForm input,#icsImportForm button,#fullDataImportForm input,#fullDataImportForm button,#peopleSettings input,#peopleSettings button,#periodList button,#newPerson,#newColor,#periodStart,#periodEnd,#periodKind,#periodLabel,#periodColor,#subName,#subUrl,#subKind,#subColor,#addSubscription,#subscriptionList button,#subscriptionList input,#batchModalBack input,#batchModalBack select").forEach(el=>el.disabled=!serverReady);
  const batchCreate=qs("#batchCreate");
  if(batchCreate) batchCreate.disabled=!serverReady || !batchPreviewKey;
  qsa(".server-export").forEach(el=>{
    el.setAttribute("aria-disabled",serverReady?"false":"true");
    el.tabIndex=serverReady?0:-1;
    if("disabled" in el) el.disabled=!serverReady;
  });
}

async function probeBackend(){
  if(!navigator.onLine) return false;
  try{
    const response=await trackedPwaFetch("/api/config",{credentials:"same-origin",cache:"no-store"});
    setBackendReachable(response.ok || response.status===401 || response.status===403);
    return pwaBackendReachable;
  }catch(_e){setBackendReachable(false);return false;}
}

async function refreshAfterReconnect(){
  if(!(await probeBackend())) return;
  try{await loadPeople();await loadEntries();await loadPeriods();await loadConfig();toast("Datenserver wieder erreichbar - Daten aktualisiert");}
  catch(e){toast(e.message);}
}

async function registerPwa(){
  if(!("serviceWorker" in navigator)){setPwaUpdateStatus("Service Worker wird von diesem Browser nicht unterstützt.");return;}
  try{
    const reg=await navigator.serviceWorker.register("/service-worker.js",{scope:"/",updateViaCache:"none"});
    pwaRegistration=reg;
    // A fully downloaded update left waiting from the previous session is safe to
    // activate now: startup has not loaded data or accepted user input yet.
    if(reg.waiting && navigator.serviceWorker.controller){
      activateWaitingWorker(reg.waiting,{startup:true});
      return "activating";
    }
    if(reg.waiting) announceWaitingWorker(reg.waiting);
    reg.addEventListener("updatefound",()=>{
      const worker=reg.installing;
      if(!worker)return;
      worker.addEventListener("statechange",()=>{
        if(worker.state==="installed" && navigator.serviceWorker.controller) announceWaitingWorker(worker);
        if(worker.state==="installed" && !navigator.serviceWorker.controller) setPwaUpdateStatus("Offline-Basis installiert. Ab dem nächsten Start läuft die App aus dem lokalen App-Cache.",false);
      });
    });
    setTimeout(()=>checkPwaUpdate(true),1200);
  }catch(e){console.warn("PWA Service Worker konnte nicht registriert werden",e);setPwaUpdateStatus("PWA-Updateprüfung momentan nicht verfügbar.",false);}
}

window.addEventListener("offline",()=>{applyOnlineState();toast("Offline - Änderungen sind deaktiviert");});
window.addEventListener("online",()=>{applyOnlineState();refreshAfterReconnect();checkPwaUpdate(true);});
setInterval(()=>{if(navigator.onLine && !pwaBackendReachable) probeBackend();},30000);
document.addEventListener("click",e=>{
  const pdfButton=e.target.closest(".pdf-share-button");
  if(pdfButton){
    e.preventDefault();
    if(!navigator.onLine){toast("PDF benötigt eine Serververbindung");return;}
    shareServerPdf(pdfButton.dataset.url,"betreuungsliste.pdf");
    return;
  }
  const link=e.target.closest("a.server-export");
  if(link && !navigator.onLine){e.preventDefault();toast("PDF/CSV Export benötigt eine Serververbindung");}
});

document.addEventListener("DOMContentLoaded", async ()=>{
  wirePwaUpdateUi();
  yearOptions(qs("#yearSelect"));
  yearOptions(qs("#filterYear"));
  upgradeDateInputs();
  upgradeTimeInputs();
  qs("#quickDate").value=isoToday();
  syncDateShell(qs("#quickDate"));
  setCalendarRangeForYear(qs("#filterYear").value);
  qs("#quickDate").addEventListener("change",()=>{updateDateContext("quick");syncOvernight("quick");});
  qs("#modalDate").addEventListener("change",()=>{updateDateContext("modal");syncOvernight("modal");});
  setTiming("quick");
  ["quick","modal","batch"].forEach(prefix=>{
    qs(`#${prefix}AllDay`)?.addEventListener("change",()=>{
      syncTiming(prefix);
      if(prefix==="batch") resetBatchPreview();
    });
    qs(`#${prefix}StartTime`)?.addEventListener("change",()=>{autoAdvanceEndDate(prefix);syncOvernight(prefix);if(prefix==="batch")resetBatchPreview();});
    qs(`#${prefix}EndTime`)?.addEventListener("change",()=>{autoAdvanceEndDate(prefix);syncOvernight(prefix);if(prefix==="batch")resetBatchPreview();});
    qs(`#${prefix}EndDate`)?.addEventListener("change",()=>syncOvernight(prefix));
  });

  qs("#quickSave").addEventListener("click",async()=>{
    try{
      if(!qs("#quickDate").value||!selectedQuick)return toast("Datum und Betreuung wählen");
      const quickTiming=timingPayload("quick");
      if(!quickTiming.all_day && !quickTiming.end_day) return toast("Bis-Datum wählen");
      await saveEntry(qs("#quickDate").value,selectedQuick,qs("#quickNote").value,quickTiming);
      qs("#quickNote").value="";toast("Gespeichert");
    }catch(e){toast(e.message);}
  });
  qs("#openBatch").addEventListener("click",openBatchModal);
  qs("#listExportPeople").addEventListener("click",openExportPeopleModal);
  qs("#listPdfButton").addEventListener("click",()=>{
    const url=qs("#listPdfButton").dataset.url;
    if(url) shareServerPdf(url,"betreuungsliste.pdf");
  });
  qs("#yearPdfButton").addEventListener("click",()=>{
    const year=qs("#yearSelect").value;
    const params=yearMonthQuery();
    const query=params.toString();
    const suffix=yearMonthsAreAll()?"":yearMonths.length===1?`-${String(yearMonths[0]).padStart(2,"0")}`:`-${yearMonths.length}-monate`;
    const url=`/export-year.pdf?year=${encodeURIComponent(year)}${query?`&${query}`:""}`;
    shareServerPdf(url,`jahresplan-${year}${suffix}.pdf`);
  });
  qs("#exportPeopleAll").addEventListener("click",()=>setExportPeopleChecks("all"));
  qs("#exportPeopleApply").addEventListener("click",applyExportPeopleSelection);
  qs("#batchPreview").addEventListener("click",previewBatch);
  qs("#batchCreate").addEventListener("click",createBatch);
  qs("#batchPerson").addEventListener("change",()=>{selectedBatch=Number(qs("#batchPerson").value);resetBatchPreview();});
  ["#batchWeekday","#batchStart","#batchEnd","#batchNote","#batchSkipVacations","#batchSkipHolidays"].forEach(sel=>{
    qs(sel).addEventListener(sel==="#batchNote"?"input":"change",resetBatchPreview);
  });
  qs("#modalSave").addEventListener("click",async()=>{
    try{
      if(!qs("#modalDate").value||!selectedModal)return toast("Datum und Betreuung wählen");
      const modalTiming=timingPayload("modal");
      if(!modalTiming.all_day && !modalTiming.end_day) return toast("Bis-Datum wählen");
      await saveEntry(qs("#modalDate").value,selectedModal,qs("#modalNote").value,modalTiming,editingId);
      closeModal();toast("Gespeichert");
    }catch(e){toast(e.message);}
  });
  qs("#modalDuplicate").addEventListener("click",()=>{
    if(!editingId) return;
    const e=entries.find(x=>Number(x.id)===Number(editingId)); if(!e) return;
    editingId=null; qs("#modalTitle").textContent="Termin duplizieren";
    qs("#modalDate").value=addDaysIso(e.day,7); syncDateShell(qs("#modalDate"));
    if(qs("#modalEndDate")){ qs("#modalEndDate").value=addDaysIso(entryEndDay(e),7); syncDateShell(qs("#modalEndDate")); syncOvernight("modal"); }
    qs("#modalDelete").style.display="none"; qs("#modalShare").style.display="none"; qs("#modalDuplicate").style.display="none";
    toast("Neues Datum wählen und speichern");
  });
  qs("#modalShare").addEventListener("click",()=>{
    if(!editingId) return;
    const e=entries.find(x=>Number(x.id)===Number(editingId));
    const fallback=e?`betreuung-${e.day}.ics`:`betreuung.ics`;
    shareServerFile(`/api/entries/${editingId}/ics`,fallback,"text/calendar","Kalendereintrag wird erstellt …");
  });
  qs("#modalDelete").addEventListener("click",async()=>{
    if(!editingId||!confirm("Eintrag wirklich löschen?"))return;
    try{await api(`/api/entries/${editingId}`,{method:"DELETE"});closeModal();await loadEntries();toast("Gelöscht");}
    catch(e){toast(e.message);}
  });

  qs("#saveIcalTitle")?.addEventListener("click",async()=>{try{const v=qs("#icalTitleTemplate").value.trim()||"{person}";await api("/api/config",{method:"PUT",body:JSON.stringify({ical_title_template:v})});toast("Kalendertitel gespeichert");}catch(e){toast(e.message);}});
  qs("#refreshHistory")?.addEventListener("click",loadHistory);
  qs("#filterYear").addEventListener("change",()=>{setCalendarRangeForYear(qs("#filterYear").value);renderList();});
  qs("#listRangeFrom")?.addEventListener("change",updateExportControls);
  qs("#listRangeTo")?.addEventListener("change",updateExportControls);
  qs("#listIcsButton")?.addEventListener("click",()=>{
    const from=qs("#listRangeFrom").value, to=qs("#listRangeTo").value;
    if(!from || !to) return toast("Von und Bis wählen");
    if(from>to) return toast("Von liegt nach Bis");
    shareServerFile(qs("#listIcsButton").dataset.url,`betreuung-${from}-bis-${to}.ics`,"text/calendar","Kalenderdatei wird erstellt …");
  });
  qs("#listAppleCalendarButton")?.addEventListener("click",()=>{
    const from=qs("#listRangeFrom").value, to=qs("#listRangeTo").value;
    if(!from || !to) return toast("Von und Bis wählen");
    if(from>to) return toast("Von liegt nach Bis");
    const url=qs("#listAppleCalendarButton").dataset.url;
    if(!url) return;
    // Direct navigation is intentional: iOS handles an inline text/calendar response
    // more reliably than a File object shared from an installed PWA.
    window.location.href=url;
  });
  qs("#filterSearch").addEventListener("input",renderList);
  qs("#yearSelect").addEventListener("change",()=>{renderYear();renderStatsByPerson();});
  qs("#yearMonthSelect").addEventListener("click",openYearMonthsModal);
  qs("#yearMonthsAll").addEventListener("click",()=>setYearMonthChecks("all"));
  qs("#yearMonthsApply").addEventListener("click",applyYearMonthSelection);
  qs("#addPerson").addEventListener("click",async()=>{
    const name=qs("#newPerson").value.trim();if(!name)return;
    try{
      await api("/api/people",{method:"POST",body:JSON.stringify({name,color:qs("#newColor").value})});
      qs("#newPerson").value="";await loadPeople();toast("Person hinzugefügt");
    }catch(e){toast(e.message);}
  });
  qs("#personEditorSave").addEventListener("click",savePersonEditor);

  qs("#copyIcal").addEventListener("click",()=>copyCalendarUrl(qs("#copyIcal").dataset.url));
  qs("#showGlobalQr").addEventListener("click",()=>toggleQrBox("#globalQrBox","#globalQrImage",qs("#showGlobalQr").dataset.url||"/api/calendar-qr.png"));
  qs("#periodStart").value=isoToday();
  qs("#periodEnd").value=isoToday();
  syncDateShell(qs("#periodStart"));
  syncDateShell(qs("#periodEnd"));
  qs("#addPeriod").addEventListener("click",addPeriod);
  qs("#periodKind").addEventListener("change",()=>{
    qs("#periodColor").value=qs("#periodKind").value==="holiday"?"#d65a6f":qs("#periodKind").value==="vacation"?"#f2a65a":"#80a4c2";
  });
  qs("#addSubscription").addEventListener("click",addSubscription);
  qs("#subKind").addEventListener("change",()=>{qs("#subColor").value=qs("#subKind").value==="holiday"?"#d65a6f":qs("#subKind").value==="vacation"?"#f2a65a":"#80a4c2";});
  qs("#icsImportForm").addEventListener("submit",async(e)=>{
    e.preventDefault();
    if(!navigator.onLine) return toast("ICS Import benötigt eine Serververbindung");
    const fd=new FormData(e.target);
    try{
      const res=await trackedPwaFetch("/import.ics",{method:"POST",body:fd});
      const data=await res.json();
      if(!res.ok) throw new Error(data.error||"ICS Import fehlgeschlagen");
      await loadPeriods();
      toast(`${data.imported} Feiertage importiert${data.skipped?`, ${data.skipped} übersprungen`:""}`);
    }catch(err){toast(err.message);}
  });

  const logoutForm=qs("#logoutForm");
  if(logoutForm) logoutForm.addEventListener("submit",()=>{
    if(navigator.serviceWorker?.controller) navigator.serviceWorker.controller.postMessage({type:"CLEAR_PRIVATE_DATA"});
  });

  qs("#fullDataExport").addEventListener("click",()=>{
    shareServerFile("/export-data.json","betreuungsplan-backup.json","application/json");
  });
  qs("#fullDataImportForm").addEventListener("submit",async(e)=>{
    e.preventDefault();
    if(!navigator.onLine) return toast("Import benötigt eine Serververbindung");
    if(!confirm("Aktuelle Personen, Einträge, Ferien und Feiertage durch dieses Backup ersetzen? Vorher wird automatisch ein Sicherheitsbackup erstellt.")) return;
    const fd=new FormData(e.target);
    try{
      toast("Backup wird wiederhergestellt …");
      const res=await trackedPwaFetch("/import-data.json",{method:"POST",body:fd});
      const data=await res.json();
      if(!res.ok) throw new Error(data.error||"Import fehlgeschlagen");
      await loadPeople(); await loadEntries(); await loadPeriods(); await loadCalendarSubscriptions(); await loadConfig();
      e.target.reset();
      toast(`${data.people} Personen, ${data.entries} Einträge, ${data.periods} Zeiträume, ${data.calendar_subscriptions||0} Abos wiederhergestellt`);
    }catch(err){toast(err.message);}
  });

  qs("#importForm").addEventListener("submit",async(e)=>{
    e.preventDefault();
    const fd=new FormData(e.target);
    try{
      const res=await trackedPwaFetch("/import.csv",{method:"POST",body:fd});
      const data=await res.json();
      if(!res.ok)throw new Error(data.error||"Import fehlgeschlagen");
      await loadPeople();await loadEntries();toast(`${data.imported} Zeilen importiert`);
    }catch(err){toast(err.message);}
  });

  applyOnlineState();
  await registerPwa();
  try{await loadPeople();}catch(e){toast(e.message);}
  try{await loadEntries();}catch(e){toast(e.message);}
  try{await loadPeriods();}catch(e){toast(e.message);}
  try{await loadCalendarSubscriptions();}catch(e){if(navigator.onLine) toast(e.message);}
  try{await loadConfig();}catch(e){if(navigator.onLine) toast(e.message);}
  showPage(rememberedPage(),{persist:false,scroll:false});
  applyOnlineState();
});
