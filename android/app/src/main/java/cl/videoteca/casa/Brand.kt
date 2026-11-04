package cl.videoteca.casa

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

val BrandRed = Color(0xFFE53D49)
val BrandBlack = Color(0xFF0A0A0B)

/** Marca de la aplicación: la S de SergioFlix, en dos arcos gruesos. */
@Composable
fun BrandMark(size: Dp = 64.dp, modifier: Modifier = Modifier) {
    Box(modifier.size(size), contentAlignment = Alignment.Center) {
        Canvas(Modifier.fillMaxSize()) {
            val lado = this.size.minDimension
            val trazo = lado * 0.15f
            val radio = lado * 0.19f
            // Cada bucle abarca 265 grados: lo que queda abierto es lo que hace la S.
            listOf(0.34f, 0.66f).forEachIndexed { indice, centro ->
                drawArc(color = BrandRed, startAngle = if (indice == 0) 95f else 275f,
                    sweepAngle = 265f, useCenter = false,
                    style = Stroke(width = trazo, cap = StrokeCap.Round),
                    topLeft = Offset(lado * 0.5f - radio, lado * centro - radio),
                    size = Size(radio * 2, radio * 2))
            }
        }
    }
}

/** Nombre en dos pesos: se lee como marca y no como una palabra suelta en rojo. */
@Composable
fun BrandWordmark(size: androidx.compose.ui.unit.TextUnit = 34.sp) {
    Row {
        Text("SERGIO", color = Color.White, fontWeight = FontWeight.Light, fontSize = size, letterSpacing = 4.sp)
        Text("FLIX", color = BrandRed, fontWeight = FontWeight.Black, fontSize = size, letterSpacing = 4.sp)
    }
}

/** Pantalla negra de marca: se usa entre transmisiones, al cargar y mientras no hay video. */
@Composable
fun BrandScreen(message: String = "", showProgress: Boolean = true, modifier: Modifier = Modifier) {
    Box(modifier.fillMaxSize().background(BrandBlack), contentAlignment = Alignment.Center) {
        Column(horizontalAlignment = Alignment.CenterHorizontally) {
            BrandMark(size = 76.dp)
            Spacer(Modifier.height(18.dp))
            BrandWordmark()
            if (showProgress) CircularProgressIndicator(color = BrandRed, strokeWidth = 3.dp,
                modifier = Modifier.padding(top = 30.dp).size(30.dp))
            if (message.isNotBlank()) Text(message, color = Color(0xFFB9B8BE), fontSize = 13.sp,
                modifier = Modifier.padding(top = 22.dp, start = 32.dp, end = 32.dp))
        }
    }
}
