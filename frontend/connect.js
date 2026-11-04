// El código permanece en el fragmento del teléfono; no se envía en peticiones de página.
const data=new URLSearchParams(location.hash.slice(1));
const server=data.get('server');const code=data.get('code');
if(server && code && server.startsWith('https://')){
  document.getElementById('open-app').href='videoteca://pair?'+new URLSearchParams({server,code});
}else{
  document.getElementById('status').textContent='Genera un nuevo QR desde el panel del PC.';
  document.getElementById('open-app').removeAttribute('href');
}
