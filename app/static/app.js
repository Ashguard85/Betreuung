let people = [];
let entries = [];
let selectedQuick = null;
let selectedModal = null;
let editingId = null;

const qs = s => document.querySelector(s);
const qsa = s => [...document.querySelectorAll(s)];

async function api(url, options={}) {
  const res = await fetch(url, {
    headers: {"Content-Type":"application/json", ...(options.headers||{})},
    ...options
  });
  if (res.status === 401) { location.href="/login"; throw new Error("login"); }
  const type = res.headers.get("content-type") || "";
  const body = type.includes("json") ? await res.json() : await res.text();
  if (!res.ok) throw new Error(body.error || body || `HTTP ${res.status}`);
  return body;
}
function esc(s){ return String(s ?? "").replace(/[&<>"']/g, m=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[m])); }
function isoToday(){ const d=new Date(); return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,"0")}-${String(d.getDate()).padStart(2,"0")}`; }
function monthShort(d){ return d.toLocaleDateString("de-CH",{month:"short"}).replace(".",""); }
function weekdayShort(d){ return d.toLocaleDateString("de-CH",{weekday:"short"}).replace(".",""); }
function personById(id){ return people.find(p=>Number(p.id)===Number(id)); }
function personColor(name){ return (people.find(p=>p.name===name)||{color:"#ececec"}).color; }

async function loadPeople(){
  people = await api("/api/people");
  if (!selectedQuick && people.length) selectedQuick = people[0].id;
  if (!selectedModal && people.length) selectedModal = people[0].id;
  renderPersonButtons("#quickPeople","quick");
  renderPersonButtons("#modalPeople","modal");
  renderPersonFilter();
  renderPeopleSettings();
}
async function loadEntries(){
  entries = await api("/api/entries");
  renderAll();
}

function renderPersonButtons(target, mode){
  const selected = mode==="quick" ? selectedQuick : selectedModal;
  qs(target).innerHTML = people.map(p=>`
    <button class="person-btn ${Number(selected)===Number(p.id)?"selected":""}"
      style="background:${esc(p.color)}" onclick="selectPerson('${mode}',${p.id})">${esc(p.name)}</button>
  `).join("");
}
function selectPerson(mode,id){
  if(mode==="quick") selectedQuick=id; else selectedModal=id;
  renderPersonButtons(mode==="quick"?"#quickPeople":"#modalPeople",mode);
}

function yearOptions(select){
  const now = new Date().getFullYear();
  const years = [];
  for(let y=now-1;y<=now+2;y++) years.push(y);
  select.innerHTML=years.map(y=>`<option ${y===now?"selected":""}>${y}</option>`).join("");
}
function renderPersonFilter(){
  const current=qs("#filterPerson").value;
  qs("#filterPerson").innerHTML='<option value="">Alle</option>'+people.map(p=>`<option>${esc(p.name)}</option>`).join("");
  if(current) qs("#filterPerson").value=current;
}
function showPage(name){
  qsa(".page").forEach(p=>p.classList.remove("active"));
  qs("#"+name+"Page").classList.add("active");
  qsa(".navbtn").forEach(b=>b.classList.toggle("active",b.dataset.page===name));
  if(name==="list") renderList();
  if(name==="year"){ renderYear(); renderStatsByPerson(); }
  if(name==="settings") loadConfig();
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
function renderList(){
  const person=qs("#filterPerson").value;
  const year=qs("#filterYear").value;
  const search=qs("#filterSearch").value.toLowerCase().trim();
  let arr=entries.filter(e=>e.day.startsWith(year));
  if(person) arr=arr.filter(e=>e.person===person);
  if(search) arr=arr.filter(e=>(e.note||"").toLowerCase().includes(search)||e.person.toLowerCase().includes(search));
  arr.sort((a,b)=>a.day.localeCompare(b.day));
  qs("#fullList").innerHTML=arr.length?arr.map(itemHtml).join(""):'<div class="small">Keine Einträge.</div>';

  const params=new URLSearchParams();
  if(year) params.set("year",year);
  if(person) params.set("person",person);
  qs("#listCsvLink").href=`/export.csv?${params.toString()}`;
  qs("#listExportHint").textContent=person
    ? `${arr.length} Einträge für ${person} im Jahr ${year}`
    : `${arr.length} Einträge im Jahr ${year}`;
}
function renderStats(){
  const today=isoToday();
  const year=String(new Date().getFullYear());
  qs("#statUpcoming").textContent=entries.filter(e=>e.day>=today).length;
  qs("#statPeople").textContent=new Set(entries.map(e=>e.person)).size;
  qs("#statYear").textContent=entries.filter(e=>e.day.startsWith(year)).length;
}
function renderYear(){
  const year=Number(qs("#yearSelect").value);
  const months=["Januar","Februar","März","April","Mai","Juni","Juli","August","September","Oktober","November","Dezember"];
  const byDay=new Map(entries.filter(e=>e.day.startsWith(String(year))).map(e=>[e.day,e]));
  let out='<table class="year"><colgroup><col class="day-col">'+months.map(()=>'<col class="month-col">').join("")+'</colgroup><thead><tr><th>Tag</th>'+months.map(m=>`<th>${m}</th>`).join("")+'</tr></thead><tbody>';
  for(let day=1;day<=31;day++){
    out+=`<tr><td>${day}</td>`;
    for(let month=0;month<12;month++){
      const d=new Date(year,month,day,12);
      const valid=d.getFullYear()===year&&d.getMonth()===month&&d.getDate()===day;
      if(!valid){out+='<td class="invalid"></td>';continue;}
      const iso=`${year}-${String(month+1).padStart(2,"0")}-${String(day).padStart(2,"0")}`;
      const e=byDay.get(iso);
      const weekend=d.getDay()===0||d.getDay()===6;
      const fill=e?`<div class="entry-fill" style="background:${esc(e.color)}"><span>${esc(e.person)}</span></div>`:"";
      out+=`<td class="${weekend?"weekend":""}" onclick="${e?`openModal(${e.id})`:`prefillDate('${iso}')`}">${fill}</td>`;
    }
    out+='</tr>';
  }
  out+='</tbody></table>';
  qs("#yearTableWrap").innerHTML=out;
  qs("#legend").innerHTML=people.map(p=>`<span><i class="dot" style="background:${esc(p.color)}"></i>${esc(p.name)}</span>`).join("")+`<span><i class="dot" style="background:var(--weekend)"></i>Wochenende</span>`;
  qs("#csvLink").href=`/export.csv?year=${year}`;
}
async function renderStatsByPerson(){
  const year=qs("#yearSelect").value;
  const data=await api(`/api/stats?year=${encodeURIComponent(year)}`);
  qs("#personStats").innerHTML=data.per_person.length?data.per_person.map(x=>{
    const href=`/export.csv?year=${encodeURIComponent(year)}&person=${encodeURIComponent(x.name)}`;
    return `<a class="stat-chip stat-chip-link" href="${href}" title="Liste von ${esc(x.name)} als CSV exportieren"><i class="dot" style="background:${esc(x.color)}"></i>${esc(x.name)}: ${x.count}<span class="export-mark">CSV ↓</span></a>`;
  }).join(""):'<span class="small">Noch keine Einträge.</span>';
}
function renderAll(){ renderStats(); renderNext(); renderList(); renderYear(); renderStatsByPerson(); }

function openModal(id=null){
  editingId=id;
  const e=id?entries.find(x=>Number(x.id)===Number(id)):null;
  qs("#modalTitle").textContent=e?"Eintrag bearbeiten":"Betreuung eintragen";
  qs("#modalDate").value=e?e.day:(qs("#quickDate").value||isoToday());
  qs("#modalNote").value=e?e.note:"";
  selectedModal=e?e.person_id:(people[0]?.id||null);
  renderPersonButtons("#modalPeople","modal");
  qs("#modalDelete").style.display=e?"block":"none";
  qs("#modalBack").classList.add("open");
}
function closeModal(){qs("#modalBack").classList.remove("open");editingId=null;}
function prefillDate(day){openModal();qs("#modalDate").value=day;}
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
async function loadConfig(){
  const c=await api("/api/config");
  qs("#dataFile").textContent=c.data_file+"  |  Backups: "+c.backup_dir;
  if(c.ical_enabled){
    qs("#icalBox").textContent=c.ical_url;
    qs("#copyIcal").style.display="";
    qs("#copyIcal").dataset.url=c.ical_url;
  }else{
    qs("#icalBox").textContent="Nicht aktiviert. In Portainer ICAL_TOKEN setzen.";
    qs("#copyIcal").style.display="none";
  }
}

document.addEventListener("DOMContentLoaded", async ()=>{
  yearOptions(qs("#yearSelect"));
  yearOptions(qs("#filterYear"));
  qs("#quickDate").value=isoToday();

  qs("#quickSave").addEventListener("click",async()=>{
    try{
      if(!qs("#quickDate").value||!selectedQuick)return toast("Datum und Betreuung wählen");
      await saveEntry(qs("#quickDate").value,selectedQuick,qs("#quickNote").value);
      qs("#quickNote").value="";toast("Gespeichert");
    }catch(e){toast(e.message);}
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
  qs("#filterPerson").addEventListener("change",renderList);
  qs("#filterYear").addEventListener("change",renderList);
  qs("#filterSearch").addEventListener("input",renderList);
  qs("#yearSelect").addEventListener("change",()=>{renderYear();renderStatsByPerson();});
  qs("#addPerson").addEventListener("click",async()=>{
    const name=qs("#newPerson").value.trim();if(!name)return;
    try{
      await api("/api/people",{method:"POST",body:JSON.stringify({name,color:qs("#newColor").value})});
      qs("#newPerson").value="";await loadPeople();toast("Person hinzugefügt");
    }catch(e){toast(e.message);}
  });
  qs("#copyIcal").addEventListener("click",async()=>{
    try{await navigator.clipboard.writeText(qs("#copyIcal").dataset.url);toast("Kalenderlink kopiert");}
    catch{toast("Link markieren und kopieren");}
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

  await loadPeople();
  await loadEntries();
  await loadConfig();
});
