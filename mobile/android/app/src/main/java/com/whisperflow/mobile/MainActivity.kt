package com.whisperflow.mobile

import android.content.Intent
import android.os.Bundle
import android.provider.Settings
import android.view.inputmethod.InputMethodManager
import android.widget.Button
import androidx.appcompat.app.AppCompatActivity

class MainActivity : AppCompatActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate()
        setContentView(R.layout.activity_main)

        val btnEnableKeyboard = findViewById<Button>(R.id.btnEnableKeyboard)
        val btnSelectKeyboard = findViewById<Button>(R.id.btnSelectKeyboard)

        btnEnableKeyboard.setOnClickListener {
            val intent = Intent(Settings.ACTION_INPUT_METHOD_SETTINGS)
            startActivity(intent)
        }

        btnSelectKeyboard.setOnClickListener {
            val imeManager = getSystemService(INPUT_METHOD_SERVICE) as InputMethodManager
            imeManager.showInputMethodPicker()
        }
    }
}
