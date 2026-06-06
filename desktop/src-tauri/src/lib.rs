use std::{
    collections::{HashMap, HashSet},
    path::PathBuf,
    process::{Child, Command, Stdio},
    sync::{Arc, Mutex, Weak},
    time::{Duration, Instant},
};

use tauri::{Emitter, Manager};

const ACTOR_API_PORT: u16 = 47322;
const ACTOR_API_TOKEN: &str = "radar-desktop-actor-token";
const YOUTUBE_ACTOR_ID: &str = "builtin.youtube_search_nab";
const ACTOR_POLL_INTERVAL: Duration = Duration::from_secs(5);
const ACTOR_SUGGESTION_COOLDOWN: Duration = Duration::from_secs(45);

#[derive(Clone)]
struct ActorRuntimeHandle {
    inner: Arc<ActorRuntimeInner>,
}

struct ActorRuntimeInner {
    child: Mutex<Option<Child>>,
    inflight_actors: Mutex<HashSet<String>>,
    cooldown_until: Mutex<HashMap<String, Instant>>,
}

impl ActorRuntimeHandle {
    fn new() -> Self {
        Self {
            inner: Arc::new(ActorRuntimeInner {
                child: Mutex::new(None),
                inflight_actors: Mutex::new(HashSet::new()),
                cooldown_until: Mutex::new(HashMap::new()),
            }),
        }
    }

    fn api_url(&self) -> String {
        actor_api_url()
    }

    fn clear_inflight(&self, actor_id: &str) {
        if let Ok(mut inflight) = self.inner.inflight_actors.lock() {
            inflight.remove(actor_id);
        }
    }

    fn cool_down(&self, actor_id: &str) {
        if let Ok(mut cooldowns) = self.inner.cooldown_until.lock() {
            cooldowns.insert(actor_id.to_string(), Instant::now() + ACTOR_SUGGESTION_COOLDOWN);
        }
    }
}

impl Drop for ActorRuntimeInner {
    fn drop(&mut self) {
        if let Ok(mut child) = self.child.lock() {
            if let Some(process) = child.as_mut() {
                let _ = process.kill();
                let _ = process.wait();
            }
        }
    }
}

#[derive(Clone, serde::Serialize)]
struct AssistantSuggestion {
    id: String,
    title: String,
    body: String,
    primary_action: String,
    actor_id: Option<String>,
    trigger_id: Option<String>,
}

#[derive(serde::Deserialize)]
struct ActorShouldTriggerResponse {
    ok: bool,
    output: Option<ActorShouldTriggerOutput>,
}

#[derive(serde::Deserialize)]
struct ActorShouldTriggerOutput {
    available: Option<bool>,
    trigger_id: Option<String>,
    presentation: Option<ActorPresentation>,
    reason: Option<String>,
}

#[derive(serde::Deserialize)]
struct ActorPresentation {
    title: Option<String>,
    message: Option<String>,
    button_label: Option<String>,
}

#[derive(serde::Deserialize, serde::Serialize)]
struct ActorRunResponse {
    ok: bool,
    output: Option<serde_json::Value>,
    error: Option<String>,
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .manage(ActorRuntimeHandle::new())
        .invoke_handler(tauri::generate_handler![complete_actor_suggestion])
        .setup(|app| {
            app.set_activation_policy(tauri::ActivationPolicy::Accessory);
            position_assistant_window(app)?;
            setup_tray(app)?;

            let actor_runtime = app.state::<ActorRuntimeHandle>().inner().clone();
            start_actor_runtime_process(&actor_runtime);
            start_actor_monitor(app.handle().clone(), Arc::downgrade(&actor_runtime.inner));

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

#[tauri::command]
fn complete_actor_suggestion(
    state: tauri::State<'_, ActorRuntimeHandle>,
    actor_id: String,
    trigger_id: String,
    run: bool,
) -> Result<Option<ActorRunResponse>, String> {
    state.clear_inflight(&actor_id);
    state.cool_down(&actor_id);

    if !run {
        return Ok(None);
    }

    let url = format!("{}/api/actors/{}/run", state.api_url(), actor_id);
    let client = reqwest::blocking::Client::new();
    let response = client
        .post(url)
        .bearer_auth(ACTOR_API_TOKEN)
        .json(&serde_json::json!({ "trigger_id": trigger_id }))
        .send()
        .map_err(|error| format!("failed to run actor: {error}"))?;

    if !response.status().is_success() {
        return Err(format!("actor run failed with HTTP {}", response.status()));
    }

    let payload = response
        .json::<ActorRunResponse>()
        .map_err(|error| format!("failed to parse actor run response: {error}"))?;

    if !payload.ok {
        return Err(payload
            .error
            .clone()
            .unwrap_or_else(|| "actor run failed".to_string()));
    }

    Ok(Some(payload))
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

fn actor_api_url() -> String {
    format!("http://127.0.0.1:{ACTOR_API_PORT}")
}

fn radar_home() -> PathBuf {
    if let Some(value) = std::env::var_os("RADAR_HOME") {
        return PathBuf::from(value);
    }

    let home = std::env::var_os("HOME").unwrap_or_else(|| ".".into());
    PathBuf::from(home).join(".radar")
}

fn radar_repo_root() -> Option<PathBuf> {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .and_then(|path| path.parent())
        .map(|path| path.to_path_buf())
}

fn start_actor_runtime_process(runtime: &ActorRuntimeHandle) {
    let Some(repo_root) = radar_repo_root() else {
        eprintln!("radar actor runtime: could not resolve repo root");
        return;
    };

    let mut child_slot = match runtime.inner.child.lock() {
        Ok(slot) => slot,
        Err(_) => return,
    };

    if child_slot.is_some() {
        return;
    }

    let home = radar_home();
    let child = Command::new("python3")
        .arg("debug/app.py")
        .arg("--host")
        .arg("127.0.0.1")
        .arg("--port")
        .arg(ACTOR_API_PORT.to_string())
        .arg("--work-dir")
        .arg(&home)
        .arg("--actor-path")
        .arg("builtin/actor")
        .current_dir(&repo_root)
        .env("RADAR_HOME", &home)
        .env("RADAR_API_TOKEN", ACTOR_API_TOKEN)
        .env("RADAR_ACTOR_PATH", "builtin/actor")
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn();

    match child {
        Ok(process) => {
            *child_slot = Some(process);
        }
        Err(error) => {
            eprintln!("radar actor runtime: failed to start debug coordinator: {error}");
        }
    }
}

fn start_actor_monitor(app: tauri::AppHandle, runtime: Weak<ActorRuntimeInner>) {
    std::thread::spawn(move || {
        loop {
            std::thread::sleep(ACTOR_POLL_INTERVAL);

            let Some(runtime) = runtime.upgrade() else {
                return;
            };

            let Some(suggestion) = poll_youtube_actor(&runtime) else {
                continue;
            };

            let _ = app.emit_to("assistant", "radar://mock-suggestion", suggestion);
            let _ = show_assistant_window(&app);
        }
    });
}

fn poll_youtube_actor(runtime: &ActorRuntimeInner) -> Option<AssistantSuggestion> {
    if actor_is_in_cooldown(runtime, YOUTUBE_ACTOR_ID) {
        return None;
    }

    if actor_is_inflight(runtime, YOUTUBE_ACTOR_ID) {
        return None;
    }

    let client = reqwest::blocking::Client::new();
    let url = format!("{}/api/actors/{YOUTUBE_ACTOR_ID}/should_trigger", actor_api_url());
    let response = client
        .post(url)
        .bearer_auth(ACTOR_API_TOKEN)
        .json(&serde_json::json!({}))
        .send()
        .ok()?;

    if !response.status().is_success() {
        return None;
    }

    let payload = response.json::<ActorShouldTriggerResponse>().ok()?;
    if !payload.ok {
        return None;
    }

    let output = payload.output?;
    if output.available != Some(true) {
        return None;
    }

    let trigger_id = output.trigger_id?;
    mark_actor_inflight(runtime, YOUTUBE_ACTOR_ID);

    let presentation = output.presentation;
    let title = presentation
        .as_ref()
        .and_then(|item| item.title.clone())
        .unwrap_or_else(|| "Search YouTube for NAB".to_string());
    let body = presentation
        .as_ref()
        .and_then(|item| item.message.clone())
        .or(output.reason)
        .unwrap_or_else(|| "Fill the YouTube search box with NAB.".to_string());
    let primary_action = presentation
        .and_then(|item| item.button_label)
        .unwrap_or_else(|| "Type NAB".to_string());

    Some(AssistantSuggestion {
        id: format!("{YOUTUBE_ACTOR_ID}:{trigger_id}"),
        title,
        body,
        primary_action,
        actor_id: Some(YOUTUBE_ACTOR_ID.to_string()),
        trigger_id: Some(trigger_id),
    })
}

fn actor_is_inflight(runtime: &ActorRuntimeInner, actor_id: &str) -> bool {
    runtime
        .inflight_actors
        .lock()
        .map(|inflight| inflight.contains(actor_id))
        .unwrap_or(false)
}

fn mark_actor_inflight(runtime: &ActorRuntimeInner, actor_id: &str) {
    if let Ok(mut inflight) = runtime.inflight_actors.lock() {
        inflight.insert(actor_id.to_string());
    }
}

fn actor_is_in_cooldown(runtime: &ActorRuntimeInner, actor_id: &str) -> bool {
    if let Ok(mut cooldowns) = runtime.cooldown_until.lock() {
        if let Some(until) = cooldowns.get(actor_id).copied() {
            if Instant::now() < until {
                return true;
            }
            cooldowns.remove(actor_id);
        }
    }

    false
}
