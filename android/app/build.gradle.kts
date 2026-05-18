import java.io.FileInputStream
import java.util.Properties

plugins {
    id("com.android.application")
    id("kotlin-android")
    id("dev.flutter.flutter-gradle-plugin")
}

val keystoreProperties = Properties()
val keystorePropertiesFile = rootProject.file("key.properties")

if (keystorePropertiesFile.exists()) {
    FileInputStream(keystorePropertiesFile).use(keystoreProperties::load)
}

android {
    namespace = "com.example.raycash"
    // MODIFICATION : On passe en 36 pour satisfaire camera_android_camerax
    compileSdk = 36 
    ndkVersion = "28.2.13676358"

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    kotlinOptions {
        jvmTarget = JavaVersion.VERSION_17.toString()
    }

    defaultConfig {
        applicationId = "com.example.raycash"
        minSdk = 25 
        // MODIFICATION : On aligne le targetSdk sur 36
        targetSdk = 36
        versionCode = flutter.versionCode
        versionName = flutter.versionName
    }

    signingConfigs {
        create("release") {
            if (keystorePropertiesFile.exists()) {
                keyAlias = keystoreProperties["keyAlias"] as String
                keyPassword = keystoreProperties["keyPassword"] as String
                storeFile = file(keystoreProperties["storeFile"] as String)
                storePassword = keystoreProperties["storePassword"] as String
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = true       // R8 : obfusque et réduit le bytecode
            isShrinkResources = true     // Supprime les ressources inutilisées
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")

            signingConfig = if (keystorePropertiesFile.exists()) {
                signingConfigs.getByName("release")
            } else {
                signingConfigs.getByName("debug")
            }
        }
    }
}

flutter {
    source = "../.."
}

// --- SOLUTION FINALE POUR NAMESPACE + LSTAR + SDK VERSION ---
subprojects {
    val subproject = this
    subproject.plugins.whenPluginAdded {
        if (this is com.android.build.gradle.api.AndroidBasePlugin) {
            subproject.extensions.configure<com.android.build.gradle.BaseExtension> {
                // On force TOUS les plugins en 36 pour éviter les conflits de métadonnées
                compileSdkVersion(36)
                
                if (namespace == null) {
                    namespace = "com.example.raycash.${subproject.name}"
                }
            }
        }
    }
    
    // Nettoyage indispensable pour tflite_v2
    subproject.tasks.withType<com.android.build.gradle.tasks.ProcessLibraryManifest>().configureEach {
        doFirst {
            val manifestFile = mainManifest.get().asFile
            if (manifestFile.exists()) {
                val content = manifestFile.readText()
                if (content.contains("package=")) {
                    val newContent = content.replace(Regex("""package="[^"]*""""), "")
                    manifestFile.writeText(newContent)
                }
            }
        }
    }
}