package cl.videoteca.casa

import android.content.Intent
import android.os.Bundle
import androidx.activity.compose.BackHandler
import androidx.activity.compose.setContent
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.fragment.app.FragmentActivity
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.lifecycle.compose.LocalLifecycleOwner
import androidx.mediarouter.app.MediaRouteButton
import coil.compose.AsyncImage
import com.google.android.gms.cast.framework.CastButtonFactory
import com.google.android.gms.cast.framework.CastContext
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.items
import androidx.compose.material.icons.filled.LiveTv
import org.json.JSONObject
import java.net.URLEncoder

private val Red=Color(0xFFE53D49)
private val Background=Color(0xFF101011)

private fun resumeClock(ms:Long):String{
    val total=(ms.coerceAtLeast(0))/1000
    return if(total>=3600)String.format("%d:%02d:%02d",total/3600,(total%3600)/60,total%60)
    else String.format("%d:%02d",total/60,total%60)
}

class MainActivity:FragmentActivity(){
    private var pairingLink by mutableStateOf("")
    // Cuánto se lleva visto de cada ficha, para marcarlo sobre su carátula.
    private var watched by mutableStateOf<Map<String,Float>>(emptyMap())
    override fun onCreate(savedInstanceState:Bundle?){
        super.onCreate(savedInstanceState)
        pairingLink=intent?.dataString.orEmpty()
        runCatching{CastContext.getSharedInstance(this)}
        setContent{MaterialTheme(colorScheme=darkColorScheme(primary=Red,onPrimary=Color.White,background=Background,surface=Color(0xFF1C1C20))){Home(Api(this),pairingLink)}}
    }
    override fun onNewIntent(intent:Intent){super.onNewIntent(intent);pairingLink=intent.dataString.orEmpty()}

    @OptIn(ExperimentalMaterial3Api::class)
    @Composable private fun Home(api:Api,incoming:String){
        val scope=rememberCoroutineScope()
        var connected by remember{mutableStateOf(api.connected)}
        var link by remember{mutableStateOf("")}
        var error by remember{mutableStateOf("")}
        var busy by remember{mutableStateOf(false)}
        var screen by remember{mutableStateOf("Inicio")}
        var search by remember{mutableStateOf("")}
        var searchMode by remember{mutableStateOf(false)}
        var movies by remember{mutableStateOf<List<JSONObject>>(emptyList())}
        var series by remember{mutableStateOf<List<JSONObject>>(emptyList())}
        var favorites by remember{mutableStateOf<List<JSONObject>>(emptyList())}
        var continuing by remember{mutableStateOf<List<JSONObject>>(emptyList())}
        var collections by remember{mutableStateOf<List<JSONObject>>(emptyList())}
        var channels by remember{mutableStateOf<List<JSONObject>>(emptyList())}
        var channelFilter by remember{mutableStateOf("")}
        var tvSection by remember{mutableStateOf("nacional")}
        var playingNow by remember{mutableStateOf<JSONObject?>(null)}
        var selected by remember{mutableStateOf<JSONObject?>(null)}
        var movieSkip by remember{mutableIntStateOf(0)}
        var seriesSkip by remember{mutableIntStateOf(0)}
        var genre by remember{mutableStateOf("")}
        var settings by remember{mutableStateOf(false)}
        // Terminar lo que se está viendo se pregunta antes: es irreversible y cuesta
        // volver a preparar la fuente entera.
        var confirmClose by remember{mutableStateOf(false)}
        suspend fun reload(){
            busy=true;error=""
            try{
                val suffix=if(genre.isBlank())"" else "&genre="+URLEncoder.encode(genre,"UTF-8")
                movies=api.call("/api/catalog/movie?skip=0$suffix").items();movieSkip=50
                series=api.call("/api/catalog/series?skip=0$suffix").items();seriesSkip=50
                favorites=api.call("/api/library/favorites").items()
                continuing=api.call("/api/library/continue").items()
                collections=api.call("/api/collections").items()
                watched=api.call("/api/library/progress").optJSONObject("items")?.fractions().orEmpty()
            }catch(e:Exception){error=e.message.orEmpty()}finally{busy=false}
        }
        /** Lo que sigue sonando en el PC: da acceso permanente a la ventana de reproducción. */
        suspend fun refreshPlaying(){
            val saved=getSharedPreferences("playing",MODE_PRIVATE)
            val stored=saved.getString("content","").orEmpty()
            if(stored.isBlank()){playingNow=null;return}
            playingNow=runCatching{
                if(api.call("/api/playback/status").optBoolean("active"))
                    JSONObject().put("content",stored).put("video_id",saved.getString("video_id","").orEmpty())
                        .put("channel_id",saved.getString("channel_id","").orEmpty())
                        .put("name",saved.getString("name","").orEmpty())
                else null
            }.getOrNull()
        }
        fun resumePlaying(){
            val row=playingNow?:return
            startActivity(Intent(this@MainActivity,PlayerActivity::class.java)
                .putExtra("content",row.optString("content")).putExtra("video_id",row.optString("video_id"))
                .putExtra("channel_id",row.optString("channel_id")))
        }
        fun castClient()=runCatching{CastContext.getSharedInstance(this@MainActivity)
            .sessionManager.currentCastSession?.remoteMediaClient}.getOrNull()
        /** Pausa o reanuda lo que ya suena, sin abrir ni preparar nada. */
        fun togglePlaying(){
            val remote=castClient()
            when{
                PlaybackLink.live->PlaybackLink.toggle()
                remote?.mediaInfo!=null->if(remote.isPlaying)remote.pause() else remote.play()
                // No hay nada a mano que controlar: entonces sí toca abrir la ventana.
                else->resumePlaying()
            }
        }
        suspend fun endPlaying(){
            runCatching{castClient()?.stop()}
            runCatching{api.call("/api/playback/stop","POST")}
            getSharedPreferences("playing",MODE_PRIVATE).edit().clear().apply()
            playingNow=null;error="Reproducción detenida."
        }
        // Los canales se piden al abrir la pestaña y al cambiar de sección: son cientos.
        LaunchedEffect(screen,connected,tvSection){
            if(screen=="TV" && connected){
                busy=true;error="";channels=emptyList()
                try{channels=api.call("/api/tv/channels?section=$tvSection").items()}
                catch(e:Exception){error=e.message.orEmpty()}finally{busy=false}
            }
        }
        fun tune(channel:JSONObject){
            val content=JSONObject().put("id","tv:"+channel.optString("id")).put("type","tv")
                .put("name",channel.optString("name")).put("poster",channel.optString("logo"))
                .put("background",channel.optString("logo")).put("releaseInfo","En directo")
            startActivity(Intent(this@MainActivity,PlayerActivity::class.java)
                .putExtra("content",content.toString())
                .putExtra("video_id",content.getString("id"))
                .putExtra("channel_id",channel.optString("id")))
        }
        /** `startMs` distingue reanudar exactamente de forzar el comienzo. Sin este
         *  dato un episodio en sus últimos minutos ya figura como completado y el
         *  backend lo abriría en cero aunque la persona eligiera continuar. */
        fun playContent(videoId:String,startMs:Long?=null){
            val player=Intent(this@MainActivity,PlayerActivity::class.java)
                .putExtra("content",selected!!.toString()).putExtra("video_id",videoId)
            if(startMs!=null)player.putExtra("start_ms",startMs)
            startActivity(player)
        }
        fun open(item:JSONObject){scope.launch{busy=true;error="";try{
            val detail=api.call("/api/content/${item.optString("type","movie")}/${item.getString("id")}")
            val progress=continuing.firstOrNull{it.optString("id")==item.optString("id")}
            if(progress!=null)detail.put("resume_video_id",progress.optString("video_id"))
            selected=detail
            // Mientras se lee la ficha, el PC va preparando lo que se va a reproducir.
            // Medido: con medio minuto de ventaja, pulsar Reproducir arranca al instante.
            val type=detail.optString("type","movie")
            val episodes=detail.optJSONArray("episodes")?.objects().orEmpty()
            val first=detail.optString("resume_video_id").ifBlank{
                if(episodes.isEmpty())detail.optString("id") else ""}
            if(first.isNotBlank())launch{
                runCatching{api.call("/api/playback/sources/$type/$first?warm=true")}
            }
        }catch(e:Exception){error=e.message.orEmpty()}finally{busy=false}}}
        val lifecycle=LocalLifecycleOwner.current.lifecycle
        DisposableEffect(lifecycle,connected){
            val observer=LifecycleEventObserver{_,event->if(event==Lifecycle.Event.ON_RESUME && connected)scope.launch{
                runCatching{continuing=api.call("/api/library/continue").items()}
                runCatching{watched=api.call("/api/library/progress").optJSONObject("items")?.fractions().orEmpty()}
                refreshPlaying()
            }}
            lifecycle.addObserver(observer)
            onDispose{lifecycle.removeObserver(observer)}
        }
        LaunchedEffect(incoming){if(incoming.isNotBlank())link=incoming}
        LaunchedEffect(connected,genre){if(connected)reload()}
        BackHandler(selected!=null){selected=null}
        Scaffold(containerColor=Background,topBar={
            Column(Modifier.statusBarsPadding()){
                Row(Modifier.fillMaxWidth().padding(horizontal=20.dp,vertical=12.dp),verticalAlignment=Alignment.CenterVertically){
                    if(selected!=null){IconButton(onClick={selected=null}){Icon(Icons.Default.ArrowBack,"Volver")}}
                    Row(Modifier.weight(1f),verticalAlignment=Alignment.CenterVertically){
                        BrandMark(size=26.dp)
                        Spacer(Modifier.width(8.dp))
                        BrandWordmark(size=16.sp)
                    }
                    if(connected){
                        IconButton(onClick={searchMode=!searchMode;selected=null}){Icon(Icons.Default.Search,"Buscar")}
                        AndroidView(factory={context->MediaRouteButton(context).apply{runCatching{CastButtonFactory.setUpMediaRouteButton(context,this)}}},modifier=Modifier.size(44.dp))
                        IconButton(onClick={settings=true}){Icon(Icons.Default.Settings,"Configuración")}
                    }
                }
                if(searchMode && connected){OutlinedTextField(value=search,onValueChange={search=it},label={Text("Películas y series")},singleLine=true,modifier=Modifier.fillMaxWidth().padding(horizontal=20.dp),trailingIcon={IconButton(onClick={scope.launch{busy=true;error="";try{val term=URLEncoder.encode(search,"UTF-8");movies=api.call("/api/catalog/movie?search=$term").items();series=api.call("/api/catalog/series?search=$term").items();screen="Inicio"}catch(e:Exception){error=e.message.orEmpty()}finally{busy=false}}}){Icon(Icons.Default.Search,"Buscar")}})}
                if(busy)LinearProgressIndicator(Modifier.fillMaxWidth(),color=Red)
                if(connected && playingNow!=null){
                    // La barra manda sobre lo que ya suena y nunca lo relanza: los botones
                    // actúan sobre la reproducción viva y el cuerpo solo vuelve a su ventana.
                    var running by remember{mutableStateOf(false)}
                    LaunchedEffect(playingNow){
                        while(true){
                            running=PlaybackLink.playing || castClient()?.isPlaying==true
                            delay(700)
                        }
                    }
                    Row(Modifier.fillMaxWidth().padding(horizontal=20.dp,vertical=4.dp)
                        .clip(RoundedCornerShape(10.dp)).background(Color(0xFF23161A)).clickable{resumePlaying()}.padding(start=4.dp,end=4.dp,top=6.dp,bottom=6.dp),
                        verticalAlignment=Alignment.CenterVertically){
                        IconButton(onClick={togglePlaying()}){
                            Icon(if(running)Icons.Default.Pause else Icons.Default.PlayArrow,
                                if(running)"Pausar" else "Reanudar",tint=Red)
                        }
                        Column(Modifier.weight(1f).padding(horizontal=6.dp)){
                            Text("Volver a la reproducción",fontSize=13.sp,fontWeight=FontWeight.SemiBold)
                            Text(playingNow!!.optString("name"),fontSize=11.sp,color=Color.Gray,maxLines=1)
                        }
                        IconButton(onClick={confirmClose=true}){
                            Icon(Icons.Default.Close,"Terminar la reproducción",tint=Color(0xFF9A99A0))
                        }
                    }
                }
                if(connected && playingNow==null && continuing.isNotEmpty()){
                    // Al abrir la app, dónde quedó lo último. Solo cuando no hay nada
                    // sonando: si lo hay, manda la barra de control y no se apilan dos.
                    val last=continuing.first()
                    val seen=last.optDouble("progress",0.0).toFloat()
                    Row(Modifier.fillMaxWidth().padding(horizontal=20.dp,vertical=4.dp)
                        .clip(RoundedCornerShape(10.dp)).background(Color(0xFF1B1B20))
                        .clickable{open(last)}.padding(12.dp),verticalAlignment=Alignment.CenterVertically){
                        Icon(Icons.Default.PlayArrow,null,tint=Red)
                        Column(Modifier.weight(1f).padding(horizontal=10.dp)){
                            Text("Seguir donde quedaste",fontSize=13.sp,fontWeight=FontWeight.SemiBold)
                            Text(last.optString("name"),fontSize=11.sp,color=Color.Gray,maxLines=1)
                            if(seen>0f)Box(Modifier.fillMaxWidth().padding(top=6.dp).clip(RoundedCornerShape(2.dp))
                                .background(Color(0x33FFFFFF)).height(3.dp)){
                                Box(Modifier.fillMaxWidth(seen.coerceIn(0.03f,1f)).fillMaxHeight().background(Red))
                            }
                        }
                        Text("${(seen*100).toInt()}%",fontSize=11.sp,color=Color.Gray)
                    }
                }
                else if(connected && playingNow==null)TextButton(onClick={scope.launch{
                    busy=true
                    runCatching{CastContext.getSharedInstance(this@MainActivity).sessionManager.currentCastSession?.remoteMediaClient?.stop()}
                    try{api.call("/api/playback/stop","POST");error="Reproducción detenida."}
                    catch(e:Exception){error=e.message.orEmpty()}finally{busy=false}
                }}){Text("Detener reproducción")}
            }
        },bottomBar={if(connected && selected==null){NavigationBar(containerColor=Color(0xFF171719)){
            listOf("Inicio" to Icons.Default.Home,"Películas" to Icons.Default.Movie,"Series" to Icons.Default.Tv,"TV" to Icons.Default.LiveTv,"Mi lista" to Icons.Default.Bookmark).forEach{(title,icon)->NavigationBarItem(selected=screen==title,onClick={screen=title},icon={Icon(icon,title)},label={Text(title)})}
        }}}){padding->
            Column(Modifier.padding(padding).fillMaxSize()){
                if(error.isNotBlank())Text(error,color=Color(0xFFFFA0A6),modifier=Modifier.padding(18.dp).clickable{scope.launch{reload()}})
                if(!connected){
                    Column(Modifier.fillMaxWidth().verticalScroll(rememberScrollState()).padding(28.dp),verticalArrangement=Arrangement.spacedBy(20.dp)){
                        Spacer(Modifier.height(35.dp));Text("Tu cine.\nEn casa.",fontSize=45.sp,lineHeight=50.sp,fontWeight=FontWeight.Bold)
                        Text("Conecta tu teléfono con tu videoteca. Ambos deben estar en la misma red.",color=Color.LightGray)
                        OutlinedTextField(link,{link=it},label={Text("Enlace de conexión")},singleLine=true,visualTransformation=PasswordVisualTransformation(),modifier=Modifier.fillMaxWidth())
                        Button(onClick={scope.launch{busy=true;try{api.pair(link);link="";connected=true;error=""}catch(e:Exception){error=e.message.orEmpty()}finally{busy=false}}},enabled=!busy && link.isNotBlank(),modifier=Modifier.fillMaxWidth()){Text("Conectar mi videoteca")}
                        Text("Desde el panel de tu videoteca: elige Conectar Android.",fontSize=13.sp,color=Color.Gray)
                    }
                }else if(selected!=null){
                    Detail(selected!!,favorites.any{it.optString("id")==selected!!.optString("id")},
                        onFavorite={enabled->scope.launch{try{api.call("/api/library/favorites/${selected!!.getString("id")}","PUT",JSONObject().put("enabled",enabled));favorites=api.call("/api/library/favorites").items()}catch(e:Exception){error=e.message.orEmpty()}}},
                        onPlay=::playContent)
                }else if(busy && movies.isEmpty() && series.isEmpty() && screen!="TV"){
                    BrandScreen("Cargando tu videoteca…")
                }else if(screen=="TV"){
                    Channels(channels,channelFilter,{channelFilter=it},tvSection,{tvSection=it},::tune)
                }else{
                    LazyColumn(verticalArrangement=Arrangement.spacedBy(25.dp),contentPadding=PaddingValues(bottom=30.dp)){
                        if(!searchMode && screen=="Inicio" && movies.isNotEmpty())item{Hero(movies.first()){open(movies.first())}}
                        if(screen!="Mi lista")item{LazyRow(contentPadding=PaddingValues(horizontal=18.dp),horizontalArrangement=Arrangement.spacedBy(8.dp)){
                            items(listOf("Todos" to "","Ciencia ficción" to "Sci-Fi","Terror" to "Horror","Thrillers" to "Thriller","Documentales" to "Documentary","Drama" to "Drama")){(label,value)->FilterChip(selected=genre==value,onClick={genre=value},label={Text(label)})}
                        }}
                        if(screen=="Inicio" && continuing.isNotEmpty())item{Shelf("Continuar viendo",continuing,::open)}
                        if(screen=="Inicio" || screen=="Películas"){
                            item{Shelf(if(searchMode)"Películas encontradas" else "Películas populares",movies,::open)}
                            if(screen=="Películas")item{Button(onClick={scope.launch{busy=true;try{val suffix=if(genre.isBlank())"" else "&genre="+URLEncoder.encode(genre,"UTF-8");movies=(movies+api.call("/api/catalog/movie?skip=$movieSkip$suffix").items()).distinctBy{it.getString("id")};movieSkip+=50}catch(e:Exception){error=e.message.orEmpty()}finally{busy=false}}},modifier=Modifier.padding(horizontal=20.dp)){Text("Cargar más películas")}}
                        }
                        if(screen=="Inicio" || screen=="Series"){
                            item{Shelf(if(searchMode)"Series encontradas" else "Series populares",series,::open)}
                            if(screen=="Series")item{Button(onClick={scope.launch{busy=true;try{val suffix=if(genre.isBlank())"" else "&genre="+URLEncoder.encode(genre,"UTF-8");series=(series+api.call("/api/catalog/series?skip=$seriesSkip$suffix").items()).distinctBy{it.getString("id")};seriesSkip+=50}catch(e:Exception){error=e.message.orEmpty()}finally{busy=false}}},modifier=Modifier.padding(horizontal=20.dp)){Text("Cargar más series")}}
                        }
                        if(screen=="Mi lista")item{if(favorites.isEmpty())Text("Guarda títulos desde su ficha para encontrarlos aquí.",modifier=Modifier.padding(25.dp),color=Color.Gray) else Shelf("Mi lista",favorites,::open)}
                        if(screen=="Inicio" && !searchMode && genre.isBlank())items(collections){collection->Shelf(collection.optString("title"),collection.items(),::open)}
                        if(screen=="Inicio")item{Text("Catálogo de Cinemeta · Fuentes de PenguPlay, Flix-Streams y MediaFusion",color=Color.Gray,fontSize=11.sp,modifier=Modifier.padding(horizontal=20.dp))}
                    }
                }
            }
        }
        if(confirmClose)AlertDialog(onDismissRequest={confirmClose=false},
            title={Text("¿Terminar la reproducción?")},
            text={Text("Se detiene lo que se está viendo y se libera el equipo. Volver a empezarla exige preparar la fuente otra vez.")},
            confirmButton={TextButton(onClick={confirmClose=false;scope.launch{endPlaying()}}){Text("Terminar")}},
            dismissButton={TextButton(onClick={confirmClose=false}){Text("Cancelar")}})
        if(settings)AlertDialog(onDismissRequest={settings=false},title={Text("Tu videoteca")},text={Text("Conectada a ${api.server}\n\nTu videoteca debe estar encendida. La configuración de los proveedores se administra desde su panel.")},confirmButton={TextButton(onClick={settings=false}){Text("Cerrar")}},dismissButton={TextButton(onClick={api.disconnect();connected=false;settings=false;movies=emptyList();series=emptyList();selected=null}){Text("Desconectar")}})
    }

    /** Rejilla de canales en directo: logotipo, nombre y categoría, con filtro por nombre. */
    @Composable private fun Channels(channels:List<JSONObject>,filter:String,onFilter:(String)->Unit,
        section:String,onSection:(String)->Unit,tune:(JSONObject)->Unit){
        val shown=remember(channels,filter){
            if(filter.isBlank())channels
            else channels.filter{it.optString("name").contains(filter,ignoreCase=true)}
        }
        Column(Modifier.fillMaxSize()){
            Row(Modifier.fillMaxWidth().padding(horizontal=20.dp,vertical=6.dp),horizontalArrangement=Arrangement.spacedBy(8.dp)){
                listOf("nacional" to "TV nacional","deportes" to "Deportes","cable" to "Cable").forEach{(value,label)->
                    FilterChip(selected=section==value,onClick={onSection(value)},label={Text(label)})
                }
            }
            OutlinedTextField(filter,onFilter,label={Text("Buscar canal")},singleLine=true,
                modifier=Modifier.fillMaxWidth().padding(horizontal=20.dp,vertical=6.dp))
            val origin=when(section){"cable"->"internacionales en español";"deportes"->"deportivos en español";else->"de Chile"}
            Text(if(channels.isEmpty())"Cargando canales…" else "${shown.size} canales $origin · señal en directo",
                color=Color.Gray,fontSize=12.sp,modifier=Modifier.padding(horizontal=20.dp,vertical=4.dp))
            LazyVerticalGrid(columns=GridCells.Adaptive(112.dp),contentPadding=PaddingValues(16.dp),
                horizontalArrangement=Arrangement.spacedBy(10.dp),verticalArrangement=Arrangement.spacedBy(14.dp)){
                items(shown,key={it.optString("id")}){channel->
                    Column(Modifier.clickable{tune(channel)}){
                        Box(Modifier.fillMaxWidth().height(74.dp).clip(RoundedCornerShape(8.dp)).background(Color(0xFF1D1D22)),
                            contentAlignment=Alignment.Center){
                            if(channel.optString("logo").isBlank())
                                Icon(Icons.Default.LiveTv,null,tint=Color(0xFF6C6B72),modifier=Modifier.size(30.dp))
                            else AsyncImage(channel.optString("logo"),channel.optString("name"),
                                contentScale=ContentScale.Fit,modifier=Modifier.fillMaxSize().padding(9.dp))
                        }
                        Text(channel.optString("name"),maxLines=2,fontSize=12.sp,modifier=Modifier.padding(top=6.dp))
                        val categories=channel.optJSONArray("categories")?.let{list->(0 until list.length()).map{list.optString(it)}}.orEmpty()
                        val tags=(listOf(channel.optString("country"))+categories).filter{it.isNotBlank()}.joinToString(" · ")
                        if(tags.isNotBlank())Text(tags,fontSize=10.sp,color=Color.Gray,maxLines=1)
                    }
                }
            }
        }
    }

    @Composable private fun Shelf(title:String,items:List<JSONObject>,open:(JSONObject)->Unit){
        Column(verticalArrangement=Arrangement.spacedBy(12.dp)){
            Text(title,fontSize=21.sp,fontWeight=FontWeight.Bold,modifier=Modifier.padding(horizontal=20.dp))
            if(items.isEmpty())Text("No hay títulos en esta selección.",color=Color.Gray,modifier=Modifier.padding(horizontal=20.dp))
            LazyRow(contentPadding=PaddingValues(horizontal=20.dp),horizontalArrangement=Arrangement.spacedBy(11.dp)){
                items(items,key={it.getString("id")}){item->Column(Modifier.width(124.dp).clickable{open(item)}){
                    Box{
                        AsyncImage(item.optString("poster"),item.optString("name"),contentScale=ContentScale.Crop,modifier=Modifier.height(180.dp).fillMaxWidth().clip(RoundedCornerShape(7.dp)).background(Color(0xFF29292D)))
                        // Lo empezado se distingue de un vistazo, sin abrir la ficha.
                        val seen=watched[item.optString("id")] ?: 0f
                        if(seen>0f)Box(Modifier.align(Alignment.BottomCenter).fillMaxWidth()
                            .padding(horizontal=6.dp,vertical=6.dp).clip(RoundedCornerShape(2.dp))
                            .background(Color(0x8C000000)).height(3.dp)){
                            Box(Modifier.fillMaxWidth(seen.coerceIn(0.03f,1f)).fillMaxHeight().background(Red))
                        }
                    }
                    Text(item.optString("name"),maxLines=2,fontSize=12.sp,modifier=Modifier.padding(top=7.dp))
                    Text(item.optString("releaseInfo"),fontSize=11.sp,color=Color.Gray)
                }}
            }
        }
    }
    @Composable private fun Hero(item:JSONObject,showButton:Boolean=true,open:()->Unit){
        Box(Modifier.fillMaxWidth().height(330.dp).clickable(onClick=open)){
            AsyncImage(item.optString("background",item.optString("poster")),null,contentScale=ContentScale.Crop,modifier=Modifier.fillMaxSize())
            Box(Modifier.fillMaxSize().background(Brush.verticalGradient(listOf(Color.Transparent,Background))))
            Column(Modifier.align(Alignment.BottomStart).padding(24.dp)){
                Text("DESCUBRE HOY",fontSize=10.sp,letterSpacing=3.sp,color=Color.LightGray)
                Text(item.optString("name"),fontSize=30.sp,lineHeight=34.sp,fontWeight=FontWeight.ExtraBold,maxLines=2)
                Text(item.optString("releaseInfo"),color=Color.LightGray,modifier=Modifier.padding(vertical=6.dp))
                if(showButton)Button(onClick=open){Icon(Icons.Default.Info,null);Spacer(Modifier.width(8.dp));Text("Ver ficha")}
            }
        }
    }
    @Composable private fun Detail(item:JSONObject,favorite:Boolean,onFavorite:(Boolean)->Unit,
        onPlay:(String,Long?)->Unit){
        val episodes=item.optJSONArray("episodes")?.objects()?:emptyList()
        val videos=item.optJSONObject("playback")?.optJSONObject("videos")
        fun progress(videoId:String)=videos?.optJSONObject(videoId)
        val latestIncomplete=episodes.filter{
            val seen=progress(it.optString("id"))
            seen!=null && seen.optLong("position_ms")>0 && !seen.optBoolean("completed")
        }.maxByOrNull{progress(it.optString("id"))?.optString("updated_at").orEmpty()}
        val resumeId=item.optString("resume_video_id")
        val resumed=episodes.firstOrNull{it.optString("id")==resumeId}
        val lastCompleted=episodes.indexOfLast{progress(it.optString("id"))?.optBoolean("completed")==true}
        val mainEpisode=latestIncomplete ?: resumed ?:
            episodes.getOrNull((lastCompleted+1).coerceAtMost((episodes.size-1).coerceAtLeast(0)))
        val anySeriesProgress=episodes.any{
            val seen=progress(it.optString("id"));seen!=null && seen.optLong("position_ms")>0
        }
        val movieProgress=progress(item.optString("id"))
        val movieStarted=movieProgress?.optLong("position_ms",0)?.let{it>0}==true
        val initialSeason=mainEpisode?.optInt("season")?:episodes.firstOrNull()?.optInt("season")?:1
        var season by remember(item.optString("id"),initialSeason){mutableIntStateOf(initialSeason)}
        var pendingEpisode by remember(item.optString("id")){mutableStateOf<JSONObject?>(null)}
        LazyColumn(contentPadding=PaddingValues(bottom=30.dp)){
            item{Hero(item,showButton=false){}}
            item{Row(Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()).padding(horizontal=20.dp),
                horizontalArrangement=Arrangement.spacedBy(12.dp)){
                if(episodes.isEmpty()){
                    Button(onClick={onPlay(item.getString("id"),movieProgress?.optLong("position_ms")?.takeIf{it>0})}){
                        Icon(Icons.Default.PlayArrow,null);Text(if(movieStarted)"Continuar viendo" else "Reproducir")
                    }
                    if(movieStarted)OutlinedButton(onClick={onPlay(item.getString("id"),0L)}){
                        Icon(Icons.Default.Replay,null);Text("Ver desde el inicio")
                    }
                }else if(mainEpisode!=null){
                    val seen=progress(mainEpisode.getString("id"))
                    val exact=seen?.optLong("position_ms")?.takeIf{it>0 && !seen.optBoolean("completed")}
                    Button(onClick={onPlay(mainEpisode.getString("id"),exact)}){
                        Icon(Icons.Default.PlayArrow,null)
                        Text((if(anySeriesProgress)"Continuar viendo · " else "Reproducir · ")+
                            "T${mainEpisode.optInt("season")}:E${mainEpisode.optInt("episode")}")
                    }
                    if(anySeriesProgress)OutlinedButton(onClick={onPlay(episodes.first().getString("id"),0L)}){
                        Icon(Icons.Default.Replay,null);Text("Ver desde el inicio")
                    }
                }
                OutlinedButton(onClick={onFavorite(!favorite)}){Icon(if(favorite)Icons.Default.Check else Icons.Default.Add,null);Text("Mi lista")}
            }}
            item{Text(item.optString("description","Sin sinopsis disponible."),color=Color.LightGray,fontSize=14.sp,modifier=Modifier.padding(20.dp))}
            if(episodes.isNotEmpty()){
                item{LazyRow(contentPadding=PaddingValues(horizontal=20.dp),horizontalArrangement=Arrangement.spacedBy(8.dp)){items(episodes.map{it.optInt("season")}.distinct()){number->FilterChip(selected=season==number,onClick={season=number},label={Text("Temporada $number")})}}}
                items(episodes.filter{it.optInt("season")==season},key={it.getString("id")}){episode->
                    val seen=progress(episode.getString("id"))
                    ListItem(
                        headlineContent={Text("${episode.optInt("episode")}. ${episode.optString("name",episode.optString("title","Episodio"))}")},
                        supportingContent={Column{
                            Text(episode.optString("overview"),maxLines=2,color=Color.Gray)
                            if(seen!=null && seen.optLong("position_ms")>0)Text(
                                if(seen.optBoolean("completed"))"Visto" else "Visto ${(seen.optDouble("progress")*100).toInt()} %",
                                color=Red,fontSize=11.sp)
                        }},
                        trailingContent={Icon(if(seen?.optBoolean("completed")==true)Icons.Default.Check else Icons.Default.PlayArrow,"Reproducir")},
                        modifier=Modifier.clickable{
                            if(seen!=null && seen.optLong("position_ms")>0)pendingEpisode=episode
                            else onPlay(episode.getString("id"),null)
                        },colors=ListItemDefaults.colors(containerColor=Background))
                }
            }
        }
        pendingEpisode?.let{episode->
            val seen=progress(episode.getString("id"))!!
            val at=seen.optLong("position_ms")
            val remaining=seen.optLong("duration_ms")-at
            AlertDialog(onDismissRequest={pendingEpisode=null},
                title={Text("¿Cómo quieres ver este episodio?")},
                text={Text("T${episode.optInt("season")} · E${episode.optInt("episode")}. "+
                    if(remaining in 1..59_999)"Queda menos de un minuto."
                    else "Llegaste hasta ${resumeClock(at)}.")},
                confirmButton={TextButton(onClick={pendingEpisode=null;onPlay(episode.getString("id"),at)}){Text("Continuar viendo")}},
                dismissButton={TextButton(onClick={pendingEpisode=null;onPlay(episode.getString("id"),0L)}){Text("Ver desde el inicio")}})
        }
    }
}
