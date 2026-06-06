#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .setup(|app| {
            app.set_activation_policy(tauri::ActivationPolicy::Accessory);
            position_assistant_window(app)?;
            setup_tray(app)?;
            start_mock_behavior_monitor(app.handle().clone());

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

fn position_assistant_window(app: &mut tauri::App) -> tauri::Result<()> {
    use tauri::Manager;

    let Some(window) = app.get_webview_window("assistant") else {
        return Ok(());
    };

    position_assistant_webview_window(&window)
}

fn position_assistant_webview_window(window: &tauri::WebviewWindow) -> tauri::Result<()> {
    use tauri::{PhysicalPosition, Position};

    const WINDOW_WIDTH: f64 = 400.0;
    const WINDOW_HEIGHT: f64 = 240.0;
    const WINDOW_TOP_MARGIN: f64 = 56.0;
    const WINDOW_RIGHT_MARGIN: f64 = 14.0;

    let monitor = window
        .current_monitor()?
        .or(window.primary_monitor()?)
        .ok_or_else(|| tauri::Error::WindowNotFound)?;

    let scale_factor = monitor.scale_factor();
    let monitor_position = monitor.position();
    let monitor_size = monitor.size();
    let width = (WINDOW_WIDTH * scale_factor).round() as i32;
    let height = (WINDOW_HEIGHT * scale_factor).round() as i32;
    let top_margin = (WINDOW_TOP_MARGIN * scale_factor).round() as i32;
    let right_margin = (WINDOW_RIGHT_MARGIN * scale_factor).round() as i32;

    let x = monitor_position.x + monitor_size.width as i32 - width - right_margin;
    let y = monitor_position.y + top_margin;

    window.set_size(tauri::Size::Physical(tauri::PhysicalSize::new(
        width as u32,
        height as u32,
    )))?;
    window.set_position(Position::Physical(PhysicalPosition::new(x, y)))?;
    window.set_always_on_top(true)?;

    Ok(())
}

fn setup_tray(app: &mut tauri::App) -> tauri::Result<()> {
    use tauri::{
        menu::MenuBuilder,
        tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent},
    };

    let menu = MenuBuilder::new(app).text("quit", "退出").build()?;
    let mut tray = TrayIconBuilder::with_id("main")
        .menu(&menu)
        .show_menu_on_left_click(false)
        .tooltip("Radar")
        .on_menu_event(|app, event| {
            if event.id() == "quit" {
                app.exit(0);
            }
        })
        .on_tray_icon_event(|tray, event| {
            if let TrayIconEvent::Click {
                button: MouseButton::Left,
                button_state: MouseButtonState::Up,
                ..
            } = event
            {
                let _ = show_settings_window(tray.app_handle());
            }
        });

    if let Some(icon) = app.default_window_icon().cloned() {
        tray = tray.icon(icon).icon_as_template(true);
    }

    tray.build(app)?;
    Ok(())
}

fn show_settings_window(app: &tauri::AppHandle) -> tauri::Result<()> {
    use tauri::Manager;

    if let Some(window) = app.get_webview_window("settings") {
        let _ = window.center();
        window.show()?;
        window.set_focus()?;
    }

    Ok(())
}

fn show_assistant_window(app: &tauri::AppHandle) -> tauri::Result<()> {
    use tauri::Manager;

    if let Some(window) = app.get_webview_window("assistant") {
        position_assistant_webview_window(&window)?;
        window.show()?;
    }

    Ok(())
}

fn start_mock_behavior_monitor(app: tauri::AppHandle) {
    #[derive(Clone, serde::Serialize)]
    struct MockSuggestion {
        id: u64,
        title: String,
        body: String,
        primary_action: String,
    }

    std::thread::spawn(move || {
        use tauri::Emitter;

        let samples = [
            (
                "你似乎正在重复整理窗口。",
                "Radar 可以帮你把当前工作区保存成一个临时场景，稍后自动恢复。",
                "保存场景",
            ),
            (
                "你连续打开了同一组资料。",
                "要不要把这些页面归为一个研究集合？下次可以直接恢复这组上下文。",
                "创建集合",
            ),
            (
                "当前任务有点像每日例行检查。",
                "Radar 可以把这组步骤记成一个轻量流程，并在相似场景下提醒你。",
                "记录流程",
            ),
        ];

        let mut next_id = 1;

        loop {
            std::thread::sleep(std::time::Duration::from_secs(10));

            let sample = samples[((next_id - 1) as usize) % samples.len()];
            let suggestion = MockSuggestion {
                id: next_id,
                title: sample.0.to_string(),
                body: sample.1.to_string(),
                primary_action: sample.2.to_string(),
            };

            let _ = app.emit_to("assistant", "radar://mock-suggestion", suggestion);
            let _ = show_assistant_window(&app);
            next_id += 1;
        }
    });
}
