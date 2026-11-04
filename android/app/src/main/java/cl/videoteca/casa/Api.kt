package cl.videoteca.casa

import android.content.Context
import android.net.Uri
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlin.coroutines.resumeWithException
import okhttp3.Call
import okhttp3.Callback
import okhttp3.Response
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONArray
import org.json.JSONObject
import java.security.KeyStore
import java.util.concurrent.TimeUnit
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

/** Error del servicio con su código, para poder distinguir el fin de una sesión de
    una fuente caída: lo primero se avisa, lo segundo se reemplaza. */
class ApiError(val code:String,message:String):IllegalStateException(message)

/** Clave revocable por teléfono, cifrada con Android Keystore. Nunca incluida en el APK. */
class Api(private val context: Context) {
    private val prefs=context.getSharedPreferences("connection",Context.MODE_PRIVATE)
    private val client=OkHttpClient.Builder().connectTimeout(12,TimeUnit.SECONDS).readTimeout(135,TimeUnit.SECONDS).build()
    var server:String
        get()=prefs.getString("server","") ?: ""
        private set(value) {prefs.edit().putString("server",value).apply()}
    val connected get()=server.startsWith("https://") && prefs.contains("key")

    private fun encryptionKey():SecretKey {
        val store=KeyStore.getInstance("AndroidKeyStore").apply{load(null)}
        (store.getKey("videoteca.device",null) as? SecretKey)?.let{return it}
        return KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES,"AndroidKeyStore").apply{
            init(KeyGenParameterSpec.Builder("videoteca.device",KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM).setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE).build())
        }.generateKey()
    }
    private fun saveKey(value:String) {
        val cipher=Cipher.getInstance("AES/GCM/NoPadding").apply{init(Cipher.ENCRYPT_MODE,encryptionKey())}
        val encrypted=cipher.doFinal(value.toByteArray())
        prefs.edit().putString("key",Base64.encodeToString(cipher.iv+encrypted,Base64.NO_WRAP)).apply()
    }
    private fun key():String {
        if(!prefs.contains("key")) return ""
        val bytes=Base64.decode(prefs.getString("key","")!!,Base64.NO_WRAP)
        return String(Cipher.getInstance("AES/GCM/NoPadding").apply{
            init(Cipher.DECRYPT_MODE,encryptionKey(),GCMParameterSpec(128,bytes.copyOfRange(0,12)))
        }.doFinal(bytes.copyOfRange(12,bytes.size)))
    }
    fun disconnect(){prefs.edit().clear().apply()}

    suspend fun pair(link:String) {
        val uri=Uri.parse(link.trim())
        val address=uri.getQueryParameter("server")?.trimEnd('/') ?: error("El enlace de conexión no es válido.")
        val code=uri.getQueryParameter("code") ?: error("Falta el código de conexión.")
        require(uri.scheme=="videoteca" && uri.host=="pair" && address.startsWith("https://")){"Usa el enlace generado en tu videoteca."}
        val result=callAt(address,"/api/devices/pair","POST",JSONObject().put("code",code),false)
        saveKey(result.getString("api_key"));server=address
    }

    private suspend fun callAt(base:String,path:String,method:String,data:JSONObject?,auth:Boolean):JSONObject=withContext(Dispatchers.IO){
        require(base.startsWith("https://")){"La conexión debe usar HTTPS."}
        val request=Request.Builder().url(base+path)
        if(auth) request.header("X-API-Key",key())
        if(method!="GET") request.method(method,(data?:JSONObject()).toString().toRequestBody("application/json".toMediaType()))
        try {
            val response=suspendCancellableCoroutine<Response>{continuation->
                val call=client.newCall(request.build())
                continuation.invokeOnCancellation{call.cancel()}
                call.enqueue(object:Callback{
                    override fun onFailure(call:Call,error:java.io.IOException){if(continuation.isActive)continuation.resumeWithException(error)}
                    override fun onResponse(call:Call,response:Response){
                        continuation.resume(response){response.close()}
                    }
                })
            }
            response.use{response->
                val body=response.body?.string().orEmpty()
                val result=if(body.isBlank())JSONObject() else JSONObject(body)
                if(!response.isSuccessful) throw ApiError(result.optString("code"),
                    result.optString("message","No se pudo completar la operación."))
                result
            }
        } catch(error:javax.net.ssl.SSLException){throw IllegalStateException("No se pudo verificar el certificado de tu videoteca.")}
        catch(error:java.io.IOException){throw IllegalStateException("No se pudo conectar. Comprueba que tu videoteca esté encendida y en la misma red.")}
    }
    suspend fun call(path:String,method:String="GET",data:JSONObject?=null)=callAt(server,path,method,data,true)
}

fun JSONArray.objects():List<JSONObject> = (0 until length()).mapNotNull{optJSONObject(it)}
fun JSONObject.items():List<JSONObject> = optJSONArray("items")?.objects() ?: emptyList()
fun JSONObject.fractions():Map<String,Float> = keys().asSequence().associateWith{optDouble(it,0.0).toFloat()}
