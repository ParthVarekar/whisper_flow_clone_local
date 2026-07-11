// Second Brain — Tauri desktop shell
//
// This is the desktop wrapper that turns the Next.js web app into a native
// desktop application with:
//   - Global hotkey Ctrl+Shift+B (brain capture)
//   - Global hotkey Ctrl+Shift+F (brain search)
//   - System tray icon with quick actions
//   - Capture overlay window (appears on hotkey press)
//   - Python sidecar integration (Moonshine ASR from WhisperFlow)
//
// Build instructions (on Windows with Rust + Node installed):
//   cd second-brain
//   bun install
//   cargo tauri dev    # development
//   cargo tauri build  # production .msi/.exe
//
// The Next.js dev server runs on port 3000 (bun run dev). Tauri loads it
// in a native webview. When the user presses Ctrl+Shift+B, a small capture
// overlay window appears, records audio, sends it to the Python sidecar
// for transcription, then POSTs the transcript to /api/brain/capture.

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use serde::{Deserialize, Serialize};
use std::sync::Mutex;
use tauri::{
    menu::{Menu, MenuItem},
    tray::TrayIconBuilder,
    AppHandle, Manager, WebviewWindowBuilder,
};
use tauri_plugin_global_shortcut::{GlobalShortcutExt, Shortcut, ShortcutState};

// ── State ──────────────────────────────────────────────────────────────────

struct AppState {
    is_capturing: Mutex<bool>,
}

impl Default for AppState {
    fn default() -> Self {
        Self {
            is_capturing: Mutex::new(false),
        }
    }
}

// ── Commands (callable from frontend via `invoke`) ────────────────────────

#[derive(Serialize, Deserialize)]
struct CaptureResult {
    note_id: String,
    title: String,
    related_count: usize,
}

/// Called from the capture overlay when recording stops.
/// Sends the audio file to the Python sidecar for transcription, then
/// POSTs the transcript to the Next.js /api/brain/capture endpoint.
#[tauri::command]
async fn capture_from_audio(
    app: AppHandle,
    audio_path: String,
) -> Result<CaptureResult, String> {
    // Step 1: Transcribe via the Python sidecar (Moonshine ASR)
    let transcript = transcribe_via_sidecar(&audio_path).await?;

    if transcript.trim().is_empty() {
        return Err("No speech detected".into());
    }

    // Step 2: Classify + store via the Next.js API
    let client = reqwest::Client::new();
    let resp = client
        .post("http://localhost:3000/api/brain/capture")
        .json(&serde_json::json!({
            "transcript": transcript,
            "source": "voice"
        }))
        .send()
        .await
        .map_err(|e| format!("Capture API failed: {}", e))?;

    let body: serde_json::Value = resp
        .json()
        .await
        .map_err(|e| format!("Invalid response: {}", e))?;

    let note = body.get("note").ok_or("No note in response")?;
    let related = body.get("relatedNotes").and_then(|v| v.as_array());

    Ok(CaptureResult {
        note_id: note.get("id").and_then(|v| v.as_str()).unwrap_or("").to_string(),
        title: note.get("title").and_then(|v| v.as_str()).unwrap_or("").to_string(),
        related_count: related.map(|a| a.len()).unwrap_or(0),
    })
}

/// Send audio to the Python sidecar (Moonshine ASR) and get back text.
/// The sidecar is a small HTTP server that wraps whisper_flow's Moonshine
/// backend. See binaries/whisper-sidecar for the Python script.
async fn transcribe_via_sidecar(audio_path: &str) -> Result<String, String> {
    let client = reqwest::Client::new();

    // Read the audio file and send it as multipart
    let file_bytes = std::fs::read(audio_path)
        .map_err(|e| format!("Failed to read audio: {}", e))?;

    let part = reqwest::multipart::Part::bytes(file_bytes)
        .file_name("audio.webm")
        .mime_str("audio/webm")
        .map_err(|e| e.to_string())?;

    let form = reqwest::multipart::Form::new().part("audio", part);

    let resp = client
        .post("http://127.0.0.1:5001/transcribe")
        .multipart(form)
        .send()
        .await
        .map_err(|e| format!("Sidecar request failed: {}", e))?;

    let body: serde_json::Value = resp
        .json()
        .await
        .map_err(|e| format!("Invalid sidecar response: {}", e))?;

    body.get("transcript")
        .and_then(|v| v.as_str())
        .map(|s| s.to_string())
        .ok_or("No transcript in sidecar response".into())
}

/// Show a notification (used for proactive surfacing + capture confirmation).
#[tauri::command]
async fn show_notification(app: AppHandle, title: String, body: String) -> Result<(), String> {
    app.notification()
        .builder()
        .title(&title)
        .body(&body)
        .show()
        .map_err(|e| e.to_string())
}

// ── App setup ──────────────────────────────────────────────────────────────

fn show_capture_overlay(app: &AppHandle) {
    // Don't start a new capture if one is already in progress
    let state = app.state::<AppState>();
    let mut is_capturing = state.is_capturing.lock().unwrap();
    if *is_capturing {
        return;
    }
    *is_capturing = true;
    drop(is_capturing);

    // Show the capture overlay window
    if let Some(overlay) = app.get_webview_window("capture-overlay") {
        let _ = overlay.show();
        let _ = overlay.set_focus();
        // The overlay's frontend will handle recording + call capture_from_audio
    } else {
        // Create the overlay if it doesn't exist
        let _ = WebviewWindowBuilder::new(
            app,
            "capture-overlay",
            tauri::WebviewUrl::App("/capture".into()),
        )
        .title("Capture")
        .inner_size(400.0, 120.0)
        .decorations(false)
        .transparent(true)
        .always_on_top(true)
        .skip_taskbar(true)
        .center()
        .build();
    }
}

fn hide_capture_overlay(app: &AppHandle) {
    let state = app.state::<AppState>();
    let mut is_capturing = state.is_capturing.lock().unwrap();
    *is_capturing = false;

    if let Some(overlay) = app.get_webview_window("capture-overlay") {
        let _ = overlay.hide();
    }
}

fn show_main_window(app: &AppHandle) {
    if let Some(main) = app.get_webview_window("main") {
        let _ = main.show();
        let _ = main.set_focus();
    }
}

fn setup_tray(app: &AppHandle) -> tauri::Result<()> {
    let capture_item = MenuItem::with_id(app, "capture", "Capture (Ctrl+Shift+B)", true, None::<&str>)?;
    let search_item = MenuItem::with_id(app, "search", "Search (Ctrl+Shift+F)", true, None::<&str>)?;
    let show_item = MenuItem::with_id(app, "show", "Show Brain", true, None::<&str>)?;
    let quit_item = MenuItem::with_id(app, "quit", "Quit", true, None::<&str>)?;

    let menu = Menu::with_items(app, &[&capture_item, &search_item, &show_item, &quit_item])?;

    TrayIconBuilder::with_id("main-tray")
        .icon(app.default_window_icon().unwrap().clone())
        .tooltip("Second Brain — Ctrl+Shift+B to capture")
        .menu(&menu)
        .on_menu_event(|app, event| {
            match event.id.as_ref() {
                "capture" => show_capture_overlay(app),
                "search" => {
                    if let Some(main) = app.get_webview_window("main") {
                        let _ = main.show();
                        let _ = main.set_focus();
                        let _ = main.eval("document.querySelector('input[placeholder*=\"Search\"]')?.focus()");
                    }
                }
                "show" => show_main_window(app),
                "quit" => app.exit(0),
                _ => {}
            }
        })
        .build(app)?;

    Ok(())
}

fn setup_hotkeys(app: &AppHandle) -> tauri::Result<()> {
    let app_handle = app.clone();

    // Ctrl+Shift+B → brain capture
    let capture_shortcut: Shortcut = "Ctrl+Shift+B".parse()?;
    app.global_shortcut().on_shortcut(capture_shortcut, move |app, _shortcut, event| {
        if event.state == ShortcutState::Pressed {
            show_capture_overlay(app);
        }
    })?;

    // Ctrl+Shift+F → brain search (show main window + focus search)
    let search_shortcut: Shortcut = "Ctrl+Shift+F".parse()?;
    app.global_shortcut().on_shortcut(search_shortcut, move |app, _shortcut, event| {
        if event.state == ShortcutState::Pressed {
            show_main_window(app);
            if let Some(main) = app.get_webview_window("main") {
                let _ = main.eval("document.querySelector('input[placeholder*=\"Search\"]')?.focus()");
            }
        }
    })?;

    // Keep app_handle alive (silences unused variable warning)
    let _ = app_handle;

    Ok(())
}

// ── Main ───────────────────────────────────────────────────────────────────

pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_global_shortcut::Builder::new().build())
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_notification::init())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_fs::init())
        .manage(AppState::default())
        .setup(|app| {
            setup_tray(app.handle())?;
            setup_hotkeys(app.handle())?;

            // Show the main window on startup
            if let Some(main) = app.get_webview_window("main") {
                let _ = main.show();
                let _ = main.set_focus();
            }

            Ok(())
        })
        .on_window_event(|window, event| {
            // When the capture overlay is closed, reset the capturing state
            if window.label() == "capture-overlay" {
                if let tauri::WindowEvent::CloseRequested | tauri::WindowEvent::Destroyed = event {
                    let app = window.app_handle();
                    let state = app.state::<AppState>();
                    let mut is_capturing = state.is_capturing.lock().unwrap();
                    *is_capturing = false;
                }
            }
            // Don't quit when the main window is closed — keep running in tray
            if window.label() == "main" {
                if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                    let _ = window.hide();
                    api.prevent_close();
                }
            }
        })
        .invoke_handler(tauri::generate_handler![
            capture_from_audio,
            show_notification,
        ])
        .run(tauri::generate_context!())
        .expect("error while running Second Brain");
}
