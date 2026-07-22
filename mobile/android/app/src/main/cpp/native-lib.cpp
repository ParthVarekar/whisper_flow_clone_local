#include <jni.h>
#include <string>
#include <android/log.h>

#define LOG_TAG "WhisperFlowNative"
#define LOGI(...) __android_log_print(ANDROID_LOG_INFO, LOG_TAG, __VA_ARGS__)
#define LOGE(...) __android_log_print(ANDROID_LOG_ERROR, LOG_TAG, __VA_ARGS__)

extern "C" JNIEXPORT jboolean JNICALL
Java_com_whisperflow_mobile_NativeLlamaBridge_loadModel(
        JNIEnv* env,
        jobject /* this */,
        jstring modelPath) {
    const char *path = env->GetStringUTFChars(modelPath, nullptr);
    LOGI("Loading Gemma GGUF model from path: %s", path);
    env->ReleaseStringUTFChars(modelPath, path);
    // llama.cpp model loading logic placeholder
    return JNI_TRUE;
}

extern "C" JNIEXPORT jstring JNICALL
Java_com_whisperflow_mobile_NativeLlamaBridge_processTranscript(
        JNIEnv* env,
        jobject /* this */,
        jstring rawTranscript,
        jstring systemPrompt) {
    const char *transcript = env->GetStringUTFChars(rawTranscript, nullptr);
    const char *prompt = env->GetStringUTFChars(systemPrompt, nullptr);
    
    LOGI("Processing transcript via local LLM...");
    
    std::string result = transcript; // Placeholder until llama.cpp context infer is linked
    
    env->ReleaseStringUTFChars(rawTranscript, transcript);
    env->ReleaseStringUTFChars(systemPrompt, prompt);
    
    return env->NewStringUTF(result.c_str());
}

extern "C" JNIEXPORT void JNICALL
Java_com_whisperflow_mobile_NativeLlamaBridge_unloadModel(
        JNIEnv* env,
        jobject /* this */) {
    LOGI("Unloading local Gemma model from memory");
}
