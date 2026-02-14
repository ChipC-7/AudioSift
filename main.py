import sys
import os
import subprocess
import re
from pathlib import Path
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                             QHBoxLayout, QPushButton, QLineEdit, QLabel, 
                             QFileDialog, QComboBox, QTextEdit, QGroupBox, 
                             QProgressBar)
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QFont, QFontDatabase, QPalette, QColor


def clean_input(user_text):
    """清理用户输入"""
    dangerous = "'\"`;|&$(){}[]<>\\"
    return user_text.translate(str.maketrans('', '', dangerous))


def get_ffmpeg_path():
    """获取 ffmpeg 路径（跨平台）"""
    system = sys.platform
    
    possible_paths = []
    
    # Windows 路径
    if system == "win32":
        possible_paths = [
            "ffmpeg.exe",
            r"C:\ffmpeg\bin\ffmpeg.exe",
            r"C:\Program Files\ffmpeg\bin\ffmpeg.exe",
            r"C:\Program Files (x86)\ffmpeg\bin\ffmpeg.exe",
            os.path.expanduser(r"~\ffmpeg\ffmpeg.exe"),
            os.path.join(os.path.dirname(sys.executable), "ffmpeg.exe"),
            os.path.join(os.path.dirname(__file__), "ffmpeg.exe"),
        ]
    # macOS 路径
    elif system == "darwin":
        possible_paths = [
            "ffmpeg",
            "/opt/homebrew/bin/ffmpeg",  # Apple Silicon
            "/usr/local/bin/ffmpeg",      # Intel Mac
            "/usr/bin/ffmpeg",
            os.path.expanduser("~/ffmpeg/ffmpeg"),
        ]
    # Linux 路径
    else:
        possible_paths = [
            "ffmpeg",
            "/usr/bin/ffmpeg",
            "/usr/local/bin/ffmpeg",
            "/opt/ffmpeg/ffmpeg",
            os.path.expanduser("~/ffmpeg/ffmpeg"),
        ]
    
    for path in possible_paths:
        try:
            result = subprocess.run(
                [path, "-version"], 
                capture_output=True, 
                timeout=5
            )
            if result.returncode == 0:
                return path
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
    
    return None


def extract_audio_ffmpeg(input_path: str, output_path: str, 
                         bitrate: str = "192k", format_type: str = "mp3",
                         progress_callback=None):
    """使用 ffmpeg 提取音频"""
    ffmpeg = get_ffmpeg_path()
    if not ffmpeg:
        raise RuntimeError("未找到 ffmpeg，请安装:\n"
                         "Windows: 下载 ffmpeg.exe 放到程序目录\n"
                         "macOS: brew install ffmpeg\n"
                         "Linux: sudo apt install ffmpeg")
    
    input_path = Path(input_path)
    output_path = Path(output_path)
    
    if not input_path.exists():
        raise FileNotFoundError(f"输入文件不存在: {input_path}")
    
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    format_params = {
        "mp3":  ["-c:a", "libmp3lame"],
        "wav":  ["-c:a", "pcm_s16le"],
        "ogg":  ["-c:a", "libvorbis", "-q:a", "6"],
        "m4a":  ["-c:a", "aac", "-b:a", bitrate],
        "flac": ["-c:a", "flac"],
        "aiff": ["-c:a", "pcm_s16be"],
    }
    
    if format_type not in format_params:
        raise ValueError(f"不支持的格式: {format_type}")
    
    cmd = [ffmpeg, "-i", str(input_path), "-vn", "-y"]
    cmd.extend(format_params[format_type])
    
    if format_type in ["mp3", "m4a"]:
        cmd.extend(["-b:a", bitrate])
    
    cmd.append(str(output_path))
    
    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        universal_newlines=True,
        bufsize=1
    )
    
    duration = None
    pattern_time = re.compile(r"time=(\d{2}):(\d{2}):(\d{2})\.(\d{2})")
    pattern_duration = re.compile(r"Duration: (\d{2}):(\d{2}):(\d{2})\.(\d{2})")
    
    for line in process.stdout:
        line = line.strip()
        
        if duration is None and "Duration:" in line:
            match = pattern_duration.search(line)
            if match:
                h, m, s, ms = map(int, match.groups())
                duration = h * 3600 + m * 60 + s + ms / 100
        
        if "time=" in line and duration:
            match = pattern_time.search(line)
            if match:
                h, m, s, ms = map(int, match.groups())
                current = h * 3600 + m * 60 + s + ms / 100
                percent = min(int((current / duration) * 100), 100)
                if progress_callback:
                    progress_callback(percent, f"提取中... {percent}%")
        
        if "Error" in line:
            raise RuntimeError(f"FFmpeg错误: {line}")
    
    process.wait()
    if process.returncode != 0:
        raise RuntimeError(f"处理失败，返回码: {process.returncode}")
    
    return output_path


class ExtractThread(QThread):
    """后台提取线程"""
    
    log_signal = Signal(str)
    progress_signal = Signal(int, str)
    finished_signal = Signal(bool, str)

    def __init__(self, input_path, output_path, bitrate, format_type):
        super().__init__()
        self.input_path = input_path
        self.output_path = output_path
        self.bitrate = bitrate
        self.format_type = format_type
        self._is_running = True

    def run(self):
        try:
            def progress_callback(percent, text):
                if self._is_running:
                    self.progress_signal.emit(percent, text)
            
            self.log_signal.emit(f"🚀 开始提取音频...")
            self.log_signal.emit(f"格式: {self.format_type.upper()}, 质量: {self.bitrate}")
            
            result_path = extract_audio_ffmpeg(
                self.input_path, 
                self.output_path, 
                self.bitrate,
                self.format_type,
                progress_callback
            )
            
            if self._is_running:
                self.progress_signal.emit(100, "完成")
                self.finished_signal.emit(True, f"✅ 提取成功！保存至: {result_path}")
                
        except Exception as e:
            if self._is_running:
                self.finished_signal.emit(False, str(e))
    
    def stop(self):
        self._is_running = False
        self.wait(1000)


class AudioSiftApp(QMainWindow):
    def __init__(self):
        self.current_output_format = "mp3"
        super().__init__()
        
        self.setWindowTitle("AudioSift")
        self.setMinimumSize(900, 750)
        
        self.is_dark_theme = self.detect_dark_theme()
        self.setup_fonts()
        
        self.ffmpeg_available = get_ffmpeg_path() is not None
        
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        layout = QVBoxLayout(central_widget)
        layout.setSpacing(18)
        layout.setContentsMargins(30, 25, 30, 25)
        
        self.create_title(layout)
        self.create_input_section(layout)
        self.create_settings_section(layout)
        self.create_progress_section(layout)
        self.create_action_button(layout)
        self.create_log_section(layout)
        
        self.thread = None
        self.apply_theme()
        
        if not self.ffmpeg_available:
            self.log_text.append("⚠️ 未检测到 ffmpeg:\n"
                               "Windows: 下载 ffmpeg.exe 放到程序目录\n"
                               "macOS: brew install ffmpeg\n"
                               "Linux: sudo apt install ffmpeg")
            self.extract_btn.setEnabled(False)

    def detect_dark_theme(self):
        """检测系统主题（跨平台）"""
        try:
            if sys.platform == "win32":
                # Windows 注册表
                import winreg
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, 
                                  r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as key:
                    value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
                    return value == 0
            else:
                # Linux/macOS gsettings
                result = subprocess.run(
                    ["gsettings", "get", "org.gnome.desktop.interface", "gtk-theme"],
                    capture_output=True, text=True, timeout=2
                )
                return "dark" in result.stdout.lower()
        except:
            return False

    def setup_fonts(self):
        """配置字体（跨平台）"""
        app = QApplication.instance()
        families = QFontDatabase.families()
        
        # 根据平台选择字体
        if sys.platform == "win32":
            preferred = ["Microsoft YaHei", "SimHei", "Segoe UI", "Arial"]
        elif sys.platform == "darwin":
            preferred = ["PingFang SC", "Heiti SC", "STHeiti", "Arial"]
        else:
            preferred = ["Noto Sans CJK SC", "WenQuanYi Micro Hei", 
                        "Source Han Sans SC", "DejaVu Sans"]
        
        selected = next((f for f in preferred if f in families), "Sans Serif")
        
        font = QFont(selected, 10)
        font.setStyleHint(QFont.SansSerif)
        app.setFont(font)
        self.main_font = selected

    def create_title(self, layout):
        """标题栏"""
        title_frame = QWidget()
        title_layout = QHBoxLayout(title_frame)
        title_layout.setContentsMargins(0, 0, 0, 15)
        
        title = QLabel("🎵 AudioSift")
        title.setFont(QFont(self.main_font, 26, QFont.Bold))
        title_layout.addWidget(title)
        title_layout.addStretch()
        
        self.theme_btn = QPushButton("🌙 深色" if not self.is_dark_theme else "☀️ 浅色")
        self.theme_btn.setCheckable(True)
        self.theme_btn.setChecked(self.is_dark_theme)
        self.theme_btn.setFont(QFont(self.main_font, 10))
        self.theme_btn.clicked.connect(self.toggle_theme)
        title_layout.addWidget(self.theme_btn)
        
        layout.addWidget(title_frame)

    def create_input_section(self, layout):
        """文件选择区域"""
        group = QGroupBox("文件选择")
        group.setFont(QFont(self.main_font, 12, QFont.Bold))
        
        group_layout = QVBoxLayout(group)
        group_layout.setSpacing(12)
        group_layout.setContentsMargins(20, 15, 20, 20)
        
        # 输入文件
        input_layout = QHBoxLayout()
        input_label = QLabel("视频文件:")
        input_label.setFont(QFont(self.main_font, 11))
        input_label.setFixedWidth(80)
        
        self.input_edit = QLineEdit()
        self.input_edit.setPlaceholderText("选择要处理的视频文件...")
        self.input_edit.textChanged.connect(self.auto_update_output)
        
        input_btn = QPushButton("浏览...")
        input_btn.clicked.connect(self.browse_input)
        
        input_layout.addWidget(input_label)
        input_layout.addWidget(self.input_edit)
        input_layout.addWidget(input_btn)
        group_layout.addLayout(input_layout)
        
        # 输出文件
        output_layout = QHBoxLayout()
        output_label = QLabel("保存位置:")
        output_label.setFont(QFont(self.main_font, 11))
        output_label.setFixedWidth(80)
        
        self.output_edit = QLineEdit()
        self.output_edit.setPlaceholderText("自动匹配或手动选择...")
        
        output_btn = QPushButton("保存为...")
        output_btn.clicked.connect(self.browse_output)
        
        output_layout.addWidget(output_label)
        output_layout.addWidget(self.output_edit)
        output_layout.addWidget(output_btn)
        group_layout.addLayout(output_layout)
        
        layout.addWidget(group)

    def create_settings_section(self, layout):
        """设置区域"""
        settings_widget = QWidget()
        settings_layout = QHBoxLayout(settings_widget)
        settings_layout.setSpacing(20)
        
        # 格式选择
        format_layout = QHBoxLayout()
        format_label = QLabel("输出格式:")
        format_label.setFont(QFont(self.main_font, 11, QFont.Bold))
        
        self.format_combo = QComboBox()
        self.format_items = [
            ("MP3 - 兼容最好", "mp3", ".mp3"),
            ("WAV - 无损音质", "wav", ".wav"),
            ("OGG - 开源格式", "ogg", ".ogg"),
            ("M4A - Apple格式", "m4a", ".m4a"),
            ("FLAC - 无损压缩", "flac", ".flac"),
            ("AIFF - 专业音频", "aiff", ".aiff"),
        ]
        
        for display, value, _ in self.format_items:
            self.format_combo.addItem(display, value)
        
        self.format_combo.currentIndexChanged.connect(self.on_format_changed)
        
        format_layout.addWidget(format_label)
        format_layout.addWidget(self.format_combo)
        format_layout.addStretch()
        
        # 质量选择
        quality_layout = QHBoxLayout()
        quality_label = QLabel("音频质量:")
        quality_label.setFont(QFont(self.main_font, 11, QFont.Bold))
        
        self.bitrate_combo = QComboBox()
        self.bitrate_items = [
            ("128k (标准音质)", "128k"),
            ("192k (高品质)", "192k"),
            ("256k (超高品质)", "256k"),
            ("320k (无损级)", "320k"),
        ]
        for display, value in self.bitrate_items:
            self.bitrate_combo.addItem(display, value)
        
        self.bitrate_combo.setCurrentIndex(1)
        
        quality_layout.addWidget(quality_label)
        quality_layout.addWidget(self.bitrate_combo)
        quality_layout.addStretch()
        
        settings_layout.addLayout(format_layout, 1)
        settings_layout.addLayout(quality_layout, 1)
        
        layout.addWidget(settings_widget)

    def create_progress_section(self, layout):
        """进度条"""
        self.progress_label = QLabel("就绪")
        self.progress_label.setAlignment(Qt.AlignCenter)
        
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        
        layout.addWidget(self.progress_label)
        layout.addWidget(self.progress_bar)

    def create_action_button(self, layout):
        """操作按钮"""
        self.extract_btn = QPushButton("✨ 开始提取音频")
        self.extract_btn.setFont(QFont(self.main_font, 14, QFont.Bold))
        self.extract_btn.setMinimumHeight(50)
        self.extract_btn.clicked.connect(self.start_extract)
        layout.addWidget(self.extract_btn)

    def create_log_section(self, layout):
        """日志区域"""
        log_label = QLabel("📋 处理日志")
        log_label.setFont(QFont(self.main_font, 12, QFont.Bold))
        layout.addWidget(log_label)
        
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        mono_fonts = ["JetBrains Mono", "Consolas", "Monaco", "monospace"]
        self.log_text.setFont(QFont(mono_fonts, 10))
        self.log_text.setMinimumHeight(200)
        layout.addWidget(self.log_text)

    def on_format_changed(self, index):
        """格式改变"""
        self.current_output_format = self.format_combo.currentData()
        
        current_output = self.output_edit.text()
        if current_output:
            base = os.path.splitext(current_output)[0]
            _, _, ext = self.format_items[index]
            self.output_edit.setText(base + ext)
        
        lossless = ["wav", "flac", "aiff"]
        self.bitrate_combo.setEnabled(self.current_output_format not in lossless)

    def auto_update_output(self):
        """自动更新输出路径"""
        input_path = self.input_edit.text()
        if not input_path:
            return
            
        base, _ = os.path.splitext(input_path)
        for _, value, ext in self.format_items:
            if value == self.current_output_format:
                self.output_edit.setText(base + ext)
                break

    def browse_input(self):
        """选择输入"""
        path, _ = QFileDialog.getOpenFileName(
            self, "选择视频文件", "",
            "视频文件 (*.mp4 *.avi *.mkv *.mov *.wmv *.flv *.webm *.mpg *.mpeg);;所有文件 (*.*)"
        )
        if path:
            self.input_edit.setText(path)

    def browse_output(self):
        """选择输出"""
        current_input = self.input_edit.text()
        
        if current_input and os.path.exists(current_input):
            base, _ = os.path.splitext(current_input)
            for _, value, ext in self.format_items:
                if value == self.current_output_format:
                    default_name = base + ext
                    break
        else:
            default_name = ""
        
        path, _ = QFileDialog.getSaveFileName(
            self, "保存音频文件", default_name,
            "音频文件 (*.*)"
        )
        if path:
            base, ext = os.path.splitext(path)
            if not ext or ext.lower() != f".{self.current_output_format}":
                path = base + f".{self.current_output_format}"
            self.output_edit.setText(path)

    def start_extract(self):
        """开始提取"""
        in_path = self.input_edit.text().strip()
        out_path = self.output_edit.text().strip()
        bitrate = self.bitrate_combo.currentData()
        format_type = self.format_combo.currentData()

        if not in_path:
            self.log_text.append("❌ 请先选择输入视频文件")
            return
        
        if not os.path.exists(in_path):
            self.log_text.append(f"❌ 输入文件不存在: {in_path}")
            return

        if not out_path:
            base, _ = os.path.splitext(in_path)
            out_path = base + f".{format_type}"
            self.output_edit.setText(out_path)

        in_path = clean_input(in_path)
        out_path = clean_input(out_path)

        self.extract_btn.setEnabled(False)
        self.extract_btn.setText("⏳ 正在提取...")
        self.progress_bar.setValue(0)
        self.progress_label.setText("准备中...")
        self.log_text.clear()
        
        self.thread = ExtractThread(in_path, out_path, bitrate, format_type)
        self.thread.log_signal.connect(self.update_log)
        self.thread.progress_signal.connect(self.update_progress)
        self.thread.finished_signal.connect(self.extract_finished)
        self.thread.start()

    def update_progress(self, percent, text):
        """更新进度"""
        self.progress_bar.setValue(percent)
        self.progress_label.setText(text)

    def update_log(self, text):
        """更新日志"""
        self.log_text.append(text)
        scrollbar = self.log_text.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def extract_finished(self, success, message):
        """完成处理"""
        if success:
            self.progress_bar.setValue(100)
            self.progress_label.setText("✅ 完成")
            self.log_text.append(f"\n{message}")
        else:
            self.progress_label.setText("❌ 失败")
            self.log_text.append(f"\n❌ 错误: {message}")

        self.extract_btn.setEnabled(True)
        self.extract_btn.setText("✨ 开始提取音频")

    def toggle_theme(self):
        """切换主题"""
        self.is_dark_theme = self.theme_btn.isChecked()
        self.theme_btn.setText("☀️ 浅色" if self.is_dark_theme else "🌙 深色")
        self.apply_theme()

    def apply_theme(self):
        """应用主题"""
        app = QApplication.instance()
        
        if self.is_dark_theme:
            app.setStyle("Fusion")
            palette = QPalette()
            palette.setColor(QPalette.Window, QColor("#0f172a"))
            palette.setColor(QPalette.WindowText, QColor("#f8fafc"))
            palette.setColor(QPalette.Base, QColor("#1e293b"))
            palette.setColor(QPalette.Text, QColor("#f8fafc"))
            palette.setColor(QPalette.Button, QColor("#334155"))
            palette.setColor(QPalette.ButtonText, QColor("#f8fafc"))
            palette.setColor(QPalette.Highlight, QColor("#3b82f6"))
            app.setPalette(palette)
        else:
            app.setStyle("Fusion")
            palette = QPalette()
            palette.setColor(QPalette.Window, QColor("#f8fafc"))
            palette.setColor(QPalette.WindowText, QColor("#0f172a"))
            palette.setColor(QPalette.Base, QColor("#ffffff"))
            palette.setColor(QPalette.Text, QColor("#0f172a"))
            palette.setColor(QPalette.Button, QColor("#e2e8f0"))
            palette.setColor(QPalette.ButtonText, QColor("#0f172a"))
            palette.setColor(QPalette.Highlight, QColor("#3b82f6"))
            app.setPalette(palette)


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    
    window = AudioSiftApp()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
