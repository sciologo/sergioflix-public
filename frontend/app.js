/* Credencial solo en memoria. La UI nunca registra formularios ni URLs privadas. */
let apiKey = '';
let pairingLink = '';
const el = id => document.getElementById(id);
const say = message => { el('feedback').textContent = message; };
const ORDER = {penguplay:'Alojador', hdhub:'Alojador', flixstreams:'Segunda opción', mediafusion:'Respaldo'};
async function api(path, method='GET', data) {
  const response = await fetch('/api/' + path, {method, headers:{'X-API-Key':apiKey,'Content-Type':'application/json'},body:data===undefined?undefined:JSON.stringify(data)});
  const result = response.status===204?null:await response.json();
  if (!response.ok) throw new Error(result.message || 'No se pudo completar la operación.');
  return result;
}
async function event(name){ await api('events','POST',{event:name}); }
async function action(button, callback){
  button.disabled=true;say('');
  try { await callback(); } catch(error) { say(error.message || 'No se pudo conectar con el servicio local.'); }
  finally {button.disabled=false;}
}
function node(tag, text, attributes){
  const element=document.createElement(tag);
  if(text!==undefined && text!==null) element.textContent=text;
  Object.entries(attributes||{}).forEach(([name,value])=>element.setAttribute(name,value));
  return element;
}
function describeProvider(item){
  const summary=item.summary;
  if(!item.configured) return 'Sin configurar. SergioFlix lo omitirá hasta que guardes tu enlace.';
  return 'Enlace guardado' + (summary&&summary.version?` · versión ${summary.version}`:'') +
    (summary&&summary.catalog_count!==undefined?` · ${summary.catalog_count} catálogos declarados`:'') +
    ` · salud ${item.health}/100.`;
}
/* Una tarjeta independiente por proveedor: guardar uno nunca toca la configuración de otro. */
function providerCard(item){
  const card=node('section',null,{'data-provider':item.provider});
  const heading=node('div',null,{class:'section-heading'});
  heading.append(node('h3',item.name), node('span',ORDER[item.provider]||'',{class:'small'}));
  const state=node('p',describeProvider(item));
  const link=node('a','Abrir configuración de '+item.name+' ↗',{href:item.configure_url,target:'_blank',rel:'noopener noreferrer'});
  const form=node('form');
  const field='manifest-'+item.provider;
  form.append(node('label','Enlace de instalación',{for:field}),
    node('input',null,{id:field,type:'password',autocomplete:'off',placeholder:'https://…/manifest.json',required:'required'}),
    node('button','Guardar y comprobar'));
  const tools=node('div');
  const check=node('button','Comprobar conexión',{class:'secondary',type:'button'});
  const probe=node('button','Consultar fuentes de prueba',{class:'secondary',type:'button'});
  const result=node('p',null,{class:'small'});
  const forget=node('button','Olvidar enlace',{class:'secondary',type:'button'});
  tools.append(check,' ',probe,' ',forget);
  form.addEventListener('submit',e=>{e.preventDefault();action(e.submitter,async()=>{
    const input=el(field);const value=input.value;input.value='';
    await api('providers/'+item.provider,'PUT',{manifest_url:value});
    say(`${item.name}: enlace comprobado y guardado en este PC.`);await refreshProviders();
  });});
  check.addEventListener('click',()=>action(check,async()=>{
    const data=await api('providers/'+item.provider+'/check','POST');await event('connection_checked');
    state.textContent=describeProvider({...item,configured:data.configured,summary:data.summary});
    say(`${item.name} responde. Esto comprueba el manifiesto, no los videos.`);
  }));
  probe.addEventListener('click',()=>action(probe,async()=>{
    await event('stream_check_requested');
    const data=await api('providers/'+item.provider+'/streams/check','POST',{media_type:'movie',video_id:'tt1254207'});
    result.textContent=data.returned_count
      ? `${data.returned_count} fuentes: ${data.counts.http} HTTP, ${data.counts.torrent} torrent y ${data.counts.external} externas. Español y reproducción quedan por comprobar.`
      : `${item.name} respondió, pero no encontró fuentes para el corto de prueba con esta configuración.`;
  }));
  forget.addEventListener('click',()=>action(forget,async()=>{
    await api('providers/'+item.provider,'DELETE');say(`${item.name}: enlace eliminado de este PC.`);await refreshProviders();
  }));
  card.append(heading,state,link,form,tools,result);
  return card;
}
async function refreshProviders(){
  const data=await api('providers');
  const container=el('providers');
  container.replaceChildren(...data.items.map(providerCard));
  const pending=data.items.filter(item=>!item.configured).map(item=>item.name);
  el('state').textContent=pending.length===data.items.length
    ? 'Todavía no hay ningún proveedor configurado. Guarda al menos uno para poder reproducir.'
    : pending.length ? `Servicio activo. Falta configurar: ${pending.join(', ')}.` : 'Servicio activo con los tres proveedores configurados.';
}
/* Indicadores del diario por intento, separados de la configuración. */
async function refreshPlayback(){
  const box=el('playback-analysis');
  try {
    const data=await api('playback/analysis?days=7');
    box.replaceChildren();
    if(!data.providers.length){box.append(node('p','Todavía no hay intentos registrados.'));return;}
    const table=node('table'), head=node('thead'), headings=node('tr'), body=node('tbody');
    ['Proveedor','Intentos','Primera fuente con imagen','Fallos','Cambios automáticos','Inicio promedio','Tiempo detenido'].forEach(label=>headings.append(node('th',label,{scope:'col'})));
    head.append(headings);
    for(const item of data.providers){
      const row=node('tr');
      const initial=item.resolved_first_attempts?`${item.first_attempts_with_image}/${item.resolved_first_attempts}`:'—';
      const startup=item.average_startup_ms===null?'—':`${(item.average_startup_ms/1000).toFixed(1)} s`;
      [item.provider,item.attempts,initial,item.failed_attempts,item.automatic_switches,startup,`${Math.round(item.buffering_ms/1000)} s`].forEach(value=>row.append(node('td',value)));
      body.append(row);
    }
    table.append(head,body);box.append(table);
  }catch(error){box.textContent=error.message||'No se pudo consultar el historial.';}
}
async function enter(){ await refreshProviders(); el('access').hidden=true; el('workspace').hidden=false; await refreshPlayback(); }
el('login-form').addEventListener('submit', e=>{e.preventDefault();action(e.submitter,async()=>{
  apiKey=el('key').value.trim();
  try {await api('providers');await event('login');await enter();}
  catch(error){apiKey='';throw error;}finally{el('key').value='';}
});});
el('logout').addEventListener('click', ()=>action(el('logout'),async()=>{try{await event('logout');}finally{apiKey='';pairingLink='';el('pairing').hidden=true;el('pair-qr').removeAttribute('src');el('providers').replaceChildren();el('workspace').hidden=true;el('access').hidden=false;}}));
el('check').addEventListener('click',()=>action(el('check'),async()=>{await refreshProviders();say('Estado de los proveedores actualizado.');}));
el('check-playback').addEventListener('click',()=>action(el('check-playback'),refreshPlayback));
el('pair').addEventListener('click',()=>action(el('pair'),async()=>{const data=await api('devices/pairing','POST');pairingLink=data.link;el('pair-qr').src=data.qr;el('pairing').hidden=false;}));
el('pair-tv').addEventListener('click',()=>action(el('pair-tv'),async()=>{const data=await api('devices/pairing/tv','POST');el('tv-code').textContent=data.code.replace(/(\d{4})(\d{4})/,'$1 $2');el('tv-address').textContent=data.address;el('pairing-tv').hidden=false;}));
el('copy-pair').addEventListener('click',()=>action(el('copy-pair'),async()=>{await navigator.clipboard.writeText(pairingLink);say('Enlace copiado. Pégalo en SergioFlix en tu Android.');}));

// El lanzador entrega un código efímero en el fragmento; no se envía en URLs HTTP.
const ticket=new URLSearchParams(location.hash.slice(1)).get('access');
if(ticket){
  history.replaceState(null,'',location.pathname);
  (async()=>{try{
    const result=await api('admin/session','POST',{code:ticket});apiKey=result.api_key;await enter();
  }catch(error){say(error.message);}})();
}
