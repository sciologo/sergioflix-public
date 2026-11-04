package cl.videoteca.casa

import android.content.Context
import com.google.android.gms.cast.CastMediaControlIntent
import com.google.android.gms.cast.framework.CastOptions
import com.google.android.gms.cast.framework.OptionsProvider
import com.google.android.gms.cast.framework.SessionProvider

/** Receptor oficial para la primera entrega. La cola se precarga antes de cerrar el teléfono. */
class HomeCastOptions:OptionsProvider {
    override fun getCastOptions(context:Context)=CastOptions.Builder().setReceiverApplicationId(CastMediaControlIntent.DEFAULT_MEDIA_RECEIVER_APPLICATION_ID).build()
    override fun getAdditionalSessionProviders(context:Context):List<SessionProvider>?=null
}

