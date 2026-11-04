@file:androidx.annotation.OptIn(androidx.media3.common.util.UnstableApi::class)

package cl.videoteca.casa

import android.content.Context
import android.graphics.Color
import android.graphics.Typeface
import androidx.media3.common.util.UnstableApi
import androidx.media3.ui.CaptionStyleCompat
import com.google.android.gms.cast.TextTrackStyle

/** Presets de subtítulos habituales en streaming: color, borde, caja y tipografía. */
data class CaptionPreset(
    val name: String,
    val detail: String,
    val foreground: Int,
    val background: Int,
    val edge: Int,
    val edgeColor: Int,
    val typeface: Typeface?,
    val castFont: String,
)

private const val BOX = 0xCC000000.toInt()
private const val AMBER = 0xFFF2C700.toInt()

val CAPTION_PRESETS = listOf(
    CaptionPreset("Blanco clásico", "Sombra suave, sin fondo", Color.WHITE, Color.TRANSPARENT,
        CaptionStyleCompat.EDGE_TYPE_DROP_SHADOW, Color.BLACK, Typeface.SANS_SERIF, "sans-serif"),
    CaptionPreset("Blanco con borde", "Contorno negro, máxima legibilidad", Color.WHITE, Color.TRANSPARENT,
        CaptionStyleCompat.EDGE_TYPE_OUTLINE, Color.BLACK, Typeface.SANS_SERIF, "sans-serif"),
    CaptionPreset("Amarillo con borde", "El clásico de televisión y DVD", AMBER, Color.TRANSPARENT,
        CaptionStyleCompat.EDGE_TYPE_OUTLINE, Color.BLACK, Typeface.SANS_SERIF, "sans-serif"),
    CaptionPreset("Caja negra", "Fondo sólido, estilo emisión", Color.WHITE, BOX,
        CaptionStyleCompat.EDGE_TYPE_NONE, Color.BLACK, Typeface.SANS_SERIF, "sans-serif"),
    CaptionPreset("Amarillo en caja", "Fondo sólido y texto ámbar", AMBER, BOX,
        CaptionStyleCompat.EDGE_TYPE_NONE, Color.BLACK, Typeface.SANS_SERIF, "sans-serif"),
    CaptionPreset("Serif con relieve", "Letra con remates, borde elevado", Color.WHITE, Color.TRANSPARENT,
        CaptionStyleCompat.EDGE_TYPE_RAISED, Color.BLACK, Typeface.SERIF, "serif"),
    CaptionPreset("Monoespaciada", "Letra de ancho fijo sobre caja", Color.WHITE, BOX,
        CaptionStyleCompat.EDGE_TYPE_NONE, Color.BLACK, Typeface.MONOSPACE, "monospace"),
)

/** Fracción de la altura de pantalla; SubtitleView usa 0.0533 por omisión. */
val CAPTION_SIZES = listOf("Pequeño" to 0.042f, "Normal" to 0.0533f, "Grande" to 0.070f, "Muy grande" to 0.088f)

fun CaptionPreset.media3(): CaptionStyleCompat =
    CaptionStyleCompat(foreground, background, Color.TRANSPARENT, edge, edgeColor, typeface)

/** El Chromecast dibuja sus propios subtítulos: se le manda el equivalente del preset elegido. */
fun CaptionPreset.cast(scale: Float): TextTrackStyle = TextTrackStyle().also { style ->
    style.setForegroundColor(foreground)
    style.setBackgroundColor(background)
    style.setEdgeColor(edgeColor)
    style.setEdgeType(when (edge) {
        CaptionStyleCompat.EDGE_TYPE_OUTLINE -> TextTrackStyle.EDGE_TYPE_OUTLINE
        CaptionStyleCompat.EDGE_TYPE_DROP_SHADOW -> TextTrackStyle.EDGE_TYPE_DROP_SHADOW
        CaptionStyleCompat.EDGE_TYPE_RAISED -> TextTrackStyle.EDGE_TYPE_RAISED
        else -> TextTrackStyle.EDGE_TYPE_NONE
    })
    style.setFontFamily(castFont)
    style.setFontScale(scale / 0.0533f)
}

/** La elección de subtítulos se recuerda entre reproducciones y entre episodios. */
class CaptionChoice(context: Context) {
    private val prefs = context.getSharedPreferences("captions", Context.MODE_PRIVATE)
    var preset: Int
        get() = prefs.getInt("preset", 1).coerceIn(0, CAPTION_PRESETS.lastIndex)
        set(value) { prefs.edit().putInt("preset", value).apply() }
    var size: Int
        get() = prefs.getInt("size", 1).coerceIn(0, CAPTION_SIZES.lastIndex)
        set(value) { prefs.edit().putInt("size", value).apply() }
}
