# Add project specific ProGuard rules here.
# You can control the set of applied configuration files using the
# proguardFiles setting in build.gradle.kts.
#
# For more details, see
#   http://developer.android.com/guide/developing/tools/proguard.html

# AuxBus is passed between activities via Intent.putExtra(Serializable),
# and Java's default serialization resolves fields by name through
# reflection - keep field names stable so R8 doesn't rename them away
# from what the serialization machinery expects.
-keepnames class com.digico.monitormix.AuxBus implements java.io.Serializable
-keepclassmembers class com.digico.monitormix.AuxBus implements java.io.Serializable {
    <fields>;
}
