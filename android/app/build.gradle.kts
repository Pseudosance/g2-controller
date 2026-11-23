plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("kotlin-android")
}

android {
    namespace = "com.even.g2controller"
    compileSdk = 34

    defaultConfig {
        applicationId = "com.even.g2controller"
        minSdk = 26
        targetSdk = 34
        versionCode = 1
        versionName = "1.0"
    }

    buildFeatures {
        compose = true
    }

    composeOptions {
        kotlinCompilerExtensionVersion = "1.5.12"
    }

    packaging {
        resources.excludes.add("META-INF/*")
    }

    kotlinOptions {
        jvmTarget = "17"
    }
    
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    buildToolsVersion = "34.0.0"
}

dependencies {
    val compose_version = "1.6.8"
    val material3_version = "1.2.1"
    val activity_version = "1.9.0"
    val lifecycle_version = "2.7.0"
    val core_version = "1.13.0"
    val coroutines_version = "1.7.3"
    
    implementation("androidx.compose.ui:ui:$compose_version")
    implementation("androidx.compose.ui:ui-graphics:$compose_version")
    implementation("androidx.compose.ui:ui-tooling-preview:$compose_version")
    implementation("androidx.compose.material3:material3:$material3_version")
    implementation("androidx.activity:activity-compose:$activity_version")
    implementation("androidx.lifecycle:lifecycle-runtime-compose:$lifecycle_version")
    implementation("androidx.lifecycle:lifecycle-viewmodel-compose:$lifecycle_version")
    implementation("androidx.lifecycle:lifecycle-livedata-ktx:$lifecycle_version")
    
    implementation("androidx.compose.runtime:runtime-livedata:$compose_version")
    
    implementation("com.google.android.material:material:1.12.0")
    implementation("androidx.compose.material:material-icons-extended:$compose_version")
    
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:$coroutines_version")
    implementation("androidx.core:core-ktx:$core_version")
    implementation("androidx.activity:activity-ktx:$activity_version")
}
