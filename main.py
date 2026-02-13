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
    """清理用户输入，移除非法字符，防止命令注入"""
    # 移除危险字符：引号、分号、管道、反引号、$等
    dangerous = "'\"`;|&$(){}[]<>\\"
    return user_text.translate(str.maketrans('', '', dangerous))


def get_ffmpeg_path():
    """获取ffmpeg可执行文件路径"""
    # 常见路径检查
    possible_paths = [
        "ffmpeg",
        "/usr/bin/ffmpeg",
        "/usr/local/bin/ffmpeg",
        "/opt/homebrew/bin/ffmpeg",  # macOS Homebrew (Apple Silicon)
        "C:\\ffmpeg\\bin\\ffmpeg.exe",
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
    """
    使用ffmpeg提取音频，支持多种格式，带进度回调
    :param progress_callback: 回调函数(percent, status_text)
    """
    ffmpeg = get_ffmpeg_path()
    if not ffmpeg:
        raise RuntimeError("未找到ffmpeg，请先安装: sudo apt install ffmpeg 或 brew install ffmpeg")
    
    input_path = Path(input_path)
    output_path = Path(output_path)
    
    # 验证输入
    if not input_path.exists():
        raise FileNotFoundError(f"输入文件不存在: {input_path}")
    
    # 确保输出目录存在
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    # 格式参数映射
    format_params = {
        "mp3":  ["-c:a", "libmp3lame", "-q:a", "0" if bitrate == "320k" else "2"],
        "wav":  ["-c:a", "pcm_s16le"],
        "ogg":  ["-c:a", "libvorbis", "-q:a", "6"],
        "m4a":  ["-c:a", "aac", "-b:a", bitrate],
        "flac": ["-c:a", "flac"],
        "aiff": ["-c:a", "pcm_s16be"],
    }
    
    if format_type not in format_params:
        raise ValueError(f"不支持的格式: {format_type}")
    
    # 构建命令
    cmd = [
        ffmpeg,
        "-i", str(input_path),           # 输入
        "-vn",                            # 禁用视频
        "-y",                             # 覆盖输出
    ]
    
    # 添加格式特定参数
    cmd.extend(format_params[format_type])
    
    # 比特率（部分格式需要）
    if format_type in ["mp3", "m4a"]:
        cmd.extend(["-b:a", bitrate])
    
    cmd.append(str(output_path))
    
    # 执行并捕获进度
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
        
        # 解析总时长
        if duration is None and "Duration:" in line:
            match = pattern_duration.search(line)
            if match:
                h, m, s, ms = map(int, match.groups())
                duration = h * 3600 + m * 60 + s + ms / 100
        
        # 解析当前进度
        if "time=" in line and duration:
            match = pattern_time.search(line)
            if match:
                h, m, s, ms = map(int, match.groups())
                current = h * 3600 + m * 60 + s + ms / 100
                percent = min(int((current / duration) * 100), 100)
                if progress_callback:
                    progress_callback(percent, f"提取中... {percent}%")
        
        # 错误检测
        if "Error" in line or "Invalid" in line:
            raise RuntimeError(f"FFmpeg错误: {line}")
    
    process.wait()
    if process.returncode != 0:
        raise RuntimeError(f"处理失败，返回码: {process.returncode}")
    
    return output_path


class ExtractThread(QThread):
    """后台提取线程，支持进度回调"""
    
    log_signal = Signal(str)
    progress_signal = Signal(int, str)  # 进度百分比, 状态文本
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
        self.wait(1000)  # 等待1秒


class VideoExtractorApp(QMainWindow):
    def __init__(self):
        self.current_output_format = "mp3"
        super().__init__()
        
        self.setWindowTitle("视频音频提取器 Pro")
        self.setMinimumSize(900, 750)
        self.resize(950, 800)
        
        self.is_dark_theme = self.detect_dark_theme()
        self.setup_fonts()
        
        # 检查ffmpeg
        self.ffmpeg_available = get_ffmpeg_path() is not None
        
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        layout = QVBoxLayout(central_widget)
        layout.setSpacing(18)
        layout.setContentsMargins(30, 25, 30, 25)
        
        self.create_title(layout)
        self.create_input_section(layout)
        self.create_settings_section(layout)  # 包含格式选择
        self.create_progress_section(layout)   # 新增进度条
        self.create_action_button(layout)
        self.create_log_section(layout)
        
        self.thread = None
        
        # 初始主题应用
        self.apply_theme()
        
        # ffmpeg警告
        if not self.ffmpeg_available:
            self.log_text.append("⚠️ 警告: 未检测到ffmpeg，请先安装后再使用")
            self.extract_btn.setEnabled(False)

    def detect_dark_theme(self):
        """检测系统主题"""
        try:
            result = subprocess.run(
                ["gsettings", "get", "org.gnome.desktop.interface", "gtk-theme"],
                capture_output=True, text=True, timeout=2
            )
            return "dark" in result.stdout.lower()
        except:
            return os.environ.get("GTK_THEME", "").lower().find("dark") != -1

    def setup_fonts(self):
        """配置字体"""
        app = QApplication.instance()
        families = QFontDatabase.families()
        
        preferred = [
            "Noto Sans CJK SC", "WenQuanYi Micro Hei", 
            "Source Han Sans SC", "Microsoft YaHei", "PingFang SC",
            "DejaVu Sans", "Segoe UI"
        ]
        
        selected = next((f for f in preferred if f in families), "Sans Serif")
        
        font = QFont(selected, 10)
        font.setStyleHint(QFont.SansSerif)
        app.setFont(font)
        self.main_font = selected

    def create_title(self, layout):
        """创建标题栏"""
        title_frame = QWidget()
        title_layout = QHBoxLayout(title_frame)
        title_layout.setContentsMargins(0, 0, 0, 15)
        
        title = QLabel("🎬 视频音频提取器 Pro")
        title.setFont(QFont(self.main_font, 26, QFont.Bold))
        title_layout.addWidget(title)
        title_layout.addStretch()
        
        # 主题切换
        self.theme_btn = QPushButton("🌙 深色" if not self.is_dark_theme else "☀️ 浅色")
        self.theme_btn.setCheckable(True)
        self.theme_btn.setChecked(self.is_dark_theme)
        self.theme_btn.setFont(QFont(self.main_font, 10))
        self.theme_btn.setStyleSheet(self.get_button_style("secondary"))
        self.theme_btn.clicked.connect(self.toggle_theme)
        title_layout.addWidget(self.theme_btn)
        
        layout.addWidget(title_frame)

    def create_input_section(self, layout):
        """文件选择区域"""
        group = QGroupBox("文件选择")
        group.setFont(QFont(self.main_font, 12, QFont.Bold))
        group.setStyleSheet(self.get_groupbox_style())
        
        group_layout = QVBoxLayout(group)
        group_layout.setSpacing(12)
        group_layout.setContentsMargins(20, 15, 20, 20)
        
        # 输入文件
        input_layout = QHBoxLayout()
        input_label = QLabel("视频文件:")
        input_label.setFont(QFont(self.main_font, 11))
        input_label.setFixedWidth(80)
        
        self.input_edit = QLineEdit()
        self.input_edit.setPlaceholderText("点击浏览选择视频文件...")
        self.input_edit.setFont(QFont(self.main_font, 11))
        self.input_edit.setMinimumHeight(40)
        self.input_edit.setStyleSheet(self.get_input_style())
        self.input_edit.textChanged.connect(self.auto_update_output)  # 自动更新输出路径
        
        input_btn = QPushButton("浏览...")
        input_btn.setFont(QFont(self.main_font, 10, QFont.Bold))
        input_btn.setCursor(Qt.PointingHandCursor)
        input_btn.setStyleSheet(self.get_button_style("primary"))
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
        self.output_edit.setFont(QFont(self.main_font, 11))
        self.output_edit.setMinimumHeight(40)
        self.output_edit.setStyleSheet(self.get_input_style())
        
        output_btn = QPushButton("保存为...")
        output_btn.setFont(QFont(self.main_font, 10, QFont.Bold))
        output_btn.setCursor(Qt.PointingHandCursor)
        output_btn.setStyleSheet(self.get_button_style("primary"))
        output_btn.clicked.connect(self.browse_output)
        
        output_layout.addWidget(output_label)
        output_layout.addWidget(self.output_edit)
        output_layout.addWidget(output_btn)
        group_layout.addLayout(output_layout)
        
        layout.addWidget(group)

    def create_settings_section(self, layout):
        """设置区域 - 包含格式和质量的并排布局"""
        settings_widget = QWidget()
        settings_layout = QHBoxLayout(settings_widget)
        settings_layout.setSpacing(20)
        
        # ===== 输出格式选择（下拉菜单）=====
        format_layout = QHBoxLayout()
        format_label = QLabel("输出格式:")
        format_label.setFont(QFont(self.main_font, 11, QFont.Bold))
        
        self.format_combo = QComboBox()
        # (显示文本, 内部值, 文件扩展名)
        self.format_items = [
            ("MP3 - 兼容最好", "mp3", ".mp3"),
            ("WAV - 无损音质", "wav", ".wav"),
            ("OGG - 开源格式", "ogg", ".ogg"),
            ("M4A - Apple格式", "m4a", ".m4a"),
            ("FLAC - 无损压缩", "flac", ".flac"),
            ("AIFF - 专业音频", "aiff", ".aiff"),
        ]
        
        for display, value, _ in self.format_items:
            self.format_combo.addItem(display, value)  # 存储内部值
        
        self.format_combo.setCurrentIndex(0)
        self.format_combo.setFont(QFont(self.main_font, 11))
        self.format_combo.setMinimumHeight(40)
        self.format_combo.setStyleSheet(self.get_combo_style())
        self.format_combo.currentIndexChanged.connect(self.on_format_changed)
        
        format_layout.addWidget(format_label)
        format_layout.addWidget(self.format_combo)
        format_layout.addStretch()
        
        # ===== 音频质量选择 =====
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
        
        self.bitrate_combo.setCurrentIndex(1)  # 默认192k
        self.bitrate_combo.setFont(QFont(self.main_font, 11))
        self.bitrate_combo.setMinimumHeight(40)
        self.bitrate_combo.setStyleSheet(self.get_combo_style())
        
        quality_layout.addWidget(quality_label)
        quality_layout.addWidget(self.bitrate_combo)
        quality_layout.addStretch()
        
        # 添加到主设置布局
        settings_layout.addLayout(format_layout, 1)
        settings_layout.addLayout(quality_layout, 1)
        
        layout.addWidget(settings_widget)

    def create_progress_section(self, layout):
        """创建进度条区域"""
        progress_widget = QWidget()
        progress_layout = QVBoxLayout(progress_widget)
        progress_layout.setContentsMargins(0, 5, 0, 5)
        
        # 进度标签
        self.progress_label = QLabel("就绪")
        self.progress_label.setFont(QFont(self.main_font, 11))
        self.progress_label.setAlignment(Qt.AlignCenter)
        
        # 进度条
        self.progress_bar = QProgressBar()
        self.progress_bar.setMinimumHeight(25)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setStyleSheet(self.get_progress_style())
        
        progress_layout.addWidget(self.progress_label)
        progress_layout.addWidget(self.progress_bar)
        
        layout.addWidget(progress_widget)

    def create_action_button(self, layout):
        """主操作按钮"""
        self.extract_btn = QPushButton("✨ 开始提取音频")
        self.extract_btn.setFont(QFont(self.main_font, 14, QFont.Bold))
        self.extract_btn.setMinimumHeight(50)
        self.extract_btn.setCursor(Qt.PointingHandCursor)
        self.extract_btn.setStyleSheet(self.get_button_style("success"))
        self.extract_btn.clicked.connect(self.start_extract)
        layout.addWidget(self.extract_btn)

    def create_log_section(self, layout):
        """日志区域"""
        log_label = QLabel("📋 处理日志")
        log_label.setFont(QFont(self.main_font, 12, QFont.Bold))
        layout.addWidget(log_label)
        
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        mono_fonts = ["JetBrains Mono", "Consolas", "Monaco", "DejaVu Sans Mono", "monospace"]
        self.log_text.setFont(QFont(mono_fonts, 10))  
        self.log_text.setMinimumHeight(200)
        self.log_text.setStyleSheet(self.get_log_style())
        layout.addWidget(self.log_text)

    # ===== 事件处理 =====
    
    def on_format_changed(self, index):
        """格式改变时更新内部状态，并自动更新输出文件扩展名"""
        self.current_output_format = self.format_combo.currentData()
        
        # 自动更新输出路径的扩展名
        current_output = self.output_edit.text()
        if current_output:
            base = os.path.splitext(current_output)[0]
            _, _, ext = self.format_items[index]
            new_output = base + ext
            self.output_edit.setText(new_output)
        
        # WAV/FLAC/AIFF 是无损格式，禁用质量选择
        lossless_formats = ["wav", "flac", "aiff"]
        is_lossless = self.current_output_format in lossless_formats
        self.bitrate_combo.setEnabled(not is_lossless)
        
        if is_lossless:
            self.log_text.append(f"ℹ️ {self.current_output_format.upper()} 是无损格式，无需选择比特率")

    def auto_update_output(self):
        """输入路径改变时自动更新输出路径"""
        input_path = self.input_edit.text()
        if not input_path:
            return
            
        base, _ = os.path.splitext(input_path)
        # 获取当前格式的扩展名
        for display, value, ext in self.format_items:
            if value == self.current_output_format:
                self.output_edit.setText(base + ext)
                break

    def browse_input(self):
        """选择输入文件"""
        path, _ = QFileDialog.getOpenFileName(
            self, "选择视频文件", "",
            "视频文件 (*.mp4 *.avi *.mkv *.mov *.wmv *.flv *.webm *.mpg *.mpeg *.m4v);;所有文件 (*.*)"
        )
        if path:
            self.input_edit.setText(path)

    def browse_output(self):
        """选择输出位置"""
        current_input = self.input_edit.text()
        
        # 构建默认文件名
        if current_input and os.path.exists(current_input):
            base, _ = os.path.splitext(current_input)
            for display, value, ext in self.format_items:
                if value == self.current_output_format:
                    default_name = base + ext
                    break
            else:
                default_name = base + ".mp3"
        else:
            default_name = ""
        
        # 根据格式设置过滤器
        format_filters = {
            "mp3": "MP3 音频 (*.mp3)",
            "wav": "WAV 音频 (*.wav)",
            "ogg": "OGG 音频 (*.ogg)",
            "m4a": "M4A 音频 (*.m4a)",
            "flac": "FLAC 音频 (*.flac)",
            "aiff": "AIFF 音频 (*.aiff)",
        }
        selected_filter = format_filters.get(self.current_output_format, "所有文件 (*)")
        
        path, _ = QFileDialog.getSaveFileName(
            self, "保存音频文件", default_name,
            f"{selected_filter};;所有文件 (*)"
        )
        if path:
            # 确保扩展名正确
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

        # 验证
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

        # 清理路径
        in_path = clean_input(in_path)
        out_path = clean_input(out_path)

        # 准备UI
        self.extract_btn.setEnabled(False)
        self.extract_btn.setText("⏳ 正在提取...")
        self.progress_bar.setValue(0)
        self.progress_label.setText("准备中...")
        self.log_text.clear()
        
        # 启动线程
        self.thread = ExtractThread(in_path, out_path, bitrate, format_type)
        self.thread.log_signal.connect(self.update_log)
        self.thread.progress_signal.connect(self.update_progress)
        self.thread.finished_signal.connect(self.extract_finished)
        self.thread.start()

    def update_progress(self, percent, text):
        """更新进度条"""
        self.progress_bar.setValue(percent)
        self.progress_label.setText(text)
        
        # 根据进度改变颜色
        if percent < 30:
            color = "#3b82f6"  # 蓝
        elif percent < 70:
            color = "#f59e0b"  # 黄
        else:
            color = "#10b981"  # 绿
            
        self.progress_bar.setStyleSheet(f"""
            QProgressBar {{
                border: 2px solid {'#475569' if self.is_dark_theme else '#cbd5e1'};
                border-radius: 6px;
                text-align: center;
                font-weight: bold;
                background-color: {'#334155' if self.is_dark_theme else '#e2e8f0'};
            }}
            QProgressBar::chunk {{
                background-color: {color};
                border-radius: 4px;
            }}
        """)

    def update_log(self, text):
        """更新日志"""
        self.log_text.append(text)
        scrollbar = self.log_text.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def extract_finished(self, success, message):
        """提取完成处理"""
        if success:
            self.progress_bar.setValue(100)
            self.progress_label.setText("✅ 完成")
            self.log_text.append(f"\n{message}")
        else:
            self.progress_label.setText("❌ 失败")
            self.log_text.append(f"\n❌ 错误: {message}")
            self.progress_bar.setStyleSheet("""
                QProgressBar::chunk { background-color: #ef4444; }
            """)

        self.extract_btn.setEnabled(True)
        self.extract_btn.setText("✨ 开始提取音频")

    # ===== 样式系统 =====
    
    def toggle_theme(self):
        """切换主题"""
        self.is_dark_theme = self.theme_btn.isChecked()
        self.theme_btn.setText("☀️ 浅色" if self.is_dark_theme else "🌙 深色")
        self.apply_theme()

    def apply_theme(self):
        """应用主题"""
        app = QApplication.instance()
        
        if self.is_dark_theme:
            # 深色主题
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
            
            self.setStyleSheet("""
                QMainWindow { background-color: #0f172a; }
                QLabel { color: #f8fafc; }
                QGroupBox { color: #f8fafc; border: 2px solid #334155; }
            """)
        else:
            # 浅色主题
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
            
            self.setStyleSheet("""
                QMainWindow { background-color: #f8fafc; }
                QLabel { color: #0f172a; }
                QGroupBox { color: #334155; border: 2px solid #e2e8f1; }
            """)
        
        # 刷新组件样式
        self.update_styles()

    def update_styles(self):
        """刷新所有动态样式"""
        # 重新应用输入框样式
        if hasattr(self, 'input_edit'):
            self.input_edit.setStyleSheet(self.get_input_style())
            self.output_edit.setStyleSheet(self.get_input_style())
            self.format_combo.setStyleSheet(self.get_combo_style())
            self.bitrate_combo.setStyleSheet(self.get_combo_style())
            self.log_text.setStyleSheet(self.get_log_style())
            self.progress_bar.setStyleSheet(self.get_progress_style())

    def get_input_style(self):
        """输入框样式"""
        if self.is_dark_theme:
            return """
                QLineEdit {
                    padding: 10px;
                    border: 2px solid #475569;
                    border-radius: 8px;
                    background: #334155;
                    color: #f8fafc;
                    font-size: 13px;
                }
                QLineEdit:focus { border-color: #3b82f6; }
                QLineEdit::placeholder { color: #94a3b8; }
            """
        else:
            return """
                QLineEdit {
                    padding: 10px;
                    border: 2px solid #cbd5e1;
                    border-radius: 8px;
                    background: white;
                    color: #0f172a;
                    font-size: 13px;
                }
                QLineEdit:focus { border-color: #3b82f6; }
                QLineEdit::placeholder { color: #94a3b8; }
            """

    def get_combo_style(self):
        """下拉框样式"""
        if self.is_dark_theme:
            return """
                QComboBox {
                    padding: 10px;
                    border: 2px solid #475569;
                    border-radius: 8px;
                    background: #334155;
                    color: #f8fafc;
                    min-width: 160px;
                    font-size: 13px;
                }
                QComboBox:hover { border-color: #3b82f6; }
                QComboBox::drop-down { border: none; width: 30px; }
                QComboBox QAbstractItemView {
                    background: #334155;
                    color: #f8fafc;
                    selection-background-color: #3b82f6;
                }
            """
        else:
            return """
                QComboBox {
                    padding: 10px;
                    border: 2px solid #cbd5e1;
                    border-radius: 8px;
                    background: white;
                    color: #0f172a;
                    min-width: 160px;
                    font-size: 13px;
                }
                QComboBox:hover { border-color: #3b82f6; }
                QComboBox::drop-down { border: none; width: 30px; }
                QComboBox QAbstractItemView {
                    background: white;
                    selection-background-color: #3b82f6;
                }
            """

    def get_button_style(self, style_type):
        """按钮样式工厂"""
        styles = {
            "primary": ("#3b82f6", "#2563eb", "#1d4ed8"),
            "secondary": ("#64748b", "#475569", "#334155"),
            "success": ("#10b981", "#059669", "#047857"),
        }
        normal, hover, pressed = styles.get(style_type, styles["primary"])
        
        text_color = "white"
        if style_type == "secondary" and not self.is_dark_theme:
            text_color = "white"
        
        return f"""
            QPushButton {{
                background-color: {normal};
                color: {text_color};
                border-radius: 8px;
                border: none;
                padding: 10px 20px;
                font-weight: bold;
            }}
            QPushButton:hover {{ background-color: {hover}; }}
            QPushButton:pressed {{ background-color: {pressed}; }}
            QPushButton:disabled {{ background-color: #6b7280; color: #9ca3af; }}
        """

    def get_groupbox_style(self):
        """分组框样式"""
        border_color = "#334155" if self.is_dark_theme else "#e2e8f0"
        text_color = "#f8fafc" if self.is_dark_theme else "#334155"
        return f"""
            QGroupBox {{
                border: 2px solid {border_color};
                border-radius: 12px;
                margin-top: 12px;
                padding-top: 12px;
                font-weight: bold;
                color: {text_color};
            }}
            QGroupBox::title {{
                subcontrol-origin: margin;
                left: 12px;
                padding: 0 8px;
            }}
        """

    def get_progress_style(self):
        """进度条基础样式"""
        bg = "#334155" if self.is_dark_theme else "#e2e8f0"
        return f"""
            QProgressBar {{
                border: 2px solid {'#475569' if self.is_dark_theme else '#cbd5e1'};
                border-radius: 6px;
                text-align: center;
                font-weight: bold;
                background-color: {bg};
                color: {'#f8fafc' if self.is_dark_theme else '#0f172a'};
            }}
            QProgressBar::chunk {{
                background-color: #3b82f6;
                border-radius: 4px;
            }}
        """

    def get_log_style(self):
        """日志区域样式"""
        if self.is_dark_theme:
            return """
                QTextEdit {
                    background-color: #020617;
                    color: #e2e8f0;
                    border-radius: 12px;
                    padding: 15px;
                    border: 1px solid #334155;
                    font-size: 12px;
                    line-height: 1.6;
                }
            """
        else:
            return """
                QTextEdit {
                    background-color: #0f172a;
                    color: #e2e8f0;
                    border-radius: 12px;
                    padding: 15px;
                    border: 1px solid #cbd5e1;
                    font-size: 12px;
                    line-height: 1.6;
                }
            """


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    
    window = VideoExtractorApp()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()