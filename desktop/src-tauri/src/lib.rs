#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .setup(|app| {
            position_main_window(app)?;

            if cfg!(debug_assertions) {
                app.handle().plugin(
                    tauri_plugin_log::Builder::default()
                        .level(log::LevelFilter::Info)
                        .build(),
                )?;
            }
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}

fn position_main_window(app: &mut tauri::App) -> tauri::Result<()> {
    use tauri::{Manager, PhysicalPosition, Position};

    const WINDOW_WIDTH: f64 = 400.0;
    const WINDOW_HEIGHT: f64 = 240.0;
    const WINDOW_MARGIN: f64 = 24.0;

    let Some(window) = app.get_webview_window("main") else {
        return Ok(());
    };

    let monitor = window
        .current_monitor()?
        .or(window.primary_monitor()?)
        .ok_or_else(|| tauri::Error::WindowNotFound)?;

    let scale_factor = monitor.scale_factor();
    let monitor_position = monitor.position();
    let monitor_size = monitor.size();
    let width = (WINDOW_WIDTH * scale_factor).round() as i32;
    let height = (WINDOW_HEIGHT * scale_factor).round() as i32;
    let margin = (WINDOW_MARGIN * scale_factor).round() as i32;

    let x = monitor_position.x + monitor_size.width as i32 - width - margin;
    let y = monitor_position.y + margin;

    window.set_size(tauri::Size::Physical(tauri::PhysicalSize::new(
        width as u32,
        height as u32,
    )))?;
    window.set_position(Position::Physical(PhysicalPosition::new(x, y)))?;
    window.set_always_on_top(true)?;

    Ok(())
}
