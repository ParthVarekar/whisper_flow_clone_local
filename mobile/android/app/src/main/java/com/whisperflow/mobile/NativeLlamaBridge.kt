package com.whisperflow.mobile

import android.util.Log

class NativeLlamaBridge {

    companion object {
        private const val TAG = "NativeLlamaBridge"

        init {
            try {
                System.loadLibrary("whisperflow_native")
                Log.i(TAG, "Native C++ WhisperFlow library loaded successfully")
            } catch (e: UnsatisfiedLinkError) {
                Log.e(TAG, "Failed to load native C++ library: ${e.message}")
            }
        }
    }

    external fun loadModel(modelPath: String): Boolean
    external fun processTranscript(rawTranscript: String, systemPrompt: String): String
    external fun unloadModel()
}
