let people = [];
let entries = [];
let periods = [];
let selectedQuick = null;
let selectedModal = null;
let selectedBatch = null;
let batchPreviewKey = null;
let exportPeople = [];
let exportPeopleAllSelected = true;
const YEAR_MONTH_NAMES = ["Januar","Februar","März","April","Mai","Juni","Juli","August","September","Oktober","November","Dezember"];
let yearMonths = Array.from({length:12},(_,i)=>i+1);
let editingId = null;
let reloadingForServiceWorker = false;

const qs = s => document.querySelector(s);
const qsa = s => [...document.querySelectorAll(s)];

async function api(url, options={}) {
  const method=(options.method||"GET").toUpperCase();
  if(method!=="GET" && !navigator.onLine) throw new Error("Offline - Änderungen sind nicht möglich");
  let res;
  try {
    res = await fetch(url, {
      headers: {"Content-Type":"application/json", ...(options.headers||{})},
      ...options
    });
  } catch (err) {
    throw new Error(navigator.onLine ? "Server nicht erreichbar" : "Offline - noch keine lokal gespeicherten Daten verfügbar");
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
function upgradeDateInputs(){
  qsa('input[type="date"].input').forEach(input=>{
    if(input.closest(".date-shell")){ syncDateShell(input); return; }
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
    syncDateShell(input);
  });
}
function monthShort(d){ return d.toLocaleDateString("de-CH",{month:"short"}).replace(".",""); }
function weekdayShort(d){ return d.toLocaleDateString("de-CH",{weekday:"short"}).replace(".",""); }
function personById(id){ return people.find(p=>Number(p.id)===Number(id)); }
function personColor(name){ return (people.find(p=>p.name===name)||{color:"#ececec"}).color; }

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
function showPage(name){
  qsa(".page").forEach(p=>p.classList.remove("active"));
  qs("#"+name+"Page").classList.add("active");
  qsa(".navbtn").forEach(b=>b.classList.toggle("active",b.dataset.page===name));
  if(name==="list") renderList();
  if(name==="year"){ renderYear(); renderStatsByPerson(); }
  if(name==="settings"){ loadConfig(); renderPeriodSettings(); }
  window.scrollTo({top:0,behavior:"smooth"});
}
function renderNext(){
  const today=isoToday();
  const arr=entries.filter(e=>e.day>=today).sort((a,b)=>a.day.localeCompare(b.day)).slice(0,5);
  qs("#nextList").innerHTML = arr.length ? arr.map(itemHtml).join("") : '<div class="small">Keine kommenden Einträge.</div>';
}
function itemHtml(e){
  const d=new Date(e.day+"T12:00:00");
  return `<div class="item" onclick="openModal(${e.id})">
    <div class="datebox"><b>${d.getDate()}</b><span>${monthShort(d)}</span></div>
    <div><div class="who"><span class="dot" style="background:${esc(e.color)}"></span>${esc(e.person)}</div>
    <div class="note">${weekdayShort(d)}${e.note?" · "+esc(e.note):""}</div></div>
    <button class="kebab">›</button>
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
function updateExportControls(){
  const params=exportParams();
  qs("#listCsvLink").href=`/export.csv?${params.toString()}`;
  qs("#listPdfButton").dataset.url=`/export.pdf?${params.toString()}`;
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
  let arr=entries.filter(e=>e.day.startsWith(year));
  if(!exportPeopleAreAll()) arr=arr.filter(e=>exportPeople.includes(e.person));
  if(search) arr=arr.filter(e=>(e.note||"").toLowerCase().includes(search)||e.person.toLowerCase().includes(search));
  arr.sort((a,b)=>a.day.localeCompare(b.day));
  qs("#fullList").innerHTML=arr.length?arr.map(itemHtml).join(""):'<div class="small">Keine Einträge.</div>';

  updateExportControls();
  const exportLabel=exportPeopleAreAll()?"alle Personen":exportPeople.length===1?exportPeople[0]:`${exportPeople.length} Personen`;
  qs("#listExportHint").textContent=`${year} · ${exportLabel}`;
}
function renderStats(){
  const today=isoToday();
  const year=String(new Date().getFullYear());
  qs("#statUpcoming").textContent=entries.filter(e=>e.day>=today).length;
  qs("#statPeople").textContent=new Set(entries.map(e=>e.person)).size;
  qs("#statYear").textContent=entries.filter(e=>e.day.startsWith(year)).length;
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
      const dayMarks=marks.get(iso)||[];
      const weekend=d.getDay()===0||d.getDay()===6;
      const fill=e?`<div class="entry-fill" style="background:${esc(e.color)}"><span>${esc(e.person)}</span></div>`:"";
      const markTitle=dayMarks.map(p=>`${periodKindName(p.kind)}: ${p.label}`).join(" · ");
      const rail=dayMarks.length?`<div class="period-rail" title="${esc(markTitle)}">${dayMarks.map(p=>`<span class="period-segment" style="background:${esc(p.color)}"></span>`).join("")}</div>`:"";
      const cellClass=[weekend?"weekend":"",dayMarks.length?"has-period":""].filter(Boolean).join(" ");
      out+=`<td class="${cellClass}" onclick="${e?`openModal(${e.id})`:`prefillDate('${iso}')`}"><div class="year-cell-content">${fill}${rail}</div></td>`;
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
  qs("#legend").innerHTML='<span class="legend-title">Farblegende:</span>'+people.map(p=>`<span><i class="dot" style="background:${esc(p.color)}"></i>${esc(p.name)}</span>`).join("")+`<span><i class="dot" style="background:var(--weekend)"></i>Wochenende</span>`+periodLegend.join("");
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
  qs("#modalNote").value=e?e.note:"";
  selectedModal=e?e.person_id:(people[0]?.id||null);
  renderPersonButtons("#modalPeople","modal");
  qs("#modalDelete").style.display=e?"block":"none";
  qs("#modalBack").classList.add("open");
}
function closeModal(){qs("#modalBack").classList.remove("open");editingId=null;}
function prefillDate(day){openModal();qs("#modalDate").value=day;syncDateShell(qs("#modalDate"));}

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
    qs("#batchPreviewBox").innerHTML=`<b>${data.matched_count} ${esc(weekday)}</b><span>${data.create_count} neu · ${data.skipped_count} belegt</span>`;
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
async function saveEntry(day,personId,note,id=null){
  const payload={day,person_id:Number(personId),note};
  if(id) await api(`/api/entries/${id}`,{method:"PUT",body:JSON.stringify(payload)});
  else await api("/api/entries",{method:"POST",body:JSON.stringify(payload)});
  await loadEntries();
}

function renderPeopleSettings(){
  qs("#peopleSettings").innerHTML=people.map(p=>`
    <div class="person-setting">
      <input type="color" class="color" value="${esc(p.color)}" onchange="updatePerson(${p.id})" id="pc-${p.id}">
      <input class="name" value="${esc(p.name)}" id="pn-${p.id}" onchange="updatePerson(${p.id})">
      <button class="mini danger" onclick="deletePerson(${p.id})">×</button>
    </div>`).join("");
}
async function updatePerson(id){
  try{
    await api(`/api/people/${id}`,{method:"PUT",body:JSON.stringify({name:qs(`#pn-${id}`).value,color:qs(`#pc-${id}`).value})});
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
  box.innerHTML=periods.length?periods.map(p=>{
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
function renderPersonIcalFeeds(items=[]){
  const box=qs("#icalPersonList");
  if(!box) return;
  if(!items.length){box.innerHTML="";return;}
  box.innerHTML=`<div class="ical-section-label">Pro Person</div>`+items.map(item=>`
    <div class="ical-feed">
      <div class="ical-feed-head"><span class="dot" style="background:${esc(item.color||"#ececec")}"></span><b>${esc(item.name)}</b></div>
      <div class="pathbox compact">${esc(item.url)}</div>
      <div class="row topgap">
        <button class="secondary ical-copy" type="button" data-url="${esc(item.url)}" data-name="${esc(item.name)}">Link kopieren</button>
        <a class="secondary link-button" href="${esc(webcalUrl(item.url))}">Auf iPhone abonnieren</a>
      </div>
    </div>`).join("");
  qsa(".ical-copy").forEach(btn=>btn.addEventListener("click",()=>copyCalendarUrl(btn.dataset.url,`${btn.dataset.name}-Link`)));
}
async function loadConfig(){
  const c=await api("/api/config");
  qs("#dataFile").textContent=c.data_file+"  |  Backups: "+c.backup_dir;
  if(c.ical_enabled){
    qs("#icalBox").textContent=c.ical_url;
    qs("#copyIcal").style.display="";
    qs("#copyIcal").dataset.url=c.ical_url;
    qs("#subscribeIcal").style.display="";
    qs("#subscribeIcal").href=webcalUrl(c.ical_url);
    qs("#icalSecurityHint").style.display="";
    renderPersonIcalFeeds(c.ical_person_urls||[]);
  }else{
    qs("#icalBox").textContent="Nicht aktiviert. In Portainer ICAL_TOKEN setzen.";
    qs("#copyIcal").style.display="none";
    qs("#subscribeIcal").style.display="none";
    qs("#icalSecurityHint").style.display="none";
    renderPersonIcalFeeds([]);
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
    const response=await fetch(url,{credentials:"same-origin",cache:"no-store"});
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

async function shareServerFile(url, fallbackName, mimeType){
  if(!navigator.onLine){toast("Export benötigt eine Serververbindung");return;}
  try{
    toast("Backup wird erstellt …");
    const response=await fetch(url,{credentials:"same-origin",cache:"no-store"});
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
    toast("Backup exportiert");
  }catch(error){console.error(error);toast(error.message || "Export fehlgeschlagen");}
}

function applyOnlineState(){
  const online=navigator.onLine;
  document.body.classList.toggle("is-offline",!online);
  const banner=qs("#offlineBanner");
  if(banner) banner.hidden=online;
  ["#quickSave","#modalSave","#modalDelete","#openBatch","#batchPreview","#addPerson","#addPeriod"].forEach(sel=>{
    const el=qs(sel); if(el) el.disabled=!online;
  });
  qsa("#importForm input,#importForm button,#icsImportForm input,#icsImportForm button,#fullDataImportForm input,#fullDataImportForm button,#peopleSettings input,#peopleSettings button,#periodList button,#newPerson,#newColor,#periodStart,#periodEnd,#periodKind,#periodLabel,#periodColor,#batchModalBack input,#batchModalBack select").forEach(el=>el.disabled=!online);
  const batchCreate=qs("#batchCreate");
  if(batchCreate) batchCreate.disabled=!online || !batchPreviewKey;
  qsa(".server-export").forEach(el=>{
    el.setAttribute("aria-disabled",online?"false":"true");
    el.tabIndex=online?0:-1;
    if("disabled" in el) el.disabled=!online;
  });
}

async function refreshAfterReconnect(){
  try{await loadPeople();await loadEntries();await loadPeriods();await loadConfig();toast("Wieder online - Daten aktualisiert");}
  catch(e){toast(e.message);}
}

async function registerPwa(){
  if(!("serviceWorker" in navigator)) return;
  const hadController=Boolean(navigator.serviceWorker.controller);
  try{
    const reg=await navigator.serviceWorker.register("/service-worker.js",{scope:"/"});
    await reg.update();
    if(reg.waiting) reg.waiting.postMessage({type:"SKIP_WAITING"});
    reg.addEventListener("updatefound",()=>{
      const worker=reg.installing;
      if(!worker)return;
      worker.addEventListener("statechange",()=>{
        if(worker.state==="installed" && navigator.serviceWorker.controller){
          worker.postMessage({type:"SKIP_WAITING"});
        }
      });
    });
    navigator.serviceWorker.addEventListener("controllerchange",()=>{
      if(!hadController || reloadingForServiceWorker)return;
      reloadingForServiceWorker=true;
      location.reload();
    });
  }catch(e){console.warn("PWA Service Worker konnte nicht registriert werden",e);}
}

window.addEventListener("offline",()=>{applyOnlineState();toast("Offline - Änderungen sind deaktiviert");});
window.addEventListener("online",()=>{applyOnlineState();refreshAfterReconnect();});
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
  yearOptions(qs("#yearSelect"));
  yearOptions(qs("#filterYear"));
  upgradeDateInputs();
  qs("#quickDate").value=isoToday();
  syncDateShell(qs("#quickDate"));

  qs("#quickSave").addEventListener("click",async()=>{
    try{
      if(!qs("#quickDate").value||!selectedQuick)return toast("Datum und Betreuung wählen");
      await saveEntry(qs("#quickDate").value,selectedQuick,qs("#quickNote").value);
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
  ["#batchWeekday","#batchStart","#batchEnd","#batchNote"].forEach(sel=>{
    qs(sel).addEventListener(sel==="#batchNote"?"input":"change",resetBatchPreview);
  });
  qs("#modalSave").addEventListener("click",async()=>{
    try{
      if(!qs("#modalDate").value||!selectedModal)return toast("Datum und Betreuung wählen");
      await saveEntry(qs("#modalDate").value,selectedModal,qs("#modalNote").value,editingId);
      closeModal();toast("Gespeichert");
    }catch(e){toast(e.message);}
  });
  qs("#modalDelete").addEventListener("click",async()=>{
    if(!editingId||!confirm("Eintrag wirklich löschen?"))return;
    try{await api(`/api/entries/${editingId}`,{method:"DELETE"});closeModal();await loadEntries();toast("Gelöscht");}
    catch(e){toast(e.message);}
  });
  qs("#filterYear").addEventListener("change",renderList);
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
  qs("#copyIcal").addEventListener("click",()=>copyCalendarUrl(qs("#copyIcal").dataset.url));
  qs("#periodStart").value=isoToday();
  qs("#periodEnd").value=isoToday();
  syncDateShell(qs("#periodStart"));
  syncDateShell(qs("#periodEnd"));
  qs("#addPeriod").addEventListener("click",addPeriod);
  qs("#periodKind").addEventListener("change",()=>{
    qs("#periodColor").value=qs("#periodKind").value==="holiday"?"#d65a6f":qs("#periodKind").value==="vacation"?"#f2a65a":"#80a4c2";
  });
  qs("#icsImportForm").addEventListener("submit",async(e)=>{
    e.preventDefault();
    if(!navigator.onLine) return toast("ICS Import benötigt eine Serververbindung");
    const fd=new FormData(e.target);
    try{
      const res=await fetch("/import.ics",{method:"POST",body:fd});
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
      const res=await fetch("/import-data.json",{method:"POST",body:fd});
      const data=await res.json();
      if(!res.ok) throw new Error(data.error||"Import fehlgeschlagen");
      await loadPeople(); await loadEntries(); await loadPeriods(); await loadConfig();
      e.target.reset();
      toast(`${data.people} Personen, ${data.entries} Einträge, ${data.periods} Zeiträume wiederhergestellt`);
    }catch(err){toast(err.message);}
  });

  qs("#importForm").addEventListener("submit",async(e)=>{
    e.preventDefault();
    const fd=new FormData(e.target);
    try{
      const res=await fetch("/import.csv",{method:"POST",body:fd});
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
  try{await loadConfig();}catch(e){if(navigator.onLine) toast(e.message);}
  applyOnlineState();
});
