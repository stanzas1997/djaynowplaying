# Nowplaying Monitor for djay by Algoriddim
# Author: stanzas
# Added: 2025-12-31
# copyright (c) 2025-2026 stanzas
# License: MIT
# version 1.1
import sqlite3
import time
import os
import re
import datetime
import json
import threading
import socket
import sys
import webbrowser
import urllib.parse
import shutil
import tkinter as tk
from tkinter import scrolledtext, filedialog, messagebox
from http.server import HTTPServer, BaseHTTPRequestHandler
import appdirs
from pathlib import Path

# Try to import mutagen for artwork extraction
try:
    import mutagen
    HAS_MUTAGEN = True
except ImportError:
    HAS_MUTAGEN = False

# ================= Configuration =================
CONFIG_FILE = "config.json"
DEFAULT_CONFIG = {
    "db_path": None,
    "poll_interval": 0.5,
    "port": 8000,
    "template_file": "template.html",
    "show_source": True,
    "show_history": True,
    "show_history_time": True
}
# ===========================================

# Global State
server_state = {
    "current": {
        "artist": "-",
        "title": "Waiting for playback...",
        "status": "Ready",
        "timestamp": "",
        "type": "info", # info, playing, preview
        "has_artwork": False,
        "artwork_ts": 0
    },
    "history": []
}

# Global Config
current_config = DEFAULT_CONFIG.copy()

def get_app_dir():
    """Get the directory where the application is running"""
    config_dir=appdirs.user_config_dir("DjayNowplaying")
    Path(config_dir).mkdir(parents=True, exist_ok=True)
    return config_dir
    #if getattr(sys, 'frozen', False):
    #    return os.path.dirname(sys.executable)
    #return os.path.dirname(os.path.abspath(__file__))
    
    

def get_template_content():
    """Read HTML template, use default if not found"""
    base_dir = get_app_dir()
    template_file = current_config.get("template_file", "template.html")
    #template_path = os.path.join(base_dir, template_file)
    template_path = Path(base_dir,template_file)
    if not template_path.exists():
        default_file=Path(Path(__file__).resolve().parent,"template.html")
        shutil.copy(default_file,base_dir)
    try:
        with open(template_path, 'r', encoding='utf-8') as f:
            return f.read()
    except FileNotFoundError:
        return """<!DOCTYPE html><html><body><h1>Template not found</h1></body></html>"""

def load_config():
    """Load configuration from JSON file"""
    global current_config
    config_path = os.path.join(get_app_dir(), CONFIG_FILE)

    loaded_config = DEFAULT_CONFIG.copy()
    
    if os.path.exists(config_path):
        try:
            with open(config_path, 'r') as f:
                file_config = json.load(f)
                loaded_config.update(file_config)
        except:
            pass
    else:
        # Create default config file if it doesn't exist
        save_config(loaded_config)
        
    current_config = loaded_config
    return current_config

def save_config(config_data):
    """Save configuration to JSON file"""
    config_path = os.path.join(get_app_dir(), CONFIG_FILE)
    try:
        with open(config_path, 'w') as f:
            json.dump(config_data, f, indent=4)
    except:
        pass

def update_config(key, value):
    """Update a single config value and save"""
    global current_config
    current_config[key] = value
    save_config(current_config)

def find_available_port(start_port):
    """Find an available port"""
    port = start_port
    while port < 65535:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(('localhost', port)) != 0:
                return port
            port += 1
    return start_port

class ArtworkManager:
    def __init__(self, db_path):
        self.db_path = db_path
        self.path_cache = {} # (artist, title) -> file_path
        self.load_paths()
        
    def load_paths(self):
        try:
            conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True, timeout=1.0)
            cursor = conn.cursor()
            cursor.execute("SELECT data FROM database2 WHERE collection='localMediaItemLocations'")
            rows = cursor.fetchall()
            conn.close()
            
            count = 0
            for row in rows:
                res = self.parse_blob_for_path(row[0])
                if res:
                    artist, title, path = res
                    # Normalize keys
                    key = (artist.strip(), title.strip())
                    self.path_cache[key] = path
                    count += 1
            return count
        except Exception as e:
            print(f"Error loading paths: {e}")
            return 0

    def parse_blob_for_path(self, blob_data):
        try:
            # Extract all strings
            strings = re.findall(b'[a-zA-Z0-9\s_\.\-\(\)\&\'\,\[\]\!\?\\\\\/\:\%\+]+', blob_data)
            decoded = []
            for s in strings:
                try:
                    d = s.decode('utf-8', errors='ignore').strip()
                    if len(d) > 1: decoded.append(d)
                except: pass
            
            title = None
            artist = None
            file_path = None
            
            # Heuristic: Value comes before Key in this specific binary format
            for i, s in enumerate(decoded):
                if s == 'title' and i > 0:
                    title = decoded[i-1]
                elif s == 'artist' and i > 0:
                    artist = decoded[i-1]
                elif s.startswith('file:///') and file_path == None:  #make sure that only one will be taken
                    file_path = s
            
            if title and artist and file_path:
                # Decode URL
                path = urllib.parse.unquote(file_path)
                if path.startswith('file:///'):
                    if sys.platform=="win32": #for windows users 
                        path = path[8:] # Remove file:///
                        path = path.replace('/', '\\')
                    else:
                        path = path[7:] # Remove file://
                return (artist, title, path)
        except:
            pass
        return None

    def extract_artwork(self, artist, title):
        if not HAS_MUTAGEN:
            return False
            
        key = (artist.strip(), title.strip())
        path = self.path_cache.get(key)
        
        if not path or not os.path.exists(path):
            # Try refreshing cache once if not found
            self.load_paths()
            path = self.path_cache.get(key)
            if not path or not os.path.exists(path):
                return False
        
        try:
            f = mutagen.File(path)
            if not f: return False
            
            artwork_data = None
            
            # ID3 (MP3)
            if hasattr(f, 'tags') and f.tags:
                for tag in f.tags.values():
                    if hasattr(tag, 'data') and hasattr(tag, 'mime'):
                        if tag.mime.startswith('image/'):
                            artwork_data = tag.data
                            break
            
            # FLAC / Vorbis
            if not artwork_data and hasattr(f, 'pictures'):
                if f.pictures:
                    artwork_data = f.pictures[0].data
            
            if artwork_data:
                cover_path = os.path.join(get_app_dir(), 'current_cover.jpg')
                with open(cover_path, 'wb') as img:
                    img.write(artwork_data)
                return True
                
        except Exception as e:
            print(f"Artwork extraction error: {e}")
            
        return False

class RequestHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/':
            self.send_response(200)
            self.send_header('Content-type', 'text/html; charset=utf-8')
            self.end_headers()
            content = get_template_content()
            self.wfile.write(content.encode('utf-8'))
        elif self.path == '/api/now_playing':
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            
            # Construct response with settings
            response = server_state.copy()
            response['settings'] = {
                'show_history': current_config.get('show_history', True),
                'show_history_time': current_config.get('show_history_time', True),
                'show_source': current_config.get('show_source', True)
            }
            self.wfile.write(json.dumps(response).encode('utf-8'))
        elif self.path.startswith('/cover.jpg'):
            cover_path = os.path.join(get_app_dir(), 'current_cover.jpg')
            if os.path.exists(cover_path):
                self.send_response(200)
                self.send_header('Content-type', 'image/jpeg')
                self.end_headers()
                with open(cover_path, 'rb') as f:
                    self.wfile.write(f.read())
            else:
                self.send_error(404)
        else:
            self.send_error(404)
    
    def log_message(self, format, *args):
        pass # Disable HTTP request logging

class PlaybackMonitor(threading.Thread):
    def __init__(self, db_path, log_callback):
        super().__init__()
        self.daemon = True
        self.db_path = db_path
        self.log_callback = log_callback
        self.artwork_manager = ArtworkManager(db_path)
        self.last_snapshot = {}
        self.recent_tracks = []
        self.target_collections = [
            'historySessionItems'
        ]
        self.poll_interval = current_config.get("poll_interval", 0.5)

    def parse_blob(self, blob_data):
        try:
            strings = re.findall(b'[a-zA-Z0-9\s_\.\-\(\)\&\'\,\[\]\!\?]+', blob_data)
            decoded = []
            for s in strings:
                try:
                    d = s.decode('utf-8', errors='ignore').strip()
                    if len(d) > 1: decoded.append(d)
                except: pass
            
            title = "Unknown"
            artist = "Unknown"
            source = "Unknown"
            
            # Heuristic: Value comes before Key
            for i, s in enumerate(decoded):
                if s == 'title' and i > 0:
                    title = decoded[i-1]
                elif s == 'artist' and i > 0:
                    artist = decoded[i-1]
                elif s == 'originSourceID' and i > 0:
                    source = decoded[i-1]
                
            if title == "Unknown" and artist == "Unknown": return None
            return {"artist": artist, "title": title, "source": source}
        except:
            return None

    def get_snapshot(self):
        snapshot = {}
        try:
            conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True, timeout=1.0)
            cursor = conn.cursor()
            placeholders = ','.join(['?'] * len(self.target_collections))
            query = f"SELECT rowid, collection, data FROM database2 WHERE collection IN ({placeholders})"
            cursor.execute(query, self.target_collections)
            rows = cursor.fetchall()
            conn.close()
            for r in rows:
                snapshot[r[0]] = {'collection': r[1], 'data': r[2]}
            return snapshot, None
        except Exception as e:
            return None, str(e)

    def is_duplicate(self, track_str):
        now = time.time()
        self.recent_tracks = [t for t in self.recent_tracks if now - t[1] < 5.0]
        for track, timestamp in self.recent_tracks:
            if track == track_str: return True
        self.recent_tracks.append((track_str, now))
        return False

    def run(self):
        self.log_callback("Monitor thread started...")
        self.log_callback(f"Loaded {len(self.artwork_manager.path_cache)} file paths for artwork.")
        
        self.last_snapshot, _ = self.get_snapshot()
        if not self.last_snapshot: self.last_snapshot = {}

        while True:
            time.sleep(self.poll_interval)
            current_snapshot, err = self.get_snapshot()
            if err:
                # self.log_callback(f"DB Error: {err}") # Optional: log errors
                continue
            
            if not current_snapshot: continue

            for rowid, info in current_snapshot.items():
                if rowid not in self.last_snapshot:
                    track_data = self.parse_blob(info['data'])
                    if track_data:
                        track_str = f"{track_data['artist']} - {track_data['title']}"
                        if not self.is_duplicate(track_str):
                            timestamp = datetime.datetime.now().strftime('%H:%M:%S')
                            
                            # Extract Artwork
                            has_artwork = self.artwork_manager.extract_artwork(track_data['artist'], track_data['title'])
                            
                            raw_source = track_data.get('source', 'Unknown')
                            display_source = raw_source
                            
                            # Logic: Hide 'explorer' always.
                            if str(raw_source).lower() == 'explorer':
                                display_source = None
                            else:
                                display_source = raw_source
                            
                            new_track = {
                                "artist": track_data['artist'],
                                "title": track_data['title'],
                                "source": display_source,
                                "status": "Playing",
                                "timestamp": timestamp,
                                "type": "playing",
                                "has_artwork": has_artwork,
                                "artwork_ts": int(time.time())
                            }
                            
                            server_state['current'] = new_track
                            
                            history_item = new_track.copy()
                            server_state['history'].insert(0, history_item)
                            if len(server_state['history']) > 10:
                                server_state['history'].pop()
                                
                            self.log_callback(f"[{timestamp}] Detected: {track_str} (ON AIR) [Source: {raw_source}] [Art: {'Yes' if has_artwork else 'No'}]")

            self.last_snapshot = current_snapshot

class MonitorGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("DjayNowplaying")
        self.root.geometry("600x450")
        
        # Load Config
        load_config()
        
        # Resolve DB Path
        self.db_path = self.resolve_db_path()
        if not self.db_path:
            # If user cancelled or failed to pick, we can't proceed.
            # We schedule destroy to happen after init returns
            self.root.after(100, self.root.destroy)
            return

        # Status Frame
        status_frame = tk.Frame(root, pady=10)
        status_frame.pack(fill=tk.X, padx=10)
        
        # Determine Port
        config_port = current_config.get("port", 8000)
        self.port = find_available_port(config_port)
        self.url = f"http://localhost:{self.port}"
        
        tk.Label(status_frame, text="Server Status:", font=("Arial", 10, "bold")).pack(side=tk.LEFT)
        tk.Label(status_frame, text="Running", fg="green").pack(side=tk.LEFT, padx=5)
        
        # DB Path Display
        db_frame = tk.Frame(root, pady=2)
        db_frame.pack(fill=tk.X, padx=10)
        tk.Label(db_frame, text="Database:", font=("Arial", 9, "bold")).pack(side=tk.LEFT)
        tk.Label(db_frame, text=os.path.basename(self.db_path), fg="#555").pack(side=tk.LEFT, padx=5)
        
        # URL Frame
        url_frame = tk.Frame(root, pady=5)
        url_frame.pack(fill=tk.X, padx=10)
        
        tk.Label(url_frame, text="Monitor URL:").pack(side=tk.LEFT)
        url_link = tk.Label(url_frame, text=self.url, fg="blue", cursor="hand2")
        url_link.pack(side=tk.LEFT, padx=5)
        url_link.bind("<Button-1>", lambda e: webbrowser.open(self.url))
        
        tk.Button(url_frame, text="Settings", command=self.open_settings).pack(side=tk.RIGHT, padx=5)
        tk.Button(url_frame, text="Open Browser", command=lambda: webbrowser.open(self.url)).pack(side=tk.RIGHT)

        # Log Area
        tk.Label(root, text="Activity Log:", anchor="w").pack(fill=tk.X, padx=10, pady=(10,0))
        self.log_area = scrolledtext.ScrolledText(root, height=15)
        self.log_area.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        
        if not HAS_MUTAGEN:
            self.log("Warning: 'mutagen' library not found. Artwork extraction disabled.")
            self.log("Run 'pip install mutagen' to enable artwork support.")
        
        # Start Threads
        self.start_threads()

    def open_settings(self):
        # Reload config to ensure freshness
        load_config()
        
        settings_win = tk.Toplevel(self.root)
        settings_win.title("Settings")
        settings_win.geometry("400x400")
        
        # DB Path
        tk.Label(settings_win, text="Database Path:").pack(anchor="w", padx=10, pady=(10, 0))
        db_frame = tk.Frame(settings_win)
        db_frame.pack(fill=tk.X, padx=10, pady=5)
        
        db_var = tk.StringVar(master=settings_win, value=current_config.get("db_path", ""))
        tk.Entry(db_frame, textvariable=db_var).pack(side=tk.LEFT, fill=tk.X, expand=True)
        
        def browse_db():
            path = filedialog.askopenfilename(
                parent=settings_win,
                title="Select MediaLibrary.db",
                filetypes=[("SQLite Database", "*.db"), ("All Files", "*.*")]
            )
            if path: db_var.set(path)
            
        tk.Button(db_frame, text="Browse", command=browse_db).pack(side=tk.RIGHT, padx=(5, 0))
        
        # Port
        tk.Label(settings_win, text="Web Server Port (Requires Restart):").pack(anchor="w", padx=10, pady=(10, 0))
        port_var = tk.IntVar(master=settings_win, value=current_config.get("port", 8000))
        tk.Entry(settings_win, textvariable=port_var).pack(fill=tk.X, padx=10, pady=5)
        
        # Poll Interval
        tk.Label(settings_win, text="Poll Interval (seconds):").pack(anchor="w", padx=10, pady=(10, 0))
        poll_var = tk.DoubleVar(master=settings_win, value=current_config.get("poll_interval", 0.5))
        tk.Entry(settings_win, textvariable=poll_var).pack(fill=tk.X, padx=10, pady=5)
        
        # Show Source
        show_source_var = tk.BooleanVar(master=settings_win, value=bool(current_config.get("show_source", True)))
        tk.Checkbutton(settings_win, text="Show Streaming Service Name", variable=show_source_var).pack(anchor="w", padx=10, pady=2)

        # Show History
        show_history_var = tk.BooleanVar(master=settings_win, value=bool(current_config.get("show_history", True)))
        tk.Checkbutton(settings_win, text="Show History Section", variable=show_history_var).pack(anchor="w", padx=10, pady=2)

        # Show History Time
        show_history_time_var = tk.BooleanVar(master=settings_win, value=bool(current_config.get("show_history_time", True)))
        tk.Checkbutton(settings_win, text="Show History Timestamps", variable=show_history_time_var).pack(anchor="w", padx=10, pady=2)

        # Save Button
        def save():
            try:
                new_db = db_var.get()
                new_port = port_var.get()
                new_poll = poll_var.get()
                new_show_source = show_source_var.get()
                new_show_history = show_history_var.get()
                new_show_history_time = show_history_time_var.get()
                
                if not new_db:
                    messagebox.showerror("Error", "Database path cannot be empty.", parent=settings_win)
                    return

                if not os.path.exists(new_db):
                    messagebox.showerror("Error", "Database file does not exist.", parent=settings_win)
                    return
                
                # Update global config directly and save once
                global current_config
                current_config["db_path"] = new_db
                current_config["port"] = new_port
                current_config["poll_interval"] = new_poll
                current_config["show_source"] = bool(new_show_source)
                current_config["show_history"] = bool(new_show_history)
                current_config["show_history_time"] = bool(new_show_history_time)
                
                save_config(current_config)
                
                # Update runtime values where possible
                if hasattr(self, 'monitor'):
                    self.monitor.poll_interval = new_poll
                
                messagebox.showinfo("Saved", "Settings saved.\nRestart application for Port changes to take effect.", parent=settings_win)
                settings_win.destroy()
            except Exception as e:
                messagebox.showerror("Error", f"Invalid values: {e}", parent=settings_win)

        tk.Button(settings_win, text="Save Settings", command=save, bg="#e1e1e1", height=4).pack(pady=20, fill=tk.X, padx=20)

    def resolve_db_path(self):
        # 1. Check config
        path = current_config.get("db_path")
        if path and os.path.exists(path):
            return path
        
        # 2. Check default location
        user_profile = None
        default_path = None
        if sys.platform=="darwin": #for macOS users 
            user_profile = os.environ.get('HOME')
            default_path = os.path.join(user_profile, 'Music', 'djay', 'djay Media Library.djayMediaLibrary', 'MediaLibrary.db')
        else: 
            user_profile = os.environ.get('USERPROFILE')
            default_path = os.path.join(user_profile, 'Music', 'djay', 'djay Media Library', 'MediaLibrary.db')
        if user_profile:
            if os.path.exists(default_path):
                update_config("db_path", default_path)
                return default_path
        
        # 3. Ask user
        messagebox.showinfo(
            "Database Not Found", 
            "Could not automatically find 'MediaLibrary.db'.\n\n"
            "Please locate it manually.\n"
            "Typical location: Music -> djay -> djay Media Library"
        )
        
        initial_dir = os.path.join(user_profile, 'Music') if user_profile else "/"
        selected_path = filedialog.askopenfilename(
            title="Select MediaLibrary.db",
            filetypes=[("SQLite Database", "*.db"), ("All Files", "*.*")],
            initialdir=initial_dir
        )
        
        if selected_path:
            update_config("db_path", selected_path)
            return selected_path
            
        return None

    def log(self, message):
        self.log_area.insert(tk.END, message + "\n")
        self.log_area.see(tk.END)

    def start_threads(self):
        # Start Monitor
        self.monitor = PlaybackMonitor(self.db_path, self.log_callback_safe)
        self.monitor.start()
        
        # Start Server
        self.server_thread = threading.Thread(target=self.run_server, daemon=True)
        self.server_thread.start()
        
        self.log(f"Server started on port {self.port}")
        self.log(f"Monitoring database: {self.db_path}")

    def log_callback_safe(self, message):
        # Ensure thread safety for GUI updates
        self.root.after(0, lambda: self.log(message))

    def run_server(self):
        server_address = ('', self.port)
        httpd = HTTPServer(server_address, RequestHandler)
        try:
            httpd.serve_forever()
        except Exception as e:
            self.log_callback_safe(f"Server Error: {e}")

def main():
    root = tk.Tk()
    app = MonitorGUI(root)
    root.mainloop()

if __name__ == "__main__":
    main()
