use serde::Serialize;
use std::os::windows::process::CommandExt;
use std::process::{Child, Command};
use std::sync::Mutex;
use tauri::{Manager, State};

/// Windows CREATE_NO_WINDOW flag — prevents console window from appearing.
const CREATE_NO_WINDOW: u32 = 0x08000000;

// ── Python server state ──────────────────────────────────────────────

/// Wrapper that kills the child process when dropped, preventing orphan processes.
struct ManagedChild(Option<Child>);

impl Drop for ManagedChild {
    fn drop(&mut self) {
        if let Some(ref mut child) = self.0 {
            let _ = child.kill();
            let _ = child.wait();
        }
    }
}

struct PythonServer {
    child: Mutex<Option<ManagedChild>>,
}

#[derive(Serialize, Clone)]
struct ServerStatus {
    running: bool,
    url: String,
}

// ── Tauri commands (callable from JS frontend) ───────────────────────

#[tauri::command]
fn get_server_status(state: State<PythonServer>) -> ServerStatus {
    let running = state
        .child
        .lock()
        .ok()
        .map(|guard| guard.is_some())
        .unwrap_or(false);

    ServerStatus {
        running,
        url: "http://127.0.0.1:8765".into(),
    }
}

// ── Python server lifecycle ─────────────────────────────────────────

/// Kill any process currently listening on port 8765 (stale orphan from previous run).
fn kill_port_8765() {
    if let Ok(output) = Command::new("netstat")
        .args(["-ano"])
        .creation_flags(CREATE_NO_WINDOW)
        .output()
    {
        let stdout = String::from_utf8_lossy(&output.stdout);
        for line in stdout.lines() {
            if line.contains(":8765") && line.contains("LISTENING") {
                let parts: Vec<&str> = line.split_whitespace().collect();
                if let Some(pid_str) = parts.last() {
                    let _ = Command::new("taskkill")
                        .args(["/F", "/PID", pid_str])
                        .creation_flags(CREATE_NO_WINDOW)
                        .stdout(std::process::Stdio::null())
                        .stderr(std::process::Stdio::null())
                        .spawn();
                }
            }
        }
    }
}

fn find_code_root() -> std::path::PathBuf {
    // Try AINOVEL_CODE_ROOT env var first
    if let Ok(root) = std::env::var("AINOVEL_CODE_ROOT") {
        let p = std::path::PathBuf::from(&root);
        if p.join("dashboard").join("server.py").exists() {
            return p;
        }
    }

    // Fallback: navigate up from the exe to find ainovel-write/dashboard/
    // exe is at: ainovel-write/dashboard/frontend/src-tauri/target/release/
    if let Ok(exe) = std::env::current_exe() {
        let mut path = exe.clone();
        // Walk up 6 levels: exe → release → target → src-tauri → frontend → dashboard → ainovel-write
        for _ in 0..6 {
            match path.parent() {
                Some(p) => path = p.to_path_buf(),
                None => break,
            }
        }
        if path.join("dashboard").join("server.py").exists() {
            return path;
        }
    }

    // Last resort: CWD
    std::env::current_dir().unwrap_or_default()
}

fn resolve_project_root() -> Option<String> {
    // CLI arg-like: read from env
    if let Ok(root) = std::env::var("AINOVEL_PROJECT_ROOT") {
        return Some(root);
    }
    // Read pointer file
    if let Ok(cwd) = std::env::current_dir() {
        let pointer = cwd.join(".claude").join(".ainovel-current-project");
        if pointer.is_file() {
            if let Ok(target) = std::fs::read_to_string(&pointer) {
                let p = std::path::PathBuf::from(target.trim());
                if p.join(".ainovel").join("state.json").exists() {
                    return Some(p.to_string_lossy().to_string());
                }
            }
        }
        // Try walking up to find pointer file
        let mut dir = cwd.clone();
        loop {
            let pointer = dir.join(".claude").join(".ainovel-current-project");
            if pointer.is_file() {
                if let Ok(target) = std::fs::read_to_string(&pointer) {
                    let p = std::path::PathBuf::from(target.trim());
                    if p.join(".ainovel").join("state.json").exists() {
                        return Some(p.to_string_lossy().to_string());
                    }
                }
            }
            if dir.join(".ainovel").join("state.json").exists() {
                return Some(dir.to_string_lossy().to_string());
            }
            dir = match dir.parent() {
                Some(p) => p.to_path_buf(),
                None => break,
            };
        }
    }
    None
}

/// Get the path to the Python path cache file.
fn python_cache_path() -> std::path::PathBuf {
    let home = std::env::var("USERPROFILE")
        .or_else(|_| std::env::var("HOME"))
        .unwrap_or_default();
    std::path::PathBuf::from(home)
        .join(".claude")
        .join("ainovel-write")
        .join("python-path.txt")
}

fn find_python() -> String {
    // Phase 0 optimization: try cached path first (avoids disk scan)
    let cache = python_cache_path();
    if cache.is_file() {
        if let Ok(cached) = std::fs::read_to_string(&cache) {
            let cached = cached.trim().to_string();
            if !cached.is_empty() {
                if let Ok(output) = Command::new(&cached)
                    .arg("--version")
                    .creation_flags(CREATE_NO_WINDOW)
                    .output()
                {
                    if output.status.success() {
                        return cached;
                    }
                }
            }
        }
    }

    // Fallback: search common install locations
    let candidates: Vec<String> = {
        let mut v = vec![
            "python".to_string(),
            "python3".to_string(),
        ];
        for base in &[
            r"C:\Users\24357\AppData\Local\Programs\Python",
            r"C:\Python",
            r"C:\Program Files\Python",
        ] {
            if let Ok(entries) = std::fs::read_dir(base) {
                for entry in entries.flatten() {
                    let exe = entry.path().join("python.exe");
                    if exe.is_file() {
                        v.push(exe.to_string_lossy().to_string());
                    }
                }
            }
        }
        v
    };
    for cand in &candidates {
        if let Ok(output) = Command::new(cand)
            .arg("--version")
            .creation_flags(CREATE_NO_WINDOW)
            .output()
        {
            if output.status.success() {
                // Cache the found path for next startup
                let _ = std::fs::create_dir_all(cache.parent().unwrap_or(&std::path::PathBuf::from(".")));
                let _ = std::fs::write(&cache, cand);
                return cand.clone();
            }
        }
    }
    "python".to_string()
}

fn start_python_server() -> Option<ManagedChild> {
    // Phase 0: If a healthy backend is already running, reuse it (no kill, no restart).
    // This supports external pre-starting (e.g. by launcher or background service).
    if is_port_healthy("http://127.0.0.1:8765/api/story-runtime/health") {
        return Some(ManagedChild(None));
    }

    // Kill any stale server from a previous run
    kill_port_8765();

    let python_cmd = find_python();
    let code_root = find_code_root();
    let project_root = resolve_project_root().unwrap_or_default();

    let mut child = Command::new(&python_cmd)
        .args([
            "-m",
            "dashboard.server",
            "--host",
            "127.0.0.1",
            "--port",
            "8765",
            "--no-browser",
            "--project-root",
            &project_root,
        ])
        .current_dir(&code_root)
        .creation_flags(CREATE_NO_WINDOW)
        .spawn()
        .ok()?;

    // Wait for the server to be ready (poll health endpoint).
    // If health check fails, kill the child and return None so the caller
    // knows the backend didn't start.
    let healthy = wait_for_health("http://127.0.0.1:8765/api/story-runtime/health", 15);
    if !healthy {
        let _ = child.kill();
        let _ = child.wait();
        return None;
    }

    Some(ManagedChild(Some(child)))
}

/// Quick check: is any backend already listening? (1s timeout).
/// Accepts any HTTP response (even 5xx) — if we got a response, the server is running.
fn is_port_healthy(url: &str) -> bool {
    let client = reqwest::blocking::Client::new();
    client
        .get(url)
        .timeout(std::time::Duration::from_secs(1))
        .send()
        .is_ok()  // any response = server is there
}

fn wait_for_health(url: &str, timeout_secs: u64) -> bool {
    let client = reqwest::blocking::Client::new();
    let start = std::time::Instant::now();

    while start.elapsed().as_secs() < timeout_secs {
        match client.get(url).timeout(std::time::Duration::from_secs(2)).send() {
            Ok(_resp) => return true,  // any response = server is running
            // Phase 0: 200ms poll for faster response (was 500ms)
            _ => std::thread::sleep(std::time::Duration::from_millis(200)),
        }
    }
    false
}

fn stop_python_server(state: &PythonServer) {
    if let Ok(mut guard) = state.child.lock() {
        *guard = None;
    }
}

// ── App setup ───────────────────────────────────────────────────────

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .setup(|app| {
            // Start Python backend SYNCHRONOUSLY — wait until it's healthy
            // before creating the window. This way the WebView never sees
            // a "page cannot be accessed" error because the backend is
            // already serving the SPA when the first navigation happens.
            // With lazy imports, Python cold-start is now ~2-5s (was ~30s).
            let child = start_python_server();

            app.manage(PythonServer {
                child: Mutex::new(child),
            });

            // Create the main window AFTER backend is ready
            let frontend_url: tauri::Url = "http://127.0.0.1:8765/index.html"
                .parse()
                .expect("invalid frontend URL");
            use tauri::WebviewWindowBuilder;
            let _main_window = WebviewWindowBuilder::new(
                app,
                "main",
                tauri::WebviewUrl::External(frontend_url),
            )
            .title("AInovel Harness")
            .inner_size(1280.0, 860.0)
            .min_inner_size(900.0, 600.0)
            .resizable(true)
            .maximized(true)   // 最大化启动（保留标题栏 - □ × 控制条；fullscreen 会隐藏控制条，用户 2026-08-09 反馈）
            .center()
            .build()?;

            // Set up system tray
            #[cfg(desktop)]
            {
                use tauri::menu::{MenuBuilder, MenuItemBuilder};
                use tauri::tray::TrayIconBuilder;

                let quit = MenuItemBuilder::with_id("quit", "退出").build(app)?;
                let toggle = MenuItemBuilder::with_id("toggle_server", "重启服务").build(app)?;
                let menu = MenuBuilder::new(app)
                    .item(&toggle)
                    .separator()
                    .item(&quit)
                    .build()?;

                let _tray = TrayIconBuilder::new()
                    .tooltip("AInovel Harness")
                    .menu(&menu)
                    .on_menu_event(|app, event| match event.id().as_ref() {
                        "quit" => {
                            let state = app.state::<PythonServer>();
                            stop_python_server(&state);
                            app.exit(0);
                        }
                        "toggle_server" => {
                            let state = app.state::<PythonServer>();
                            stop_python_server(&state);
                            let new_child = start_python_server();
                            if let Ok(mut guard) = state.child.lock() {
                                *guard = new_child;
                            };
                        }
                        _ => {}
                    })
                    .build(app)?;
            }

            Ok(())
        })
        .on_window_event(|window, event| {
            match event {
                tauri::WindowEvent::CloseRequested { .. } | tauri::WindowEvent::Destroyed => {
                    let state = window.state::<PythonServer>();
                    stop_python_server(&state);
                }
                _ => {}
            }
        })
        .invoke_handler(tauri::generate_handler![get_server_status])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
