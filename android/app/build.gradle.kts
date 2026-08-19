import java.io.FileInputStream
import java.util.Properties

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

// Single source of truth for the app version - bump android/version.properties
// on every release (see that file for the bump convention) rather than editing
// versionCode/versionName here directly.
val versionProps = Properties().apply {
    load(FileInputStream(rootProject.file("version.properties")))
}
val verMajor = versionProps.getProperty("VERSION_MAJOR").toInt()
val verMinor = versionProps.getProperty("VERSION_MINOR").toInt()
val verPatch = versionProps.getProperty("VERSION_PATCH").toInt()

// Release signing credentials live in android/keystore.properties, which is
// gitignored - this only signs the release build when that file is present,
// so a fresh checkout without it still builds fine (just unsigned).
val keystorePropertiesFile = rootProject.file("keystore.properties")
val keystoreProperties = Properties()
val hasKeystoreProperties = keystorePropertiesFile.exists()
if (hasKeystoreProperties) {
    keystoreProperties.load(FileInputStream(keystorePropertiesFile))
}

android {
    namespace = "com.clmix"
    compileSdk = 34

    defaultConfig {
        applicationId = "com.clmix.myapp"
        minSdk = 24
        targetSdk = 34
        versionCode = verMajor * 10000 + verMinor * 100 + verPatch
        versionName = "$verMajor.$verMinor.$verPatch"
    }

    signingConfigs {
        if (hasKeystoreProperties) {
            create("release") {
                storeFile = rootProject.file(keystoreProperties.getProperty("storeFile"))
                storePassword = keystoreProperties.getProperty("storePassword")
                keyAlias = keystoreProperties.getProperty("keyAlias")
                keyPassword = keystoreProperties.getProperty("keyPassword")
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = true
            isShrinkResources = true
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro"
            )
            if (hasKeystoreProperties) {
                signingConfig = signingConfigs.getByName("release")
            }
        }
    }

    buildFeatures {
        viewBinding = true
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_1_8
        targetCompatibility = JavaVersion.VERSION_1_8
    }

    kotlinOptions {
        jvmTarget = "1.8"
    }
}

dependencies {
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.appcompat:appcompat:1.7.0")
    implementation("com.google.android.material:material:1.12.0")
    implementation("androidx.recyclerview:recyclerview:1.3.2")
    implementation("androidx.constraintlayout:constraintlayout:2.1.4")
    implementation("androidx.drawerlayout:drawerlayout:1.2.0")
    implementation("com.squareup.okhttp3:okhttp:4.12.0")
    // Keystore-backed encryption at rest for the persisted session token.
    implementation("androidx.security:security-crypto:1.1.0-alpha06")
}
