package cl.videoteca.casa

import android.content.Context
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch
import kotlinx.coroutines.withTimeout
import org.json.JSONArray
import org.json.JSONObject
import java.util.UUID

/** Cola secundaria, persistida y acotada. Una caída de telemetría nunca toca el player. */
class PlaybackJournal(context:Context,private val api:Api) {
    private val prefs=context.applicationContext.getSharedPreferences("playback-journal",Context.MODE_PRIVATE)
    companion object {
        private val lock=Any()
        private var flushing=false
        private val writer=CoroutineScope(SupervisorJob()+Dispatchers.IO)
    }
    private fun read():MutableList<JSONObject> {
        val rows=runCatching{JSONArray(prefs.getString("queue","[]"))}.getOrDefault(JSONArray())
        return (0 until rows.length()).mapNotNull{rows.optJSONObject(it)}.toMutableList()
    }
    private fun save(rows:List<JSONObject>){prefs.edit().putString("queue",JSONArray(rows.takeLast(200)).toString()).apply()}

    /** Solo IDs de intento y cifras; jamás URLs, claves ni mensajes de excepción. */
    fun event(attempt:String,event:String,fields:JSONObject=JSONObject()) {
        if(attempt.isBlank())return
        fields.put("attempt_id",attempt).put("event_id",UUID.randomUUID().toString()).put("event",event)
        if(!fields.has("stage"))fields.put("stage","player")
        runCatching { synchronized(lock){
            val rows=read()
            rows.add(JSONObject().put("at",System.currentTimeMillis()).put("data",fields))
            save(rows)
        } }
        flush()
    }

    fun flush(){
        synchronized(lock){if(flushing || !api.connected)return;flushing=true}
        writer.launch{
            try{
                while(true){
                    val entry=synchronized(lock){read().firstOrNull()}?:break
                    val expired=System.currentTimeMillis()-entry.optLong("at")>3*86400000L
                    if(!expired){
                        try{withTimeout(8000){api.call("/api/playback/event","POST",entry.getJSONObject("data"))}}
                        catch(error:Exception){
                            // Esperar un inicio concurrente; los intentos anteriores
                            // al diario no pueden bloquear los de la versión nueva.
                            val code=(error as? ApiError)?.code
                            val missing=code=="attempt_not_found" && System.currentTimeMillis()-entry.optLong("at")>60000
                            if(code!="invalid_input" && !missing)break
                        }
                    }
                    synchronized(lock){
                        val rows=read()
                        if(rows.firstOrNull()?.getJSONObject("data")?.optString("event_id")==
                            entry.getJSONObject("data").optString("event_id"))rows.removeAt(0)
                        save(rows)
                    }
                }
            }finally{synchronized(lock){flushing=false}}
        }
    }
}
