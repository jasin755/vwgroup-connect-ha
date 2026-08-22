plugins {
    id("com.android.application")
}

android {
    namespace = "me.pognerebko.vagcompanion"
    compileSdk = 35

    defaultConfig {
        applicationId = "me.pognerebko.vagcompanion"
        minSdk = 28
        targetSdk = 35
        versionCode = 3
        versionName = "0.2.0"

        testInstrumentationRunner = "android.test.InstrumentationTestRunner"
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro",
            )
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
}

dependencies {
    testImplementation("junit:junit:4.13.2")
}
