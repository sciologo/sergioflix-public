import java.util.Properties

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.plugin.compose")
}

android {
    namespace = "cl.videoteca.casa"
    compileSdk = 35
    defaultConfig {
        applicationId = "cl.videoteca.casa"
        minSdk = 26
        targetSdk = 35
        versionCode = 28
        versionName = "0.4.9"
    }
    buildFeatures { compose = true }
    compileOptions { sourceCompatibility = JavaVersion.VERSION_17; targetCompatibility = JavaVersion.VERSION_17 }
    kotlinOptions { jvmTarget = "17" }
    val credentialsFile=rootProject.file("../private/signing.properties")
    if(credentialsFile.exists()){
        val credentials=Properties().apply{credentialsFile.inputStream().use{load(it)}}
        signingConfigs.create("personal"){
            storeFile=rootProject.file("../private/android-release.p12")
            storePassword=credentials.getProperty("storePassword")
            keyAlias=credentials.getProperty("keyAlias")
            keyPassword=credentials.getProperty("keyPassword")
        }
    }
    buildTypes { release {
        isMinifyEnabled = false
        signingConfigs.findByName("personal")?.let{signingConfig=it}
    } }
}

dependencies {
    implementation(platform("androidx.compose:compose-bom:2025.04.00"))
    implementation("androidx.activity:activity-compose:1.10.1")
    implementation("androidx.compose.material3:material3")
    implementation("androidx.compose.material:material-icons-extended")
    implementation("androidx.compose.ui:ui-tooling-preview")
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.9.0")
    implementation("androidx.lifecycle:lifecycle-runtime-compose:2.9.0")
    implementation("androidx.fragment:fragment-ktx:1.8.6")
    implementation("androidx.appcompat:appcompat:1.7.0")
    implementation("androidx.media3:media3-exoplayer:1.6.1")
    implementation("androidx.media3:media3-exoplayer-hls:1.6.1")
    implementation("androidx.media3:media3-exoplayer-dash:1.6.1")
    implementation("androidx.media3:media3-ui:1.6.1")
    implementation("androidx.mediarouter:mediarouter:1.7.0")
    implementation("com.google.android.gms:play-services-cast-framework:22.0.0")
    implementation("com.squareup.okhttp3:okhttp:4.12.0")
    implementation("io.coil-kt:coil-compose:2.7.0")
}
