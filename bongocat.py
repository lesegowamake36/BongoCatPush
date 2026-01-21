"""Bongo Cat Overlay

Usage:
    bongocat.py [options]
    bongocat.py -h | --help

Options:
    -h --help                    Show this screen
    -s --scale=<n>               Scale factor (0-1) [default: 1.0]
    -r --rotate=<deg>            Rotation in degrees (-360 to 360) [default: 0]
    -p --counter-position=<pos>  Position of the counter (top|bottom) [default: bottom]
"""

import sys, os

# Suppress Qt DBus warnings when running as root/sudo
os.environ["QT_LOGGING_RULES"] = "qt.qpa.theme.dbus=false;qt.qpa.theme.gnome=false"

import itertools
import random
import queue
import time
import sqlite3
import threading
from PyQt6.QtWidgets import QApplication, QLabel, QWidget, QMenu, QInputDialog
from PyQt6.QtGui import QPixmap, QImage, QPainter, QColor, QTransform, QAction
from PyQt6.QtCore import Qt, QPoint, pyqtSignal, QObject, QTimer

__all__ = ()

class BongoCatWindow(QWidget):
    def __init__(self, neutral_pixmap, responses_pixmaps, scale, rotate, counter_pos):
        super().__init__()
        
        # Store original pixmaps for re-scaling/rotating in settings
        self.original_neutral = neutral_pixmap
        self.original_responses = responses_pixmaps

        # Determine the database path
        home = os.path.expanduser("~")
        if os.environ.get("SUDO_USER") and os.name == 'posix':
            try:
                import pwd
                home = pwd.getpwnam(os.environ.get("SUDO_USER")).pw_dir
            except (ImportError, KeyError):
                pass
        
        self.db_path = os.path.join(home, ".bongocat_stats.db")
        self.init_db()
        
        # Initial values
        self.scale_factor = scale
        self.rotation = rotate
        self.counter_pos = counter_pos
        
        self.active_keys = set()
        self.active_mouse = set()
        self.kb_mapping = {}
        self.click_count = self.load_clicks()
        
        # Pre-process pixmaps
        self.neutral = self.process_pixmap(self.original_neutral)
        self.responses = {name: self.process_pixmap(pm) for name, pm in self.original_responses.items()}
        
        # Dimensions
        all_pixmaps = [self.neutral] + list(self.responses.values())
        self.max_w = max(p.width() for p in all_pixmaps if p)
        self.max_h = max(p.height() for p in all_pixmaps if p)
        self._current_ww, self._current_wh = 0, 0

        self.is_mirrored = False
        self.next_mirror_at = self.click_count + random.randint(5, 10)
        self.last_press_time = 0
        self.alternator = itertools.cycle([self.responses.get('r', self.neutral), self.responses.get('l', self.neutral)])
        
        self.initUI()
        
    def init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS stats (id INTEGER PRIMARY KEY, clicks INTEGER)")
            for column, default in [("x", 100), ("y", 100), ("scale", 1.0), ("rotate", 0.0), ("pos", "'bottom'")]:
                try:
                    conn.execute(f"ALTER TABLE stats ADD COLUMN {column} {type(default).__name__} DEFAULT {default}")
                except sqlite3.OperationalError:
                    pass
            conn.execute("INSERT OR IGNORE INTO stats (id, clicks, x, y, scale, rotate, pos) VALUES (1, 0, 100, 100, 1.0, 0.0, 'bottom')")

    def load_clicks(self):
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.execute("SELECT clicks, x, y, scale, rotate, pos FROM stats WHERE id = 1")
                row = cursor.fetchone()
                if row:
                    self.saved_x = row[1]
                    self.saved_y = row[2]
                    self.scale_factor = row[3]
                    self.rotation = row[4]
                    self.counter_pos = row[5]
                    return row[0]
                return 0
        except:
            self.saved_x, self.saved_y = 100, 100
            return 0

    def save_stats(self, force=False):
        if not force:
            return
        try:
            pos = self.pos()
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("UPDATE stats SET clicks = ?, x = ?, y = ?, scale = ?, rotate = ?, pos = ? WHERE id = 1", 
                             (self.click_count, pos.x(), pos.y(), float(self.scale_factor), float(self.rotation), str(self.counter_pos)))
                conn.commit()
        except Exception as e:
            print(f"Error saving stats: {e}")

    def process_pixmap(self, pixmap):
        if pixmap is None: return None
        transform = QTransform()
        if self.scale_factor > 1.0: self.scale_factor = 1.0
        if self.scale_factor < 0: self.scale_factor = 0
        if self.scale_factor != 1.0: transform.scale(self.scale_factor, self.scale_factor)
        if self.rotation != 0: transform.rotate(self.rotation)
        return pixmap.transformed(transform, Qt.TransformationMode.SmoothTransformation)

    def initUI(self):
        flags = Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool
        if os.name == 'posix': flags |= Qt.WindowType.X11BypassWindowManagerHint
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        
        self.image_label = QLabel(self)
        self.image_label.setPixmap(self.neutral)
        
        self.counter_label = QLabel(str(self.click_count), self)
        self.counter_label.setStyleSheet(f"""
            color: #333333; font-family: Helvetica; font-size: {int(40 * self.scale_factor)}pt; 
            font-weight: bold; padding: 1px 3px; background-color: white;
            border: 2px solid #333333; border-radius: 5%;
        """)
        self.counter_label.adjustSize()
        self.update_layout()
        if hasattr(self, 'saved_x'): self.move(self.saved_x, self.saved_y)
        self.show()

    def update_layout(self):
        img = self.image_label.pixmap()
        iw, ih = img.width(), img.height()
        self.counter_label.adjustSize()
        cw, ch = self.counter_label.width(), self.counter_label.height()
        ww = int(max(self.max_w, cw))
        rot_norm = abs(self.rotation % 360)
        is_upside_down = rot_norm > 90 and rot_norm < 270
        
        if self.counter_pos == 'top':
            overlap = int((90 if is_upside_down else 40) * self.scale_factor)
        else:
            overlap = int((40 if is_upside_down else 90) * self.scale_factor)
            
        wh = int(self.max_h + ch - overlap)
        
        buffer = 5
        if ww + buffer != self._current_ww or wh + buffer != self._current_wh:
            self._current_ww, self._current_wh = ww + buffer, wh + buffer
            self.setFixedSize(self._current_ww, self._current_wh)
        
        self.image_label.resize(int(iw), int(ih))
        
        if self.counter_pos == 'top':
            self.counter_label.move((ww - cw) // 2, 0)
            self.image_label.move((ww - iw) // 2, ch - overlap)
            self.counter_label.raise_()
        else:
            self.image_label.move((ww - iw) // 2, 0)
            self.counter_label.move((ww - cw) // 2, self.max_h - overlap)
            self.image_label.raise_()            

    def update_display(self):
        img = self.neutral
        
        # Logic to choose image
        if self.active_mouse:
            if 'left' in self.active_mouse:
                img = self.responses.get('l', self.neutral)
            elif 'right' in self.active_mouse:
                img = self.responses.get('r', self.neutral)
        elif self.active_keys:
            last_key = list(self.active_keys)[-1]
            img = self.kb_mapping.get(last_key, self.neutral)
            
        if self.click_count >= self.next_mirror_at:
            self.is_mirrored = not self.is_mirrored
            self.next_mirror_at = self.click_count + random.randint(5, 10)

        if self.is_mirrored:
            mirrored_image = img.toImage().mirrored(True, False)
            img = QPixmap.fromImage(mirrored_image)

        self.image_label.setPixmap(img)
        self.counter_label.setText(str(self.click_count))
        self.update_layout()
            
    def contextMenuEvent(self, event):
        menu = QMenu(self)
        settings_menu = menu.addMenu("Settings")
        
        scale_action = QAction("Scale", self)
        scale_action.triggered.connect(self.set_scale)
        settings_menu.addAction(scale_action)
        
        rotate_action = QAction("Rotate", self)
        rotate_action.triggered.connect(self.set_rotate)
        settings_menu.addAction(rotate_action)
        
        pos_action = QAction("Counter Position", self)
        pos_action.triggered.connect(self.set_counter_pos)
        settings_menu.addAction(pos_action)

        menu.addSeparator()
        fix_action = QAction("Fix device identification", self)
        fix_action.triggered.connect(self.fix_devices)
        exit_action = QAction("Exit", self)
        exit_action.triggered.connect(QApplication.instance().quit)
        
        menu.addAction(fix_action)
        menu.addSeparator()
        menu.addAction(exit_action)
        menu.exec(event.globalPos())

    def set_scale(self):
        val, ok = QInputDialog.getDouble(self, "Scale", "Factor (0.1 - 1.0):", self.scale_factor, 0.1, 1.0, 2, Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.X11BypassWindowManagerHint)
        if ok:
            self.scale_factor = val
            self.reinit_pixmaps()
            self.update_layout()
            self.update_display()
            self.save_stats(force=True)

    def set_rotate(self):
        val, ok = QInputDialog.getInt(self, "Rotate", "Degrees (-360 - 360):", int(self.rotation), -360, 360, 1, Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.X11BypassWindowManagerHint)
        if ok:
            self.rotation = float(val)
            self.reinit_pixmaps()
            self.update_layout()
            self.update_display()
            self.save_stats(force=True)

    def set_counter_pos(self):
        items = ["top", "bottom"]
        val, ok = QInputDialog.getItem(self, "Counter Position", "Select position:", items, items.index(self.counter_pos), False, Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.X11BypassWindowManagerHint)
        if ok:
            self.counter_pos = val.lower()
            self.update_layout()
            self.show()
            self.update_display()
            self.save_stats(force=True)

    def reinit_pixmaps(self):
        if hasattr(self, 'original_neutral'):
            self.neutral = self.process_pixmap(self.original_neutral)
            self.responses = {name: self.process_pixmap(pm) for name, pm in self.original_responses.items()}
            all_pixmaps = [self.neutral] + list(self.responses.values())
            self.max_w = max(p.width() for p in all_pixmaps if p)
            self.max_h = max(p.height() for p in all_pixmaps if p)
            self.alternator = itertools.cycle([self.responses.get('r', self.neutral), self.responses.get('l', self.neutral)])
            self.counter_label.setStyleSheet(f"""
                color: #333333; font-family: Helvetica; font-size: {int(40 * self.scale_factor)}pt; 
                font-weight: bold; padding: 1px 3px; background-color: white;
                border: 2px solid #333333; border-radius: 5%;
            """)

    def fix_devices(self):
        if hasattr(self, 'rehook_callback'): self.rehook_callback()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.MouseButton.LeftButton:
            new_pos = event.globalPosition().toPoint() - self.drag_pos
            self.move(new_pos)
            self.save_stats(force=True)
            event.accept()

def load_assets(default, path):
    if hasattr(sys, '_MEIPASS'): base_path = sys._MEIPASS
    else:
        if os.name == 'posix' and os.path.exists('/opt/bongocat') and not os.path.exists(os.path.join(os.getcwd(), path)):
            base_path = '/opt/bongocat'
        else:
            base_path = os.getcwd()

    directory = os.path.join(base_path, path)
    responses = {}
    neutral = None
    extensions = ('.png', '.jpg', '.jpeg')
    
    if not os.path.exists(directory): raise FileNotFoundError(f"Assets directory not found: {directory}")

    for entry in os.scandir(directory):
        if not entry.is_file(): continue
        name, ext = os.path.splitext(entry.name)
        if ext.lower() not in extensions: continue
        pixmap = QPixmap(entry.path)
        if name.lower() == default.lower(): neutral = pixmap
        else: responses[name.lower()] = pixmap
            
    if neutral is None: raise ValueError(f"Missing file '{default}' for neutral state in {directory}.")
    return neutral, responses

def start():
    def handle_thread_exception(args):
        if isinstance(args.exc_value, OSError) and args.exc_value.errno == 19: print("Device disconnected.")
        else: sys.__excepthook__(args.exc_type, args.exc_value, args.exc_traceback)
    threading.excepthook = handle_thread_exception

    import docopt
    if os.name == 'posix' and os.geteuid() != 0:
        print("Error: Root required on Linux.")
        sys.exit(1)

    import keyboard
    import mouse

    arguments = docopt.docopt(__doc__)
    app = QApplication(sys.argv)
    
    try: scale = float(arguments['--scale'] or 1.0)
    except: scale = 1.0
    try: rotate = float(arguments['--rotate'] or 0.0)
    except: rotate = 0.0
    counter_pos = (arguments['--counter-position'] or 'bottom').lower()
    
    neutral, responses = load_assets('idle', 'images/kb-mouse')
    window = BongoCatWindow(neutral, responses, scale, rotate, counter_pos)
    
    # Load overrides from DB if CLI args were default
    try:
        with sqlite3.connect(window.db_path) as conn:
            row = conn.execute("SELECT scale, rotate, pos FROM stats WHERE id = 1").fetchone()
            if row:
                if arguments['--scale'] is None: window.scale_factor = float(row[0])
                if arguments['--rotate'] is None: window.rotation = float(row[1])
                if arguments['--counter-position'] is None: window.counter_pos = str(row[2])
                window.reinit_pixmaps()
                window.update_layout()
    except: pass

    event_queue = queue.Queue()

    def on_key(event):
        if event.name == 'f4' and keyboard.is_pressed('shift'):
            QTimer.singleShot(0, app.quit)
            return
        # DEBUG
        if event.event_type == 'down':
            print(f"[DEBUG] Keyboard Down: {event.name}")
            event_queue.put(('key_down', event.name))
        else:
            event_queue.put(('key_up', event.name))

    def on_mouse(event):
        if type(event) is not mouse.ButtonEvent: return
        # DEBUG
        if event.event_type == 'down':
            print(f"[DEBUG] Mouse Clicked: {event.button}")
            event_queue.put(('mouse_down', event.button))
        else:
            print(f"[DEBUG] Mouse Released: {event.button}")
            event_queue.put(('mouse_up', event.button))

    def rehook():
        print("Restarting...")
        window.save_stats(force=True)
        import subprocess
        subprocess.Popen([sys.executable] + sys.argv)
        app.quit()

    window.rehook_callback = rehook
    
    def process_queue():
        current_time = time.time()
        
        if event_queue.qsize() > 20:
            print("[DEBUG] Queue overflow, clearing...")
            with event_queue.mutex:
                event_queue.queue.clear()
            return

        try:
            
            etype, data = event_queue.get_nowait()
            
            needs_update = False
            
            if etype == 'key_down':
                if data not in window.active_keys:
                    window.active_keys.add(data)
                    window.kb_mapping[data] = next(window.alternator)
                    window.click_count += 1
                    needs_update = True
            
            elif etype == 'mouse_down':
                print(f"[DEBUG] Processing Mouse Down: {data}")
                window.click_count += 1
                if os.name == 'nt' or data not in window.active_mouse:
                    window.active_mouse.add(data)
                needs_update = True
            
            elif etype == 'key_up':
                if data in window.active_keys:
                    window.active_keys.discard(data)
                    needs_update = True
            
            elif etype == 'mouse_up':
                print(f"[DEBUG] Processing Mouse Up: {data}")
                if data in window.active_mouse:
                    window.active_mouse.discard(data)
                    needs_update = True

            if needs_update:
                window.last_press_time = current_time
                window.update_display()
                return

        except queue.Empty:
            pass

        if window.active_keys or window.active_mouse:
            if current_time - window.last_press_time > (0.02 if os.name == 'nt' else 0.05):
                pass

    def watchdog():
        changed = False
        if window.active_keys:
            valid_keys = set()
            for k in window.active_keys:
                try:
                    if k != 'unknown' and keyboard.is_pressed(k):
                        valid_keys.add(k)
                except: pass
            if valid_keys != window.active_keys:
                window.active_keys = valid_keys
                changed = True
        
        if window.active_mouse:
            if time.time() - window.last_press_time > 1.0:
                print("[DEBUG] Watchdog resetting mouse")
                window.active_mouse.clear()
                changed = True

        if changed:
            window.update_display()

    timer = QTimer()
    timer.timeout.connect(process_queue)
    timer.start(1)

    wd_timer = QTimer()
    wd_timer.timeout.connect(watchdog)
    wd_timer.start(1000)

    try:
        keyboard.hook(on_key)
        mouse.hook(on_mouse)
    except Exception as e:
        print(f"Error hooking devices: {e}")

    sys.exit(app.exec())

if __name__ == '__main__':
    start()