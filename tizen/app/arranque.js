/* Arranque de la aplicación de televisor.

   Lo único que hace es esperar a que la videoteca responda y llevarse la pantalla allí.
   La comprobación se hace cargando una imagen del propio servicio: una imagen no está
   sujeta a las reglas entre orígenes ni a la parte estricta de la política de contenido,
   así que funciona igual en el Tizen 3 del televisor de 2017 y en el Tizen 6 del de 2021.

   Que el arranque sea local y la interfaz remota tiene dos ventajas: si el equipo está
   apagado se puede decir con todas las letras en vez de mostrar un error del sistema, y
   cada mejora de la interfaz llega al televisor sin volver a instalar nada. */
(function () {
  'use strict';
  var BASE = 'http://127.0.0.1:8765';          // la copia local se prepara desde .env antes de empaquetar
  var intentos = 0, intento = 0, esperando = false, proximo = null;

  function el(id) { return document.getElementById(id); }

  function probar() {
    if (esperando) return;
    esperando = true;
    intentos++;
    var actual = ++intento, terminado = false, senal = new Image();
    el('reintentar').textContent = intentos > 1 ? 'Comprobando…' : 'Reintentar';
    function completar(ok) {
      if (terminado || actual !== intento) return;
      terminado = true; esperando = false;
      if (ok) {
        el('nota').textContent = 'Abriendo tu videoteca…';
        window.location.replace(BASE + '/tv/');
        return;
      }
      el('nota').textContent = 'No encuentro el PC de la videoteca. Enciéndelo y pulsa Reintentar.';
      el('reintentar').textContent = 'Reintentar'; el('reintentar').hidden = false;
      proximo = window.setTimeout(probar, 5000);
    }
    senal.onload = function () { completar(true); };
    senal.onerror = function () { completar(false); };
    window.setTimeout(function () { completar(false); }, 4000);
    senal.src = BASE + '/tv/ping.png?' + new Date().getTime();
  }

  function reintentar() {
    if (proximo) window.clearTimeout(proximo);
    intento++; esperando = false;
    el('nota').textContent = 'Comprobando el PC de la videoteca…';
    el('reintentar').textContent = 'Comprobando…';
    probar();
  }

  el('reintentar').onclick = reintentar;

  /* La tecla Volver cierra; OK ejecuta siempre el único botón visible. */
  document.addEventListener('keydown', function (event) {
    if (event.keyCode === 13 && !el('reintentar').hidden) {
      reintentar(); event.preventDefault(); return;
    }
    if (event.keyCode === 10009 || event.keyCode === 461) {
      try { window.tizen.application.getCurrentApplication().exit(); } catch (error) { /* fuera de Tizen */ }
    }
  }, false);

  el('donde').textContent = BASE;
  probar();
})();
