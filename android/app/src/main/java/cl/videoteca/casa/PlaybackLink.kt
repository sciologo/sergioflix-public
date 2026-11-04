package cl.videoteca.casa

import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue

/** Puente entre la ventana de reproducción y la barra del catálogo.

    La barra tiene que poder pausar y reanudar lo que ya está sonando sin abrir ni
    preparar nada: cualquier atajo que pase por volver a lanzar la reproducción la
    reinicia, que es justo lo que no debe ocurrir. */
object PlaybackLink{
    /** Hay una ventana de reproducción viva y con el video listo. */
    var live by mutableStateOf(false)
        private set
    /** Lo que está sonando ahora mismo en esa ventana. */
    var playing by mutableStateOf(false)
        private set
    private var switch:(()->Unit)?=null

    fun attach(onToggle:()->Unit){switch=onToggle;live=true}
    fun update(ready:Boolean,running:Boolean){live=ready && switch!=null;playing=running}
    fun detach(onToggle:()->Unit){
        if(switch===onToggle){switch=null;live=false;playing=false}
    }
    fun toggle(){switch?.invoke()}
}
