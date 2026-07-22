package com.whisperflow.mobile

import android.inputmethodservice.InputMethodService
import android.util.Log
import android.view.View
import android.widget.Button
import android.widget.TextView

class WhisperInputMethodService : InputMethodService() {

    private lateinit var dictationService: DictationService
    private val llamaBridge = NativeLlamaBridge()
    private var isRecording = false

    companion object {
        private const val TAG = "WhisperIME"
    }

    override fun onCreate() {
        super.onCreate()
        dictationService = DictationService()
        dictationService.onCreate()
    }

    override fun onCreateInputView(): View {
        val view = layoutInflater.inflate(R.layout.keyboard_view, null)
        val btnDictate = view.findViewById<Button>(R.id.btnDictate)
        val tvStatus = view.findViewById<TextView>(R.id.tvStatus)

        btnDictate.setOnClickListener {
            if (!isRecording) {
                isRecording = true
                tvStatus.text = "Listening (Release/Tap to Stop)..."
                btnDictate.text = "⏹️"
                
                dictationService.startDictation(
                    onResult = { rawTranscript ->
                        isRecording = false
                        tvStatus.text = "Polishing ✨..."
                        btnDictate.text = "🎙️"
                        
                        // Pass through local LLM bridge
                        val polished = llamaBridge.processTranscript(rawTranscript, "Clean up transcript.")
                        
                        // Auto-insert into active text field
                        currentInputConnection?.commitText(polished, 1)
                        tvStatus.text = "Inserted ✓"
                    },
                    onError = { err ->
                        isRecording = false
                        tvStatus.text = "Error: $err"
                        btnDictate.text = "🎙️"
                    }
                )
            } else {
                isRecording = false
                tvStatus.text = "Processing..."
                btnDictate.text = "🎙️"
                dictationService.stopDictation()
            }
        }

        return view
    }

    override fun onDestroy() {
        super.onDestroy()
        dictationService.onDestroy()
    }
}
