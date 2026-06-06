use std::{
    collections::{HashMap, HashSet},
    io::{Read, Write},
    net::{TcpListener, TcpStream},
    path::PathBuf,
    process::{Child, Command, Stdio},
    sync::{Arc, Mutex, Weak},
    time::{Duration, Instant, SystemTime, UNIX_EPOCH},
};

use tauri::{Emitter, Manager};

const ACTOR_API_PORT: u16 = 47322;
const ACTOR_API_TOKEN: &str = "radar-desktop-actor-token";
const PROXY_API_PORT: u16 = 8888;
const ACTOR_MONITOR_TICK: Duration = Duration::from_secs(1);
const CHROME_BRIDGE_PORT: u16 = 9223;
const COLLECTOR_META_CHAT_TRANSCRIPT: &str = "builtin/collector/chat_transcript/meta.json";
const COLLECTOR_META_CHROME: &str = "builtin/collector/chrome/meta.json";
const COLLECTOR_META_SEATALK: &str = "builtin/collector/seatalk/meta.json";
const DEFAULT_ACTOR_POLL_INTERVAL_SECONDS: u64 = 10;
const DEFAULT_ACTOR_SUGGESTION_COOLDOWN_SECONDS: u64 = 45;
const GOOGLE_OAUTH_AUTHORIZE_URL: &str = "https://accounts.google.com/o/oauth2/v2/auth";
const GOOGLE_OAUTH_TOKEN_URL: &str = "https://oauth2.googleapis.com/token";
const CALENDAR_API_BASE_URL: &str = "https://www.googleapis.com/calendar/v3";
const CALENDAR_READONLY_SCOPE: &str = "https://www.googleapis.com/auth/calendar.readonly";
const GMAIL_API_BASE_URL: &str = "https://gmail.googleapis.com/gmail/v1";
const GMAIL_PROFILE_URL: &str = "https://gmail.googleapis.com/gmail/v1/users/me/profile";
const GMAIL_READONLY_SCOPE: &str = "https://www.googleapis.com/auth/gmail.readonly";
const GOOGLE_CONNECTION_SCOPES: &str =
    "openid email https://www.googleapis.com/auth/gmail.readonly https://www.googleapis.com/auth/calendar.readonly";
const GOOGLE_OAUTH_TIMEOUT: Duration = Duration::from_secs(180);

#[derive(Clone)]
struct ActorRuntimeHandle {
    inner: Arc<ActorRuntimeInner>,
}

struct ActorRuntimeInner {
    child: Mutex<Option<Child>>,
    inflight_actors: Mutex<HashSet<String>>,
    cooldown_until: Mutex<HashMap<String, Instant>>,
    last_polled_at: Mutex<HashMap<String, Instant>>,
}

#[derive(Clone, serde::Serialize)]
struct MonitoringStatus {
    running: bool,
    api_url: String,
    chrome_bridge_url: String,
    work_dir: String,
}

impl ActorRuntimeHandle {
    fn new() -> Self {
        Self {
            inner: Arc::new(ActorRuntimeInner {
                child: Mutex::new(None),
                inflight_actors: Mutex::new(HashSet::new()),
                cooldown_until: Mutex::new(HashMap::new()),
                last_polled_at: Mutex::new(HashMap::new()),
            }),
        }
    }

    fn api_url(&self) -> String {
        actor_api_url()
    }

    fn clear_inflight(&self, actor_id: &str) {
        clear_actor_inflight(&self.inner, actor_id);
    }

    fn cool_down(&self, actor_id: &str) {
        apply_actor_cooldown(&self.inner, actor_id, None);
    }

    fn status(&self) -> MonitoringStatus {
        monitoring_status(self)
    }

    fn start(&self, collector_meta: &str) -> MonitoringStatus {
        start_actor_runtime_process(self, collector_meta);
        self.status()
    }

    fn stop(&self) -> MonitoringStatus {
        stop_actor_runtime_process(self);
        self.status()
    }
}

impl Drop for ActorRuntimeInner {
    fn drop(&mut self) {
        stop_actor_runtime_inner(self);
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

#[derive(Clone, serde::Deserialize)]
struct ActorListResponse {
    actors: Vec<ActorPackage>,
}

#[derive(Clone, serde::Deserialize)]
struct ActorPackage {
    actor_id: String,
    title: Option<String>,
    #[serde(default = "default_true")]
    enabled: bool,
    #[serde(default)]
    activation: ActorActivation,
    #[serde(default)]
    trigger: ActorTrigger,
}

#[derive(Clone, Default, serde::Deserialize)]
struct ActorActivation {
    mode: Option<String>,
    button_label: Option<String>,
}

#[derive(Clone, Default, serde::Deserialize)]
struct ActorTrigger {
    polling_interval_seconds: Option<u64>,
}

#[derive(serde::Deserialize)]
struct ActorShouldTriggerOutput {
    available: Option<bool>,
    trigger_id: Option<String>,
    presentation: Option<ActorPresentation>,
    reason: Option<String>,
    debounce_seconds: Option<u64>,
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

#[derive(Clone)]
struct GoogleOAuthConfig {
    client_id: String,
    client_secret: Option<String>,
}

#[derive(serde::Serialize)]
struct GoogleConnectionStatus {
    connected: bool,
    email: Option<String>,
    scopes: Vec<String>,
    configured: bool,
}

#[derive(serde::Serialize, serde::Deserialize)]
struct GoogleConnection {
    provider: String,
    email: Option<String>,
    access_token: String,
    refresh_token: Option<String>,
    expires_at: Option<u64>,
    scopes: Vec<String>,
}

#[derive(serde::Deserialize)]
struct GoogleTokenResponse {
    access_token: String,
    expires_in: Option<u64>,
    refresh_token: Option<String>,
    scope: Option<String>,
}

#[derive(serde::Deserialize)]
struct GmailProfileResponse {
    #[serde(rename = "emailAddress")]
    email_address: Option<String>,
}

#[derive(Default, serde::Deserialize)]
struct RadarLocalConfig {
    google: Option<GoogleLocalConfig>,
    google_client_id: Option<String>,
    google_client_secret: Option<String>,
}

#[derive(Default, serde::Deserialize)]
struct GoogleLocalConfig {
    client_id: Option<String>,
    client_secret: Option<String>,
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .manage(ActorRuntimeHandle::new())
        .invoke_handler(tauri::generate_handler![
            complete_actor_suggestion,
            get_monitoring_status,
            start_monitoring,
            stop_monitoring,
            get_google_connection_status,
            connect_google_account,
            disconnect_google_account
        ])
        .setup(|app| {
            app.set_activation_policy(tauri::ActivationPolicy::Accessory);
            position_assistant_window(app)?;
            setup_tray(app)?;

            let actor_runtime = app.state::<ActorRuntimeHandle>().inner().clone();
            let collector_meta = default_collector_meta();
            start_actor_runtime_process(&actor_runtime, &collector_meta);
            start_actor_monitor(app.handle().clone(), Arc::downgrade(&actor_runtime.inner));
            start_proxy_api_server();

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
fn get_monitoring_status(state: tauri::State<'_, ActorRuntimeHandle>) -> MonitoringStatus {
    state.status()
}

#[tauri::command]
fn start_monitoring(
    state: tauri::State<'_, ActorRuntimeHandle>,
    collector_ids: Option<Vec<String>>,
) -> MonitoringStatus {
    let collector_meta = collector_meta_for_ids(collector_ids);
    state.start(&collector_meta)
}

#[tauri::command]
fn stop_monitoring(state: tauri::State<'_, ActorRuntimeHandle>) -> MonitoringStatus {
    state.stop()
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

    Ok(Some(run_actor_action(
        &state.api_url(),
        &actor_id,
        &trigger_id,
    )?))
}

fn run_actor_action(
    api_url: &str,
    actor_id: &str,
    trigger_id: &str,
) -> Result<ActorRunResponse, String> {
    let url = format!("{api_url}/api/actors/{actor_id}/run");
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

    Ok(payload)
}

#[tauri::command]
fn get_google_connection_status() -> Result<GoogleConnectionStatus, String> {
    let configured = google_oauth_config().is_ok();
    let Some(connection) = read_google_connection()? else {
        return Ok(GoogleConnectionStatus {
            connected: false,
            email: None,
            scopes: google_default_scopes(),
            configured,
        });
    };

    Ok(GoogleConnectionStatus {
        connected: true,
        email: connection.email,
        scopes: connection.scopes,
        configured,
    })
}

#[tauri::command]
fn connect_google_account() -> Result<GoogleConnectionStatus, String> {
    let config = google_oauth_config()?;
    let listener = TcpListener::bind("127.0.0.1:0")
        .map_err(|error| format!("failed to bind OAuth callback server: {error}"))?;
    listener
        .set_nonblocking(true)
        .map_err(|error| format!("failed to configure OAuth callback server: {error}"))?;

    let port = listener
        .local_addr()
        .map_err(|error| format!("failed to read OAuth callback address: {error}"))?
        .port();
    let redirect_uri = format!("http://127.0.0.1:{port}/oauth/google/callback");
    let state = google_oauth_state();
    let auth_url = google_authorization_url(&config.client_id, &redirect_uri, &state);

    open_external_url(&auth_url)?;
    let authorization_code = wait_for_google_oauth_code(listener, &state)?;
    let token = exchange_google_oauth_code(&config, &redirect_uri, &authorization_code)?;
    let scopes = google_token_scopes(token.scope.as_deref());
    ensure_required_google_scopes_granted(&scopes)?;
    let profile = fetch_gmail_profile(&token.access_token)?;

    let connection = GoogleConnection {
        provider: "google".to_string(),
        email: profile.email_address.clone(),
        access_token: token.access_token,
        refresh_token: token.refresh_token,
        expires_at: token
            .expires_in
            .map(|seconds| current_unix_seconds() + seconds),
        scopes,
    };

    write_google_connection(&connection)?;

    Ok(GoogleConnectionStatus {
        connected: true,
        email: profile.email_address,
        scopes: connection.scopes,
        configured: true,
    })
}

#[tauri::command]
fn disconnect_google_account() -> Result<GoogleConnectionStatus, String> {
    let path = google_connection_path();
    if path.exists() {
        std::fs::remove_file(&path)
            .map_err(|error| format!("failed to remove Google connection: {error}"))?;
    }

    Ok(GoogleConnectionStatus {
        connected: false,
        email: None,
        scopes: google_default_scopes(),
        configured: google_oauth_config().is_ok(),
    })
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
        window.show()?;
    }

    Ok(())
}

fn actor_api_url() -> String {
    format!("http://127.0.0.1:{ACTOR_API_PORT}")
}

fn start_proxy_api_server() {
    std::thread::spawn(move || {
        let address = format!("127.0.0.1:{PROXY_API_PORT}");
        let listener = match TcpListener::bind(&address) {
            Ok(listener) => listener,
            Err(error) => {
                eprintln!("radar proxy api: failed to bind {address}: {error}");
                return;
            }
        };
        let client = reqwest::blocking::Client::new();

        for stream in listener.incoming() {
            match stream {
                Ok(stream) => {
                    let client = client.clone();
                    std::thread::spawn(move || handle_proxy_api_connection(stream, client));
                }
                Err(error) => {
                    eprintln!("radar proxy api: failed to accept connection: {error}");
                }
            }
        }
    });
}

struct ProxyHttpRequest {
    method: String,
    target: String,
    headers: HashMap<String, String>,
    body: Vec<u8>,
}

fn handle_proxy_api_connection(mut stream: TcpStream, client: reqwest::blocking::Client) {
    let _ = stream.set_read_timeout(Some(Duration::from_secs(8)));
    let _ = stream.set_write_timeout(Some(Duration::from_secs(8)));

    match read_proxy_http_request(&mut stream)
        .and_then(|request| proxy_api_response(&client, request))
    {
        Ok(response) => {
            let _ = stream.write_all(&response);
        }
        Err(error) => {
            let response = proxy_json_response(
                500,
                serde_json::json!({
                    "ok": false,
                    "error": error,
                })
                .to_string()
                .as_bytes(),
            );
            let _ = stream.write_all(&response);
        }
    }
}

fn read_proxy_http_request(stream: &mut TcpStream) -> Result<ProxyHttpRequest, String> {
    let mut buffer = Vec::new();
    let header_end = loop {
        let mut chunk = [0_u8; 4096];
        let size = stream
            .read(&mut chunk)
            .map_err(|error| format!("failed to read proxy request: {error}"))?;
        if size == 0 {
            return Err("proxy request was empty".to_string());
        }
        buffer.extend_from_slice(&chunk[..size]);

        if let Some(index) = find_header_end(&buffer) {
            break index;
        }
        if buffer.len() > 64 * 1024 {
            return Err("proxy request headers are too large".to_string());
        }
    };

    let header_bytes = &buffer[..header_end];
    let header_text = String::from_utf8_lossy(header_bytes);
    let mut lines = header_text.split("\r\n");
    let request_line = lines
        .next()
        .ok_or_else(|| "proxy request line is missing".to_string())?;
    let mut request_parts = request_line.split_whitespace();
    let method = request_parts
        .next()
        .ok_or_else(|| "proxy request method is missing".to_string())?
        .to_string();
    let target = request_parts
        .next()
        .ok_or_else(|| "proxy request target is missing".to_string())?
        .to_string();

    let mut headers = HashMap::new();
    for line in lines {
        if line.is_empty() {
            continue;
        }
        if let Some((name, value)) = line.split_once(':') {
            headers.insert(name.trim().to_ascii_lowercase(), value.trim().to_string());
        }
    }

    let content_length = headers
        .get("content-length")
        .and_then(|value| value.parse::<usize>().ok())
        .unwrap_or(0);
    let body_start = header_end + 4;
    let mut body = buffer.get(body_start..).unwrap_or_default().to_vec();

    while body.len() < content_length {
        let mut chunk = vec![0_u8; content_length - body.len()];
        let size = stream
            .read(&mut chunk)
            .map_err(|error| format!("failed to read proxy request body: {error}"))?;
        if size == 0 {
            break;
        }
        body.extend_from_slice(&chunk[..size]);
    }
    body.truncate(content_length);

    Ok(ProxyHttpRequest {
        method,
        target,
        headers,
        body,
    })
}

fn find_header_end(buffer: &[u8]) -> Option<usize> {
    buffer.windows(4).position(|window| window == b"\r\n\r\n")
}

fn proxy_api_response(
    client: &reqwest::blocking::Client,
    request: ProxyHttpRequest,
) -> Result<Vec<u8>, String> {
    if request.method.eq_ignore_ascii_case("OPTIONS") {
        return Ok(proxy_empty_response(204));
    }

    if request.target.starts_with("/api/google_gmail") {
        return proxy_google_api_response(
            client,
            request,
            "/api/google_gmail",
            GMAIL_API_BASE_URL,
            GMAIL_READONLY_SCOPE,
            "Gmail",
        );
    }

    if request.target.starts_with("/api/google_calendar") {
        return proxy_google_api_response(
            client,
            request,
            "/api/google_calendar",
            CALENDAR_API_BASE_URL,
            CALENDAR_READONLY_SCOPE,
            "Google Calendar",
        );
    }

    Ok(proxy_json_response(
        404,
        br#"{"ok":false,"error":"unknown proxy api"}"#,
    ))
}

fn proxy_google_api_response(
    client: &reqwest::blocking::Client,
    request: ProxyHttpRequest,
    local_prefix: &str,
    upstream_base_url: &str,
    required_scope: &str,
    api_label: &str,
) -> Result<Vec<u8>, String> {
    let upstream_url =
        match google_api_upstream_url(&request.target, local_prefix, upstream_base_url) {
            Ok(url) => url,
            Err(error) => {
                return Ok(proxy_json_response(
                    400,
                    serde_json::json!({
                        "ok": false,
                        "error": error,
                    })
                    .to_string()
                    .as_bytes(),
                ));
            }
        };
    let access_token = google_access_token_for_proxy(required_scope, api_label)?;
    let method = reqwest::Method::from_bytes(request.method.as_bytes())
        .map_err(|error| format!("unsupported proxy method: {error}"))?;

    let mut builder = client
        .request(method, upstream_url)
        .bearer_auth(access_token);
    if let Some(content_type) = request.headers.get("content-type") {
        builder = builder.header(reqwest::header::CONTENT_TYPE, content_type);
    }
    if let Some(accept) = request.headers.get("accept") {
        builder = builder.header(reqwest::header::ACCEPT, accept);
    }
    if !request.body.is_empty() {
        builder = builder.body(request.body);
    }

    let response = builder
        .send()
        .map_err(|error| format!("failed to call {api_label} API: {error}"))?;
    let status = response.status().as_u16();
    let content_type = response
        .headers()
        .get(reqwest::header::CONTENT_TYPE)
        .and_then(|value| value.to_str().ok())
        .unwrap_or("application/json")
        .to_string();
    let body = response
        .bytes()
        .map_err(|error| format!("failed to read {api_label} API response: {error}"))?;

    Ok(proxy_response(status, &content_type, body.as_ref()))
}

fn google_api_upstream_url(
    target: &str,
    local_prefix: &str,
    upstream_base_url: &str,
) -> Result<String, String> {
    let suffix = target
        .strip_prefix(&format!("{local_prefix}/"))
        .or_else(|| target.strip_prefix(local_prefix))
        .unwrap_or_default()
        .trim_start_matches('/');

    if suffix.is_empty() {
        return Err(format!("missing Google API path after {local_prefix}/"));
    }

    Ok(format!("{upstream_base_url}/{suffix}"))
}

fn proxy_empty_response(status: u16) -> Vec<u8> {
    proxy_response(status, "text/plain", &[])
}

fn proxy_json_response(status: u16, body: &[u8]) -> Vec<u8> {
    proxy_response(status, "application/json", body)
}

fn proxy_response(status: u16, content_type: &str, body: &[u8]) -> Vec<u8> {
    let reason = match status {
        200 => "OK",
        201 => "Created",
        204 => "No Content",
        400 => "Bad Request",
        401 => "Unauthorized",
        403 => "Forbidden",
        404 => "Not Found",
        405 => "Method Not Allowed",
        429 => "Too Many Requests",
        500 => "Internal Server Error",
        502 => "Bad Gateway",
        503 => "Service Unavailable",
        _ => "OK",
    };
    let headers = format!(
        "HTTP/1.1 {status} {reason}\r\nContent-Type: {content_type}\r\nContent-Length: {}\r\nAccess-Control-Allow-Origin: *\r\nAccess-Control-Allow-Methods: GET,POST,PUT,PATCH,DELETE,OPTIONS\r\nAccess-Control-Allow-Headers: Content-Type, Accept\r\nConnection: close\r\n\r\n",
        body.len()
    );
    let mut response = headers.into_bytes();
    response.extend_from_slice(body);
    response
}

fn chrome_bridge_url() -> String {
    format!("ws://127.0.0.1:{CHROME_BRIDGE_PORT}/radar-chrome-bridge-ws")
}

fn monitoring_status(runtime: &ActorRuntimeHandle) -> MonitoringStatus {
    MonitoringStatus {
        running: actor_runtime_is_running(runtime),
        api_url: actor_api_url(),
        chrome_bridge_url: chrome_bridge_url(),
        work_dir: radar_home().display().to_string(),
    }
}

fn default_collector_meta() -> String {
    [COLLECTOR_META_CHROME, COLLECTOR_META_CHAT_TRANSCRIPT].join(",")
}

fn collector_meta_for_ids(collector_ids: Option<Vec<String>>) -> String {
    let Some(collector_ids) = collector_ids else {
        return default_collector_meta();
    };

    let mut meta_paths = Vec::new();
    for collector_id in collector_ids {
        match collector_id.as_str() {
            "collector.chrome" => meta_paths.push(COLLECTOR_META_CHROME),
            "collector.chat_transcript" => meta_paths.push(COLLECTOR_META_CHAT_TRANSCRIPT),
            "collector.seatalk" => meta_paths.push(COLLECTOR_META_SEATALK),
            _ => {}
        }
    }

    meta_paths.join(",")
}

fn radar_home() -> PathBuf {
    if let Some(value) = std::env::var_os("RADAR_HOME") {
        return PathBuf::from(value);
    }

    let home = std::env::var_os("HOME").unwrap_or_else(|| ".".into());
    PathBuf::from(home).join(".radar")
}

fn google_connection_path() -> PathBuf {
    radar_home().join("connections").join("google.json")
}

fn radar_config_path() -> PathBuf {
    radar_home().join("config.json")
}

fn google_default_scopes() -> Vec<String> {
    GOOGLE_CONNECTION_SCOPES
        .split_whitespace()
        .map(str::to_string)
        .collect()
}

fn google_token_scopes(scope: Option<&str>) -> Vec<String> {
    scope
        .unwrap_or(GOOGLE_CONNECTION_SCOPES)
        .split_whitespace()
        .map(str::to_string)
        .collect()
}

fn ensure_google_scope_granted(
    scopes: &[String],
    required_scope: &str,
    api_label: &str,
) -> Result<(), String> {
    if scopes.iter().any(|scope| scope == required_scope) {
        return Ok(());
    }

    Err(format!(
        "Google account connected, but {api_label} permission was not granted. Add {required_scope} to your Google OAuth consent screen scopes, make sure the {api_label} API is enabled, then connect again."
    ))
}

fn ensure_required_google_scopes_granted(scopes: &[String]) -> Result<(), String> {
    ensure_google_scope_granted(scopes, GMAIL_READONLY_SCOPE, "Gmail")?;
    ensure_google_scope_granted(scopes, CALENDAR_READONLY_SCOPE, "Google Calendar")
}

fn google_oauth_config() -> Result<GoogleOAuthConfig, String> {
    let local_config = read_radar_local_config()?;
    let client_id = local_config
        .google
        .as_ref()
        .and_then(|google| non_empty_string(google.client_id.as_deref()))
        .or_else(|| non_empty_string(local_config.google_client_id.as_deref()))
        .or_else(|| std::env::var("RADAR_GOOGLE_CLIENT_ID").ok())
        .or_else(|| std::env::var("GOOGLE_CLIENT_ID").ok())
        .and_then(|value| non_empty_string(Some(&value)))
        .ok_or_else(|| {
            format!(
                "missing Google OAuth client id in {}",
                radar_config_path().display()
            )
        })?;

    let client_secret = local_config
        .google
        .as_ref()
        .and_then(|google| non_empty_string(google.client_secret.as_deref()))
        .or_else(|| non_empty_string(local_config.google_client_secret.as_deref()))
        .or_else(|| std::env::var("RADAR_GOOGLE_CLIENT_SECRET").ok())
        .or_else(|| std::env::var("GOOGLE_CLIENT_SECRET").ok())
        .and_then(|value| non_empty_string(Some(&value)));

    Ok(GoogleOAuthConfig {
        client_id,
        client_secret,
    })
}

fn read_radar_local_config() -> Result<RadarLocalConfig, String> {
    let path = radar_config_path();
    if !path.exists() {
        return Ok(RadarLocalConfig::default());
    }

    let content = std::fs::read_to_string(&path)
        .map_err(|error| format!("failed to read Radar config: {error}"))?;
    serde_json::from_str::<RadarLocalConfig>(&content)
        .map_err(|error| format!("failed to parse Radar config: {error}"))
}

fn non_empty_string(value: Option<&str>) -> Option<String> {
    value
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(str::to_string)
}

fn google_oauth_state() -> String {
    format!(
        "radar-{}-{}",
        std::process::id(),
        current_unix_nanos().unwrap_or_default()
    )
}

fn current_unix_seconds() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_secs())
        .unwrap_or_default()
}

fn current_unix_nanos() -> Option<u128> {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .ok()
        .map(|duration| duration.as_nanos())
}

fn google_authorization_url(client_id: &str, redirect_uri: &str, state: &str) -> String {
    let mut url = url::Url::parse(GOOGLE_OAUTH_AUTHORIZE_URL).expect("valid Google OAuth URL");
    url.query_pairs_mut()
        .append_pair("client_id", client_id)
        .append_pair("redirect_uri", redirect_uri)
        .append_pair("response_type", "code")
        .append_pair("scope", GOOGLE_CONNECTION_SCOPES)
        .append_pair("access_type", "offline")
        .append_pair("prompt", "consent")
        .append_pair("state", state);
    url.to_string()
}

fn open_external_url(url: &str) -> Result<(), String> {
    #[cfg(target_os = "macos")]
    let command = Command::new("open").arg(url).spawn();

    #[cfg(target_os = "windows")]
    let command = Command::new("cmd").args(["/C", "start", url]).spawn();

    #[cfg(all(unix, not(target_os = "macos")))]
    let command = Command::new("xdg-open").arg(url).spawn();

    command
        .map(|_| ())
        .map_err(|error| format!("failed to open browser for Google OAuth: {error}"))
}

struct OAuthCallback {
    code: Option<String>,
    state: Option<String>,
    error: Option<String>,
}

fn wait_for_google_oauth_code(
    listener: TcpListener,
    expected_state: &str,
) -> Result<String, String> {
    let started_at = Instant::now();

    loop {
        if started_at.elapsed() > GOOGLE_OAUTH_TIMEOUT {
            return Err("timed out waiting for Google OAuth callback".to_string());
        }

        match listener.accept() {
            Ok((mut stream, _)) => {
                let mut buffer = [0_u8; 4096];
                let bytes_read = stream
                    .read(&mut buffer)
                    .map_err(|error| format!("failed to read OAuth callback: {error}"))?;
                let request = String::from_utf8_lossy(&buffer[..bytes_read]);
                let first_line = request.lines().next().unwrap_or_default();
                let callback = parse_oauth_callback_line(first_line)?;

                let response = if callback.error.is_some() {
                    "HTTP/1.1 400 Bad Request\r\nContent-Type: text/plain\r\n\r\nGoogle connection was cancelled."
                } else {
                    "HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\n\r\nGoogle account connected. You can return to Radar."
                };
                let _ = stream.write_all(response.as_bytes());

                if let Some(error) = callback.error {
                    return Err(format!("Google OAuth failed: {error}"));
                }
                if callback.state.as_deref() != Some(expected_state) {
                    return Err("Google OAuth state did not match".to_string());
                }
                return callback
                    .code
                    .ok_or_else(|| "Google OAuth callback did not include a code".to_string());
            }
            Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => {
                std::thread::sleep(Duration::from_millis(120));
            }
            Err(error) => return Err(format!("failed to accept OAuth callback: {error}")),
        }
    }
}

fn parse_oauth_callback_line(line: &str) -> Result<OAuthCallback, String> {
    let path = line
        .split_whitespace()
        .nth(1)
        .ok_or_else(|| "OAuth callback request was malformed".to_string())?;
    let url = url::Url::parse(&format!("http://127.0.0.1{path}"))
        .map_err(|error| format!("OAuth callback URL was malformed: {error}"))?;

    let mut code = None;
    let mut state = None;
    let mut oauth_error = None;

    for (key, value) in url.query_pairs() {
        match key.as_ref() {
            "code" => code = Some(value.into_owned()),
            "state" => state = Some(value.into_owned()),
            "error" => oauth_error = Some(value.into_owned()),
            _ => {}
        }
    }

    Ok(OAuthCallback {
        code,
        state,
        error: oauth_error,
    })
}

fn exchange_google_oauth_code(
    config: &GoogleOAuthConfig,
    redirect_uri: &str,
    code: &str,
) -> Result<GoogleTokenResponse, String> {
    let mut form = vec![
        ("client_id", config.client_id.as_str()),
        ("code", code),
        ("grant_type", "authorization_code"),
        ("redirect_uri", redirect_uri),
    ];

    if let Some(secret) = config.client_secret.as_deref() {
        form.push(("client_secret", secret));
    }

    let response = reqwest::blocking::Client::new()
        .post(GOOGLE_OAUTH_TOKEN_URL)
        .form(&form)
        .send()
        .map_err(|error| format!("failed to exchange Google OAuth code: {error}"))?;

    if !response.status().is_success() {
        let status = response.status();
        let body = response.text().unwrap_or_default();
        return Err(format!(
            "Google token exchange failed with HTTP {status}: {body}"
        ));
    }

    response
        .json::<GoogleTokenResponse>()
        .map_err(|error| format!("failed to parse Google token response: {error}"))
}

fn fetch_gmail_profile(access_token: &str) -> Result<GmailProfileResponse, String> {
    let response = reqwest::blocking::Client::new()
        .get(GMAIL_PROFILE_URL)
        .bearer_auth(access_token)
        .send()
        .map_err(|error| format!("failed to access Gmail profile: {error}"))?;

    if !response.status().is_success() {
        return Err(format!(
            "Gmail profile request failed with HTTP {}",
            response.status()
        ));
    }

    response
        .json::<GmailProfileResponse>()
        .map_err(|error| format!("failed to parse Gmail profile: {error}"))
}

fn google_access_token_for_proxy(required_scope: &str, api_label: &str) -> Result<String, String> {
    let Some(mut connection) = read_google_connection()? else {
        return Err("Google account is not connected".to_string());
    };

    ensure_google_scope_granted(&connection.scopes, required_scope, api_label)?;

    let expires_soon = connection
        .expires_at
        .map(|expires_at| expires_at <= current_unix_seconds() + 60)
        .unwrap_or(false);

    if expires_soon {
        refresh_google_connection_token(&mut connection, required_scope, api_label)?;
        write_google_connection(&connection)?;
    }

    Ok(connection.access_token)
}

fn refresh_google_connection_token(
    connection: &mut GoogleConnection,
    required_scope: &str,
    api_label: &str,
) -> Result<(), String> {
    let refresh_token = connection.refresh_token.clone().ok_or_else(|| {
        "Google access token expired and no refresh token is available".to_string()
    })?;
    let config = google_oauth_config()?;
    let mut form = vec![
        ("client_id", config.client_id.as_str()),
        ("grant_type", "refresh_token"),
        ("refresh_token", refresh_token.as_str()),
    ];

    if let Some(secret) = config.client_secret.as_deref() {
        form.push(("client_secret", secret));
    }

    let response = reqwest::blocking::Client::new()
        .post(GOOGLE_OAUTH_TOKEN_URL)
        .form(&form)
        .send()
        .map_err(|error| format!("failed to refresh Google token: {error}"))?;

    if !response.status().is_success() {
        let status = response.status();
        let body = response.text().unwrap_or_default();
        return Err(format!(
            "Google token refresh failed with HTTP {status}: {body}"
        ));
    }

    let token = response
        .json::<GoogleTokenResponse>()
        .map_err(|error| format!("failed to parse Google token refresh response: {error}"))?;
    connection.access_token = token.access_token;
    connection.expires_at = token
        .expires_in
        .map(|seconds| current_unix_seconds() + seconds);

    if let Some(refresh_token) = token.refresh_token {
        connection.refresh_token = Some(refresh_token);
    }
    if token.scope.is_some() {
        connection.scopes = google_token_scopes(token.scope.as_deref());
        ensure_google_scope_granted(&connection.scopes, required_scope, api_label)?;
    }

    Ok(())
}

fn read_google_connection() -> Result<Option<GoogleConnection>, String> {
    let path = google_connection_path();
    if !path.exists() {
        return Ok(None);
    }

    let content = std::fs::read_to_string(&path)
        .map_err(|error| format!("failed to read Google connection: {error}"))?;
    serde_json::from_str::<GoogleConnection>(&content)
        .map(Some)
        .map_err(|error| format!("failed to parse Google connection: {error}"))
}

fn write_google_connection(connection: &GoogleConnection) -> Result<(), String> {
    let path = google_connection_path();
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)
            .map_err(|error| format!("failed to create connection directory: {error}"))?;
    }

    let content = serde_json::to_string_pretty(connection)
        .map_err(|error| format!("failed to serialize Google connection: {error}"))?;
    std::fs::write(path, content)
        .map_err(|error| format!("failed to write Google connection: {error}"))
}

fn radar_repo_root() -> Option<PathBuf> {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .and_then(|path| path.parent())
        .map(|path| path.to_path_buf())
}

fn start_actor_runtime_process(runtime: &ActorRuntimeHandle, collector_meta: &str) {
    if actor_runtime_is_running(runtime) {
        return;
    }

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
        .env("RADAR_COLLECTOR_META", collector_meta)
        .env("RADAR_CHROME_BRIDGE_PORT", CHROME_BRIDGE_PORT.to_string())
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

fn stop_actor_runtime_process(runtime: &ActorRuntimeHandle) {
    stop_actor_runtime_inner(runtime.inner.as_ref());
}

fn stop_actor_runtime_inner(runtime: &ActorRuntimeInner) {
    let child = match runtime.child.lock() {
        Ok(mut child_slot) => child_slot.take(),
        Err(_) => None,
    };

    if let Some(mut process) = child {
        let _ = process.kill();
        let _ = process.wait();
    }

    if let Ok(mut inflight_actors) = runtime.inflight_actors.lock() {
        inflight_actors.clear();
    }
    if let Ok(mut cooldown_until) = runtime.cooldown_until.lock() {
        cooldown_until.clear();
    }
    if let Ok(mut last_polled_at) = runtime.last_polled_at.lock() {
        last_polled_at.clear();
    }
}

fn actor_runtime_is_running(runtime: &ActorRuntimeHandle) -> bool {
    let mut child_slot = match runtime.inner.child.lock() {
        Ok(slot) => slot,
        Err(_) => return false,
    };

    let should_clear = match child_slot.as_mut() {
        Some(process) => match process.try_wait() {
            Ok(Some(_)) | Err(_) => true,
            Ok(None) => return true,
        },
        None => false,
    };

    if should_clear {
        *child_slot = None;
    }

    false
}

fn start_actor_monitor(app: tauri::AppHandle, runtime: Weak<ActorRuntimeInner>) {
    std::thread::spawn(move || {
        let client = reqwest::blocking::Client::new();

        loop {
            std::thread::sleep(ACTOR_MONITOR_TICK);

            let Some(runtime) = runtime.upgrade() else {
                return;
            };

            let Ok(actors) = fetch_actor_packages(&client) else {
                continue;
            };

            for actor in actors {
                if !actor.enabled || !actor_should_run_in_background(&actor) {
                    continue;
                }

                if !actor_poll_is_due(&runtime, &actor) {
                    continue;
                }

                match poll_actor(&client, &runtime, &actor) {
                    Some(ActorPollOutcome::Suggestion(suggestion)) => {
                        let _ = app.emit_to("assistant", "radar://mock-suggestion", suggestion);
                        let _ = show_assistant_window(&app);
                    }
                    Some(ActorPollOutcome::AutomaticRun {
                        actor_id,
                        trigger_id,
                        debounce_seconds,
                    }) => {
                        start_automatic_actor_run(
                            runtime.clone(),
                            actor_id,
                            trigger_id,
                            debounce_seconds,
                        );
                    }
                    None => {}
                }
            }
        }
    });
}

enum ActorPollOutcome {
    Suggestion(AssistantSuggestion),
    AutomaticRun {
        actor_id: String,
        trigger_id: String,
        debounce_seconds: Option<u64>,
    },
}

fn fetch_actor_packages(
    client: &reqwest::blocking::Client,
) -> Result<Vec<ActorPackage>, reqwest::Error> {
    let response = client
        .get(format!("{}/api/actors", actor_api_url()))
        .bearer_auth(ACTOR_API_TOKEN)
        .send()?;

    if !response.status().is_success() {
        return Ok(Vec::new());
    }

    Ok(response.json::<ActorListResponse>()?.actors)
}

fn actor_should_run_in_background(actor: &ActorPackage) -> bool {
    matches!(
        actor.activation.mode.as_deref().unwrap_or("manual"),
        "automatic" | "desktop_option"
    )
}

fn default_true() -> bool {
    true
}

fn actor_poll_is_due(runtime: &ActorRuntimeInner, actor: &ActorPackage) -> bool {
    let interval_seconds = actor
        .trigger
        .polling_interval_seconds
        .unwrap_or(DEFAULT_ACTOR_POLL_INTERVAL_SECONDS)
        .max(1);
    let interval = Duration::from_secs(interval_seconds);
    let now = Instant::now();

    let Ok(mut last_polled_at) = runtime.last_polled_at.lock() else {
        return false;
    };

    if let Some(last_polled) = last_polled_at.get(&actor.actor_id) {
        if now.duration_since(*last_polled) < interval {
            return false;
        }
    }

    last_polled_at.insert(actor.actor_id.clone(), now);
    true
}

fn poll_actor(
    client: &reqwest::blocking::Client,
    runtime: &Arc<ActorRuntimeInner>,
    actor: &ActorPackage,
) -> Option<ActorPollOutcome> {
    if actor_is_in_cooldown(runtime, &actor.actor_id) {
        return None;
    }

    if actor_is_inflight(runtime, &actor.actor_id) {
        return None;
    }

    let url = format!(
        "{}/api/actors/{}/should_trigger",
        actor_api_url(),
        actor.actor_id
    );
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
    if actor.activation.mode.as_deref() == Some("automatic") {
        mark_actor_inflight(runtime, &actor.actor_id);
        return Some(ActorPollOutcome::AutomaticRun {
            actor_id: actor.actor_id.clone(),
            trigger_id,
            debounce_seconds: output.debounce_seconds,
        });
    }

    mark_actor_inflight(runtime, &actor.actor_id);

    let presentation = output.presentation;
    let title = presentation
        .as_ref()
        .and_then(|item| item.title.clone())
        .or_else(|| actor.title.clone())
        .unwrap_or_else(|| actor.actor_id.clone());
    let body = presentation
        .as_ref()
        .and_then(|item| item.message.clone())
        .or(output.reason)
        .unwrap_or_else(|| "A Radar actor is available.".to_string());
    let primary_action = presentation
        .and_then(|item| item.button_label)
        .or_else(|| actor.activation.button_label.clone())
        .unwrap_or_else(|| "Run".to_string());

    Some(ActorPollOutcome::Suggestion(AssistantSuggestion {
        id: format!("{}:{trigger_id}", actor.actor_id),
        title,
        body,
        primary_action,
        actor_id: Some(actor.actor_id.clone()),
        trigger_id: Some(trigger_id),
    }))
}

fn start_automatic_actor_run(
    runtime: Arc<ActorRuntimeInner>,
    actor_id: String,
    trigger_id: String,
    debounce_seconds: Option<u64>,
) {
    std::thread::spawn(move || {
        if let Err(error) = run_actor_action(&actor_api_url(), &actor_id, &trigger_id) {
            eprintln!("radar actor runtime: automatic actor {actor_id} failed: {error}");
        }

        clear_actor_inflight(&runtime, &actor_id);
        apply_actor_cooldown(&runtime, &actor_id, debounce_seconds);
    });
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

fn clear_actor_inflight(runtime: &ActorRuntimeInner, actor_id: &str) {
    if let Ok(mut inflight) = runtime.inflight_actors.lock() {
        inflight.remove(actor_id);
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

fn apply_actor_cooldown(
    runtime: &ActorRuntimeInner,
    actor_id: &str,
    debounce_seconds: Option<u64>,
) {
    let seconds = debounce_seconds
        .unwrap_or(DEFAULT_ACTOR_SUGGESTION_COOLDOWN_SECONDS)
        .max(1);

    if let Ok(mut cooldowns) = runtime.cooldown_until.lock() {
        cooldowns.insert(
            actor_id.to_string(),
            Instant::now() + Duration::from_secs(seconds),
        );
    }
}
