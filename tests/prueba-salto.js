/* Prueba del avance y retroceso de la app de televisor, sin televisor.

   Se monta un DOM mínimo y un `<video>` que se comporta como el de un Samsung: acepta
   que le escriban `currentTime` y no se mueve, que es exactamente lo que hace con una
   lista HLS que todavía crece —sin marca de final la trata como emisión en directo, y
   en directo saltar no significa nada—. Ese silencio era el fallo: el mando parecía
   muerto. Aquí se comprueba que la app lo detecta y pide la lista al equipo.

   Ejecutar: node tests/prueba-salto.js                                              */
'use strict';

var fs = require('fs');
var path = require('path');

// --------------------------------------------------------------------- DOM falso
function nodo(id) {
  var self = {
    textContent: '', className: '', hidden: false, value: '',
    style: {}, offsetLeft: 0, offsetTop: 0, offsetParent: null, parentNode: null, children: [],
    appendChild: function (hijo) {
      hijo.parentNode = self;
      if (!hijo.offsetParent) hijo.offsetParent = self;
      self.children.push(hijo);
      return hijo;
    },
    addEventListener: function (nombre, fn) { (self.oyentes[nombre] = self.oyentes[nombre] || []).push(fn); },
    removeEventListener: function (nombre, fn) {
      var lista = self.oyentes[nombre] || [];
      for (var i = 0; i < lista.length; i++) if (lista[i] === fn) { lista.splice(i, 1); return; }
    },
    removeAttribute: function () {},
    focus: function () { doc.activeElement = self; if (self.onfocus) self.onfocus(); },
    blur: function () { if (doc.activeElement === self) doc.activeElement = null; },
    disparar: function (nombre) {
      var lista = (self.oyentes[nombre] || []).slice();
      for (var i = 0; i < lista.length; i++) lista[i]({ type: nombre });
    },
    oyentes: {}
  };
  var identity = id, html = '';
  Object.defineProperty(self, 'id', {
    get: function () { return identity; },
    set: function (value) { identity = value; if (value) nodos[value] = self; }
  });
  Object.defineProperty(self, 'innerHTML', {
    get: function () { return html; },
    set: function (value) { html = value; self.children = []; }
  });
  return self;
}

var nodos = {};
var creados = 0;
var doc = {
  readyState: 'complete',
  getElementById: function (id) { return nodos[id] || (nodos[id] = nodo(id)); },
  createElement: function () { return nodo('nuevo-' + (++creados)); },
  querySelector: function (selector) {
    var pideCard = selector.indexOf('.card.focus') >= 0;
    var pideEp = selector.indexOf('.ep.focus') >= 0;
    for (var key in nodos) {
      var classes = ' ' + (nodos[key].className || '') + ' ';
      if (pideCard && classes.indexOf(' card ') >= 0 && classes.indexOf(' focus ') >= 0) return nodos[key];
      if (pideEp && classes.indexOf(' ep ') >= 0 && classes.indexOf(' focus ') >= 0) return nodos[key];
    }
    return null;
  },
  addEventListener: function (nombre, fn) { (doc.oyentes[nombre] = doc.oyentes[nombre] || []).push(fn); },
  activeElement: null, oyentes: {}
};

// ------------------------------------------------------------- televisor simulado
var tv = {
  posicion: 0,          // lo que el reproductor cree que está mostrando, en segundos
  obedece: true,        // si false, ignora `currentTime` como hace Tizen en directo
  convertido: 1800,     // segundos ya convertidos por el equipo
  cargas: []            // direcciones que se le han pedido
};

var video = doc.getElementById('video');
Object.defineProperty(video, 'currentTime', {
  get: function () { return tv.posicion; },
  set: function (valor) { if (tv.obedece) tv.posicion = valor; }
});
video.paused = false;
video.duration = 7200;
video.readyState = 4;
video.seeking = false;
video.seekable = { length: 1, start: function () { return 0; }, end: function () { return tv.convertido; } };
video.load = function () {};
video.play = function () { video.paused = false; };
video.pause = function () { video.paused = true; };
video.removeAttribute = function (nombre) { if (nombre === 'src') tv.ultimaUrl = ''; };
Object.defineProperty(video, 'src', {
  get: function () { return tv.ultimaUrl || ''; },
  set: function (valor) { tv.ultimaUrl = valor; tv.cargas.push(valor); tv.posicion = 0; }
});

// -------------------------------------------------------------- servidor simulado
var servidor = {
  llamadas: [],
  offset: 0,
  responder: null,      // si se define, decide la respuesta
  demora: 0
};

var peticionesTexto = [];
function XHR() {
  var self = this;
  self.readyState = 0; self.status = 0; self.responseText = '';
  self.open = function (metodo, url) { self.metodo = metodo; self.url = url; };
  self.setRequestHeader = function () {};
  self.send = function (cuerpo) {
    if (self.url.indexOf('.vtt') >= 0) peticionesTexto.push(self);
    var datos = cuerpo ? JSON.parse(cuerpo) : null;
    servidor.llamadas.push({ url: self.url, metodo: self.metodo, cuerpo: datos });
    var respuesta = servidor.responder
      ? servidor.responder(self.url, datos)
      : { status: 200, body: {} };
    setTimeout(function () {
      self.readyState = 4;
      self.status = respuesta.status;
      self.responseText = respuesta.text === undefined ? JSON.stringify(respuesta.body || {}) : respuesta.text;
      if (self.onreadystatechange) self.onreadystatechange();
    }, respuesta.delay === undefined ? servidor.demora : respuesta.delay);
  };
}

// ------------------------------------------------------------------ entorno global
global.document = doc;
global.window = global;
global.XMLHttpRequest = XHR;
var guardadoLocal = { 'sergioflix.key': 'clave-de-prueba' };
global.localStorage = {
  getItem: function (key) { return guardadoLocal[key] || ''; },
  setItem: function (key, value) { guardadoLocal[key] = value; }
};
global.Image = function () { return { onload: null }; };
var recargas = 0;
global.location = { reload: function () { recargas++; } };
// navigator ya existe en Node y no hace falta tocarlo.

// El reloj se controla a mano: nada de esperar de verdad.
var pendientes = [];
var ahora = 0;
var setTimeoutReal = setTimeout;
global.setTimeout = function (fn, ms) { pendientes.push({ fn: fn, cuando: ahora + (ms || 0) }); return pendientes.length; };
global.setInterval = function (fn, ms) { pendientes.push({ fn: fn, cuando: ahora + (ms || 0), cada: ms || 1 }); return pendientes.length; };
global.clearTimeout = function () {};
global.clearInterval = function () {};
var fechaReal = Date.now;
Date.now = function () { return ahora; };

/** Avanza el reloj simulado disparando lo que toque, y el video si está corriendo. */
function avanzar(ms) {
  var fin = ahora + ms;
  while (ahora < fin) {
    ahora += 100;
    if (tv.corriendo && !video.paused) tv.posicion += 0.1;
    var listos = [];
    for (var i = 0; i < pendientes.length; i++) if (pendientes[i].cuando <= ahora) listos.push(pendientes[i]);
    for (var j = 0; j < listos.length; j++) {
      var tarea = listos[j];
      if (tarea.cada) tarea.cuando = ahora + tarea.cada;
      else pendientes.splice(pendientes.indexOf(tarea), 1);
      tarea.fn();
    }
  }
}

// --------------------------------------------------------------------- la app
var codigo = fs.readFileSync(path.join(__dirname, '..', 'tv', 'app.js'), 'utf8');
/* La app se cierra sobre sí misma a propósito, para no ensuciar el navegador del
   televisor. En vez de abrirle un agujero al código de verdad, el asidero se le
   inyecta aquí, que es donde debe estar. */
var cierre = '})();';
var corte = codigo.lastIndexOf(cierre);
if (corte < 0) throw new Error('no encuentro el cierre de app.js');
codigo = codigo.slice(0, corte) +
  'window.__tv = { playVideo: playVideo, saltarA: saltarA, state: state,' +
  ' activarBoton: activarBoton, botonesReproductor: botonesReproductor,' +
  ' abrirVersiones: function () { openMenu("versiones"); }, chooseMenu: chooseMenu, menuOptions: menuOptions,' +
  ' abrirAudio: function () { openMenu("audio"); }, abrirSubtitulos: function () { openMenu("subs"); },' +
  ' pedirSubtitulos: pedirSubtitulos, pedirAudio: pedirAudio, sincronizarSubtitulo: sincronizarSubtitulo,' +
  ' loadSubtitles: loadSubtitles, loadAudioTracks: loadAudioTracks, fetchCues: fetchCues, paintSubtitle: paintSubtitle,' +
  ' prepararSubtitulos: prepararSubtitulos, startAt: startAt, cambiarAudio: cambiarAudio,' +
  ' whenBroken: whenBroken, saveProgress: saveProgress, alternar: alternar,' +
  ' renderHome: renderHome, focusCard: focusCard, renderDetail: renderDetail,' +
  ' activarDetalle: activarDetalle, reproducirEpisodio: reproducirEpisodio,' +
  ' resolverEleccionEpisodio: resolverEleccionEpisodio,' +
  ' cargarMas: cargarMas, abrirBusqueda: abrirBusqueda, buscar: buscar,' +
  ' openItem: openItem, seleccionDiaria: seleccionDiaria, diaCatalogo: diaCatalogo, tick: tick,' +
  ' abrirAsistente: abrirAsistente, enviarAsistente: enviarAsistente,' +
  ' api: api, reintentarConexion: reintentarConexion, enviarDiario: enviarDiario,' +
  ' logroCarga: logroCarga,' +
  ' activarNav: activarNav, show: show, columnas: columnas, stopPlayback: stopPlayback,' +
  ' tituloReproduccion: tituloReproduccion, whenFinished: whenFinished };' +
  cierre;
eval(codigo);
var app = global.__tv;
var subtitlePrecisionTick = pendientes.filter(function (task) { return task.cada === 50; })[0];

function bajarTecla(code) {
  var event = { keyCode: code, defaultPrevented: false,
    preventDefault: function () { this.defaultPrevented = true; } };
  var lista = doc.oyentes.keydown || [];
  for (var i = 0; i < lista.length; i++) lista[i](event);
  return event;
}
function soltarTecla(code) {
  var lista = doc.oyentes.keyup || [];
  for (var i = 0; i < lista.length; i++) lista[i]({ keyCode: code, preventDefault: function () {} });
}
function tecla(code) { bajarTecla(code); soltarTecla(code); }

// ------------------------------------------------------------------- resultados
var fallos = 0, hechas = 0;
function comprobar(nombre, condicion, detalle) {
  hechas++;
  if (condicion) { console.log('  ok   ' + nombre); return; }
  fallos++;
  console.log('  FALLA ' + nombre + (detalle ? '  -> ' + detalle : ''));
}

// ------------------------------------------------------------ arranque de un video
function responderNormal(url, datos) {
  if (url.indexOf('playback/sources') >= 0) return { status: 200, body: { items: [{ source_id: 'A' }, { source_id: 'B' }] } };
  if (url.indexOf('playback/start') >= 0) return { status: 200, body: { session_id: 'S1', position_ms: 0, subtitles: [] } };
  if (url.indexOf('playback/cast') >= 0) {
    servidor.offset = datos.position_ms;
    return { status: 200, body: { stream_url: 'http://equipo/cast/A/index.m3u8?start=' + datos.position_ms,
                                  offset_ms: datos.position_ms, duration_ms: 7200000,
                                  seekable_ms: tv.convertido * 1000 } };
  }
  if (url.indexOf('playback/progress') >= 0) return { status: 200, body: {} };
  if (url.indexOf('playback/status') >= 0) return { status: 200, body: { active: true } };
  return { status: 200, body: {} };
}
// El PC apagado, para las pruebas de la vigilancia del final.
function pcApagado() { return { status: 0, body: {} }; }
servidor.responder = responderNormal;

console.log('\nAvance y retroceso en la app de televisor\n');

/** Deja pasar el tiempo con imagen para que termine lo que hubiera en marcha. */
function reposar() {
  tv.obedece = true; tv.corriendo = true; video.paused = false;
  avanzar(8000);
  app.state.salto = null;
  app.state.avanceEn = Date.now();
  doc.getElementById('trabajo').hidden = true;
}

avanzar(3000);            // que termine de cargarse la pantalla de inicio
// Se entra directo al reproductor, saltándose el paseo por el catálogo.
var arrancar = app.playVideo;
arrancar('movie', 'peli-1', 'Una película');
tv.corriendo = true;
avanzar(8000);

comprobar('la película arranca y el cartel de marca se retira',
          doc.getElementById('player-brand').hidden === true,
          'cartel oculto=' + doc.getElementById('player-brand').hidden);

// ------------------------------------------------ 1. el televisor obedece el salto
var llamadasAntes = servidor.llamadas.length;
tv.obedece = true;
tv.posicion = 60;
var saltar = app.saltarA;
saltar(600000);                       // minuto 10
avanzar(1500);
comprobar('salto dentro de lo convertido: no molesta al equipo',
          servidor.llamadas.slice(llamadasAntes).filter(function (l) { return l.url.indexOf('playback/cast') >= 0; }).length === 0,
          servidor.llamadas.length - llamadasAntes + ' llamadas');
comprobar('salto dentro de lo convertido: el video queda en el minuto 10',
          Math.abs(tv.posicion - 600) < 3, 'quedó en ' + tv.posicion);
comprobar('el aviso de trabajo se retira al terminar', doc.getElementById('trabajo').hidden);

// --------------------------------- 2. el televisor ignora la orden (el fallo real)
tv.obedece = false;
tv.posicion = 600;
llamadasAntes = servidor.llamadas.length;
saltar(1200000);                      // minuto 20
avanzar(400);
comprobar('mientras comprueba, el aviso dice a dónde va',
          doc.getElementById('trabajo').hidden === false &&
          doc.getElementById('trabajo-texto').textContent.indexOf('20:00') >= 0,
          doc.getElementById('trabajo-texto').textContent);
avanzar(3000);
var pedidos = servidor.llamadas.slice(llamadasAntes).filter(function (l) { return l.url.indexOf('playback/cast') >= 0; });
comprobar('un salto ignorado por el televisor se le pide al equipo',
          pedidos.length === 1, pedidos.length + ' peticiones de lista');
comprobar('se pide exactamente el punto elegido',
          pedidos.length === 1 && pedidos[0].cuerpo.position_ms === 1200000 &&
          pedidos[0].cuerpo.force_adapted === true,
          pedidos.length ? String(pedidos[0].cuerpo.position_ms) : 'ninguna');
comprobar('la dirección lleva algo que la distingue de la anterior',
          /[?&]v=\d+/.test(tv.ultimaUrl), tv.ultimaUrl);

// ------------------------------------------- 3. la barra no vuelve atrás mientras
reposar();
tv.obedece = false;
tv.posicion = 600;
tv.corriendo = false;
saltar(2400000);                      // minuto 40
avanzar(600);                         // una vuelta de reloj, para que se pinte
comprobar('la barra se queda en el destino durante el salto',
          doc.getElementById('elapsed').textContent === '40:00',
          doc.getElementById('elapsed').textContent);

// -------------------------------------------------- 4. no se encadenan los saltos
llamadasAntes = servidor.llamadas.length;
saltar(3000000);
avanzar(200);
comprobar('un segundo salto mientras hay uno en marcha no se manda',
          servidor.llamadas.filter(function (l) { return l.url.indexOf('playback/cast') >= 0; }).length ===
          servidor.llamadas.slice(0, llamadasAntes).filter(function (l) { return l.url.indexOf('playback/cast') >= 0; }).length);
comprobar('y se explica por qué',
          doc.getElementById('trabajo-texto').textContent.indexOf('Espera') >= 0,
          doc.getElementById('trabajo-texto').textContent);

// --------------------------------------- 5. cuando vuelve la imagen, se limpia todo
tv.posicion = 0.5; tv.corriendo = true;
avanzar(6000);
comprobar('con imagen de nuevo, el aviso desaparece', doc.getElementById('trabajo').hidden);

// ------------------------------------------------------ 6. Reiniciar usa el mismo camino
reposar();
tv.posicion = 900;
llamadasAntes = servidor.llamadas.length;
var reiniciar = app.activarBoton;
app.state.botones = app.botonesReproductor();
reiniciar(0);
avanzar(1500);
comprobar('Reiniciar deja el video al principio', tv.posicion < 3, 'quedó en ' + tv.posicion);

// ------------------------------------------- 7. imagen congelada sin aviso del televisor
reposar();
tv.corriendo = false; tv.posicion = 500;
app.state.playing.rescates = 0;
llamadasAntes = servidor.llamadas.length;
avanzar(5000);
comprobar('a los pocos segundos quietos, se avisa',
          doc.getElementById('trabajo-texto').textContent.indexOf('Recuperando') >= 0,
          doc.getElementById('trabajo-texto').textContent);
avanzar(11000);
var rescates = servidor.llamadas.slice(llamadasAntes).filter(function (l) { return l.url.indexOf('playback/cast') >= 0; });
comprobar('y si sigue quieta, se pide la lista de nuevo sin cambiar de edición',
          rescates.length === 1, rescates.length + ' rescates');

// ----------------------------------------------- 8. un salto fallido no cambia de fuente
reposar();
tv.posicion = 300;
servidor.responder = function (url, datos) {
  if (url.indexOf('playback/cast') >= 0) return { status: 500, body: { message: 'no se pudo' } };
  if (url.indexOf('playback/status') >= 0) return { status: 200, body: { active: true } };
  return { status: 200, body: {} };
};
var fuenteAntes = app.state.attempt;
tv.obedece = false;
saltar(5000000);
avanzar(4000);
comprobar('un salto que falla no salta a otra edición',
          app.state.attempt === fuenteAntes, 'intento ' + app.state.attempt);
comprobar('y lo dice sin dramatismo',
          doc.getElementById('trabajo-texto').textContent.indexOf('Se conserva el punto anterior') >= 0,
          doc.getElementById('trabajo-texto').textContent);


// ------------------------------------------- 9. una preparación lenta no se abandona
/* El equipo tarda de verdad: consultar proveedores, arrancar la sesión y convertir los
   primeros segundos suman más de un minuto en una edición pesada. Abandonar a mitad
   para probar la siguiente deja dos preparaciones peleando y acaba fallando todo. */
reposar();
app.state.dead = false;
servidor.llamadas.length = 0;
servidor.demora = 70000;              // el equipo tarda 70 s en contestar
servidor.responder = function (url, datos) {
  if (url.indexOf('playback/sources') >= 0) {
    return { status: 200, body: { items: [{ source_id: 'A' }, { source_id: 'B' }, { source_id: 'C' }] } };
  }
  if (url.indexOf('playback/start') >= 0) return { status: 200, body: { session_id: 'S9', position_ms: 0, subtitles: [] } };
  if (url.indexOf('playback/cast') >= 0) {
    return { status: 200, body: { stream_url: 'http://equipo/cast/A/index.m3u8', offset_ms: 0,
                                  duration_ms: 7200000, seekable_ms: 600000 } };
  }
  return { status: 200, body: {} };
};
tv.corriendo = false; tv.posicion = 0;
doc.getElementById('player-title').textContent = 'Título anterior';
arrancar('movie', 'peli-lenta', 'Una edición pesada');
comprobar('el título cambia al pedir el nuevo video y no conserva el anterior',
          doc.getElementById('player-title').textContent === 'Una edición pesada',
          doc.getElementById('player-title').textContent);
avanzar(60000);                       // un minuto entero esperando
var arranques = servidor.llamadas.filter(function (l) { return l.url.indexOf('playback/start') >= 0; });
comprobar('un minuto esperando al equipo no descarta la edición',
          arranques.length === 0 && !doc.getElementById('player-action').hidden === false,
          arranques.length + ' arranques, fin=' + !doc.getElementById('player-action').hidden);
comprobar('la espera sigue visible sin inventar porcentajes ni segundos',
          doc.getElementById('player-loading').hidden === false &&
          codigo.indexOf('player-percent') < 0 && codigo.indexOf('notaDesde') < 0,
          doc.getElementById('player-loading-text').textContent);
avanzar(120000);                      // que lleguen las tres respuestas
comprobar('cuando el equipo responde, la edición arranca sin haber cambiado de fuente',
          servidor.llamadas.filter(function (l) { return l.url.indexOf('playback/start') >= 0; }).length === 1,
          servidor.llamadas.filter(function (l) { return l.url.indexOf('playback/start') >= 0; }).length + ' arranques');

// ------------------------------ 10. si el equipo dice que no, entonces sí se prueba otra
servidor.demora = 0;
var fallosStart = 0;
servidor.responder = function (url, datos) {
  if (url.indexOf('playback/sources') >= 0) {
    return { status: 200, body: { items: [{ source_id: 'A' }, { source_id: 'B' }, { source_id: 'C' }] } };
  }
  if (url.indexOf('playback/start') >= 0) {
    fallosStart++;
    if (fallosStart < 3) return { status: 502, body: { message: 'esa edicion no responde' } };
    return { status: 200, body: { session_id: 'S10', position_ms: 0, subtitles: [] } };
  }
  if (url.indexOf('playback/cast') >= 0) {
    return { status: 200, body: { stream_url: 'http://equipo/cast/C/index.m3u8', offset_ms: 0,
                                  duration_ms: 7200000, seekable_ms: 600000 } };
  }
  return { status: 200, body: {} };
};
servidor.llamadas.length = 0;
app.state.dead = false;
arrancar('movie', 'peli-3', 'Tres ediciones');
avanzar(4000);
comprobar('dos ediciones que fallan llevan a la tercera, no al mensaje de rendición',
          fallosStart === 3 && app.state.dead === false,
          fallosStart + ' arranques, rendido=' + app.state.dead);

// ------------------------- 10b. el cuarto fallo no convierte una lista larga en vacía
reposar();
var intentosLargos = [];
servidor.responder = function (url, datos) {
  if (url.indexOf('playback/sources') >= 0) {
    return { status: 200, body: { items: [
      { source_id: 'A' }, { source_id: 'B' }, { source_id: 'C' },
      { source_id: 'D' }, { source_id: 'E' }, { source_id: 'F' }
    ] } };
  }
  if (url.indexOf('playback/start') >= 0) {
    intentosLargos.push(datos.source_id);
    return { status: 502, body: { message: 'esa edición no responde' } };
  }
  return { status: 200, body: {} };
};
app.state.dead = false;
arrancar('series', 'serie:1:6', 'Seis ediciones');
avanzar(5000);
comprobar('si fallan cuatro fuentes continúa con todas las alternativas en el orden recibido',
          intentosLargos.join(',') === 'A,B,C,D,E,F', intentosLargos.join(','));
comprobar('solo muestra No disponible después de agotar la lista completa',
          app.state.dead === true && doc.getElementById('player-note').textContent.indexOf('no está disponible') >= 0,
          'intentos=' + intentosLargos.length + ', rendido=' + app.state.dead);

// ----------------------------------------- 11. elegir versión a mano desde el mando
/* Una edición sin subtítulos, o con un audio que no sirve, deja la película invisible
   si no hay forma de cambiarla. El botón tiene que ir a la que se pidió, no a la
   siguiente de la cadena de reintentos. */
reposar();
servidor.demora = 0;
servidor.llamadas.length = 0;
var pedidas = [];
servidor.responder = function (url, datos) {
  if (url.indexOf('playback/sources') >= 0) {
    return { status: 200, body: { items: [
      { source_id: 'A', label: 'MovieBox 2Peckle', delivered: 1080, size_gb: 3.9,
        measured_bps: 3000000, speed_scope: 'server', demand_mbps: 5.8,
        ranking_tier: 0, origin: 'original', languages: ['spanish', 'english'] },
      { source_id: 'B', provider_name: 'MediaFusion', delivered: 720, size_gb: 1.2,
        demand_mbps: 3.1, origin: 'cine', languages: ['spanish'] },
      { source_id: 'C', provider_name: 'Flix-Streams', delivered: 720, size_gb: 0.5,
        origin: 'original', languages: [] }
    ] } };
  }
  if (url.indexOf('playback/start') >= 0) {
    pedidas.push(datos.source_id);
    return { status: 200, body: { session_id: 'S11', position_ms: 0, subtitles: [] } };
  }
  if (url.indexOf('playback/cast') >= 0) {
    return { status: 200, body: { stream_url: 'http://equipo/cast/x/index.m3u8', offset_ms: 0,
                                  duration_ms: 7200000, seekable_ms: 600000 } };
  }
  return { status: 200, body: {} };
};
app.state.dead = false;
arrancar('movie', 'peli-versiones', 'Con varias versiones');
avanzar(3000);
comprobar('arranca con la primera versión del orden del equipo',
          pedidas.length === 1 && pedidas[0] === 'A', pedidas.join(','));

app.abrirVersiones();
comprobar('el menú ofrece las tres versiones, no solo las que se prueban solas',
          doc.getElementById('subs').hidden === false &&
          doc.getElementById('subs').children.length === 5,
          doc.getElementById('subs').children.length + ' filas');
var primeraVersion = doc.getElementById('subs').children[2].innerHTML;
var segundaVersion = doc.getElementById('subs').children[3].innerHTML;
comprobar('cada versión destaca gráficamente resolución, nivel y datos comparables',
          primeraVersion.indexOf('quality-meter') >= 0 && primeraVersion.indexOf('network-meter') >= 0 &&
          primeraVersion.indexOf('1080p') >= 0 && primeraVersion.indexOf('Full HD') >= 0 &&
          primeraVersion.indexOf('Recomendada') >= 0 && primeraVersion.indexOf('Red') >= 0 &&
          primeraVersion.indexOf('Excelente') >= 0 && primeraVersion.indexOf('Servidor medido') >= 0 &&
          primeraVersion.indexOf('1080p con margen cómodo') >= 0 &&
          primeraVersion.indexOf('EN USO') >= 0 && primeraVersion.indexOf('24,0 Mbps') >= 0 &&
          primeraVersion.indexOf('5,8 Mbps') >= 0 && primeraVersion.indexOf('3,9 GB') >= 0 &&
          primeraVersion.indexOf('Original · Español · Inglés') >= 0,
          primeraVersion);
comprobar('la versión de cine se simplifica a Cine y oculta nombres técnicos',
          segundaVersion.indexOf('Cine') >= 0 && primeraVersion.indexOf('MovieBox') < 0 &&
          primeraVersion.indexOf('2Peckle') < 0 && primeraVersion.indexOf('PenguPlay') < 0,
          primeraVersion + ' / ' + segundaVersion);

app.state.menu = 2;                   // la tercera versión
app.chooseMenu();
comprobar('cambiar de versión reinicia la experiencia de carga por hitos',
          app.state.carga.activa && doc.getElementById('player-loading').hidden === false &&
          doc.getElementById('player-loading-text').textContent === 'Buscando en la red…',
          doc.getElementById('player-loading-text').textContent);
avanzar(3000);
comprobar('elegir la tercera versión va exactamente a esa',
          pedidas.length === 2 && pedidas[1] === 'C', pedidas.join(','));
comprobar('y el menú se cierra al elegir', doc.getElementById('subs').hidden === true);

// ------------------------------------------ 11b. elegir una pista de audio en la TV
reposar();
var cambiosAudio = [];
var consultasAudio = 0;
servidor.responder = function (url, datos) {
  if (url.indexOf('playback/sources') >= 0)
    return { status: 200, body: { items: [{ source_id: 'AUDIO' }] } };
  if (url.indexOf('playback/start') >= 0)
    return { status: 200, body: { session_id: 'SAUDIO', position_ms: 0, subtitles: [] } };
  if (url.indexOf('playback/audio') >= 0) {
    consultasAudio++;
    return { status: 200, body: {
      audio_stream_index: 2,
      items: [
        { stream_index: 1, language: 'hin', label: 'Hindi', selected: false },
        { stream_index: 2, language: 'eng', label: 'Inglés', selected: true }
      ]
    } };
  }
  if (url.indexOf('playback/cast') >= 0) {
    if (datos.audio_stream_index !== undefined) cambiosAudio.push(datos);
    var selected = datos.audio_stream_index === undefined ? 2 : datos.audio_stream_index;
    var castAudio = {
      stream_url: 'http://equipo/cast/audio/index.m3u8', offset_ms: datos.position_ms || 0,
      duration_ms: 7200000, seekable_ms: 600000
    };
    // El arranque prueba que la lista viene de la inspección paralela; el cambio de
    // pista sí devuelve el estado actualizado de la conversión.
    if (datos.audio_stream_index !== undefined) {
      castAudio.audio_stream_index = selected;
      castAudio.audio_tracks = [
        { stream_index: 1, language: 'hin', label: 'Hindi', selected: selected === 1 },
        { stream_index: 2, language: 'eng', label: 'Inglés', selected: selected === 2 }
      ];
    }
    return { status: 200, body: castAudio };
  }
  return { status: 200, body: {} };
};
arrancar('movie', 'peli-audio', 'Dos audios');
avanzar(9000);
comprobar('las pistas de audio se inspeccionan en paralelo sin pedirlo desde el menú',
          consultasAudio === 1 && app.state.audio.list.length === 2,
          'consultas=' + consultasAudio + ' pistas=' + app.state.audio.list.length);
var controlesAudio = app.botonesReproductor().map(function (boton) { return boton.label; });
comprobar('audio y subtítulos tienen controles independientes',
          controlesAudio.indexOf('Audio') >= 0 && controlesAudio.indexOf('Subtítulos') >= 0,
          controlesAudio.join(', '));
app.abrirAudio();
comprobar('el menú muestra las pistas embebidas y marca la que está sonando',
          doc.getElementById('subs').children[2].innerHTML.indexOf('Hindi') >= 0 &&
          doc.getElementById('subs').children[3].innerHTML.indexOf('Inglés') >= 0 &&
          doc.getElementById('subs').children[3].className.indexOf('on') >= 0,
          doc.getElementById('subs').children[3].innerHTML);
comprobar('el menú de audio no ofrece una acción manual de búsqueda',
          doc.getElementById('subs').innerHTML.toLowerCase().indexOf('buscar') < 0);
app.state.menu = 0;                 // Hindi, para comprobar que la elección es real
app.chooseMenu();
avanzar(200);
comprobar('cambiar audio pide esa pista sin perder el punto de la película',
          cambiosAudio.length === 1 && cambiosAudio[0].audio_stream_index === 1 &&
          cambiosAudio[0].position_ms >= 0 && app.state.audio.track === 1 &&
          /[?&]v=\d+/.test(tv.ultimaUrl),
          JSON.stringify(cambiosAudio));
comprobar('el selector de audio se cierra al aplicar', doc.getElementById('subs').hidden === true);

app.abrirSubtitulos();
var filasSubtitulos = doc.getElementById('subs').children;
var filaAmarilla = -1;
for (var fila = 2; fila < filasSubtitulos.length; fila++) {
  if (filasSubtitulos[fila].innerHTML.indexOf('Amarillo con borde') >= 0) filaAmarilla = fila;
}
comprobar('subtítulos ofrece estilos visuales sin mezclar opciones de audio',
          filaAmarilla >= 2 && doc.getElementById('subs').innerHTML.indexOf('Hindi') < 0,
          doc.getElementById('subs').innerHTML);
app.state.menu = filaAmarilla - 2;
app.chooseMenu();
comprobar('el estilo elegido se aplica y queda guardado',
          doc.getElementById('subtitle').className.indexOf('subtitle-yellow-outline') >= 0 &&
          guardadoLocal['sergioflix.caption-style'] === 'yellow-outline',
          doc.getElementById('subtitle').className);
tecla(10009); // cerrar el menú antes de la siguiente prueba

// ------------------------------------- 12. subtítulos que el equipo no alcanzó a traer
/* Buscar subtítulos no puede retrasar el arranque, pero tampoco perderse en silencio:
   una película en inglés sin texto es una película que no se puede ver. */
reposar();
var pedidosSub = 0;
servidor.llamadas.length = 0;
servidor.responder = function (url, datos) {
  if (url.indexOf('playback/sources') >= 0) return { status: 200, body: { items: [{ source_id: 'A' }] } };
  if (url.indexOf('playback/start') >= 0) {
    return { status: 200, body: { session_id: 'S12', position_ms: 0, subtitles: [], subtitles_pending: true } };
  }
  if (url.indexOf('playback/cast') >= 0) {
    return { status: 200, body: { stream_url: 'http://equipo/cast/A/index.m3u8', offset_ms: 0,
                                  duration_ms: 7200000, seekable_ms: 600000 } };
  }
  if (url.indexOf('playback/subtitles') >= 0) {
    pedidosSub++;
    return { status: 200, body: { items: [{ url: 'http://equipo/subtitles/a.vtt', label: 'Español 1' }] } };
  }
  return { status: 200, body: {} };
};
app.state.dead = false;
arrancar('movie', 'peli-sin-subs', 'En inglés');
avanzar(8000);
comprobar('si el equipo avisa que sigue buscando, se le vuelve a preguntar',
          pedidosSub === 1, pedidosSub + ' consultas');
comprobar('y los subtítulos entran con la película ya en marcha',
          app.state.subs.list.length === 1, app.state.subs.list.length + ' pistas');

// ------------------------------------------- 13. cuando de verdad no hay, se dice
reposar();
app.state.subs.list = []; app.state.subs.status = '';
servidor.responder = function (url) {
  if (url.indexOf('playback/subtitles') >= 0) return { status: 200, body: { items: [] } };
  return { status: 200, body: {} };
};
app.pedirSubtitulos(true);
avanzar(1500);
comprobar('sin subtítulos disponibles se dice, en vez de callar',
          app.state.subs.status.indexOf('volver a buscar') >= 0, app.state.subs.status);

// ------------------------------- 14. sincronía automática conserva el original
reposar();
servidor.llamadas.length = 0;
app.state.playing = { source: 'A', title: 'Película', video: 'tt1234567' };
app.state.subs.list = [{ source_id: 'SUB-ORIGINAL', url: 'http://equipo/subtitles/original.vtt',
                         label: 'Español · OpenSubtitles · mejor coincidencia' }];
app.state.subs.track = 0; app.state.subs.syncing = false;
servidor.responder = function (url, datos) {
  if (url.indexOf('playback/subtitles/sync') >= 0) return { status: 200, body: {
    changed: true, message: 'Subtítulos sincronizados.',
    item: { url: 'http://equipo/subtitles/synced/ajustado.vtt',
            label: 'Español · sincronizado automáticamente', automatic: true }
  } };
  return { status: 200, body: {} };
};
app.sincronizarSubtitulo(); avanzar(500);
var llamadaSync = servidor.llamadas.filter(function (call) {
  return call.url.indexOf('playback/subtitles/sync') >= 0;
})[0];
comprobar('sincronizar envía la edición de video y la pista exacta elegida',
          llamadaSync && llamadaSync.cuerpo.source_id === 'A' &&
          llamadaSync.cuerpo.subtitle_source_id === 'SUB-ORIGINAL');
comprobar('el resultado se añade sin reemplazar el subtítulo original',
          app.state.subs.list.length === 2 && app.state.subs.list[0].source_id === 'SUB-ORIGINAL' &&
          app.state.subs.track === 1, app.state.subs.list.length + ' pistas');

// --------------------------------------- 15. identidad, controles y finales verdaderos
/* La barra debe decir qué capítulo se ve, cerrarse antes de salir y no confundir un
   corte de red con el final de una película. Son tres defensas del mismo reproductor. */
app.state.item = { name: 'Mi serie', episodes: [
  { id: 'serie:1:4', season: 1, episode: 4, name: 'El capítulo perdido' }
] };
comprobar('la barra identifica serie, temporada, episodio y nombre del capítulo',
          app.tituloReproduccion('serie:1:4') === 'Mi serie · T1 E4 · El capítulo perdido',
          app.tituloReproduccion('serie:1:4'));

app.state.screen = 'player'; app.state.dead = false; app.state.menu = -1;
app.state.playing = { title: 'Mi serie', offset: 0, total: 7200000, source: 'A',
                      session: 'S-final', sequence: 0, video: 'serie:1:4', limite: 0 };
video.paused = false; tv.corriendo = true; tv.posicion = 960;
app.state.zone = 'bar'; doc.getElementById('controls').hidden = false;
app.state.controlsUntil = Date.now() + 5000;
avanzar(5500);
comprobar('los controles se ocultan solos aunque la barra tuviera el foco',
          doc.getElementById('controls').hidden && app.state.zone === 'none');

tecla(39);                              // mostrar y entrar a la barra
var reproduccionAntes = app.state.playing;
tecla(10009);                           // la primera vuelta solo cierra controles
comprobar('Retroceder con la barra visible no abandona la reproducción',
          doc.getElementById('controls').hidden && app.state.playing === reproduccionAntes);

servidor.llamadas.length = 0;
app.state.playing = { title: 'Película larga', offset: 0, total: 7200000, source: 'A',
                      session: 'S-corte', sequence: 0, video: 'peli-corte', limite: 0,
                      rescates: 0, ultimaPosicion: 960000 };
tv.posicion = 0;                         // Samsung puede perder el reloj junto con la fuente
app.whenFinished(); avanzar(200);
var avancesCorte = servidor.llamadas.filter(function (l) { return l.url.indexOf('playback/progress') >= 0; });
comprobar('un final al minuto 16 conserva el avance y no marca la película terminada',
          avancesCorte.length && avancesCorte[0].cuerpo.completed === false &&
          avancesCorte[0].cuerpo.position_ms === 960000,
          avancesCorte.length ? JSON.stringify(avancesCorte[0].cuerpo) : 'sin progreso');

servidor.llamadas.length = 0;
app.state.playing.session = 'S-fin'; app.state.playing.sequence = 0;
tv.posicion = 7199;
app.whenFinished(); avanzar(200);
var avancesFin = servidor.llamadas.filter(function (l) { return l.url.indexOf('playback/progress') >= 0; });
comprobar('el final verdadero sigue marcándose como terminado',
          avancesFin.length && avancesFin[0].cuerpo.completed === true,
          avancesFin.length ? JSON.stringify(avancesFin[0].cuerpo) : 'sin progreso');

// --------------------------------------- 16. ficha explícita y progreso por episodio
app.state.item = { id: 'tt7654321', type: 'movie', name: 'Mi película', episodes: [],
  playback: { videos: { tt7654321: { progress: 0.4, completed: false, updated_at: '2026-09-10' } } } };
app.renderDetail();
comprobar('una película empezada ofrece Reanudar y Ver desde el inicio',
          app.state.dacciones.length === 3 && app.state.dacciones[0].label === 'Reanudar' &&
          app.state.dacciones[1].label === 'Ver desde el inicio' && app.state.dacciones[2].id === 'list',
          app.state.dacciones.map(function (a) { return a.label; }).join(', '));

app.state.item = { id: 'tt7654322', type: 'series', name: 'Mi serie', resume_video_id: 'tt7654322:1:4', episodes: [
  { id: 'tt7654322:1:4', season: 1, episode: 4, name: 'Cuatro' },
  { id: 'tt7654322:1:5', season: 1, episode: 5, name: 'Cinco' }
], playback: { videos: {
  'tt7654322:1:4': { progress: 1, completed: true, updated_at: '2026-09-10' }
} } };
app.renderDetail();
comprobar('al completar T1:E4 la ficha prioriza T1:E5',
          app.state.dacciones[0].label === 'Continuar viendo · T1:E5' &&
          app.state.dacciones[0].video === 'tt7654322:1:5', app.state.dacciones[0].label);
var barraCompleta = doc.getElementById('dep0').children[0].children[1];
comprobar('el episodio completado muestra barra completa y check',
          barraCompleta && barraCompleta.className.indexOf('complete') >= 0 &&
          barraCompleta.children[0].style.width === '100%' && barraCompleta.children.length === 2,
          barraCompleta ? barraCompleta.children[0].style.width : 'sin barra');

app.state.item.playback.videos['tt7654322:1:4'] =
  { progress: 0.6, completed: false, updated_at: '2026-09-10' };
app.renderDetail();
comprobar('una serie en progreso identifica el episodio exacto y dibuja el porcentaje',
          app.state.dacciones[0].label === 'Continuar viendo · T1:E4' &&
          doc.getElementById('dep0').children[0].children[1].children[0].style.width === '60%',
          app.state.dacciones[0].label);

// Las temporadas no son una etiqueta decorativa: cambian la lista que se navega.
var casiTerminado = { id: 'tt7654323:2:1', season: 2, episode: 1, name: 'Otra vez' };
app.state.item = { id: 'tt7654323', type: 'series', name: 'Dos temporadas', episodes: [
  { id: 'tt7654323:1:1', season: 1, episode: 1, name: 'Primero' },
  casiTerminado
], playback: { videos: {
  'tt7654323:2:1': { position_ms: 3560000, duration_ms: 3600000,
    progress: 1, completed: true, updated_at: '2026-09-11' }
} } };
app.renderDetail();
comprobar('la ficha separa los capítulos por temporadas y abre la que corresponde al avance',
          doc.getElementById('detail-seasons').children.length === 2 && app.state.dseason === 1 &&
          doc.getElementById('detail-episodes').children.length === 1 &&
          doc.getElementById('detail-episodes').children[0].children[1].textContent.indexOf('T2 E1') === 0,
          'temporada=' + app.state.dseason + ', episodios=' + doc.getElementById('detail-episodes').children.length);
comprobar('una serie ya comenzada ofrece continuar y verla desde el inicio',
          app.state.dacciones[0].label === 'Continuar viendo · T2:E1' &&
          app.state.dacciones[1].label === 'Ver desde el inicio',
          app.state.dacciones.map(function (a) { return a.label; }).join(', '));

servidor.responder = function (url, datos) {
  if (url.indexOf('playback/sources') >= 0) return { status: 200, body: { items: [{ source_id: 'A' }] } };
  if (url.indexOf('playback/start') >= 0) return { status: 200, body: { session_id: 'S-EP', position_ms: 0, subtitles: [] } };
  if (url.indexOf('playback/cast') >= 0) return { status: 200, body: {
    stream_url: 'http://equipo/cast/A/index.m3u8?start=' + datos.position_ms,
    offset_ms: datos.position_ms, duration_ms: 3600000, seekable_ms: 3600000
  } };
  return { status: 200, body: {} };
};
servidor.llamadas.length = 0;
app.reproducirEpisodio(casiTerminado);
comprobar('un capítulo con menos de un minuto pregunta antes de abrirse',
          app.state.resumePrompt && doc.getElementById('resume-prompt').hidden === false &&
          doc.getElementById('resume-detail').textContent.indexOf('menos de un minuto') >= 0 &&
          servidor.llamadas.length === 0,
          doc.getElementById('resume-detail').textContent);
app.resolverEleccionEpisodio(0); avanzar(8000);
var reanudacionFinal = servidor.llamadas.filter(function (l) { return l.url.indexOf('playback/cast') >= 0; });
comprobar('continuar un capítulo casi terminado conserva la posición real aunque figure visto',
          reanudacionFinal.length && reanudacionFinal[0].cuerpo.position_ms === 3560000 &&
          reanudacionFinal[0].cuerpo.force_adapted === true,
          reanudacionFinal.length ? String(reanudacionFinal[0].cuerpo.position_ms) : 'sin reproducción');

servidor.llamadas.length = 0;
app.reproducirEpisodio(casiTerminado);
app.resolverEleccionEpisodio(1); avanzar(8000);
var reinicioFinal = servidor.llamadas.filter(function (l) { return l.url.indexOf('playback/cast') >= 0; });
comprobar('ver desde el inicio fuerza cero para ese mismo capítulo',
          reinicioFinal.length && reinicioFinal[0].cuerpo.position_ms === 0,
          reinicioFinal.length ? String(reinicioFinal[0].cuerpo.position_ms) : 'sin reproducción');

var css = fs.readFileSync(path.join(__dirname, '..', 'tv', 'style.css'), 'utf8');
comprobar('el modal usa una rueda simple y no un indicador numérico falso',
          css.indexOf('.loading-spinner {') >= 0 && css.indexOf('#player-percent') < 0 &&
          css.indexOf('.loading-progress') < 0, css.slice(css.indexOf('.loading-modal {'), css.indexOf('.controls {')).replace(/\s+/g, ' '));

// -------------------------------------- 17. cerrar invalida toda preparación anterior
/* Cada respuesta conserva deliberadamente su propia demora. Así se reproduce el fallo
   real: la TV ya volvió a la ficha, pero una respuesta vieja llega y trata de abrir el
   video que se abandonó. */
reposar();
servidor.llamadas.length = 0; tv.cargas.length = 0; tv.ultimaUrl = '';
servidor.responder = function (url) {
  if (url.indexOf('playback/sources') >= 0) {
    return { status: 200, delay: 5000, body: { items: [{ source_id: 'FUENTE-TARDIA-0000000001' }] } };
  }
  return { status: 200, body: {} };
};
arrancar('movie', 'peli-abandonada', 'Película abandonada');
avanzar(500); app.stopPlayback(false); avanzar(6000);
comprobar('salir durante la búsqueda impide que una respuesta tardía vuelva a arrancar',
          app.state.playing === null && tv.cargas.length === 0 &&
          servidor.llamadas.filter(function (l) { return l.url.indexOf('playback/start') >= 0; }).length === 0);

servidor.llamadas.length = 0; tv.cargas.length = 0; tv.ultimaUrl = '';
servidor.responder = function (url) {
  if (url.indexOf('playback/sources') >= 0) {
    return { status: 200, body: { items: [{ source_id: 'FUENTE-PENDIENTE-00000001' }] } };
  }
  if (url.indexOf('playback/start') >= 0) {
    return { status: 200, delay: 5000, body: { session_id: 'S-PENDIENTE', position_ms: 0, subtitles: [] } };
  }
  return { status: 200, body: {} };
};
arrancar('movie', 'peli-pendiente', 'Película pendiente'); avanzar(300);
app.stopPlayback(false); avanzar(6000);
var cierresPendientes = servidor.llamadas.filter(function (l) { return l.url.indexOf('playback/stop') >= 0; });
comprobar('salir durante el inicio cancela la fuente que ya se estaba preparando',
          cierresPendientes.length === 1 &&
          cierresPendientes[0].cuerpo.source_id === 'FUENTE-PENDIENTE-00000001' &&
          servidor.llamadas.filter(function (l) { return l.url.indexOf('playback/cast') >= 0; }).length === 0);

servidor.llamadas.length = 0; tv.cargas.length = 0; tv.ultimaUrl = '';
servidor.responder = function (url, datos) {
  if (url.indexOf('peli-anterior') >= 0) {
    return { status: 200, body: { items: [{ source_id: 'FUENTE-ANTERIOR-00000001' }] } };
  }
  if (url.indexOf('peli-nueva') >= 0) {
    return { status: 200, body: { items: [{ source_id: 'FUENTE-NUEVA-00000000001' }] } };
  }
  if (url.indexOf('playback/start') >= 0) {
    return { status: 200, body: { session_id: 'S-' + datos.source_id, position_ms: 0, subtitles: [] } };
  }
  if (url.indexOf('playback/cast') >= 0) {
    var vieja = datos.source_id.indexOf('ANTERIOR') >= 0;
    return { status: 200, delay: vieja ? 5000 : 100,
      body: { stream_url: 'http://equipo/' + (vieja ? 'anterior' : 'nueva') + '.m3u8',
              offset_ms: 0, duration_ms: 7200000, seekable_ms: 600000 } };
  }
  return { status: 200, body: {} };
};
arrancar('movie', 'peli-anterior', 'Anterior'); avanzar(400);
app.stopPlayback(false);
arrancar('movie', 'peli-nueva', 'Nueva'); avanzar(8000);
comprobar('abrir otra película nunca permite que la anterior llegue después y la reemplace',
          tv.ultimaUrl.indexOf('/nueva.m3u8') >= 0 && tv.ultimaUrl.indexOf('/anterior.m3u8') < 0,
          tv.ultimaUrl);

// ------------------------------------------ 18. el segundo hito dura unos seis segundos
reposar();
servidor.llamadas.length = 0; tv.cargas.length = 0; tv.ultimaUrl = '';
servidor.responder = function (url) {
  if (url.indexOf('playback/sources') >= 0) {
    return { status: 200, body: { items: [{ source_id: 'FUENTE-HITOS-000000000001' }] } };
  }
  if (url.indexOf('playback/start') >= 0) {
    return { status: 200, body: { session_id: 'S-HITOS', position_ms: 0, subtitles: [] } };
  }
  if (url.indexOf('playback/cast') >= 0) {
    return { status: 200, delay: 100,
      body: { stream_url: 'http://equipo/hitos.m3u8', offset_ms: 0,
              duration_ms: 7200000, seekable_ms: 600000 } };
  }
  return { status: 200, body: {} };
};
arrancar('movie', 'peli-hitos', 'Hitos'); avanzar(2000);
comprobar('el segundo hito sigue visible después de su antigua espera breve',
          doc.getElementById('player-loading-text').textContent === 'Eligiendo la mejor alternativa…');
avanzar(3500);
comprobar('la elección continúa visible cerca de los seis segundos',
          doc.getElementById('player-loading-text').textContent === 'Eligiendo la mejor alternativa…');
avanzar(1500);
comprobar('después de seis segundos pasa a la carga real del video',
          doc.getElementById('player-loading-text').textContent === 'Cargando…' ||
          doc.getElementById('player-success').hidden === false,
          doc.getElementById('player-loading-text').textContent);

// --------------------------------------------- 19. navegación completa del catálogo
console.log('\nNavegación del catálogo de TV\n');

function titulo(id, type) {
  return { id: id, type: type, name: 'Título ' + id, poster: 'http://imagen/' + id + '.jpg',
           background: 'http://imagen/' + id + '-fondo.jpg', releaseInfo: '2026', genres: ['Drama'] };
}

function versionKaraoke(id, calidad) {
  return { id: id, version_id: id, label: calidad + 'p', delivered: calidad,
           size_mb: 24, status: 'neutral' };
}
var cancionesKaraoke = [];
for (var k = 0; k < 10; k++) cancionesKaraoke.push({
  id: 'artista--cancion-' + k, type: 'karaoke_song', artist_id: 'artista', artist: 'Artista',
  name: 'Canción ' + (k + 1), title: 'Canción ' + (k + 1),
  season: 1, episode: k + 1, overview: 'Karaoke local · 2 versiones',
  thumbnail: '/karaoke/songs/artista--cancion-' + k + '/thumbnail',
  default_version_id: 'aaaaaaaaaaaaaaaaaaaa',
  versions: [versionKaraoke('aaaaaaaaaaaaaaaaaaaa', 1080),
             versionKaraoke('bbbbbbbbbbbbbbbbbbbb', 720)]
});
function artistaKaraoke() {
  return { id: 'artista', type: 'karaoke_artist', name: 'Artista',
    releaseInfo: '10 canciones', description: 'Canciones locales', genres: ['Favoritos'],
    poster: '/karaoke/artists/artista/poster', background: '/karaoke/artists/artista/background',
    episodes: cancionesKaraoke, playback: { videos: {} } };
}

servidor.responder = function (url, datos) {
  var items = [], i;
  if (url === '/api/karaoke') {
    for (i = 0; i < 3; i++) items.push({ id: i ? 'artista-' + i : 'artista',
      type: 'karaoke_artist', name: 'Artista ' + (i + 1), releaseInfo: '10 canciones',
      poster: '/karaoke/artists/artista/poster' });
    return { status: 200, body: { artist_count: 3, root_available: true, items: [
      { title: 'Favoritos', items: items },
      { title: 'Rock Clásico', items: [items[0]] }
    ] } };
  }
  if (url.indexOf('/api/karaoke/artists/') === 0) return { status: 200, body: artistaKaraoke() };
  if (url.indexOf('/api/karaoke/songs/') === 0 && url.indexOf('/play') >= 0) {
    return { status: 200, body: {
      stream_url: 'http://equipo/karaoke-' + datos.version_id + '.mp4',
      version_id: datos.version_id, content_type: 'video/mp4'
    } };
  }
  if (url.indexOf('/api/karaoke/songs/') === 0) return { status: 200, body: artistaKaraoke() };
  if (url.indexOf('catalog/series?skip=0&search=matrix') >= 0) {
    return { status: 200, body: { items: [titulo('tt-search-series', 'series')], next_skip: null } };
  }
  if (url.indexOf('catalog/movie?skip=0&search=matrix') >= 0) {
    return { status: 200, body: { items: [titulo('tt-search-movie', 'movie')], next_skip: null } };
  }
  if (url.indexOf('catalog/series?skip=50') >= 0) {
    for (i = 50; i < 100; i++) items.push(titulo('tt-series-' + i, 'series'));
    return { status: 200, body: { items: items, next_skip: 100 } };
  }
  if (url.indexOf('catalog/series?skip=0') >= 0) {
    for (i = 0; i < 35; i++) items.push(titulo('tt-series-' + i, 'series'));
    return { status: 200, body: { items: items, next_skip: 50 } };
  }
  if (url.indexOf('tv/channels?section=') >= 0) {
    var cable = url.indexOf('section=cable') >= 0;
    if (url.indexOf('section=deportes') >= 0) return { status: 200, body: { items: [
      { id: 'TyCSports.ar', name: 'TyC Sports', logo: 'http://imagen/tyc.png', categories: ['sports'] },
      { id: 'FIFAPlus.uk', name: 'FIFA+', logo: 'http://imagen/fifa.png', categories: ['sports'] }
    ] } };
    if (cable) return { status: 200, body: { items: [
      { id: 'CNNenEspanol.us', name: 'CNN en Español', logo: 'http://imagen/cnn.png',
        categories: ['news'], favorite: true, favorite_rank: 4 },
      { id: 'Cable.cl', name: 'Cable', logo: 'http://imagen/canal.png', categories: ['general'] }
    ] } };
    return { status: 200, body: { items: [
      { id: 'TVN.cl', name: 'TVN', logo: 'http://imagen/tvn.png', categories: ['general'], favorite: true, favorite_rank: 0 },
      { id: 'ChileVision.cl', name: 'CHV', logo: 'http://imagen/chv.png', categories: ['general'], favorite: true, favorite_rank: 1 },
      { id: 'Canal13.cl', name: 'Canal 13', logo: 'http://imagen/13.png', categories: ['general'], favorite: true, favorite_rank: 2 },
      { id: 'Mega.cl', name: 'Mega', logo: 'http://imagen/mega.png', categories: ['general'], favorite: true, favorite_rank: 3 }
    ] } };
  }
  return { status: 200, body: { items: [], next_skip: null } };
};

app.state.section = 'home'; app.state.homeZone = 'rows'; app.state.nav = 0;
app.state.row = 0; app.state.column = 0;
app.state.rows = [{ title: 'Categoría', wide: false,
  items: [
    { id: 'tt-a', title: 'Uno', meta: '2026', art: '', raw: titulo('tt-a', 'movie') },
    { id: 'tt-b', title: 'Dos', meta: '2026', art: '', raw: titulo('tt-b', 'movie') }
  ], more: { type: 'movie', skip: 0, buffered: [] } }];
app.renderHome(); app.show('home'); avanzar(500);
comprobar('cada categoría termina con una tarjeta para ver treinta títulos más',
          doc.getElementById('strip0').children.length === 3,
          doc.getElementById('strip0').children.length + ' tarjetas');
comprobar('la fila navegada queda marcada como activa',
          doc.getElementById('rows').children[0].className.indexOf('active') >= 0,
          doc.getElementById('rows').children[0].className);

tecla(38); tecla(39);
comprobar('subir desde la primera fila alcanza la barra y permite elegir Series',
          app.state.homeZone === 'nav' && app.state.nav === 1 &&
          doc.getElementById('nav-series').className.indexOf('focus') >= 0);
tecla(13); avanzar(800);
comprobar('Series abre una vista propia con doce sugerencias del día y su botón más',
          app.state.section === 'series' && app.state.rows.length === 1 &&
          app.state.rows[0].items.length === 12 && !!app.state.rows[0].more,
          app.state.rows[0].items.length + ' títulos');

app.state.column = 12; tecla(13); avanzar(800);
comprobar('el botón más agrega exactamente treinta sugerencias sin abandonar la fila',
          app.state.rows[0].items.length === 42 && app.state.column === 12,
          app.state.rows[0].items.length + ' títulos, foco ' + app.state.column);

var enfocada = doc.getElementById('card0_12'), carrusel = doc.getElementById('strip0');
enfocada.offsetLeft = 400; enfocada.offsetParent = carrusel; app.focusCard();
comprobar('la tarjeta enfocada usa coordenadas relativas y conserva margen de seguridad',
          carrusel.style.transform === 'translateX(-370px)', carrusel.style.transform);

app.abrirBusqueda(); avanzar(100);
doc.getElementById('search-input').value = 'matrix'; app.buscar(); avanzar(800);
comprobar('Buscar consulta películas y series y reúne sus resultados',
          app.state.searchResults.length === 2 && app.state.homeZone === 'searchResults',
          app.state.searchResults.length + ' resultados');

// Recorrido real del mando: escribir, seleccionar Buscar y confirmar sin invocar
// buscar() directamente. También comprobamos carga, errores y respuestas atrasadas.
var campoBusqueda = doc.getElementById('search-input');
var botonBusqueda = doc.getElementById('search-submit');
var responderAntesDeBusqueda = servidor.responder;
function consultasBusqueda() {
  return servidor.llamadas.filter(function (call) { return call.url.indexOf('&search=') >= 0; }).length;
}
app.abrirBusqueda(); avanzar(100); campoBusqueda.value = 'matrix';
var borrarBusqueda = bajarTecla(8); soltarTecla(8);
comprobar('Backspace en Buscar conserva la edición y permite el borrado nativo',
  app.state.searchEditing && doc.activeElement === campoBusqueda && !borrarBusqueda.defaultPrevented);
tecla(39);
comprobar('Derecha selecciona el botón Buscar con foco real y visible',
  app.state.homeZone === 'searchSubmit' && !app.state.searchEditing &&
  doc.activeElement === botonBusqueda && botonBusqueda.className.indexOf('focus') >= 0);
tecla(37); avanzar(100);
comprobar('Izquierda desde Buscar vuelve al texto sin borrarlo',
  app.state.searchEditing && doc.activeElement === campoBusqueda && campoBusqueda.value === 'matrix');
tecla(40);
comprobar('Abajo también selecciona el botón Buscar',
  app.state.homeZone === 'searchSubmit' && doc.activeElement === botonBusqueda);
var peticionesAntesDelOK = consultasBusqueda();
tecla(13); tecla(13);
comprobar('OK en Buscar envía una sola búsqueda aunque el mando repita la pulsación',
  app.state.searchBusy && consultasBusqueda() === peticionesAntesDelOK + 2 &&
  botonBusqueda.textContent.indexOf('Buscando') >= 0);
avanzar(300);
comprobar('terminar la búsqueda enfoca los resultados y libera el botón',
  !app.state.searchBusy && app.state.homeZone === 'searchResults' &&
  app.state.searchResults.length === 2 && doc.activeElement !== campoBusqueda &&
  doc.activeElement !== botonBusqueda && botonBusqueda.textContent === 'Buscar');
tecla(38);
comprobar('Arriba desde los resultados recupera el botón Buscar',
  app.state.homeZone === 'searchSubmit' && doc.activeElement === botonBusqueda);
tecla(37); avanzar(100); campoBusqueda.value = ' a ';
var peticionesAntesDeValidar = consultasBusqueda();
tecla(40); tecla(13); avanzar(100);
comprobar('una consulta corta vuelve al campo con explicación y sin llamar a la API',
  consultasBusqueda() === peticionesAntesDeValidar && app.state.searchEditing &&
  doc.activeElement === campoBusqueda && doc.getElementById('search-status').textContent.indexOf('dos letras') >= 0);
tecla(10009);
comprobar('Volver desde el texto cierra la edición y conserva el botón accesible',
  app.state.homeZone === 'searchSubmit' && doc.activeElement === botonBusqueda && campoBusqueda.value === ' a ');
tecla(10009);
comprobar('Volver desde el botón recupera el menú sin dejar un foco oculto',
  app.state.homeZone === 'nav' && doc.activeElement !== botonBusqueda && !app.state.searchEditing);
tecla(40); avanzar(100);
comprobar('Abajo desde el menú vuelve al campo de búsqueda',
  app.state.searchEditing && doc.activeElement === campoBusqueda);
tecla(40); doc.getElementById('nav-search').onmouseover();
comprobar('seleccionar el menú con puntero también libera el foco real del botón Buscar',
  app.state.homeZone === 'nav' && doc.activeElement !== botonBusqueda && !app.state.searchEditing);
tecla(40); avanzar(100);

var modoBusqueda = 'normal';
servidor.responder = function (url, datos) {
  if (url.indexOf('&search=') < 0) return responderAntesDeBusqueda(url, datos);
  var type = url.indexOf('catalog/series') >= 0 ? 'series' : 'movie';
  var term = decodeURIComponent(url.slice(url.indexOf('&search=') + 8));
  var delay = term === 'lenta' ? 800 : 100;
  if (modoBusqueda === 'error' || (modoBusqueda === 'parcial' && type === 'movie'))
    return { status: 503, body: { message: 'Catálogo temporalmente ocupado.' }, delay: delay };
  return { status: 200, body: { items: modoBusqueda === 'vacio' ? [] :
    [titulo('tt-' + term + '-' + type, type)], next_skip: null }, delay: delay };
};
modoBusqueda = 'vacio'; campoBusqueda.value = 'vacio'; tecla(40); tecla(13); avanzar(300);
comprobar('sin coincidencias el botón queda accesible para reintentar y explica el resultado',
  !app.state.searchBusy && !app.state.searchResults.length && app.state.homeZone === 'searchSubmit' &&
  doc.activeElement === botonBusqueda && doc.getElementById('search-status').textContent.indexOf('No se encontraron') >= 0);
modoBusqueda = 'error'; tecla(13); avanzar(300);
comprobar('un fallo de catálogo no se presenta como ausencia de títulos',
  !app.state.searchBusy && app.state.homeZone === 'searchSubmit' &&
  doc.getElementById('search-status').textContent.indexOf('No se pudo completar') >= 0);
modoBusqueda = 'parcial'; tecla(13); avanzar(300);
comprobar('un catálogo fallido conserva los resultados del otro y avisa que son parciales',
  !app.state.searchBusy && app.state.searchResults.length === 1 &&
  app.state.searchResults[0].raw.type === 'series' &&
  doc.getElementById('search-status').textContent.indexOf('incompleta') >= 0);
modoBusqueda = 'normal';
app.abrirBusqueda(); avanzar(100); campoBusqueda.value = 'lenta'; tecla(40); tecla(13);
tecla(37); avanzar(100); campoBusqueda.value = 'nueva'; tecla(40); tecla(13); avanzar(300);
comprobar('editar durante una carga permite enviar una nueva consulta',
  app.state.searchResults.length === 2 && app.state.searchResults[0].id.indexOf('tt-nueva-') === 0);
avanzar(800);
comprobar('la respuesta antigua no reemplaza los resultados de la consulta nueva',
  app.state.searchResults.length === 2 && app.state.searchResults[0].id.indexOf('tt-nueva-') === 0);
app.abrirBusqueda(); avanzar(100); campoBusqueda.value = 'lenta'; tecla(40); tecla(13);
campoBusqueda.focus(); campoBusqueda.value = 'texto editado'; avanzar(1000);
comprobar('editar el campo con foco nativo invalida la respuesta pendiente sin robar el foco',
  !app.state.searchBusy && app.state.searchEditing && doc.activeElement === campoBusqueda &&
  !app.state.searchResults.length && botonBusqueda.textContent === 'Buscar');
app.abrirBusqueda(); avanzar(100); campoBusqueda.value = 'lenta'; tecla(40); tecla(13);
tecla(10009); avanzar(1000);
comprobar('una respuesta tardía no quita el foco al menú',
  app.state.homeZone === 'nav' && doc.activeElement !== botonBusqueda &&
  botonBusqueda.className.indexOf('focus') < 0);
app.abrirBusqueda(); avanzar(100); campoBusqueda.value = 'lenta'; tecla(40); tecla(13);
app.activarNav(1); avanzar(1000);
comprobar('salir a Series impide que la búsqueda pendiente cambie su navegación',
  app.state.section === 'series' && app.state.homeZone === 'rows' && !app.state.searchBusy);
app.abrirBusqueda(); avanzar(100); campoBusqueda.value = 'lenta'; tecla(40); tecla(13);
app.abrirAsistente(); avanzar(1000);
comprobar('una búsqueda normal que termina bajo GPT no roba su foco',
  app.state.assistantOpen && doc.activeElement === doc.getElementById('assistant-input') &&
  app.state.assistantEditing);
tecla(10009);
comprobar('cerrar GPT recupera el botón Buscar desde donde se abrió',
  !app.state.assistantOpen && app.state.homeZone === 'searchSubmit' && doc.activeElement === botonBusqueda);
app.abrirBusqueda(); avanzar(100); campoBusqueda.value = 'clic'; botonBusqueda.onclick(); avanzar(300);
comprobar('el botón Buscar también conserva su funcionamiento con clic',
  app.state.searchResults.length === 2 && app.state.searchResults[0].id.indexOf('tt-clic-') === 0);
servidor.responder = responderAntesDeBusqueda;

app.activarNav(4); avanzar(800);
comprobar('Karaoke reutiliza las estanterías y carátulas de SergioFlix',
          app.state.section === 'karaoke' && app.state.rows.length === 2 &&
          app.state.rows[0].title === 'Favoritos' && !app.state.rows[0].wide,
          app.state.rows.length + ' estanterías');
tecla(13); avanzar(800);
comprobar('cada artista abre una ficha de serie y las canciones son sus capítulos',
          app.state.screen === 'detail' && app.state.item.type === 'karaoke_artist' &&
          app.state.item.episodes.length === 10 &&
          doc.getElementById('detail-strip-title').textContent === 'Canciones');
tecla(13); avanzar(2200);
comprobar('la canción usa el reproductor normal con la URL local del PC',
          app.state.screen === 'player' && app.state.playing.localKaraoke &&
          tv.ultimaUrl.indexOf('/karaoke-aaaaaaaaaaaaaaaaaaaa.mp4') >= 0,
          tv.ultimaUrl);
app.abrirVersiones(); app.state.menu = 1; app.chooseMenu(); avanzar(2200);
comprobar('cambiar versión conserva el reproductor y elige otro archivo local',
          app.state.screen === 'player' && app.state.playing.localKaraoke &&
          tv.ultimaUrl.indexOf('/karaoke-bbbbbbbbbbbbbbbbbbbb.mp4') >= 0,
          tv.ultimaUrl);
app.stopPlayback(false);
comprobar('Volver conserva la ficha del artista y su lista de canciones',
          app.state.screen === 'detail' && app.state.item.episodes.length === 10);

app.activarNav(5); avanzar(800);
comprobar('TV deja los cinco canales pedidos como favoritos y conserva las secciones',
          app.state.section === 'tv' && app.state.rows.length === 4 &&
          app.state.rows[0].title === 'Favoritos' && app.state.rows[0].items.length === 5 &&
          app.state.rows[0].items[0].id === 'TVN.cl' && app.state.rows[0].items[4].id === 'CNNenEspanol.us' &&
          app.state.rows[2].title === 'TV nacional' && app.state.rows[3].title === 'TV por cable',
          app.state.rows.length + ' filas');
comprobar('Deportes es una subcategoría propia justo después de Favoritos',
          app.state.rows[1].title === 'Deportes' && app.state.rows[1].items.length === 2 &&
          app.state.rows[1].items[0].id === 'TyCSports.ar',
          app.state.rows[1].title);

// Una ficha que desapareció de la biblioteca nunca puede secuestrar el mando.
servidor.responder = function (url) {
  if (url.indexOf('content/movie/tt-desaparecida') >= 0)
    return { status: 404, body: { message: 'No se encontró la ficha del título.' } };
  return { status: 200, body: {} };
};
app.openItem({ raw: titulo('tt-desaparecida', 'movie') }); avanzar(300);
comprobar('un elemento ausente muestra una salida visible',
          app.state.screen === 'brand' && doc.getElementById('brand-back').hidden === false,
          doc.getElementById('brand-note').textContent);
tecla(10009);
comprobar('Volver sale del error de biblioteca y recupera el catálogo',
          app.state.screen === 'home' && doc.getElementById('brand-back').hidden === true,
          app.state.screen);

// El acceso lateral aparece solo después de agotar el límite izquierdo y el
// resultado del asistente conserva un minuto sugerido hasta iniciar la película.
servidor.llamadas.length = 0;
servidor.responder = function (url, datos) {
  if (url.indexOf('assistant/search') >= 0) return { status: 200, body: {
    message: 'La escena corresponde a Spider-Man.', start_seconds: 1500, approximate: true,
    items: [titulo('tt-spiderman', 'movie')]
  } };
  if (url.indexOf('content/movie/tt-spiderman') >= 0)
    return { status: 200, body: titulo('tt-spiderman', 'movie') };
  if (url.indexOf('playback/sources') >= 0)
    return { status: 200, body: { items: [{ source_id: 'FUENTE-GPT-00000000000001' }] } };
  if (url.indexOf('playback/start') >= 0)
    return { status: 200, body: { session_id: 'S-GPT', position_ms: 7000, subtitles: [] } };
  if (url.indexOf('playback/cast') >= 0) return { status: 200, body: {
    stream_url: 'http://equipo/gpt.m3u8', offset_ms: 1500000,
    duration_ms: 7200000, seekable_ms: 1800000
  } };
  return { status: 200, body: {} };
};
app.state.column = 0; app.state.homeZone = 'rows'; app.show('home'); tecla(37);
comprobar('al agotar el borde izquierdo aparece el acceso GPT',
          app.state.homeZone === 'assistant' && doc.getElementById('gpt-launcher').hidden === false);
tecla(13); avanzar(100);
comprobar('GPT abre el campo de dictado del televisor',
          app.state.assistantOpen && doc.activeElement === doc.getElementById('assistant-input'));

// El DOM mínimo no edita texto: comprobamos que la app deje el borrado nativo libre
// y conserve el campo enfocado, incluso cuando ya no queda texto por borrar.
var campoGPT = doc.getElementById('assistant-input');
var peticionesAntesDeBorrarGPT = servidor.llamadas.length;
var casosBorradoGPT = [
  { code: 8, texto: 'consulta de prueba', nombre: 'Backspace con texto' },
  { code: 8, texto: '', nombre: 'Backspace con el campo vacío' },
  { code: 46, texto: 'consulta de prueba', nombre: 'Delete con texto' }
];
for (var casoGPT = 0; casoGPT < casosBorradoGPT.length; casoGPT++) {
  app.abrirAsistente(); avanzar(100);
  campoGPT.focus(); campoGPT.value = casosBorradoGPT[casoGPT].texto;
  var eventoBorradoGPT = bajarTecla(casosBorradoGPT[casoGPT].code);
  soltarTecla(casosBorradoGPT[casoGPT].code);
  comprobar(casosBorradoGPT[casoGPT].nombre + ' conserva abierto y enfocado el buscador GPT',
    app.state.assistantOpen && app.state.assistantEditing &&
    !doc.getElementById('assistant-overlay').hidden && doc.activeElement === campoGPT);
  comprobar(casosBorradoGPT[casoGPT].nombre + ' permite que el teclado nativo borre',
    !eventoBorradoGPT.defaultPrevented);
}
var teclasVolverGPT = [10009, 27, 461];
for (var volverGPT = 0; volverGPT < teclasVolverGPT.length; volverGPT++) {
  app.abrirAsistente(); avanzar(100);
  campoGPT.focus();
  tecla(teclasVolverGPT[volverGPT]);
  comprobar('Volver (' + teclasVolverGPT[volverGPT] + ') sigue cerrando GPT durante la edición',
    !app.state.assistantOpen && doc.getElementById('assistant-overlay').hidden &&
    doc.activeElement !== campoGPT);
}
comprobar('borrar y cerrar el campo GPT no inicia consultas ni peticiones adicionales',
  servidor.llamadas.length === peticionesAntesDeBorrarGPT);
app.abrirAsistente(); avanzar(100);
doc.getElementById('assistant-input').value = 'Muéstrame la escena de Spider-Man';
tecla(39);
comprobar('Derecha baja al botón Buscar y lo marca claramente',
          doc.getElementById('assistant-submit').className.indexOf('focus') >= 0 &&
          app.state.assistantZone === 'submit');
tecla(13); avanzar(800);
var llamadaGPT = servidor.llamadas.filter(function (l) { return l.url.indexOf('assistant/search') >= 0; })[0];
comprobar('la voz convertida a texto viaja como un prompt breve al PC',
          llamadaGPT && llamadaGPT.cuerpo.prompt === 'Muéstrame la escena de Spider-Man');
comprobar('la respuesta reúne texto, resultado y momento aproximado',
          app.state.assistantResults.length === 1 && app.state.assistantStartMs === 1500000 &&
          doc.getElementById('assistant-answer').textContent.indexOf('Spider-Man') >= 0);
tecla(13); avanzar(800);
comprobar('una escena crea un botón separado y conserva la reproducción normal',
          app.state.dacciones[0].id === 'play' && app.state.dacciones[1].id === 'scene');
app.activarDetalle(1); avanzar(800);
var saltoGPT = servidor.llamadas.filter(function (l) { return l.url.indexOf('playback/cast') >= 0; })[0];
comprobar('el momento sugerido se aplica al iniciar y reemplaza la reanudación anterior',
          saltoGPT && saltoGPT.cuerpo.position_ms === 1500000,
          saltoGPT ? saltoGPT.cuerpo.position_ms : 'sin inicio');
app.stopPlayback(false); avanzar(200);
app.state.item = { id: 'tt-silo', type: 'series', name: 'Silo', playback: { videos: {} },
  assistant_episode: { video_id: 'tt-silo:1:3', season: 1, episode: 3, title: 'Machines' },
  assistant_start_ms: 840000, episodes: [
    { id: 'tt-silo:1:2', season: 1, episode: 2, name: "Holston's Pick" },
    { id: 'tt-silo:1:3', season: 1, episode: 3, name: 'Machines' }
  ] };
app.renderDetail();
comprobar('la serie abre exactamente el capítulo resuelto dentro de su ficha',
          app.state.dacciones[0].video === 'tt-silo:1:3' &&
          app.state.dacciones[0].label.indexOf('T1:E3') >= 0);
comprobar('el minuto de una serie pertenece al capítulo correcto y tiene botón propio',
          app.state.dacciones[1].id === 'scene' && app.state.dacciones[1].video === 'tt-silo:1:3');
app.show('home'); avanzar(200);
tecla(10225); avanzar(100);
comprobar('la tecla de voz abre GPT cuando Tizen la entrega a la aplicación', app.state.assistantOpen);
tecla(10009); avanzar(100);

// Las veinte elecciones llegan como fichas normales y todas son accesibles con el mando.
var eleccionesGPT = [];
for (var gptIndex = 0; gptIndex < 20; gptIndex++) {
  var eleccionGPT = titulo('tt-eleccion-' + gptIndex, 'movie');
  eleccionGPT.assistant_reason = 'Robert De Niro destaca por su interpretación en la elección ' + gptIndex + '.';
  eleccionesGPT.push(eleccionGPT);
}
servidor.responder = function (url) {
  if (url.indexOf('assistant/search') >= 0) return { status: 200, body: {
    message: 'Te propongo estas películas.', items: eleccionesGPT, start_seconds: null, approximate: false
  } };
  if (url.indexOf('content/movie/tt-eleccion-19') >= 0)
    return { status: 200, body: eleccionesGPT[19] };
  return { status: 200, body: {} };
};
tecla(10225); avanzar(100);
// Al reabrir se enfoca la búsqueda anterior; subir dos veces permite editarla.
tecla(38); tecla(38);
doc.getElementById('assistant-input').value = 'Muéstrame películas de Robert De Niro';
tecla(13); avanzar(800);
comprobar('GPT muestra las veinte elecciones en el orden recibido',
          app.state.assistantResults.length === 20 &&
          app.state.assistantResults[0].raw.id === 'tt-eleccion-0' &&
          app.state.assistantResults[19].raw.id === 'tt-eleccion-19' &&
          doc.getElementById('assistant-results').children.length === 20 &&
          doc.getElementById('assistant-status').textContent === '20 resultados');
for (gptIndex = 0; gptIndex < 19; gptIndex++) tecla(39);
comprobar('el mando permite llegar a la vigésima elección de la IA', app.state.assistantIndex === 19);
comprobar('cada tarjeta muestra el motivo propio sin mezclar títulos',
          doc.getElementById('assistant-reason0').textContent === eleccionesGPT[0].assistant_reason &&
          doc.getElementById('assistant-reason19').textContent === eleccionesGPT[19].assistant_reason);
comprobar('el contador sigue la selección del mando', doc.getElementById('assistant-position').textContent === '20 / 20');
tecla(13); avanzar(800);
comprobar('OK abre la ficha de la vigésima elección', app.state.item.id === 'tt-eleccion-19');

// ----------------------------------------- volver a una búsqueda sin repetir la IA
var seccionAntesDelRegreso = app.state.section;
var consultaConservada = doc.getElementById('assistant-input').value;
var respuestaConservada = doc.getElementById('assistant-answer').textContent;
function consultasIA() { return servidor.llamadas.filter(function (call) {
  return call.url.indexOf('assistant/search') >= 0;
}).length; }
var consultasAntesDelRegreso = consultasIA();
tecla(10009);
comprobar('Volver desde una ficha recupera las veinte elecciones y la tarjeta seleccionada',
  app.state.assistantOpen && app.state.assistantIndex === 19 && app.state.assistantResults.length === 20 &&
  doc.getElementById('assistant-card19').className.indexOf('focus') >= 0);
comprobar('volver conserva consulta y respuesta sin otra petición de IA',
  doc.getElementById('assistant-input').value === consultaConservada &&
  doc.getElementById('assistant-answer').textContent === respuestaConservada &&
  consultasIA() === consultasAntesDelRegreso);
tecla(10009); tecla(10225); avanzar(100);
comprobar('cerrar y reabrir el asistente también conserva la búsqueda y su selección',
  app.state.assistantOpen && app.state.assistantIndex === 19 && !app.state.assistantEditing &&
  doc.getElementById('assistant-input').value === consultaConservada);
comprobar('volver conserva también las explicaciones y el contador',
          doc.getElementById('assistant-reason19').textContent === eleccionesGPT[19].assistant_reason &&
          doc.getElementById('assistant-position').textContent === '20 / 20');

var falloFicha = true, demoraFicha = 0, modoFuentes = 'fallan';
servidor.responder = function (url, datos) {
  if (url.indexOf('content/movie/tt-eleccion-') >= 0) return {
    status: falloFicha ? 503 : 200, delay: demoraFicha,
    body: falloFicha ? { message: 'No se pudo abrir la ficha.' } :
      titulo(url.slice(url.lastIndexOf('/') + 1), 'movie')
  };
  if (url.indexOf('playback/sources') >= 0) return modoFuentes === 'consulta'
    ? { status: 503, body: { message: 'El proveedor no respondió.' } }
    : { status: 200, body: { items: modoFuentes === 'vacio' ? [] :
      [{ source_id: 'REGRESO-A' }, { source_id: 'REGRESO-B' }] } };
  if (url.indexOf('playback/start') >= 0 && modoFuentes === 'fallan')
    return { status: 503, body: { message: 'La fuente no respondió.' } };
  return responderNormal(url, datos);
};
tecla(13); avanzar(300);
comprobar('una ficha que falla ofrece una salida antes de intentar reproducir',
  app.state.screen === 'brand' && !doc.getElementById('brand-back').hidden);
tecla(10009);
comprobar('el error de ficha devuelve al mismo resultado de IA',
  app.state.assistantOpen && app.state.assistantIndex === 19 && consultasIA() === consultasAntesDelRegreso);

falloFicha = false; demoraFicha = 1000;
tecla(13); tecla(10009); avanzar(1500);
comprobar('una ficha tardía no tapa los resultados después de volver durante su carga',
  app.state.screen === 'home' && app.state.assistantOpen && app.state.assistantIndex === 19);
falloFicha = true;
tecla(13); tecla(10009); avanzar(1500);
comprobar('un error tardío de ficha tampoco altera los resultados después de volver',
  app.state.screen === 'home' && app.state.assistantOpen && doc.getElementById('brand-back').hidden);
falloFicha = false; demoraFicha = 0;
tecla(13); avanzar(300); tecla(13); avanzar(1800);
var intentosRegreso = servidor.llamadas.filter(function (call) {
  return call.url.indexOf('playback/start') >= 0 && call.cuerpo.source_id.indexOf('REGRESO-') === 0;
});
comprobar('se agotan ambas fuentes antes de ofrecer Reintentar y Volver a resultados',
  app.state.dead && intentosRegreso.length === 2 && app.state.fin[0].label === 'Reintentar' &&
  app.state.fin[1].label === 'Volver a resultados');
doc.getElementById('fin1').onclick();
comprobar('el botón de regreso libera el intento y recupera la misma búsqueda con su foco',
  !app.state.playing && app.state.assistantOpen && app.state.assistantIndex === 19 &&
  consultasIA() === consultasAntesDelRegreso);
tecla(37); tecla(13); avanzar(300);
comprobar('después del fallo se puede elegir otro título de la misma respuesta',
  app.state.screen === 'detail' && app.state.item.id === 'tt-eleccion-18');

modoFuentes = 'consulta'; tecla(13); avanzar(500);
comprobar('un proveedor que no responde también permite reintentar o volver a resultados',
  app.state.dead && app.state.fin[0].label === 'Reintentar' &&
  app.state.fin[1].label === 'Volver a resultados');
modoFuentes = 'funciona'; tecla(13); avanzar(8000);
comprobar('Reintentar consulta de nuevo las fuentes del mismo título sin repetir la IA',
  !app.state.dead && app.state.playing.video === 'tt-eleccion-18' &&
  app.state.playing.source === 'REGRESO-A' && consultasIA() === consultasAntesDelRegreso,
  JSON.stringify({ dead: app.state.dead, play: app.state.playing, ia: consultasIA(), antes: consultasAntesDelRegreso }));
app.stopPlayback(false); modoFuentes = 'vacio'; tecla(13); avanzar(300); tecla(10009);
comprobar('Volver con el mando desde un título sin fuentes recupera el resultado elegido',
  app.state.assistantOpen && app.state.assistantIndex === 18 && !app.state.playing);

// Una escena conserva su destino incluso si el usuario reintenta tras agotar fuentes.
app.state.assistantResults[18].assistantStartMs = 840000;
tecla(13); avanzar(300); app.activarDetalle(1); avanzar(300);
modoFuentes = 'funciona'; tecla(13); avanzar(1000);
var reintentoEscena = servidor.llamadas.filter(function (call) {
  return call.url.indexOf('playback/cast') >= 0;
}).slice(-1)[0];
comprobar('Reintentar una escena conserva el minuto sugerido en la reproducción',
  reintentoEscena && reintentoEscena.cuerpo.position_ms === 840000);
app.stopPlayback(false); tecla(10009); tecla(10009);

// La búsqueda escrita usa el mismo regreso, sin abrir una respuesta antigua de IA.
modoFuentes = 'vacio';
servidor.responder = function (url, datos) {
  if (url.indexOf('catalog/') >= 0) return { status: 200, body: {
    items: [titulo(url.indexOf('/series') >= 0 ? 'tt-regreso-serie' : 'tt-regreso-pelicula',
      url.indexOf('/series') >= 0 ? 'series' : 'movie')], next_skip: null } };
  if (url.indexOf('content/') >= 0) return { status: 200, body:
    titulo(url.slice(url.lastIndexOf('/') + 1), url.indexOf('/series/') >= 0 ? 'series' : 'movie') };
  if (url.indexOf('playback/sources') >= 0) return { status: 200, body: { items: [] } };
  return responderNormal(url, datos);
};
app.abrirBusqueda(); avanzar(100);
doc.getElementById('search-input').value = 'matrix'; app.buscar(); avanzar(300);
var consultasCatalogo = servidor.llamadas.filter(function (call) { return call.url.indexOf('catalog/') >= 0; }).length;
tecla(39); tecla(13); avanzar(300); tecla(13); avanzar(300); tecla(10009);
comprobar('Volver desde una búsqueda escrita recupera la lista, el texto y la tarjeta sin abrir IA',
  app.state.screen === 'home' && app.state.section === 'search' && !app.state.assistantOpen &&
  app.state.homeZone === 'searchResults' && app.state.searchIndex === 1 &&
  !doc.getElementById('search-panel').hidden && doc.getElementById('search-input').value === 'matrix' &&
  servidor.llamadas.filter(function (call) { return call.url.indexOf('catalog/') >= 0; }).length === consultasCatalogo);
tecla(13); avanzar(300); tecla(10009);
comprobar('Volver desde una ficha escrita conserva el foco de su resultado',
  app.state.homeZone === 'searchResults' && app.state.searchIndex === 1 &&
  doc.getElementById('search-card1').className.indexOf('focus') >= 0);

app.abrirBusqueda(); app.abrirAsistente(); avanzar(100);
comprobar('abrir IA desde el campo de búsqueda no deja que el teclado oculto robe el foco',
  app.state.assistantOpen && doc.activeElement !== doc.getElementById('search-input'));
tecla(13); avanzar(300);
comprobar('abrir una elección de IA desde la búsqueda escrita mantiene libre el foco de la ficha',
  app.state.screen === 'detail' && doc.activeElement !== doc.getElementById('search-input'));
tecla(10009);
comprobar('Volver distingue los resultados de IA de la búsqueda escrita que quedó debajo',
  app.state.assistantOpen && app.state.assistantIndex === 18 && app.state.section === 'search' &&
  doc.getElementById('assistant-input').value === consultaConservada);
tecla(10009);

app.state.section = seccionAntesDelRegreso; app.state.homeZone = 'rows';
app.openItem({ raw: titulo('tt-catalogo-sin-fuentes', 'movie') }); avanzar(300);
tecla(13); avanzar(300);
comprobar('una reproducción abierta desde el catálogo mantiene su regreso a inicio',
  app.state.dead && app.state.fin[1].label === 'Ir a inicio');
tecla(10009);
comprobar('el catálogo no reabre una búsqueda de IA ajena al título',
  app.state.screen === 'home' && !app.state.assistantOpen);
app.renderHome(); avanzar(100);

/* ------------------------------------------------ la videoteca que va y viene

   Las comprobaciones viajan por el mismo wifi que la película, así que alguna se
   pierde por el camino. Una perdida no prueba nada; lo que no puede pasar es que
   el cartel entre y salga encima de una película que se está viendo bien. */
var mini = doc.getElementById('connection-mini');
var cartel = doc.getElementById('connection-lost');

// Empezar con el panel conectado, sin depender de respuestas pendientes de otros casos.
servidor.responder = responderNormal;
app.state.connectionFails = 1; app.state.connectionChecking = false;
app.api('providers', 'GET', null, function () {}, function () {}); avanzar(100);
servidor.responder = pcApagado;
app.state.connectionFails = 0; app.state.connectionLost = false; app.state.pidiendo = 0;
app.state.lastServerResponse = Date.now();
avanzar(20000);
comprobar('una comprobación perdida no apaga la pantalla',
          !app.state.connectionLost && cartel.hidden === true && mini.hidden === true,
          app.state.connectionFails + ' fallos apuntados');
avanzar(45000); // al menos tres comprobaciones, cualquiera sea la fase del intervalo
comprobar('pero si el PC insiste en no contestar, se avisa entero',
          app.state.connectionLost && cartel.hidden === false &&
          doc.getElementById('connection-retry').textContent === 'Reintentar',
          app.state.connectionFails + ' fallos apuntados');

servidor.responder = responderNormal;
avanzar(16000);
comprobar('cuando el PC vuelve, el cartel se va solo sin tocar el mando',
          !app.state.connectionLost && cartel.hidden === true && recargas === 0,
          recargas + ' recargas');

// Con película en marcha, el colchón cumple su función y la pérdida queda en silencio.
arrancar('movie', 'peli-2', 'Otra película');
tv.obedece = true; tv.corriendo = true; video.paused = false;
avanzar(9000);
comprobar('vuelve a haber película para la siguiente prueba',
          app.state.screen === 'player' && !!app.state.playing && !app.state.connectionLost);
servidor.responder = pcApagado;
avanzar(60000);
comprobar('con la imagen corriendo la pérdida de red permanece silenciosa',
          !app.state.connectionMini && mini.hidden === true && cartel.hidden === true,
          app.state.connectionFails + ' fallos, cartel oculto=' + cartel.hidden);
app.state.zone = 'none';
tecla(40);
comprobar('y el mando sigue siendo de quien ve la película',
          app.state.zone === 'bar', 'zona=' + app.state.zone);

// Si además la imagen se para de verdad, entonces sí no queda nada que ver.
video.paused = true; tv.corriendo = false;
avanzar(70000);
comprobar('con la imagen parada de verdad, el aviso pasa a pantalla completa',
          app.state.connectionLost && cartel.hidden === false && mini.hidden === true,
          app.state.connectionFails + ' fallos apuntados');

servidor.responder = responderNormal;
app.state.connectionChecking = false;
tecla(13); avanzar(600);
comprobar('Reintentar recupera la sesión sin recargar mientras haya película',
          !app.state.connectionLost && recargas === 0, recargas + ' recargas');

// Fuera del reproductor no hay nada que conservar: ahí recargar sí es lo correcto.
app.stopPlayback(true); avanzar(600);
servidor.responder = pcApagado;
// Cubre también un latido omitido por la respuesta fresca al cerrar el video.
avanzar(60000);
comprobar('en el catálogo, sin nada que ver, el aviso vuelve a ser el de siempre',
          app.state.connectionLost && cartel.hidden === false,
          JSON.stringify({ screen: app.state.screen, fails: app.state.connectionFails,
            last: app.state.lastServerResponse, now: ahora, origin: app.state.detailOrigin }));
servidor.responder = responderNormal;
app.state.connectionChecking = false;
tecla(13); avanzar(600);
comprobar('Reintentar comprueba el PC y recarga la sesión cuando vuelve',
          !app.state.connectionLost && recargas === 1, recargas + ' recargas');

// La inspección de audio no puede mantener el modal encima de una película viva.
servidor.responder = function (url, datos) {
  if (url.indexOf('playback/audio') >= 0)
    return { status: 200, delay: 30000, body: { items: [] } };
  return responderNormal(url, datos);
};
tv.corriendo = false;
arrancar('movie', 'peli-modal', 'Carga con audio lento'); avanzar(8000);
comprobar('con el video quieto se conserva el modal de carga',
          app.state.playing.arrancando && !doc.getElementById('player-loading').hidden);
video.disparar('canplay');
comprobar('canplay no retira la espera antes de que empiece la reproducción',
          !doc.getElementById('player-loading').hidden);
// Deja una transición pendiente que intentaría volver a mostrar «Cargando».
app.logroCarga('¡Listo!', 'Cargando…');
video.disparar('playing');
comprobar('playing retira inmediatamente el modal aunque el audio siga pendiente',
          app.state.audio.loading && !app.state.playing.arrancando &&
          !app.state.carga.activa && doc.getElementById('player-loading').hidden &&
          doc.getElementById('player-success').hidden);
tv.corriendo = true; avanzar(2000);
comprobar('las transiciones pendientes no vuelven a tapar la película',
          doc.getElementById('player-loading').hidden && doc.getElementById('player-success').hidden);

// Motores que omiten playing: el vigilante debe cerrar con evidencia del video.
tv.corriendo = false;
arrancar('movie', 'peli-sin-evento', 'Sin evento playing'); avanzar(8000);
video.paused = true; tv.posicion = 120; avanzar(600);
comprobar('una posición de reanudación en pausa no confirma reproducción',
          app.state.playing.arrancando && !doc.getElementById('player-loading').hidden);
app.state.pidiendo = 1;
video.paused = false; tv.corriendo = true; avanzar(600);
comprobar('el vigilante cierra el modal sin playing y con peticiones pendientes',
          app.state.pidiendo > 0 && !app.state.playing.arrancando &&
          doc.getElementById('player-loading').hidden && doc.getElementById('player-success').hidden);

app.state.pidiendo = 0;
arrancar('movie', 'peli-preparando', 'Preparando'); avanzar(2000);
video.disparar('playing');
comprobar('un playing atrasado no cancela la preparación del siguiente título',
          app.state.playing.arrancando && app.state.carga.activa &&
          app.state.carga.iniciarVideo !== null);
avanzar(32000); servidor.responder = responderNormal;

var salidas = 0;
global.tizen = { application: { getCurrentApplication: function () {
  return { exit: function () { salidas++; } };
} } };
app.activarNav(6);
comprobar('Salir cierra la aplicación Tizen', salidas === 1, salidas + ' llamadas');

console.log('\nMi lista y recomendaciones diarias\n');
app.state.playing = null; app.state.dead = false;
var favoritosPrueba = [];
for (var fp = 0; fp < 35; fp++) favoritosPrueba.push(titulo('tt-fav-' + fp, fp % 2 ? 'series' : 'movie'));
servidor.responder = function (url, datos) {
  if (url.indexOf('library/favorites/') >= 0) {
    if (!datos.enabled) favoritosPrueba.shift();
    return { status: 200, body: { favorite: datos.enabled } };
  }
  if (url.indexOf('library/favorites') >= 0) return { status: 200, body: { items: favoritosPrueba } };
  if (url.indexOf('content/') >= 0) return { status: 200, body: favoritosPrueba[0] };
  return responderNormal(url, datos);
};
app.activarNav(3); avanzar(800);
comprobar('Mi lista permite acceder a todos los guardados, incluidas películas y series',
          app.state.section === 'list' && app.state.rows[0].items.length === 35 &&
          app.state.rows[0].items[1].raw.type === 'series' && !app.state.rows[0].more);
tecla(13); avanzar(800);
comprobar('un título guardado abre su ficha desde Mi lista', app.state.screen === 'detail' && app.state.item.id === 'tt-fav-0');
var accionLista = -1;
for (var al = 0; al < app.state.dacciones.length; al++) if (app.state.dacciones[al].id === 'list') accionLista = al;
app.activarDetalle(accionLista); avanzar(500); tecla(10009); avanzar(800);
comprobar('quitar un título actualiza Mi lista al volver desde la ficha',
          app.state.screen === 'home' && app.state.rows[0].items.length === 34 && !app.state.favorites['tt-fav-0']);
favoritosPrueba = []; app.activarNav(3); avanzar(800);
comprobar('Mi lista vacía explica cómo guardar y deja el mando en el menú',
          app.state.screen === 'home' && !app.state.rows.length && app.state.homeZone === 'nav' &&
          doc.getElementById('hero-title').textContent === 'Tu lista está vacía');
tecla(40);
comprobar('Bajar con Mi lista vacía conserva una opción accesible', app.state.homeZone === 'nav');

var DateOriginal = Date, fechaDiaria = new DateOriginal(2026, 9, 1).getTime();
global.Date = function () { return new DateOriginal(fechaDiaria); };
Date.UTC = DateOriginal.UTC; Date.now = DateOriginal.now;
var candidatosDia = [];
for (var cd = 0; cd < 24; cd++) candidatosDia.push('pelicula-' + cd);
var seleccionHoy = app.seleccionDiaria(candidatosDia);
comprobar('las sugerencias permanecen iguales durante todo el día',
          seleccionHoy.join() === app.seleccionDiaria(candidatosDia).join());
fechaDiaria = new DateOriginal(2026, 9, 2).getTime();
var seleccionManana = app.seleccionDiaria(candidatosDia);
comprobar('al día siguiente aparece otra tanda de doce títulos',
          seleccionManana.slice(0, 12).every(function (id) { return seleccionHoy.slice(0, 12).indexOf(id) < 0; }));
comprobar('la rotación conserva todos los títulos para Ver más, sin duplicarlos',
          seleccionManana.length === 24 && seleccionManana.slice().sort().join() === candidatosDia.slice().sort().join());
comprobar('una colección vacía se mantiene vacía', app.seleccionDiaria([]).length === 0);
servidor.responder = function (url, datos) {
  if (url.indexOf('catalog/series') >= 0) return { status: 200, body: { items: [titulo('tt-dia', 'series')], next_skip: null } };
  return responderNormal(url, datos);
};
app.activarNav(1); avanzar(800);
var llamadasDia = servidor.llamadas.length;
app.tick();
comprobar('navegar el mismo día no vuelve a cargar las recomendaciones', servidor.llamadas.length === llamadasDia);
fechaDiaria = new DateOriginal(2026, 9, 3).getTime();
app.show('detail'); app.tick();
comprobar('el cambio de día no interrumpe una ficha abierta', app.state.screen === 'detail' && servidor.llamadas.length === llamadasDia);
app.show('player'); app.tick();
comprobar('el cambio de día no interrumpe el reproductor', app.state.screen === 'player' && servidor.llamadas.length === llamadasDia);
app.show('home'); app.tick(); avanzar(800);
comprobar('al volver al catálogo se cargan las recomendaciones del nuevo día',
          servidor.llamadas.length === llamadasDia + 1 && app.state.section === 'series' && app.state.screen === 'home');
global.Date = DateOriginal;

// ----------------------------------------------------- subtítulos y calibración
pendientes = []; tv.corriendo = false;
app.state.playing = { source: 'SUB-VIDEO', offset: 900000, total: 3600000 };
app.state.audio.track = 2;
video.currentTime = 1;
var textoUno = 'WEBVTT\n\n00:15:00.000 --> 00:15:04.000\nAnterior\n';
var textoDos = 'WEBVTT\n\n00:15:00.000 --> 00:15:04.000\nActual\n';
servidor.responder = function (url) {
  if (url.indexOf('slow.vtt') >= 0) return { status: 200, text: textoUno, delay: 800 };
  if (url.indexOf('current.vtt') >= 0) return { status: 200, text: textoDos, delay: 50 };
  return { status: 200, body: { changed: false } };
};
app.loadSubtitles([{ url: 'http://equipo/slow.vtt' }, { url: 'http://equipo/current.vtt' }]);
var vieja = peticionesTexto[peticionesTexto.length - 1];
app.state.subs.track = 1; app.fetchCues(1); avanzar(100);
comprobar('los subtítulos usan el reloj absoluto después de retomar', doc.getElementById('subtitle').textContent === 'Actual');
avanzar(900);
comprobar('una descarga anterior tardía no sobrescribe la pista seleccionada', app.state.subs.cues[0].text === 'Actual');
vieja.ontimeout();
comprobar('el timeout de una pista anterior no vacía la pista actual', app.state.subs.cues.length === 1);

app.loadSubtitles([{ url: 'http://equipo/slow.vtt' }]);
app.state.menuTipo = 'subs'; app.state.menu = 1; app.chooseMenu(); avanzar(900);
comprobar('Sin subtítulos conserva el texto apagado aunque termine una descarga',
          app.state.subs.track === -1 && !app.state.subs.cues.length && !doc.getElementById('subtitle').textContent);

pendientes = []; var peticionesSync = 0;
servidor.responder = function (url) {
  if (url.indexOf('playback/subtitles/sync') >= 0) {
    peticionesSync++;
    return { status: 200, body: { changed: true, item: { url: 'http://equipo/calibrated.vtt', automatic: true } } };
  }
  if (url.indexOf('calibrated.vtt') >= 0) return { status: 200, text: textoDos };
  if (url.indexOf('.vtt') >= 0) return { status: 200, text: textoUno };
  return { status: 200, body: {} };
};
var cargasAntes = tv.cargas.length;
app.loadSubtitles([{ url: 'http://equipo/original.vtt', source_id: 'SUB-ORIGINAL' }]); avanzar(3000);
comprobar('la TV comprueba automáticamente la pista una sola vez', peticionesSync === 1 && app.state.subs.list.length === 2);
comprobar('una calibración aceptada aparece sin recargar ni mover el video',
          doc.getElementById('subtitle').textContent === 'Actual' && tv.cargas.length === cargasAntes && video.currentTime === 1);
avanzar(5000);
comprobar('una pista calibrada no inicia otra comprobación automática', peticionesSync === 1);

pendientes = [];
servidor.responder = function (url) {
  if (url.indexOf('playback/subtitles/sync') >= 0)
    return { status: 200, delay: 1500, body: { changed: true, item: { url: 'http://equipo/calibrated.vtt', automatic: true } } };
  if (url.indexOf('.vtt') >= 0) return { status: 200, text: textoUno };
  return { status: 200, body: {} };
};
app.loadSubtitles([{ url: 'http://equipo/original.vtt', source_id: 'SUB-ORIGINAL' }]); avanzar(2700);
app.state.menuTipo = 'subs';
app.state.menu = app.menuOptions().findIndex(function (option) { return option.shift === -500; });
app.chooseMenu(); avanzar(2000);
comprobar('una corrección manual tiene prioridad sobre el cálculo automático en curso',
          app.state.subs.list.length === 1 && app.state.subs.sync === -500);

pendientes = [];
app.loadSubtitles([{ url: 'http://equipo/original.vtt', source_id: 'SUB-ORIGINAL' }]); avanzar(2700);
app.state.generacion++; app.loadSubtitles([]); app.state.subs.syncing = true; avanzar(2000);
comprobar('el resultado del capítulo anterior no cambia el estado de la reproducción nueva',
          app.state.subs.list.length === 0 && app.state.subs.syncing);

// ------------------------------------------------ regresiones al retomar y saltar
console.log('\nCoherencia de los saltos después de retomar\n');
function prepararSalto() {
  pendientes = []; app.state.generacion++; app.state.salto = null;
  app.startAt(video, 0); video.readyState = 4; video.seeking = false;
  app.state.screen = 'player'; app.state.dead = false; app.state.esperando = false;
  app.state.audio.switching = false; app.state.audio.track = 2;
  app.state.playing = { source: 'TEST', session: 'SEEK', sequence: 0,
    offset: 1800000, total: 7200000, limite: 1800000, rescates: 0,
    ultimaPosicion: 1800000, arrancando: false, watchedMs: 60000 };
  app.state.sources = ['TEST']; app.state.attempt = 0;
  app.state.ultimoT = -1; app.state.avanceEn = Date.now();
  tv.posicion = 0; tv.obedece = true; tv.corriendo = false; video.paused = false;
  video.seekable = { length: 1, start: function () { return 0; }, end: function () { return 1800; } };
  servidor.llamadas = []; servidor.demora = 0;
  servidor.responder = function (url, datos) {
    if (url.indexOf('playback/cast') >= 0) return { status: 200, body: {
      stream_url: 'http://equipo/cast/test/index.m3u8?generation=NEW',
      offset_ms: datos.position_ms, duration_ms: 7200000, seekable_ms: 600000,
      audio_stream_index: datos.audio_stream_index === undefined ? 2 : datos.audio_stream_index,
      audio_tracks: [{ stream_index: 1 }, { stream_index: 2 }] } };
    return { status: 200, body: {} };
  };
  setInterval(app.tick, 500);
}
function peticionesSalto() {
  return servidor.llamadas.filter(function (l) { return l.url.indexOf('playback/cast') >= 0; });
}

prepararSalto();
tv.posicion = 60;
app.saltarA(1820000); avanzar(1000);
comprobar('cambiar el reloj sin volver a correr no confirma un salto local',
          app.state.salto !== null && !doc.getElementById('trabajo').hidden);
app.saveProgress();
comprobar('un destino aún congelado no reemplaza el progreso guardado',
          servidor.llamadas.filter(function (l) { return l.url.indexOf('playback/progress') >= 0; }).length === 0);
avanzar(1500);
comprobar('un salto local congelado usa el respaldo sin esperar catorce segundos',
          peticionesSalto().length === 1 && peticionesSalto()[0].cuerpo.position_ms === 1820000);

prepararSalto();
video.paused = true;
app.saltarA(1810000); avanzar(600);
comprobar('retroceder o adelantar localmente en pausa confirma el punto sin reproducir',
          video.paused && app.state.salto === null && tv.posicion === 10);

prepararSalto();
video.paused = true;
app.saltarA(1795000); avanzar(1100);
comprobar('retroceder antes de lo retomado pide exactamente la escena anterior',
          peticionesSalto().length === 1 && peticionesSalto()[0].cuerpo.position_ms === 1795000);
comprobar('un salto por servidor conserva la pausa y confirma disponibilidad',
          video.paused && app.state.salto === null && app.state.playing.offset === 1795000);

prepararSalto();
video.seekable = { length: 2, start: function (i) { return i ? 60 : 20; },
                  end: function (i) { return i ? 120 : 40; } };
app.saltarA(1805000); avanzar(100);
comprobar('no intenta saltar localmente antes del inicio real del rango disponible',
          peticionesSalto().length === 1);
prepararSalto();
video.seekable = { length: 2, start: function (i) { return i ? 60 : 20; },
                  end: function (i) { return i ? 120 : 40; } };
app.saltarA(1850000); avanzar(100);
comprobar('un hueco entre rangos también necesita el servidor', peticionesSalto().length === 1);

prepararSalto(); servidor.demora = 1000;
app.saltarA(1790000); app.cambiarAudio(1);
comprobar('cambiar audio no lanza otra preparación mientras se está saltando',
          peticionesSalto().length === 1);
prepararSalto(); servidor.demora = 1000;
app.cambiarAudio(1); app.saltarA(1790000);
comprobar('un cambio de audio también bloquea nuevos saltos',
          peticionesSalto().length === 1 && peticionesSalto()[0].cuerpo.audio_stream_index === 1);

prepararSalto();
servidor.responder = function (url, datos) {
  if (url.indexOf('playback/cast') >= 0) return { status: 200, delay: 100000, body: {
    stream_url: 'http://equipo/cast/test/index.m3u8', offset_ms: datos.position_ms } };
  return { status: 200, body: {} };
};
app.saltarA(1790000); var lento = app.state.salto;
avanzar(91000); app.saltarA(1500000);
comprobar('a los noventa segundos la petición sigue siendo la misma y no se encadena otra',
          app.state.salto === lento && peticionesSalto().length === 1);

prepararSalto();
var numeroSalto = 0;
servidor.responder = function (url, datos) {
  if (url.indexOf('playback/cast') >= 0) {
    numeroSalto++;
    return { status: 200, delay: numeroSalto === 1 ? 1000 : 0, body: {
      stream_url: 'http://equipo/cast/test/index.m3u8', offset_ms: datos.position_ms } };
  }
  return { status: 200, body: {} };
};
app.saltarA(1790000); app.state.salto = null; // operación retirada con su respuesta ya en tránsito
app.saltarA(1500000); avanzar(100);
avanzar(1100);
comprobar('una respuesta retirada no cambia el destino de la operación vigente',
          app.state.playing.offset === 1500000 && (!app.state.salto || app.state.salto.target === 1500000));

prepararSalto();
var fallarPrimero = true;
servidor.responder = function (url, datos) {
  if (url.indexOf('playback/cast') >= 0 && fallarPrimero) {
    fallarPrimero = false;
    return { status: 504, body: { message: 'No entregó el destino', recovery_required: true } };
  }
  if (url.indexOf('playback/cast') >= 0) return { status: 200, body: {
    stream_url: 'http://equipo/cast/test/index.m3u8', offset_ms: datos.position_ms } };
  return { status: 200, body: {} };
};
app.saltarA(1790000); avanzar(300);
comprobar('si reemplazaron el material, el fallo recupera desde el último punto confirmado',
          peticionesSalto().length === 2 && peticionesSalto()[1].cuerpo.position_ms === 1800000 &&
          app.state.salto.rescate);

prepararSalto();
servidor.responder = function (url) {
  return url.indexOf('playback/cast') >= 0 ?
    { status: 504, body: { recovery_required: true } } : { status: 200, body: {} };
};
app.saltarA(1790000); avanzar(1000);
comprobar('dos fallos de preparación dejan una salida y no un bucle de rescates',
          peticionesSalto().length === 2 && video.paused && app.state.salto === null &&
          doc.getElementById('trabajo-texto').textContent.indexOf('Pulsa Volver') >= 0);

prepararSalto();
var recuperacionesFallidas = 0;
servidor.responder = function (url, datos) {
  if (url.indexOf('playback/cast') >= 0) {
    recuperacionesFallidas++;
    return recuperacionesFallidas === 1 ? { status: 200, body: {
      stream_url: 'http://equipo/cast/test/index.m3u8', offset_ms: datos.position_ms } } :
      { status: 504, body: { recovery_required: true } };
  }
  return { status: 200, body: {} };
};
app.saltarA(1500000); avanzar(47000);
comprobar('un destino sin imagen queda sin confirmar después de fallar su recuperación',
          app.state.salto === null && app.state.playing.posicionSinConfirmar &&
          app.state.playing.ultimaPosicion === 1800000);
app.saveProgress();
comprobar('el destino sin imagen tampoco se guarda tras terminar el aviso de fallo',
          servidor.llamadas.filter(function (l) { return l.url.indexOf('playback/progress') >= 0; }).length === 0);
app.stopPlayback(false); avanzar(100);
var cierreFallido = servidor.llamadas.filter(function (l) { return l.url.indexOf('playback/progress') >= 0; });
comprobar('Volver conserva el último punto confirmado después de un salto fallido',
          cierreFallido.length === 1 && cierreFallido[0].cuerpo.position_ms === 1800000);

prepararSalto();
app.saltarA(1810000); app.alternar(); avanzar(600);
comprobar('pausar durante un salto local conserva la nueva intención',
          video.paused && app.state.salto === null);

// La TV puede conservar waiting/seeking después de volver a mostrar imagen.
// Confirmar por avance real en el destino debe liberar también los subtítulos.
function prepararConfirmacionSalto(local, paused) {
  prepararSalto();
  app.state.subs.track = 0; app.state.subs.sync = 0; app.state.subs.render = null;
  app.state.subs.list = [{ key: 'subtitle-seek-test' }];
  app.state.subs.cues = [{ start: 0, end: 7200000, text: 'Subtítulo del destino' }];
  video.paused = !!paused; tv.corriendo = !paused;
  app.saltarA(local ? 1810000 : 1795000); avanzar(200); app.paintSubtitle();
}
function eventosSalto(event) {
  return servidor.llamadas.filter(function (call) {
    return call.url === '/api/events' && call.cuerpo.event === event;
  });
}

prepararConfirmacionSalto(false, false);
video.disparar('waiting'); avanzar(2500); app.paintSubtitle();
comprobar('el avance real en el destino cierra el salto aunque falte el evento de fin de carga',
          !app.state.salto && !app.state.esperando && !app.state.playing.posicionSinConfirmar &&
          doc.getElementById('trabajo').hidden && peticionesSalto().length === 1);
comprobar('cerrar el salto restaura los subtítulos y permite guardar el punto confirmado',
          doc.getElementById('subtitle').textContent === 'Subtítulo del destino');
app.saveProgress();
comprobar('el progreso vuelve a guardarse tras confirmar por imagen',
          servidor.llamadas.some(function (call) { return call.url === '/api/playback/progress'; }));
comprobar('la confirmación queda registrada una sola vez con el estado observado de la TV',
          eventosSalto('playback_seek_completed').length === 1 &&
          eventosSalto('playback_seek_completed')[0].cuerpo.waiting === true &&
          eventosSalto('playback_seek_completed')[0].cuerpo.reason === 'clock');

prepararConfirmacionSalto(false, false);
video.seeking = true; avanzar(2500); app.paintSubtitle();
comprobar('un seeking atrasado no conserva el aviso ni oculta subtítulos de un salto confirmado',
          !app.state.salto && video.seeking && doc.getElementById('trabajo').hidden &&
          doc.getElementById('subtitle').textContent === 'Subtítulo del destino');
video.disparar('seeking'); app.paintSubtitle();
comprobar('una nueva búsqueda nativa vuelve a ocultar el texto hasta conocer su posición',
          doc.getElementById('subtitle').textContent === '');
video.seeking = false; video.disparar('seeked');

prepararConfirmacionSalto(true, false);
video.disparar('waiting'); video.seeking = true; avanzar(1600);
comprobar('un salto local con imagen y señales atrasadas evita una reconversión innecesaria',
          !app.state.salto && peticionesSalto().length === 0 &&
          doc.getElementById('subtitle').textContent === 'Subtítulo del destino');

prepararConfirmacionSalto(false, false);
video.disparar('waiting'); video.seeking = true; pendientes = [];
for (var seekTick = 0; seekTick < 6; seekTick++) {
  ahora += 250; tv.posicion += 0.25; video.disparar('timeupdate');
}
comprobar('timeupdate confirma la imagen aunque el temporizador de vigilancia no se ejecute',
          !app.state.salto && doc.getElementById('trabajo').hidden &&
          doc.getElementById('subtitle').textContent === 'Subtítulo del destino');

prepararConfirmacionSalto(false, false);
tv.corriendo = false; video.disparar('playing'); video.disparar('canplay'); avanzar(3000);
comprobar('playing y canplay sin avance del reloj no confirman una imagen congelada',
          !!app.state.salto && eventosSalto('playback_seek_completed').length === 0);
app.saveProgress();
comprobar('un destino congelado sigue protegido contra el guardado de progreso',
          !servidor.llamadas.some(function (call) { return call.url === '/api/playback/progress'; }));

prepararConfirmacionSalto(false, false);
tv.corriendo = false; tv.posicion = 20; avanzar(21000);
tv.corriendo = true; avanzar(2000);
comprobar('reproducir lejos del destino no se vuelve una confirmación por dejar pasar el tiempo',
          !!app.state.salto && eventosSalto('playback_seek_completed').length === 0);

prepararConfirmacionSalto(false, false);
tv.corriendo = false; tv.posicion = 0; avanzar(600);
tv.posicion = 1; avanzar(600);
comprobar('un cambio aislado del reloj requiere más evidencia antes de confirmar el salto',
          !!app.state.salto && eventosSalto('playback_seek_completed').length === 0);

prepararConfirmacionSalto(false, false);
video.readyState = 1; avanzar(2500);
comprobar('el reloj sin un fotograma disponible no libera el salto ni los subtítulos',
          !!app.state.salto && doc.getElementById('subtitle').textContent === '');

prepararConfirmacionSalto(false, false);
pendientes = [];
for (var sameSeekTick = 0; sameSeekTick < 5; sameSeekTick++) {
  tv.posicion += 0.1; video.disparar('timeupdate');
}
comprobar('varios eventos en el mismo instante no simulan avance continuo',
          !!app.state.salto && eventosSalto('playback_seek_completed').length === 0);

prepararConfirmacionSalto(false, false);
var beforeAuditFailure = servidor.responder;
servidor.responder = function (url, data) {
  if (url === '/api/events') throw new Error('Fallo local de auditoría');
  return beforeAuditFailure(url, data);
};
avanzar(2500);
comprobar('un fallo local del registro no deja el reproductor atrapado en el salto',
          !app.state.salto && doc.getElementById('trabajo').hidden &&
          doc.getElementById('subtitle').textContent === 'Subtítulo del destino');

prepararConfirmacionSalto(false, true);
video.disparar('waiting'); avanzar(2500);
comprobar('un salto pausado conserva la pausa y espera disponibilidad sin inferir reproducción',
          !!app.state.salto && video.paused);
video.disparar('canplay'); avanzar(600);
comprobar('la disponibilidad confirma un salto pausado sin ponerlo a reproducir',
          !app.state.salto && video.paused && doc.getElementById('trabajo').hidden);

prepararConfirmacionSalto(false, false);
tv.corriendo = false; avanzar(47000);
comprobar('un salto sin imagen conserva la recuperación acotada desde el punto anterior',
          peticionesSalto().length === 2 && app.state.salto && app.state.salto.rescate &&
          peticionesSalto()[1].cuerpo.position_ms === 1800000 &&
          eventosSalto('playback_seek_failed')[0].cuerpo.reason === 'timeout');

prepararSalto();
video.readyState = 0;
app.startAt(video, 90); app.startAt(video, 20);
comprobar('solo queda el posicionamiento del recurso vigente',
          (video.oyentes.loadedmetadata || []).length === 1);
video.disparar('loadedmetadata');
comprobar('los metadatos aplican únicamente el nuevo destino', tv.posicion === 20);
app.startAt(video, 90); app.state.generacion++;
video.disparar('loadedmetadata');
comprobar('los metadatos de otra generación no mueven el video', tv.posicion === 20);

// --------------------------------------------- misma edición y ajustes al retomar
console.log('\nPreferencias al continuar viendo\n');
var huellaA = 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', huellaB = 'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb';
var pistaKey = 'cccccccccccccccccccccccccccccccc', otraKey = 'dddddddddddddddddddddddddddddddd';
var syncToken = 'eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee';
var preferenciasResume = { source_fingerprint: huellaB, audio_stream_index: 2,
  subtitle_key: pistaKey, subtitle_offset_ms: -2500, subtitle_manual: true,
  subtitle_disabled: false, subtitle_style: 'yellow-outline' };
var falloGuardada = '', sinGuardada = false, subsPendientes = false, subsVacias = false, audioDesaparecido = false;
function responderResume(url, datos) {
  if (url.indexOf('playback/sources') >= 0) return { status: 200, body: {
    preferences: preferenciasResume,
    items: sinGuardada ? [{ source_id: 'NUEVA-A', fingerprint: huellaA }] :
      [{ source_id: 'NUEVA-A', fingerprint: huellaA }, { source_id: 'NUEVA-B', fingerprint: huellaB }]
  } };
  var pistas = [{ source_id: 'SUB-NUEVA-1', key: otraKey, url: 'http://equipo/otra.vtt' },
                { source_id: 'SUB-NUEVA-2', key: pistaKey, url: 'http://equipo/guardada.vtt' }];
  if (url.indexOf('playback/start') >= 0) {
    if (datos.source_id === 'NUEVA-B' && falloGuardada === 'expired')
      return { status: 410, body: { code: 'source_expired', message: 'La fuente venció.' } };
    return { status: 200, body: { session_id: 'RESUME-' + datos.source_id, position_ms: 600000,
      source_fingerprint: datos.source_id === 'NUEVA-B' ? huellaB : huellaA,
      preferences: preferenciasResume, subtitles: subsPendientes || subsVacias ? [] : pistas,
      subtitles_pending: subsPendientes } };
  }
  if (url.indexOf('playback/cast') >= 0) {
    if (datos.source_id === 'NUEVA-B' && falloGuardada === 'cast')
      return { status: 502, body: { message: 'Falló esta edición.' } };
    if (audioDesaparecido && datos.audio_stream_index === 2)
      return { status: 422, body: { code: 'audio_track_not_found', message: 'Esa pista ya no existe.' } };
    return { status: 200, body: { stream_url: 'http://equipo/cast/' + datos.source_id + '/index.m3u8',
      offset_ms: datos.position_ms, duration_ms: 3600000, seekable_ms: 600000,
      audio_tracks: [{ stream_index: 2, label: 'Original' }],
      audio_stream_index: datos.audio_stream_index === undefined ? 0 : datos.audio_stream_index } };
  }
  if (url.indexOf('playback/audio') >= 0) return { status: 200, body: { items: [], audio_stream_index: 0 } };
  if (url.indexOf('playback/subtitles/') >= 0) return { status: 200, body: { items: pistas } };
  if (url.indexOf('.vtt') >= 0) return { status: 200, text: textoUno };
  return { status: 200, body: {} };
}
function retomarConPreferencias() {
  pendientes = []; servidor.llamadas.length = 0; servidor.responder = responderResume;
  video.readyState = 4; video.seekable = { length: 1, start: function () { return 0; }, end: function () { return 600; } };
  tv.corriendo = true; tv.obedece = true;
  app.playVideo('movie', 'retomar', 'Continuar viendo', false, 600000); avanzar(8000);
}
retomarConPreferencias();
var intentosResume = servidor.llamadas.filter(function (l) { return l.url.indexOf('playback/start') >= 0; });
comprobar('continuar elige la fuente guardada aunque su ID y el ranking cambien',
          intentosResume.length === 1 && intentosResume[0].cuerpo.source_id === 'NUEVA-B');
var castResume = servidor.llamadas.filter(function (l) { return l.url.indexOf('playback/cast') >= 0; });
comprobar('retoma el mismo minuto y la misma pista de audio',
          castResume[0].cuerpo.position_ms === 600000 && castResume[0].cuerpo.audio_stream_index === 2);
app.state.audio.inspected = false;
servidor.responder = function (url, datos) {
  if (url.indexOf('playback/audio') >= 0) return { status: 200, delay: 1200,
    body: { items: [{ stream_index: 0 }, { stream_index: 2 }], audio_stream_index: 0 } };
  return responderResume(url, datos);
};
app.abrirAudio(); app.pedirAudio();
app.state.audio.inspected = true; app.state.audio.track = 2; avanzar(1400);
comprobar('una inspección de audio tardía no sobrescribe la pista restaurada por el cast', app.state.audio.track === 2);
servidor.responder = responderResume;
comprobar('restaura la pista por identidad y conserva su desfase y estilo',
          app.state.subs.track === 1 && app.state.subs.sync === -2500 && app.state.subs.style === 'yellow-outline');
app.saveProgress(); avanzar(100);
var progresoResume = servidor.llamadas.filter(function (l) { return l.url.indexOf('playback/progress') >= 0; }).pop();
comprobar('el progreso envía fuente, pista y desfase para recuperarlos en otra TV',
          progresoResume.cuerpo.preferences.source_id === 'NUEVA-B' &&
          progresoResume.cuerpo.preferences.subtitle_key === pistaKey &&
          progresoResume.cuerpo.preferences.subtitle_offset_ms === -2500);
falloGuardada = 'cast'; retomarConPreferencias();
intentosResume = servidor.llamadas.filter(function (l) { return l.url.indexOf('playback/start') >= 0; });
comprobar('si la edición guardada falla prueba las otras, incluida la primera del ranking',
          intentosResume.length === 2 && intentosResume[0].cuerpo.source_id === 'NUEVA-B' &&
          intentosResume[1].cuerpo.source_id === 'NUEVA-A' && app.state.playing.source === 'NUEVA-A');
castResume = servidor.llamadas.filter(function (l) { return l.url.indexOf('playback/cast') >= 0; });
comprobar('el respaldo conserva el minuto y no hereda audio ni desfase de otra edición',
          castResume[1].cuerpo.position_ms === 600000 && castResume[1].cuerpo.audio_stream_index === undefined && app.state.subs.sync === 0);
falloGuardada = 'expired'; retomarConPreferencias();
comprobar('un enlace guardado caducado también permite retomar con otra fuente', app.state.playing.source === 'NUEVA-A');
falloGuardada = ''; sinGuardada = true; retomarConPreferencias();
comprobar('si la edición desapareció retoma con la alternativa disponible sin aplicar su desfase',
          app.state.playing.source === 'NUEVA-A' && app.state.playing.offset === 600000 && app.state.subs.sync === 0);
sinGuardada = false; audioDesaparecido = true; retomarConPreferencias();
castResume = servidor.llamadas.filter(function (l) { return l.url.indexOf('playback/cast') >= 0; });
comprobar('si falta solo el audio guardado conserva la edición y prueba su audio disponible',
          app.state.playing.source === 'NUEVA-B' && castResume.length === 2 && castResume[1].cuerpo.audio_stream_index === undefined);
audioDesaparecido = false; subsPendientes = true; retomarConPreferencias();
comprobar('los subtítulos que llegan después también recuperan su selección y ajuste',
          app.state.subs.track === 1 && app.state.subs.sync === -2500);
subsPendientes = false; preferenciasResume.subtitle_disabled = true; retomarConPreferencias();
comprobar('Sin subtítulos sigue apagado al retomar y ante una búsqueda posterior',
          app.state.subs.track === -1 && app.state.subs.disabled && !doc.getElementById('subtitle').textContent);
app.pedirSubtitulos(false); avanzar(100);
comprobar('una búsqueda posterior respeta que el espectador dejó los subtítulos apagados', app.state.subs.track === -1);
preferenciasResume.subtitle_disabled = false; preferenciasResume.subtitle_sync_token = syncToken;
subsVacias = true; retomarConPreferencias();
comprobar('recupera la copia calibrada incluso si el proveedor ya no ofrece pistas originales',
          app.state.subs.list[app.state.subs.track].sync_token === syncToken && app.state.subs.sync === -2500);
delete preferenciasResume.subtitle_sync_token; preferenciasResume.subtitle_offset_ms = 0;
subsVacias = false; retomarConPreferencias();
comprobar('una corrección manual centrada en cero también se conserva y bloquea recalibraciones',
          app.state.subs.manual && app.state.subs.sync === 0 &&
          servidor.llamadas.filter(function (l) { return l.url.indexOf('playback/subtitles/sync') >= 0; }).length === 0);
preferenciasResume.subtitle_sync_token = syncToken;
retomarConPreferencias();
servidor.responder = function (url, datos) {
  if (url.indexOf('/subtitles/synced/') >= 0) return { status: 404, body: {} };
  return responderResume(url, datos);
};
app.loadSubtitles([{ source_id: 'SUB-RECUPERADA', key: pistaKey, url: 'http://equipo/guardada.vtt' }], preferenciasResume);
avanzar(100); avanzar(100);
comprobar('si se limpió la copia calibrada recupera el original sin aplicarle el ajuste de esa copia',
          app.state.subs.track === 0 && app.state.subs.sync === 0 && !app.state.subs.manual && app.state.subs.cues.length > 0,
          JSON.stringify({ track: app.state.subs.track, sync: app.state.subs.sync, manual: app.state.subs.manual,
            status: app.state.subs.status, cues: app.state.subs.cues.length }));

// ------------------------------------------------------- fallo al leer el audio
retomarConPreferencias();
app.state.audio.list = []; app.state.audio.inspected = false; app.state.audio.track = -1;
servidor.responder = function (url, datos) {
  if (url.indexOf('playback/audio') >= 0) return { status: 504, body: { code: 'video_probe_failed', message: 'Falló la lectura.' } };
  return responderResume(url, datos);
};
app.abrirAudio(); app.pedirAudio(); avanzar(100);
var menuAudioError = doc.getElementById('subs').children.map(function (n) { return n.innerHTML || n.textContent; }).join(' ');
comprobar('un fallo de inspección dice que no pudo leer las pistas y no inventa que no hay alternativas',
          app.state.audio.error && menuAudioError.indexOf('No se pudieron leer las pistas') >= 0 &&
          menuAudioError.indexOf('Reintentar lectura de audio') >= 0,
          JSON.stringify(app.state.audio) + ' ' + menuAudioError);
servidor.responder = function (url, datos) {
  if (url.indexOf('playback/audio') >= 0) return { status: 200, body: {
    items: [{ stream_index: 2, label: 'Inglés', selected: true }], audio_stream_index: 2 } };
  return responderResume(url, datos);
};
app.state.menu = 1; app.chooseMenu(); avanzar(100);
comprobar('se puede reintentar una lectura fallida sin cambiar ni reiniciar la fuente',
          !app.state.audio.error && app.state.audio.list.length === 1 && app.state.audio.track === 2 &&
          app.state.playing.source === 'NUEVA-B',JSON.stringify(app.state.audio));

// ------------------------------------------ diario y retirada de una fuente rota
console.log('\nDiario de reproducción y respaldo\n');
servidor.responder = responderNormal;
app.playVideo('movie', 'diario-1', 'Diario'); tv.corriendo = true; avanzar(9000);
// Los escenarios aislados anteriores limpiaron el reloj. Restaurar los mismos
// intervalos que instala start() para medir avance y reenvío reales.
setInterval(app.tick, 500); setInterval(app.enviarDiario, 15000);
var traceDiario = app.state.playing.trace.id;
var eventosDiario = function () { return servidor.llamadas.filter(function (call) {
  return call.url === '/api/playback/event' && call.cuerpo.attempt_id === traceDiario;
}); };
video.disparar('playing'); video.disparar('playing'); avanzar(100);
comprobar('el primer fotograma se registra una sola vez por intento',
          eventosDiario().filter(function (call) { return call.cuerpo.event === 'first_frame'; }).length === 1);
video.disparar('waiting'); video.disparar('waiting'); avanzar(1000);
video.disparar('playing'); avanzar(100);
comprobar('un atasco registra inicio y duración sin duplicar waiting',
          eventosDiario().filter(function (call) { return call.cuerpo.event === 'buffering'; }).length === 1 &&
          eventosDiario().some(function (call) { return call.cuerpo.event === 'buffering_end' && call.cuerpo.duration_ms > 0; }));
avanzar(65000);
comprobar('un minuto de avance real confirma estabilidad', eventosDiario().some(function (call) {
  return call.cuerpo.event === 'stable' && call.cuerpo.duration_ms >= 60000;
}));

var diarioSinRed = true, reenviosDiario = [];
servidor.responder = function (url, datos) {
  if (url === '/api/playback/event') {
    reenviosDiario.push(datos.event_id);
    return { status: diarioSinRed ? 0 : 200, body: { accepted: true } };
  }
  return responderNormal(url, datos);
};
video.disparar('waiting'); avanzar(100);
var colaSinRed = JSON.parse(guardadoLocal['sergioflix.playback-events']);
comprobar('un corte de telemetría conserva el evento sin apagar la reproducción',
          colaSinRed.length > 0 && app.state.playing.source === 'A' && !video.paused);
var eventPendiente = colaSinRed[0].data.event_id;
diarioSinRed = false; avanzar(16000);
comprobar('el reenvío conserva el ID para que PostgreSQL pueda deduplicarlo',
          reenviosDiario.filter(function (id) { return id === eventPendiente; }).length >= 2);
comprobar('la cola se vacía al volver la conexión', JSON.parse(guardadoLocal['sergioflix.playback-events']).length === 0);

var sourceAntes = app.state.playing.source;
app.state.playing.ultimaPosicion = 720000;
app.state.playing.rescates = 2;
app.whenBroken(); avanzar(100);
comprobar('el recurso abandonado se retira antes de cargar el respaldo', tv.ultimaUrl === '' && video.paused);
avanzar(9000);
var respaldoStart = servidor.llamadas.filter(function (call) { return call.url.indexOf('playback/start') >= 0; }).slice(-1)[0];
var respaldoCast = servidor.llamadas.filter(function (call) { return call.url.indexOf('playback/cast') >= 0; }).slice(-1)[0];
comprobar('el respaldo tiene otro intento y conserva el último minuto confirmado',
          respaldoStart.cuerpo.attempt_id !== traceDiario && respaldoCast.cuerpo.position_ms === 720000);
comprobar('el cambio automático queda identificado en el diario', eventosDiario().some(function (call) {
  return call.cuerpo.event === 'source_switch' && call.cuerpo.switch_reason === 'automatic';
}));
comprobar('la fuente abandonada se libera explícitamente', servidor.llamadas.some(function (call) {
  return call.url.indexOf('playback/stop') >= 0 && call.cuerpo.source_id === sourceAntes;
}));

// ------------------------------------ candidatas externas y restauraciones antiguas
pendientes = []; tv.corriendo = false;
app.state.playing = { source: 'OFFICE-VIDEO', offset: 900000, total: 1921000 };
app.state.audio.track = 1; video.currentTime = 1;
var officeCalls = 0, officeVerified = false;
servidor.responder = function (url) {
  if (url.indexOf('playback/subtitles/sync') >= 0) {
    officeCalls++;
    return { status: 200, delay: 1000, body: officeVerified ? {
      changed: true, verified: true, item: { url: 'http://equipo/office-verified.vtt',
        key: 'correct-office', automatic: true, validation_required: true, verified: true }
    } : { changed: false, verified: false, message: 'No encontramos subtítulos compatibles con esta versión.' } };
  }
  if (url.indexOf('office-verified.vtt') >= 0) return { status: 200, text: textoDos };
  return { status: 200, text: textoUno };
};
var oldOfficePreferences = { subtitle_key: 'wrong-office', subtitle_manual: true,
  subtitle_offset_ms: -38000, subtitle_sync_token: 'a'.repeat(40) };
var officeCandidates = [{ source_id: 'OFFICE-SUB', key: 'wrong-office',
  url: 'http://equipo/wrong-office.vtt', validation_required: true, verified: false }];
app.loadSubtitles(officeCandidates, oldOfficePreferences);
comprobar('una candidata externa y su preferencia antigua no muestran texto antes de verificar',
  !app.state.subs.cues.length && !doc.getElementById('subtitle').textContent && app.state.subs.list.length === 1);
avanzar(3000);
comprobar('una preferencia manual antigua también se verifica', officeCalls === 1);
avanzar(1500);
comprobar('un rechazo deja el texto apagado y explica que no hay una pista compatible',
  !app.state.subs.cues.length && !doc.getElementById('subtitle').textContent &&
  app.state.subs.status.indexOf('compatibles') >= 0);
officeVerified = true; var loadsBeforeOffice = tv.cargas.length;
app.loadSubtitles(officeCandidates, oldOfficePreferences); avanzar(4500);
comprobar('el respaldo verificado conserva su identidad y elimina el desfase de la pista incompatible',
  app.state.subs.list[app.state.subs.track].key === 'correct-office' && app.state.subs.sync === 0 &&
  doc.getElementById('subtitle').textContent === 'Actual');
comprobar('verificar y sustituir subtítulos no recarga ni mueve el video en TV',
  tv.cargas.length === loadsBeforeOffice && video.currentTime === 1);
var verifiedOfficeCalls = officeCalls;
app.loadSubtitles(officeCandidates, { subtitle_key: 'wrong-office', subtitle_manual: true,
  subtitle_offset_ms: -500, subtitle_sync_token: 'b'.repeat(40), subtitle_verified: true });
avanzar(3500);
comprobar('una copia ya verificada conserva el ajuste manual al retomar sin recalibrar',
  app.state.subs.manual && app.state.subs.sync === -500 && officeCalls === verifiedOfficeCalls &&
  app.state.subs.list[app.state.subs.track].verified);

// ------------------------------------------- avance sostenido del mando real
function prepararAvance() {
  pendientes = []; servidor.llamadas = []; app.state.generacion++;
  app.state.screen = 'player'; app.state.zone = 'none'; app.state.menu = -1;
  app.state.notice = null; app.state.dead = false; app.state.connectionLost = false;
  app.state.salto = null; app.state.scrubHold = null; app.state.audio.switching = false;
  app.state.playing = { source: 'HOLD-VIDEO', offset: 0, total: 7200000, limite: 30000,
    ultimaPosicion: 120000, session: 'HOLD-SESSION', sequence: 0, watchedMs: 60000 };
  tv.corriendo = false; tv.obedece = true; tv.posicion = 120;
  video.paused = false; video.readyState = 4; video.seeking = false; app.state.esperando = false;
  video.seekable = { length: 1, start: function () { return 0; }, end: function () { return 30; } };
  servidor.responder = function (url, datos) {
    if (url.indexOf('playback/cast') >= 0) return { status: 200, body: {
      stream_url: 'http://equipo/hold.m3u8', offset_ms: datos.position_ms,
      duration_ms: 7200000, seekable_ms: 600000 } };
    return { status: 200, body: { accepted: true } };
  };
}
function saltosAvance() { return servidor.llamadas.filter(function (call) {
  return call.url.indexOf('playback/cast') >= 0;
}); }
prepararAvance(); tecla(39); var destinoTap = app.state.scrub; avanzar(1800);
comprobar('un toque corto mueve diez segundos desde la primera pulsación y se detiene al soltar',
  destinoTap === 130000 && app.state.scrub === destinoTap && !app.state.scrubHold && tv.posicion === 120);
tecla(37);
comprobar('un toque de retroceso conserva la precisión de diez segundos', app.state.scrub === 120000);

prepararAvance(); bajarTecla(39); avanzar(800);
comprobar('mantener el mando activa el nivel 3x y lo muestra junto al destino',
  doc.getElementById('scrub-time').textContent.indexOf('3×') >= 0);
var inicioTramo = app.state.scrub; avanzar(1000);
comprobar('el nivel 3x recorre treinta segundos por segundo de pulsación', Math.abs(app.state.scrub - inicioTramo - 30000) < 1);
avanzar(900); inicioTramo = app.state.scrub; avanzar(1000);
comprobar('la pulsación sostenida sube a 6x y duplica el recorrido',
  doc.getElementById('scrub-time').textContent.indexOf('6×') >= 0 && Math.abs(app.state.scrub - inicioTramo - 60000) < 1);
avanzar(1500); inicioTramo = app.state.scrub; avanzar(1000);
comprobar('la pulsación sostenida llega a 10x con recorrido de cien segundos por segundo',
  doc.getElementById('scrub-time').textContent.indexOf('10×') >= 0 && Math.abs(app.state.scrub - inicioTramo - 100000) < 1);
comprobar('explorar a cualquier velocidad no solicita conversiones ni mueve el video', saltosAvance().length === 0 && tv.posicion === 120);
app.saveProgress(false); avanzar(100);
var progresoExploracion = servidor.llamadas.filter(function (call) { return call.url.indexOf('playback/progress') >= 0; }).pop();
comprobar('el destino explorado nunca reemplaza el progreso real', progresoExploracion.cuerpo.position_ms === 120000);
soltarTecla(39); var destinoHold = app.state.scrub; avanzar(1400);
comprobar('soltar detiene inmediatamente el cursor y retira la velocidad',
  app.state.scrub === destinoHold && !app.state.scrubHold && doc.getElementById('scrub-time').textContent.indexOf('×') < 0);
tecla(13);
comprobar('OK realiza un único salto al destino final mediante la ruta existente',
  saltosAvance().length === 1 && saltosAvance()[0].cuerpo.position_ms === destinoHold);

prepararAvance(); bajarTecla(39); avanzar(2000); var sinRepeticiones = app.state.scrub; soltarTecla(39);
prepararAvance(); bajarTecla(39);
for (var repeat = 0; repeat < 20; repeat++) { avanzar(100); bajarTecla(39); }
comprobar('la velocidad es idéntica aunque el mando envíe muchas repeticiones', app.state.scrub === sinRepeticiones);
soltarTecla(39); var antesDelNuevoToque = app.state.scrub; tecla(39);
comprobar('una pulsación nueva vuelve a diez segundos sin heredar aceleración', app.state.scrub === antesDelNuevoToque + 10000);

prepararAvance(); tv.posicion = 3600; bajarTecla(39); avanzar(5500);
var antesDeInvertir = app.state.scrub; bajarTecla(37); soltarTecla(39);
comprobar('invertir el sentido reinicia la aceleración y el keyup anterior no la cancela',
  app.state.scrub === antesDeInvertir - 10000 && app.state.scrubHold && app.state.scrubHold.direction === -1);
avanzar(800);
comprobar('el retroceso sostenido también muestra el primer nivel con su dirección',
  doc.getElementById('scrub-time').textContent.indexOf('« 3×') >= 0);
soltarTecla(37);

prepararAvance(); app.state.playing.total = 150000; tv.posicion = 140;
bajarTecla(39); avanzar(6000); soltarTecla(39);
comprobar('el recorrido nunca supera el final utilizable del video', app.state.scrub === 148000 && !app.state.scrubHold);
prepararAvance(); tv.posicion = 3; bajarTecla(37); avanzar(6000); soltarTecla(37);
comprobar('el retroceso nunca baja de cero', app.state.scrub === 0 && !app.state.scrubHold);

prepararAvance(); bajarTecla(39); avanzar(3000); tecla(10009); avanzar(2000);
comprobar('Volver cancela el recorrido sin saltar ni detener la película',
  !app.state.scrubHold && app.state.zone === 'none' && saltosAvance().length === 0 && tv.posicion === 120);
prepararAvance(); bajarTecla(39); avanzar(900);
doc.hidden = true; (doc.oyentes.visibilitychange || []).forEach(function (fn) { fn(); }); doc.hidden = false;
var destinoOculto = app.state.scrub; avanzar(3000);
comprobar('ocultar la aplicación detiene la aceleración incluso si no llega keyup',
  !app.state.scrubHold && app.state.scrub === destinoOculto);
prepararAvance(); bajarTecla(39); avanzar(900); app.state.generacion++; app.state.scrub = 2000; avanzar(1500);
comprobar('un temporizador de la reproducción anterior no modifica la nueva', !app.state.scrubHold && app.state.scrub === 2000);

prepararAvance(); app.state.playing.live = true; tecla(417); avanzar(1000);
comprobar('la aceleración no se activa en televisión en directo', !app.state.scrubHold && app.state.zone === 'none');
prepararAvance(); app.state.salto = { target: 300000 }; tecla(417); avanzar(1000);
comprobar('un salto pendiente bloquea el inicio de otro recorrido', !app.state.scrubHold && saltosAvance().length === 0);
prepararAvance(); video.paused = true; bajarTecla(417); avanzar(3000); soltarTecla(417);
comprobar('la tecla multimedia de avance utiliza la aceleración y conserva la pausa', app.state.scrub > 130000 && video.paused);
tecla(13);
comprobar('confirmar un avance iniciado en pausa mantiene esa intención durante la preparación',
  saltosAvance().length === 1 && app.state.salto.paused);
prepararAvance(); tecla(412);
comprobar('la tecla multimedia de retroceso también funciona desde el reproductor', app.state.scrub === 110000);

console.log('\nPreparación y reloj de subtítulos\n');
prepararAvance();
app.state.playing.trace = { id: 'caption-attempt-00001', sequence: 0, started: ahora, closed: false };
app.state.subs.serverPending = false; app.state.subs.disabled = false; app.state.subs.render = null;
var captionKey = 'c'.repeat(32), captionToken = 'd'.repeat(40);
servidor.responder = function (url) {
  if (url.indexOf('.vtt') >= 0) return { status: 200, text:
    'WEBVTT\n\n00:02:00.150 --> 00:02:00.400\nUno\n\n00:02:00.450 --> 00:02:10.000\nDos\n' };
  return { status: 200, body: { accepted: true } };
};
app.loadSubtitles([{key:captionKey, sync_token:captionToken, url:'http://equipo/prepared.vtt',
  verified:true, validation_required:true, verification:{revision:4}}],
  {subtitle_key:captionKey,subtitle_sync_token:'e'.repeat(40),subtitle_verified:true});
avanzar(100);
comprobar('la pista aprobada para el archivo/audio prevalece sobre un token antiguo guardado',
  app.state.subs.list[app.state.subs.track].sync_token === captionToken);
tv.posicion = 120.21; ahora += 50; subtitlePrecisionTick.fn();
comprobar('el reloj de 50 ms presenta texto aunque Samsung no emita timeupdate',
  doc.getElementById('subtitle').textContent === 'Uno');
var captionEvents = function () { return servidor.llamadas.filter(function (call) {
  return call.url.indexOf('playback/event') >= 0 && call.cuerpo.event === 'subtitle_render';
}); };
comprobar('la primera entrada mide demora contra el inicio real del cue',
  captionEvents().length === 1 && captionEvents()[0].cuerpo.render_lag_ms === 60 &&
  captionEvents()[0].cuerpo.render_context === 'start');
tv.posicion = 120.51; ahora += 50; subtitlePrecisionTick.fn();
comprobar('el mismo reloj retira la entrada anterior y muestra la siguiente',
  doc.getElementById('subtitle').textContent === 'Dos');
video.paused = true; video.disparar('pause'); avanzar(100);
comprobar('las entradas continuas se agregan y se envían al pausar sin guardar diálogos',
  captionEvents().length === 2 && captionEvents()[1].cuerpo.render_context === 'continuous' &&
  captionEvents()[1].cuerpo.render_samples === 1 && JSON.stringify(captionEvents()).indexOf('Dos') < 0);
var measured = captionEvents().length;
ahora += 10000; subtitlePrecisionTick.fn();
comprobar('una pausa no avanza el texto ni inventa otra medición',
  doc.getElementById('subtitle').textContent === 'Dos' && captionEvents().length === measured);
video.disparar('seeking'); app.state.salto = {target:127000}; subtitlePrecisionTick.fn();
comprobar('un salto pendiente oculta el texto de la posición anterior', doc.getElementById('subtitle').textContent === '');
app.state.salto = null; tv.posicion = 127; ahora += 50; video.disparar('seeked'); avanzar(100);
var afterSeek = captionEvents()[captionEvents().length - 1].cuerpo;
comprobar('retomar dentro de una frase mide activación y no los segundos ya transcurridos de esa frase',
  afterSeek.render_context === 'seek' && afterSeek.render_lag_ms === 50 && doc.getElementById('subtitle').textContent === 'Dos');
app.state.playing.posicionSinConfirmar = true; subtitlePrecisionTick.fn();
comprobar('un destino sin confirmar tampoco muestra una frase del reloj anterior', doc.getElementById('subtitle').textContent === '');
app.state.playing.posicionSinConfirmar = false;
app.state.playing.arrancando = true; app.state.subs.render.context = 'resume'; app.state.subs.render.first = true;
video.disparar('seeking');
comprobar('el seek nativo del arranque conserva la identificación de reanudar', app.state.subs.render.context === 'resume');
app.state.playing.arrancando = false;

prepararAvance(); app.state.subs.serverPending = true; app.state.subs.disabled = false;
app.state.buscandoSubs = false; var pendingCaptionRequests = 0, beforeCaptionLoads = tv.cargas.length;
servidor.responder = function (url) {
  if (url.indexOf('playback/subtitles/') >= 0) {
    pendingCaptionRequests++;
    return {status:200,body:pendingCaptionRequests === 1 ? {items:[],pending:true} :
      {items:[{key:captionKey,sync_token:captionToken,url:'http://equipo/ready.vtt',verified:true,
        validation_required:true,verification:{revision:4}}],pending:false}};
  }
  if (url.indexOf('.vtt') >= 0) return {status:200,text:'WEBVTT\n\n00:01:59.000 --> 00:02:10.000\nPreparado\n'};
  return {status:200,body:{accepted:true}};
};
app.loadSubtitles([{key:captionKey,source_id:'raw-caption',validation_required:true,verified:false}]);
avanzar(2500);
comprobar('una preparación pendiente se consulta hasta recibir la pista aprobada',
  pendingCaptionRequests === 2 && !app.state.subs.serverPending && doc.getElementById('subtitle').textContent === 'Preparado');
comprobar('esperar el trabajo compartido no duplica la sincronización ni recarga el video',
  tv.cargas.length === beforeCaptionLoads && servidor.llamadas.filter(function (call) {
    return call.url.indexOf('playback/subtitles/sync') >= 0; }).length === 0);

prepararAvance(); app.state.audio.track = 1; app.state.subs.serverPending = false;
app.state.playing.trace = {id:'caption-audio-00001',sequence:0,started:ahora,closed:false};
var audioCaptionToken = 'f'.repeat(40);
servidor.responder = function (url, data) {
  if (url.indexOf('playback/cast') >= 0) return {status:200,body:{stream_url:'http://equipo/audio.m3u8',
    offset_ms:data.position_ms,duration_ms:7200000,seekable_ms:600000,audio_stream_index:2,
    audio_tracks:[{stream_index:1},{stream_index:2,selected:true}],subtitles_pending:false,
    subtitles:[{key:captionKey,sync_token:audioCaptionToken,url:'http://equipo/audio.vtt',verified:true,
      validation_required:true,verification:{revision:4}}]}};
  if (url.indexOf('.vtt') >= 0) return {status:200,text:'WEBVTT\n\n00:01:59.000 --> 00:02:10.000\nAudio nuevo\n'};
  return {status:200,body:{accepted:true}};
};
app.cambiarAudio(2); avanzar(200); video.disparar('seeking');
comprobar('cambiar audio instala la asociación aprobada para esa pista y conserva su contexto de medición',
  app.state.subs.list[app.state.subs.track].sync_token === audioCaptionToken && app.state.subs.render.context === 'audio');
comprobar('mientras el audio aún está cambiando no aparece texto de la lista anterior',
  doc.getElementById('subtitle').textContent === '');

prepararAvance(); app.state.subs.disabled = false; app.state.playing.video = 'tt-test:1:1';
app.state.playing.total = 200000; tv.posicion = 90; tv.corriendo = true;
app.state.item = {type:'series',episodes:[{id:'tt-test:1:1'},{id:'tt-test:1:2'}]};
app.tick(); app.tick();
comprobar('cerca del final anticipa subtítulos del siguiente episodio una sola vez',
  servidor.llamadas.filter(function (call) { return call.url.indexOf('subtitles/next') >= 0; }).length === 1);
comprobar('anticipar subtítulos no inicia ni convierte otro video',
  saltosAvance().length === 0 && servidor.llamadas.filter(function (call) { return call.url.indexOf('playback/start') >= 0; }).length === 0);

// ---------------------------------------- búsqueda finita y recuperación explícita
prepararAvance(); app.state.subs.serverPending = true; app.state.subs.disabled = false;
app.state.subs.pollDeadline = 0; app.state.buscandoSubs = false;
var failedSearchRequests = 0, retrySearchRequests = 0, retryStatusRequests = 0;
var loadsBeforeRetry = tv.cargas.length;
servidor.responder = function (url, body, method) {
  if (url.indexOf('playback/subtitles/') >= 0 && url.slice(-6) === '/retry') {
    retrySearchRequests++;
    return {status:200,body:{items:[],pending:true,state:'retrying',message:'Buscando otras alternativas…'}};
  }
  if (url.indexOf('playback/subtitles/') >= 0) {
    if (!retrySearchRequests) {
      failedSearchRequests++;
      return {status:200,body:{items:[],pending:false,state:'failed',reason:'audio_decode_failed',
        message:'No se pudo confirmar una pista. Puedes volver a buscar.'}};
    }
    retryStatusRequests++;
    return {status:200,body:{items:[{key:captionKey,sync_token:captionToken,url:'http://equipo/retry.vtt',
      verified:true,validation_required:true,verification:{revision:4}}],pending:false,state:'ready'}};
  }
  if (url.indexOf('.vtt') >= 0) return {status:200,text:'WEBVTT\n\n00:01:59.000 --> 00:02:10.000\nAlternativa\n'};
  return {status:200,body:{accepted:true}};
};
app.loadSubtitles([],null,{subtitle_state:'searching'});app.prepararSubtitulos();avanzar(15000);
comprobar('un fallo final detiene las consultas y conserva el motivo sin candidatas eternas',
  failedSearchRequests === 1 && !app.state.subs.serverPending && app.state.subs.preparationState === 'failed' &&
  app.state.subs.status.indexOf('volver a buscar') >= 0 && !app.state.subs.list.length);
app.state.menuTipo = 'subs';
app.state.menu = app.menuOptions().findIndex(function (option) { return option.buscar; });app.chooseMenu();avanzar(1600);
comprobar('Volver a buscar inicia una búsqueda nueva y adopta la alternativa confirmada',
  retrySearchRequests === 1 && retryStatusRequests === 1 && app.state.subs.preparationState === 'ready' &&
  doc.getElementById('subtitle').textContent === 'Alternativa',
  JSON.stringify({retry:retrySearchRequests,polls:retryStatusRequests,state:app.state.subs.preparationState,
    text:doc.getElementById('subtitle').textContent,disabled:app.state.subs.disabled}));
comprobar('la búsqueda alternativa no recarga el video ni duplica una calibración',
  tv.cargas.length === loadsBeforeRetry && servidor.llamadas.filter(function (call) {
    return call.url.indexOf('playback/subtitles/sync') >= 0; }).length === 0);

prepararAvance();app.state.subs.serverPending = true;app.state.subs.pollDeadline = 0;
app.state.subs.disabled = false;app.state.buscandoSubs = false;var foreverPolls = 0;
servidor.responder = function (url) {
  if (url.indexOf('playback/subtitles/') >= 0) {foreverPolls++;return {status:200,body:{items:[],pending:true,state:'searching'}};}
  return {status:200,body:{accepted:true}};
};
app.loadSubtitles([],null,{subtitle_state:'searching'});app.prepararSubtitulos();avanzar(18000);
var stoppedPolls = foreverPolls;avanzar(15000);
comprobar('un servidor que sigue pendiente también tiene un límite total visible en la TV',
  foreverPolls === stoppedPolls && foreverPolls <= 24 && !app.state.subs.serverPending &&
  app.state.subs.preparationState === 'failed');

prepararAvance();app.state.subs.serverPending = false;
servidor.responder = function (url) {return url.indexOf('.vtt') >= 0 ?
  {status:200,text:'WEBVTT\n\n00:01:59.000 --> 00:02:10.000\nAprobado\n'} : {status:200,body:{accepted:true}};};
app.loadSubtitles([{key:captionKey,sync_token:captionToken,url:'http://equipo/approved.vtt',verified:true,
  validation_required:true,verification:{revision:4}},
  {key:captionKey,source_id:'same-unchecked',verified:false,validation_required:true}],
  {subtitle_key:captionKey}, {subtitle_state:'ready'});avanzar(100);
comprobar('restaurar una preferencia elige la copia aprobada aunque la candidata tenga la misma clave',
  app.state.subs.track === 0 && doc.getElementById('subtitle').textContent === 'Aprobado');

prepararAvance();app.state.audio.track = 1;app.state.buscandoSubs = false;
servidor.responder = function (url) {
  if (url.indexOf('playback/subtitles/') >= 0) return {status:200,delay:800,body:{state:'ready',pending:false,
    items:[{key:'old-audio',url:'http://equipo/old.vtt',verified:true}]}};
  return {status:200,body:{accepted:true}};
};
app.pedirSubtitulos(false);app.state.audio.track = 2;
app.loadSubtitles([{key:'new-audio',url:'http://equipo/new.vtt',verified:true}],null,{subtitle_state:'ready'});avanzar(1000);
comprobar('una búsqueda tardía del audio anterior no sustituye la pista del audio actual',
  app.state.subs.list[0].key === 'new-audio');

// Las películas con VAD inconcluso conservan opciones completas para elección manual.
prepararAvance(); app.state.subs.serverPending = false; app.state.subs.manual = false;
app.state.audio.track = 1; servidor.llamadas = [];
var manualMovie = {key:'serpico-full',source_id:'full-movie-caption',
  url:'http://equipo/full.vtt?manual=true',label:'Español · OpenSubtitles',
  validation_required:true,verified:false,manual_only:true};
servidor.responder = function (url) {
  if (url.indexOf('.vtt') >= 0) return {status:200,text:'WEBVTT\n\n00:01:59.000 --> 00:02:10.000\nElección manual\n'};
  return {status:200,body:{accepted:true}};
};
app.loadSubtitles([],null,{subtitle_state:'available',subtitle_alternatives:[manualMovie]});
avanzar(100);
comprobar('una película ofrece alternativas aunque la sincronía no pueda comprobarse',
  app.state.subs.list.length === 1 && app.state.subs.track === -1);
comprobar('la alternativa sin comprobar no se descarga ni activa automáticamente',
  app.state.subs.cues.length === 0 && !servidor.llamadas.some(function (call) { return call.url.indexOf('.vtt') >= 0; }));
app.abrirSubtitulos();
comprobar('el menú distingue la sincronía sin comprobar', app.menuOptions()[0].label.indexOf('sincronía sin comprobar') >= 0);
app.state.menu = 0; var beforeManualMovieLoads = tv.cargas.length;
app.chooseMenu(); avanzar(100);
comprobar('elegir una alternativa permite leer el original sin abrir otra calibración',
  app.state.subs.cues.length === 1 && servidor.llamadas.some(function (call) { return call.url.indexOf('?manual=true') >= 0; }) &&
  !servidor.llamadas.some(function (call) { return call.url.indexOf('/subtitles/sync') >= 0; }));
app.loadSubtitles([],null,{subtitle_state:'available',subtitle_alternatives:[manualMovie]});
comprobar('el resultado final de la búsqueda conserva la pista elegida y el video en curso',
  app.state.subs.track === 0 && app.state.subs.cues.length === 1 && tv.cargas.length === beforeManualMovieLoads);
servidor.responder = function (url) {
  if (url.indexOf('/subtitles/sync') >= 0)return {status:200,body:{changed:false,verified:false,reason:'audio_read_timeout'}};
  return {status:200,body:{accepted:true}};
};
app.sincronizarSubtitulo(false); avanzar(100);
comprobar('una calibración inconclusa conserva la pista que el usuario eligió manualmente', app.state.subs.cues.length === 1);
var manualDownloads = servidor.llamadas.filter(function (call) { return call.url.indexOf('?manual=true') >= 0; }).length;
app.state.audio.track = 2; app.fetchCues(0); avanzar(100);
comprobar('cambiar audio invalida el permiso de activar una alternativa manual',
  !app.state.subs.cues.length && servidor.llamadas.filter(function (call) { return call.url.indexOf('?manual=true') >= 0; }).length === manualDownloads);
app.state.audio.track = 1; app.state.playing.source = 'another-movie-edition';
app.fetchCues(0); avanzar(100);
comprobar('cambiar edición tampoco reutiliza la elección manual anterior',
  !app.state.subs.cues.length && servidor.llamadas.filter(function (call) { return call.url.indexOf('?manual=true') >= 0; }).length === manualDownloads);
app.state.subs.manual = false;
app.loadSubtitles([],{subtitle_key:manualMovie.key,subtitle_manual:true},
  {subtitle_state:'available',subtitle_alternatives:[manualMovie]});
comprobar('una preferencia antigua no activa una alternativa sin comprobar', app.state.subs.track === -1);

// Inglés real: la primera alternativa admitida se usa sin abrir el selector.
function prepararSubtituloPorDefecto(language) {
  prepararAvance();
  app.state.audio.track = 1; app.state.audio.inspected = true;
  app.state.audio.list = [{stream_index:1,language:language,selected:true}];
  app.state.subs.list = []; app.state.subs.track = -1; app.state.subs.cues = [];
  app.state.subs.manual = false; app.state.subs.disabled = false; app.state.subs.restore = null;
  app.state.subs.serverPending = false; app.state.buscandoSubs = false;
  servidor.responder = function (url) {
    if (url.indexOf('.vtt') >= 0) return {status:200,text:'WEBVTT\n\n00:01:59.000 --> 00:02:10.000\n' +
      (url.indexOf('second') >= 0 ? 'Segunda' : 'Recomendada') + '\n'};
    return {status:200,body:{accepted:true}};
  };
}
var secondManualMovie = {key:'movie-second',source_id:'second-caption',
  url:'http://equipo/second.vtt?manual=true',label:'Español · alternativa 2',
  validation_required:true,verified:false,manual_only:true};
var recommendedPreparation = {subtitle_state:'available',subtitle_alternatives:[manualMovie,secondManualMovie]};
['eng','en','EN-us','en_GB'].forEach(function (language) {
  prepararSubtituloPorDefecto(language);
  var loads = tv.cargas.length;
  app.loadSubtitles([],null,recommendedPreparation); avanzar(100);
  comprobar('audio ' + language + ' activa la primera recomendada conservando su condición sin comprobar',
    app.state.subs.track === 0 && !app.state.subs.list[0].verified &&
    doc.getElementById('subtitle').textContent === 'Recomendada');
  comprobar('la selección por defecto no recarga el video ni abre otra calibración (' + language + ')',
    tv.cargas.length === loads && !servidor.llamadas.some(function (call) {
      return call.url.indexOf('playback/cast') >= 0 || call.url.indexOf('subtitles/sync') >= 0;
    }) && servidor.llamadas.filter(function (call) {
      return call.url.indexOf('full.vtt?manual=true') >= 0;
    }).length === 1);
});
['spa','und',''].forEach(function (language) {
  prepararSubtituloPorDefecto(language);
  app.loadSubtitles([],null,recommendedPreparation); avanzar(100);
  comprobar('audio ' + (language || 'sin identificar') + ' conserva la elección manual de alternativas',
    app.state.subs.track === -1 && !servidor.llamadas.some(function (call) {return call.url.indexOf('.vtt') >= 0;}));
});
prepararSubtituloPorDefecto('spa');
app.state.audio.list.push({stream_index:2,language:'eng'});
app.loadSubtitles([],null,recommendedPreparation); avanzar(100);
comprobar('tener una pista inglesa sin seleccionarla no activa el subtítulo',app.state.subs.track === -1);

prepararSubtituloPorDefecto('eng'); app.state.audio.track = -1;
app.state.audio.inspected = false; app.state.audio.list = [];
app.loadSubtitles([],null,recommendedPreparation); avanzar(100);
comprobar('si los subtítulos llegan antes que el audio, espera su idioma real',app.state.subs.track === -1);
app.loadAudioTracks([{stream_index:2,language:'eng',selected:true}],2,true); avanzar(100);
comprobar('el audio inglés que llega después activa la recomendada automáticamente',
  app.state.subs.track === 0 && doc.getElementById('subtitle').textContent === 'Recomendada');

prepararSubtituloPorDefecto('eng'); app.state.subs.serverPending = true;
app.loadSubtitles([],null,{subtitle_state:'searching',subtitle_alternatives:[manualMovie]}); avanzar(100);
comprobar('la búsqueda conserva el plazo de comprobación antes de elegir una alternativa',app.state.subs.track === -1);
app.state.subs.serverPending = false;
app.loadSubtitles([],null,recommendedPreparation); avanzar(100);
comprobar('al terminar la búsqueda activa la primera alternativa sin intervención',app.state.subs.track === 0);
app.state.menuTipo = 'subs'; app.state.menu = app.menuOptions().findIndex(function (option) {return option.pick === -1;});
app.chooseMenu();
var downloadsAfterOff = servidor.llamadas.filter(function (call) {return call.url.indexOf('.vtt') >= 0;}).length;
app.loadSubtitles([],{subtitle_disabled:true},recommendedPreparation); avanzar(100);
comprobar('Sin subtítulos sigue teniendo prioridad sobre la selección por defecto',
  app.state.subs.track === -1 && app.state.subs.disabled &&
  servidor.llamadas.filter(function (call) {return call.url.indexOf('.vtt') >= 0;}).length === downloadsAfterOff);

prepararSubtituloPorDefecto('eng');app.loadSubtitles([],null,recommendedPreparation);avanzar(100);
app.state.menuTipo = 'subs';app.state.menu = 1;app.chooseMenu();avanzar(100);
app.loadSubtitles([],null,recommendedPreparation);avanzar(100);
comprobar('una alternativa elegida por el usuario no vuelve a la primera al consultar el estado',
  app.state.subs.track === 1 && doc.getElementById('subtitle').textContent === 'Segunda');

prepararSubtituloPorDefecto('eng');app.state.playing.pendingSource = 'NEXT-EDITION';
app.loadSubtitles([],null,recommendedPreparation);avanzar(100);
comprobar('el idioma de la edición anterior no autoriza activar una pista mientras cambia la fuente',app.state.subs.track === -1);
prepararSubtituloPorDefecto('eng');
app.loadSubtitles([{key:'raw-candidate',url:'http://equipo/raw.vtt',validation_required:true,verified:false}],null,{subtitle_state:'failed'});
avanzar(100);
comprobar('la nueva preferencia no habilita candidatas crudas que no pasaron los filtros del servidor',
  !app.state.subs.cues.length && !servidor.llamadas.some(function (call) {return call.url.indexOf('.vtt') >= 0;}));

prepararSubtituloPorDefecto('spa');
servidor.responder = function (url) {
  if (url.indexOf('playback/sources/') >= 0) return {status:200,body:{items:[{source_id:'ENGLISH-START'}]}};
  if (url.indexOf('playback/start') >= 0) return {status:200,body:{session_id:'START-SESSION',position_ms:0,
    subtitles:[],subtitles_pending:false,subtitle_state:'available',subtitle_alternatives:[manualMovie]}};
  if (url.indexOf('playback/audio/') >= 0) return {status:200,body:{audio_stream_index:2,
    items:[{stream_index:2,language:'eng',selected:true}]}};
  if (url.indexOf('playback/cast') >= 0) return {status:200,body:{stream_url:'http://equipo/start.m3u8',
    offset_ms:0,duration_ms:7200000,seekable_ms:600000,audio_stream_index:2,
    audio_tracks:[{stream_index:2,language:'eng',selected:true}],subtitles:[],subtitles_pending:false,
    subtitle_state:'available',subtitle_alternatives:[manualMovie]}};
  if (url.indexOf('.vtt') >= 0) return {status:200,text:'WEBVTT\n\n00:00:00.000 --> 00:00:10.000\nInicio automático\n'};
  return {status:200,body:{accepted:true}};
};
// Incluye las transiciones existentes y los cinco segundos mínimos de elección.
app.playVideo('movie','tt-default-english','Película en inglés');avanzar(9000);
comprobar('el arranque completo activa la recomendada aunque audio y subtítulos lleguen antes que el video',
  app.state.playing.source === 'ENGLISH-START' && app.state.subs.track === 0 &&
  app.state.subs.manualAudio === 2 && app.state.subs.manualSource === 'ENGLISH-START' && app.state.subs.cues.length === 1,
  JSON.stringify({source:app.state.playing.source,pending:app.state.playing.pendingSource,audio:app.state.audio,
    track:app.state.subs.track,manualAudio:app.state.subs.manualAudio,manualSource:app.state.subs.manualSource,
    cues:app.state.subs.cues.length,serverPending:app.state.subs.serverPending,list:app.state.subs.list,
    carga:app.state.carga.etapa,requests:servidor.llamadas.map(function(call){return call.url;})}));

console.log('\nBarra de progreso y lista de episodios\n');
prepararAvance(); tv.posicion = 0;
app.state.scrub = 5616000; app.state.scrubMoved = false;
doc.getElementById('bar-fill').style.width = '78%';
doc.getElementById('bar-dot').style.left = '78%';
video.disparar('timeupdate');
comprobar('el inicio sustituye el 78 % anterior por cero sin esperar al temporizador',
  doc.getElementById('bar-fill').style.width === '0%' &&
  doc.getElementById('bar-dot').style.left === '0%' && doc.getElementById('elapsed').textContent === '0:00');
tecla(38); tv.posicion = 2;
app.state.ultimoT = 2; app.state.avanceEn = ahora; app.tick();
comprobar('enfocar la barra sin moverla sigue mostrando el avance real del video',
  app.state.zone === 'bar' && doc.getElementById('elapsed').textContent === '0:02' &&
  Math.abs(parseFloat(doc.getElementById('bar-fill').style.width) - 2 / 72) < 0.0001 &&
  doc.getElementById('scrub-time').textContent === '0:02');
tecla(39);
comprobar('el primer salto después de esperar con foco parte del minuto real', app.state.scrub === 12000);
tv.posicion = 3; video.disparar('timeupdate');
comprobar('mover el cursor conserva la previsualización sin adelantar el video',
  doc.getElementById('elapsed').textContent === '0:12' && tv.posicion === 3 && saltosAvance().length === 0);
tecla(10009); video.disparar('timeupdate');
comprobar('cancelar la previsualización restaura de inmediato la posición reproducida',
  doc.getElementById('elapsed').textContent === '0:03' && app.state.zone === 'none');
tecla(38); tv.posicion = 4; tecla(13);
comprobar('OK después de enfocar sin explorar no rebobina al punto anterior',
  tv.posicion === 4 && !app.state.salto && saltosAvance().length === 0);
app.state.playing.offset = 1200000; tv.posicion = 2; video.disparar('timeupdate');
comprobar('el avance tras retomar suma el desplazamiento de la lista una sola vez',
  doc.getElementById('elapsed').textContent === '20:02' &&
  Math.abs(parseFloat(doc.getElementById('bar-dot').style.left) - 1202 / 72) < 0.0001);
app.state.playing.offset = 0; app.state.playing.total = 0;
tv.posicion = 12; video.duration = 200; video.disparar('durationchange');
comprobar('una duración recién conocida actualiza el porcentaje sin conservar el dibujo anterior',
  doc.getElementById('bar-fill').style.width === '6%' && doc.getElementById('total').textContent === '3:20');
video.duration = 7200;

prepararAvance();
app.state.item = { name: 'Serie', episodes: [
  { id: 'serie:1:1', season: 1, episode: 1, name: 'Primero' },
  { id: 'serie:2:1', season: 2, episode: 1, name: 'Segundo' }
] };
app.state.playing.video = 'serie:1:1';
app.state.botones = app.botonesReproductor();
var botonEpisodios = app.state.botones.map(function (b) { return b.id; }).indexOf('eps');
app.activarBoton(botonEpisodios);
comprobar('abrir las temporadas oculta los controles que tapaban los capítulos',
  app.state.zone === 'seasons' && !doc.getElementById('player-strip').hidden && doc.getElementById('controls').hidden);
app.state.ultimoT = tv.posicion; app.state.avanceEn = ahora; app.tick();
comprobar('el temporizador no vuelve a superponer la barra sobre las temporadas', doc.getElementById('controls').hidden);
tecla(40); video.disparar('pause');
comprobar('recorrer capítulos y actualizar la reproducción mantiene la lista despejada',
  app.state.zone === 'episodes' && !doc.getElementById('player-strip').hidden && doc.getElementById('controls').hidden);
tecla(38); tecla(38);
comprobar('volver a los botones restaura los controles y cierra la lista',
  app.state.zone === 'buttons' && doc.getElementById('player-strip').hidden && !doc.getElementById('controls').hidden);
app.state.item.episodes.pop(); app.activarBoton(botonEpisodios); app.tick();
comprobar('una serie con una sola temporada también muestra los capítulos sin superposición',
  app.state.zone === 'episodes' && !doc.getElementById('player-strip').hidden && doc.getElementById('controls').hidden);
tecla(10009);
comprobar('Volver desde la lista cierra la selección sin detener el video',
  app.state.zone === 'none' && doc.getElementById('player-strip').hidden && app.state.playing.video === 'serie:1:1');

console.log('\n' + (hechas - fallos) + ' de ' + hechas + ' comprobaciones en verde\n');
process.exit(fallos ? 1 : 0);
