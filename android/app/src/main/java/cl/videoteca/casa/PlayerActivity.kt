package cl.videoteca.casa

import android.app.PictureInPictureParams
import android.content.Intent
import android.content.pm.ActivityInfo
import android.content.res.Configuration
import android.os.Build
import android.os.Bundle
import android.util.Rational
import android.net.Uri
import android.view.WindowManager
import androidx.activity.compose.BackHandler
import androidx.activity.compose.setContent
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Check
import androidx.compose.material.icons.filled.ClosedCaption
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Forward10
import androidx.compose.material.icons.filled.Pause
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material.icons.filled.Replay
import androidx.compose.material.icons.filled.Replay10
import androidx.compose.material.icons.filled.ScreenRotation
import androidx.compose.material.icons.filled.PictureInPictureAlt
import androidx.compose.material.icons.filled.SkipNext
import androidx.compose.material.icons.filled.Stop
import androidx.compose.material.icons.filled.SwapHoriz
import androidx.compose.material.icons.filled.VideoLibrary
import androidx.compose.material.icons.filled.VolumeUp
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.fragment.app.FragmentActivity
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.lifecycleScope
import androidx.lifecycle.repeatOnLifecycle
import androidx.media3.common.C
import androidx.media3.common.MediaItem
import androidx.media3.common.MimeTypes
import androidx.media3.common.PlaybackException
import androidx.media3.common.Player
import androidx.media3.common.TrackSelectionOverride
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.exoplayer.DefaultLoadControl
import androidx.media3.exoplayer.source.DefaultMediaSourceFactory
import androidx.media3.datasource.DefaultHttpDataSource
import androidx.media3.datasource.HttpDataSource
import androidx.media3.ui.AspectRatioFrameLayout
import androidx.media3.ui.PlayerView
import androidx.mediarouter.app.MediaRouteButton
import com.google.android.gms.cast.MediaInfo
import com.google.android.gms.cast.MediaMetadata
import com.google.android.gms.cast.MediaQueueItem
import com.google.android.gms.cast.MediaStatus
import com.google.android.gms.cast.MediaTrack
import com.google.android.gms.cast.framework.CastButtonFactory
import com.google.android.gms.cast.framework.CastContext
import com.google.android.gms.cast.framework.CastSession
import com.google.android.gms.cast.framework.SessionManagerListener
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.Job
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withTimeout
import kotlinx.coroutines.withTimeoutOrNull
import org.json.JSONArray
import org.json.JSONObject

// Terminar una Activity no debe cancelar el último guardado del historial.
private val progressWriter=CoroutineScope(SupervisorJob()+Dispatchers.IO)

// Probar unas pocas fuentes razonables evita tanto rendirse pronto como ocupar el PC sin límite.
private const val MAX_ATTEMPTS=5
private const val SKIP_MS=10_000L
private const val CONTROLS_VISIBLE_MS=4_500L
// Un receptor que carga lento no es una fuente rota: a los 45 s se avisa y solo a los
// dos minutos se cambia de edición. Cambiarla antes cortaba emisiones que iban bien.
// Episodios encadenados sin que nadie toque nada antes de preguntar si alguien mira.
// Es donde se va el equipo de noche: convirtiendo capítulos para una sala vacía.
private const val AUTO_ADVANCE_LIMIT=3
private const val PRESENCE_SECONDS=60
private const val CAST_SLOW_MS=45_000L
private const val CAST_GIVE_UP_MS=120_000L
private val Red=Color(0xFFE53D49)

private const val STAGE_SOURCES="fuentes"
private const val STAGE_PREPARING="preparando"
private const val STAGE_SENDING="enviando"
private const val STAGE_BUFFERING="cargando"
private const val STAGE_SEEKING="saltando"
private const val STAGE_READY="listo"

private fun clock(ms:Long):String{
    if(ms<=0)return "0:00"
    val total=ms/1000
    return if(total>=3600)String.format("%d:%02d:%02d",total/3600,(total%3600)/60,total%60)
    else String.format("%d:%02d",total/60,total%60)
}

/** Reproducción local y cola Cast. Las URLs temporales se resuelven desde el backend. */
@androidx.annotation.OptIn(androidx.media3.common.util.UnstableApi::class)
@OptIn(ExperimentalMaterial3Api::class)
class PlayerActivity:FragmentActivity(){
    private lateinit var api:Api
    private lateinit var player:ExoPlayer
    private lateinit var content:JSONObject
    private lateinit var captions:CaptionChoice
    private var videoId=""
    private var channelId=""
    // null usa la decisión normal del backend; 0 fuerza el comienzo y un valor mayor
    // conserva la elección explícita de continuar incluso si ya superó el 92 %.
    private var requestedStartMs:Long?=null
    private var session:JSONObject?=null
    private var sequence=0L
    private var choices by mutableStateOf<List<JSONObject>>(emptyList())
    private var message by mutableStateOf("Buscando fuentes…")
    private var loading by mutableStateOf(true)
    private var choosing by mutableStateOf(true)
    private var remote by mutableStateOf(false)
    private var castContext:CastContext?=null
    private val attempted=mutableSetOf<String>()
    private var prepareJob:Job?=null
    private var sourceJob:Job?=null
    private var castJob:Job?=null
    private var queueJob:Job?=null
    private var countdownJob:Job?=null
    private var generation=0
    private var preparationStatus by mutableStateOf("")
    private var autoRecover=true
    // Fuente que ya se reintentó una vez tras un corte: evita reintentos en bucle.
    private var retriedSource=""
    // Episodios que se encadenaron solos desde la última vez que alguien tocó algo.
    private var autoAdvances=0
    private var askingPresence by mutableStateOf(false)
    private var presenceLeft by mutableStateOf(0)
    private var presenceJob:Job?=null
    // Marca que la fuente en curso viene de retomar una sesión ya abierta: si falla,
    // hay que buscar la lista normal antes de rendirse.
    private var reattaching=false
    private var activeSource=""
    private var castAccepted=false
    private var remoteWaitingSince=0L
    private var playRequestedAt=0L
    private var firstFrameReported=false
    private var bufferingReported=false
    private lateinit var journal:PlaybackJournal
    private var attemptId=""
    private var playbackId=java.util.UUID.randomUUID().toString()
    private var traceClosed=false
    private var bufferingSince=0L
    private var stableMs=0L
    private var stableReported=false
    private var tracePosition=0L
    private var recoveringTrace=false
    // Estado que sólo mira la interfaz del reproductor.
    private var position by mutableStateOf(0L)
    private var duration by mutableStateOf(0L)
    private var playing by mutableStateOf(false)
    private var scrubbing by mutableStateOf<Float?>(null)
    private var controlsUntil by mutableStateOf(0L)
    private var showEpisodes by mutableStateOf(false)
    private var pendingEpisode by mutableStateOf<JSONObject?>(null)
    private var showCaptions by mutableStateOf(false)
    private var showAudio by mutableStateOf(false)
    private var audioTracks by mutableStateOf<List<JSONObject>>(emptyList())
    private var selectedAudioStream by mutableStateOf(-1)
    private var audioLoading by mutableStateOf(false)
    private var audioStatus by mutableStateOf("")
    private var audioJob:Job?=null
    private var captionPreset by mutableStateOf(0)
    private var captionSize by mutableStateOf(1)
    // Pista de texto elegida. -1 apaga los subtítulos.
    private var captionTrack by mutableStateOf(0)
    private var manualCaptionKey = ""
    private var manualCaptionAudio = -1
    private var captionExplicitOff = false
    private var seekLimit=Long.MAX_VALUE
    private var adapted=false
    private var offsetMs=0L
    private var totalMs=0L
    private var jumping by mutableStateOf(false)
    private var nextIn by mutableStateOf(-1)
    private var seekJob:Job?=null
    private var subtitleSyncJob:Job?=null
    private var countdownToNext:Job?=null
    private var view:PlayerView?=null
    // Se guarda la referencia exacta para poder soltarla al destruir la ventana.
    private val bridge:()->Unit={togglePlay()}
    private var inPip by mutableStateOf(false)
    // Falso hasta el primer fotograma: mientras tanto manda la pantalla de marca.
    private var videoReady by mutableStateOf(false)
    // Ajuste fino que el usuario aplica cuando el subtítulo de la edición no encaja.
    private var subtitleSync by mutableStateOf(0L)
    private var subtitleSyncing by mutableStateOf(false)
    private var subtitleSyncStatus by mutableStateOf("")
    private var subtitleAttempt=""
    private var subtitleListLoading=false
    private var subtitleRetryRequested=false
    private var subtitleSearchDeadline=0L
    private var subtitleSearchKey=""
    private var subtitleNextSource=""
    private var captionPauseAfterReload=false
    private var lastMoved:JSONObject?=null
    /* Etapa visible del proceso. Solo en `listo` los controles hacen algo, así que
       hasta entonces no se dibujan: ver Play sin poder pulsarlo confunde. */
    private var stage by mutableStateOf(STAGE_SOURCES)
    private var freeRotation by mutableStateOf(false)
    // Aviso corto de lo que acaba de hacer el usuario: sin él, un salto que tarda
    // parece que no se registró y se pulsa otra vez, encadenando reconversiones.
    private var flash by mutableStateOf("")
    private var flashUntil=0L
    private val castListener=object:SessionManagerListener<CastSession>{
        override fun onSessionStarted(s:CastSession,id:String){startCast()}
        override fun onSessionResumed(s:CastSession,suspended:Boolean){
            remote=true;player.pause()
            // Sin adoptar la sesión que ya estaba en marcha, la app no seguía su estado y
            // pulsar Atrás detenía la película que el televisor estaba dando.
            val info=s.remoteMediaClient?.mediaInfo?.customData
            if(info!=null && info.optString("video_id")==videoId){
                // Solo se adopta la sesión, no la pantalla: la carga normal sigue su curso y
                // es la que fija el desplazamiento con el que se guarda el punto de la película.
                session=info;activeSource=info.optString("source_id");castAccepted=true
            }
        }
        override fun onSessionEnded(s:CastSession,error:Int){
            // Al desconectar el televisor el teléfono se quedaba sin nada cargado y con los
            // controles muertos: se retoma aquí, en el mismo punto.
            if(remote && !choosing && session!=null)playHereFrom(position,"Terminó en la TV. Retomando aquí…")
            else{remote=false;message="Reproducción en TV finalizada."}
        }
        override fun onSessionStarting(s:CastSession){}
        override fun onSessionStartFailed(s:CastSession,error:Int){message="No se pudo conectar con el Chromecast."}
        override fun onSessionEnding(s:CastSession){}
        override fun onSessionResuming(s:CastSession,id:String){}
        override fun onSessionResumeFailed(s:CastSession,error:Int){message="No se pudo recuperar la conexión con el Chromecast."}
        override fun onSessionSuspended(s:CastSession,reason:Int){}
    }

    override fun onCreate(savedInstanceState:Bundle?){
        super.onCreate(savedInstanceState)
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        api=Api(this)
        captions=CaptionChoice(this)
        captionPreset=captions.preset;captionSize=captions.size
        content=JSONObject(intent.getStringExtra("content")?:"{}")
        videoId=intent.getStringExtra("video_id")?:content.optString("id")
        channelId=intent.getStringExtra("channel_id").orEmpty()
        requestedStartMs=intent.getLongExtra("start_ms",-1L).takeIf{it>=0}
        journal=PlaybackJournal(this,api)
        val dataSource=DefaultHttpDataSource.Factory().setConnectTimeoutMs(12000).setReadTimeoutMs(120000)
        player=ExoPlayer.Builder(this)
            .setMediaSourceFactory(DefaultMediaSourceFactory(dataSource))
            .setLoadControl(DefaultLoadControl.Builder().setBufferDurationsMs(30000,90000,2000,6000).build()).build()
        player.trackSelectionParameters=player.trackSelectionParameters.buildUpon()
            .setPreferredAudioLanguage("es").setPreferredTextLanguage("es").build()
        player.addListener(object:Player.Listener{
            override fun onPlayerError(error:PlaybackException){
                if(remote || choosing)return
                saveProgress()
                // 410: el equipo ya soltó esta sesión. No es una fuente rota.
                if(httpCode(error)==410){idleStop();return}
                // Un corte del servidor o un fragmento que todavía no está no justifican
                // cambiar de edición: se reintenta la misma donde iba, una sola vez.
                if(activeSource.isNotBlank() && retriedSource!=activeSource && position>0){
                    retriedSource=activeSource
                    recoveringTrace=true
                    trace("recovery_started",JSONObject().put("error_code","player_error"))
                    playHereFrom(position,"Reanudando la reproducción…")
                    return
                }
                recover("Esta fuente dejó de responder.")
            }
            override fun onPlaybackStateChanged(state:Int){
                if(remote || choosing)return
                if(state==Player.STATE_READY){
                    loading=false;message="";videoReady=true;stage=STAGE_READY
                    // Cada recarga trae pistas nuevas: sin esto se perdía la elegida.
                    applyCaptionTrack();prepareSubtitles();finishBuffering();revealControls()
                }
                if(state==Player.STATE_BUFFERING){loading=true;noteBuffering()}
                if(state==Player.STATE_ENDED){trace("ended");traceClosed=true;saveProgress(true);startNextCountdown()}
            }
            override fun onRenderedFirstFrame(){if(!remote)noteFirstFrame()}
        })
        castContext=runCatching{CastContext.getSharedInstance(this)}.getOrNull()
        castContext?.sessionManager?.addSessionManagerListener(castListener,CastSession::class.java)
        PlaybackLink.attach(bridge)
        setContent{MaterialTheme(colorScheme=darkColorScheme(primary=Red,onPrimary=Color.White,surface=Color(0xFF17171A))){
            BackHandler{leavePlayer()}
            Surface(modifier=Modifier.fillMaxSize(),color=Color.Black,contentColor=Color.White){
                if(choosing)SourcePicker() else PlayerScreen()
            }
            if(showEpisodes)EpisodeSheet()
            if(showCaptions)CaptionSheet()
            if(showAudio)AudioSheet()
            pendingEpisode?.let{episode->EpisodeStartDialog(episode)}
            if(askingPresence)AlertDialog(
                onDismissRequest={},
                title={Text("¿Sigues ahí?")},
                text={Text("Van $AUTO_ADVANCE_LIMIT episodios seguidos sin que nadie toque nada. "+
                    "Si no respondes, la reproducción se detiene en $presenceLeft s.")},
                confirmButton={TextButton(onClick={
                    presenceJob?.cancel();askingPresence=false;autoAdvances=0;nextEpisode()
                }){Text("Sigo aquí")}},
                dismissButton={TextButton(onClick={
                    presenceJob?.cancel();askingPresence=false;stopPlayback()
                }){Text("Detener")}})
        }}
        // Si el televisor ya está dando este mismo video no se vuelve a enviar nada.
        if(!adoptRunningCast())loadSources()
        lifecycleScope.launch{repeatOnLifecycle(Lifecycle.State.STARTED){
            while(true){delay(10000);saveProgress();journal.flush();if(activeSource.isNotBlank())runCatching{api.call("/api/playback/status")}}
        }}
        lifecycleScope.launch{repeatOnLifecycle(Lifecycle.State.STARTED){
            // Al volver del segundo plano el estado del receptor llega con retraso:
            // se reinicia la espera para no dar por fallida una emisión que va bien.
            remoteWaitingSince=0L
            while(true){delay(2000);observeCast()}
        }}
        lifecycleScope.launch{repeatOnLifecycle(Lifecycle.State.STARTED){
            while(true){delay(400);trackPosition()}
        }}
    }

    // ---------------------------------------------------------------- interfaz

    private fun revealControls(){controlsUntil=System.currentTimeMillis()+CONTROLS_VISIBLE_MS}

    private fun trackPosition(){
        val client=castContext?.sessionManager?.currentCastSession?.remoteMediaClient
        val previousTracePosition=tracePosition
        if(remote){
            position=(offsetMs+(client?.approximateStreamPosition?:0)).coerceAtLeast(0)
            duration=if(totalMs>0)totalMs else (client?.streamDuration?:0).coerceAtLeast(0)
            playing=client?.isPlaying==true
        }else{
            // Una fuente convertida empieza su lista en `offsetMs`: la posición real los suma.
            position=(offsetMs+player.currentPosition).coerceAtLeast(0)
            duration=if(totalMs>0)totalMs else player.duration.takeIf{it>0}?:0
            playing=player.isPlaying
        }
        PlaybackLink.update(stage==STAGE_READY,playing)
        val advance=position-previousTracePosition
        tracePosition=position
        if(playing && firstFrameReported && !loading && !jumping && advance in 1..1500){
            finishBuffering()
            if(recoveringTrace){trace("recovered");recoveringTrace=false}
            stableMs+=advance
            if(stableMs>=60000 && !stableReported){stableReported=true;trace("stable",JSONObject().put("duration_ms",stableMs))}
        }else if(advance<0 || advance>1500){stableMs=0;stableReported=false}
        // Con la reproducción detenida los controles se quedan a la vista.
        if(!playing)revealControls()
        if(flash.isNotBlank() && System.currentTimeMillis()>flashUntil)flash=""
        // Solo los subtítulos del siguiente: el video actual conserva su sesión y recursos.
        if(playing && !loading && !jumping && captionTrack>=0 && position>30000 &&
            duration-position in 1..120000 && activeSource.isNotBlank() && subtitleNextSource!=activeSource &&
            (content.optJSONArray("episodes")?.length()?:0)>1){
            subtitleNextSource=activeSource
            val source=activeSource
            lifecycleScope.launch{runCatching{api.call("/api/playback/subtitles/next","POST",JSONObject().put("source_id",source))}}
        }
    }

    private val controlsVisible:Boolean get() = System.currentTimeMillis()<controlsUntil

    private fun togglePlay(){
        touched()
        val client=castContext?.sessionManager?.currentCastSession?.remoteMediaClient
        val sonando=if(remote)client?.isPlaying==true else player.isPlaying
        if(remote){if(sonando)client?.pause() else client?.play()}
        else{if(sonando)player.pause() else player.play()}
        // La barra del catálogo lee esto: sin actualizarlo aquí, su botón se quedaría
        // mostrando lo contrario hasta que la ventana volviera a estar a la vista.
        playing=!sonando
        PlaybackLink.update(stage==STAGE_READY,!sonando)
        revealControls()
    }
    private fun seekTo(target:Long){
        touched()
        // Un salto en curso ignora nuevas órdenes: encadenarlas reinicia la conversión
        // una y otra vez y es justo lo que hace que parezca colgado.
        if(jumping){note("Espera: todavía se está saltando…");return}
        val safe=target.coerceIn(0,if(duration>0)duration-1000 else Long.MAX_VALUE).coerceAtLeast(0)
        revealControls()
        val local=safe-offsetMs
        // Dentro de lo ya convertido el salto es instantáneo; fuera hay que reconvertir desde ahí.
        // La lista crece mientras el PC convierte, así que la ventana real la da el reproductor.
        val available=if(!remote && player.duration>0)maxOf(player.duration,seekLimit) else seekLimit
        val inWindow=!adapted || (local>=0 && local<=available)
        if(remote){
            if(inWindow){castContext?.sessionManager?.currentCastSession?.remoteMediaClient?.seek(if(adapted)local else safe);position=safe}
            else reconvertAt(safe)
            return
        }
        if(inWindow){player.seekTo(if(adapted)local else safe);position=safe;return}
        reconvertAt(safe)
    }
    /** Adelantar en una fuente convertida: el PC vuelve a convertir desde el punto pedido. */
    private fun reconvertAt(target:Long){
        val data=session?:return
        seekJob?.cancel()
        val requestGeneration=generation
        position=target;jumping=true;loading=true;stage=STAGE_SEEKING
        note("Saltando a "+clock(target))
        seekJob=lifecycleScope.launch{
            message="Saltando a ${clock(target)}…"
            try{
                val moved=withTimeout(120000){api.call("/api/playback/cast","POST",
                    JSONObject().put("source_id",data.getString("source_id")).put("position_ms",target))}
                if(requestGeneration!=generation)return@launch
                // La lista reutilizada puede empezar antes de lo pedido: hay que colocarse.
                if(remote)sendToCast(data,moved,target-moved.optLong("offset_ms",0))
                else loadAdapted(data,moved,resumeAt=(target-moved.optLong("offset_ms",0)).coerceAtLeast(0))
                if(!remote)stage=STAGE_READY
                message=""
            }catch(e:CancellationException){throw e}
            catch(e:Exception){message="No se pudo saltar a ese punto. "+e.message.orEmpty()}
            finally{if(requestGeneration==generation){jumping=false;loading=false;prepareSubtitles()}}
        }
    }
    /** Deja el reproductor apuntando a la lista devuelta por el PC, con su desplazamiento. */
    private fun loadAdapted(data:JSONObject,moved:JSONObject,resumeAt:Long){
        acceptPreparedSubtitles(data,moved)
        updateAudio(moved)
        adapted=moved.optBoolean("adapted",false)
        offsetMs=moved.optLong("offset_ms",0)
        totalMs=moved.optLong("duration_ms",0)
        seekLimit=moved.optLong("seekable_ms",Long.MAX_VALUE)
        // Las pistas posteriores llegan por otra petición; no restaurar una lista antigua al recargar.
        lastMoved=JSONObject(moved.toString()).apply{remove("subtitles");remove("subtitles_pending")}
        val declared=moved.optString("content_type","video/mp4")
        val mime=if(declared.contains("mpegurl",ignoreCase=true))MimeTypes.APPLICATION_M3U8 else declared
        val tracks=data.optJSONArray("subtitles")?.objects()?.map{track->
            MediaItem.SubtitleConfiguration.Builder(Uri.parse(subtitleUrl(track.getString("url"))))
                .setMimeType("text/vtt").setLanguage(track.optString("language","es")).setLabel(track.optString("label")).build()
        }?:emptyList()
        player.setMediaItem(MediaItem.Builder().setUri(moved.getString("stream_url")).setMimeType(mime)
            .setSubtitleConfigurations(tracks).build())
        if(resumeAt>0)player.seekTo(resumeAt)
        player.trackSelectionParameters=player.trackSelectionParameters.buildUpon()
            .setTrackTypeDisabled(C.TRACK_TYPE_TEXT,!captionUsable()).build()
        player.prepare();player.play();applyCaptions()
    }
    /** Pone el video en este teléfono desde `at`, conservando la edición en curso. */
    private fun playHereFrom(at:Long,reason:String){
        val data=session
        if(data==null || choosing){message=reason;loading=false;return}
        // Invalidar lo que quede en vuelo sin cancelar la corrutina desde la que se llama:
        // el contador de generación ya descarta por sí solo las respuestas atrasadas.
        generation++
        seekJob?.cancel();countdownJob?.cancel()
        remote=false;castAccepted=false;remoteWaitingSince=0L;jumping=false
        loading=true;stage=STAGE_PREPARING;message=reason;preparationStatus=""
        val requestGeneration=generation
        prepareJob=lifecycleScope.launch{
            try{
                val moved=withTimeout(120000){api.call("/api/playback/cast","POST",
                    JSONObject().put("source_id",data.getString("source_id")).put("position_ms",at))}
                if(requestGeneration!=generation)return@launch
                loadAdapted(data,moved,resumeAt=(at-moved.optLong("offset_ms",0)).coerceAtLeast(0))
                // La etapa la cierra el propio reproductor al dar el primer fotograma: dar
                // por listo antes deja los controles encima de una pantalla todavía negra.
                requestedOrientation=ActivityInfo.SCREEN_ORIENTATION_SENSOR_LANDSCAPE
                revealControls()
            }catch(e:CancellationException){throw e}
            catch(e:Exception){loading=false;failed(e,reason)}
        }
    }
    private fun skip(amount:Long){
        val target=(position+amount).coerceAtLeast(0)
        note(if(amount>0)"Adelantando a "+clock(target) else "Retrocediendo a "+clock(target))
        seekTo(target)
    }
    /** Confirma en pantalla que la orden se recibió, aunque tarde en verse. */
    private fun note(text:String){flash=text;flashUntil=System.currentTimeMillis()+2500;revealControls()}

    @Composable
    private fun PlayerScreen(){
        val interaction=remember{MutableInteractionSource()}
        var visible by remember{mutableStateOf(true)}
        // Recalcular la visibilidad con el mismo latido que la posicion.
        LaunchedEffect(Unit){while(true){visible=controlsVisible;delay(300)}}
        val ready=stage==STAGE_READY
        Box(Modifier.fillMaxSize().background(BrandBlack)){
            if(!remote)LocalStage()
            // Hasta que hay imagen manda la marca: ni la TV ni el telefono muestran nada util.
            if(!ready)BrandScreen(stageNote())
            if(inPip)return@Box
            Box(Modifier.fillMaxSize().clickable(interactionSource=interaction,indication=null){
                if(controlsVisible)controlsUntil=0 else revealControls()
            })
            AnimatedVisibility(visible || !ready,enter=fadeIn(),exit=fadeOut()){ControlOverlay(ready)}
            if(nextIn>0)NextEpisodeBanner()
            if(flash.isNotBlank())Text(flash,color=Color.White,fontSize=15.sp,fontWeight=FontWeight.SemiBold,
                modifier=Modifier.align(Alignment.TopCenter).statusBarsPadding().padding(top=64.dp)
                    .clip(RoundedCornerShape(20.dp)).background(Color(0xE60E0E10)).padding(horizontal=18.dp,vertical=10.dp))
        }
    }

    /** Que esta pasando ahora mismo, en una frase, sin mezclar etapas. */
    private fun stageNote():String{
        val device=castContext?.sessionManager?.currentCastSession?.castDevice?.friendlyName
        return when(stage){
            STAGE_SOURCES->message.ifBlank{"Buscando fuentes en tus proveedores…"}
            STAGE_PREPARING->message.ifBlank{"Preparando el video…"}
            STAGE_SENDING->"Enviando a tu dispositivo…"
            STAGE_BUFFERING->"Cargando en "+(device?:"tu dispositivo")+"…"
            STAGE_SEEKING->message.ifBlank{"Saltando…"}
            else->message
        }
    }

    @Composable
    private fun LocalStage(){
        AndroidView(
            factory={context->PlayerView(context).apply{
                player=this@PlayerActivity.player
                useController=false
                resizeMode=AspectRatioFrameLayout.RESIZE_MODE_FIT
                setShowBuffering(PlayerView.SHOW_BUFFERING_WHEN_PLAYING)
                subtitleView?.setApplyEmbeddedStyles(false)
                view=this
            }},
            // El preset elegido se reaplica en cada recomposición: cambiarlo se ve al instante.
            update={applyCaptions()},
            modifier=Modifier.fillMaxSize())
    }

    /** El subtítulo mide desde el principio de la película; la lista convertida empieza en
     *  `offsetMs`. Sin restarlo, tras retomar o saltar el texto va desfasado ese mismo tiempo. */
    private fun subtitleUrl(base:String):String{
        val total=offsetMs+subtitleSync
        if(total==0L)return base
        return base+(if(base.contains("?"))"&" else "?")+"offset="+total
    }
    /** Rehace la pista de texto conservando el punto de reproducción. */
    private fun reloadSubtitles(){
        // En la TV el desfase viaja dentro de la dirección del subtítulo, así que cambiarlo
        // obliga a reenviar la cola. Eso no puede pasar a cada toque: lo pide el botón.
        if(remote)return
        val moved=lastMoved?:return
        val data=session?:return
        val at=player.currentPosition.coerceAtLeast(0)
        val wasPlaying=player.playWhenReady
        loadAdapted(data,moved,resumeAt=at)
        if(!wasPlaying)player.pause()
    }

    /** Una alternativa admitida conserva su identidad/audio y no se anuncia como verificada. */
    private fun captionUsable():Boolean{
        val chosen=session?.optJSONArray("subtitles")?.optJSONObject(captionTrack)?:return false
        return !chosen.optBoolean("validation_required") || chosen.optBoolean("verified") ||
            (chosen.optBoolean("manual_only") && manualCaptionKey==chosen.optString("key") &&
                manualCaptionAudio==selectedAudioStream)
    }

    /** Inglés confirmado: elegir la primera alternativa disponible sin alterar los filtros. */
    private fun selectDefaultCaption():Boolean{
        val data=session?:return false
        if(channelId.isNotBlank() || captionExplicitOff ||
            captionTrack>=0 || data.optBoolean("subtitles_pending"))return false
        val language=audioTracks.firstOrNull{it.optInt("stream_index",-1)==selectedAudioStream}
            ?.optString("language").orEmpty().lowercase().replace('_','-').substringBefore('-')
        if(language !in setOf("en","eng"))return false
        val first=data.optJSONArray("subtitles")?.optJSONObject(0)?:return false
        if(!first.optBoolean("manual_only") || first.optString("key").isBlank())return false
        captionTrack=0;manualCaptionKey=first.optString("key");manualCaptionAudio=selectedAudioStream
        subtitleSync=0;subtitleAttempt=""
        subtitleSyncStatus="Sincronía sin comprobar; puedes ajustarla manualmente."
        android.util.Log.i("SergioFlix","Subtítulo recomendado seleccionado para audio inglés; sincronía sin comprobar.")
        return true
    }

    /** El booleano verified conserva compatibilidad; verification detalla identidad/tiempo/contenido. */
    private fun acceptPreparedSubtitles(data:JSONObject,prepared:JSONObject){
        if(!prepared.has("subtitles"))return
        val previous=data.optJSONArray("subtitles")?.optJSONObject(captionTrack)
        val previousKey=previous?.optString("key").orEmpty()
        var tracks=prepared.optJSONArray("subtitles")?:JSONArray()
        val alternatives=prepared.optJSONArray("subtitle_alternatives")?:JSONArray()
        val keys=tracks.objects().map{it.optString("key")}.toMutableSet()
        for(track in alternatives.objects())if(keys.add(track.optString("key")))tracks.put(track)
        val audioChanged=selectedAudioStream>=0 && prepared.has("audio_stream_index") &&
            prepared.optInt("audio_stream_index",-1)!=selectedAudioStream
        if(tracks.length()==0 && data.optBoolean("subtitle_retry") && !audioChanged && previous?.optBoolean("verified")==true)
            tracks=data.optJSONArray("subtitles")?:tracks
        data.put("subtitles",tracks).put("subtitles_pending",prepared.optBoolean("subtitles_pending"))
        for(key in listOf("subtitle_state","subtitle_reason","subtitle_message")){
            if(prepared.has(key))data.put(key,prepared.get(key))
        }
        if(prepared.has("subtitle_message"))subtitleSyncStatus=prepared.optString("subtitle_message")
        if(!prepared.optBoolean("subtitles_pending"))data.remove("subtitle_retry")
        if(!captionExplicitOff){
            val found=tracks.objects().indexOfFirst{previousKey.isNotBlank() && it.optString("key")==previousKey}
            captionTrack=if(found>=0 && (!tracks.getJSONObject(found).optBoolean("manual_only") ||
                (manualCaptionKey==previousKey && manualCaptionAudio==selectedAudioStream && !audioChanged)))found
                else tracks.objects().indexOfFirst{!it.optBoolean("manual_only")}
            if(previousKey.isNotBlank() && tracks.optJSONObject(captionTrack)?.optString("key")!=previousKey){
                subtitleSync=0;subtitleAttempt=""
            }
        }
        if(audioChanged){subtitleSync=0;subtitleAttempt="";manualCaptionKey=""}
        else selectDefaultCaption()
    }

    private fun prepareSubtitles(){
        val data=session?:return
        if(channelId.isNotBlank() || subtitleSyncing || subtitleListLoading || jumping || audioLoading)return
        val source=data.optString("source_id")
        val available=data.optJSONArray("subtitles")
        if(data.optBoolean("subtitles_pending")){
            val searchKey=source+":"+selectedAudioStream
            if(subtitleSearchDeadline==0L || subtitleSearchKey!=searchKey){
                subtitleSearchKey=searchKey;subtitleSearchDeadline=android.os.SystemClock.elapsedRealtime()+12000
            }
            if(android.os.SystemClock.elapsedRealtime()>=subtitleSearchDeadline){
                data.put("subtitles_pending",false).put("subtitle_state","failed")
                subtitleSyncStatus="La búsqueda terminó sin confirmar una pista. Puedes volver a buscar."
                return
            }
            subtitleListLoading=true
            val retry=subtitleRetryRequested;subtitleRetryRequested=false
            val requestGeneration=generation
            val requestAudio=selectedAudioStream
            lifecycleScope.launch{
                try{
                    val found=withTimeoutOrNull(3000){
                        api.call("/api/playback/subtitles/$source"+(if(retry)"/retry" else ""),if(retry)"POST" else "GET")
                    }?:throw IllegalStateException("La búsqueda no respondió. Puedes volver a buscar.")
                    if(requestGeneration!=generation || session!==data || requestAudio!=selectedAudioStream || jumping || audioLoading)return@launch
                    val previous=data.optJSONArray("subtitles")?.optJSONObject(captionTrack.coerceAtLeast(0))?.optString("sync_token")
                    val previousKey=data.optJSONArray("subtitles")?.optJSONObject(captionTrack.coerceAtLeast(0))?.optString("key")
                    val previousUsable=captionUsable()
                    acceptPreparedSubtitles(data,JSONObject().put("subtitles",found.optJSONArray("items")?:JSONArray())
                        .put("subtitle_alternatives",found.optJSONArray("alternatives")?:JSONArray())
                        .put("subtitles_pending",found.optBoolean("pending"))
                        .put("subtitle_state",found.optString("state"))
                        .put("subtitle_reason",found.optString("reason"))
                        .put("subtitle_message",found.optString("message")))
                    val chosen=data.optJSONArray("subtitles")?.optJSONObject(captionTrack.coerceAtLeast(0))
                    if(captionUsable() && (!previousUsable || chosen?.optString("sync_token")!=previous ||
                        chosen?.optString("key")!=previousKey)){
                        if(remote){
                            captionPauseAfterReload=castContext?.sessionManager?.currentCastSession?.remoteMediaClient?.isPaused==true
                            startCast()
                        }else reloadSubtitles()
                    }else if(previousUsable && !captionUsable())applyCaptionTrack()
                    if(found.optBoolean("pending"))delay(1000)
                }catch(e:CancellationException){throw e}
                catch(e:Exception){if(requestGeneration==generation && session===data && requestAudio==selectedAudioStream){
                    data.put("subtitles_pending",false).put("subtitle_state","failed")
                    subtitleSyncStatus="No se pudieron buscar subtítulos. Puedes volver a buscar."
                }}
                finally{if(requestGeneration==generation){subtitleListLoading=false;prepareSubtitles()}}
            }
            return
        }
        subtitleSearchDeadline=0L
        // Un estado del servidor nuevo ya incluye la búsqueda de alternativas.
        // No volver a abrir una calibración después de un resultado final.
        if(data.optString("subtitle_state").isNotBlank())return
        val chosen=available?.optJSONObject(captionTrack)?:return
        if(!chosen.optBoolean("validation_required") || chosen.optBoolean("verified"))return
        val attempt=source+":"+chosen.optString("source_id")+":"+selectedAudioStream
        if(subtitleAttempt==attempt)return
        subtitleAttempt=attempt
        autoSyncSubtitle(true)
    }

    /** El reintento explícito tiene un plazo nuevo; consultar el estado no lo reinicia. */
    private fun retrySubtitleSearch(){
        val data=session?:return
        if(subtitleListLoading || subtitleSyncing || data.optBoolean("subtitles_pending"))return
        subtitleRetryRequested=true;subtitleSearchDeadline=0L
        data.put("subtitles_pending",true).put("subtitle_state","searching").put("subtitle_retry",true)
        subtitleSyncStatus="Buscando otras alternativas para esta versión…"
        prepareSubtitles()
    }

    /** Pide una copia ajustada contra la voz del video. El archivo elegido no se toca. */
    private fun autoSyncSubtitle(automatic:Boolean=false){
        val data=session?:return
        val available=data.optJSONArray("subtitles")?:return
        val chosen=available.optJSONObject(captionTrack)?:return
        val subtitleSource=chosen.optString("source_id")
        val videoSource=data.optString("source_id")
        if(subtitleSource.isBlank() || videoSource.isBlank() || subtitleSyncing)return
        val requestGeneration=generation
        val audio=selectedAudioStream
        subtitleSyncJob?.cancel()
        subtitleSyncing=true;subtitleSyncStatus="Comparando el texto con la voz del video…"
        subtitleSyncJob=lifecycleScope.launch{
            try{
                val result=withTimeoutOrNull(12000){api.call("/api/playback/subtitles/sync","POST",
                    JSONObject().put("source_id",videoSource).put("subtitle_source_id",subtitleSource).put("recover",automatic))}
                    ?:throw IllegalStateException("No se pudo confirmar esta pista. Vuelve a buscar alternativas.")
                if(requestGeneration!=generation || session!==data || audio!=selectedAudioStream ||
                    data.optJSONArray("subtitles")?.optJSONObject(captionTrack)!==chosen)return@launch
                val item=result.optJSONObject("item")
                if(result.optBoolean("changed") && item!=null){
                    val tracks=data.optJSONArray("subtitles")?:JSONArray().also{data.put("subtitles",it)}
                    tracks.put(item);captionTrack=tracks.length()-1;subtitleSync=0
                    if(remote){
                        captionPauseAfterReload=castContext?.sessionManager?.currentCastSession?.remoteMediaClient?.isPaused==true
                        startCast()
                    }else reloadSubtitles()
                }
                subtitleSyncStatus=result.optString("message","Subtítulos sincronizados.")
                if(result.optBoolean("pending")){
                    data.put("subtitles_pending",true).put("subtitle_state",result.optString("state","searching"))
                }
                if(!automatic || !result.optBoolean("verified"))note(subtitleSyncStatus)
            }catch(e:CancellationException){throw e}
            catch(e:Exception){subtitleSyncStatus=e.message.orEmpty().ifBlank{"No se pudieron sincronizar los subtítulos."};note(subtitleSyncStatus)}
            finally{if(requestGeneration==generation){subtitleSyncing=false;prepareSubtitles()}}
        }
    }

    /** Se llama al elegir un preset: el estilo cambia sobre el video que ya está corriendo. */
    private fun applyCaptions(){
        view?.subtitleView?.setApplyEmbeddedStyles(false)
        view?.subtitleView?.setStyle(CAPTION_PRESETS[captionPreset].media3())
        view?.subtitleView?.setFractionalTextSize(CAPTION_SIZES[captionSize].second)
        // El receptor dibuja sus propios subtítulos y solo los recibía al cargar la cola:
        // sin esto, en la TV no cambiaba nada de lo que se elegía aquí.
        if(remote)castContext?.sessionManager?.currentCastSession?.remoteMediaClient
            ?.setTextTrackStyle(CAPTION_PRESETS[captionPreset].cast(CAPTION_SIZES[captionSize].second))
    }
    /** Elegir entre las opciones de subtítulo, o quitarlos, sin recargar el video. */
    private fun applyCaptionTrack(){
        if(remote){
            castContext?.sessionManager?.currentCastSession?.remoteMediaClient?.setActiveMediaTracks(
                if(!captionUsable())longArrayOf() else longArrayOf(captionTrack.toLong()+1))
            return
        }
        val groups=player.currentTracks.groups.filter{it.type==C.TRACK_TYPE_TEXT}
        player.trackSelectionParameters=player.trackSelectionParameters.buildUpon().apply{
            clearOverridesOfType(C.TRACK_TYPE_TEXT)
            setTrackTypeDisabled(C.TRACK_TYPE_TEXT,!captionUsable())
            groups.getOrNull(captionTrack)?.let{setOverrideForType(TrackSelectionOverride(it.mediaTrackGroup,0))}
        }.build()
    }

    /** Aviso de siguiente episodio, con cuenta atrás y salida, como en cualquier servicio. */
    @Composable
    private fun BoxScope.NextEpisodeBanner(){
        val episodes=content.optJSONArray("episodes")?.objects().orEmpty()
        val current=episodes.indexOfFirst{it.optString("id")==videoId}
        val following=episodes.getOrNull(current+1)
        Column(Modifier.align(Alignment.BottomEnd).systemBarsPadding().padding(24.dp).clip(RoundedCornerShape(12.dp))
            .background(Color(0xF20E0E10)).padding(18.dp)){
            Text("Siguiente episodio en $nextIn",color=Color.White,fontSize=15.sp,fontWeight=FontWeight.SemiBold)
            if(following!=null)Text("T${following.optInt("season")} · E${following.optInt("episode")}. "+
                following.optString("name",following.optString("title","Episodio")),
                color=Color(0xFFB9B8BE),fontSize=12.sp,maxLines=1,modifier=Modifier.padding(top=4.dp))
            Row(Modifier.padding(top=10.dp),horizontalArrangement=Arrangement.spacedBy(10.dp)){
                Button(onClick={cancelNextCountdown();nextEpisode()}){Text("Ver ahora")}
                TextButton(onClick={cancelNextCountdown()}){Text("Cancelar")}
            }
        }
    }

    @Composable
    private fun ControlOverlay(ready:Boolean){
        val episodes=content.optJSONArray("episodes")?.objects().orEmpty()
        val dismiss=remember{MutableInteractionSource()}
        val device=castContext?.sessionManager?.currentCastSession?.castDevice?.friendlyName
        Box(Modifier.fillMaxSize().background(Brush.verticalGradient(
            0f to Color(0xCC000000),0.35f to Color(0x33000000),0.65f to Color(0x33000000),1f to Color(0xE6000000)))
            .clickable(interactionSource=dismiss,indication=null){if(ready)controlsUntil=0}){
            // `systemBarsPadding` tambien aparta de la barra lateral de Android en horizontal.
            Row(Modifier.fillMaxWidth().systemBarsPadding().padding(horizontal=8.dp,vertical=6.dp),
                verticalAlignment=Alignment.CenterVertically){
                IconButton(onClick={closePlayer()}){Icon(Icons.Default.Close,"Cerrar",tint=Color.White)}
                Column(Modifier.weight(1f).padding(horizontal=6.dp)){
                    Text(content.optString("name"),maxLines=1,fontSize=16.sp,fontWeight=FontWeight.SemiBold)
                    val subtitle=if(remote && device!=null)"En "+device else episodeLabel()
                    if(subtitle.isNotBlank())Text(subtitle,color=Color(0xFFB9B8BE),fontSize=12.sp,maxLines=1)
                }
                if(!remote && ready)IconButton(onClick={toggleRotation()}){
                    Icon(Icons.Default.ScreenRotation,if(freeRotation)"Fijar en horizontal" else "Permitir girar",
                        tint=if(freeRotation)Red else Color.White)
                }
                if(!remote && ready)IconButton(onClick={enterPip()}){Icon(Icons.Default.PictureInPictureAlt,"Ver en miniatura",tint=Color.White)}
                // Enviar a la TV debe poder hacerse tambien mientras el PC prepara el video.
                AndroidView(factory={context->MediaRouteButton(context).apply{runCatching{CastButtonFactory.setUpMediaRouteButton(context,this)}}},
                    modifier=Modifier.size(46.dp))
            }
            if(ready)Row(Modifier.align(Alignment.Center),verticalAlignment=Alignment.CenterVertically,
                horizontalArrangement=Arrangement.spacedBy(30.dp)){
                RoundControl(Icons.Default.Replay10,"Retroceder 10 segundos",52.dp){skip(-SKIP_MS)}
                Box(Modifier.size(76.dp).background(Red,CircleShape).clickable{togglePlay()},contentAlignment=Alignment.Center){
                    Icon(if(playing)Icons.Default.Pause else Icons.Default.PlayArrow,
                        if(playing)"Pausar" else "Reproducir",tint=Color.White,modifier=Modifier.size(42.dp))
                }
                RoundControl(Icons.Default.Forward10,"Adelantar 10 segundos",52.dp){skip(SKIP_MS)}
            }
            Column(Modifier.align(Alignment.BottomCenter).systemBarsPadding().padding(horizontal=14.dp,vertical=8.dp)){
                if(!ready){
                    // La etapa ya se lee en el centro; aquí solo lo que sí funciona ahora.
                    Row(horizontalArrangement=Arrangement.spacedBy(10.dp)){
                        FlatAction(Icons.Default.Stop,"Cancelar"){stopPlayback()}
                        FlatAction(Icons.Default.SwapHoriz,"Otra fuente"){stopPlayback();autoRecover=false}
                    }
                }else{
                    val total=duration.coerceAtLeast(1L)
                    Slider(
                        value=scrubbing ?: (position.toFloat()/total).coerceIn(0f,1f),
                        onValueChange={scrubbing=it;revealControls()},
                        onValueChangeFinished={scrubbing?.let{seekTo((it*total).toLong())};scrubbing=null},
                        enabled=duration>0,
                        colors=SliderDefaults.colors(thumbColor=Red,activeTrackColor=Red,inactiveTrackColor=Color(0x66FFFFFF)))
                    Row(Modifier.fillMaxWidth(),horizontalArrangement=Arrangement.SpaceBetween){
                        Text(clock(scrubbing?.let{(it*total).toLong()} ?: position),color=Color(0xFFD6D5DA),fontSize=12.sp)
                        Text(if(duration>0)clock(duration) else "en directo",color=Color(0xFFD6D5DA),fontSize=12.sp)
                    }
                    Row(Modifier.fillMaxWidth().padding(top=4.dp),horizontalArrangement=Arrangement.SpaceEvenly,
                        verticalAlignment=Alignment.CenterVertically){
                        FlatAction(Icons.Default.Replay,"Reiniciar"){seekTo(0)}
                        if(episodes.isNotEmpty())FlatAction(Icons.Default.SkipNext,"Siguiente"){saveProgress();nextEpisode()}
                        if(episodes.isNotEmpty())FlatAction(Icons.Default.VideoLibrary,"Episodios"){showEpisodes=true;revealControls()}
                        FlatAction(Icons.Default.VolumeUp,"Audio"){openAudio();revealControls()}
                        FlatAction(Icons.Default.ClosedCaption,"Subtitulos"){showCaptions=true;revealControls()}
                        FlatAction(Icons.Default.SwapHoriz,"Otra fuente"){stopPlayback();autoRecover=false}
                        FlatAction(Icons.Default.Stop,"Detener"){stopPlayback()}
                    }
                }
            }
        }
    }

    /** El telefono empieza apaisado, pero se puede liberar para verlo vertical. */
    private fun toggleRotation(){
        freeRotation=!freeRotation
        requestedOrientation=if(freeRotation)ActivityInfo.SCREEN_ORIENTATION_USER
            else ActivityInfo.SCREEN_ORIENTATION_SENSOR_LANDSCAPE
        revealControls()
    }

    @Composable
    private fun RoundControl(icon:androidx.compose.ui.graphics.vector.ImageVector,label:String,size:androidx.compose.ui.unit.Dp,action:()->Unit){
        Box(Modifier.size(size).background(Color(0x59000000),CircleShape).clickable{action()},contentAlignment=Alignment.Center){
            Icon(icon,label,tint=Color.White,modifier=Modifier.size(size*0.62f))
        }
    }

    @Composable
    private fun FlatAction(icon:androidx.compose.ui.graphics.vector.ImageVector,label:String,action:()->Unit){
        Column(horizontalAlignment=Alignment.CenterHorizontally,
            modifier=Modifier.clip(RoundedCornerShape(8.dp)).clickable{action()}.padding(horizontal=8.dp,vertical=5.dp)){
            Icon(icon,label,tint=Color.White,modifier=Modifier.size(23.dp))
            Text(label,color=Color(0xFFD6D5DA),fontSize=10.sp,modifier=Modifier.padding(top=2.dp))
        }
    }

    private fun episodeLabel():String{
        val episodes=content.optJSONArray("episodes")?.objects().orEmpty()
        val current=episodes.firstOrNull{it.optString("id")==videoId}?:return ""
        val name=current.optString("name",current.optString("title","")).takeIf{it.isNotBlank()}
        return listOfNotNull("T${current.optInt("season")} · E${current.optInt("episode")}",name).joinToString(" — ")
    }

    private fun episodeProgress(target:String):JSONObject?=
        content.optJSONObject("playback")?.optJSONObject("videos")?.optJSONObject(target)

    /** La regla del 92 % sigue marcando visto; esta pregunta solo permite elegir la
     *  posición real guardada cuando alguien abre ese capítulo deliberadamente. */
    @Composable
    private fun EpisodeStartDialog(episode:JSONObject){
        val seen=episodeProgress(episode.getString("id"))?:return
        val at=seen.optLong("position_ms")
        val remaining=seen.optLong("duration_ms")-at
        AlertDialog(onDismissRequest={pendingEpisode=null},
            title={Text("¿Cómo quieres ver este episodio?")},
            text={Text("T${episode.optInt("season")} · E${episode.optInt("episode")}. "+
                if(remaining in 1..59_999)"Queda menos de un minuto."
                else "Llegaste hasta ${clock(at)}.")},
            confirmButton={TextButton(onClick={pendingEpisode=null;openEpisode(episode.getString("id"),at)}){
                Text("Continuar viendo")
            }},
            dismissButton={TextButton(onClick={pendingEpisode=null;openEpisode(episode.getString("id"),0L)}){
                Text("Ver desde el inicio")
            }})
    }

    @Composable
    private fun EpisodeSheet(){
        val episodes=content.optJSONArray("episodes")?.objects().orEmpty()
        val current=episodes.firstOrNull{it.optString("id")==videoId}
        var season by remember(videoId){mutableIntStateOf(current?.optInt("season")?:episodes.firstOrNull()?.optInt("season")?:1)}
        val seasons=episodes.map{it.optInt("season")}.distinct()
        ModalBottomSheet(onDismissRequest={showEpisodes=false},containerColor=Color(0xFF17171A),
            sheetState=rememberModalBottomSheetState(skipPartiallyExpanded=true)){
          Column(Modifier.navigationBarsPadding()){
            Text("Episodios",fontSize=19.sp,fontWeight=FontWeight.SemiBold,modifier=Modifier.padding(start=20.dp,bottom=6.dp))
            LazyRow(Modifier.fillMaxWidth(),contentPadding=PaddingValues(horizontal=16.dp),
                horizontalArrangement=Arrangement.spacedBy(8.dp)){
                items(seasons){number->FilterChip(selected=season==number,onClick={season=number},
                    label={Text("Temporada $number")})}
            }
            LazyColumn(Modifier.fillMaxWidth().heightIn(max=440.dp)){
                items(episodes.filter{it.optInt("season")==season}){episode->
                    val isCurrent=episode.optString("id")==videoId
                    val seen=episodeProgress(episode.getString("id"))
                    ListItem(
                        headlineContent={Text("T${episode.optInt("season")} · E${episode.optInt("episode")}. "+
                            episode.optString("name",episode.optString("title","Episodio")),maxLines=1,
                            color=if(isCurrent)Red else Color.White)},
                        supportingContent={Column{
                            Text(episode.optString("overview"),maxLines=2,color=Color(0xFF9A99A0),fontSize=12.sp)
                            if(seen!=null && seen.optLong("position_ms")>0)Text(
                                if(seen.optBoolean("completed"))"Visto" else "Visto ${(seen.optDouble("progress")*100).toInt()} %",
                                color=Red,fontSize=11.sp)
                        }},
                        trailingContent={if(isCurrent)Icon(Icons.Default.Check,"En reproducción",tint=Red)},
                        colors=ListItemDefaults.colors(containerColor=Color(0xFF17171A)),
                        modifier=Modifier.clickable{
                            if(!isCurrent){
                                showEpisodes=false
                                if(seen!=null && seen.optLong("position_ms")>0)pendingEpisode=episode
                                else openEpisode(episode.getString("id"))
                            }
                        })
                }
            }
            Spacer(Modifier.height(12.dp))
          }
        }
    }

    @Composable
    private fun CaptionSheet(){
        ModalBottomSheet(onDismissRequest={showCaptions=false},containerColor=Color(0xFF17171A),
            sheetState=rememberModalBottomSheetState(skipPartiallyExpanded=true)){
          Column(Modifier.navigationBarsPadding()){
            Text("Subtítulos",fontSize=19.sp,fontWeight=FontWeight.SemiBold,modifier=Modifier.padding(start=20.dp))
            Text("Se aplica al instante y se recuerda para los próximos episodios.",
                color=Color(0xFF9A99A0),fontSize=12.sp,modifier=Modifier.padding(start=20.dp,end=20.dp,bottom=8.dp))
            val available=session?.optJSONArray("subtitles")?.objects().orEmpty()
            if(available.isNotEmpty()){
                Text("Pista",fontSize=13.sp,fontWeight=FontWeight.Medium,modifier=Modifier.padding(start=20.dp,top=4.dp))
                LazyRow(Modifier.fillMaxWidth().padding(vertical=6.dp),contentPadding=PaddingValues(horizontal=16.dp),
                    horizontalArrangement=Arrangement.spacedBy(8.dp)){
                    items(available.size){index->
                        val track=available[index]
                        FilterChip(selected=captionTrack==index,
                            onClick={captionTrack=index;captionExplicitOff=false
                                manualCaptionKey=if(track.optBoolean("manual_only"))track.optString("key") else ""
                                manualCaptionAudio=selectedAudioStream
                                subtitleSync=0
                                subtitleSyncStatus=if(track.optBoolean("manual_only"))"Sincronía sin comprobar; puedes ajustarla manualmente." else ""
                                if(track.optBoolean("manual_only")){
                                    if(remote){
                                        captionPauseAfterReload=castContext?.sessionManager?.currentCastSession?.remoteMediaClient?.isPaused==true
                                        startCast()
                                    }else {reloadSubtitles();applyCaptionTrack()}
                                }else applyCaptionTrack()
                                prepareSubtitles()},
                            label={Text(track.optString("label","Español "+(index+1))+
                                (if(track.optBoolean("manual_only"))" · sincronía sin comprobar" else ""),fontSize=12.sp)})
                    }
                    item{FilterChip(selected=captionTrack<0,onClick={captionTrack=-1;captionExplicitOff=true;manualCaptionKey="";applyCaptionTrack()},
                        label={Text("Ninguno",fontSize=12.sp)})}
                }
            }
            OutlinedButton(onClick={retrySubtitleSearch()},
                enabled=!subtitleListLoading && !subtitleSyncing && session?.optBoolean("subtitles_pending")!=true,
                modifier=Modifier.padding(horizontal=20.dp,vertical=2.dp)){
                Text(if(subtitleListLoading || session?.optBoolean("subtitles_pending")==true)"Buscando alternativas…" else "Volver a buscar subtítulos")
            }
            Text("Tamaño",fontSize=13.sp,fontWeight=FontWeight.Medium,modifier=Modifier.padding(start=20.dp,top=4.dp))
            Row(Modifier.fillMaxWidth().padding(horizontal=16.dp,vertical=6.dp),horizontalArrangement=Arrangement.spacedBy(8.dp)){
                CAPTION_SIZES.forEachIndexed{index,(name,_)->
                    FilterChip(selected=captionSize==index,onClick={captionSize=index;captions.size=index;applyCaptions()},label={Text(name,fontSize=12.sp)})
                }
            }
            Text("Sincronía",fontSize=13.sp,fontWeight=FontWeight.Medium,modifier=Modifier.padding(start=20.dp,top=10.dp))
            Text(if(subtitleSync==0L)"Sin ajuste manual de tiempo." else
                    (if(subtitleSync>0)"El texto se adelanta " else "El texto se retrasa ")+
                    String.format("%.1f",kotlin.math.abs(subtitleSync)/1000.0)+" s",
                color=Color(0xFF9A99A0),fontSize=12.sp,modifier=Modifier.padding(start=20.dp,end=20.dp,top=2.dp))
            Row(Modifier.fillMaxWidth().padding(horizontal=16.dp,vertical=6.dp),
                horizontalArrangement=Arrangement.spacedBy(8.dp),verticalAlignment=Alignment.CenterVertically){
                OutlinedButton(onClick={subtitleSync-=500;reloadSubtitles()}){Text("−0,5 s")}
                OutlinedButton(onClick={subtitleSync=0;reloadSubtitles()}){Text("Centrar")}
                OutlinedButton(onClick={subtitleSync+=500;reloadSubtitles()}){Text("+0,5 s")}
            }
            val canAutoSync=available.getOrNull(captionTrack)?.optString("source_id").orEmpty().isNotBlank()
            if(canAutoSync || subtitleSyncing){
                Button(onClick={autoSyncSubtitle()},enabled=canAutoSync && !subtitleSyncing,
                    modifier=Modifier.padding(horizontal=20.dp,vertical=2.dp)){
                    Text(if(subtitleSyncing)"Sincronizando…" else "Sincronizar automáticamente")
                }
            }
            if(subtitleSyncStatus.isNotBlank())Text(subtitleSyncStatus,color=Color(0xFF9A99A0),fontSize=12.sp,
                modifier=Modifier.padding(horizontal=20.dp,vertical=2.dp))
            // En el teléfono el ajuste se ve solo; en la TV hay que reenviar el subtítulo.
            if(remote)Button(onClick={showCaptions=false;startCast()},
                modifier=Modifier.padding(horizontal=20.dp,vertical=2.dp)){Text("Aplicar en la TV")}
            Text("Estilo",fontSize=13.sp,fontWeight=FontWeight.Medium,modifier=Modifier.padding(start=20.dp,top=6.dp))
            LazyColumn(Modifier.fillMaxWidth().heightIn(max=340.dp)){
                items(CAPTION_PRESETS){preset->
                    val index=CAPTION_PRESETS.indexOf(preset)
                    ListItem(
                        headlineContent={Text(preset.name,color=if(captionPreset==index)Red else Color.White)},
                        supportingContent={Text(preset.detail,color=Color(0xFF9A99A0),fontSize=12.sp)},
                        leadingContent={CaptionSample(preset)},
                        trailingContent={if(captionPreset==index)Icon(Icons.Default.Check,"Elegido",tint=Red)},
                        colors=ListItemDefaults.colors(containerColor=Color(0xFF17171A)),
                        modifier=Modifier.clickable{captionPreset=index;captions.preset=index;applyCaptions()})
                }
            }
            Spacer(Modifier.height(12.dp))
          }
        }
    }

    private fun updateAudio(result:JSONObject):Boolean{
        val tracks=result.optJSONArray("audio_tracks")?:return false
        val previous=selectedAudioStream
        audioTracks=tracks.objects()
        selectedAudioStream=result.optInt("audio_stream_index",-1)
        if(previous>=0 && previous!=selectedAudioStream){
            manualCaptionKey=""
            val available=session?.optJSONArray("subtitles")?.objects().orEmpty()
            val chosen=available.getOrNull(captionTrack)
            if(chosen?.optBoolean("validation_required")==true && chosen.optBoolean("automatic")){
                val original=available.indexOfFirst{it.optString("source_id").isNotBlank() && it.optString("key")==chosen.optString("key")}
                if(original>=0){captionTrack=original;subtitleSync=0;subtitleAttempt=""}
            }
        }
        // Viaja en customData al receptor para que al volver al reproductor no se pierda.
        session?.put("audio_tracks",JSONArray(tracks.toString()))
            ?.put("audio_stream_index",selectedAudioStream)
        return selectDefaultCaption()
    }

    private fun openAudio(){
        showAudio=true;audioStatus=""
        if(audioTracks.isNotEmpty() || activeSource.isBlank())return
        audioJob?.cancel();audioLoading=true
        audioJob=lifecycleScope.launch{
            try{
                val result=withTimeout(45000){api.call("/api/playback/audio/$activeSource")}
                val wrapped=JSONObject().put("audio_tracks",result.optJSONArray("items")?:JSONArray())
                    .put("audio_stream_index",result.optInt("audio_stream_index",-1))
                if(updateAudio(wrapped)){
                    if(remote){
                        captionPauseAfterReload=castContext?.sessionManager?.currentCastSession?.remoteMediaClient?.isPaused==true
                        startCast()
                    }else reloadSubtitles()
                }
                if(audioTracks.size<2)audioStatus="Esta edición contiene una sola pista de audio."
            }catch(e:CancellationException){throw e}
            catch(e:Exception){audioStatus=e.message.orEmpty().ifBlank{"No se pudieron leer las pistas de esta edición."}}
            finally{audioLoading=false;prepareSubtitles()}
        }
    }

    private fun switchAudio(track:JSONObject){
        val data=session?:return
        val index=track.optInt("stream_index",-1)
        if(index<0 || index==selectedAudioStream)return
        val target=position
        val requestGeneration=generation
        showAudio=false;audioLoading=true;loading=true;stage=STAGE_PREPARING
        message="Cambiando el audio…"
        audioJob?.cancel()
        audioJob=lifecycleScope.launch{
            try{
                val moved=withTimeout(120000){api.call("/api/playback/cast","POST",
                    JSONObject().put("source_id",data.getString("source_id")).put("position_ms",target)
                        .put("audio_stream_index",index))}
                if(requestGeneration!=generation)return@launch
                if(remote)sendToCast(data,moved,(target-moved.optLong("offset_ms",0)).coerceAtLeast(0))
                else loadAdapted(data,moved,(target-moved.optLong("offset_ms",0)).coerceAtLeast(0))
                note("Audio: "+track.optString("label","pista elegida"))
            }catch(e:CancellationException){throw e}
            catch(e:Exception){loading=false;stage=STAGE_READY;audioStatus=e.message.orEmpty();note("No se pudo cambiar el audio.")}
            finally{audioLoading=false}
        }
    }

    /** Selector explícito: la TV recibe una nueva lista con una sola pista inequívoca. */
    @Composable
    private fun AudioSheet(){
        ModalBottomSheet(onDismissRequest={if(!audioLoading)showAudio=false},containerColor=Color(0xFF17171A),
            sheetState=rememberModalBottomSheetState(skipPartiallyExpanded=true)){
            Column(Modifier.navigationBarsPadding().padding(bottom=12.dp)){
                Text("Audio",fontSize=19.sp,fontWeight=FontWeight.SemiBold,modifier=Modifier.padding(horizontal=20.dp))
                Text("La opción elegida se aplica desde el mismo punto de la película.",
                    color=Color(0xFF9A99A0),fontSize=12.sp,
                    modifier=Modifier.padding(start=20.dp,end=20.dp,bottom=8.dp))
                if(audioLoading){
                    LinearProgressIndicator(Modifier.fillMaxWidth(),color=Red)
                    Text("Leyendo las pistas del video…",color=Color(0xFFB9B8BE),fontSize=12.sp,
                        modifier=Modifier.padding(20.dp))
                }else if(audioTracks.isEmpty()){
                    Text(audioStatus.ifBlank{"Esta edición no informa pistas de audio alternativas."},
                        color=Color(0xFFB9B8BE),fontSize=13.sp,modifier=Modifier.padding(20.dp))
                }else LazyColumn(Modifier.fillMaxWidth().heightIn(max=360.dp)){
                    items(audioTracks){track->
                        val index=track.optInt("stream_index",-1)
                        val selected=index==selectedAudioStream
                        ListItem(
                            headlineContent={Text(track.optString("label","Pista de audio"),
                                color=if(selected)Red else Color.White)},
                            supportingContent={Text(if(selected)"En reproducción" else "Cambiar a esta pista",
                                color=Color(0xFF9A99A0),fontSize=12.sp)},
                            trailingContent={if(selected)Icon(Icons.Default.Check,"Elegida",tint=Red)},
                            colors=ListItemDefaults.colors(containerColor=Color(0xFF17171A)),
                            modifier=Modifier.clickable(enabled=!selected){switchAudio(track)})
                    }
                }
            }
        }
    }

    /** Muestra el aspecto real del preset antes de elegirlo. */
    @Composable
    private fun CaptionSample(preset:CaptionPreset){
        Box(Modifier.size(width=62.dp,height=34.dp).background(Color(0xFF2A2A30),RoundedCornerShape(5.dp)),
            contentAlignment=Alignment.Center){
            Text("Hola",fontSize=14.sp,fontWeight=FontWeight.SemiBold,
                color=Color(preset.foreground),
                modifier=Modifier.background(Color(preset.background),RoundedCornerShape(2.dp)).padding(horizontal=3.dp))
        }
    }

    @Composable
    private fun SourcePicker(){
        Column(Modifier.fillMaxSize().statusBarsPadding().navigationBarsPadding()){
            Row(Modifier.fillMaxWidth().padding(horizontal=8.dp,vertical=6.dp),verticalAlignment=Alignment.CenterVertically){
                IconButton(onClick={leavePlayer()}){Icon(Icons.Default.Close,"Volver",tint=Color.White)}
                Column(Modifier.weight(1f).padding(horizontal=6.dp)){
                    Text(content.optString("name"),maxLines=1,fontSize=17.sp,fontWeight=FontWeight.SemiBold)
                    val subtitle=episodeLabel()
                    if(subtitle.isNotBlank())Text(subtitle,color=Color(0xFFB9B8BE),fontSize=12.sp,maxLines=1)
                }
                AndroidView(factory={context->MediaRouteButton(context).apply{runCatching{CastButtonFactory.setUpMediaRouteButton(context,this)}}},
                    modifier=Modifier.size(46.dp))
            }
            if(loading && choices.isEmpty()){BrandScreen(message,modifier=Modifier.weight(1f));return@Column}
            if(loading)LinearProgressIndicator(Modifier.fillMaxWidth(),color=Red)
            if(message.isNotBlank())Text(message,modifier=Modifier.padding(horizontal=18.dp,vertical=10.dp),fontSize=14.sp)
            if(preparationStatus.isNotBlank()){
                Text(preparationStatus,color=Color(0xFFB9B8BE),fontSize=12.sp,modifier=Modifier.padding(horizontal=18.dp))
                TextButton(onClick={stopPlayback()}){Text("Cancelar preparación")}
            }
            if(!loading && choices.isEmpty()){
                Text("No se encontraron fuentes para este título con la configuración actual.",
                    modifier=Modifier.padding(18.dp),color=Color(0xFFB9B8BE))
                Button(onClick={loadSources()},modifier=Modifier.padding(horizontal=18.dp)){Text("Volver a buscar")}
            }
            LazyColumn(Modifier.weight(1f),contentPadding=PaddingValues(16.dp),verticalArrangement=Arrangement.spacedBy(9.dp)){
                items(choices){source->
                    Column(Modifier.fillMaxWidth().clip(RoundedCornerShape(10.dp))
                        .background(Color(0xFF1C1C21)).clickable(enabled=!loading){play(source,manual=true)}.padding(14.dp)){
                        Text(source.optString("label"),fontSize=13.sp,maxLines=3)
                        // Ver el proveedor y el motivo técnico ayuda a elegir a mano cuando el orden automático falla.
                        Text(sourceDetail(source),color=Color(0xFF9A99A0),fontSize=11.sp,modifier=Modifier.padding(top=5.dp))
                    }
                }
            }
        }
    }

    // ------------------------------------------------------------- reproducción

    private fun sourceDetail(source:JSONObject):String{
        val provider=source.optString("provider_name").ifBlank{source.optString("provider")}
        val size=source.optDouble("size_gb",0.0).takeIf{it>0}?.let{String.format("%.1f GB",it)}
        val reasons=source.optJSONArray("reasons")?.let{list->(0 until list.length()).map{list.optString(it)}}?.filter{it.isNotBlank()}.orEmpty()
        return listOfNotNull(provider.takeIf{it.isNotBlank()},size,reasons.take(3).joinToString(" · ").takeIf{it.isNotBlank()})
            .joinToString(" — ")
    }
    private fun loadSources(){
        sourceJob?.cancel()
        sourceJob=lifecycleScope.launch{
            loading=true;choosing=true;stage=STAGE_SOURCES
            message=if(channelId.isNotBlank())"Sintonizando el canal…" else "Buscando fuentes…"
            // Si el equipo ya está preparando o sirviendo este mismo video, se retoma esa
            // sesión en vez de empezar de cero: volver a la app no debe reiniciar nada.
            val running=runCatching{api.call("/api/playback/status")}.getOrNull()
            if(running!=null && running.optBoolean("active") && running.optString("video_id")==videoId
                && running.optString("source_id").isNotBlank()){
                val existing=JSONObject().put("source_id",running.getString("source_id"))
                    .put("label","Retomando lo que ya estaba en marcha").put("transport","http")
                reattaching=true
                choices=listOf(existing);attempted.clear();autoRecover=true
                play(existing);return@launch
            }
            searchSources()
        }
    }
    /** Busca la lista de fuentes desde cero. Es el camino normal y el respaldo del reintento. */
    private fun restartSearch(){
        sourceJob?.cancel()
        sourceJob=lifecycleScope.launch{loading=true;choosing=true;stage=STAGE_SOURCES;searchSources()}
    }
    private suspend fun searchSources(){
        reattaching=false
        try{
            val data=if(channelId.isNotBlank())api.call("/api/tv/tune","POST",JSONObject().put("channel_id",channelId))
                else api.call("/api/playback/sources/${content.optString("type","movie")}/$videoId")
            choices=data.items()
            attempted.clear();autoRecover=true
            val failed=data.optJSONArray("failures")?.objects()?.map{it.optString("name")}?.filter{it.isNotBlank()}.orEmpty()
            val warning=if(failed.isEmpty())"" else " Sin respuesta de: ${failed.joinToString(", ")}."
            message=if(choices.isEmpty())"No se encontraron fuentes.$warning"
                else if(channelId.isNotBlank())"Conectando con la señal…" else "Preparando la mejor fuente disponible…$warning"
        }catch(e:CancellationException){throw e}
        catch(e:Exception){message=e.message.orEmpty();choices=emptyList()}finally{loading=false}
        preferredSource()?.let{play(it)}
    }
    /** El backend ya ordenó las fuentes con idioma, caudal y calidad como claves jerárquicas.
     *  Volver a maximizar el puntaje aquí deshacía ese orden y podía elegir otro idioma. */
    private fun preferredSource():JSONObject?{
        val pending=choices.filter{it.optString("source_id") !in attempted}
        if(pending.any{it.has("score")}) return pending.firstOrNull()
        return pending.sortedByDescending{
            val label=it.optString("label").lowercase()
            (if(it.optString("transport")=="http")100 else 0)+
            it.optInt("seeders").coerceAtMost(50)+
            (if(listOf("spanish","español","latino","castellano").any{word->word in label})40 else 0)+
            (if("mp4" in label)15 else 0)+(if("avc" in label || "x264" in label)10 else 0)-
            (if(listOf("remux","3d","2160","hevc","x265").any{word->word in label})30 else 0)
        }.firstOrNull()
    }
    /** Distingue la sesión que el equipo soltó de una fuente que se cayó. */
    private fun failed(error:Exception,fallback:String){
        if((error as? ApiError)?.code=="playback_stopped"){idleStop();return}
        recover(error.message.orEmpty().ifBlank{fallback},(error as? ApiError)?.code?:"network_timeout",error is ApiError)
    }
    /** El equipo soltó la sesión por inactividad. Se dice y se ofrece retomarla:
     *  buscar otra edición en silencio hacía parecer que la fuente estaba rota. */
    private fun idleStop(){
        cancelPreparation()
        activeSource="";session=null;choices=emptyList()
        choosing=true;loading=false;autoRecover=false
        message="La reproducción se detuvo por inactividad. Pulsa «Volver a buscar» para retomarla."
    }
    private fun recover(reason:String,code:String="player_error",serverFailure:Boolean=false){
        loading=false
        // Liberar el intento fallido antes de ocupar el PC con el siguiente.
        val failedSource=activeSource
        val next=if(autoRecover && attempted.size<MAX_ATTEMPTS)preferredSource() else null
        finishBuffering()
        if(!serverFailure)trace("error",JSONObject().put("error_code",code))
        trace("source_switch",JSONObject().put("switch_reason","automatic").put("error_code",code)
            .apply{next?.optString("fingerprint")?.takeIf{it.isNotBlank()}?.let{put("next_fingerprint",it)}})
        traceClosed=true
        // Un destino de salto pendiente nunca sustituye al último punto visto.
        if(firstFrameReported && !jumping && position>0)requestedStartMs=position
        castContext?.sessionManager?.currentCastSession?.remoteMediaClient?.stop()
        cancelPreparation();activeSource=""
        releaseBackend(failedSource)
        // El PC ya anota los fallos de preparación; aquí solo los que él no puede ver.
        if(firstFrameReported)reportMetric(failedSource,"error")
        // Lo que estaba en marcha ya no sirve: se busca la lista completa en vez de
        // dejar la pantalla sin salida con una sola fuente probada.
        if(reattaching){
            reattaching=false
            message="Retomando la reproducción desde el catálogo…"
            restartSearch();return
        }
        if(next!=null){
            message="$reason Probando otra fuente (${attempted.size+1} de $MAX_ATTEMPTS)…"
            play(next)
        }
        else message=if(attempted.size>=MAX_ATTEMPTS)"$reason Se probaron $MAX_ATTEMPTS fuentes sin éxito. Elige otra edición o vuelve al catálogo."
            else "$reason Puedes elegir otra edición o volver al catálogo."
    }
    /** Código HTTP que provocó el fallo, si lo hubo, mirando toda la cadena de causas. */
    private fun httpCode(error:Throwable?):Int{
        var cause=error
        while(cause!=null){
            if(cause is HttpDataSource.InvalidResponseCodeException)return cause.responseCode
            cause=cause.cause
        }
        return 0
    }
    /** Tiempo real desde Play hasta el primer fotograma; una sola medición por fuente. */
    private fun noteFirstFrame(){
        prepareSubtitles()
        if(firstFrameReported || activeSource.isBlank() || playRequestedAt==0L)return
        firstFrameReported=true
        trace("first_frame",JSONObject().put("elapsed_ms",(System.currentTimeMillis()-playRequestedAt).coerceIn(0L,86400000L)))
        reportMetric(activeSource,"first_frame",System.currentTimeMillis()-playRequestedAt)
    }
    private fun noteBuffering(){
        if(!firstFrameReported || activeSource.isBlank() || jumping)return
        if(bufferingSince==0L){
            bufferingSince=System.currentTimeMillis();stableMs=0;stableReported=false
            trace("buffering")
        }
        if(!bufferingReported){bufferingReported=true;reportMetric(activeSource,"buffering")}
    }
    private fun trace(event:String,fields:JSONObject=JSONObject()){
        if(!::journal.isInitialized || attemptId.isBlank() || traceClosed)return
        fields.put("position_ms",position.coerceIn(0L,86400000L))
        if(!remote && ::player.isInitialized)fields.put("buffer_ms",(player.bufferedPosition-player.currentPosition).coerceIn(0L,86400000L))
        journal.event(attemptId,event,fields)
    }
    private fun finishBuffering(){
        if(bufferingSince>0){trace("buffering_end",JSONObject().put("duration_ms",(System.currentTimeMillis()-bufferingSince).coerceIn(0L,86400000L)));bufferingSince=0}
    }
    private fun reportMetric(source:String,event:String,startMs:Long=-1){
        if(source.isBlank())return
        val payload=JSONObject().put("source_id",source).put("event",event)
        if(startMs>=0)payload.put("start_ms",startMs.coerceAtMost(600000))
        progressWriter.launch{runCatching{withTimeout(8000){api.call("/api/playback/metric","POST",payload)}}}
    }
    private fun releaseBackend(source:String){
        if(source.isBlank())return
        progressWriter.launch{runCatching{withTimeout(10000){api.call("/api/playback/stop","POST",JSONObject().put("source_id",source))}}}
    }
    private fun cancelPreparation(){
        generation++;prepareJob?.cancel();castJob?.cancel();queueJob?.cancel();countdownJob?.cancel()
        seekJob?.cancel();audioJob?.cancel();subtitleSyncJob?.cancel();countdownToNext?.cancel();nextIn=-1;jumping=false;videoReady=false
        freeRotation=false
        adapted=false;offsetMs=0;totalMs=0;lastMoved=null;subtitleSync=0
        showAudio=false;audioTracks=emptyList();selectedAudioStream=-1;audioLoading=false;audioStatus=""
        subtitleSyncing=false;subtitleSyncStatus="";subtitleAttempt="";subtitleListLoading=false
        subtitleRetryRequested=false;subtitleSearchDeadline=0L;subtitleSearchKey=""
        captionPauseAfterReload=false;stage=STAGE_SOURCES
        preparationStatus="";choosing=true;remote=false;loading=false;castAccepted=false;remoteWaitingSince=0L
        seekLimit=Long.MAX_VALUE;position=0;duration=0;playing=false;scrubbing=null;retriedSource=""
        requestedOrientation=ActivityInfo.SCREEN_ORIENTATION_UNSPECIFIED
        player.stop();player.clearMediaItems()
    }
    private fun stopPlayback(){
        saveProgress()
        trace("cancelled");traceClosed=true
        val source=activeSource
        sourceJob?.cancel();autoRecover=false
        castContext?.sessionManager?.currentCastSession?.remoteMediaClient?.stop()
        cancelPreparation();activeSource="";session=null
        message="Deteniendo…";loading=true
        val stoppedGeneration=generation
        // La liberación debe terminar aunque se vuelva al catálogo inmediatamente.
        progressWriter.launch{
            val result=runCatching{withTimeout(10000){api.call("/api/playback/stop","POST",JSONObject().apply{if(source.isNotBlank())put("source_id",source)})}}
            kotlinx.coroutines.withContext(Dispatchers.Main){if(generation==stoppedGeneration){
                loading=false
                message=if(result.isSuccess)"Reproducción detenida." else "No se pudo confirmar la detención. Vuelve a intentarlo."
            }}
        }
    }
    private fun leavePlayer(){
        // Atrás no corta la película: la deja en miniatura, como cualquier reproductor
        // moderno. También en pausa: pausar antes de salir no puede costar la preparación.
        if(!remote && !choosing && stage==STAGE_READY && enterPip())return
        if(remote && castAccepted){saveProgress();finish()}else{closePlayer()}
    }
    private fun closePlayer(){remember(null);stopPlayback();finish()}
    /** Deja constancia de lo que se está viendo para el acceso «Volver a la reproducción». */
    private fun remember(data:JSONObject?){
        val prefs=getSharedPreferences("playing",MODE_PRIVATE).edit()
        if(data==null)prefs.clear() else prefs.putString("content",content.toString())
            .putString("video_id",videoId).putString("channel_id",channelId).putString("name",content.optString("name"))
        prefs.apply()
    }
    private fun enterPip():Boolean{
        if(Build.VERSION.SDK_INT<Build.VERSION_CODES.O)return false
        return runCatching{
            enterPictureInPictureMode(PictureInPictureParams.Builder().setAspectRatio(Rational(16,9)).build())
        }.getOrDefault(false)
    }
    override fun onUserLeaveHint(){
        if(!inPip && !remote && !choosing && stage==STAGE_READY)enterPip()
        super.onUserLeaveHint()
    }
    override fun onPictureInPictureModeChanged(isInPictureInPictureMode:Boolean,newConfig:Configuration){
        super.onPictureInPictureModeChanged(isInPictureInPictureMode,newConfig)
        inPip=isInPictureInPictureMode
        if(!inPip)revealControls()
    }
    override fun onNewIntent(intent:Intent){
        super.onNewIntent(intent)
        val target=intent.getStringExtra("video_id").orEmpty()
        // Volver desde el catálogo a lo que ya está sonando no reinicia nada.
        if(target.isBlank())return
        val explicit=intent.getLongExtra("start_ms",-1L).takeIf{it>=0}
        if(target==videoId){
            // Una orden explícita sí manda aunque sea el mismo capítulo: es el caso
            // «Ver desde el inicio» mientras su reproducción todavía está abierta.
            if(explicit!=null){requestedStartMs=explicit;if(stage==STAGE_READY)seekTo(explicit) else{cancelPreparation();loadSources()}}
            return
        }
        setIntent(intent)
        content=JSONObject(intent.getStringExtra("content")?:"{}")
        videoId=target;channelId=intent.getStringExtra("channel_id").orEmpty()
        requestedStartMs=explicit
        cancelPreparation();loadSources()
    }
    /** Toma la sesión que el televisor ya está reproduciendo, sin reenviar nada.

     *  Volver al reproductor desde el catálogo relanzaba la cola y la TV reiniciaba
     *  la película que se estaba viendo. */
    private fun adoptRunningCast():Boolean{
        val session=castContext?.sessionManager?.currentCastSession?:return false
        if(!session.isConnected)return false
        val client=session.remoteMediaClient?:return false
        val info=client.mediaInfo?.customData?:return false
        if(info.optString("video_id")!=videoId)return false
        if(client.playerState==MediaStatus.PLAYER_STATE_IDLE || client.playerState==MediaStatus.PLAYER_STATE_UNKNOWN)return false
        this.session=info
        activeSource=info.optString("source_id")
        adapted=info.optBoolean("adapted",false)
        offsetMs=info.optLong("offset_ms",0)
        totalMs=info.optLong("duration_ms",0)
        seekLimit=info.optLong("seekable_ms",Long.MAX_VALUE)
        updateAudio(info)
        remote=true;castAccepted=true;choosing=false;loading=false
        stage=STAGE_READY;message=""
        // Si esta sesión resulta no servir, se cae a la búsqueda normal en vez de
        // quedarse sin salida: es para lo que existe esta marca.
        reattaching=true
        remember(info)
        return true
    }
    private fun play(source:JSONObject,manual:Boolean=false){
        if(manual){
            saveProgress()
            finishBuffering()
            trace("source_switch",JSONObject().put("switch_reason","manual")
                .apply{source.optString("fingerprint").takeIf{it.isNotBlank()}?.let{put("next_fingerprint",it)}})
            if(firstFrameReported && !jumping)requestedStartMs=position
        }
        // No se detiene el receptor aquí: la cola nueva sustituye a la anterior y así la
        // TV sigue mostrando algo mientras el PC prepara, en vez de quedarse en negro.
        cancelPreparation()
        activeSource=source.getString("source_id")
        manualCaptionKey=""
        attemptId=java.util.UUID.randomUUID().toString();traceClosed=false
        bufferingSince=0;stableMs=0;stableReported=false;tracePosition=0;recoveringTrace=false
        playRequestedAt=System.currentTimeMillis();firstFrameReported=false;bufferingReported=false
        val requestGeneration=generation
        attempted.add(source.getString("source_id"))
        prepareJob=lifecycleScope.launch{
            loading=true;stage=STAGE_PREPARING;message="Preparando la reproducción…"
            countdownJob=lifecycleScope.launch{
                for(seconds in 0..84){preparationStatus="Fuente ${attempted.size} de $MAX_ATTEMPTS · ${seconds}s";delay(1000)}
            }
            try{
                session=withTimeout(85000){api.call("/api/playback/start","POST",JSONObject().put("source_id",source.getString("source_id"))
                    .put("attempt_id",attemptId).put("playback_id",playbackId).put("client",if(remote)"cast" else "android"))}
                if(requestGeneration!=generation)return@launch
                countdownJob?.cancel();preparationStatus=""
                sequence=0;choosing=false
                val data=session!!
                if(castContext?.sessionManager?.currentCastSession?.isConnected==true){
                    // Cast consume el video directamente: no abrir ExoPlayer en paralelo.
                    remember(data);startCast();return@launch
                }
                // El teléfono también necesita un formato decodificable (p. ej. MKV/E-AC3).
                message="Preparando el video…"
                countdownJob=lifecycleScope.launch{for(seconds in 0..74){preparationStatus="Preparando · ${seconds}s";delay(1000)}}
                // Retomar donde se dejó: el PC convierte desde ese punto, no desde el principio.
                val resume=if(channelId.isBlank())requestedStartMs?:data.optLong("position_ms") else 0L
                val moved=withTimeout(120000){api.call("/api/playback/cast","POST",
                    JSONObject().put("source_id",data.getString("source_id")).put("position_ms",resume))}
                if(requestGeneration!=generation)return@launch
                countdownJob?.cancel();preparationStatus=""
                data.put("stream_url",moved.getString("stream_url")).put("content_type",moved.getString("content_type"))
                // El trabajo adelantado puede empezar antes de donde se retoma: por eso el
                // punto se calcula contra `offset_ms` y no se asume cero.
                loadAdapted(data,moved,resumeAt=(resume-moved.optLong("offset_ms",0)).coerceAtLeast(0))
                remember(data)
                // Pantalla completa apaisada mientras se ve en el teléfono, como cualquier reproductor.
                requestedOrientation=ActivityInfo.SCREEN_ORIENTATION_SENSOR_LANDSCAPE
                revealControls()
            }catch(e:kotlinx.coroutines.TimeoutCancellationException){countdownJob?.cancel();preparationStatus="";recover("Esta fuente tardó demasiado.")}
            catch(e:CancellationException){throw e}
            catch(e:Exception){countdownJob?.cancel();preparationStatus="";failed(e,"No se pudo preparar la fuente.")}
        }
    }
    /** Cuenta atrás tipo Netflix al terminar un episodio, con opción de cancelar. */
    private fun startNextCountdown(){
        val episodes=content.optJSONArray("episodes")?.objects().orEmpty()
        val current=episodes.indexOfFirst{it.optString("id")==videoId}
        if(current<0 || current+1>=episodes.size){nextEpisode();return}
        // En la TV no se pregunta: quien mira está en el sillón y el teléfono en un
        // bolsillo, así que cortar por no responder aquí sería cortar una sala llena.
        if(!remote && autoAdvances>=AUTO_ADVANCE_LIMIT){askPresence();return}
        countdownToNext?.cancel()
        countdownToNext=lifecycleScope.launch{
            for(remaining in 10 downTo 1){nextIn=remaining;delay(1000)}
            nextIn=-1;autoAdvances++;nextEpisode()
        }
    }
    /** Cualquier gesto del usuario devuelve la cuenta de episodios encadenados a cero. */
    private fun touched(){autoAdvances=0}
    private fun askPresence(){
        saveProgress()
        askingPresence=true;presenceLeft=PRESENCE_SECONDS
        presenceJob?.cancel()
        presenceJob=lifecycleScope.launch{
            while(presenceLeft>0){delay(1000);presenceLeft--}
            askingPresence=false;stopPlayback()
        }
    }
    private fun cancelNextCountdown(){countdownToNext?.cancel();nextIn=-1}
    private fun nextEpisode(){
        val episodes=content.optJSONArray("episodes")?.objects()?:run{releaseBackend(activeSource);activeSource="";return}
        val current=episodes.indexOfFirst{it.optString("id")==videoId}
        if(current<0 || current+1>=episodes.size){releaseBackend(activeSource);activeSource="";message="Terminaste los episodios disponibles.";return}
        openEpisode(episodes[current+1].getString("id"))
    }
    /** Cambiar de episodio, tanto por la lista como al terminar el anterior. */
    private fun openEpisode(target:String,startAt:Long?=null){
        touched();cancelNextCountdown()
        trace("cancelled");traceClosed=true;playbackId=java.util.UUID.randomUUID().toString()
        videoId=target
        requestedStartMs=startAt
        attempted.clear();autoRecover=true
        cancelPreparation()
        sourceJob?.cancel()
        sourceJob=lifecycleScope.launch{
            loading=true;message="Buscando fuentes del episodio…"
            try{
                choices=api.call("/api/playback/sources/series/$videoId").items()
                if(choices.isEmpty()){message="Este episodio no tiene fuentes disponibles.";choosing=true}
                else preferredSource()?.let{play(it)}
            }catch(e:CancellationException){throw e}
            catch(e:Exception){message=e.message.orEmpty();choosing=true}finally{loading=false}
        }
    }
    private fun castItem(data:JSONObject,title:String):MediaQueueItem {
        val metadata=MediaMetadata(MediaMetadata.MEDIA_TYPE_MOVIE).apply{putString(MediaMetadata.KEY_TITLE,title)}
        val tracks=data.optJSONArray("subtitles")?.objects()?.mapIndexed{index,track->
            MediaTrack.Builder(index.toLong()+1,MediaTrack.TYPE_TEXT).setSubtype(MediaTrack.SUBTYPE_SUBTITLES)
                .setContentId(subtitleUrl(track.getString("url"))).setContentType("text/vtt").setLanguage("es").setName(track.optString("label")).build()
        }?:emptyList()
        val info=MediaInfo.Builder(data.getString("stream_url")).setContentType(data.optString("content_type","video/mp4"))
            .setStreamType(MediaInfo.STREAM_TYPE_BUFFERED).setMetadata(metadata).setCustomData(data).setMediaTracks(tracks)
            // El receptor dibuja sus propios subtítulos: recibe el equivalente del estilo elegido.
            .setTextTrackStyle(CAPTION_PRESETS[captionPreset].cast(CAPTION_SIZES[captionSize].second)).build()
        val chosen=if(!captionUsable() || captionTrack>=tracks.size)longArrayOf() else longArrayOf(captionTrack.toLong()+1)
        return MediaQueueItem.Builder(info).setAutoplay(true).apply{if(chosen.isNotEmpty())setActiveTrackIds(chosen)}.build()
    }
    private fun startCast(){
        // Una preparación anterior no puede impedir la siguiente: se cancela y se sigue.
        // Ese bloqueo era lo que dejaba el televisor sin responder al cambiar de video.
        if(choosing)return
        castJob?.cancel()
        castAccepted=false;remoteWaitingSince=0L
        val data=session?:return
        val client=castContext?.sessionManager?.currentCastSession?.remoteMediaClient?:return
        // Posición real en la película: `player.currentPosition` mide desde el inicio de
        // la lista convertida, no del video, y enviaba a la TV a un punto equivocado.
        val at=if(position>0)position else requestedStartMs?:data.optLong("position_ms")
        val requestGeneration=generation
        remote=true;player.stop();player.clearMediaItems();loading=true;stage=STAGE_SENDING
        message="Enviando el video a tu dispositivo…"
        requestedOrientation=ActivityInfo.SCREEN_ORIENTATION_UNSPECIFIED
        castJob=lifecycleScope.launch{
            countdownJob?.cancel()
            countdownJob=lifecycleScope.launch{for(seconds in 0..74){preparationStatus="Enviando · ${seconds}s";delay(1000)}}
            try{
                // La TV arranca donde se quedó: el PC convierte desde ese punto.
                val cast=withTimeout(120000){api.call("/api/playback/cast","POST",
                    JSONObject().put("source_id",data.getString("source_id")).put("position_ms",at))}
                if(requestGeneration!=generation)return@launch
                countdownJob?.cancel();preparationStatus="";loading=false
                sendToCast(data,cast,at-cast.optLong("offset_ms",0))
            }catch(e:kotlinx.coroutines.TimeoutCancellationException){
                countdownJob?.cancel();preparationStatus=""
                playHereFrom(at,"El envío tardó demasiado. Sigue aquí…")
            }
            catch(e:CancellationException){throw e}
            catch(e:Exception){
                countdownJob?.cancel();preparationStatus=""
                playHereFrom(at,"No se pudo enviar al dispositivo. Sigue aquí…")
            }
        }
    }
    /** Envía la lista al receptor guardando el desplazamiento, igual que en el teléfono. */
    private fun sendToCast(data:JSONObject,moved:JSONObject,startWithin:Long){
        val client=castContext?.sessionManager?.currentCastSession?.remoteMediaClient?:return
        acceptPreparedSubtitles(data,moved)
        val requestGeneration=generation
        adapted=moved.optBoolean("adapted",false)
        offsetMs=moved.optLong("offset_ms",0)
        totalMs=moved.optLong("duration_ms",0)
        seekLimit=moved.optLong("seekable_ms",Long.MAX_VALUE)
        updateAudio(moved)
        // Se viaja con el desplazamiento y la duración: sin ellos, al retomar la sesión
        // desde el catálogo la posición mostrada sería la de la lista, no la de la película.
        val prepared=JSONObject(data.toString()).put("stream_url",moved.getString("stream_url"))
            .put("content_type",moved.getString("content_type"))
            .put("adapted",adapted).put("offset_ms",offsetMs)
            .put("duration_ms",totalMs).put("seekable_ms",seekLimit)
        client.queueLoad(arrayOf(castItem(prepared,content.optString("name"))),0,MediaStatus.REPEAT_MODE_REPEAT_OFF,
            startWithin.coerceAtLeast(0),null)
            .setResultCallback{result->if(requestGeneration==generation){
                if(!result.status.isSuccess){remote=false;recover("El Chromecast no pudo cargar esta fuente.")}
                else{castAccepted=true;remoteWaitingSince=System.currentTimeMillis();stage=STAGE_BUFFERING
                    message="Enviado. Cargando…";loading=true;revealControls()}
            }}
    }

    private fun observeCast(){
        if(!remote || !castAccepted || choosing)return
        // Sin sesión conectada no hay información fiable: callar es mejor que inventar un fallo.
        val session=castContext?.sessionManager?.currentCastSession
        if(session==null || !session.isConnected){remoteWaitingSince=0L;return}
        val client=session.remoteMediaClient?:return
        if(client.mediaInfo?.customData?.optString("source_id")!=activeSource)return
        when(client.playerState){
            MediaStatus.PLAYER_STATE_PLAYING->{loading=false;stage=STAGE_READY;message="";remoteWaitingSince=0L;noteFirstFrame();finishBuffering()
                if(captionPauseAfterReload){captionPauseAfterReload=false;client.pause()}}
            MediaStatus.PLAYER_STATE_PAUSED->{loading=false;stage=STAGE_READY;message="";remoteWaitingSince=0L}
            MediaStatus.PLAYER_STATE_IDLE->{
                if(client.idleReason==MediaStatus.IDLE_REASON_FINISHED){trace("ended");traceClosed=true;castAccepted=false;saveProgress(true);startNextCountdown()}
                else if(client.idleReason==MediaStatus.IDLE_REASON_ERROR){castAccepted=false;recover("El Chromecast no pudo reproducir esta edición.")}
            }
            MediaStatus.PLAYER_STATE_BUFFERING,MediaStatus.PLAYER_STATE_LOADING->{
                noteBuffering()
                loading=true;stage=STAGE_BUFFERING
                if(remoteWaitingSince==0L)remoteWaitingSince=System.currentTimeMillis()
                val waited=System.currentTimeMillis()-remoteWaitingSince
                message=if(waited>CAST_SLOW_MS)"Cargando… esta fuente va lenta." else "Cargando…"
                if(waited>CAST_GIVE_UP_MS){castAccepted=false;recover("La fuente no entregó datos suficientes.")}
            }
            // PLAYER_STATE_UNKNOWN llega cuando el receptor todavía no ha informado.
            // No dice que algo vaya mal, así que no se toca nada.
            else->remoteWaitingSince=0L
        }
    }
    private fun saveProgress(completed:Boolean=false){
        val remoteClient=castContext?.sessionManager?.currentCastSession?.remoteMediaClient
        val data=if(remote)remoteClient?.mediaInfo?.customData?:session else session
        data?:return
        if(choosing || (remote && remoteClient?.mediaInfo==null))return
        val inList=(if(remote)remoteClient?.approximateStreamPosition?:0 else player.currentPosition).coerceAtLeast(0)
        val measured=(if(remote)remoteClient?.streamDuration?:0 else player.duration).coerceAtLeast(0)
        // La lista convertida empieza en `offsetMs`. Sin sumarlo se guardaba el minuto 5 de
        // una película que iba por el 45, y al volver a abrirla retomaba media hora atrás.
        val current=offsetMs+inList
        val total=if(totalMs>0)totalMs else if(measured>0)offsetMs+measured else 0L
        sequence=maxOf(sequence+1,System.currentTimeMillis())
        val payload=JSONObject().put("session_id",data.optString("session_id")).put("sequence",sequence)
            .put("position_ms",current.coerceAtLeast(0)).put("duration_ms",total).put("completed",completed || (!remote && player.playbackState==Player.STATE_ENDED))
        progressWriter.launch{runCatching{withTimeout(10000){api.call("/api/playback/progress","PUT",payload)}}}
    }
    override fun onStop(){
        saveProgress()
        if(!remote && !inPip)player.pause()
        PlaybackLink.update(false,false)
        super.onStop()
    }
    override fun onDestroy(){
        PlaybackLink.detach(bridge)
        castContext?.sessionManager?.removeSessionManagerListener(castListener,CastSession::class.java)
        player.release();super.onDestroy()
    }
}
