(function(){
'use strict';
const $=id=>document.getElementById(id);
const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const token=new URLSearchParams(location.search).get('t');
let events=[], selected=null, results=[], standings=[], members=[], tracks=[], presets=[], race=null, sync=null, pane='meeting', timingStageId=null;
let timer=null;

async function api(path,body){
  if(token)path+=(path.includes('?')?'&':'?')+'t='+encodeURIComponent(token);
  const options=body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)};
  const response=await fetch(path,options);
  let data={};try{data=await response.json();}catch(_e){}
  if(!response.ok||data.ok===false)throw Error(data.error||'Permintaan event gagal');
  return data;
}
function fail(error){
  const message=error?.message||String(error);
  const area=$('eventError');
  area.hidden=false;area.textContent=message;
}
function className(id){return selected?.classes.find(c=>c.id===id)?.name||'—';}
function fmt(ms){
  if(ms==null||!Number.isFinite(Number(ms)))return '—';
  const value=Math.round(Number(ms)),minutes=Math.floor(value/60000),seconds=Math.floor(value/1000)%60;
  return (minutes?minutes+':':'')+String(seconds).padStart(2,'0')+'.'+String(value%1000).padStart(3,'0');
}
async function load(preferred){
  const [list,club,people,settings]=await Promise.all([
    api('/api/event/list'),api('/api/tracks'),api('/api/members'),api('/api/presets')]);
  events=list.events||[];tracks=club.tracks||[];members=people.members||[];presets=settings.presets||[];
  window.YurLapsEventTimingLock?.(events.some(e=>e.stages.some(s=>s.status==='live')));
  const target=preferred||selected?.id||events.find(e=>!e.deleted_at&&e.status==='running')?.id||
    events.find(e=>!e.deleted_at&&e.status!=='completed')?.id||events.find(e=>!e.deleted_at)?.id||events[0]?.id;
  if(target&&events.some(e=>e.id===target)){
    const detail=await api('/api/event/detail?id='+encodeURIComponent(target));
    selected=detail.event;results=detail.results||[];standings=detail.standings||[];race=detail.race;sync=detail.sync;
    if(timingStageId&&!selected.stages.some(stage=>stage.id===timingStageId&&stage.status==='planned'))timingStageId=null;
  }else{selected=null;results=[];standings=[];race=null;sync=null;timingStageId=null;}
  render();
}
function renderTabs(){
  document.querySelectorAll('[data-event-pane-tab]').forEach(button=>{
    button.classList.toggle('on',button.dataset.eventPaneTab===pane);
    button.setAttribute('aria-current',button.dataset.eventPaneTab===pane?'step':'false');
  });
  document.querySelectorAll('[data-event-pane]').forEach(section=>{section.hidden=section.dataset.eventPane!==pane;});
}
function renderList(){
  const active=events.filter(e=>e.status!=='completed'&&!e.deleted_at);
  const history=events.filter(e=>e.status==='completed'&&!e.deleted_at);
  const deleteConflicts=events.filter(e=>e.deleted_at);
  $('eventMeetingCount').textContent=String(active.length);
  $('eventActiveList').innerHTML=active.length?active.map(e=>`<button class="event-item ${selected?.id===e.id?'on':''}" data-event-select="${esc(e.id)}"><b>${esc(e.name)}</b><small>${esc(e.track_name)} · ${esc(e.date||'Tanggal belum diisi')} · ${esc(e.status)}</small></button>`).join(''):'<div class="empty"><b>Belum ada meeting aktif</b>Buat meeting untuk memulai.</div>';
  $('eventHistoryList').innerHTML=(history.map(e=>`<button class="event-item ${selected?.id===e.id?'on':''}" data-event-select="${esc(e.id)}"><b>${esc(e.name)}</b><small>${esc(e.track_name)} · ${esc(e.date||'—')} · ${e.stages.length} sesi</small></button>`).join('')+
    deleteConflicts.map(e=>`<button class="event-item ${selected?.id===e.id?'on':''}" data-event-select="${esc(e.id)}"><b>${esc(e.name)}</b><small>Penghapusan perlu diselesaikan</small></button>`).join(''))||'<div class="empty"><b>Belum ada event selesai</b>Hasil resmi event akan muncul di sini.</div>';
}
function renderCreate(){
  const selectedTrack=$('eventTrack').value;
  const options=tracks.map(t=>`<option value="${esc(t.id)}">${esc(t.name)}</option>`).join('');
  $('eventTrack').innerHTML=options;
  $('eventEditTrack').innerHTML=options;
  if(tracks.some(t=>t.id===selectedTrack))$('eventTrack').value=selectedTrack;
  if(!$('eventDate').value)$('eventDate').value=new Date().toLocaleDateString('sv-SE');
  if(!selected)$('eventCreateDetails').open=true;
  $('eventMeetingSettings').hidden=!selected||!!selected.deleted_at||!['draft','registration'].includes(selected.status);
  if(selected&&document.activeElement?.closest('#eventMeetingForm')===null){
    $('eventEditName').value=selected.name;
    $('eventEditDate').value=selected.date||'';
    $('eventEditTrack').value=selected.track_id;
    $('eventEditLocation').value=selected.location||'';
    $('eventEditSeries').value=selected.series||'';
    $('eventEditFormat').value=selected.format||'qualifying_main';
    $('eventEditFormat').disabled=selected.stages.length>0;
    $('eventEditBestLegs').value=selected.best_legs||1;
    $('eventEditPoints').value=(selected.points_table||[]).join(',');
  }
}
function renderNextStep(){
  const box=$('eventNextStep');
  let title,detail,button,paneName;
  if(!selected){title='Mulai dari meeting';detail='Pilih track dan buat meeting. Kelas serta peserta bisa ditambahkan setelahnya.';button='Buat meeting';paneName='create';}
  else if(selected.deleted_at){title='Penghapusan meeting tertunda';detail='Pilih versi aplikasi untuk mengirim penghapusan ke website, atau versi website untuk memulihkan meeting.';button='Lihat sinkronisasi';paneName='meeting';}
  else if(selected.status==='completed'){title='Meeting selesai';detail='Hasil sesi dan klasemen sudah tersimpan.';button='Lihat hasil';paneName='results';}
  else if(!selected.classes.length){title='Tambahkan competition';detail='Setiap kelas memiliki daftar peserta dan format ronde sendiri.';button='Tambah kelas';paneName='competition';}
  else if(!selected.entries.length){title='Daftarkan peserta';detail='Pilih pembalap dan transponder untuk setiap kelas.';button='Buka peserta';paneName='drivers';}
  else if(selected.stages.some(s=>s.status==='live')){title='Sesi sedang berjalan';detail='Pantau passing dan leaderboard di Timing.';button='Buka timing';paneName='timing';}
  else if(!selected.entries.some(e=>e.approved)){title='Setujui peserta';detail='Periksa pendaftaran sebelum membuat ronde.';button='Buka peserta';paneName='drivers';}
  else if(!selected.stages.length){title='Susun sesi';detail='Atur format kelas, lalu buat ronde atau sesi khusus.';button='Buka jadwal';paneName='schedule';}
  else if(selected.status==='draft'){title='Buka pendaftaran';detail='Peserta dan sesi siap. Buka pendaftaran, lalu lakukan check-in.';button='Buka pendaftaran';paneName='registration';}
  else if(!selected.entries.some(e=>e.approved&&e.checked_in)){title='Check-in peserta';detail='Tandai peserta yang hadir agar meeting dapat dimulai.';button='Buka peserta';paneName='drivers';}
  else if(selected.status==='registration'){title='Mulai meeting';detail='Meeting siap dijalankan. Setelah dimulai, buka Timing untuk sesi pertama.';button='Mulai meeting';paneName='running';}
  else if(selected.stages.every(s=>s.status==='saved')){title='Semua sesi selesai';detail='Periksa hasil, lalu tutup meeting saat klasemen sudah benar.';button='Lihat hasil';paneName='results';}
  else{title='Jalankan sesi berikutnya';detail='Pilih sesi terjadwal di Timing, lalu mulai saat peserta siap.';button='Buka timing';paneName='timing';}
  box.innerHTML=`<div><b>${esc(title)}</b><span>${esc(detail)}</span></div><button type="button" class="btn primary" data-event-next="${paneName}">${esc(button)}</button>`;
}
function renderEntries(){
  const classes=selected?.classes||[];
  const options=classes.map(c=>`<option value="${esc(c.id)}">${esc(c.name)}</option>`).join('');
  const current=$('eventEntryClass').value;
  $('eventEntryClass').innerHTML=options;$('eventEntryClass').value=classes.some(c=>c.id===current)?current:classes[0]?.id||'';
  const currentMember=$('eventEntryMember').value;
  $('eventEntryMember').innerHTML=members.map(m=>`<option value="${esc(m.id)}">${esc(m.nick||m.name)} · ${esc(m.source==='local'?'Tamu lokal':'Member/Tamu')}</option>`).join('');
  if(members.some(m=>m.id===currentMember))$('eventEntryMember').value=currentMember;
  renderCars();
  const entries=selected?.entries||[];
  $('eventEntries').innerHTML=entries.length?entries.map(e=>{
    const match=!members.some(m=>m.id===e.member_id)&&members.some(m=>
      (m.transponder||[]).some(tp=>String(tp)===String(e.transponder)));
    let actions='';
    if(!selected.deleted_at&&selected.status!=='completed'){
      if(match)actions+=`<button class="btn sm" data-entry-action="reconcile" data-id="${esc(e.id)}">Cocokkan akun</button>`;
      actions+=e.approved
        ? `<button class="btn sm" data-entry-action="${e.checked_in?'checkout':'checkin'}" data-id="${esc(e.id)}">${e.checked_in?'Batalkan hadir':'Check-in'}</button>`
        : `<button class="btn sm" data-entry-action="approve" data-id="${esc(e.id)}">Setujui</button>`;
      if(['draft','registration'].includes(selected.status))
        actions+=`<button class="btn sm danger" data-entry-action="remove" data-id="${esc(e.id)}">Hapus</button>`;
    }
    return `<div class="event-row"><div><b>${esc(e.nick||e.name)}</b>
      <div class="event-muted">${esc(className(e.class_id))} · ${esc(e.transponder)} · ${esc(e.car_brand||'Mobil belum diisi')}</div>
      <small class="event-status">${e.approved?(e.checked_in?'Hadir':'Disetujui · belum check-in'):'Menunggu persetujuan'}</small></div>
      <div class="actions">${actions}</div></div>`;
  }).join(''):'<p class="note">Belum ada peserta. Tambahkan member atau tamu dan pilih mobilnya.</p>';
  $('eventEntryForm').hidden=!selected||!!selected.deleted_at||!selected.classes.length||!['draft','registration'].includes(selected.status);
}
function renderCars(){
  const member=members.find(m=>m.id===$('eventEntryMember').value);
  const details=member?.transponder_details||[];
  $('eventEntryCar').innerHTML=(member?.transponder||[]).map(tp=>{
    const d=details.find(item=>String(item.transponder_id)===String(tp))||{};
    return `<option value="${esc(tp)}">${esc(tp)} · ${esc(d.rc_class||member.kelas||'Kelas belum diisi')} · ${esc(d.rc_brand||member.mobil||'Mobil belum diisi')}</option>`;
  }).join('');
}
function competitionConfig(c){return Object.assign({practice:false,qualifier_rounds:3,final_rounds:3,
  group_min:4,group_max:12,initial_stacking:'even',qualifier_start:'staggered',final_start:'grid',
  qualifier_rank:'result',final_rank:'position',qualifier_target:300,final_target:300},c.config||{});}
function renderCompetitions(){
  const classes=selected?.classes||[];
  $('eventCompetitionAddForm').hidden=!selected||!!selected.deleted_at||!['draft','registration'].includes(selected.status);
  if(!selected||selected.deleted_at||selected.status==='completed')$('eventCompetitionForm').hidden=true;
  $('eventCompetitionSummary').innerHTML=classes.length?classes.map(c=>{
    const count=selected.entries.filter(e=>e.class_id===c.id).length;
    const cfg=competitionConfig(c);
    const first=selected.format==='heat_final'?'heat':'qualifier';
    return `<div class="event-competition-summary"><strong>${esc(c.name)}</strong><span>${count} peserta · ${cfg.qualifier_rounds} ${first} · ${cfg.final_rounds} final</span></div>`;
  }).join(''):'<p class="note">Belum ada kelas. Tambahkan kelas pertama di bawah.</p>';
  if(selected?.deleted_at){$('eventCompetitionList').innerHTML='';return;}
  $('eventCompetitionList').innerHTML=classes.map(c=>{
    const cfg=competitionConfig(c),stages=selected.stages.filter(s=>s.class_id===c.id);
    const firstKind=selected.format==='heat_final'?'heat':'qualifying';
    const firstLabel=firstKind==='heat'?'heats':'qualifiers';
    const q=stages.some(s=>s.kind===firstKind),f=stages.some(s=>s.kind==='final');
    const p=stages.some(s=>s.kind==='practice');
    const qPending=stages.some(s=>s.kind===firstKind&&s.status!=='saved');
    const approved=selected.entries.filter(e=>e.class_id===c.id&&e.approved).length;
    return `<article class="event-competition"><div><b>${esc(c.name)}</b><small>${approved} disetujui · Grup ${cfg.group_min}–${cfg.group_max} · ${cfg.qualifier_rounds} ${firstKind==='heat'?'H':'Q'} / ${cfg.final_rounds} F</small></div>
      <div class="actions"><button class="btn sm" data-competition-edit="${esc(c.id)}" ${stages.length||selected.status==='completed'?'disabled':''}>Atur format</button>
      ${cfg.practice?`<button class="btn sm" data-competition-practice="${esc(c.id)}" ${p||!approved||selected.status==='completed'?'disabled':''}>Buat practice</button>`:''}
      <button class="btn sm" data-competition-generate="${firstKind}" data-class-id="${esc(c.id)}" ${q||!approved||!cfg.qualifier_rounds||selected.status==='completed'?'disabled':''}>Buat ${firstLabel}</button>
      <button class="btn sm" data-competition-generate="final" data-class-id="${esc(c.id)}" ${f||!approved||!cfg.final_rounds||qPending||selected.status==='completed'?'disabled':''}>Buat finals</button>
      ${!stages.some(s=>s.status!=='planned')&&['draft','registration'].includes(selected.status)&&!selected.deleted_at?`<button class="btn sm danger" data-competition-remove="${esc(c.id)}">Hapus</button>`:''}</div></article>`;
  }).join('')||'<p class="note">Belum ada competition.</p>';
}
function renderStages(){
  const classes=selected?.classes||[];
  const selectedClass=$('eventStageClass').value;
  $('eventStageClass').innerHTML=classes.map(c=>`<option value="${esc(c.id)}">${esc(c.name)}</option>`).join('');
  if(classes.some(c=>c.id===selectedClass))$('eventStageClass').value=selectedClass;
  $('eventStagePreset').innerHTML='<option value="">Atur langsung di bawah</option>'+presets.map(p=>`<option value="${esc(p.id)}">${esc(p.name)} · ${p.target==='laps'?esc(p.value)+' lap':esc(p.value)+' detik'}</option>`).join('');
  renderStageEntries();
  const stages=selected?.stages||[];
  $('eventStages').innerHTML=stages.length?stages.map(s=>{
    let actions='';
    if(!selected.deleted_at&&s.status==='planned'){
      actions=`<button class="btn sm" data-stage-action="prepare" data-id="${esc(s.id)}">Buka Timing</button>
        <button class="btn sm" data-stage-action="move" data-direction="up" data-id="${esc(s.id)}" aria-label="Naikkan ${esc(s.name)}">↑</button>
        <button class="btn sm" data-stage-action="move" data-direction="down" data-id="${esc(s.id)}" aria-label="Turunkan ${esc(s.name)}">↓</button>
        <button class="btn sm danger" data-stage-action="remove" data-id="${esc(s.id)}">Hapus</button>`;
    }else if(!selected.deleted_at&&s.status==='saved'&&!s.superseded_by&&selected.status!=='completed'){
      actions=`<button class="btn sm" data-stage-action="repeat" data-id="${esc(s.id)}">Race ulang</button>
        <button class="btn sm danger" data-stage-action="remove" data-id="${esc(s.id)}">Hapus dari meeting</button>`;
    }
    return `<div class="event-row"><div><b>${esc(s.sequence)}. ${esc(s.name)}</b>
      <div class="event-muted">${esc(className(s.class_id))} · ${esc(s.group)} · ${esc(kindName(s.kind))} · ${s.entry_ids.length?s.entry_ids.length+' peserta':'semua peserta kelas'}</div>
      <small class="event-status">${s.status==='saved'?'Hasil tersimpan':s.status==='live'?(race?.status==='idle'?'Timing terputus':'Sedang berjalan'):'Terjadwal'}</small></div>
      <div class="actions">${actions}</div></div>`;
  }).join(''):'<p class="note">Belum ada sesi. Tambahkan kualifikasi, heat, main, atau final sesuai format event.</p>';
  $('eventStageDetails').hidden=!selected||!!selected.deleted_at||selected.status==='completed'||!classes.length;
}
function renderStageEntries(){
  const classId=$('eventStageClass').value;
  const entries=(selected?.entries||[]).filter(e=>e.class_id===classId);
  $('eventStageEntries').innerHTML=entries.map(e=>`<label style="display:inline-flex;align-items:center;gap:4px;margin:6px 12px 0 0"><input type="checkbox" value="${esc(e.id)}" style="width:auto;margin:0">${esc(e.nick||e.name)}</label>`).join('')||'<span class="event-muted">Belum ada peserta di kelas ini.</span>';
}
function renderResults(){
  const saved=results.filter(r=>r.status==='saved');
  const overall=standings.map(group=>`<div style="margin:10px 0"><b>${esc(className(group.class_id))} · Main ${esc(group.group)}</b><div class="event-board"><table><thead><tr><th>Rank</th><th>Driver</th><th class="r">Poin</th><th class="r">Leg dihitung</th></tr></thead><tbody>${group.drivers.map(d=>`<tr><td>${d.position}</td><td>${esc(d.nick||d.name)}</td><td class="r">${d.points}</td><td class="r">${d.scores.join(' + ')}</td></tr>`).join('')}</tbody></table></div></div>`).join('');
  const content=(overall?`<div class="event-muted">Klasemen main/final · ${selected.best_legs} leg terbaik dihitung</div>${overall}`:'<p class="note">Klasemen akhir muncul setelah main/final disimpan.</p>')+
    (saved.length?'<h3 style="margin-top:18px">Hasil per sesi</h3>'+saved.map(r=>`<details style="margin:8px 0"><summary style="cursor:pointer;font-weight:600">${esc(className(r.class_id))} · ${esc(r.name)} · ${esc(r.group)}</summary><div class="event-board"><table><thead><tr><th>Pos</th><th>Driver</th><th>Status</th><th class="r">Lap</th><th class="r">Total</th><th class="r">Best lap</th></tr></thead><tbody>${(r.drivers||[]).map(d=>`<tr><td>${esc(d.pos??'—')}</td><td>${esc(d.nick||d.name||'—')}<small style="display:block">${esc(d.transponder??'')}</small></td><td>${esc(d.result_status||'—')}</td><td class="r">${esc(d.n_lap??d.laps?.length??0)}</td><td class="r">${fmt(d.elapsed_ms)}</td><td class="r">${fmt(d.best_ms)}</td></tr>`).join('')}</tbody></table></div><a class="btn sm" href="/api/races/${encodeURIComponent(r.race_id)}/pdf${token?'?t='+encodeURIComponent(token):''}" download>Unduh PDF sesi</a></details>`).join(''):'');
  $('eventResults').innerHTML=content;
}
function renderLive(){
  const live=selected?.stages.find(s=>s.status==='live');
  if(!live){$('eventLive').innerHTML='<p class="note">Tidak ada sesi Event yang sedang berjalan.</p>';return;}
  const rows=race?.klasemen||[];
  const state={countdown:'Countdown',live:'Berjalan',done:'Selesai',paused:'Dijeda'}[race?.status]||'Memuat';
  const clock=race?.status==='countdown'&&race.countdown_value!=null
    ? String(race.countdown_value) : fmt(race?.elapsed_ms);
  const remaining=race?.cfg?.target==='time'?`<span>Sisa ${fmt(race.sisa_ms)}</span>`:'';
  const passings=(race?.log||[]).slice(0,8);
  $('eventLive').innerHTML=`<div class="event-timing-strip"><div><strong>${esc(live.name)}</strong><span>${esc(className(live.class_id))} · ${esc(live.group)}</span></div><div class="event-timing-clock"><b>${esc(clock)}</b><small>${esc(state)}</small></div>${remaining}</div>
    ${race?.clock_stale&&race.status==='live'?'<p class="note">Belum ada data waktu terbaru dari decoder. Sesi tetap berjalan.</p>':''}
    <div class="event-board"><table><thead><tr><th>Pos</th><th>Driver</th><th>Status</th><th class="r">Lap</th><th class="r">Total</th><th class="r">Best lap</th></tr></thead><tbody>${rows.map((d,i)=>`<tr><td>${esc(d.pos??i+1)}</td><td>${esc(d.nick||d.name)}<small style="display:block">${esc(d.transponder??'')}</small></td><td>${d.finish_decoder_ts!=null?'FIN':d.first_decoder_ts==null?'Belum start':'LIVE'}</td><td class="r">${esc(d.n_lap??d.laps?.length??0)}</td><td class="r">${fmt(d.elapsed_ms)}</td><td class="r">${fmt(d.best_ms)}</td></tr>`).join('')}</tbody></table></div>
    <h3 class="event-passing-title">Passing terbaru</h3>${passings.length?`<div class="event-board"><table><thead><tr><th>Driver</th><th>Status</th><th class="r">Lap</th><th class="r">RSSI</th><th class="r">Hit</th></tr></thead><tbody>${passings.map(p=>`<tr><td>${esc(p.nama||p.transponder)}</td><td>${esc(p.alasan||({origin:'Start',lap:'Lap',finished:'Setelah finish',ignored:'Diabaikan'}[p.status]||p.status||'—'))}</td><td class="r">${esc(p.lap??'—')}</td><td class="r">${p.rssi==null?'—':esc(Number(p.rssi).toFixed(1))}</td><td class="r">${esc(p.hit_count??'—')}</td></tr>`).join('')}</tbody></table></div>`:'<p class="note">Belum ada passing pada sesi ini.</p>'}`;
}
function renderTiming(){
  const stages=selected?.stages||[];
  const live=stages.find(stage=>stage.status==='live');
  const saved=stages.find(stage=>stage.status==='saved'&&race?.status==='done'&&race?.saved_id===stage.race_id);
  const next=stages.find(stage=>stage.id===timingStageId&&stage.status==='planned')||stages.find(stage=>stage.status==='planned');
  const stage=live||saved||(race?.status==='idle'?next:null);
  if(!selected||selected.deleted_at){$('eventTiming').innerHTML='<p class="note">Pilih meeting aktif untuk menjalankan sesi.</p>';return;}
  if(!stage){
    $('eventTiming').innerHTML=race?.status!=='idle'
      ? '<p class="note">Sesi timing sebelumnya masih aktif. Simpan atau buang hasilnya, lalu reset sebelum menjalankan sesi berikutnya.</p>'
      : '<p class="note">Belum ada sesi siap. Susun sesi di Jadwal.</p>';
    return;
  }
  const entryIds=new Set(stage.entry_ids||[]);
  const entrants=selected.entries.filter(entry=>entry.class_id===stage.class_id&&(!entryIds.size||entryIds.has(entry.id)));
  const ready=entrants.filter(entry=>entry.approved&&entry.checked_in);
  let action='';
  if(stage.status==='planned'&&selected.status==='running'&&race?.status==='idle'){
    action=`<button class="btn primary" data-stage-action="start" data-id="${esc(stage.id)}" ${!ready.length?'disabled':''}>Mulai sesi</button>`;
  }else if(stage.status==='live'&&race?.status==='idle'){
    action=`<button class="btn" data-stage-action="retry" data-id="${esc(stage.id)}">Jalankan ulang</button>`;
  }else if(stage.status==='live'){
    action=(['live','paused','done'].includes(race?.status)?`<button class="btn fin" data-stage-action="save" data-id="${esc(stage.id)}">Selesai & simpan</button>`:'')+
      `<button class="btn danger" data-stage-action="discard" data-id="${esc(stage.id)}">Batalkan & ulang</button>`;
  }else if(saved){
    action=`<button class="btn" data-stage-action="reset" data-id="${esc(stage.id)}">Siap sesi berikutnya</button>`;
  }
  $('eventTiming').innerHTML=`<div class="event-row"><div><b>${esc(stage.name)}</b><div class="event-muted">${esc(className(stage.class_id))} · ${esc(stage.group)} · ${esc(kindName(stage.kind))} · ${ready.length}/${entrants.length} check-in</div></div><div class="actions">${action}</div></div>${stage.status==='planned'&&selected.status!=='running'?'<p class="note">Mulai Event di tab Meeting sebelum menjalankan sesi.</p>':''}${stage.status==='planned'&&!ready.length?'<p class="note">Setujui dan check-in peserta di tab Drivers sebelum memulai.</p>':''}`;
}
function kindName(kind){return {practice:'Practice',qualifying:'Qualifier',heat:'Heat',main:'Main',final:'Final'}[kind]||kind;}
function render(){
  renderTabs();renderList();renderCreate();
  $('eventTitle').textContent=selected?.name||'Pilih event';
  $('eventState').textContent=selected?.deleted_at?'Dihapus':selected?.status||'';
  $('eventMeta').textContent=selected?`${selected.date||'—'} · ${selected.track_name}${selected.location?' · '+selected.location:''}${selected.series?' · '+selected.series:''} · ${selected.classes.length} kelas · ${selected.entries.length} peserta`:'';
  $('eventSync').innerHTML=!selected?'':sync?.conflict?'<p style="color:var(--bad)">Event berubah di aplikasi dan website. Pilih versi yang dipertahankan.</p><div class="actions"><button class="btn sm" data-event-resolve="remote">Pakai versi website</button><button class="btn sm danger" data-event-resolve="local">Kirim versi aplikasi</button></div>':sync?.pending?'Perubahan menunggu sinkronisasi':'Tersinkron';
  const actions=$('eventActions');
  actions.innerHTML=!selected||selected.deleted_at?'':(selected.status==='draft'?'<button class="btn primary" data-event-status="registration">Buka pendaftaran</button>':selected.status==='registration'?'<button class="btn primary" data-event-status="running">Mulai event</button>':selected.status==='running'?'<button class="btn" data-event-status="completed">Tutup event</button>':'')+
    '<button class="btn danger" type="button" data-event-delete>Hapus meeting</button>';
  renderNextStep();renderEntries();renderCompetitions();renderStages();renderResults();renderTiming();renderLive();
}
async function mutate(path,body){try{await api(path,body);$('eventError').hidden=true;await load(selected?.id);}catch(error){fail(error);}}

document.querySelectorAll('[data-event-pane-tab]').forEach(button=>button.onclick=()=>{pane=button.dataset.eventPaneTab;renderTabs();});
document.addEventListener('click',async event=>{
  const next=event.target.closest('[data-event-next]');
  if(next){
    const step=next.dataset.eventNext;
    if(step==='create'){$('eventCreateDetails').open=true;$('eventName').focus();}
    else if(step==='competition'){$('eventCompetitionNewName').focus();}
    else if(step==='registration'||step==='running')await mutate('/api/event/status',{event_id:selected.id,status:step});
    else{pane=step;renderTabs();}
    return;
  }
  const select=event.target.closest('[data-event-select]');
  if(select){try{timingStageId=null;await load(select.dataset.eventSelect);pane='meeting';$('eventCreateDetails').open=false;renderTabs();}catch(error){fail(error);}return;}
  const deleteMeeting=event.target.closest('[data-event-delete]');
  if(deleteMeeting&&selected){
    if(!window.confirm('Hapus meeting ini beserta kelas dan jadwalnya? Hasil race yang sudah disimpan tetap ada di History Race.'))return;
    try{
      await api('/api/event/meeting/delete',{event_id:selected.id});
      selected=null;timingStageId=null;$('eventError').hidden=true;
      await load();pane='meeting';renderTabs();
    }catch(error){fail(error);}
    return;
  }
  const edit=event.target.closest('[data-competition-edit]');
  if(edit&&selected){
    const c=selected.classes.find(x=>x.id===edit.dataset.competitionEdit),cfg=competitionConfig(c);
    $('eventCompetitionId').value=c.id;$('eventCompetitionHeading').textContent=c.name;
    const fields={eventCompetitionName:c.name,eventCompetitionPractice:String(cfg.practice),
      eventCompetitionStack:cfg.initial_stacking,eventCompetitionMin:cfg.group_min,
      eventCompetitionMax:cfg.group_max,eventQualRounds:cfg.qualifier_rounds,
      eventQualTime:cfg.qualifier_target,eventQualStart:cfg.qualifier_start,eventQualRank:cfg.qualifier_rank,
      eventFinalRounds:cfg.final_rounds,eventFinalTime:cfg.final_target,
      eventFinalStart:cfg.final_start,eventFinalRank:cfg.final_rank};
    Object.entries(fields).forEach(([id,value])=>{$(id).value=value;});
    $('eventCompetitionForm').hidden=false;$('eventCompetitionForm').scrollIntoView({block:'nearest'});return;
  }
  const generate=event.target.closest('[data-competition-generate]');
  if(generate&&selected){
    const c=selected.classes.find(x=>x.id===generate.dataset.classId),cfg=competitionConfig(c);
    const final=generate.dataset.competitionGenerate==='final';
    const firstKind=selected.format==='heat_final'?'heat':'qualifying';
    const kind=final?'final':firstKind;
    const name=final?'Final':firstKind==='heat'?'Heat':'Qualifier';
    const rounds=final?cfg.final_rounds:cfg.qualifier_rounds;
    const start=final?cfg.final_start:cfg.qualifier_start;
    const rank=final?cfg.final_rank:cfg.qualifier_rank;
    const value=final?cfg.final_target:cfg.qualifier_target;
    await mutate('/api/event/stage/generate',{event_id:selected.id,class_id:c.id,
      kind,name,rounds,group_max:cfg.group_max,
      seed_basis:final&&selected.stages.some(s=>s.kind===firstKind&&s.status==='saved')?'best':'registration',
      group_mode:final?'top':cfg.initial_stacking==='front'?'top':'balanced',
      cfg:{name,target:'time',value,target_time_ms:value*1000,
        rank,start_mode:start,cd:5,random_delay_max:2,countdown_step_ms:1500,
        finish_current_lap:true}});return;
  }
  const practice=event.target.closest('[data-competition-practice]');
  if(practice&&selected){
    const c=selected.classes.find(x=>x.id===practice.dataset.competitionPractice);
    await mutate('/api/event/stage/add',{event_id:selected.id,class_id:c.id,
      kind:'practice',name:'Practice · '+c.name,group:'Semua',
      cfg:{name:'Practice',target:'time',value:300,target_time_ms:300000,
        start_mode:'staggered',rank:'best',cd:5,random_delay_max:2,
        countdown_step_ms:1500,finish_current_lap:true}});return;
  }
  const removeCompetition=event.target.closest('[data-competition-remove]');
  if(removeCompetition&&selected){
    if(!window.confirm('Hapus kelas ini beserta peserta dan sesi terjadwalnya?'))return;
    await mutate('/api/event/competition/remove',
    {event_id:selected.id,class_id:removeCompetition.dataset.competitionRemove});return;}
  const status=event.target.closest('[data-event-status]');
  if(status&&selected){await mutate('/api/event/status',{event_id:selected.id,status:status.dataset.eventStatus});return;}
  const resolve=event.target.closest('[data-event-resolve]');
  if(resolve&&selected){
    const choice=resolve.dataset.eventResolve;
    if(!window.confirm(choice==='local'?'Versi aplikasi akan mengganti perubahan website untuk Event ini. Lanjutkan?':'Versi website akan mengganti perubahan Event lokal. Lanjutkan?'))return;
    await mutate('/api/event/resolve',{event_id:selected.id,choice});return;
  }
  const entry=event.target.closest('[data-entry-action]');
  if(entry&&selected){
    const action=entry.dataset.entryAction,body={event_id:selected.id,entry_id:entry.dataset.id};
    if(action==='remove')await mutate('/api/event/entry/remove',body);
    else if(action==='reconcile')await mutate('/api/event/entry/reconcile',body);
    else{if(action==='approve')body.approved=true;else body.checked_in=action==='checkin';await mutate('/api/event/entry/set',body);}
    return;
  }
  const stage=event.target.closest('[data-stage-action]');
  if(stage&&selected){
    if(stage.dataset.stageAction==='prepare'){
      timingStageId=stage.dataset.id;pane='timing';renderTiming();renderTabs();return;
    }
    if(stage.dataset.stageAction==='save'&&!window.confirm('Akhiri sesi timing dan simpan hasilnya?'))return;
    if(stage.dataset.stageAction==='discard'&&!window.confirm('Buang seluruh passing sesi ini dan kembalikan ke jadwal untuk race ulang?'))return;
    if(stage.dataset.stageAction==='remove'&&!window.confirm('Hapus sesi dari meeting? Hasil race yang sudah disimpan tetap ada di History Race.'))return;
    if(stage.dataset.stageAction==='repeat'){
      try{
        const response=await api('/api/event/stage/repeat',{event_id:selected.id,stage_id:stage.dataset.id});
        timingStageId=response.stage.id;pane='schedule';$('eventError').hidden=true;
        await load(selected.id);renderTabs();
      }catch(error){fail(error);}
      return;
    }
    if(stage.dataset.stageAction==='discard'){timingStageId=stage.dataset.id;pane='timing';}
    await mutate('/api/event/stage/'+stage.dataset.stageAction,{event_id:selected.id,stage_id:stage.dataset.id,
      direction:stage.dataset.direction});
  }
});
$('eventEntryMember').onchange=renderCars;
$('eventCompetitionAddForm').onsubmit=async event=>{
  event.preventDefault();if(!selected)return;
  try{await api('/api/event/competition/add',{event_id:selected.id,
    name:$('eventCompetitionNewName').value});$('eventCompetitionNewName').value='';await load(selected.id);}catch(error){fail(error);}
};
$('eventCompetitionCancel').onclick=()=>{$('eventCompetitionForm').hidden=true;};
$('eventCompetitionForm').onsubmit=async event=>{
  event.preventDefault();if(!selected)return;
  const body={event_id:selected.id,class_id:$('eventCompetitionId').value,
    name:$('eventCompetitionName').value,practice:$('eventCompetitionPractice').value==='true',
    initial_stacking:$('eventCompetitionStack').value,group_min:Number($('eventCompetitionMin').value),
    group_max:Number($('eventCompetitionMax').value),qualifier_rounds:Number($('eventQualRounds').value),
    qualifier_target:Number($('eventQualTime').value),qualifier_start:$('eventQualStart').value,
    qualifier_rank:$('eventQualRank').value,final_rounds:Number($('eventFinalRounds').value),
    final_target:Number($('eventFinalTime').value),final_start:$('eventFinalStart').value,
    final_rank:$('eventFinalRank').value};
  try{await api('/api/event/competition/config',body);$('eventCompetitionForm').hidden=true;await load(selected.id);}catch(error){fail(error);}
};
$('eventSyncNow').onclick=async()=>{
  try{
    const response=await api('/api/cloud/sync',{});
    if(!response.cloud?.user_id)throw Error('Login ke akun YurLaps untuk mengambil Event dari website.');
    $('eventSyncNow').disabled=true;
    $('eventSyncNow').textContent='Sinkronisasi…';
    for(const delay of [1500,3000,5000]){
      await new Promise(resolve=>setTimeout(resolve,delay));
      await load(selected?.id);
    }
  }catch(error){fail(error);}finally{
    $('eventSyncNow').disabled=false;
    $('eventSyncNow').textContent='Ambil dari website';
  }
};
$('eventStageClass').onchange=renderStageEntries;
$('eventStageStart').onchange=()=>{$('eventStageRank').value=$('eventStageStart').value==='grid'?'position':'result';};
$('eventStageKind').onchange=()=>{
  if($('eventStageKind').value==='qualifying')$('eventStageRank').value='best';
};
$('eventStagePreset').onchange=()=>{
  const preset=presets.find(p=>p.id===$('eventStagePreset').value);
  if(!preset)return;
  $('eventStageTarget').value=preset.target;
  $('eventStageValue').value=preset.target==='time'?Math.ceil((preset.target_time_ms||preset.value*1000)/1000):preset.value;
  $('eventStageStart').value=preset.start_mode;
  $('eventStageRank').value=preset.rank==='lapsTime'?'result':preset.rank;
  $('eventStageCountdown').value=preset.cd;
  $('eventStageDelay').value=preset.random_delay_max||2;
  $('eventStageStep').value=preset.countdown_step_ms||1500;
  $('eventStageGap').value=(preset.stagger_gap_ms||10000)/1000;
  $('eventStageStagger').value=preset.stagger_mode||'manual';
  $('eventStageFinishLap').checked=!!preset.finish_current_lap;
};
$('eventCreateForm').onsubmit=async event=>{
  event.preventDefault();
  try{
    const response=await api('/api/event/create',{name:$('eventName').value,date:$('eventDate').value,
      track_id:$('eventTrack').value,location:$('eventLocation').value,series:$('eventSeries').value});
    $('eventCreateDetails').open=false;
    $('eventError').hidden=true;
    await load(response.event.id);pane='meeting';renderTabs();$('eventCreateForm').reset();
  }catch(error){fail(error);}
};
$('eventMeetingForm').onsubmit=async event=>{
  event.preventDefault();if(!selected)return;
  try{
    await api('/api/event/meeting/update',{event_id:selected.id,name:$('eventEditName').value,
      date:$('eventEditDate').value,track_id:$('eventEditTrack').value,
      location:$('eventEditLocation').value,series:$('eventEditSeries').value,
      format:$('eventEditFormat').value,best_legs:Number($('eventEditBestLegs').value),
      points_table:$('eventEditPoints').value});
    $('eventError').hidden=true;$('eventMeetingSettings').open=false;
    await load(selected.id);
  }catch(error){fail(error);}
};
$('eventEntryForm').onsubmit=async event=>{
  event.preventDefault();if(!selected)return;
  await mutate('/api/event/entry/add',{event_id:selected.id,class_id:$('eventEntryClass').value,
    member_id:$('eventEntryMember').value,transponder:$('eventEntryCar').value});
};
$('eventStageForm').onsubmit=async event=>{
  event.preventDefault();if(!selected)return;
  const target=$('eventStageTarget').value,value=Number($('eventStageValue').value);
  const cfg={name:$('eventStageName').value,target,value,rank:$('eventStageRank').value,
    start_mode:$('eventStageStart').value,cd:Number($('eventStageCountdown').value),
    random_delay_max:Number($('eventStageDelay').value),
    countdown_step_ms:Number($('eventStageStep').value),
    stagger_mode:$('eventStageStagger').value,stagger_gap_ms:Number($('eventStageGap').value)*1000,
    finish_current_lap:$('eventStageFinishLap').checked,
    target_time_ms:target==='time'?value*1000:null};
  await mutate('/api/event/stage/add',{event_id:selected.id,class_id:$('eventStageClass').value,
    kind:$('eventStageKind').value,name:$('eventStageName').value,group:$('eventStageGroup').value,
    cfg,entry_ids:[...$('eventStageEntries').querySelectorAll('input:checked')].map(input=>input.value)});
};
window.YurLapsEventPage={open:()=>load().catch(fail)};
timer=setInterval(async()=>{
  if($('view-event').style.display==='none'||!selected?.stages.some(s=>s.status==='live'))return;
  try{const detail=await api('/api/event/detail?id='+encodeURIComponent(selected.id));race=detail.race;renderTiming();renderLive();}catch(_error){}
},1000);
})();
