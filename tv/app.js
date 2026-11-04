/* SergioFlix para televisor.
   ES5 a propósito: el navegador de un Samsung de 2017 (Tizen 3) no admite
   `let`, funciones flecha ni plantillas de cadena. Sin dependencias externas. */
(function () {
  'use strict';

  var KEY_LEFT = 37, KEY_UP = 38, KEY_RIGHT = 39, KEY_DOWN = 40, KEY_OK = 13;
  var KEY_BACK = [10009, 8, 27, 461];           // 10009 y 461 son «volver» en Tizen.
  var KEY_PLAY = [415, 10252, 179], KEY_PAUSE = [19, 10252];
  // Samsung puede reservar esta tecla para su asistente. Se registra y escucha como
  // acceso preferente; el botón lateral cubre los modelos que no la entregan a la app.
  var KEY_VOICE = [10225];
  var STORE = 'sergioflix.key';
  var CAPTION_STORE = 'sergioflix.caption-style';
  var JOURNAL_STORE = 'sergioflix.playback-events', journalQueue = [], journalBusy = false;
  var CAPTION_STYLES = [
    { id: 'white-outline', label: 'Blanco con borde' },
    { id: 'yellow-outline', label: 'Amarillo con borde' },
    { id: 'black-background', label: 'Fondo negro' },
    { id: 'clean', label: 'Sin bordes' },
    { id: 'floating', label: 'Flotante' }
  ];
  var NAV = [
    { id: 'search', section: 'search' },
    { id: 'series', section: 'series' },
    { id: 'movie', section: 'movie' },
    { id: 'list', section: 'list' },
    { id: 'karaoke', section: 'karaoke' },
    { id: 'tv', section: 'tv' },
    { id: 'exit', section: 'exit' }
  ];
  var MORE_BATCH = 30;
  var DAILY_BATCH = 12, catalogDay = '';

  function diaCatalogo() {
    var now = new Date();
    return Math.floor(Date.UTC(now.getFullYear(), now.getMonth(), now.getDate()) / 86400000);
  }

  function seleccionDiaria(items) {
    // Una ventana distinta cada día; el resto sigue disponible en «Ver más».
    if (!items.length) return [];
    var offset = (diaCatalogo() * DAILY_BATCH) % items.length;
    return items.slice(offset).concat(items.slice(0, offset));
  }
  // Episodios encadenados solos antes de preguntar si alguien sigue mirando.
  var CHAIN_LIMIT = 3, PRESENCE_SECONDS = 60, NEXT_SECONDS = 10;
  /* Teclado en pantalla para el código: la One Remote de Samsung no trae números. */
  var TECLAS = ['1', '2', '3', '4', '5', '6', '7', '8', '9', '0', 'Borrar'];
  var COLUMNAS = 5;
  // La imagen grande espera a que el foco se asiente: cambiarla en cada pulsación
  // dejaba al televisor descargando carátulas que nadie llegaba a ver.
  var HERO_DELAY = 380;
  // Cuánto mueve el punto de la barra cada pulsación.
  var PASO_MS = 10000;
  // Tres niveles de exploración sobre una base de diez segundos por segundo.
  // Un reloj propio evita que la velocidad dependa de la repetición de cada mando.
  var AVANCE_ESPERA = 600, AVANCE_6X = 2500, AVANCE_10X = 5000, AVANCE_TICK = 100;
  /* Un salto se da por bueno cuando el reproductor queda a menos de esto del punto
     pedido. Con pasos de diez segundos, tres es holgado y no confunde el avance
     natural de la reproduccion con haber obedecido la orden. */
  var SALTO_TOLERANCIA = 3, SALTO_COMPROBACIONES = 4, SALTO_ESPERA = 500;
  // Vigilancia: la imagen congelada sin que nadie avise es el peor fallo posible.
  var ATASCO_AVISO = 4000, ATASCO_RESCATE = 14000, RESCATES_MAX = 2;
  // La API vence a los 130 s y el servidor a los 115 s: la UI nunca abandona antes.
  var ARRANQUE_MAX = 45000, SALTO_MAX = 135000;
  /* Vigilancia de la videoteca. Una comprobación perdida no prueba nada: viaja por
     el mismo wifi que la película, y con una película pasando es normal que alguna
     se quede atascada en la fila. Antes se preguntaba cada cinco segundos con tres
     y medio de paciencia, y bastaba un fallo para apagar la pantalla; el resultado
     era el cartel entrando y saliendo con todo funcionando. Ahora se pregunta poco,
     se espera mucho, y hacen falta varios fallos seguidos para decir nada. */
  var LATIDO_CADA = 15000, LATIDO_ESPERA = 10000, LATIDO_FRESCO = 10000;
  var FALLOS_AVISO = 3, FALLOS_GRAVES = 6, IMAGEN_PARADA = 30000;
  // Cinco segundos de elección más la transición de logro dan unos seis segundos
  // visibles. La preparación real sigue en paralelo y ese tiempo sale de «Cargando».
  var ELECCION_MINIMA = 5000;

  /* Iconos en SVG. Van incrustados porque una tipografía de iconos es un archivo más
     que puede no llegar a tiempo en un televisor. */
  var ICON = {
    play: '<svg class="icon" width="26" height="26" viewBox="0 0 24 24"><path d="M8 5v14l11-7z" fill="#e6e5ea"/></svg>',
    playWhite: '<svg class="icon" width="34" height="34" viewBox="0 0 24 24"><path d="M8 5v14l11-7z" fill="#fff"/></svg>',
    pauseWhite: '<svg class="icon" width="34" height="34" viewBox="0 0 24 24"><path d="M6 5h4v14H6zM14 5h4v14h-4z" fill="#fff"/></svg>',
    restart: '<svg class="icon" width="26" height="26" viewBox="0 0 24 24"><path d="M12 5V1L7 6l5 5V7a5 5 0 1 1-5 5H5a7 7 0 1 0 7-7z" fill="#e6e5ea"/></svg>',
    captions: '<svg class="icon" width="26" height="26" viewBox="0 0 24 24"><path d="M3 5h18a1 1 0 0 1 1 1v12a1 1 0 0 1-1 1H3a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1zm3 5v4h4v-1H7v-2h3v-1zm8 0v4h4v-1h-3v-2h3v-1z" fill="#e6e5ea"/></svg>',
    audio: '<svg class="icon" width="26" height="26" viewBox="0 0 24 24"><path d="M4 9v6h4l5 4V5L8 9H4zm11.5 3a4.5 4.5 0 0 0-2-3.74v7.48A4.5 4.5 0 0 0 15.5 12zm-2-8.71v2.08a7 7 0 0 1 0 13.26v2.08a9 9 0 0 0 0-17.42z" fill="#e6e5ea"/></svg>',
    episode: '<svg class="icon" width="26" height="26" viewBox="0 0 24 24"><path d="M4 6h16v12H4zM2 4v16h20V4zm7 4v8l6-4z" fill="#e6e5ea"/></svg>',
    plus: '<svg class="icon" width="26" height="26" viewBox="0 0 24 24"><path d="M11 5h2v6h6v2h-6v6h-2v-6H5v-2h6z" fill="#e6e5ea"/></svg>',
    tick: '<svg class="icon" width="26" height="26" viewBox="0 0 24 24"><path d="M9 16.2 4.8 12l-1.4 1.4L9 19 21 7l-1.4-1.4z" fill="#e6e5ea"/></svg>',
    home: '<svg class="icon" width="26" height="26" viewBox="0 0 24 24"><path d="M12 3 2 12h3v9h6v-6h2v6h6v-9h3z" fill="#e6e5ea"/></svg>',
    check: '<svg class="icon" width="24" height="24" viewBox="0 0 24 24"><path d="M9 16.2 4.8 12l-1.4 1.4L9 19 21 7l-1.4-1.4z" fill="#e53d49"/></svg>',
    version: '<svg class="icon" width="26" height="26" viewBox="0 0 24 24"><path d="M4 6h11v2H4zm0 5h11v2H4zm0 5h11v2H4zm14-9.5 4 4-4 4z" fill="#e6e5ea"/></svg>',
    back: '<svg class="icon" width="26" height="26" viewBox="0 0 24 24"><path d="M20 11H7.8l5.6-5.6L12 4l-8 8 8 8 1.4-1.4L7.8 13H20z" fill="#e6e5ea"/></svg>'
  };

  var el = function (id) { return document.getElementById(id); };
  var screens = ['brand', 'pair', 'home', 'detail', 'player'];
  var state = {
    screen: 'brand', key: '', code: '', pad: 0,
    rows: [], row: 0, column: 0, progress: {}, favorites: {},
    section: 'home', homeZone: 'rows', nav: 0,
    searchResults: [], searchIndex: 0, searchEditing: false, searchGeneration: 0, searchBusy: false,
    assistantOpen: false, assistantEditing: false, assistantBusy: false,
    assistantZone: 'input',
    assistantResults: [], assistantIndex: 0, assistantGeneration: 0,
    assistantStartMs: null, assistantApproximate: false,
    assistantOriginZone: 'rows', assistantOriginSearchEditing: false, assistantResume: false,
    connectionLost: false, connectionChecking: false, lastServerResponse: 0,
    connectionFails: 0, connectionMini: false, videoVivoEn: 0, brandReturn: '',
    item: null, detailOrigin: 'rows', detailGeneration: 0,
    dzone: 'acciones', daction: 0, depisode: 0, dseason: 0,
    dacciones: [], resumePrompt: null, resumeChoice: 0,
    playing: null, sources: [], attempt: 0, dead: false,
    carga: { activa: false, transicion: false, logro: null,
             cola: [], version: 0, etapa: '', minimoHasta: 0, iniciarVideo: null },
    salto: null, saltoId: 0, posicionamiento: null, generacion: 0, ultimoT: -1, avanceEn: 0, esperando: false,
    notaBase: '', fin: [], finBoton: 0, pidiendo: 0,
    items: [], menuTipo: 'subs', buscandoSubs: false,
    controlsUntil: 0, zone: 'none', scrub: 0, scrubMoved: false, scrubHold: null, button: 0, episode: 0, pseason: 0, botones: [],
    chained: 0, notice: null, noticeTimer: null, heroTimer: null, heroId: '',
    subs: { list: [], cues: [], track: -1, sync: 0, status: '', syncing: false,
            requestId: 0, syncRequestId: 0, manualRevision: 0, autoTimer: null, autoAttempts: {},
            style: 'white-outline', manual: false, disabled: false, restore: null },
    audio: { list: [], track: -1, status: '', loading: false, switching: false,
             inspected: false, error: false }, menu: -1
  };

  function show(name) {
    detenerAvance();
    state.screen = name;
    for (var i = 0; i < screens.length; i++) el(screens[i]).hidden = screens[i] !== name;
    if (name !== 'home') ocultarGPT();
  }

  /** La pantalla de marca también necesita una salida cuando una carga termina en error. */
  function brandLoading(texto, volverA) {
    state.brandReturn = volverA || '';
    el('brand-spinner').hidden = false;
    el('brand-back').hidden = true;
    el('brand-back').textContent = 'Volver';
    el('brand-note').textContent = texto;
    show('brand');
  }

  function brandFailure(texto) {
    if (!state.brandReturn) state.brandReturn = 'exit';
    el('brand-note').textContent = texto;
    el('brand-spinner').hidden = true;
    el('brand-back').textContent = state.brandReturn === 'exit' ? 'Salir' : 'Volver';
    el('brand-back').hidden = false;
  }

  function volverDesdeBrand() {
    if (!state.brandReturn) return;
    var destino = state.brandReturn;
    state.brandReturn = '';
    state.detailGeneration++;
    el('brand-back').hidden = true;
    if (destino === 'exit') { salir(); return; }
    if (destino === 'detail' && state.item) { renderDetail(); show('detail'); return; }
    if (destino === 'assistant' || destino === 'search') { volverAlOrigen(); return; }
    show('home'); focusCard();
  }

  function renderNav() {
    for (var i = 0; i < NAV.length; i++) {
      var item = el('nav-' + NAV[i].id);
      if (!item) continue;
      item.className = 'nav-item' + (NAV[i].id === 'exit' ? ' exit' : '') +
        (state.section === NAV[i].section ? ' active' : '') +
        (state.homeZone === 'nav' && state.nav === i ? ' focus' : '');
    }
  }

  function mostrarCatalogo() {
    el('hero').hidden = false;
    el('rows-view').hidden = false;
    el('search-panel').hidden = true;
    renderNav();
  }

  function salir() {
    // La salida normal de Tizen también revoca lo que pudiera seguir preparándose.
    if (state.playing) cerrarIntento(true);
    try {
      if (window.tizen && window.tizen.application) {
        window.tizen.application.getCurrentApplication().exit();
        return;
      }
    } catch (error) { /* el navegador de prueba no tiene aplicación Tizen */ }
  }

  function activarNav(indice) {
    var item = NAV[indice];
    if (!item) return;
    state.nav = indice;
    if (item.id === 'search') { abrirBusqueda(); return; }
    if (item.id === 'series' || item.id === 'movie') { cargarSeccion(item.id); return; }
    if (item.id === 'list') { cargarMiLista(); return; }
    if (item.id === 'karaoke') { cargarKaraoke(); return; }
    if (item.id === 'tv') { cargarTelevision(); return; }
    if (item.id === 'exit') salir();
  }

  function prepararNav() {
    for (var i = 0; i < NAV.length; i++) enlazarNav(i);
    var marca = el('brand-home');
    marca.onclick = function () { loadHome(); };
    marca.onmouseover = function () {
      state.homeZone = 'nav';
      state.nav = 0;
      renderNav();
    };
  }

  function enlazarNav(indice) {
    var item = el('nav-' + NAV[indice].id);
    item.style.cursor = 'pointer';
    item.onmouseover = function () {
      if (state.section === 'search') limpiarFocoBusqueda();
      state.homeZone = 'nav'; state.nav = indice; renderNav();
    };
    item.onclick = function () {
      if (state.section === 'search') limpiarFocoBusqueda();
      state.homeZone = 'nav'; state.nav = indice; renderNav(); activarNav(indice);
    };
  }

  function api(path, method, body, done, fail) {
    var request = new XMLHttpRequest(), finished = false, slowCheck = null;
    request.open(method || 'GET', '/api/' + path, true);
    request.setRequestHeader('X-API-Key', state.key);
    if (body) request.setRequestHeader('Content-Type', 'application/json');
    request.timeout = path === 'playback/subtitles/sync' ? 12000 :
      path.indexOf('playback/subtitles/') === 0 ? 3000 : 130000;
    function finish(networkError) {
      if (finished) return;
      finished = true;
      if (slowCheck) window.clearTimeout(slowCheck);
      var data = null;
      try { data = request.responseText ? JSON.parse(request.responseText) : {}; } catch (error) { data = {}; }
      if (request.status >= 200 && request.status < 300) {
        conexionViva(); done(data);
      }
      else if (fail) fail(data && data.message ? data.message : 'No se pudo conectar con tu videoteca.',
                          request.status, data && data.code ? data.code : '', data || {});
      if (networkError || request.status === 0) conexionFallo();
    }
    request.onreadystatechange = function () { if (request.readyState === 4) finish(false); };
    request.onerror = function () { finish(true); };
    request.onabort = function () { finish(true); };
    request.ontimeout = function () { finish(true); };
    // Una operación puede ser lenta sin que el PC esté caído: preparar una edición
    // tarda de verdad. Su salud se comprueba aparte y sin prisa, y esa comprobación
    // solo suma un fallo a la cuenta; nunca decide ella sola.
    slowCheck = window.setTimeout(function () { if (!finished) comprobarPC(); }, LATIDO_CADA);
    request.send(body ? JSON.stringify(body) : null);
    return request;
  }

  /* Diario independiente: IDs/huellas y cifras, sin URLs ni mensajes privados.
     Cola persistida y acotada para poder reenviar tras un corte de red. */
  function traceId() {
    return 'sf-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2) +
      '-' + Math.random().toString(36).slice(2);
  }

  function guardarDiario() {
    try { localStorage.setItem(JOURNAL_STORE, JSON.stringify(journalQueue)); } catch (error) { /* memoria disponible */ }
  }

  function enviarDiario() {
    if (journalBusy || !state.key || !journalQueue.length) return;
    var entry = journalQueue[0], request = new XMLHttpRequest(), finished = false;
    if (Date.now() - entry.at > 3 * 86400000) {
      journalQueue.shift(); guardarDiario(); enviarDiario(); return;
    }
    journalBusy = true;
    request.open('POST', '/api/playback/event', true);
    request.setRequestHeader('X-API-Key', state.key);
    request.setRequestHeader('Content-Type', 'application/json');
    request.timeout = 8000;
    function finish() {
      if (finished) return;
      finished = true; journalBusy = false;
      // Dar tiempo a registrar un inicio concurrente. Los intentos anteriores a
      // la actualización no existen en el diario y no deben bloquear los nuevos.
      var missing = false;
      if (request.status === 404 && Date.now() - entry.at > 60000) {
        try { missing = JSON.parse(request.responseText).code === 'attempt_not_found'; } catch (error) { /* backend anterior */ }
      }
      if ((request.status >= 200 && request.status < 300) || request.status === 422 || missing) {
        if (journalQueue[0] === entry) journalQueue.shift();
        guardarDiario(); enviarDiario();
      }
    }
    request.onreadystatechange = function () { if (request.readyState === 4) finish(); };
    request.onerror = request.ontimeout = request.onabort = finish;
    try { request.send(JSON.stringify(entry.data)); } catch (error) { finish(); }
  }

  function registrarReproduccion(event, fields, trace) {
    trace = trace || (state.playing && state.playing.trace);
    if (!trace) return;
    var video = el('video'), bufferedMs = 0;
    try {
      for (var b = 0; b < video.buffered.length; b++) {
        if (video.buffered.start(b) <= video.currentTime && video.buffered.end(b) > video.currentTime) {
          bufferedMs = Math.round((video.buffered.end(b) - video.currentTime) * 1000); break;
        }
      }
    } catch (error) { /* motores sin TimeRanges */ }
    var payload = { attempt_id: trace.id, event_id: String(++trace.sequence), event: event,
                    stage: 'player', position_ms: Math.round((state.playing && state.playing.ultimaPosicion) || 0),
                    buffer_ms: Math.min(86400000, Math.max(0, bufferedMs)) };
    for (var key in fields) if (fields.hasOwnProperty(key) && fields[key] !== null && fields[key] !== undefined) payload[key] = fields[key];
    journalQueue.push({ at: Date.now(), data: payload });
    if (journalQueue.length > 200) journalQueue.shift();
    guardarDiario(); enviarDiario();
  }

  function registrarAtasco() {
    var play = state.playing, trace = play && play.trace;
    if (!trace || !trace.first || trace.buffering || state.salto || play.arrancando || el('video').paused) return;
    trace.buffering = Date.now(); trace.stableMs = 0; trace.stableSent = false;
    registrarReproduccion('buffering', {});
  }

  function terminarAtasco() {
    var trace = state.playing && state.playing.trace;
    if (!trace || !trace.buffering) return;
    registrarReproduccion('buffering_end', { duration_ms: Math.min(86400000, Date.now() - trace.buffering) });
    trace.buffering = 0;
  }

  /* Cada segundo de película que se ve salió de un fragmento que el equipo acaba de
     entregar: mientras la imagen corre, ninguna comprobación puede decir lo contrario.
     Y aun fallando de verdad, tapar la película con un cartel a pantalla completa es
     apagar lo único que estaba funcionando; para eso basta un aviso en una esquina. */
  function conexionViva() {
    state.lastServerResponse = Date.now();
    var habia = state.connectionFails;
    state.connectionFails = 0;
    if (habia || state.connectionLost || state.connectionMini) pintarConexion();
  }

  function conexionFallo() {
    state.connectionFails++;
    pintarConexion();
  }

  /** ¿Está la imagen corriendo de verdad ahora mismo? */
  function imagenViva() {
    if (state.screen !== 'player' || !state.playing) return false;
    if (el('video').paused) return false;
    return state.videoVivoEn > 0 && Date.now() - state.videoVivoEn < IMAGEN_PARADA;
  }

  /** La pantalla completa, solo cuando de verdad no queda nada que ver. */
  function conexionGrave() {
    if (state.connectionFails < FALLOS_AVISO) return false;
    if (state.screen !== 'player' || !state.playing) return true;
    if (imagenViva()) return false;
    // Con la imagen parada se espera un poco más: puede ser el propio televisor
    // rellenando su colchón, y para esa duda ya está el aviso pequeño.
    return state.connectionFails >= FALLOS_GRAVES;
  }

  function pintarConexion() {
    var grave = conexionGrave();
    // Si el video avanza, el colchón está cumpliendo su función: la pérdida de red
    // permanece silenciosa hasta que afecte realmente a la reproducción.
    var leve = !grave && !imagenViva() && state.connectionFails >= FALLOS_AVISO;
    state.connectionLost = grave;
    state.connectionMini = leve;
    el('connection-lost').hidden = !grave;
    el('connection-mini').hidden = !leve;
    if (grave) {
      el('connection-retry').className = 'connection-retry focus';
      if (!state.connectionChecking) el('connection-retry').textContent = 'Reintentar';
    }
  }

  function comprobarPC(done) {
    if (state.connectionChecking) return;
    state.connectionChecking = true;
    var check = new XMLHttpRequest(), settled = false, started = Date.now();
    function finish(ok) {
      if (settled) return;
      settled = true; state.connectionChecking = false;
      // Si mientras esta comprobación viajaba contestó cualquier otra petición, el
      // equipo está ahí y este mensajito solo se perdió por el camino: no es fallo.
      if (ok) conexionViva();
      else if (state.lastServerResponse <= started) conexionFallo();
      if (done) done(ok);
    }
    check.open('GET', '/health?tv=' + Date.now(), true);
    check.timeout = LATIDO_ESPERA;
    check.onreadystatechange = function () {
      if (check.readyState === 4) finish(check.status >= 200 && check.status < 300);
    };
    check.onerror = function () { finish(false); };
    check.ontimeout = function () { finish(false); };
    check.send(null);
  }

  function reintentarConexion() {
    el('connection-retry').textContent = 'Comprobando…';
    if (state.connectionChecking) return;
    comprobarPC(function (ok) {
      if (!ok) { el('connection-retry').textContent = 'Reintentar'; return; }
      // Recargar deja una sesión limpia, pero con una película puesta eso es perder
      // justo lo que se estaba viendo, y la sesión de reproducción nunca dejó de
      // valer: basta con quitar el cartel, y de eso ya se encargó `conexionViva`.
      if (state.screen === 'player' && state.playing) return;
      try { window.location.reload(); } catch (error) { loadHome(); }
    });
  }

  /* Igual que `api`, pero contando: mientras hay una petición de reproducción en
     vuelo nadie puede dar la fuente por muerta. Preparar una edición tarda de verdad
     —consultar proveedores, arrancar la sesión y convertir los primeros segundos—, y
     abandonarla a mitad para probar la siguiente deja dos preparaciones peleando y
     acaba fallando todo. Era exactamente eso lo que pasaba. */
  function pedir(path, method, body, done, fail) {
    var generacion = state.generacion;
    state.pidiendo++;
    // Una respuesta abandonada no descuenta las peticiones de la película nueva.
    var cerrar = function () {
      if (generacion === state.generacion && state.pidiendo > 0) state.pidiendo--;
    };
    api(path, method, body, function (data) { cerrar(); done(data); },
        function (mensaje, status, code) { cerrar(); if (fail) fail(mensaje, status, code); });
  }

  function escape(text) {
    return String(text === undefined || text === null ? '' : text)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  function inList(list, code) { for (var i = 0; i < list.length; i++) if (list[i] === code) return true; return false; }

  /* ------------------------------------------------------------ emparejar */

  function renderCode() {
    var host = el('digits');
    host.innerHTML = '';
    for (var i = 0; i < 8; i++) {
      var box = document.createElement('div');
      var value = state.code.charAt(i);
      box.className = 'digit' + (value ? ' filled' : '');
      box.textContent = value || '·';
      host.appendChild(box);
    }
    renderPad();
  }

  function renderPad() {
    var host = el('pad');
    if (!host) return;
    host.innerHTML = '';
    for (var i = 0; i < TECLAS.length; i++) {
      var tecla = document.createElement('div');
      tecla.className = 'keypad-key' + (TECLAS[i] === 'Borrar' ? ' wide' : '') + (i === state.pad ? ' focus' : '');
      tecla.textContent = TECLAS[i];
      conPunteroPad(tecla, i);
      host.appendChild(tecla);
    }
  }

  function conPunteroPad(tecla, indice) {
    tecla.style.cursor = 'pointer';
    tecla.onmouseover = function () { if (state.pad !== indice) { state.pad = indice; renderPad(); } };
    tecla.onclick = function () { state.pad = indice; pulsarPad(); };
  }

  function pulsarPad() {
    var elegida = TECLAS[state.pad];
    if (elegida === 'Borrar') { state.code = state.code.slice(0, -1); renderCode(); return; }
    digit(parseInt(elegida, 10));
  }

  function tryPair() {
    el('pair-error').textContent = 'Comprobando el código…';
    api('devices/pair', 'POST', { code: state.code }, function (data) {
      state.key = data.api_key;
      try { localStorage.setItem(STORE, data.api_key); } catch (error) { /* sin almacenamiento */ }
      el('pair-error').textContent = '';
      loadHome();
    }, function (message) {
      state.code = ''; renderCode();
      el('pair-error').textContent = message;
    });
  }

  function digit(number) {
    if (state.code.length >= 8) return;
    state.code += String(number);
    renderCode();
    if (state.code.length === 8) tryPair();
  }

  function forgetKey() {
    journalQueue = []; guardarDiario();
    try { localStorage.removeItem(STORE); } catch (error) { /* nada */ }
    state.key = ''; state.code = ''; renderCode(); show('pair');
  }

  /* --------------------------------------------------------------- inicio */

  function card(item, wide) {
    var identifier = item.id || item.source_id || '';
    return { id: identifier, title: item.name || '', meta: item.releaseInfo || '',
             art: item.poster || item.logo || '', wide: !!wide,
             seen: state.progress[identifier] || 0, raw: item };
  }

  function columnas(row) {
    return row ? row.items.length + (row.more ? 1 : 0) : 0;
  }

  function tipoDeFila(row) {
    for (var i = 0; row && i < row.items.length; i++) {
      var type = row.items[i].raw && row.items[i].raw.type;
      if (type === 'movie' || type === 'series') return type;
    }
    return '';
  }

  function loadHome() {
    catalogDay = diaCatalogo();
    state.section = 'home';
    state.homeZone = 'rows';
    brandLoading('Cargando tu videoteca…');
    var rows = [], pending = 6;
    function ready() {
      if (--pending > 0) return;
      var kept = [];
      for (var i = 0; i < rows.length; i++) if (rows[i] && rows[i].items.length) kept.push(rows[i]);
      state.rows = kept; state.row = 0; state.column = 0;
      if (!state.rows.length) { brandFailure('Tu videoteca no devolvió títulos. Comprueba el servicio.'); return; }
      renderHome(); show('home');
    }
    function collect(index, title, path, wide, moreType) {
      rows[index] = { title: title, items: [], wide: !!wide, more: null };
      api(path, 'GET', null, function (data) {
        var list = data.items || [], limit = moreType ? DAILY_BATCH : 30;
        if (moreType) list = seleccionDiaria(list);
        for (var i = 0; i < list.length && i < limit; i++) rows[index].items.push(card(list[i], wide));
        if (moreType && (list.length > limit || data.next_skip !== null)) {
          var buffered = [];
          for (var b = limit; b < list.length; b++) buffered.push(card(list[b], wide));
          rows[index].more = { type: moreType, skip: data.next_skip, buffered: buffered };
        }
        ready();
      }, function (message) { if (index === 0) el('brand-note').textContent = message; ready(); });
    }
    function shelves() {
      collect(0, 'Continuar viendo', 'library/continue');
      collect(1, 'Películas populares', 'catalog/movie?skip=0&daily=true', false, 'movie');
      collect(2, 'Series populares', 'catalog/series?skip=0&daily=true', false, 'series');
      collect(60, 'TV en directo', 'tv/channels?section=nacional', true);
      api('collections', 'GET', null, function (data) {
        var list = data.items || [];
        for (var s = 0; s < list.length && s < 40; s++) {
          var shelf = { title: list[s].title || '', items: [], wide: false, more: null };
          var titles = seleccionDiaria(list[s].items || []);
          for (var t = 0; t < titles.length && t < DAILY_BATCH; t++) shelf.items.push(card(titles[t], false));
          var shelfBuffered = [];
          for (var b = DAILY_BATCH; b < titles.length; b++) shelfBuffered.push(card(titles[b], false));
          var shelfType = list[s].media_type || tipoDeFila(shelf);
          if (shelfType === 'movie' || shelfType === 'series') {
            // Primero conserva los títulos editoriales que no cabían en la tanda inicial.
            shelf.more = { type: shelfType, skip: 0, buffered: shelfBuffered };
          }
          rows[10 + s] = shelf;
        }
        ready();
      }, function () { ready(); });
      // Mi lista, para saber qué títulos ya están guardados.
      api('library/favorites', 'GET', null, function (data) {
        var list = data.items || [];
        state.favorites = {};
        for (var f = 0; f < list.length; f++) state.favorites[list[f].id] = true;
        ready();
      }, function () { ready(); });
    }
    api('library/progress', 'GET', null, function (data) {
      state.progress = data.items || {}; shelves();
    }, function (message, status) {
      if (status === 401) { forgetKey(); return; }
      state.progress = {}; el('brand-note').textContent = message; shelves();
    });
  }

  function renderHome() {
    mostrarCatalogo();
    var host = el('rows'); host.innerHTML = '';
    for (var r = 0; r < state.rows.length; r++) {
      var row = state.rows[r];
      var block = document.createElement('div'); block.className = 'row' + (r === state.row ? ' active' : '');
      var heading = document.createElement('h2'); heading.textContent = row.title; block.appendChild(heading);
      var strip = document.createElement('div'); strip.className = 'strip';
      var inner = document.createElement('div'); inner.className = 'strip-inner'; inner.id = 'strip' + r;
      for (var c = 0; c < row.items.length; c++) {
        var item = row.items[c];
        var box = document.createElement('div');
        box.className = 'card' + (row.wide ? ' wide' : '');
        box.id = 'card' + r + '_' + c;
        var frame = document.createElement('div'); frame.className = 'art';
        var art = document.createElement('img');
        art.src = item.art || ''; art.alt = '';
        frame.appendChild(art);
        if (item.seen > 0) {
          var seen = document.createElement('i'); seen.className = 'seen';
          var fill = document.createElement('b');
          fill.style.width = Math.max(3, Math.min(100, item.seen * 100)) + '%';
          seen.appendChild(fill); frame.appendChild(seen);
        }
        var name = document.createElement('span'); name.textContent = item.title;
        var meta = document.createElement('small'); meta.textContent = item.meta;
        box.appendChild(frame); box.appendChild(name); box.appendChild(meta);
        conPuntero(box, r, c);
        inner.appendChild(box);
      }
      if (row.more) {
        var more = document.createElement('div');
        more.className = 'card more-card';
        more.id = 'card' + r + '_' + row.items.length;
        more.innerHTML = '<div class="art"><div class="more-plus">+</div>' +
          '<div class="more-copy">30 más</div></div><span>Ver más</span><small>' +
          escape(row.title) + '</small>';
        conPuntero(more, r, row.items.length);
        inner.appendChild(more);
      }
      strip.appendChild(inner); block.appendChild(strip); host.appendChild(block);
    }
    focusCard();
  }

  /* El navegador del televisor navega con puntero y no con foco: sin manejadores de
     clic, allí no hay forma de recorrer nada. */
  function conPuntero(caja, fila, columna) {
    caja.style.cursor = 'pointer';
    caja.onmouseover = function () {
      if (state.row === fila && state.column === columna) return;
      state.row = fila; state.column = columna; focusCard();
    };
    caja.onclick = function () {
      state.row = fila; state.column = columna; focusCard();
      activarTarjeta();
    };
  }

  function activarTarjeta() {
    var row = state.rows[state.row];
    if (!row) return;
    if (state.column === row.items.length && row.more) { cargarMas(row); return; }
    if (row.items[state.column]) openItem(row.items[state.column]);
  }

  function tags(item) {
    var out = '';
    if (item.releaseInfo) out += '<span class="tag">' + escape(item.releaseInfo) + '</span>';
    if (item.imdbRating) out += '<span class="tag score">IMDb ' + escape(item.imdbRating) + '</span>';
    var genres = item.genres || [];
    for (var i = 0; i < genres.length && i < 3; i++) out += '<span class="tag">' + escape(genres[i]) + '</span>';
    return out;
  }

  function updateHero() {
    if (state.section === 'list' && !state.rows.length) return;
    var row = state.rows[state.row];
    var item = row && row.items[state.column];
    if (!item) {
      el('hero-title').textContent = 'Más de ' + (row ? row.title : 'esta categoría');
      el('hero-meta').innerHTML = '<span class="tag">30 sugerencias</span>';
      el('hero-desc').textContent = 'Pulsa OK para añadir más títulos a esta misma fila.';
      return;
    }
    var raw = item.raw;
    el('hero-title').textContent = raw.name || item.title || '';
    el('hero-meta').innerHTML = tags(raw);
    el('hero-desc').textContent = (raw.description || '').slice(0, 260);
    var wanted = raw.background || raw.poster || item.art || '';
    if (wanted === state.heroId) return;
    state.heroId = wanted;
    var art = el('hero-art');
    art.className = '';
    if (!wanted) { art.removeAttribute('src'); return; }
    art.onload = function () { if (state.heroId === wanted) art.className = 'shown'; };
    art.src = wanted;
  }

  function focusCard() {
    var previous = document.querySelector('.card.focus');
    if (previous) previous.className = previous.className.replace(' focus', '');
    var box = el('card' + state.row + '_' + state.column);
    if (!box) return;
    // Toda la fila activa crece, no solo la tarjeta puntual: así se reconoce de
    // inmediato el nivel vertical sobre el que responderán izquierda y derecha.
    var rowsHost = el('rows');
    for (var r = 0; r < rowsHost.children.length; r++) {
      rowsHost.children[r].className = 'row' + (r === state.row ? ' active' : '');
    }
    box.className += ' focus';
    // Desplazar con `transform` y no con scroll: es lo único que este televisor
    // mueve por hardware, y la diferencia entre deslizar y dar tirones es esa.
    var strip = el('strip' + state.row);
    // Dejar el foco a 94 px del borde evita que el escalado y el overscan de ambos
    // televisores recorten la tarjeta seleccionada.
    if (strip) {
      // Al recibir su primer transform, el carrusel pasa a ser offsetParent en Chromium.
      // Distinguir ambos casos evita sumar dos veces el margen de la fila.
      var relativeLeft = box.offsetLeft;
      if (box.offsetParent !== strip) relativeLeft -= strip.offsetLeft;
      strip.style.transform = 'translateX(' + (30 - relativeLeft) + 'px)';
    }
    var block = box.parentNode.parentNode.parentNode;
    el('rows').style.transform = 'translateY(' + (-block.offsetTop) + 'px)';
    if (state.heroTimer) window.clearTimeout(state.heroTimer);
    state.heroTimer = window.setTimeout(updateHero, HERO_DELAY);
  }

  function cargarSeccion(type) {
    catalogDay = diaCatalogo();
    var volverA = state.screen;
    state.section = type;
    state.homeZone = 'rows';
    brandLoading(type === 'movie' ? 'Cargando películas…' : 'Cargando series…', volverA);
    api('catalog/' + type + '?skip=0&daily=true', 'GET', null, function (data) {
      var list = seleccionDiaria(data.items || []);
      var row = { title: type === 'movie' ? 'Películas populares' : 'Series populares',
                  items: [], wide: false, more: null };
      for (var i = 0; i < list.length && i < DAILY_BATCH; i++) row.items.push(card(list[i], false));
      var buffered = [];
      for (var b = DAILY_BATCH; b < list.length; b++) buffered.push(card(list[b], false));
      if (buffered.length || data.next_skip !== null) {
        row.more = { type: type, skip: data.next_skip, buffered: buffered };
      }
      state.rows = [row]; state.row = 0; state.column = 0;
      renderHome(); show('home');
    }, function (message) { brandFailure(message); });
  }

  function cargarMiLista() {
    var volverA = state.screen;
    state.section = 'list';
    state.homeZone = 'rows';
    brandLoading('Cargando mi lista…', volverA);
    api('library/favorites', 'GET', null, function (data) {
      var list = data.items || [], row = { title: 'Mi lista', items: [], wide: false, more: null };
      state.favorites = {};
      // Todos los guardados, sin límite ni recomendaciones mezcladas.
      for (var i = 0; i < list.length; i++) {
        state.favorites[list[i].id] = true;
        row.items.push(card(list[i], false));
      }
      state.rows = list.length ? [row] : []; state.row = 0; state.column = 0;
      state.homeZone = list.length ? 'rows' : 'nav';
      renderHome(); show('home');
      if (!list.length) {
        if (state.heroTimer) window.clearTimeout(state.heroTimer);
        el('hero-title').textContent = 'Tu lista está vacía';
        el('hero-meta').innerHTML = '';
        el('hero-desc').textContent = 'Abre una película o serie y pulsa «Mi lista» para guardarla aquí.';
        state.heroId = ''; el('hero-art').removeAttribute('src');
      }
    }, function (message) { brandFailure(message); });
  }

  function cargarTelevision() {
    var volverA = state.screen;
    state.section = 'tv';
    state.homeZone = 'rows';
    brandLoading('Cargando televisión en directo…', volverA);
    var rows = [], favorites = [], pending = 3;
    function ready() {
      if (--pending > 0) return;
      favorites.sort(function (a, b) { return (a.raw.favorite_rank || 0) - (b.raw.favorite_rank || 0); });
      var kept = favorites.length ? [{ title: 'Favoritos', items: favorites, wide: true, more: null }] : [];
      for (var i = 0; i < rows.length; i++) if (rows[i] && rows[i].items.length) kept.push(rows[i]);
      if (!kept.length) { brandFailure('No se encontraron canales disponibles.'); return; }
      state.rows = kept; state.row = 0; state.column = 0;
      renderHome(); show('home');
    }
    function channels(index, title, section) {
      api('tv/channels?section=' + section, 'GET', null, function (data) {
        var list = data.items || [], row = { title: title, items: [], wide: true, more: null };
        for (var i = 0; i < list.length; i++) {
          var channel = card(list[i], true);
          row.items.push(channel);
          if (list[i].favorite) favorites.push(channel);
        }
        rows[index] = row; ready();
      }, ready);
    }
    // Deportes va justo después de Favoritos: TNT Sports, ESPN y DSports la encabezan
    // cuando el PC tiene una señal verificada de ellos.
    channels(0, 'Deportes', 'deportes');
    channels(1, 'TV nacional', 'nacional');
    channels(2, 'TV por cable', 'cable');
  }

  /* La carpeta del PC se proyecta como las demás colecciones: estanterías de
     carátulas verticales y una ficha de serie para cada artista. */
  function cargarKaraoke() {
    var volverA = state.screen;
    state.section = 'karaoke';
    state.homeZone = 'rows';
    brandLoading('Leyendo tus karaokes…', volverA);
    api('karaoke', 'GET', null, function (data) {
      var shelves = data.items || [], rows = [];
      for (var s = 0; s < shelves.length; s++) {
        var list = shelves[s].items || [];
        var row = { title: shelves[s].title || '', items: [], wide: false, more: null };
        for (var i = 0; i < list.length; i++) row.items.push(card(list[i], false));
        if (row.items.length) rows.push(row);
      }
      if (!rows.length) {
        brandFailure(data.root_available
          ? 'No encontré videos dentro de las carpetas de artistas.'
          : 'No encuentro la carpeta local de karaoke.');
        return;
      }
      state.rows = rows; state.row = 0; state.column = 0;
      renderHome(); show('home');
    }, function (message) { brandFailure(message); });
  }

  function abrirArtista(artist) {
    var volverA = state.screen;
    state.section = 'karaoke';
    brandLoading('Abriendo las canciones de ' + artist.name + '…', volverA);
    api('karaoke/artists/' + encodeURIComponent(artist.id), 'GET', null, function (data) {
      state.detailOrigin = 'rows';
      state.item = data;
      renderDetail(); show('detail');
    }, function (message) { brandFailure(message); });
  }

  function cargarMas(row) {
    if (!row || !row.more || row.more.loading) return;
    var more = row.more, before = row.items.length, added = [], known = {};
    for (var i = 0; i < row.items.length; i++) known[row.items[i].id] = true;
    function add(candidate) {
      if (!candidate || !candidate.id || known[candidate.id] || added.length >= MORE_BATCH) return;
      known[candidate.id] = true; added.push(candidate);
    }
    while (more.buffered.length && added.length < MORE_BATCH) add(more.buffered.shift());
    function finish() {
      more.loading = false;
      for (var a = 0; a < added.length; a++) row.items.push(added[a]);
      if (!more.buffered.length && more.skip === null) row.more = null;
      state.column = added.length ? before : Math.max(0, columnas(row) - 1);
      state.homeZone = 'rows';
      renderHome(); show('home');
    }
    if (added.length >= MORE_BATCH || more.skip === null) { finish(); return; }
    more.loading = true;
    var path = 'catalog/' + more.type + '?skip=' + more.skip;
    api(path, 'GET', null, function (data) {
      var list = data.items || [];
      more.skip = data.next_skip === undefined ? null : data.next_skip;
      for (var j = 0; j < list.length; j++) {
        var candidate = card(list[j], false);
        if (added.length < MORE_BATCH) add(candidate);
        else if (!known[candidate.id]) { known[candidate.id] = true; more.buffered.push(candidate); }
      }
      finish();
    }, function () { more.loading = false; finish(); });
  }

  function abrirBusqueda() {
    // Volver a editar invalida la consulta pendiente sin borrar resultados guardados.
    state.searchGeneration++; state.searchBusy = false;
    el('search-submit').textContent = 'Buscar';
    limpiarFocoBusqueda();
    state.section = 'search';
    state.homeZone = 'search';
    state.searchEditing = true;
    show('home');
    el('hero').hidden = true;
    el('rows-view').hidden = true;
    el('search-panel').hidden = false;
    renderNav();
    el('search-box').className = 'search-box focus';
    window.setTimeout(function () {
      if (state.screen !== 'home' || state.section !== 'search' || !state.searchEditing || state.assistantOpen) return;
      try { el('search-input').focus(); } catch (error) { /* DOM mínimo de prueba */ }
    }, 0);
  }

  function limpiarFocoBusqueda() {
    state.searchEditing = false;
    el('search-box').className = 'search-box';
    el('search-submit').className = 'search-submit';
    try { el('search-input').blur(); el('search-submit').blur(); } catch (error) { /* DOM mínimo de prueba */ }
    var previous = document.querySelector('#search-results .card.focus');
    if (previous) previous.className = previous.className.replace(' focus', '');
  }

  function seleccionarBuscar() {
    limpiarFocoBusqueda();
    state.homeZone = 'searchSubmit';
    el('search-submit').className = 'search-submit focus';
    try { el('search-submit').focus(); } catch (error) { /* DOM mínimo de prueba */ }
    renderNav();
  }

  function menuBusqueda() {
    limpiarFocoBusqueda();
    state.homeZone = 'nav'; renderNav();
  }

  function buscar() {
    if (state.searchBusy) return; // El mando puede repetir OK mientras se carga.
    var input = el('search-input');
    var term = String(input.value || '').replace(/^\s+|\s+$/g, '');
    if (term.length < 2) {
      el('search-status').textContent = 'Escribe al menos dos letras.';
      abrirBusqueda();
      return;
    }
    seleccionarBuscar();
    state.searchBusy = true;
    el('search-submit').textContent = 'Buscando…';
    el('search-status').textContent = 'Buscando “' + term + '”…';
    state.searchResults = []; state.searchIndex = 0;
    el('search-results-view').hidden = true;
    var generation = ++state.searchGeneration, found = [], pending = 2, failures = 0;
    function ready() {
      if (--pending > 0 || generation !== state.searchGeneration) return;
      state.searchBusy = false; el('search-submit').textContent = 'Buscar';
      if (state.screen !== 'home' || state.section !== 'search') return;
      var unique = {}, kept = [];
      for (var i = 0; i < found.length && kept.length < 30; i++) {
        if (!found[i].id || unique[found[i].id]) continue;
        unique[found[i].id] = true; kept.push(found[i]);
      }
      state.searchResults = kept; state.searchIndex = 0;
      // Terminar en segundo plano no debe mover el foco del menú ni del asistente.
      if (!state.assistantOpen && state.homeZone === 'searchSubmit' && kept.length)
        state.homeZone = 'searchResults';
      el('search-status').textContent = kept.length
        ? kept.length + ' resultados' + (failures ? ' · Búsqueda incompleta; vuelve a intentarlo.' : '')
        : failures ? 'No se pudo completar la búsqueda. Pulsa Buscar para reintentar.'
                   : 'No se encontraron títulos. Prueba con otro nombre.';
      renderBuscar();
    }
    function collect(type) {
      api('catalog/' + type + '?skip=0&search=' + encodeURIComponent(term), 'GET', null, function (data) {
        var list = data.items || [];
        for (var i = 0; i < list.length; i++) found.push(card(list[i], false));
        ready();
      }, function () { failures++; ready(); });
    }
    collect('movie'); collect('series');
  }

  function renderBuscar() {
    var host = el('search-results'); host.innerHTML = '';
    for (var i = 0; i < state.searchResults.length; i++) {
      var item = state.searchResults[i], box = document.createElement('div');
      box.className = 'card'; box.id = 'search-card' + i;
      var frame = document.createElement('div'); frame.className = 'art';
      var art = document.createElement('img'); art.src = item.art || ''; art.alt = '';
      var name = document.createElement('span'); name.textContent = item.title;
      var meta = document.createElement('small');
      meta.textContent = (item.raw.type === 'series' ? 'Serie' : 'Película') + (item.meta ? ' · ' + item.meta : '');
      frame.appendChild(art); box.appendChild(frame); box.appendChild(name); box.appendChild(meta);
      enlazarResultado(box, i); host.appendChild(box);
    }
    el('search-results-view').hidden = !state.searchResults.length;
    if (state.searchResults.length && state.homeZone === 'searchResults' && !state.assistantOpen)
      focusBuscar();
  }

  function enlazarResultado(box, index) {
    box.style.cursor = 'pointer';
    box.onmouseover = function () { state.homeZone = 'searchResults'; state.searchIndex = index; focusBuscar(); };
    box.onclick = function () { state.homeZone = 'searchResults'; state.searchIndex = index; focusBuscar(); abrirResultado(); };
  }

  function focusBuscar() {
    if (!state.searchResults.length) return;
    limpiarFocoBusqueda();
    state.homeZone = 'searchResults';
    var box = el('search-card' + state.searchIndex);
    if (!box) return;
    box.className += ' focus';
    var strip = el('search-results');
    var relativeLeft = box.offsetLeft;
    if (box.offsetParent !== strip) relativeLeft -= strip.offsetLeft;
    strip.style.transform = 'translateX(' + (30 - relativeLeft) + 'px)';
  }

  /** Rutas del mando entre campo, botón, resultados y menú de la búsqueda normal. */
  function tecladoBusqueda(code, event) {
    if (state.searchEditing) {
      if (code === 8) return; // Backspace edita texto; nunca cambia el foco.
      if (code === KEY_OK) buscar();
      else if (code === KEY_RIGHT || code === KEY_DOWN || inList(KEY_BACK, code)) seleccionarBuscar();
      else if (code === KEY_UP) menuBusqueda();
      else return; // Las letras, el dictado y las demás teclas pertenecen al campo nativo.
    } else if (state.homeZone === 'searchSubmit') {
      if (code === KEY_OK) buscar();
      else if (code === KEY_UP || code === KEY_LEFT) abrirBusqueda();
      else if (code === KEY_DOWN && state.searchResults.length) focusBuscar();
      else if (inList(KEY_BACK, code)) menuBusqueda();
      else return;
    } else if (state.homeZone === 'searchResults') {
      if (code === KEY_RIGHT && state.searchIndex < state.searchResults.length - 1) {
        state.searchIndex++; focusBuscar();
      } else if (code === KEY_LEFT && state.searchIndex > 0) {
        state.searchIndex--; focusBuscar();
      } else if (code === KEY_UP || inList(KEY_BACK, code)) seleccionarBuscar();
      else if (code === KEY_OK) abrirResultado();
      else return;
    } else {
      if (code === KEY_OK || code === KEY_DOWN) abrirBusqueda();
      else if (code === KEY_UP || inList(KEY_BACK, code)) menuBusqueda();
      else return;
    }
    event.preventDefault(); // Evita que OK active también el clic nativo del botón.
  }

  /* ------------------------------------------------------- asistente GPT */

  function ocultarGPT() {
    var launcher = el('gpt-launcher');
    if (launcher) { launcher.hidden = true; launcher.className = 'gpt-launcher'; }
    if (state.homeZone === 'assistant') state.homeZone = 'rows';
  }

  function mostrarGPT() {
    if (state.screen !== 'home' || state.section === 'search') return;
    state.homeZone = 'assistant';
    var launcher = el('gpt-launcher');
    launcher.hidden = false; launcher.className = 'gpt-launcher focus';
    var current = document.querySelector('.card.focus');
    if (current) current.className = current.className.replace(' focus', '');
  }

  function abrirAsistente() {
    if (state.screen === 'pair' || state.assistantOpen) return;
    state.assistantOriginZone = state.homeZone;
    state.assistantOriginSearchEditing = state.searchEditing;
    state.assistantOpen = true; state.assistantBusy = false;
    state.assistantGeneration++;
    ocultarGPT();
    el('assistant-overlay').hidden = false;
    // Al abrir sobre una película se pausa el sonido para que el dictado se entienda.
    state.assistantResume = state.screen === 'player' && state.playing && !el('video').paused;
    if (state.assistantResume) try { el('video').pause(); } catch (error) { /* continúa */ }
    // La consulta y su respuesta siguen en el DOM durante esta sesión. Volver desde
    // una ficha o una fuente fallida no vuelve a consultar la IA ni mueve la selección.
    state.assistantEditing = !state.assistantResults.length;
    if (state.assistantResults.length) { focusAsistente(); return; }
    editarAsistente();
    window.setTimeout(function () {
      if (!state.assistantOpen || !state.assistantEditing) return;
      try { el('assistant-input').focus(); } catch (error) { /* DOM mínimo de prueba */ }
    }, 0);
  }

  function cerrarAsistente(reanudar) {
    state.assistantGeneration++;
    state.assistantOpen = false; state.assistantEditing = false; state.assistantBusy = false;
    el('assistant-overlay').hidden = true;
    el('assistant-overlay').className = 'assistant-overlay';
    try { el('assistant-input').blur(); } catch (error) { /* DOM mínimo de prueba */ }
    if (reanudar && state.assistantResume && state.screen === 'player' && state.playing)
      try { el('video').play(); } catch (error) { /* algunos Tizen exigen el mando */ }
    state.assistantResume = false;
    if (state.screen === 'home') {
      state.homeZone = state.assistantOriginZone === 'assistant' ? 'rows' : state.assistantOriginZone;
      if (state.section === 'search') {
        state.searchEditing = state.assistantOriginSearchEditing;
        if (state.searchEditing) abrirBusqueda();
        else if (state.homeZone === 'searchSubmit') seleccionarBuscar();
        else if (state.homeZone === 'searchResults') focusBuscar();
        else renderNav();
      } else focusCard();
    }
  }

  function renderAsistente() {
    var host = el('assistant-results'); host.innerHTML = '';
    for (var i = 0; i < state.assistantResults.length; i++) {
      var item = state.assistantResults[i], box = document.createElement('div');
      box.className = 'card'; box.id = 'assistant-card' + i;
      var top = document.createElement('div'); top.className = 'assistant-result-top';
      var frame = document.createElement('div'); frame.className = 'art';
      var art = document.createElement('img'); art.src = item.art || ''; art.alt = '';
      var info = document.createElement('div'); info.className = 'assistant-result-info';
      var rank = document.createElement('div'); rank.className = 'assistant-result-rank';
      rank.textContent = 'ELECCIÓN ' + (i < 9 ? '0' : '') + (i + 1);
      var name = document.createElement('h2'); name.textContent = item.title; name.title = item.title;
      var meta = document.createElement('small');
      var episode = item.assistantEpisode;
      meta.textContent = (item.raw.type === 'series' ? 'Serie' : 'Película') +
        (item.raw.releaseInfo ? ' · ' + item.raw.releaseInfo : '') +
        (episode ? ' · T' + episode.season + ' E' + episode.episode : '') +
        (state.assistantStartMs !== null ? ' · Desde ' + clock(state.assistantStartMs) : '');
      var label = document.createElement('div'); label.className = 'assistant-reason-label';
      label.textContent = 'POR QUÉ ESTÁ AQUÍ';
      var reason = document.createElement('p'); reason.className = 'assistant-reason';
      reason.id = 'assistant-reason' + i;
      // El texto de la IA se muestra como texto, sin interpretar etiquetas HTML.
      reason.textContent = item.raw.assistant_reason ||
        'Vuelve a buscar para obtener la explicación de esta selección.';
      frame.appendChild(art); top.appendChild(frame);
      info.appendChild(rank); info.appendChild(name); info.appendChild(meta); top.appendChild(info);
      box.appendChild(top); box.appendChild(label); box.appendChild(reason);
      enlazarAsistente(box, i); host.appendChild(box);
    }
    el('assistant-results-view').hidden = !state.assistantResults.length;
    if (state.assistantResults.length) focusAsistente();
  }

  function enlazarAsistente(box, index) {
    box.style.cursor = 'pointer';
    box.onmouseover = function () {
      state.assistantEditing = false; state.assistantIndex = index; focusAsistente();
    };
    box.onclick = function () { state.assistantIndex = index; abrirResultadoAsistente(); };
  }

  function focusAsistente() {
    state.assistantZone = 'results';
    el('assistant-position').textContent = (state.assistantIndex + 1) + ' / ' + state.assistantResults.length;
    el('assistant-box').className = 'assistant-box';
    el('assistant-submit').className = 'assistant-submit';
    var previous = document.querySelector('#assistant-results .card.focus');
    if (previous) previous.className = previous.className.replace(' focus', '');
    var box = el('assistant-card' + state.assistantIndex);
    if (!box) return;
    box.className += ' focus';
    var strip = el('assistant-results'), relativeLeft = box.offsetLeft;
    if (box.offsetParent !== strip) relativeLeft -= strip.offsetLeft;
    strip.style.transform = 'translateX(' + (20 - relativeLeft) + 'px)';
  }

  function editarAsistente() {
    state.assistantEditing = true; state.assistantZone = 'input';
    el('assistant-box').className = 'assistant-box focus';
    el('assistant-submit').className = 'assistant-submit';
    try { el('assistant-input').focus(); } catch (error) { /* DOM mínimo de prueba */ }
  }

  function seleccionarBuscarAsistente() {
    state.assistantEditing = false; state.assistantZone = 'submit';
    try { el('assistant-input').blur(); } catch (error) { /* DOM mínimo de prueba */ }
    el('assistant-box').className = 'assistant-box';
    el('assistant-submit').className = 'assistant-submit focus';
  }

  function enviarAsistente() {
    if (state.assistantBusy) return;
    var input = el('assistant-input');
    var prompt = String(input.value || '').replace(/^\s+|\s+$/g, '');
    if (prompt.length < 2) {
      el('assistant-status').textContent = 'Dime qué quieres buscar.'; editarAsistente(); return;
    }
    state.assistantEditing = false; state.assistantBusy = true;
    el('assistant-overlay').className = 'assistant-overlay thinking';
    // Solo enviar otra consulta reemplaza los resultados de la búsqueda anterior.
    state.assistantResults = []; state.assistantIndex = 0; state.assistantStartMs = null;
    try { input.blur(); } catch (error) { /* DOM mínimo de prueba */ }
    el('assistant-box').className = 'assistant-box';
    el('assistant-submit').className = 'assistant-submit';
    el('assistant-status').textContent = 'Buscando títulos y preparando el porqué de cada elección…';
    el('assistant-answer').hidden = true; el('assistant-results-view').hidden = true;
    var generation = ++state.assistantGeneration;
    api('assistant/search', 'POST', { prompt: prompt }, function (data) {
      if (!state.assistantOpen || generation !== state.assistantGeneration) return;
      state.assistantBusy = false;
      el('assistant-overlay').className = 'assistant-overlay';
      state.assistantStartMs = data.start_seconds === null || data.start_seconds === undefined
        ? null : Math.max(0, data.start_seconds * 1000);
      state.assistantApproximate = !!data.approximate;
      var list = data.items || [], results = [];
      for (var i = 0; i < list.length; i++) {
        var item = card(list[i], false);
        item.assistantStartMs = state.assistantStartMs;
        item.assistantApproximate = state.assistantApproximate;
        item.assistantEpisode = list[i].assistant_episode || null;
        results.push(item);
      }
      state.assistantResults = results; state.assistantIndex = 0;
      el('assistant-answer').textContent = data.message || '';
      el('assistant-answer').hidden = !data.message;
      el('assistant-status').textContent = results.length
        ? results.length + ' resultados' + (state.assistantApproximate ? ' · momento aproximado' : '')
        : 'No encontré un título disponible con esa descripción.';
      renderAsistente();
      if (!results.length) editarAsistente();
    }, function (message) {
      if (!state.assistantOpen || generation !== state.assistantGeneration) return;
      state.assistantBusy = false; el('assistant-overlay').className = 'assistant-overlay';
      el('assistant-status').textContent = message; editarAsistente();
    });
  }

  function abrirResultadoAsistente() {
    var item = state.assistantResults[state.assistantIndex];
    if (!item) return;
    if (state.screen === 'player' && state.playing) cerrarIntento(true);
    cerrarAsistente(false);
    openItem(item, 'assistant');
  }

  function tecladoAsistente(code, event) {
    if (state.assistantEditing) {
      // Backspace pertenece al campo: dejar el borrado nativo sin cerrar el asistente.
      if (code === 8) return;
      if (code === KEY_OK) { enviarAsistente(); event.preventDefault(); }
      else if (code === KEY_RIGHT || code === KEY_DOWN) {
        seleccionarBuscarAsistente(); event.preventDefault();
      }
      else if (inList(KEY_BACK, code)) { cerrarAsistente(true); event.preventDefault(); }
      return; // las demás teclas, incluido el micrófono, pertenecen al dictado de Tizen
    }
    if (inList(KEY_BACK, code)) { cerrarAsistente(true); event.preventDefault(); return; }
    if (state.assistantBusy) return;
    if (state.assistantZone === 'submit') {
      if (code === KEY_OK) enviarAsistente();
      else if (code === KEY_UP || code === KEY_LEFT) editarAsistente();
      else if (code === KEY_DOWN && state.assistantResults.length) focusAsistente();
      else return;
      event.preventDefault(); return;
    }
    if (code === KEY_UP) { seleccionarBuscarAsistente(); event.preventDefault(); return; }
    if (code === KEY_RIGHT && state.assistantIndex < state.assistantResults.length - 1)
      state.assistantIndex++;
    else if (code === KEY_LEFT && state.assistantIndex > 0) state.assistantIndex--;
    else if (code === KEY_OK) { abrirResultadoAsistente(); event.preventDefault(); return; }
    else return;
    focusAsistente(); event.preventDefault();
  }

  function abrirResultado() {
    var item = state.searchResults[state.searchIndex];
    if (item) openItem(item);
  }

  /* --------------------------------------------------------------- ficha */

  function openItem(item, origin) {
    var raw = item.raw;
    state.detailOrigin = origin || (state.section === 'search' ? 'search' : 'rows');
    if (raw.logo !== undefined && raw.categories !== undefined) { tune(raw); return; }
    if (raw.type === 'karaoke_artist') { abrirArtista(raw); return; }
    if (raw.type === 'karaoke_song') { abrirEpisodio(raw, false, null); return; }
    var type = raw.type || 'movie';
    var volverA = origin === 'assistant' ? 'assistant' :
      (state.section === 'search' && state.screen === 'home' ? 'search' : state.screen);
    var generation = ++state.detailGeneration;
    brandLoading('Abriendo ' + (raw.name || '') + '…', volverA);
    api('content/' + type + '/' + raw.id, 'GET', null, function (data) {
      if (generation !== state.detailGeneration) return;
      state.item = data;
      if (item.raw.video_id) state.item.resume_video_id = item.raw.video_id;
      if (item.assistantStartMs !== undefined && item.assistantStartMs !== null)
        state.item.assistant_start_ms = item.assistantStartMs;
      state.item.assistant_approximate = !!item.assistantApproximate;
      if (item.assistantEpisode) state.item.assistant_episode = item.assistantEpisode;
      renderDetail(); show('detail');
    }, function (message) {
      if (generation === state.detailGeneration) brandFailure(message);
    });
  }

  function episodios() {
    return (state.item && state.item.episodes) || [];
  }

  function esKaraoke() {
    return !!(state.item && state.item.type === 'karaoke_artist');
  }

  /** Las temporadas se derivan de la lista ya validada y ordenada por el backend. */
  function temporadas() {
    var lista = episodios(), resultado = [];
    for (var i = 0; i < lista.length; i++) {
      var season = lista[i].season;
      if (resultado.indexOf(season) < 0) resultado.push(season);
    }
    return resultado;
  }

  function indiceTemporada(numero) {
    var lista = temporadas();
    for (var i = 0; i < lista.length; i++) if (lista[i] === numero) return i;
    return 0;
  }

  function episodiosTemporada(indice) {
    var todos = episodios(), seasons = temporadas(), numero = seasons[indice], resultado = [];
    for (var i = 0; i < todos.length; i++) if (todos[i].season === numero) resultado.push(todos[i]);
    return resultado;
  }

  function progresoVideo(videoId) {
    var playback = state.item && state.item.playback;
    return playback && playback.videos && playback.videos[videoId] ? playback.videos[videoId] : null;
  }

  function nombreCortoEpisodio(ep) {
    return esKaraoke() ? 'Canción ' + ep.episode : 'T' + ep.season + ':E' + ep.episode;
  }

  /** Decide el episodio principal sin inventar una cola nueva: retoma lo incompleto
      más reciente o avanza uno después del último completado. */
  function episodioPrincipal(lista) {
    var retomable = null, fecha = '';
    for (var i = 0; i < lista.length; i++) {
      var progreso = progresoVideo(lista[i].id);
      if (progreso && progreso.progress > 0 && !progreso.completed &&
          (progreso.updated_at || '') >= fecha) {
        retomable = lista[i]; fecha = progreso.updated_at || '';
      }
    }
    if (retomable) return { episodio: retomable, reanudar: true };
    if (state.item.resume_video_id) {
      for (var r = 0; r < lista.length; r++) {
        if (lista[r].id === state.item.resume_video_id && !progresoVideo(lista[r].id)) {
          return { episodio: lista[r], reanudar: true };
        }
      }
    }
    var ultimo = -1;
    for (var c = 0; c < lista.length; c++) {
      var visto = progresoVideo(lista[c].id);
      if (visto && visto.completed) ultimo = c;
    }
    return { episodio: lista[Math.min(ultimo + 1, lista.length - 1)], reanudar: false };
  }

  /** Nombre completo para la barra: la serie sola no identifica qué capítulo se ve. */
  function tituloReproduccion(videoId) {
    var base = (state.item && state.item.name) || '';
    var lista = episodios();
    for (var i = 0; i < lista.length; i++) {
      if (lista[i].id !== videoId) continue;
      var ep = lista[i];
      var nombre = ep.name || ep.title || '';
      if (esKaraoke()) return base + (nombre ? ' · ' + nombre : '');
      return base + ' · T' + ep.season + ' E' + ep.episode + (nombre ? ' · ' + nombre : '');
    }
    return base;
  }

  function renderDetail() {
    var item = state.item, lista = episodios();
    var karaoke = esKaraoke();
    el('detail-art').src = item.background || item.poster || '';
    el('detail-name').textContent = item.name || '';
    el('detail-meta').innerHTML = tags(item);
    el('detail-desc').textContent = (item.description || '').slice(0, 340);
    el('detail').className = 'screen' + (lista.length ? ' conEpisodios' : '') +
      (karaoke ? ' karaoke-detail' : '');
    el('detail-strip').hidden = !lista.length;
    el('detail-strip').className = 'tira' + (karaoke ? ' song-list' : '');
    el('detail-strip-title').textContent = karaoke ? 'Canciones' : 'Episodios';
    el('detail-seasons').hidden = karaoke;
    state.dacciones = [];
    if (lista.length) {
      var objetivo = episodioPrincipal(lista), pedido = item.assistant_episode, encontrado = null;
      for (var e = 0; pedido && e < lista.length; e++) {
        if (lista[e].id === pedido.video_id ||
            (lista[e].season === pedido.season && lista[e].episode === pedido.episode)) {
          encontrado = lista[e]; break;
        }
      }
      if (encontrado) objetivo = { episodio: encontrado, reanudar: false };
      var progresoObjetivo = progresoVideo(objetivo.episodio.id);
      var empezada = false;
      for (var s = 0; s < lista.length; s++) {
        var visto = progresoVideo(lista[s].id);
        if (visto && (visto.position_ms > 0 || visto.progress > 0)) { empezada = true; break; }
      }
      state.dacciones.push({ id: 'play',
        label: karaoke ? 'Reproducir canción' :
          (empezada ? 'Continuar viendo · ' : 'Reproducir ') + nombreCortoEpisodio(objetivo.episodio),
        icon: ICON.play, principal: true, video: objetivo.episodio.id,
        start: objetivo.reanudar && progresoObjetivo ? progresoObjetivo.position_ms : null });
      if (empezada) state.dacciones.push({ id: 'restart', label: 'Ver desde el inicio',
        icon: ICON.restart, video: lista[0].id });
      if (encontrado && item.assistant_start_ms !== undefined && item.assistant_start_ms !== null)
        state.dacciones.push({ id: 'scene', label: 'Ir a la escena · ' + clock(item.assistant_start_ms),
          icon: ICON.play, video: encontrado.id });
    } else {
      var progreso = progresoVideo(item.id);
      var iniciado = progreso && progreso.progress > 0 && !progreso.completed;
      state.dacciones.push({ id: 'play', label: iniciado ? 'Reanudar' : 'Reproducir',
        icon: ICON.play, principal: true, video: item.id });
      if (iniciado) state.dacciones.push({ id: 'restart', label: 'Ver desde el inicio',
        icon: ICON.restart, video: item.id });
      if (item.assistant_start_ms !== undefined && item.assistant_start_ms !== null)
        state.dacciones.push({ id: 'scene', label: 'Ir a la escena · ' + clock(item.assistant_start_ms),
          icon: ICON.play, video: item.id });
    }
    if (!karaoke) state.dacciones.push({ id: 'list', label: state.favorites[item.id] ? 'En mi lista' : 'Mi lista',
      icon: state.favorites[item.id] ? ICON.tick : ICON.plus, marcado: !!state.favorites[item.id] });
    state.dzone = 'acciones'; state.daction = 0;
    state.dseason = lista.length ? indiceTemporada(objetivo.episodio.season) : 0;
    var visibles = lista.length ? episodiosTemporada(state.dseason) : [];
    state.depisode = 0;
    for (var d = 0; d < visibles.length; d++) if (visibles[d].id === objetivo.episodio.id) state.depisode = d;
    pintarAcciones(el('detail-actions'), state.dacciones, state.dzone === 'acciones' ? state.daction : -1,
                   'dact', activarDetalle);
    if (lista.length) pintarTemporadas(el('detail-seasons'), state.dseason, false, 'dseason', cambiarTemporadaFicha);
    if (lista.length) pintarTira(el('detail-episodes'), visibles, -1, 'dep', function (indice) {
      state.dzone = 'episodios'; state.depisode = indice; enfocarDetalle(); reproducirEpisodio(visibles[indice]);
    }, function (indice) { state.dzone = 'episodios'; state.depisode = indice; enfocarDetalle(); });
    enfocarDetalle();
  }

  /** Selector de temporada pequeño y predecible para mando y puntero. */
  function pintarTemporadas(host, activo, enfocado, prefijo, alElegir) {
    host.hidden = esKaraoke();
    if (esKaraoke()) { host.innerHTML = ''; return; }
    var lista = temporadas(); host.innerHTML = '';
    for (var i = 0; i < lista.length; i++) {
      var tab = document.createElement('div');
      tab.id = prefijo + i;
      tab.className = 'season-tab' + (i === activo ? ' active' : '') +
                      (enfocado && i === activo ? ' focus' : '');
      tab.textContent = 'Temporada ' + lista[i]; tab.style.cursor = 'pointer';
      conPunteroTemporada(tab, i, alElegir);
      host.appendChild(tab);
    }
  }

  function conPunteroTemporada(tab, indice, alElegir) {
    tab.onmouseover = function () { alElegir(indice, true); };
    tab.onclick = function () { alElegir(indice, true); };
  }

  function cambiarTemporadaFicha(indice, desdePuntero) {
    if (desdePuntero && state.dseason === indice && state.dzone === 'temporadas') return;
    state.dseason = indice; state.depisode = 0;
    if (desdePuntero) state.dzone = 'temporadas';
    var lista = episodiosTemporada(state.dseason);
    pintarTira(el('detail-episodes'), lista, -1, 'dep', function (elegido) {
      state.dzone = 'episodios'; state.depisode = elegido; enfocarDetalle(); reproducirEpisodio(lista[elegido]);
    }, function (elegido) { state.dzone = 'episodios'; state.depisode = elegido; enfocarDetalle(); });
    enfocarDetalle();
  }

  /** Botones redondos de acción, los mismos en la ficha y en el reproductor. */
  function pintarAcciones(host, acciones, activo, prefijo, alPulsar) {
    host.innerHTML = '';
    for (var i = 0; i < acciones.length; i++) {
      var boton = document.createElement('div');
      boton.className = 'pbtn' + (acciones[i].principal ? ' principal' : '') +
                        (acciones[i].marcado ? ' marcado' : '') + (i === activo ? ' focus' : '');
      boton.id = prefijo + i;
      boton.innerHTML = acciones[i].icon + '<span>' + escape(acciones[i].label) + '</span>';
      conPunteroBoton(boton, i, alPulsar, prefijo);
      host.appendChild(boton);
    }
  }

  function conPunteroBoton(boton, indice, alPulsar, prefijo) {
    boton.style.cursor = 'pointer';
    boton.onmouseover = function () {
      if (prefijo === 'dact') { state.dzone = 'acciones'; state.daction = indice; enfocarDetalle(); }
      else { state.zone = 'buttons'; state.button = indice; enfocarReproductor(); }
    };
    boton.onclick = function () { alPulsar(indice); };
  }

  function resumenCancion(ep) {
    var versiones = ep.versions || [], elegida = null;
    for (var i = 0; i < versiones.length; i++) {
      if (versiones[i].version_id === ep.default_version_id || versiones[i].id === ep.default_version_id) {
        elegida = versiones[i]; break;
      }
    }
    if (!elegida && versiones.length) elegida = versiones[0];
    var partes = [versiones.length + (versiones.length === 1 ? ' versión' : ' versiones')];
    if (elegida && elegida.delivered) partes.push(elegida.delivered + 'p');
    return partes.join(' · ');
  }

  /** En karaoke no hay miniaturas útiles: una lista vertical deja ver más canciones,
      títulos completos y calidad, igual que una biblioteca musical. */
  function pintarListaCanciones(host, lista, activo, prefijo, alPulsar, alPasar) {
    host.innerHTML = '';
    for (var i = 0; i < lista.length && i < 60; i++) {
      var ep = lista[i], progreso = progresoVideo(ep.id);
      var fila = document.createElement('div');
      fila.className = 'ep song-row' + (i === activo ? ' focus' : '');
      fila.id = prefijo + i;
      var numero = document.createElement('span'); numero.className = 'song-number';
      numero.textContent = i + 1;
      var copia = document.createElement('div'); copia.className = 'song-copy';
      var titulo = document.createElement('b');
      titulo.textContent = ep.name || ep.title || 'Canción';
      var nota = document.createElement('small'); nota.textContent = resumenCancion(ep);
      copia.appendChild(titulo); copia.appendChild(nota);
      var estado = document.createElement('span'); estado.className = 'song-status';
      estado.textContent = progreso && progreso.completed ? '✓' : '▶';
      fila.appendChild(numero); fila.appendChild(copia); fila.appendChild(estado);
      if (progreso && progreso.progress > 0 && !progreso.completed) {
        var barra = document.createElement('i'); barra.className = 'song-progress';
        var relleno = document.createElement('span');
        relleno.style.width = Math.round(Math.min(1, progreso.progress) * 100) + '%';
        barra.appendChild(relleno); fila.appendChild(barra);
      }
      conPunteroEpisodio(fila, i, alPulsar, alPasar);
      host.appendChild(fila);
    }
  }

  /** Tira horizontal para series; lista musical vertical para karaoke. */
  function pintarTira(host, lista, activo, prefijo, alPulsar, alPasar) {
    if (esKaraoke()) {
      pintarListaCanciones(host, lista, activo, prefijo, alPulsar, alPasar);
      return;
    }
    host.innerHTML = '';
    for (var i = 0; i < lista.length && i < 60; i++) {
      var ep = lista[i];
      var caja = document.createElement('div');
      caja.className = 'ep' + (i === activo ? ' focus' : '');
      caja.id = prefijo + i;
      var marco = document.createElement('div'); marco.className = 'shot';
      var img = document.createElement('img'); img.src = ep.thumbnail || ''; img.alt = '';
      marco.appendChild(img);
      var progreso = progresoVideo(ep.id);
      if (progreso && progreso.progress > 0) {
        var barra = document.createElement('i'); barra.className = 'episode-progress' +
          (progreso.completed ? ' complete' : '');
        var relleno = document.createElement('span');
        relleno.style.width = Math.round(Math.min(1, progreso.progress) * 100) + '%';
        barra.appendChild(relleno);
        if (progreso.completed) {
          var check = document.createElement('em'); check.textContent = '✓'; barra.appendChild(check);
        }
        marco.appendChild(barra);
      }
      var titulo = document.createElement('b');
      titulo.textContent = esKaraoke()
        ? (ep.name || ep.title || 'Canción')
        : 'T' + ep.season + ' E' + ep.episode + '. ' + (ep.name || ep.title || 'Episodio');
      var nota = document.createElement('small');
      nota.textContent = (ep.overview || ep.description || '').slice(0, 70);
      caja.appendChild(marco); caja.appendChild(titulo); caja.appendChild(nota);
      conPunteroEpisodio(caja, i, alPulsar, alPasar);
      host.appendChild(caja);
    }
  }

  function conPunteroEpisodio(caja, indice, alPulsar, alPasar) {
    caja.style.cursor = 'pointer';
    caja.onmouseover = function () { alPasar(indice); };
    caja.onclick = function () { alPulsar(indice); };
  }

  function desplazarListaCanciones(host, fila) {
    var vista = host.parentNode;
    var alto = vista.clientHeight || vista.offsetHeight || 620;
    var total = host.scrollHeight || host.offsetHeight || 0;
    var filaAlto = fila.offsetHeight || 78;
    var destino = (fila.offsetTop || 0) - Math.max(0, (alto - filaAlto) / 2);
    var maximo = Math.max(0, total - alto);
    if (destino < 0) destino = 0;
    if (destino > maximo) destino = maximo;
    host.style.transform = 'translateY(-' + Math.round(destino) + 'px)';
  }

  function enfocarDetalle() {
    var lista = episodiosTemporada(state.dseason);
    pintarAcciones(el('detail-actions'), state.dacciones,
                   state.dzone === 'acciones' ? state.daction : -1, 'dact', activarDetalle);
    pintarTemporadas(el('detail-seasons'), state.dseason, state.dzone === 'temporadas',
                     'dseason', cambiarTemporadaFicha);
    var previo = document.querySelector('.ep.focus');
    if (previo) previo.className = previo.className.replace(' focus', '');
    if (state.dzone === 'episodios' && lista.length) {
      var caja = el('dep' + state.depisode);
      if (caja) {
        caja.className += ' focus';
        if (esKaraoke()) desplazarListaCanciones(el('detail-episodes'), caja);
        else el('detail-episodes').style.transform = 'translateX(' + (10 - caja.offsetLeft) + 'px)';
      }
    }
  }

  function activarDetalle(indice) {
    state.dzone = 'acciones'; state.daction = indice;
    var accion = state.dacciones[indice];
    if (!accion) return;
    if (accion.id === 'play' || accion.id === 'restart' || accion.id === 'scene') {
      state.chained = 0;
      var assistantStart = accion.id === 'scene' ? state.item.assistant_start_ms : accion.start;
      if (esKaraoke()) {
        var lista = episodios(), cancion = null;
        for (var c = 0; c < lista.length; c++) if (lista[c].id === accion.video) cancion = lista[c];
        if (cancion) playKaraokeLocal(cancion, accion.id === 'restart', assistantStart);
      } else {
        playVideo(state.item.type || 'movie', accion.video, tituloReproduccion(accion.video),
                  accion.id === 'restart', assistantStart);
      }
      return;
    }
    var deseado = !state.favorites[state.item.id];
    api('library/favorites/' + state.item.id, 'PUT', { enabled: deseado }, function () {
      state.favorites[state.item.id] = deseado;
      for (var i = 0; i < state.dacciones.length; i++) {
        if (state.dacciones[i].id !== 'list') continue;
        state.dacciones[i].label = deseado ? 'En mi lista' : 'Mi lista';
        state.dacciones[i].icon = deseado ? ICON.tick : ICON.plus;
        state.dacciones[i].marcado = deseado;
      }
      enfocarDetalle();
    }, function () { /* si no se pudo guardar, la lista se queda como estaba */ });
  }

  function reproducirEpisodio(ep) {
    var progreso = ep && progresoVideo(ep.id);
    if (progreso && (progreso.position_ms > 0 || progreso.progress > 0)) {
      state.resumePrompt = { episodio: ep, progreso: progreso };
      state.resumeChoice = 0; pintarEleccionEpisodio(); return;
    }
    abrirEpisodio(ep, false, null);
  }

  function abrirEpisodio(ep, desdeInicio, posicionGuardada) {
    state.chained = 0;
    if (esKaraoke()) playKaraokeLocal(ep, !!desdeInicio, posicionGuardada);
    else playVideo('series', ep.id, tituloReproduccion(ep.id), !!desdeInicio, posicionGuardada);
  }

  function pintarEleccionEpisodio() {
    var prompt = state.resumePrompt;
    if (!prompt) { el('resume-prompt').hidden = true; return; }
    var ep = prompt.episodio, progreso = prompt.progreso || {};
    var nombre = ep.name || ep.title || 'Episodio';
    el('resume-title').textContent = nombreCortoEpisodio(ep) + ' · ' + nombre;
    var posicionGuardada = progreso.position_ms || 0;
    var restante = (progreso.duration_ms || 0) - posicionGuardada;
    el('resume-detail').textContent = restante > 0 && restante < 60000
      ? 'Queda menos de un minuto. Puedes terminarlo o volver a verlo completo.'
      : 'Llegaste hasta ' + clock(posicionGuardada) + '. Elige desde dónde continuar.';
    el('resume-continue').className = 'choice-button' + (state.resumeChoice === 0 ? ' focus' : '');
    el('resume-restart').className = 'choice-button' + (state.resumeChoice === 1 ? ' focus' : '');
    el('resume-prompt').hidden = false;
  }

  function cerrarEleccionEpisodio() {
    state.resumePrompt = null; el('resume-prompt').hidden = true;
  }

  function resolverEleccionEpisodio(indice) {
    var prompt = state.resumePrompt;
    if (!prompt) return;
    if (indice !== undefined) state.resumeChoice = indice;
    var desdeInicio = state.resumeChoice === 1;
    var posicionGuardada = desdeInicio ? null : (prompt.progreso.position_ms || null);
    var episodio = prompt.episodio;
    cerrarEleccionEpisodio();
    abrirEpisodio(episodio, desdeInicio, posicionGuardada);
  }

  /* -------------------------------------------------------- karaoke local */

  /** Abre un archivo del PC con el mismo reproductor, barra y controles de SergioFlix. */
  function playKaraokeLocal(song, desdeInicio, requestedStartMs, versionIndex) {
    if (!song) return;
    cerrarIntento(false);
    show('player'); esperarCarga('Abriendo karaoke local…');
    el('player-action').hidden = true;
    el('player-title').textContent = tituloReproduccion(song.id);
    clearNotice(); closeMenu(); loadSubtitles([]);
    var gen = state.generacion;
    var versions = song.versions || [];
    var chosen = versionIndex === undefined || versionIndex === null ? 0 : versionIndex;
    if (versionIndex === undefined || versionIndex === null) {
      for (var v = 0; v < versions.length; v++) {
        if (versions[v].version_id === song.default_version_id) chosen = v;
      }
    }
    if (chosen < 0 || chosen >= versions.length) chosen = 0;
    state.items = versions; state.sources = []; state.attempt = chosen;
    state.salto = null; trabajando(''); state.zone = 'none'; state.dead = false;
    state.playing = {
      title: tituloReproduccion(song.id), offset: 0, total: 0, source: '', pendingSource: '',
      video: song.id, episode: song, localKaraoke: true, versionId: versions[chosen] && versions[chosen].version_id,
      limite: 0, rescates: 0, ultimaPosicion: 0, arrancando: true, arranqueEn: Date.now(),
      reiniciar: !!desdeInicio, requestedStart: requestedStartMs > 0 ? requestedStartMs : null,
      watchedMs: 0
    };
    api('karaoke/songs/' + encodeURIComponent(song.id) + '/play', 'POST', {
      version_id: state.playing.versionId || null
    }, function (data) {
      if (gen !== state.generacion || !state.playing) return;
      var play = state.playing;
      var progress = progresoVideo(song.id);
      var resume = play.reiniciar ? 0 :
        (play.requestedStart !== null ? play.requestedStart : (progress ? progress.position_ms || 0 : 0));
      play.reiniciar = false; play.requestedStart = null; play.versionId = data.version_id;
      logroCarga('¡Listo!', 'Cargando…');
      alCargarVideo(function () {
        if (gen !== state.generacion || !state.playing) return;
        var video = el('video');
        video.src = data.stream_url; video.load(); startAt(video, resume / 1000);
        try { video.play(); } catch (error) { /* algunos Tizen esperan el evento del mando */ }
        state.playing.arranqueEn = Date.now(); state.ultimoT = -1; state.avanceEn = Date.now();
        state.playing.ultimaPosicion = resume;
        el('player-title').textContent = state.playing.title;
        renderControles(); touchControls();
      });
    }, function (message) {
      if (gen === state.generacion) finalizar(message || 'No se pudo abrir este karaoke.',
        [{ id: 'home', label: 'Volver a las canciones', icon: ICON.back, principal: true }]);
    });
  }

  /* ---------------------------------------------------------- reproductor */

  /* La preparación comunica hitos reales, nunca porcentajes ni segundos inventados.
     Una rueda serena confirma que hay trabajo en curso sin prometer un tiempo falso. */

  function detenerCarga() {
    var carga = state.carga;
    carga.version++;
    if (carga.logro) window.clearTimeout(carga.logro);
    carga.activa = false; carga.transicion = false;
    carga.logro = null; carga.cola = [];
    carga.etapa = ''; carga.minimoHasta = 0; carga.iniciarVideo = null;
    el('player-loading-modal').className = 'loading-modal';
    el('player-loading').hidden = true;
    el('player-success').hidden = true;
  }

  /** La imagen manda: cierra la carga y revoca sus transiciones pendientes. */
  function confirmarReproduccion() {
    var play = state.playing;
    if (state.screen !== 'player' || !play || state.dead || el('video').paused ||
        (!play.source && !play.localKaraoke) || state.carga.iniciarVideo) return;
    if (play.trace && !play.trace.first) {
      play.trace.first = true;
      registrarReproduccion('first_frame', { elapsed_ms: Math.min(86400000, Date.now() - play.trace.started) });
    }
    play.arrancando = false;
    detenerCarga();
    el('player-brand').hidden = true;
    state.esperando = false; state.avanceEn = Date.now();
    if (!state.salto) trabajando('');
  }

  function esperarCarga(texto) {
    var carga = state.carga;
    var version = carga.version;
    carga.activa = true; carga.transicion = false;
    carga.etapa = texto;
    carga.minimoHasta = texto === 'Eligiendo la mejor alternativa…' ? Date.now() + ELECCION_MINIMA : 0;
    el('player-brand').hidden = true;
    el('player-success').hidden = true;
    el('player-loading-modal').className = 'loading-modal';
    el('player-loading-text').textContent = texto;
    el('player-loading').hidden = false;
    // Si la fuente quedó lista antes de terminar el segundo hito, se cargó pero no se
    // abrió: empezar aquí evita que la película avance con sonido detrás del modal.
    if (texto === 'Cargando…' && carga.iniciarVideo) {
      var iniciar = carga.iniciarVideo; carga.iniciarVideo = null; iniciar();
    }
  }

  function siguienteLogro() {
    var carga = state.carga;
    if (!carga.activa || carga.transicion || !carga.cola.length) return;
    if (carga.minimoHasta && Date.now() < carga.minimoHasta) {
      var versionMinima = carga.version;
      if (!carga.logro) carga.logro = window.setTimeout(function () {
        if (!carga.activa || carga.version !== versionMinima) return;
        carga.logro = null; siguienteLogro();
      }, carga.minimoHasta - Date.now());
      return;
    }
    var hito = carga.cola.shift();
    var version = carga.version;
    carga.transicion = true;
    el('player-loading-modal').className = 'loading-modal';
    // El cambio breve de estado separa dos hitos reales sin simular avance numérico.
    carga.logro = window.setTimeout(function () {
      if (!carga.activa || carga.version !== version) return;
      el('player-loading').hidden = true;
      el('player-success-text').textContent = hito.texto;
      el('player-success').hidden = false;
      carga.logro = window.setTimeout(function () {
        if (!carga.activa || carga.version !== version) return;
        el('player-success').hidden = true;
        carga.transicion = false; carga.logro = null;
        if (hito.siguiente) esperarCarga(hito.siguiente);
        else carga.activa = false;
        siguienteLogro();
      }, 1000);
    }, 240);
  }

  function logroCarga(texto, siguiente) {
    if (!state.carga.activa) return;
    state.carga.cola.push({ texto: texto, siguiente: siguiente || '' });
    siguienteLogro();
  }

  function alternativaCarga() {
    detenerCarga();
    state.carga.activa = true; state.carga.transicion = true;
    var version = state.carga.version;
    el('player-brand').hidden = true;
    el('player-success-text').textContent = 'Espera. ¡Encontramos una alternativa mejor!';
    el('player-success').hidden = false;
    state.carga.logro = window.setTimeout(function () {
      if (!state.carga.activa || state.carga.version !== version) return;
      el('player-success').hidden = true;
      state.carga.transicion = false; state.carga.logro = null;
      esperarCarga('Cargando…');
      siguienteLogro();
    }, 1000);
  }

  /** Abre el archivo al llegar a «Cargando», o inmediatamente si ya no hay flujo visual. */
  function alCargarVideo(iniciar) {
    var carga = state.carga;
    if (!carga.activa || carga.etapa === 'Cargando…') { iniciar(); return; }
    carga.iniciarVideo = iniciar;
  }

  function note(text, conAccion) {
    detenerCarga();
    state.notaBase = conAccion ? '' : text;
    el('player-note').textContent = text;
    el('player-brand').hidden = false;
    el('player-spinner').hidden = !!conAccion;
    el('player-action').hidden = !conAccion;
  }

  /* Ninguna fuente sirvió. Se dice de forma que no parezca una avería del equipo,
     porque casi nunca lo es: es una edición que hoy no está disponible. */
  /* Sin respuesta de los proveedores. No es lo mismo que no haber encontrado nada:
     lo primero se arregla reintentando y lo segundo no, así que se dicen distinto. */
  function sinFuentes() {
    finalizar('No se pudo consultar tus proveedores. Puede ser cosa de un momento.',
              [{ id: 'otra', label: 'Reintentar', icon: ICON.restart, principal: true },
               salidaReproduccion()]);
  }

  function salidaReproduccion() {
    var resultados = (state.detailOrigin === 'assistant' && state.assistantResults.length) ||
      (state.detailOrigin === 'search' && state.searchResults.length);
    return { id: 'home', label: resultados ? 'Volver a resultados' : 'Ir a inicio',
             icon: resultados ? ICON.back : ICON.home };
  }

  /** Deja la pantalla en un final con botones, y el foco en el primero. */
  function finalizar(texto, botones) {
    state.dead = true;
    state.fin = botones;
    state.finBoton = 0;
    trabajando('');
    note(texto, true);
    pintarFin();
  }

  function pintarFin() {
    pintarAcciones(el('player-action'), state.fin, state.finBoton, 'fin', activarFin);
  }

  function activarFin(indice) {
    state.finBoton = indice;
    var boton = state.fin[indice];
    if (!boton || boton.id === 'home') { stopPlayback(true); return; }
    var play = state.playing;
    if (boton.id === 'otra' && play && play.channel) { tune(play.channel); return; }
    state.dead = false;
    playVideo(state.item && state.item.type ? state.item.type : 'movie',
              play ? play.video : '', play ? play.title : '',
              play && play.reiniciar && !play.ultimaPosicion,
              play ? (play.ultimaPosicion || play.requestedStart) : null);
  }

  function sinContenido() {
    finalizar('Este contenido no está disponible ahora mismo. Puedes reintentar o elegir otro título.',
              [{ id: 'otra', label: 'Reintentar', icon: ICON.restart, principal: true },
               salidaReproduccion()]);
  }

  /* Aviso ligero: se dibuja encima de la imagen sin taparla. El cartel de marca
     apaga la pantalla entera, y para un salto de diez segundos eso es desmedido;
     lo importante es que nunca haya una espera sin nada que la explique. */
  function trabajando(texto, malo) {
    var caja = el('trabajo');
    if (!texto) { caja.hidden = true; return; }
    el('trabajo-texto').textContent = texto;
    el('trabajo-rueda').hidden = !!malo;
    caja.className = malo ? 'trabajo malo' : 'trabajo';
    caja.hidden = false;
    touchControls();
  }

  /** Cuanto video puede alcanzarse sin que el equipo vuelva a convertir. */
  function ventana() {
    var video = el('video'), play = state.playing;
    var fin = (play && play.limite) || 0;
    // La lista crece mientras el equipo convierte, asi que lo que diga el propio
    // reproductor manda sobre la foto que dio el equipo al empezar.
    if (video.seekable && video.seekable.length) {
      var propio = video.seekable.end(video.seekable.length - 1) * 1000;
      if (propio > fin) fin = propio;
    }
    return fin;
  }

  /** El destino debe pertenecer a un rango real, incluidos su inicio y sus huecos. */
  function puedeSaltarLocal(local) {
    var video = el('video'), play = state.playing;
    if (local < 0) return false;
    if (video.seekable && video.seekable.length) {
      for (var i = 0; i < video.seekable.length; i++) {
        if (local >= video.seekable.start(i) && local < video.seekable.end(i)) return true;
      }
      return false;
    }
    return !!play && local * 1000 < (play.limite || 0);
  }

  function tune(channel) {
    cerrarIntento(false);
    var gen = state.generacion;
    show('player'); note('Sintonizando ' + channel.name + '…');
    state.playing = { title: channel.name, offset: 0, total: 0, source: '', pendingSource: '',
                      video: '', channel: channel };
    state.sources = []; state.attempt = 0; state.dead = false;
    api('tv/tune', 'POST', { channel_id: channel.id }, function (data) {
      if (gen !== state.generacion) return;
      var items = data.items || [];
      // Son respaldos automáticos de la misma señal, no «versiones» para elegir.
      state.items = []; state.sources = [];
      for (var i = 0; i < items.length; i++) state.sources.push(items[i].source_id);
      if (!state.sources.length) {
        finalizar('No hay una señal disponible para este canal.',
                  [{ id: 'home', label: 'Volver a canales', icon: ICON.back, principal: true }]);
        return;
      }
      begin(state.sources[0], channel.name, '');
    }, function (message) {
      if (gen === state.generacion) finalizar(message || 'No se pudo sintonizar este canal.',
        [{ id: 'otra', label: 'Reintentar', icon: ICON.restart, principal: true },
         { id: 'home', label: 'Volver a canales', icon: ICON.back }]);
    });
  }

  function playVideo(type, videoId, title, desdeInicio, requestedStartMs) {
    // Cualquier resto local o respuesta pendiente pierde autoridad antes de pedir el
    // título nuevo. Esto cubre cambio de película, episodio, reintento y versión.
    cerrarIntento(false);
    show('player'); esperarCarga('Buscando en la red…');
    el('player-action').hidden = true;
    // Cambia al pedir el nuevo video, no al terminar su preparación: durante una espera
    // nunca puede seguir rotulado el título anterior.
    el('player-title').textContent = title || '';
    clearNotice(); closeMenu();
    // Toda respuesta de un intento anterior queda invalidada aqui: sin esto, pedir otro
    // video mientras el primero se prepara deja dos preparaciones peleando, y una se
    // lleva por delante la conversion que la otra estaba sirviendo.
    var gen = state.generacion;
    state.salto = null; trabajando('');
    state.zone = 'none'; state.dead = false;
    state.playing = { title: title, offset: 0, total: 0, source: '', pendingSource: '', video: videoId, playbackId: traceId(),
                      limite: 0, rescates: 0, ultimaPosicion: 0,
                      arrancando: true, arranqueEn: Date.now(), reiniciar: !!desdeInicio,
                      requestedStart: requestedStartMs > 0 ? requestedStartMs : null,
                      watchedMs: 0 };
    pedir('playback/sources/' + type + '/' + videoId, 'GET', null, function (data) {
      if (gen !== state.generacion) return;
      var items = data.items || [];
      // Se recorre la lista entera en el orden que decidió el equipo. Declarar que no
      // hay contenido mientras aún quedan ediciones convierte un fallo puntual en uno final.
      state.items = items;
      state.sources = []; state.attempt = 0;
      // El ranking sigue visible. Solo el intento de reanudación da prioridad a la
      // edición guardada, incluso cuando su enlace e ID temporal se renovaron.
      var preferences = data.preferences || {}, preferred = '';
      for (var i = 0; i < items.length; i++) {
        if (items[i].fingerprint && items[i].fingerprint === preferences.source_fingerprint &&
            !items[i].reputation && !items[i].subtitle_edition_unavailable) {
          preferred = items[i].source_id; break;
        }
      }
      if (preferred) state.sources.push(preferred);
      for (i = 0; i < items.length; i++) {
        if (items[i].source_id !== preferred) state.sources.push(items[i].source_id);
      }
      if (!state.sources.length) { sinContenido(); return; }
      logroCarga('¡Encontrado!', 'Eligiendo la mejor alternativa…');
      begin(state.sources[0], title, videoId, true);
    }, function (mensaje, status) {
      if (gen !== state.generacion) return;
      // Que el equipo no pueda preguntar no significa que no haya nada que ver.
      if (!status || status >= 500) sinFuentes(); else sinContenido();
    });
  }

  /* Si una fuente no arranca se pasa a la siguiente sin molestar a nadie: el usuario
     no tiene por qué saber que existen varias, ni elegir entre ellas. */
  function etiquetaFuente() {
    return state.attempt ? 'Probando otra alternativa…' : 'Abriendo la mejor alternativa…';
  }

  function siguienteFuente(mensaje, status, code) {
    var play = state.playing;
    if (!play) return;
    // Una sesion caducada por inactividad no es una fuente rota: cambiar de edicion
    // seria cambiar de audio y de calidad por un motivo que no lo justifica.
    if (mensaje && mensaje.indexOf('inactividad') >= 0) {
      state.salto = null; trabajando('');
      note('La reproducción se detuvo por inactividad. Pulsa Volver y vuelve a empezarla.');
      return;
    }
    var oldSource = play.pendingSource || play.source, next = state.sources[state.attempt + 1], fingerprint = null;
    for (var k = 0; k < state.items.length; k++) if (state.items[k].source_id === next) fingerprint = state.items[k].fingerprint || null;
    terminarAtasco();
    registrarReproduccion('source_switch', { switch_reason: 'automatic', error_code: code || 'player_error',
      next_fingerprint: fingerprint, elapsed_ms: play.trace ? Math.min(86400000, Date.now() - play.trace.started) : 0 });
    if (play.trace) play.trace.closed = true;
    // Conservar primero el último minuto confirmado. Detener el recurso abandonado
    // evita que la TV siga pidiendo sus fragmentos mientras se prepara el respaldo.
    saveProgress(false, play.ultimaPosicion || undefined);
    if (play.ultimaPosicion > 0) play.requestedStart = play.ultimaPosicion;
    play.source = ''; play.pendingSource = '';
    vaciarVideo();
    if (oldSource) api('playback/stop', 'POST', { source_id: oldSource }, function () {}, function () {});
    state.salto = null; trabajando('');
    // Lo que quedara pedido de la edición anterior ya no vale: sin esto su respuesta
    // tardía pisa la preparación nueva y las dos se estorban.
    state.generacion++;
    state.attempt++;
    if (state.attempt >= state.sources.length) { sinContenido(); return; }
    if (state.carga.activa) alternativaCarga(); else note(etiquetaFuente());
    play.arrancando = true; play.arranqueEn = Date.now(); play.rescates = 0;
    begin(state.sources[state.attempt], play.title, play.video, state.carga.activa);
  }

  /** Coloca el video en el punto pedido en cuanto el televisor conoce la lista. */
  function startAt(video, seconds) {
    cancelarPosicionamiento();
    if (!(seconds > 1)) return;
    var gen = state.generacion, source = video.src;
    var place = function () {
      video.removeEventListener('loadedmetadata', place);
      if (state.posicionamiento !== place) return;
      state.posicionamiento = null;
      if (gen !== state.generacion || video.src !== source) return;
      try { video.currentTime = seconds; } catch (error) { /* el televisor lo hará al reproducir */ }
    };
    state.posicionamiento = place;
    video.addEventListener('loadedmetadata', place);
    if (video.readyState >= 1) place();
  }

  function cancelarPosicionamiento() {
    if (state.posicionamiento) el('video').removeEventListener('loadedmetadata', state.posicionamiento);
    state.posicionamiento = null;
  }

  function begin(sourceId, title, videoId, conFlujo) {
    var gen = state.generacion;
    if (!state.playing) return;
    var trace = { id: traceId(), sequence: 0, started: Date.now(), first: false,
      buffering: 0, stableMs: 0, stableSent: false, closed: false };
    state.playing.trace = trace;
    state.playing.playbackId = state.playing.playbackId || traceId();
    state.playing.pendingSource = sourceId;
    if (!conFlujo) note(etiquetaFuente());
    pedir('playback/start', 'POST', { source_id: sourceId, attempt_id: trace.id,
      playback_id: state.playing.playbackId, client: 'tv' }, function (started) {
      if (gen !== state.generacion) return;
      var pendiente = state.playing || {};
      var resume = pendiente.reiniciar ? 0 :
        (pendiente.requestedStart !== null && pendiente.requestedStart !== undefined
          ? pendiente.requestedStart : (started.position_ms || 0));
      // El minuto sugerido se aplica una sola vez. A partir de ahí, cualquier rescate
      // usa la posición real alcanzada y nunca devuelve al espectador a esa escena.
      // Un intento fallido no consume el punto sugerido: lo hereda su respaldo.
      pendiente.requestedStart = resume;
      var preferences = started.preferences || {};
      var sameEdition = !!started.source_fingerprint &&
        preferences.source_fingerprint === started.source_fingerprint;
      var restored = sameEdition ? preferences : null;
      loadAudioTracks([], -1, false);
      state.subs.serverPending = !!started.subtitles_pending;
      loadSubtitles(started.subtitles || [], restored, started);
      // Audio se inspecciona en paralelo con la preparación. `MediaTools.inspect`
      // comparte el mismo FFprobe si la conversión también lo necesita.
      pedirAudio(sourceId);
      if (conFlujo) logroCarga('¡Listo!', 'Cargando…');
      else note(etiquetaFuente());
      var castBody = { source_id: sourceId, position_ms: resume, force_adapted: resume > 0 };
      if (restored && restored.audio_stream_index !== null && restored.audio_stream_index !== undefined) {
        castBody.audio_stream_index = restored.audio_stream_index;
      }
      function castReady(moved) {
        if (gen !== state.generacion) return;
        if (moved.audio_tracks) loadAudioTracks(moved.audio_tracks,moved.audio_stream_index,true);
        if (moved.subtitles !== undefined) {
          state.subs.serverPending = !!moved.subtitles_pending;
          loadSubtitles(moved.subtitles, restored, moved);
        }
        var abrir = function () {
          if (gen !== state.generacion || !state.playing) return;
          state.playing = { title: title, offset: moved.offset_ms || 0, total: moved.duration_ms || 0,
                            trace: trace, playbackId: pendiente.playbackId,
                            source: sourceId, pendingSource: sourceId,
                            fingerprint: started.source_fingerprint || '',
                            session: started.session_id, sequence: 0,
                            video: videoId || (state.playing ? state.playing.video : ''),
                            limite: moved.seekable_ms || 0, rescates: 0, ultimaPosicion: resume,
                            arrancando: true, arranqueEn: Date.now(), reiniciar: false,
                            watchedMs: pendiente.watchedMs || 0 };
          var video = el('video');
          video.src = moved.stream_url;
          video.load();
          startAt(video, (resume - (moved.offset_ms || 0)) / 1000);
          resetSubtitleClock(resume > 0 ? 'resume' : 'start');
          seleccionarSubtituloPorDefecto();
          try { video.play(); } catch (error) { /* algunos televisores exigen el evento */ }
          el('player-title').textContent = title;
          state.zone = 'none';
          // El plazo del arranque cuenta desde aquí, que es cuando el televisor ya tiene
          // una lista que reproducir. Contarlo desde que se pulsó Reproducir castigaba a
          // las preparaciones largas, que son normales.
          state.playing.arranqueEn = Date.now();
          state.ultimoT = -1; state.avanceEn = Date.now();
          // Buscar subtítulos no puede retrasar el arranque, pero tampoco puede perderse:
          // si el equipo avisa de que sigue buscando, se le vuelve a preguntar con la
          // película ya en marcha y aparecen solos cuando lleguen.
          if (state.subs.serverPending) pedirSubtitulos(false);
          prepararSubtitulos();
          renderControles(); touchControls();
        };
        if (conFlujo) alCargarVideo(abrir); else abrir();
      }
      function castFailed(mensaje, status, code) {
        if (gen !== state.generacion) return;
        if (code === 'audio_track_not_found' && castBody.audio_stream_index !== undefined) {
          // Si desapareció solo la pista, la edición todavía puede reproducirse.
          delete castBody.audio_stream_index;
          pedir('playback/cast', 'POST', castBody, castReady, castFailed); return;
        }
        if (gen === state.generacion) siguienteFuente(mensaje, status, code);
      }
      pedir('playback/cast', 'POST', castBody, castReady, castFailed);
    }, function (mensaje, status, code) {
      if (gen === state.generacion) siguienteFuente(mensaje, status, code);
    });
  }

  /* Elegir versión a mano. No es probar la siguiente porque la anterior falló: es una
     decisión del usuario, así que se va exactamente a la que pidió y la cadena de
     reintentos automáticos arranca desde ahí. */
  function cambiarVersion(indice) {
    var item = state.items[indice], play = state.playing;
    if (!item || !play) return;
    if (play.localKaraoke) {
      playKaraokeLocal(play.episode, true, null, indice);
      return;
    }
    var titulo = play.title, videoId = play.video, watched = play.watchedMs || 0;
    var playbackId = play.playbackId;
    terminarAtasco();
    registrarReproduccion('source_switch', { switch_reason: 'manual', next_fingerprint: item.fingerprint || null });
    if (play.trace) play.trace.closed = true;
    cerrarIntento(false);
    state.salto = null; trabajando('');
    state.dead = false;
    state.attempt = indice;
    state.sources = [];
    for (var i = indice; i < state.items.length; i++) {
      state.sources.push(state.items[i].source_id);
    }
    state.attempt = 0;
    state.playing = { title: titulo, offset: 0, total: 0, source: '', pendingSource: '', video: videoId,
                      limite: 0, rescates: 0, arrancando: true, arranqueEn: Date.now(),
                      watchedMs: watched, playbackId: playbackId };
    loadSubtitles([]);
    loadAudioTracks([], -1, false);
    show('player');
    detenerCarga(); esperarCarga('Buscando en la red…');
    logroCarga('¡Encontrado!', 'Eligiendo la mejor alternativa…');
    begin(state.sources[0], titulo, videoId, true);
  }

  /** Detiene el elemento del televisor aunque una de sus operaciones particulares falle. */
  function vaciarVideo() {
    var video = el('video'), retirado = false;
    cancelarPosicionamiento();
    try { video.pause(); } catch (error) { /* se continúa con las defensas siguientes */ }
    try { video.removeAttribute('src'); retirado = true; } catch (error) { /* algunos Tizen no lo admiten */ }
    if (!retirado) try { video.src = ''; } catch (error) { /* último recurso en motores antiguos */ }
    try { video.load(); } catch (error) { /* el recurso local ya quedó revocado */ }
  }

  /** Revoca primero la sesión lógica; una respuesta tardía ya no puede volver a abrirla. */
  function cerrarIntento(reiniciarCadena) {
    var play = state.playing;
    if (play && play.trace && !play.trace.closed) {
      registrarReproduccion('cancelled', {}); play.trace.closed = true;
    }
    var sourceId = play && (play.source || play.pendingSource);
    state.generacion++;
    state.pidiendo = 0;
    detenerCarga();
    saveProgress(false, play && (state.salto || play.posicionSinConfirmar) ? play.ultimaPosicion : undefined);
    state.salto = null;
    clearNotice(); closeMenu();
    loadSubtitles([]);
    loadAudioTracks([], -1, false);
    state.playing = null; state.zone = 'none'; state.dead = false;
    if (reiniciarCadena) state.chained = 0;
    el('player-strip').hidden = true;
    vaciarVideo();
    if (sourceId) api('playback/stop', 'POST', { source_id: sourceId }, function () {}, function () {});
  }

  function stopPlayback(goHome) {
    flushSubtitleMeasurement();
    cerrarIntento(true);
    if (goHome) volverAlOrigen();
    else { show('detail'); renderDetail(); }
  }

  /** Recupera la lista y su foco sin repetir la búsqueda ni reconstruir el catálogo. */
  function volverAlOrigen() {
    show('home');
    if (state.section === 'search') {
      el('hero').hidden = true; el('rows-view').hidden = true; el('search-panel').hidden = false;
      state.searchEditing = false;
      state.homeZone = state.searchResults.length ? 'searchResults' : 'search';
      renderNav(); focusBuscar();
    } else focusCard();
    if (state.detailOrigin === 'assistant' && state.assistantResults.length) abrirAsistente();
  }

  function guardarProgresoLocal(play, position, duration, completed) {
    if (!state.item || !play || (play.watchedMs || 0) < 30000) return;
    var fraction = duration ? Math.min(1, position / duration) : 0;
    if (completed || fraction >= 0.92) fraction = 1;
    state.item.playback = state.item.playback || { videos: {} };
    state.item.playback.videos = state.item.playback.videos || {};
    state.item.playback.videos[play.video] = { position_ms: position, duration_ms: duration,
      progress: fraction, completed: fraction >= 1, updated_at: new Date().toISOString() };
  }

  /** Solo IDs/huellas y ajustes: nunca guardar la URL temporal de un proveedor. */
  function preferenciasReproduccion() {
    var play = state.playing, chosen = state.subs.list[state.subs.track];
    if (!play || !play.source || play.live || play.localKaraoke) return null;
    var restored = state.subs.restore;
    return { source_id: play.source,
      audio_stream_index: state.audio.track >= 0 ? state.audio.track : null,
      subtitle_key: restored ? restored.subtitle_key || null : chosen && chosen.key || null,
      subtitle_sync_token: restored ? restored.subtitle_sync_token || null : chosen && chosen.sync_token || null,
      subtitle_offset_ms: restored ? restored.subtitle_offset_ms || 0 : state.subs.sync,
      subtitle_disabled: restored ? !!restored.subtitle_disabled : state.subs.disabled,
      subtitle_manual: restored ? !!restored.subtitle_manual : state.subs.manual,
      subtitle_verified: restored ? !!restored.subtitle_verified : !!(chosen && chosen.verified),
      subtitle_style: state.subs.style };
  }

  function saveProgress(completed, positionOverride) {
    var play = state.playing, video = el('video');
    if (!play) return;
    // El destino aún no confirmado no puede reemplazar el punto para continuar viendo.
    if ((state.salto || play.posicionSinConfirmar) && positionOverride === undefined) return;
    var position = Math.round(positionOverride === undefined
      ? play.offset + (video.currentTime || 0) * 1000 : positionOverride);
    var duration = Math.round(play.total || (video.duration ? video.duration * 1000 : 0));
    guardarProgresoLocal(play, position, duration, !!completed);
    if (!play.session) return;
    play.sequence = Math.max(play.sequence + 1, Date.now());
    var body = {
      session_id: play.session, sequence: play.sequence,
      position_ms: position, duration_ms: duration, completed: !!completed,
      watched_ms: Math.round(play.watchedMs || 0)
    };
    var preferences = preferenciasReproduccion();
    if (preferences) body.preferences = preferences;
    api('playback/progress', 'PUT', body, function () {}, function () {});
  }

  function posicion() {
    var play = state.playing, video = el('video');
    if (!play) return 0;
    if (play.posicionSinConfirmar && !state.salto) return play.ultimaPosicion || 0;
    return play.offset + (video.currentTime || 0) * 1000;
  }

  function duracion() {
    var play = state.playing, video = el('video');
    if (!play) return 0;
    return play.total || (video.duration ? video.duration * 1000 : 0);
  }

  /** Salto a un punto absoluto de la película. */
  function saltarA(target) {
    detenerAvance();
    if (state.playing && state.playing.trace) { state.playing.trace.stableMs = 0; state.playing.trace.stableSent = false; }
    var play = state.playing;
    if (!play || state.dead) return;
    if (play.arrancando || (!play.source && !play.localKaraoke)) {
      trabajando('Espera a que comience el video…'); return;
    }
    // Encadenar saltos es lo que hace que parezca colgado: cada uno puede obligar al
    // equipo a reconvertir, y el anterior se tira a la basura a medio hacer.
    if (state.salto || state.audio.switching) { trabajando('Espera: todavía se está saltando…'); return; }
    var total = duracion();
    if (target < 0) target = 0;
    if (total && target > total - 2000) target = total - 2000;
    if (target < 0) target = 0;
    state.scrub = target;
    var local = (target - play.offset) / 1000;
    if (play.localKaraoke) { saltoLocal(target, local); return; }
    if (puedeSaltarLocal(local)) { saltoLocal(target, local); return; }
    saltoServidor(target, false);
  }

  /* El televisor no siempre obedece a `currentTime`: mientras la lista crece no lleva
     marca de final, y su reproductor la trata como emisión en directo, donde saltar no
     significa nada. Se salta igual, pero se comprueba: si el video no se movió, se le
     pide al equipo la lista desde ese punto, que sí funciona siempre. Sin esta
     comprobación el mando parecía muerto, que era justo el problema. */
  function saltoLocal(target, local) {
    resetSubtitleClock('seek');
    var video = el('video'), play = state.playing, intentos = 0;
    var gen = state.generacion;
    var operation = { id: ++state.saltoId, target: target, desde: Date.now(), servidor: false,
                      paused: video.paused, from: play.ultimaPosicion || posicion(), local: local };
    play.saltoConfirmado = false;
    state.salto = operation;
    trabajando('Saltando a ' + clock(target) + '…');
    try { video.currentTime = local; } catch (error) {
      state.salto = null;
      if (play && play.localKaraoke) trabajando('No se pudo saltar dentro de este archivo.', true);
      else saltoServidor(target, false);
      return;
    }
    play.posicionSinConfirmar = true;
    operation.visto = video.currentTime || 0; operation.vistoEn = Date.now();
    var comprobar = function () {
      if (gen !== state.generacion || state.playing !== play || state.salto !== operation) return;
      intentos++;
      comprobarSalto();
      if (state.salto !== operation) return;
      if (intentos >= SALTO_COMPROBACIONES) {
        state.salto = null;
        if (play && play.localKaraoke) trabajando('No se pudo saltar dentro de este archivo.', true);
        else saltoServidor(target, false, null, operation.paused, operation.from);
        return;
      }
      window.setTimeout(comprobar, SALTO_ESPERA);
    };
    window.setTimeout(comprobar, SALTO_ESPERA);
  }

  /** Evidencia acotada del cierre: la auditoría nunca debe impedir liberar la imagen. */
  function registrarSalto(operation, event, reason) {
    var video = el('video'), play = state.playing;
    try {
      api('events', 'POST', { event: event, operation_id: operation.id,
        target_ms: Math.round(operation.target), reason: reason,
        position_ms: Math.min(86400000, Math.max(0,
          Math.round((play ? play.offset : 0) + (video.currentTime || 0) * 1000))),
        elapsed_ms: Math.min(86400000, Math.max(0, Date.now() - operation.desde)),
        ready_state: video.readyState, paused: video.paused,
        seeking: video.seeking, waiting: state.esperando }, function () {}, function () {});
    } catch (error) { /* el registro no bloquea la confirmación ni la recuperación */ }
  }

  /** El reloj debe avanzar en el destino: un evento o un cambio aislado no bastan.
      Samsung puede conservar waiting/seeking después de recuperar la imagen. */
  function comprobarSalto() {
    var operation = state.salto, play = state.playing, video = el('video');
    if (!operation || !play || state.dead) return;
    var ahora = Date.now(), t = video.currentTime || 0;
    if (!operation.servidor || operation.respondio !== undefined) {
      var cerca = Math.abs(t - operation.local) < SALTO_TOLERANCIA;
      if (operation.paused) {
        // En pausa no hay evidencia de avance: conservar las señales de disponibilidad.
        if (cerca && video.paused && video.readyState >= 2 && !video.seeking && !state.esperando) {
          terminarSalto(operation, 'available'); return;
        }
      } else {
        var lapso = operation.vistoEn === undefined ? 0 : ahora - operation.vistoEn;
        var avance = operation.visto === undefined ? 0 : t - operation.visto;
        var enDestino = operation.llegada === undefined ? cerca :
          t >= operation.local - SALTO_TOLERANCIA &&
          t <= operation.local + SALTO_TOLERANCIA + Math.max(0, ahora - operation.llegada) / 1000;
        if (!video.paused && video.readyState >= 2 && enDestino) {
          if (operation.llegada === undefined) operation.llegada = ahora;
          if (lapso > 0) {
            var continuo = avance > 0 && avance <= lapso / 1000 * 1.5 + 0.1;
            operation.muestras = continuo ? (operation.muestras || 0) + 1 : 0;
            operation.avance = continuo ? (operation.avance || 0) + avance : 0;
            if (operation.muestras >= 2 && operation.avance >= 0.5) {
              terminarSalto(operation, 'clock'); return;
            }
          }
        } else {
          operation.llegada = undefined; operation.muestras = 0; operation.avance = 0;
        }
        // Dos callbacks en el mismo instante no cuentan como dos muestras.
        if (operation.vistoEn === undefined || lapso > 0) {
          operation.visto = t; operation.vistoEn = ahora;
        }
      }
    }
    var respondio = operation.respondio !== undefined;
    if (ahora - (respondio ? operation.respondio : operation.desde) > (respondio ? ARRANQUE_MAX : SALTO_MAX)) {
      falloSalto(operation, 'La operación no terminó a tiempo.', 0,
        { recovery_required: respondio }, 'timeout');
      if (operation.request && operation.request.abort) operation.request.abort();
    }
  }

  function terminarSalto(operation, reason) {
    if (state.salto !== operation) return;
    registrarSalto(operation, 'playback_seek_completed', reason);
    state.salto = null; state.audio.switching = false;
    state.esperando = false;
    if (state.playing) {
      state.playing.posicionSinConfirmar = false;
      // seeking es de solo lectura. Esta confirmación vale hasta la próxima búsqueda.
      state.playing.saltoConfirmado = true;
      state.playing.ultimaPosicion = posicion();
    }
    trabajando(''); el('player-brand').hidden = true;
    state.avanceEn = Date.now(); touchControls();
    paintSubtitle();
  }

  function falloSalto(operation, mensaje, status, data, reason) {
    if (state.salto !== operation) return;
    registrarSalto(operation, 'playback_seek_failed', reason || 'request');
    state.salto = null; state.audio.switching = false;
    if (status === 410) { siguienteFuente(mensaje, status); return; }
    // Una lista reemplazada no permite continuar con la URL anterior. Recuperar una
    // sola vez desde el último punto confirmado; después dejar un reintento explícito.
    if (!operation.rescate && (operation.respondio || status === 0 || (data && data.recovery_required))) {
      saltoServidor(operation.from, true, null, operation.paused, operation.from);
      return;
    }
    if (operation.rescate || operation.respondio) {
      trabajando('No se pudo recuperar la imagen. Pulsa Volver y retoma el video.', true);
      try { el('video').pause(); } catch (error) { /* detener los reintentos del televisor */ }
    } else {
      if (!operation.paused) try { el('video').play(); } catch (error) { /* esperar al video */ }
      trabajando('No se pudo saltar a ese punto. Se conserva el punto anterior.', true);
    }
    state.avanceEn = Date.now();
  }

  /* Fuera de lo convertido —o cuando el televisor ignoró la orden— manda el equipo:
     devuelve una lista desde ese punto y conserva el aviso hasta confirmar imagen. */
  function saltoServidor(target, rescate, audioIndex, paused, from) {
    resetSubtitleClock(audioIndex !== undefined && audioIndex !== null ? 'audio' : 'seek');
    var play = state.playing, video = el('video');
    if (!play) return;
    if (play.trace) { play.trace.stableMs = 0; play.trace.stableSent = false; }
    var gen = state.generacion;
    var operation = { id: ++state.saltoId, target: target, desde: Date.now(), servidor: true,
      rescate: !!rescate, paused: paused === undefined ? video.paused : paused,
      from: from === undefined ? (play.ultimaPosicion || posicion()) : from };
    play.saltoConfirmado = false;
    state.salto = operation;
    trabajando(audioIndex !== undefined && audioIndex !== null ? 'Cambiando el audio…' :
      (rescate ? 'Recuperando la señal…' : 'Saltando a ' + clock(target) + '…'));
    var body = { source_id: play.source, position_ms: Math.round(target),
                 force_adapted: true, operation_id: operation.id };
    if (audioIndex !== undefined && audioIndex !== null) body.audio_stream_index = audioIndex;
    try { video.pause(); } catch (error) { /* la operación lógica ya bloquea los rescates */ }
    operation.request = api('playback/cast', 'POST', body, function (moved) {
      if (gen !== state.generacion || state.playing !== play || state.salto !== operation) return;
      if (moved.audio_tracks) loadAudioTracks(moved.audio_tracks, moved.audio_stream_index, true);
      if (moved.subtitles !== undefined) {
        var preferences = preferenciasReproduccion();
        if (audioIndex !== undefined && audioIndex !== null && preferences) {
          preferences.subtitle_sync_token = null; preferences.subtitle_offset_ms = 0;
        }
        state.subs.serverPending = !!moved.subtitles_pending;
        loadSubtitles(moved.subtitles, preferences, moved);
      }
      play.posicionSinConfirmar = true;
      play.offset = moved.offset_ms || 0;
      play.total = moved.duration_ms || play.total;
      play.limite = moved.seekable_ms || 0;
      // La dirección puede repetirse cuando el equipo reaprovecha lo ya convertido, y
      // con la misma cadena el televisor no siempre recarga: hay que distinguirla.
      var url = moved.stream_url;
      video.src = url + (url.indexOf('?') < 0 ? '?' : '&') + 'v=' + Date.now();
      video.load();
      startAt(video, (target - play.offset) / 1000);
      state.esperando = false;
      try { if (operation.paused) video.pause(); else video.play(); } catch (error) { /* esperar al video */ }
      state.ultimoT = -1; state.avanceEn = Date.now();
      // El aviso sigue puesto: se retira cuando haya imagen de verdad, no ahora. Y a
      // partir de aquí el plazo es el de cualquier arranque, no el largo de la espera.
      operation.respondio = Date.now(); operation.visto = undefined;
      operation.local = Math.max(0, (target - play.offset) / 1000);
    }, function (mensaje, status, code, data) {
      if (gen !== state.generacion || state.playing !== play || state.salto !== operation) return;
      falloSalto(operation, mensaje, status, data);
    });
  }

  function alternar() {
    var video = el('video');
    state.chained = 0;
    if (!state.salto && state.playing && state.playing.posicionSinConfirmar && video.paused) {
      saltoServidor(state.playing.ultimaPosicion || 0, true, null, false);
      renderControles(); touchControls(); return;
    }
    if (state.salto) {
      state.salto.paused = !state.salto.paused;
      try {
        if (state.salto.paused) video.pause();
        else if (!state.salto.servidor || state.salto.respondio !== undefined) video.play();
      } catch (error) { /* la intención se aplica cuando termine la preparación */ }
      renderControles(); touchControls(); return;
    }
    if (video.paused) { try { video.play(); } catch (error) { /* nada */ } } else video.pause();
    renderControles(); touchControls();
  }

  /* ------------------------------------------------- controles y navegación */

  function botonesReproductor() {
    var local = state.playing && state.playing.localKaraoke;
    var lista = [{ id: 'restart', label: 'Reiniciar', icon: ICON.restart }];
    if (!local) {
      lista.push({ id: 'audio', label: 'Audio', icon: ICON.audio });
      lista.push({ id: 'subs', label: 'Subtítulos', icon: ICON.captions });
    }
    // Cambiar de versión tiene que estar a mano: una edición sin subtítulos, o con un
    // audio que no sirve, deja la película invisible si no hay forma de elegir otra.
    if (state.items.length > 1) lista.push({ id: 'version', label: 'Versión', icon: ICON.version });
    if (episodios().length) lista.push({ id: 'eps', label: local ? 'Canciones' : 'Episodios', icon: ICON.episode });
    return lista;
  }

  function renderControles() {
    state.botones = botonesReproductor();
    pintarAcciones(el('acciones'), state.botones, state.zone === 'buttons' ? state.button : -1,
                   'pbt', activarBoton);
    enfocarReproductor();
  }

  function activarBoton(indice) {
    state.zone = 'buttons'; state.button = indice;
    var boton = state.botones[indice];
    if (!boton) return;
    if (boton.id === 'restart') { state.zone = 'none'; renderControles(); saltarA(0); return; }
    if (boton.id === 'audio') { openMenu('audio'); return; }
    if (boton.id === 'subs') { openMenu('subs'); return; }
    if (boton.id === 'version') { openMenu('versiones'); return; }
    if (boton.id === 'eps') { entrarEpisodios(); }
  }

  function entrarEpisodios() {
    var todos = episodios();
    if (!todos.length) return;
    state.pseason = 0;
    for (var s = 0; s < todos.length; s++) {
      if (todos[s].id === state.playing.video) { state.pseason = indiceTemporada(todos[s].season); break; }
    }
    var lista = episodiosTemporada(state.pseason);
    el('player-strip-title').textContent = esKaraoke() ? 'Canciones' : 'Episodios';
    el('player-seasons').hidden = esKaraoke();
    el('player-strip').className = 'tira' + (esKaraoke() ? ' song-list player-song-list' : '');
    state.zone = temporadas().length > 1 ? 'seasons' : 'episodes';
    state.episode = 0;
    for (var i = 0; i < lista.length; i++) if (lista[i].id === state.playing.video) state.episode = i;
    pintarTemporadas(el('player-seasons'), state.pseason, state.zone === 'seasons',
                     'pseason', cambiarTemporadaReproductor);
    pintarTira(el('player-episodes'), lista, state.episode, 'pep', function (indice) {
      state.episode = indice; reproducirEpisodio(lista[indice]);
    }, function (indice) { state.zone = 'episodes'; state.episode = indice; enfocarReproductor(); });
    enfocarReproductor();
  }

  function cambiarTemporadaReproductor(indice, desdePuntero) {
    if (desdePuntero && state.pseason === indice && state.zone === 'seasons') return;
    state.pseason = indice; state.episode = 0;
    if (desdePuntero) state.zone = 'seasons';
    var lista = episodiosTemporada(state.pseason);
    pintarTira(el('player-episodes'), lista, -1, 'pep', function (elegido) {
      state.zone = 'episodes'; state.episode = elegido; enfocarReproductor(); reproducirEpisodio(lista[elegido]);
    }, function (elegido) { state.zone = 'episodes'; state.episode = elegido; enfocarReproductor(); });
    enfocarReproductor();
  }

  /** Marca en pantalla dónde está el foco dentro del reproductor. */
  function enfocarReproductor() {
    var barra = el('bar');
    barra.className = 'bar' + (state.zone === 'bar' ? ' focus' : '');
    el('scrub-time').hidden = state.zone !== 'bar';
    el('player-strip').hidden = state.zone !== 'episodes' && state.zone !== 'seasons';
    pintarAcciones(el('acciones'), state.botones, state.zone === 'buttons' ? state.button : -1,
                   'pbt', activarBoton);
    pintarTemporadas(el('player-seasons'), state.pseason, state.zone === 'seasons',
                     'pseason', cambiarTemporadaReproductor);
    var previo = document.querySelector('#player-episodes .ep.focus');
    if (previo) previo.className = previo.className.replace(' focus', '');
    if (state.zone === 'episodes') {
      var caja = el('pep' + state.episode);
      if (caja) {
        caja.className += ' focus';
        if (esKaraoke()) desplazarListaCanciones(el('player-episodes'), caja);
        else el('player-episodes').style.transform = 'translateX(' + (10 - caja.offsetLeft) + 'px)';
      }
    }
    if (state.zone !== 'none') touchControls();
    pintarBarra();
  }

  function entrarBarra() {
    detenerAvance();
    state.zone = 'bar';
    state.scrub = posicion();
    state.scrubMoved = false;
    enfocarReproductor();
  }

  /* Mover el punto no adelanta el video: solo muestra a dónde iría. El salto ocurre
     al confirmar con OK, que es lo que evita el caos de saltar en cada pulsación. */
  function moverScrub(delta) {
    var total = duracion();
    state.scrub = (state.scrubMoved ? state.scrub : posicion()) + delta;
    state.scrubMoved = true;
    if (state.scrub < 0) state.scrub = 0;
    if (total) state.scrub = Math.min(state.scrub, Math.max(0, total - 2000));
    touchControls();
    pintarBarra();
  }

  function detenerAvance() {
    var hold = state.scrubHold;
    state.scrubHold = null;
    if (hold) window.clearInterval(hold.timer);
    if (hold && state.screen === 'player') pintarBarra();
  }

  function iniciarAvance(code, direction) {
    var play = state.playing, total = duracion();
    if (!play || play.live || state.dead || play.arrancando || state.salto || state.audio.switching ||
        !total || !isFinite(total)) return;
    // Los keydown repetidos solo mantienen el mismo gesto; nunca añaden otro salto.
    if (state.scrubHold && state.scrubHold.code === code) return;
    detenerAvance();
    if (state.zone !== 'bar') entrarBarra();
    var previous = state.scrub;
    moverScrub(direction * PASO_MS);
    if (state.scrub === previous) return;
    var hold = { code: code, direction: direction, start: Date.now(), last: Date.now(), speed: 1,
                 generation: state.generacion, play: play, timer: null };
    state.scrubHold = hold;
    hold.timer = window.setInterval(function () {
      if (state.scrubHold !== hold) return;
      if (hold.generation !== state.generacion || state.playing !== hold.play || state.screen !== 'player' ||
          state.zone !== 'bar' || state.menu >= 0 || state.notice || state.connectionLost ||
          state.salto || state.audio.switching) { detenerAvance(); return; }
      var now = Date.now(), elapsed = now - hold.start;
      if (elapsed < AVANCE_ESPERA) return;
      hold.speed = elapsed >= AVANCE_10X ? 10 : elapsed >= AVANCE_6X ? 6 : 3;
      // Un fotograma tardío no debe mandar el cursor varios minutos más allá.
      var dt = Math.max(0, Math.min(250, now - Math.max(hold.last, hold.start + AVANCE_ESPERA)));
      hold.last = now;
      moverScrub(hold.direction * hold.speed * PASO_MS * dt / 1000);
      if (state.scrub <= 0 || state.scrub >= Math.max(0, duracion() - 2000)) detenerAvance();
    }, AVANCE_TICK);
  }

  function onKeyUp(event) {
    if (state.scrubHold && state.scrubHold.code === event.keyCode) detenerAvance();
  }

  function pintarBarra() {
    if (!state.playing) return;
    var total = duracion();
    // El foco por sí solo no congela el reloj: el destino se fija al moverlo.
    var preview = state.zone === 'bar' && state.scrubMoved;
    var shown = state.salto ? state.salto.target : (preview ? state.scrub : posicion());
    var percent = total ? Math.min(100, shown * 100 / total) : 0;
    el('elapsed').textContent = clock(shown);
    el('total').textContent = total ? clock(total) : 'en directo';
    el('bar-fill').style.width = percent + '%';
    el('bar-dot').style.left = percent + '%';
    if (state.zone === 'bar') {
      var label = el('scrub-time'), delta = preview ? Math.round((state.scrub - posicion()) / 1000) : 0;
      label.style.left = Math.max(12, Math.min(88, percent)) + '%';
      label.textContent = clock(shown);
      if (delta) label.textContent += delta > 0 ? '  +' + delta + 's' : '  ' + delta + 's';
      var hold = state.scrubHold;
      if (hold && hold.speed > 1) label.textContent += '  ' + (hold.direction > 0 ? '» ' : '« ') + hold.speed + '×';
    }
    var ready = total ? Math.min(100, (state.playing.offset + ventana()) * 100 / total) : 0;
    el('bar-ready').style.width = ready + '%';
  }

  /* --------------------------------------------------------- subtítulos */

  /* Se dibujan aquí y no con <track>: el navegador de un televisor con años encima
     no siempre pinta WebVTT, y haciéndolo a mano el desfase se corrige al vuelo. */
  function stampMs(text) {
    var full = /(\d+):(\d+):(\d+)[.,](\d+)/.exec(text);
    if (full) return (+full[1]) * 3600000 + (+full[2]) * 60000 + (+full[3]) * 1000 + (+full[4]);
    var short = /(\d+):(\d+)[.,](\d+)/.exec(text);
    if (short) return (+short[1]) * 60000 + (+short[2]) * 1000 + (+short[3]);
    return 0;
  }

  function parseVtt(text) {
    var lines = text.replace(/\r\n/g, '\n').replace(/\r/g, '\n').split('\n');
    var cues = [], buffer = [], start = 0, end = 0;
    function flush() {
      if (buffer.length && end > start) cues.push({ start: start, end: end, text: buffer.join('\n') });
      buffer = [];
    }
    for (var i = 0; i < lines.length; i++) {
      var line = lines[i];
      if (line.indexOf('-->') > 0) {
        var parts = line.split('-->');
        start = stampMs(parts[0]); end = stampMs(parts[1]);
        buffer = [];                       // el número de cue queda descartado aquí
      } else if (!line.replace(/\s/g, '')) flush();
      else buffer.push(line.replace(/<[^>]*>/g, ''));
    }
    flush();
    return cues;
  }

  function pedirSubtitulos(avisar, otraBusqueda) {
    var play = state.playing;
    if (!play || !play.source || state.buscandoSubs) return;
    state.buscandoSubs = true;
    var gen = state.generacion, fuente = play.source, requestAudio = state.audio.track;
    if (avisar) trabajando('Buscando subtítulos…');
    if (otraBusqueda) {
      state.subs.pollDeadline = 0; state.subs.serverPending = true;
      state.subs.preparationState = 'searching';
    }
    api('playback/subtitles/' + fuente + (otraBusqueda ? '/retry' : ''),
        otraBusqueda ? 'POST' : 'GET', otraBusqueda ? {} : null, function (data) {
      state.buscandoSubs = false;
      if (gen !== state.generacion || requestAudio !== state.audio.track || !state.playing ||
          (state.playing.source || state.playing.pendingSource) !== fuente) return;
      var lista = subtitleOptions(data.items || [], data);
      state.subs.serverPending = !!data.pending;
      state.subs.preparationState = data.state || null;
      if (!lista.length) {
        state.subs.status = data.message || (data.pending ? 'Buscando subtítulos para esta versión…' :
          'No encontramos subtítulos compatibles. Puedes volver a buscar.');
        if (data.pending) {
          if (avisar) trabajando('');
          prepararSubtitulos();
          if (state.menu >= 0) renderMenu();
          return;
        }
        state.subs.pollDeadline = 0;
        // Un fallo final retira las candidatas sin apagar una pista válida que ya suena.
        if (!state.subs.cues.length) { state.subs.list = []; state.subs.track = -1; }
        if (avisar) { trabajando(state.subs.status, true);
                      window.setTimeout(function () { trabajando(''); }, 5000); }
        if (state.menu >= 0) renderMenu();
        return;
      }
      loadSubtitles(lista, state.subs.restore || preferenciasReproduccion(), data);
      if (data.pending) prepararSubtitulos();
      if (avisar) trabajando('');
      if (state.menu >= 0) renderMenu();
    }, function () {
      state.buscandoSubs = false;
      if (gen !== state.generacion || requestAudio !== state.audio.track) return;
      state.subs.serverPending = false; state.subs.preparationState = 'failed';
      state.subs.status = 'No se pudo buscar subtítulos. Puedes volver a buscar.';
      if (avisar) { trabajando('No se pudo buscar subtítulos.', true);
                    window.setTimeout(function () { trabajando(''); }, 5000); }
      if (state.menu >= 0) renderMenu();
    });
  }

  function subtitleOptions(list, preparation) {
    var result = (list || []).slice(), extra = preparation &&
      (preparation.subtitle_alternatives || preparation.alternatives) || [];
    for (var a = 0; a < extra.length; a++) {
      var duplicate = false;
      for (var b = 0; b < result.length; b++) if (result[b].key === extra[a].key) duplicate = true;
      if (!duplicate) result.push(extra[a]);
    }
    return result;
  }

  /* Inglés confirmado: usar la primera alternativa admitida por el servidor cuando
     termine la búsqueda. Mantiene su condición de sincronía sin comprobar. */
  function seleccionarSubtituloPorDefecto() {
    var play = state.playing, first = state.subs.list[0], english = false;
    if (!play || !play.source || (play.pendingSource && play.pendingSource !== play.source) ||
        play.live || play.localKaraoke || state.subs.serverPending || state.subs.disabled ||
        state.subs.track >= 0 || !first || !first.manual_only || !first.key || !state.audio.inspected) return;
    for (var a = 0; a < state.audio.list.length; a++) {
      if (Number(state.audio.list[a].stream_index) === state.audio.track) {
        english = /^(en|eng)([-_]|$)/i.test(state.audio.list[a].language || ''); break;
      }
    }
    if (!english) return;
    state.subs.track = 0; state.subs.sync = 0; state.subs.manual = true; state.subs.restore = null;
    state.subs.manualAudio = state.audio.track; state.subs.manualSource = play.source;
    fetchCues(0);
    if (window.console && window.console.info) window.console.info('Subtítulo recomendado seleccionado para audio inglés; sincronía sin comprobar.');
  }

  function loadSubtitles(list, preferences, preparation) {
    list = subtitleOptions(list, preparation);
    var previous = state.subs.list[state.subs.track];
    // Un resultado del VAD no reemplaza la elección manual durante la película.
    if (previous && previous.manual_only && state.subs.manual && state.subs.manualAudio === state.audio.track &&
        state.playing && state.subs.manualSource === state.playing.source) {
      for (var p = 0; p < list.length; p++) if (list[p].key === previous.key) {
        list[p] = previous; state.subs.list = list; state.subs.track = p;
        state.subs.preparationState = preparation && (preparation.subtitle_state || preparation.state);
        if (!state.subs.serverPending) state.subs.pollDeadline = 0;
        return;
      }
    }
    resetSubtitleClock(state.subs.render && state.subs.render.first ? state.subs.render.context : 'start');
    state.subs.requestId++; state.subs.manualRevision++;
    state.subs.syncRequestId++;
    if (state.subs.autoTimer) window.clearTimeout(state.subs.autoTimer);
    state.subs.autoTimer = null; state.subs.autoAttempts = {};
    state.subs.list = list || [];
    state.subs.cues = []; state.subs.sync = 0; state.subs.syncing = false;
    state.subs.manual = false; state.subs.disabled = false;
    state.subs.restore = preferences || null;
    state.subs.status = state.subs.list.length ? 'cargando' : 'esta edición no ofrece subtítulos';
    state.subs.preparationState = preparation ? (preparation.subtitle_state || preparation.state || null) : null;
    if (!state.subs.serverPending) state.subs.pollDeadline = 0;
    state.subs.track = -1;
    for (var candidate = 0; candidate < state.subs.list.length; candidate++) {
      if (!state.subs.list[candidate].manual_only) { state.subs.track = candidate; break; }
    }
    if (preferences) {
      if (preferences.subtitle_style) state.subs.style = preferences.subtitle_style;
      state.subs.disabled = !!preferences.subtitle_disabled;
      if (state.subs.disabled) { state.subs.track = -1; state.subs.restore = null; }
      else if (state.subs.list.length || preferences.subtitle_sync_token) {
        var found = -1;
        for (var i = 0; i < state.subs.list.length; i++) {
          if (state.subs.list[i].key && state.subs.list[i].key === preferences.subtitle_key &&
              (found < 0 || state.subs.list[i].verified)) found = i;
        }
        // Las respuestas nuevas ya vinculan la pista al archivo/audio real. Una
        // preferencia antigua basada en etiquetas no puede sustituir esa decisión.
        if (!(state.subs.list[0] && state.subs.list[0].verification && state.subs.list[0].verification.revision >= 4) &&
            preferences.subtitle_sync_token && (preferences.subtitle_verified ||
            !(state.subs.list[found >= 0 ? found : 0] || {}).validation_required)) {
          state.subs.list.push({ key: preferences.subtitle_key, sync_token: preferences.subtitle_sync_token,
            url: '/subtitles/synced/' + preferences.subtitle_sync_token + '.vtt',
            label: 'Español · sincronizado automáticamente', automatic: true,
            verified: !!preferences.subtitle_verified, validation_required: !!preferences.subtitle_verified });
          found = state.subs.list.length - 1;
        }
        // Un ajuste de otra pista no debe descalibrar la alternativa disponible.
        if (found >= 0 && !state.subs.list[found].manual_only) {
          state.subs.track = found; state.subs.sync = preferences.subtitle_offset_ms || 0;
          state.subs.manual = !!preferences.subtitle_manual;
        }
        state.subs.restore = null;
      }
    }
    el('subtitle').textContent = '';
    if (state.subs.track >= 0) fetchCues(state.subs.track);
    else seleccionarSubtituloPorDefecto();
    if (state.subs.track < 0 && preparation) state.subs.status =
      preparation.subtitle_message || preparation.message || state.subs.status;
    applySubtitleStyle(el('controls').hidden === false);
  }

  function loadAudioTracks(list, selected, inspected) {
    var previous = state.audio.track;
    state.audio.list = list || [];
    state.audio.inspected = inspected !== false;
    state.audio.loading = false;
    state.audio.error = false;
    if (!state.audio.inspected) state.audio.switching = false;
    state.audio.track = selected === undefined || selected === null ? -1 : Number(selected);
    if (state.audio.track < 0) {
      for (var i = 0; i < state.audio.list.length; i++) {
        if (state.audio.list[i].selected) { state.audio.track = Number(state.audio.list[i].stream_index); break; }
      }
    }
    state.audio.status = state.audio.list.length > 1 ? state.audio.list.length + ' pistas disponibles' :
      state.audio.list.length === 1 ? 'Sin alternativas disponibles' :
      state.audio.inspected ? 'Sin alternativas disponibles' : 'Preparando opciones de audio…';
    if (previous >= 0 && previous !== state.audio.track) {
      resetSubtitleClock('audio');
      state.subs.serverPending = true;
      state.subs.cues = []; el('subtitle').textContent = '';
      var chosen = state.subs.list[state.subs.track];
      if (chosen && chosen.validation_required && chosen.automatic) {
        for (var i = 0; i < state.subs.list.length; i++) {
          if (state.subs.list[i].source_id && state.subs.list[i].key === chosen.key) {
            state.subs.track = i; state.subs.sync = 0; state.subs.manual = false;
            fetchCues(i); break;
          }
        }
      }
    }
    seleccionarSubtituloPorDefecto();
  }

  function pedirAudio(sourceId) {
    var play = state.playing;
    var fuente = sourceId || (play && (play.source || play.pendingSource));
    if (!play || !fuente || state.audio.loading || (state.audio.inspected && !state.audio.error)) return;
    var gen = state.generacion;
    state.audio.loading = true; state.audio.error = false; state.audio.status = 'Preparando opciones de audio…';
    if (state.menu >= 0) renderMenu();
    api('playback/audio/' + fuente, 'GET', null, function (data) {
      state.audio.loading = false;
      if (gen !== state.generacion || !state.playing ||
          (state.playing.source || state.playing.pendingSource) !== fuente) return;
      // Una inspección iniciada antes del cast puede llegar después. El resultado
      // del cast ya confirma qué pista suena y tiene prioridad sobre esa respuesta.
      var selected = state.playing.source === fuente && state.audio.inspected
        ? state.audio.track : data.audio_stream_index;
      loadAudioTracks(data.items || [], selected,true);
      if (state.menu >= 0) renderMenu();
    }, function (mensaje) {
      state.audio.loading = false;
      if (gen !== state.generacion) return;
      state.audio.inspected = true;
      state.audio.error = true;
      state.audio.status = 'No se pudieron leer las pistas de audio';
      if (state.menu >= 0) renderMenu();
    });
  }

  function cambiarAudio(streamIndex) {
    var play = state.playing;
    if (!play || play.localKaraoke || streamIndex === state.audio.track) return;
    if (play.arrancando || !play.source) { trabajando('Espera a que comience el video…'); return; }
    if (state.salto || state.audio.switching) { trabajando('Espera: todavía se está saltando…'); return; }
    state.audio.switching = true; closeMenu();
    // Audio y posición comparten identidad, confirmación, pausa y recuperación.
    saltoServidor(Math.round(posicion()), false, streamIndex);
  }

  function fetchCues(index) {
    var chosen = state.subs.list[index];
    var requestId = ++state.subs.requestId, gen = state.generacion;
    var requestAudio = state.audio.track;
    state.subs.cues = [];
    el('subtitle').textContent = '';
    if (!chosen) { state.subs.cues = []; state.subs.status = 'sin pista'; return; }
    // Una candidata puede descargarse/verificarse en el PC sin mostrar texto ajeno.
    if (chosen.validation_required && !chosen.verified &&
        !(chosen.manual_only && state.subs.manual && state.subs.manualAudio === state.audio.track &&
          state.playing && state.subs.manualSource === state.playing.source)) {
      state.subs.status = 'Comprobando subtítulos para esta versión…';
      prepararSubtitulos(); return;
    }
    state.subs.status = 'cargando';
    var request = new XMLHttpRequest();
    request.open('GET', chosen.url, true);
    request.timeout = 20000;
    function current() {
      return gen === state.generacion && requestId === state.subs.requestId &&
        requestAudio === state.audio.track &&
        state.subs.track === index && state.subs.list[index] === chosen;
    }
    request.onreadystatechange = function () {
      if (request.readyState !== 4 || !current()) return;
      state.subs.cues = request.status === 200 ? parseVtt(request.responseText) : [];
      if (!state.subs.cues.length && chosen.sync_token) {
        // La limpieza de caché puede retirar la copia calibrada. Recuperar la pista
        // original permite volver a validarla, sin aplicarle el desplazamiento viejo.
        for (var i = 0; i < state.subs.list.length; i++) {
          if (!state.subs.list[i].automatic && state.subs.list[i].key === chosen.key) {
            state.subs.track = i; state.subs.sync = 0; state.subs.manual = false;
            fetchCues(i); return;
          }
        }
      }
      state.subs.status = state.subs.cues.length ? state.subs.cues.length + ' líneas' : 'no se pudo leer el subtítulo';
      paintSubtitle(); prepararSubtitulos();
    };
    request.ontimeout = function () {
      if (!current()) return;
      state.subs.cues = []; state.subs.status = 'el subtítulo tardó demasiado';
    };
    request.send(null);
  }

  /* Comprueba la edición una vez en segundo plano. Ninguna comprobación reinicia el video.
     Una elección o corrección manual posterior siempre tiene prioridad. */
  function prepararSubtitulos() {
    var play = state.playing, chosen = state.subs.list[state.subs.track];
    if (play && play.source && state.subs.serverPending && !state.subs.disabled) {
      var pollKey = play.source + ':' + state.audio.track;
      if (!state.subs.pollDeadline || state.subs.pollKey !== pollKey) {
        state.subs.pollKey = pollKey; state.subs.pollDeadline = Date.now() + 12000;
      }
      if (Date.now() >= state.subs.pollDeadline) {
        state.subs.serverPending = false; state.subs.preparationState = 'failed';
        state.subs.status = 'La búsqueda terminó sin confirmar una pista. Puedes volver a buscar.';
        if (state.menu >= 0) renderMenu();
        return;
      }
      if (!state.subs.autoTimer && !state.buscandoSubs) {
        var pendingGen = state.generacion;
        state.subs.autoTimer = window.setTimeout(function () {
          state.subs.autoTimer = null;
          if (pendingGen === state.generacion) pedirSubtitulos(false);
        }, 500);
      }
      return;
    }
    // El servidor nuevo ya probó alternativas y devuelve un estado final. El
    // cliente no abre una segunda cadena de calibraciones para el mismo trabajo.
    if (state.subs.preparationState) return;
    if (!play || !play.source || play.live || play.localKaraoke || !chosen ||
        !chosen.source_id || chosen.automatic || state.subs.syncing || state.subs.autoTimer) return;
    var pending = chosen.validation_required && !chosen.verified;
    if (!pending && (!state.subs.cues.length || state.subs.sync !== 0 || state.subs.manual)) return;
    var key = play.source + ':' + chosen.source_id + ':' + state.audio.track;
    if (state.subs.autoAttempts[key]) return;
    var gen = state.generacion, revision = state.subs.manualRevision;
    state.subs.autoTimer = window.setTimeout(function () {
      state.subs.autoTimer = null;
      if (gen !== state.generacion || !state.playing || state.playing.source !== play.source ||
          state.subs.list[state.subs.track] !== chosen || revision !== state.subs.manualRevision) return;
      state.subs.autoAttempts[key] = true;
      sincronizarSubtitulo(true);
    }, 2500);
  }

  /* El equipo crea otra pista: la original queda disponible por si la coincidencia de
     voz no es suficientemente buena. Durante el cálculo la película sigue corriendo. */
  function sincronizarSubtitulo(automatic) {
    var play = state.playing, chosen = state.subs.list[state.subs.track];
    if (!play || !chosen || !chosen.source_id || state.subs.syncing) return;
    var gen = state.generacion, fuente = play.source, revision = state.subs.manualRevision;
    var audioTrack = state.audio.track, syncRequestId = ++state.subs.syncRequestId;
    state.subs.syncing = true; state.subs.status = 'sincronizando automáticamente…';
    if (!automatic) { closeMenu(); trabajando('Sincronizando subtítulos…'); }
    api('playback/subtitles/sync', 'POST', {
      source_id: fuente, subtitle_source_id: chosen.source_id, recover: !!automatic
    }, function (data) {
      if (gen !== state.generacion || !state.playing || state.playing.source !== fuente ||
          syncRequestId !== state.subs.syncRequestId) return;
      state.subs.syncing = false;
      if (state.subs.list[state.subs.track] !== chosen || revision !== state.subs.manualRevision ||
          audioTrack !== state.audio.track) {
        delete state.subs.autoAttempts[fuente + ':' + chosen.source_id + ':' + audioTrack];
        prepararSubtitulos(); return;
      }
      if (data.pending) {
        state.subs.serverPending = true; state.subs.preparationState = data.state || 'searching';
        state.subs.status = data.message || 'Buscando otras alternativas…';
        prepararSubtitulos(); return;
      }
      if (data.changed && data.item) {
        data.item.key = data.item.key || chosen.key || null;
        state.subs.list.push(data.item); state.subs.track = state.subs.list.length - 1;
        state.subs.sync = 0; fetchCues(state.subs.track);
        saveProgress(false);
      } else if (chosen.validation_required && !chosen.verified &&
          (!chosen.manual_only || /mismatch$|identity_unverified/.test(data.reason || ''))) {
        state.subs.cues = []; el('subtitle').textContent = '';
      }
      state.subs.status = data.message || 'subtítulos sincronizados';
      if (!automatic) {
        trabajando(state.subs.status, true);
        window.setTimeout(function () { trabajando(''); }, 5000);
      }
    }, function (mensaje) {
      if (gen !== state.generacion || syncRequestId !== state.subs.syncRequestId) return;
      state.subs.syncing = false;
      if (state.subs.list[state.subs.track] !== chosen || revision !== state.subs.manualRevision) return;
      state.subs.status = mensaje || 'no se pudieron sincronizar los subtítulos';
      if (chosen.validation_required && !chosen.verified && !chosen.manual_only) {
        state.subs.cues = []; el('subtitle').textContent = '';
      }
      if (!automatic) {
        trabajando(state.subs.status, true);
        window.setTimeout(function () { trabajando(''); }, 5000);
      }
    });
  }

  function flushSubtitleMeasurement() {
    var render = state.subs.render, chosen = state.subs.list[state.subs.track];
    if (!render || !render.samples || !chosen) return;
    registrarReproduccion('subtitle_render', { subtitle_key: chosen.key || null,
      subtitle_token: chosen.sync_token || null, render_context: render.context,
      render_samples: render.samples, render_lag_ms: render.max,
      render_total_lag_ms: render.total });
    render.samples = 0; render.max = 0; render.total = 0; render.sentAt = Date.now();
  }

  function resetSubtitleClock(context) {
    flushSubtitleMeasurement();
    state.subs.render = { context: context, readyAt: Date.now(), previousAt: null,
      cue: null, samples: 0, max: 0, total: 0, sentAt: Date.now(), first: true };
  }

  function paintSubtitle() {
    var box = el('subtitle'), play = state.playing;
    if (!play || state.subs.track < 0 || !state.subs.cues.length) {
      if (box.textContent) box.textContent = '';
      return;
    }
    // Durante una transición el reloj aún pertenece a la lista anterior.
    if (state.salto || play.arrancando || play.posicionSinConfirmar ||
        (el('video').seeking && !play.saltoConfirmado)) {
      if (box.textContent) box.textContent = '';
      return;
    }
    var at = posicion() + state.subs.sync;
    var text = '', active = null;
    for (var i = 0; i < state.subs.cues.length; i++) {
      var cue = state.subs.cues[i];
      if (cue.start > at) break;
      if (at <= cue.end) { text = cue.text; active = cue; break; }
    }
    if (box.textContent !== text) box.textContent = text;
    if (!state.subs.render) resetSubtitleClock('start');
    var render = state.subs.render;
    if (active && render.cue !== active) {
      // Cruce normal: diferencia con el inicio de la entrada. Activación en mitad
      // de una frase: demora desde la transición, nunca desde el inicio de esa frase.
      var crossing = render.previousAt !== null && render.previousAt < active.start &&
        at >= render.previousAt && at - render.previousAt < 1000;
      var lag = Math.round(Math.max(0, Math.min(60000,
        crossing ? at - active.start : render.first ? Date.now() - render.readyAt : 0)));
      render.samples++; render.total += lag; render.max = Math.max(render.max, lag);
      render.cue = active;
      if (render.first) {
        flushSubtitleMeasurement(); render.first = false; render.context = 'continuous';
      }
    }
    render.previousAt = at;
    if (render.samples && Date.now() - render.sentAt >= 30000) flushSubtitleMeasurement();
  }

  function applySubtitleStyle(raised) {
    el('subtitle').className = 'subtitle subtitle-' + state.subs.style + (raised ? ' raised' : '');
  }

  /* Audio embebido y subtítulos externos son responsabilidades distintas. */

  function menuOptions() {
    if (state.menuTipo === 'versiones') return opcionesVersion();
    var options = [];
    if (state.menuTipo === 'audio') {
      for (var a = 0; a < state.audio.list.length; a++) {
        options.push({ label: state.audio.list[a].label || ('Pista ' + (a + 1)),
                       audio: Number(state.audio.list[a].stream_index) });
      }
      if (!options.length) options.push({ label: state.audio.loading || !state.audio.inspected
        ? 'Preparando opciones de audio…' : state.audio.error ? state.audio.status : 'Sin alternativas disponibles', disabled: true });
      if (state.audio.error) options.push({ label: 'Reintentar lectura de audio', buscarAudio: true });
      return options;
    }
    for (var i = 0; i < state.subs.list.length; i++) {
      options.push({ label: (state.subs.list[i].label || ('Español ' + (i + 1))) +
        (state.subs.list[i].manual_only ? ' · sincronía sin comprobar' :
          state.subs.list[i].validation_required && !state.subs.list[i].verified ? ' · sin confirmar' : ''), pick: i });
    }
    options.push({ label: 'Sin subtítulos', pick: -1 });
    options.push({ label: state.subs.serverPending ? 'Buscando alternativas…' : 'Volver a buscar subtítulos',
      buscar: true, disabled: !!state.subs.serverPending || state.buscandoSubs });
    var chosen = state.subs.list[state.subs.track];
    if (chosen && chosen.source_id) {
      options.push({ label: state.subs.syncing ? 'Sincronizando…' : 'Sincronizar automáticamente', autoSync: true });
    }
    options.push({ label: 'Atrasar el texto 0,5 s', shift: -500 });
    options.push({ label: 'Adelantar el texto 0,5 s', shift: 500 });
    options.push({ label: 'Centrar el texto', shift: 0, reset: true });
    for (var s = 0; s < CAPTION_STYLES.length; s++) {
      options.push({ label: CAPTION_STYLES[s].label, captionStyle: CAPTION_STYLES[s].id });
    }
    return options;
  }

  function numeroVersion(valor, decimales) {
    if (valor === undefined || valor === null || valor === '' || isNaN(Number(valor))) return '';
    return Number(valor).toFixed(decimales).replace('.', ',');
  }

  function idiomaVersion(valor) {
    var nombres = {
      spanish: 'Español', english: 'Inglés', hindi: 'Hindi', tamil: 'Tamil',
      telugu: 'Telugu', malayalam: 'Malayalam', bengali: 'Bengalí', french: 'Francés',
      italian: 'Italiano', german: 'Alemán', portuguese: 'Portugués', russian: 'Ruso',
      ukrainian: 'Ucraniano', polish: 'Polaco', turkish: 'Turco', korean: 'Coreano',
      japanese: 'Japonés', chinese: 'Chino', arabic: 'Árabe', dutch: 'Neerlandés',
      swedish: 'Sueco', danish: 'Danés', norwegian: 'Noruego', finnish: 'Finés',
      persian: 'Persa', thai: 'Tailandés'
    };
    return nombres[valor] || valor;
  }

  /* El algoritmo ya entregó las versiones en el orden correcto. Aquí no se repite el
     nombre de la película, el proveedor ni el release group: solo datos que ayudan a
     decidir desde la TV. */
  function opcionesVersion() {
    var options = [];
    for (var i = 0; i < state.items.length; i++) {
      var item = state.items[i];
      var velocidad = item.measured_bps
        ? numeroVersion(item.measured_bps * 8 / 1000000, 1) + ' Mbps'
        : 'No medida';
      var bitrate = item.demand_mbps
        ? numeroVersion(item.demand_mbps, 1) + ' Mbps'
        : 'No informado';
      var margen = item.margin || (item.measured_bps && item.demand_mbps
        ? item.measured_bps * 8 / 1000000 / item.demand_mbps : 0);
      var redNivel = !item.measured_bps || !margen ? 0 : margen >= 2 ? 4 : margen >= 1.5 ? 3 : margen >= 1.15 ? 2 : 1;
      var redEstado = !item.measured_bps ? 'Sin medir' : !margen ? 'Margen sin datos' : margen >= 2 ? 'Excelente' :
        margen >= 1.5 ? 'Muy buena' : margen >= 1.15 ? 'Suficiente' :
        margen >= 1 ? 'Ajustada' : 'Insuficiente';
      var alcance = item.speed_scope === 'server' ? 'Servidor medido' :
        item.speed_scope === 'provider' ? 'Promedio del proveedor' : 'Sin historial de red';
      var idiomas = [];
      for (var j = 0; j < (item.languages || []).length; j++) {
        idiomas.push(idiomaVersion(item.languages[j]));
      }
      options.push({
        resolucion: item.delivered ? item.delivered + 'p' : 'Sin datos',
        nivel: item.delivered >= 2160 ? 4 : item.delivered >= 1080 ? 3 : item.delivered >= 720 ? 2 : item.delivered ? 1 : 0,
        grado: item.delivered >= 2160 ? 'Ultra HD' : item.delivered >= 1080 ? 'Full HD' :
               item.delivered >= 720 ? 'HD' : item.delivered ? 'Definición estándar' : 'No informada',
        velocidad: velocidad,
        bitrate: bitrate,
        margen: margen ? numeroVersion(margen, 1) + '× de margen' : '',
        redNivel: redNivel,
        redEstado: redEstado,
        alcance: alcance,
        prioridad: item.ranking_tier === 0 ? '1080p con margen cómodo' :
                   item.ranking_tier === 1 ? '720p con margen cómodo' :
                   item.ranking_tier === 2 ? 'Sin medición suficiente' :
                   item.ranking_tier === 3 ? '480p con margen cómodo' :
                   item.ranking_tier === 4 ? 'Margen ajustado' :
                   item.ranking_tier === 5 ? 'Margen insuficiente' :
                   item.delivered >= 1080 ? '1080p priorizado' :
                   item.delivered >= 720 ? '720p estable' : 'Calidad básica',
        tamano: item.size_mb >= 1024 ? numeroVersion(item.size_mb / 1024, 2).replace(/,?0+$/, '') + ' GB' :
                item.size_mb ? numeroVersion(item.size_mb, 0) + ' MB' :
                item.size_gb ? numeroVersion(item.size_gb, 2).replace(/,?0+$/, '') + ' GB' : 'No informado',
        origen: item.origin === 'cine' ? 'Cine' : 'Original',
        idiomas: idiomas.length ? idiomas.join(' · ') : 'No informados',
        fuente: i
      });
    }
    return options;
  }

  function medidorCalidad(nivel) {
    var html = '<span class="quality-meter" aria-hidden="true">';
    for (var i = 1; i <= 4; i++) html += '<i' + (i <= nivel ? ' class="on"' : '') + '></i>';
    return html + '</span>';
  }

  function medidorRed(nivel) {
    var html = '<span class="network-meter" aria-hidden="true">';
    for (var i = 1; i <= 4; i++) html += '<i' + (i <= nivel ? ' class="on"' : '') + '></i>';
    return html + '</span>';
  }

  function contenidoVersion(option, indice, activa) {
    // Valores en tarjetas separadas: capacidad medida y demanda del video son
    // cifras distintas y deben poder compararse desde el sofá sin confundirlas.
    return '<span class="version-data"><span class="version-title">' +
      (indice === 0 ? 'Recomendada' : 'Alternativa ' + (indice + 1)) +
      (activa ? '<em>EN USO</em>' : '') + '</span>' +
      '<span class="version-metrics"><span class="version-metric version-quality">' +
      '<b>Imagen</b><strong>' + escape(option.resolucion) + '</strong><small>' +
      medidorCalidad(option.nivel) + escape(option.grado) + '</small></span>' +
      '<span class="version-metric version-size"><b>Archivo</b><strong>' + escape(option.tamano) +
      '</strong><small>Tamaño total</small></span>' +
      '<span class="version-metric version-demand"><b>Video</b><strong>' + escape(option.bitrate) +
      '</strong><small>Requiere de la red</small></span>' +
      '<span class="version-metric version-capacity"><b>Capacidad medida</b><strong>' +
      escape(option.velocidad) + '</strong><small>' + escape(option.alcance) + '</small></span></span>' +
      '<span class="version-network"><span class="network-summary"><b>Red</b> <strong>' +
      escape(option.redEstado) + '</strong></span>' + medidorRed(option.redNivel) +
      '<span class="network-values">' + escape(option.margen || 'Margen no disponible') + '</span></span>' +
      '<span class="version-foot"><b>' + escape(option.prioridad) + '</b> · ' +
      escape(option.origen) + ' · ' + escape(option.idiomas) + '</span></span>';
  }

  function openMenu(tipo) {
    if (!state.playing) return;
    state.menuTipo = tipo || 'subs';
    state.menu = 0; renderMenu(); el('subs').hidden = false;
  }

  function closeMenu() { state.menu = -1; el('subs').hidden = true; }

  function renderMenu() {
    var options = menuOptions(), host = el('subs');
    var versiones = state.menuTipo === 'versiones', audio = state.menuTipo === 'audio';
    host.className = 'menu' + (versiones ? ' menu-versiones' : '');
    host.innerHTML = '';
    var title = document.createElement('h2');
    title.textContent = versiones ? 'Versión del video' : audio ? 'Audio' : 'Subtítulos';
    var sub = document.createElement('div'); sub.className = 'sub';
    var audioActual = '';
    for (var a = 0; a < state.audio.list.length; a++) {
      if (Number(state.audio.list[a].stream_index) === state.audio.track) {
        audioActual = state.audio.list[a].label || ('Pista ' + (a + 1)); break;
      }
    }
    sub.textContent = versiones ? 'Fuentes válidas: 1080p estable, luego 720p y alternativas sin medir' :
      audio ? (audioActual ? 'En reproducción: ' + audioActual +
        (state.audio.list.length < 2 ? ' · Sin alternativas disponibles' : '') : state.audio.status) :
        (state.subs.status || (state.buscandoSubs ? 'buscando subtítulos…' : 'sin pista')) +
        (state.subs.sync ? '  ·  desfase ' + (state.subs.sync > 0 ? '+' : '') +
         (state.subs.sync / 1000).toFixed(1) + ' s' : '');
    host.appendChild(title); host.appendChild(sub);
    for (var i = 0; i < options.length; i++) {
      var line = document.createElement('div');
      var active = versiones
        ? options[i].fuente === state.attempt
        : (options[i].audio !== undefined ? options[i].audio === state.audio.track :
           options[i].captionStyle !== undefined ? options[i].captionStyle === state.subs.style :
           options[i].pick !== undefined && options[i].pick === state.subs.track);
      line.className = 'menu-item' + (active ? ' on' : '') + (i === state.menu ? ' focus' : '');
      line.innerHTML = ICON.check + (versiones
        ? contenidoVersion(options[i], i, active)
        : options[i].captionStyle
          ? '<span class="caption-sample caption-sample-' + escape(options[i].captionStyle) + '">Aa</span>' +
            '<span>' + escape(options[i].label) + '</span>'
          : '<span>' + escape(options[i].label) + '</span>');
      conPunteroMenu(line, i);
      host.appendChild(line);
    }
    // Mantener a la vista lo que tiene el foco: con doce versiones, moverse a ciegas
    // por debajo del borde es lo mismo que no poder elegir.
    var elegido = host.children[state.menu + 2];
    if (elegido && elegido.offsetTop !== undefined) {
      var arriba = elegido.offsetTop - host.clientHeight / 2 + elegido.offsetHeight;
      host.scrollTop = arriba > 0 ? arriba : 0;
    }
  }

  function conPunteroMenu(linea, indice) {
    linea.style.cursor = 'pointer';
    linea.onmouseover = function () { if (state.menu !== indice) { state.menu = indice; renderMenu(); } };
    linea.onclick = function () { state.menu = indice; chooseMenu(); };
  }

  function chooseMenu() {
    var option = menuOptions()[state.menu];
    if (!option || option.disabled) return;
    if (option.fuente !== undefined) { closeMenu(); cambiarVersion(option.fuente); return; }
    if (option.audio !== undefined) { cambiarAudio(option.audio); return; }
    if (option.buscarAudio) { pedirAudio(); return; }
    if (option.buscar) { closeMenu(); pedirSubtitulos(true, true); return; }
    if (option.autoSync) { sincronizarSubtitulo(); return; }
    if (option.pick !== undefined) {
      state.subs.manualRevision++;
      state.subs.manual = true; state.subs.disabled = option.pick < 0; state.subs.restore = null;
      state.subs.manualAudio = state.audio.track;
      state.subs.manualSource = state.playing && state.playing.source;
      state.subs.track = option.pick;
      if (option.pick >= 0) fetchCues(option.pick);
      else { state.subs.requestId++; state.subs.cues = []; el('subtitle').textContent = ''; }
      saveProgress(false); closeMenu(); return;
    }
    if (option.captionStyle) {
      state.subs.style = option.captionStyle;
      try { localStorage.setItem(CAPTION_STORE, state.subs.style); } catch (error) { /* preferencia opcional */ }
      applySubtitleStyle(el('controls').hidden === false);
      saveProgress(false);
      renderMenu(); return;
    }
    state.subs.manualRevision++;
    state.subs.manual = true; state.subs.restore = null;
    state.subs.sync = option.reset ? 0 : state.subs.sync + option.shift;
    saveProgress(false);
    paintSubtitle();
    renderMenu();
  }

  /* ------------------------------------------- siguiente episodio y presencia */

  function clearNotice() {
    if (state.noticeTimer) { window.clearInterval(state.noticeTimer); state.noticeTimer = null; }
    state.notice = null; el('next').hidden = true;
  }

  function renderNotice(title, line) {
    el('next').innerHTML = '<h3>' + escape(title) + '</h3><p>' + escape(line) + '</p>';
    el('next').hidden = false;
    el('next').style.cursor = 'pointer';
    el('next').onclick = function () {
      var following = state.notice ? (state.notice.following || nextEpisode()) : null;
      clearNotice();
      if (following) { state.chained = 0; playFollowing(following); }
    };
  }

  function nextEpisode() {
    var lista = episodios(), current = -1;
    for (var i = 0; i < lista.length; i++) if (lista[i].id === state.playing.video) current = i;
    return current >= 0 && current + 1 < lista.length ? lista[current + 1] : null;
  }

  function whenFinished() {
    if (state.salto || (state.playing && state.playing.posicionSinConfirmar)) return;
    var play = state.playing, total = duracion(), actual = posicion();
    // Samsung puede anunciar `ended` si una entrega se corta. Con una duración conocida,
    // solo es un final real dentro del último 5 %, con un máximo de dos minutos.
    if (total > 0 && isFinite(total) && actual < total - Math.min(120000, total * 0.05)) {
      // Algunos motores vuelven su reloj a cero al perder la fuente. La última posición
      // que avanzó de verdad es el punto seguro para guardar y rescatar.
      if (play && play.ultimaPosicion > actual) actual = play.ultimaPosicion;
      saveProgress(false, actual);
      whenBroken();
      return;
    }
    saveProgress(true);
    registrarReproduccion('ended', {});
    if (play && play.trace) play.trace.closed = true;
    var following = nextEpisode();
    if (!following) { note(esKaraoke() ? 'Terminaste las canciones de este artista.' : 'Terminaste los episodios disponibles.'); return; }
    if (state.chained >= CHAIN_LIMIT) { askPresence(following); return; }
    var name = esKaraoke() ? following.name : 'T' + following.season + ' E' + following.episode;
    countdown(NEXT_SECONDS, esKaraoke() ? 'Siguiente canción' : 'Siguiente episodio', name + ' empieza en ', function () {
      state.chained++; playFollowing(following);
    }, following);
  }

  function askPresence(following) {
    countdown(PRESENCE_SECONDS, '¿Sigues ahí?', esKaraoke()
      ? 'Van tres canciones seguidas. Se detiene en '
      : 'Van tres episodios seguidos. Se detiene en ', function () {
      clearNotice(); stopPlayback(true);
    }, following);
  }

  function countdown(seconds, title, prefix, whenZero, following) {
    clearNotice();
    state.notice = { left: seconds, act: whenZero, following: following || null };
    var paint = function () {
      renderNotice(title, prefix + state.notice.left + ' s  ·  OK acepta, Volver cancela');
    };
    paint();
    state.noticeTimer = window.setInterval(function () {
      if (!state.notice) return;
      state.notice.left--;
      if (state.notice.left <= 0) { var act = state.notice.act; clearNotice(); act(); return; }
      paint();
    }, 1000);
  }

  function playFollowing(following) {
    clearNotice();
    state.playing.video = following.id;
    if (esKaraoke()) playKaraokeLocal(following);
    else playVideo('series', following.id, tituloReproduccion(following.id));
  }

  /* Un corte a mitad puede ser el equipo soltando la sesión por inactividad, o una
     fuente que se cayó. Lo primero se avisa; lo segundo se resuelve solo. */
  function whenBroken() {
    var play = state.playing;
    if (!play || state.dead) return;
    if (play.localKaraoke) {
      state.salto = null; trabajando('');
      note('No se pudo reproducir este archivo. Comprueba que sea MP4 con video H.264 y audio AAC.');
      return;
    }
    if (!play.source) return;
    // Un fallo durante un salto casi siempre es la lista vieja que dejó de existir:
    // el rescate ya está en marcha y cambiar de fuente ahora lo estropearía.
    if (state.salto && state.salto.servidor) return;
    if (play.consultandoError) return;
    registrarReproduccion('recovery_started', { error_code: 'player_error' });
    if (play.trace) { play.trace.stableMs = 0; play.trace.stableSent = false; play.trace.recovering = true; }
    play.consultandoError = true;
    var gen = state.generacion;
    state.salto = null;
    var rescate = play.ultimaPosicion || posicion();
    trabajando('Recuperando la señal…');
    api('playback/status', 'GET', null, function (data) {
      play.consultandoError = false;
      if (gen !== state.generacion || state.playing !== play || state.dead || state.salto) return;
      if (!data.active) {
        trabajando('');
        note('La reproducción se interrumpió en ' + clock(rescate) +
             '. Pulsa Volver y ábrela otra vez para continuar.');
        return;
      }
      // La sesión sigue viva: lo que se rompió es la lista. Pedirla otra vez desde
      // donde íbamos recupera la imagen sin perder ni la edición ni el minuto.
      if (state.playing.rescates < RESCATES_MAX) {
        state.playing.rescates++;
        saltoServidor(rescate, true);
      } else {
        registrarReproduccion('error', { error_code: 'player_error' });
        siguienteFuente('', 0, 'player_error');
      }
    }, function () {
      play.consultandoError = false;
      if (gen === state.generacion && state.playing === play && !state.salto) siguienteFuente();
    });
  }

  /* ------------------------------------------------------------- pantalla */

  function clock(ms) {
    if (!ms || ms < 0) ms = 0;
    var total = Math.floor(ms / 1000), hours = Math.floor(total / 3600);
    var minutes = Math.floor((total % 3600) / 60), seconds = total % 60;
    var pad = function (value) { return (value < 10 ? '0' : '') + value; };
    return hours ? hours + ':' + pad(minutes) + ':' + pad(seconds) : minutes + ':' + pad(seconds);
  }

  function touchControls() {
    state.controlsUntil = Date.now() + 5000;
    // Los capítulos usan el mismo espacio inferior; al recorrerlos manda la lista.
    el('controls').hidden = state.zone === 'episodes' || state.zone === 'seasons';
  }

  /** Ocultar también el foco: una barra invisible no puede capturar el siguiente botón. */
  function ocultarControles() {
    detenerAvance();
    state.controlsUntil = 0; state.zone = 'none';
    el('bar').className = 'bar';
    el('scrub-time').hidden = true;
    el('player-strip').hidden = true;
    el('controls').hidden = true;
    applySubtitleStyle(false);
  }

  /* Vigilancia del atasco. Un fragmento que ya no existe no siempre hace saltar el
     evento de error del televisor: se queda pidiéndolo una y otra vez con la imagen
     quieta y sin decir nada. Aquí se mira el reloj del propio video, que es lo único
     que no miente, y si deja de avanzar se avisa primero y se rescata después. */
  function vigilar() {
    var play = state.playing, video = el('video'), ahora = Date.now();
    if (!play || state.dead) return;
    // Todavía preparando: el cartel de marca ya explica la espera.
    if (play.arrancando) {
      if (video.currentTime > 0.2 && !video.paused) {
        confirmarReproduccion();
        return;
      }
      // Una petición pendiente solo aplaza el rescate; nunca el cierre del modal
      // cuando el televisor ya confirma que está reproduciendo.
      if (state.pidiendo) { play.arranqueEn = ahora; return; }
      if (ahora - play.arranqueEn > ARRANQUE_MAX) {
        registrarReproduccion('error', { error_code: 'startup_timeout' });
        play.arrancando = false;
        if (play.localKaraoke) {
          note('Este archivo no entregó imagen. Comprueba que use video H.264 y audio AAC.');
          return;
        }
        if (!state.sources.length) { sinFuentes(); return; }
        siguienteFuente('La edición no llegó a dar imagen.', 0, 'startup_timeout');
      }
      return;
    }
    var t = video.currentTime || 0;
    if (t !== state.ultimoT) {
      var avance = state.ultimoT < 0 ? 0 : t - state.ultimoT;
      // Solo el avance continuo cuenta como reproducción efectiva. Un salto grande
      // cambia de minuto, pero no convierte ese minuto en tiempo realmente visto.
      if (!state.salto && !play.posicionSinConfirmar && !video.paused && avance > 0 && avance < 2) {
        play.watchedMs = (play.watchedMs || 0) + avance * 1000;
        terminarAtasco();
        if (play.trace) {
          if (play.trace.recovering) { registrarReproduccion('recovered', {}); play.trace.recovering = false; }
          play.trace.stableMs += avance * 1000;
          if (play.trace.first && !play.trace.stableSent && play.trace.stableMs >= 60000) {
            play.trace.stableSent = true;
            registrarReproduccion('stable', { duration_ms: Math.round(play.trace.stableMs) });
          }
        }
      }
      // El reloj del video corriendo es la mejor prueba de vida que hay del equipo:
      // sin fragmentos suyos no habría imagen que avanzara.
      if (!video.paused && avance > 0) state.videoVivoEn = ahora;
      state.ultimoT = t; state.avanceEn = ahora;
      if (!state.salto && !play.posicionSinConfirmar && t > 0.2) play.ultimaPosicion = play.offset + t * 1000;
    }
    if (state.salto) {
      comprobarSalto();
      return;
    }
    if (video.paused) { state.avanceEn = ahora; return; }
    var quieto = ahora - state.avanceEn;
    if (quieto > ATASCO_RESCATE) {
      if (play.localKaraoke) {
        state.avanceEn = ahora;
        trabajando('El archivo local dejó de entregar imagen.', true);
        return;
      }
      registrarAtasco();
      if (play.rescates < RESCATES_MAX) {
        registrarReproduccion('recovery_started', { error_code: 'playback_stalled' });
        if (play.trace) play.trace.recovering = true;
        play.rescates++; saltoServidor(posicion(), true);
      }
      else {
        registrarReproduccion('error', { error_code: 'playback_stalled' });
        state.avanceEn = ahora; siguienteFuente('La imagen se detuvo y no volvió.', 0, 'playback_stalled');
      }
      return;
    }
    if (quieto > ATASCO_AVISO) { registrarAtasco(); trabajando('Recuperando la señal…'); }
    else if (!state.esperando) trabajando('');
  }

  function tick() {
    if (state.screen === 'player' && state.playing) {
      var video = el('video');
      vigilar();
      pintarBarra();
      var remaining = duracion() - posicion();
      if (!video.paused && !state.playing.nextSubtitles && !state.subs.disabled &&
          !state.playing.live && posicion() > 30000 && remaining > 0 && remaining <= 120000 && nextEpisode()) {
        state.playing.nextSubtitles = true;
        api('playback/subtitles/next', 'POST', { source_id: state.playing.source }, function () {}, function () {});
      }
      var visible = !(Date.now() > state.controlsUntil && state.menu < 0);
      if (!visible && (state.zone !== 'none' || !el('controls').hidden)) ocultarControles();
      else {
        el('controls').hidden = !visible || state.zone === 'episodes' || state.zone === 'seasons';
        applySubtitleStyle(visible);
      }
    }
    var now = new Date();
    el('clock').textContent = now.getHours() + ':' + (now.getMinutes() < 10 ? '0' : '') + now.getMinutes();
    // Cambiar de día refresca el catálogo al estar navegando. Nunca interrumpe
    // una película, una ficha, una búsqueda ni el asistente.
    if (state.screen === 'home' && !state.playing && !state.assistantOpen &&
        catalogDay !== '' && catalogDay !== diaCatalogo()) {
      if (state.section === 'home') loadHome();
      else if (state.section === 'movie' || state.section === 'series') cargarSeccion(state.section);
    }
  }

  /* ------------------------------------------------------------- teclado */

  function tecladoFicha(code) {
    var lista = episodiosTemporada(state.dseason), seasons = temporadas();
    if (state.dzone === 'acciones') {
      if (code === KEY_RIGHT && state.daction < state.dacciones.length - 1) state.daction++;
      else if (code === KEY_LEFT && state.daction > 0) state.daction--;
      else if (code === KEY_DOWN && lista.length) {
        state.dzone = seasons.length > 1 ? 'temporadas' : 'episodios'; state.depisode = 0;
      }
      else if (code === KEY_OK) { activarDetalle(state.daction); return; }
      else if (inList(KEY_BACK, code)) {
        if (state.section === 'list' && state.detailOrigin !== 'assistant') { cargarMiLista(); return; }
        volverAlOrigen();
        return;
      }
      else return;
      enfocarDetalle(); return;
    }
    if (state.dzone === 'temporadas') {
      if (code === KEY_RIGHT && state.dseason < seasons.length - 1) cambiarTemporadaFicha(state.dseason + 1);
      else if (code === KEY_LEFT && state.dseason > 0) cambiarTemporadaFicha(state.dseason - 1);
      else if (code === KEY_UP || inList(KEY_BACK, code)) state.dzone = 'acciones';
      else if (code === KEY_DOWN || code === KEY_OK) state.dzone = 'episodios';
      else return;
      enfocarDetalle(); return;
    }
    if (esKaraoke() && code === KEY_DOWN && state.depisode < lista.length - 1) state.depisode++;
    else if (esKaraoke() && code === KEY_UP && state.depisode > 0) state.depisode--;
    else if (!esKaraoke() && code === KEY_RIGHT && state.depisode < lista.length - 1) state.depisode++;
    else if (!esKaraoke() && code === KEY_LEFT && state.depisode > 0) state.depisode--;
    else if (code === KEY_UP) state.dzone = seasons.length > 1 ? 'temporadas' : 'acciones';
    else if (code === KEY_OK) { reproducirEpisodio(lista[state.depisode]); return; }
    else if (inList(KEY_BACK, code)) { state.dzone = 'acciones'; }
    else return;
    enfocarDetalle();
  }

  function tecladoReproductor(code) {
    if (state.dead) {
      if (inList(KEY_BACK, code)) { stopPlayback(true); return; }
      if (code === KEY_OK) { activarFin(state.finBoton); return; }
      if (code === KEY_RIGHT && state.finBoton < state.fin.length - 1) { state.finBoton++; pintarFin(); }
      else if (code === KEY_LEFT && state.finBoton > 0) { state.finBoton--; pintarFin(); }
      return;
    }
    if (state.menu >= 0) {                      // el menú manda mientras está abierto
      var total = menuOptions().length;
      if (code === KEY_DOWN && state.menu < total - 1) { state.menu++; renderMenu(); }
      else if (code === KEY_UP && state.menu > 0) { state.menu--; renderMenu(); }
      else if (code === KEY_OK) chooseMenu();
      else if (inList(KEY_BACK, code)) closeMenu();
      return;
    }
    if (state.notice) {                         // hay una pregunta en pantalla
      if (code === KEY_OK) {
        var following = state.notice.following || nextEpisode();
        clearNotice();
        if (following) { state.chained = 0; playFollowing(following); }
      } else if (inList(KEY_BACK, code)) clearNotice();
      return;
    }
    // Las teclas de reproducción del mando siempre alternan, estés donde estés.
    if (inList(KEY_PLAY, code) || inList(KEY_PAUSE, code)) { alternar(); return; }
    if (inList(KEY_BACK, code)) {
      if (!el('controls').hidden || state.zone !== 'none') ocultarControles();
      else stopPlayback(!state.item);
      return;
    }
    // Las teclas multimedia, ya registradas en Tizen, también abren el recorrido.
    // Las flechas conservan su navegación normal en botones y listas de episodios.
    var forward = code === 417, rewind = code === 412;
    if (forward || rewind || ((state.zone === 'none' || state.zone === 'bar') &&
        (code === KEY_RIGHT || code === KEY_LEFT))) {
      iniciarAvance(code, forward || code === KEY_RIGHT ? 1 : -1); return;
    }
    var lista = episodiosTemporada(state.pseason), seasons = temporadas();
    if (state.zone === 'none') {
      if (code === KEY_OK) { alternar(); return; }
      if (code === KEY_UP || code === KEY_DOWN || code === KEY_LEFT || code === KEY_RIGHT) { entrarBarra(); return; }
      return;
    }
    if (state.zone === 'bar') {
      if (code === KEY_DOWN) { state.zone = 'buttons'; state.button = 0; enfocarReproductor(); }
      else if (code === KEY_UP) { state.zone = 'none'; enfocarReproductor(); }
      else if (code === KEY_OK) {
        if (state.scrubMoved) saltarA(state.scrub);
        state.zone = 'none'; enfocarReproductor();
      }
      return;
    }
    if (state.zone === 'buttons') {
      if (code === KEY_RIGHT && state.button < state.botones.length - 1) { state.button++; enfocarReproductor(); }
      else if (code === KEY_LEFT && state.button > 0) { state.button--; enfocarReproductor(); }
      else if (code === KEY_UP) entrarBarra();
      else if (code === KEY_DOWN && lista.length) entrarEpisodios();
      else if (code === KEY_OK) activarBoton(state.button);
      return;
    }
    if (state.zone === 'seasons') {
      if (code === KEY_RIGHT && state.pseason < seasons.length - 1) cambiarTemporadaReproductor(state.pseason + 1);
      else if (code === KEY_LEFT && state.pseason > 0) cambiarTemporadaReproductor(state.pseason - 1);
      else if (code === KEY_UP || inList(KEY_BACK, code)) { state.zone = 'buttons'; enfocarReproductor(); }
      else if (code === KEY_DOWN || code === KEY_OK) { state.zone = 'episodes'; enfocarReproductor(); }
      return;
    }
    if (state.zone === 'episodes') {
      if (esKaraoke() && code === KEY_DOWN && state.episode < lista.length - 1) {
        state.episode++; enfocarReproductor();
      }
      else if (esKaraoke() && code === KEY_UP && state.episode > 0) {
        state.episode--; enfocarReproductor();
      }
      else if (!esKaraoke() && code === KEY_RIGHT && state.episode < lista.length - 1) {
        state.episode++; enfocarReproductor();
      }
      else if (!esKaraoke() && code === KEY_LEFT && state.episode > 0) {
        state.episode--; enfocarReproductor();
      }
      else if (code === KEY_UP) {
        state.zone = seasons.length > 1 ? 'seasons' : 'buttons'; enfocarReproductor();
      }
      else if (code === KEY_OK) reproducirEpisodio(lista[state.episode]);
    }
  }

  function onKey(event) {
    var code = event.keyCode;
    if (state.scrubHold && state.scrubHold.code !== code) detenerAvance();
    if (state.connectionLost) {
      if (code === KEY_OK) reintentarConexion();
      else if (inList(KEY_BACK, code)) salir();
      else return;
      event.preventDefault(); return;
    }
    if (state.screen === 'brand' && state.brandReturn) {
      if (code === KEY_OK || inList(KEY_BACK, code)) volverDesdeBrand();
      else return;
      event.preventDefault(); return;
    }
    if (state.resumePrompt) {
      if (code === KEY_RIGHT || code === KEY_LEFT) {
        state.resumeChoice = state.resumeChoice ? 0 : 1; pintarEleccionEpisodio();
      } else if (code === KEY_OK) resolverEleccionEpisodio();
      else if (inList(KEY_BACK, code)) cerrarEleccionEpisodio();
      else return;
      event.preventDefault(); return;
    }
    if (state.assistantOpen) { tecladoAsistente(code, event); return; }
    if (inList(KEY_VOICE, code) && state.screen !== 'pair') {
      abrirAsistente(); event.preventDefault(); return;
    }
    if (state.screen === 'pair') {
      if (code >= 48 && code <= 57) { digit(code - 48); return; }
      if (code >= 96 && code <= 105) { digit(code - 96); return; }
      if (code === KEY_OK) { pulsarPad(); return; }
      if (inList(KEY_BACK, code)) { state.code = state.code.slice(0, -1); renderCode(); return; }
      var destino = state.pad;
      if (code === KEY_RIGHT) destino = state.pad + 1;
      else if (code === KEY_LEFT) destino = state.pad - 1;
      else if (code === KEY_DOWN) destino = state.pad + COLUMNAS;
      else if (code === KEY_UP) destino = state.pad - COLUMNAS;
      else return;
      if (destino >= 0 && destino < TECLAS.length) { state.pad = destino; renderPad(); }
      event.preventDefault();
      return;
    }
    if (state.screen === 'home') {
      if (state.section === 'search' && state.homeZone !== 'nav') {
        tecladoBusqueda(code, event); return;
      }
      if (state.homeZone === 'nav') {
        if (code === KEY_RIGHT && state.nav < NAV.length - 1) state.nav++;
        else if (code === KEY_LEFT && state.nav > 0) state.nav--;
        else if (code === KEY_DOWN) {
          if (state.section === 'search') { abrirBusqueda(); event.preventDefault(); return; }
          if (!state.rows.length) return;
          state.homeZone = 'rows'; focusCard(); event.preventDefault(); return;
        } else if (code === KEY_OK) { activarNav(state.nav); return; }
        else if (inList(KEY_BACK, code) && state.section !== 'home') { loadHome(); return; }
        else return;
        renderNav(); event.preventDefault(); return;
      }
      if (state.homeZone === 'assistant') {
        if (code === KEY_OK) abrirAsistente();
        else if (code === KEY_RIGHT || inList(KEY_BACK, code)) {
          ocultarGPT(); focusCard();
        } else return;
        event.preventDefault(); return;
      }
      var row = state.rows[state.row];
      if (code === KEY_RIGHT && row && state.column < columnas(row) - 1) state.column++;
      else if (code === KEY_LEFT && state.column > 0) state.column--;
      else if (code === KEY_LEFT && state.column === 0) { mostrarGPT(); event.preventDefault(); return; }
      else if (code === KEY_DOWN && state.row < state.rows.length - 1) { state.row++; state.column = 0; }
      else if (code === KEY_UP && state.row > 0) { state.row--; state.column = 0; }
      else if (code === KEY_UP && state.row === 0) { state.homeZone = 'nav'; renderNav(); event.preventDefault(); return; }
      else if (code === KEY_OK && row) { activarTarjeta(); return; }
      else if (inList(KEY_BACK, code)) {
        if (state.section !== 'home') loadHome();
        else { state.homeZone = 'nav'; renderNav(); }
        return;
      }
      else return;
      focusCard(); event.preventDefault(); return;
    }
    if (state.screen === 'detail') { tecladoFicha(code); event.preventDefault(); return; }
    if (state.screen === 'player') { tecladoReproductor(code); event.preventDefault(); }
  }

  /* --------------------------------------------------------------- inicio */

  function start() {
    document.addEventListener('keydown', onKey, false);
    document.addEventListener('keyup', onKeyUp, false);
    document.addEventListener('visibilitychange', function () {
      if (document.hidden || document.visibilityState === 'hidden') detenerAvance();
    }, false);
    if (window.addEventListener) window.addEventListener('blur', detenerAvance, false);
    prepararNav();
    el('search-input').onfocus = function () {
      if (state.section !== 'search' || state.assistantOpen) return;
      // Editar con puntero o foco nativo también invalida la respuesta anterior.
      state.searchGeneration++; state.searchBusy = false;
      el('search-submit').textContent = 'Buscar';
      state.searchEditing = true; state.homeZone = 'search';
      el('search-box').className = 'search-box focus';
      el('search-submit').className = 'search-submit';
    };
    el('search-submit').onmouseover = seleccionarBuscar;
    el('search-submit').onfocus = function () {
      if (state.section !== 'search' || state.assistantOpen) return;
      state.searchEditing = false; state.homeZone = 'searchSubmit';
      el('search-box').className = 'search-box';
      el('search-submit').className = 'search-submit focus';
    };
    el('search-submit').onclick = buscar;
    el('gpt-launcher').onclick = abrirAsistente;
    el('assistant-submit').onclick = enviarAsistente;
    el('assistant-submit').onmouseover = seleccionarBuscarAsistente;
    el('assistant-input').onfocus = function () {
      if (state.assistantOpen) { state.assistantEditing = true; el('assistant-box').className = 'assistant-box focus'; }
    };
    el('connection-retry').onclick = reintentarConexion;
    el('brand-back').onclick = volverDesdeBrand;
    el('resume-continue').onmouseover = function () { state.resumeChoice = 0; pintarEleccionEpisodio(); };
    el('resume-restart').onmouseover = function () { state.resumeChoice = 1; pintarEleccionEpisodio(); };
    el('resume-continue').onclick = function () { resolverEleccionEpisodio(0); };
    el('resume-restart').onclick = function () { resolverEleccionEpisodio(1); };
    var video = el('video');
    try {
      var captionStyle = localStorage.getItem(CAPTION_STORE) || '';
      for (var c = 0; c < CAPTION_STYLES.length; c++) {
        if (CAPTION_STYLES[c].id === captionStyle) state.subs.style = captionStyle;
      }
    } catch (error) { /* el televisor puede negar almacenamiento privado */ }
    applySubtitleStyle(false);
    // Un clic sobre la imagen despierta los controles; el segundo pausa o reanuda.
    el('player').onclick = function (event) {
      if (state.screen !== 'player' || !state.playing || state.dead) return;
      if (event && event.target && event.target.id !== 'video' && event.target !== el('player')) return;
      if (el('controls').hidden) touchControls(); else alternar();
    };
    video.addEventListener('timeupdate', function () {
      // También funciona si el navegador retrasa el intervalo de vigilancia.
      if (state.screen === 'player' && state.salto) comprobarSalto();
      pintarBarra();
      paintSubtitle();
    }, false);
    video.addEventListener('durationchange', pintarBarra, false);
    video.addEventListener('seeking', function () {
      if (state.playing) state.playing.saltoConfirmado = false;
      var render = state.subs.render;
      var transition = state.salto || (state.playing && state.playing.arrancando);
      resetSubtitleClock(transition && render && render.first ? render.context : 'seek');
    }, false);
    video.addEventListener('seeked', function () { comprobarSalto(); paintSubtitle(); }, false);
    video.addEventListener('pause', flushSubtitleMeasurement, false);
    // Compatible con Tizen antiguo: el texto consulta el reloj del video cada 50 ms.
    // No depende de la frecuencia variable de timeupdate ni avanza durante una pausa.
    window.setInterval(function () {
      if (state.screen === 'player' && state.playing && !state.dead && !document.hidden) paintSubtitle();
    }, 50);
    // Buffering: decirlo enseguida y en pequeño, que es lo que hace cualquier servicio.
    video.addEventListener('waiting', function () {
      registrarAtasco();
      state.esperando = true;
      if (!state.salto && state.playing && !state.playing.arrancando) trabajando('Cargando…');
    }, false);
    video.addEventListener('playing', function () {
      if (state.playing && state.playing.arrancando) confirmarReproduccion();
      state.esperando = false;
      terminarAtasco();
      if (!state.salto) trabajando('');
      comprobarSalto();
    }, false);
    video.addEventListener('canplay', function () { state.esperando = false; comprobarSalto(); }, false);
    video.addEventListener('ended', whenFinished, false);
    video.addEventListener('error', whenBroken, false);
    video.addEventListener('play', renderControles, false);
    video.addEventListener('pause', renderControles, false);
    window.setInterval(tick, 500);
    window.setInterval(function () { if (state.screen === 'player') saveProgress(); }, 15000);
    /* Una comprobación de vez en cuando permite avisar aun con la persona quieta en
       el catálogo. No hace falta preguntar si el equipo acaba de contestar, ni
       mientras hay una petición de verdad en vuelo, ni mientras la película avanza
       sin quejarse: todo eso ya es respuesta suya. Con un fallo apuntado sí se
       insiste, y así se sale solo de un corte pasajero sin tocar el mando. */
    window.setInterval(function () {
      if (state.connectionChecking || state.pidiendo > 0) return;
      if (Date.now() - state.lastServerResponse < LATIDO_FRESCO) return;
      if (!state.connectionFails && imagenViva()) return;
      comprobarPC();
    }, LATIDO_CADA);
    try {
      if (window.tizen && window.tizen.tvinputdevice) {
        var wanted = ['MediaPlayPause', 'MediaPlay', 'MediaPause', 'MediaStop', 'MediaRewind', 'MediaFastForward', 'Search'];
        for (var i = 0; i < wanted.length; i++) window.tizen.tvinputdevice.registerKey(wanted[i]);
      }
    } catch (error) { /* fuera de Tizen no existe */ }
    var saved = '';
    try { saved = localStorage.getItem(STORE) || ''; } catch (error) { saved = ''; }
    if (!saved) { renderCode(); show('pair'); return; }
    state.key = saved;
    try { journalQueue = JSON.parse(localStorage.getItem(JOURNAL_STORE) || '[]'); } catch (error) { journalQueue = []; }
    if (!Array.isArray(journalQueue)) journalQueue = [];
    journalQueue = journalQueue.slice(-200);
    window.setInterval(enviarDiario, 15000);
    arrancarSesion();
  }

  /* Solo una clave rechazada obliga a emparejar de nuevo. Si el PC no contesta, la
     clave sigue siendo buena: se avisa y se reintenta solo. Mandar a la pantalla de
     emparejar por un corte de red es perder el televisor por nada. */
  function arrancarSesion() {
    api('providers', 'GET', null, function () { loadHome(); }, function (mensaje, status) {
      if (status === 401 || status === 403) { forgetKey(); return; }
      state.connectionFails = FALLOS_GRAVES; pintarConexion();
      window.setTimeout(arrancarSesion, LATIDO_CADA);
    });
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start, false);
  else start();
})();
