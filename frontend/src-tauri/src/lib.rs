use tauri::Manager;

#[tauri::command]
fn load_startup_audio() -> Option<Vec<u8>> {
  // Single designated location: assets/audio.mp3
  let audio_path = std::env::current_exe()
    .ok()
    .and_then(|exe| exe.parent().map(|p| p.join("assets").join("audio.mp3")))
    .filter(|p| p.is_file())
    .unwrap_or_else(|| std::path::PathBuf::from("assets/audio.mp3"));

  if audio_path.is_file() {
    if let Ok(bytes) = std::fs::read(&audio_path) {
      eprintln!("[AMADEUS_TAURI] Loaded external startup audio from: {:?}", audio_path);
      return Some(bytes);
    }
  }
  eprintln!("[AMADEUS_TAURI] No external startup audio found at: {:?}. Silent mode active.", audio_path);
  None
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
  tauri::Builder::default()
    .invoke_handler(tauri::generate_handler![load_startup_audio])
    .setup(|app| {
      eprintln!("[AMADEUS_TAURI] Setup hook running");
      if let Some(win) = app.get_webview_window("main") {
        eprintln!("[AMADEUS_TAURI] Found existing main window! Showing...");
        let _ = win.show();
        let _ = win.set_focus();
      } else if !app.config().app.windows.is_empty() {
        eprintln!("[AMADEUS_TAURI] Building main window from config...");
        match tauri::WebviewWindowBuilder::from_config(app.handle(), &app.config().app.windows[0]) {
          Ok(builder) => match builder.build() {
            Ok(w) => {
              eprintln!("[AMADEUS_TAURI] Main window built successfully!");
              let _ = w.show();
              let _ = w.set_focus();
            }
            Err(e) => eprintln!("[AMADEUS_TAURI] Failed to build window: {:?}", e),
          },
          Err(e) => eprintln!("[AMADEUS_TAURI] Failed to create builder from config: {:?}", e),
        }
      }
      Ok(())
    })
    .run(tauri::generate_context!())
    .expect("error while building tauri application");
}
