import sys
import math
import yt_dlp
import os
import json
import urllib.request
import datetime
from PyQt5.QtWidgets import (QApplication, QWidget, QVBoxLayout, QHBoxLayout,
                             QPushButton, QLineEdit, QComboBox, QLabel,
                             QProgressBar, QFileDialog, QStyle, QScrollArea,
                             QGroupBox, QGridLayout, QCheckBox, QTabWidget, QTabBar, QStackedWidget,
                             QTableWidget, QTableWidgetItem, QDialog, QHeaderView,
                             QMenuBar, QAction, QDialogButtonBox)
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QTimer, QUrl, QSettings, QSize
from PyQt5.QtGui import QIcon, QPixmap, QDesktopServices

# --- THREAD WORKERS ---

class InfoFetcherThread(QThread):
    finished = pyqtSignal(dict)
    error = pyqtSignal(str)

    def __init__(self, url):
        super().__init__()
        self.url = url

    def run(self):
        try:
            ydl_opts = {'extract_flat': 'in_playlist', 'quiet': True, 'skip_download': True}
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(self.url, download=False)
            self.finished.emit(info)
        except Exception as e:
            self.error.emit(str(e))

class ThumbnailDownloaderThread(QThread):
    finished = pyqtSignal(QPixmap)

    def __init__(self, url):
        super().__init__()
        self.url = url

    def run(self):
        try:
            data = urllib.request.urlopen(self.url).read()
            pixmap = QPixmap()
            pixmap.loadFromData(data)
            self.finished.emit(pixmap)
        except Exception as e:
            print(f"Could not load thumbnail: {e}")
            self.finished.emit(QPixmap())

class DownloaderThread(QThread):
    progress = pyqtSignal(int)
    stats = pyqtSignal(str, str)
    postprocessing = pyqtSignal(str)
    finished = pyqtSignal(bool, str, dict)

    def __init__(self, video_info, format_selection, output_path, filename_template, rate_limit):
        super().__init__()
        self.video_info = video_info
        self.format_selection = format_selection
        self.output_path = output_path
        self.filename_template = filename_template
        self.rate_limit = rate_limit

    def run(self):
        try:
            ydl_opts = {
                'progress_hooks': [self.progress_hook],
                'outtmpl': os.path.join(self.output_path, self.filename_template),
                'noplaylist': True,
                'ignoreerrors': True,
                'format': self.format_selection,
                'postprocessors': [],
            }
            if self.rate_limit:
                ydl_opts['ratelimit'] = self.rate_limit

            if self.video_info.get('selected_format_type') == 'audio':
                ydl_opts.update({
                    'postprocessors': [
                        {'key': 'FFmpegExtractAudio', 'preferredcodec': self.video_info.get('selected_format_ext'), 'preferredquality': '192'},
                        {'key': 'EmbedThumbnail'},
                        {'key': 'FFmpegMetadata', 'add_metadata': True},
                    ]
                })
            else:
                ydl_opts['merge_output_format'] = self.video_info.get('selected_format_ext')

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([self.video_info['webpage_url']])

            self.finished.emit(True, "Download completed!", self.video_info)

        except Exception as e:
            self.finished.emit(False, f"Error: {e}", self.video_info)

    def progress_hook(self, d):
        if d['status'] == 'downloading':
            total_bytes = d.get('total_bytes') or d.get('total_bytes_estimate')
            if total_bytes:
                downloaded_bytes = d.get('downloaded_bytes', 0)
                percentage = int((downloaded_bytes / total_bytes) * 100)
                self.progress.emit(percentage)
            self.stats.emit(d.get('_speed_str', 'N/A'), d.get('_eta_str', 'N/A'))
        elif d['status'] == 'finished':
            self.postprocessing.emit("Post-processing (merging, converting)...")


class VideoDownloader(QWidget):
    def __init__(self):
        super().__init__()
        self.settings = QSettings("AreaVII", "VideoDownloader")
        self.fetched_info = None
        self.playlist_items = []
        self.download_queue = []
        self.is_downloading = False
        self.history_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "history.json")

        self.load_settings()
        self.initUI()
        self.load_history()

        self.setAcceptDrops(True)

    def initUI(self):
        self.setWindowTitle('AV (Video Downloader)')
        self.setWindowIcon(QIcon("AREA IT.ico"))
        self.setGeometry(200, 200, 800, 700)
        self.setStyleSheet("""
            QWidget { 
                background-color: #1e2124; 
                color: #ffffff; 
                font-size: 14px; 
            }
            QStackedWidget {
                border: 1px solid #43b581;
                border-radius: 6px;
            }
            QTabBar::tab {
                background: #282b30;
                color: #b9bbbe;
                padding: 10px 20px;
                border-top-left-radius: 6px;
                border-top-right-radius: 6px;
                border: 1px solid #282b30;
                margin-right: 2px;
                font-weight: bold;
            }
            QTabBar::tab:selected {
                background: #43b581;
                color: white;
            }
            QTabBar::tab:!selected:hover {
                background: #36393f;
            }
            QGroupBox { 
                font-weight: bold; 
                border: 1px solid #43b581; 
                border-radius: 5px; 
                margin-top: 1ex; 
            }
            QGroupBox::title { 
                subcontrol-origin: margin; 
                subcontrol-position: top center; 
                padding: 0 3px; 
                color: #43b581; 
            }
            QPushButton { 
                background-color: #43b581; 
                border: none; 
                color: white; 
                padding: 8px 16px; 
                margin: 5px; 
                border-radius: 4px; 
                font-weight: bold; 
            }
            QPushButton:hover { 
                background-color: #3aa071; 
            }
            QPushButton:disabled { 
                background-color: #282b30; 
                color: #72767d; 
            }
            QLineEdit, QComboBox, QTableWidget { 
                padding: 8px; 
                margin: 5px; 
                border: 1px solid #43b581;
                border-radius: 4px; 
                background-color: #282b30; 
                color: #ffffff; 
            }
            QProgressBar { 
                border: 2px solid #43b581; 
                border-radius: 5px; 
                text-align: center; 
                background-color: #282b30; 
                color: #ffffff; 
                font-weight: bold; 
            }
            QProgressBar::chunk { 
                background-color: #43b581; 
            }
            QLabel { 
                margin: 2px 5px; 
                color: #b9bbbe; 
            }
            QLabel#video_title, QLabel#video_duration, QLabel#file_info { 
                color: #ffffff; 
            }
            QHeaderView::section { 
                background-color: #282b30; 
                color: #43b581; 
                padding: 4px; 
                border: 1px solid #43b581; 
            }
        """)

        main_layout = QVBoxLayout(self)

        self.downloader_tab = QWidget()
        self.queue_tab = QWidget()
        self.history_tab = QWidget()
        self.settings_tab = QWidget()

        self.tab_bar = QTabBar()
        self.tab_bar.setExpanding(False)
        self.tab_bar.setDocumentMode(True)
        
        download_icon = self.style().standardIcon(QStyle.SP_ArrowDown)
        queue_icon = self.style().standardIcon(QStyle.SP_FileDialogListView)
        history_icon = self.style().standardIcon(QStyle.SP_FileDialogDetailedView)
        settings_icon = self.style().standardIcon(QStyle.SP_ToolBarHorizontalExtensionButton)

        self.tab_bar.addTab(download_icon, "Downloader")
        self.tab_bar.addTab(queue_icon, "Download Queue")
        self.tab_bar.addTab(history_icon, "History")
        self.tab_bar.addTab(settings_icon, "Settings")
        self.tab_bar.setIconSize(QSize(20, 20))
        
        self.stacked_widget = QStackedWidget()
        self.stacked_widget.addWidget(self.downloader_tab)
        self.stacked_widget.addWidget(self.queue_tab)
        self.stacked_widget.addWidget(self.history_tab)
        self.stacked_widget.addWidget(self.settings_tab)
        
        self.tab_bar.currentChanged.connect(self.stacked_widget.setCurrentIndex)
        
        self.init_downloader_tab()
        self.init_queue_tab()
        self.init_history_tab()
        self.init_settings_tab()
        
        tab_bar_layout = QHBoxLayout()
        tab_bar_layout.addStretch()
        tab_bar_layout.addWidget(self.tab_bar)
        tab_bar_layout.addStretch()

        main_layout.addLayout(tab_bar_layout)
        main_layout.addWidget(self.stacked_widget)

        progress_group = QGroupBox("Current Download")
        progress_layout = QVBoxLayout()
        stats_layout = QHBoxLayout()
        self.speed_label = QLabel("Speed: N/A")
        stats_layout.addWidget(self.speed_label)
        self.eta_label = QLabel("ETA: N/A")
        stats_layout.addWidget(self.eta_label)
        progress_layout.addLayout(stats_layout)
        self.progress_bar = QProgressBar()
        self.progress_bar.setTextVisible(True)
        progress_layout.addWidget(self.progress_bar)
        status_layout = QHBoxLayout()
        self.status_label = QLabel("Welcome! Drop a URL to begin.")
        self.status_label.setAlignment(Qt.AlignLeft)
        status_layout.addWidget(self.status_label, 1)

        self.stop_button = QPushButton("Stop")
        self.stop_button.clicked.connect(self.stop_download)
        self.stop_button.setEnabled(False)
        status_layout.addWidget(self.stop_button)

        self.open_folder_button = QPushButton("Open Download Folder")
        self.open_folder_button.clicked.connect(self.open_download_folder)
        self.open_folder_button.setVisible(False)
        status_layout.addWidget(self.open_folder_button)
        progress_layout.addLayout(status_layout)
        progress_group.setLayout(progress_layout)
        main_layout.addWidget(progress_group)

    def init_downloader_tab(self):
        layout = QVBoxLayout(self.downloader_tab)
        layout.setContentsMargins(0,0,0,0)
        
        logo_label = QLabel(self)
        logo_pixmap = QPixmap('AREA IT.png') 
        logo_label.setPixmap(logo_pixmap.scaled(250, 100, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        logo_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(logo_label)

        input_group = QGroupBox("1. URL Input")
        input_layout = QHBoxLayout()
        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("Enter or Drop Video/Playlist URL here")
        input_layout.addWidget(self.url_input)
        self.fetch_button = QPushButton("Fetch Info")
        self.fetch_button.clicked.connect(self.fetch_video_info)
        input_layout.addWidget(self.fetch_button)
        input_group.setLayout(input_layout)
        layout.addWidget(input_group)

        options_group = QGroupBox("2. Download Options")
        options_layout = QGridLayout()
        options_layout.addWidget(QLabel("Format:"), 0, 0)
        self.format_combo = QComboBox()
        self.format_combo.addItems(["Video (MP4)", "Video (MKV)", "Audio (MP3)", "Audio (M4A)"])
        self.format_combo.currentIndexChanged.connect(self.toggle_resolution_box)
        options_layout.addWidget(self.format_combo, 0, 1)
        self.resolution_label = QLabel("Quality:")
        options_layout.addWidget(self.resolution_label, 1, 0)
        self.resolution_combo = QComboBox()
        self.resolution_combo.currentTextChanged.connect(self.update_file_size)
        options_layout.addWidget(self.resolution_combo, 1, 1)
        self.output_button = QPushButton("Select Output Folder")
        self.output_button.clicked.connect(self.select_output_folder)
        options_layout.addWidget(self.output_button, 2, 0, 1, 2)
        options_group.setLayout(options_layout)
        layout.addWidget(options_group)

        info_group = QGroupBox("Video Information")
        info_main_layout = QHBoxLayout()
        self.thumbnail_label = QLabel()
        self.thumbnail_label.setFixedSize(160, 90)
        self.thumbnail_label.setStyleSheet("border: 1px solid #43b581; background-color: #2C2F33;")
        self.thumbnail_label.setAlignment(Qt.AlignCenter)
        info_main_layout.addWidget(self.thumbnail_label)
        info_details_layout = QVBoxLayout()
        self.video_title = QLabel("Title:")
        self.video_title.setObjectName("video_title")
        self.video_title.setWordWrap(True)
        info_details_layout.addWidget(self.video_title)
        self.video_duration = QLabel("Duration:")
        self.video_duration.setObjectName("video_duration")
        info_details_layout.addWidget(self.video_duration)
        self.file_info = QLabel("Estimated File Size:")
        self.file_info.setObjectName("file_info")
        info_details_layout.addWidget(self.file_info)
        info_main_layout.addLayout(info_details_layout)
        info_group_widget = QWidget()
        info_group_widget.setLayout(info_main_layout)
        outer_info_layout = QVBoxLayout()
        outer_info_layout.addWidget(info_group_widget)
        self.playlist_scroll_area = QScrollArea()
        self.playlist_scroll_area.setWidgetResizable(True)
        self.playlist_view_widget = QWidget()
        self.video_list_layout = QVBoxLayout(self.playlist_view_widget)
        self.video_list_layout.setAlignment(Qt.AlignTop)
        self.playlist_scroll_area.setWidget(self.playlist_view_widget)
        self.playlist_scroll_area.setVisible(False)
        outer_info_layout.addWidget(self.playlist_scroll_area)
        info_group.setLayout(outer_info_layout)
        layout.addWidget(info_group)

        self.action_buttons_layout = QHBoxLayout()
        self.download_now_button = QPushButton("Download Now")
        self.download_now_button.clicked.connect(self.start_direct_download)
        self.add_to_queue_button = QPushButton("Add to Queue")
        self.add_to_queue_button.clicked.connect(self.add_selected_to_queue)
        self.clear_info_button = QPushButton("Clear Info")
        self.clear_info_button.clicked.connect(self.reset_info_fields)
        self.action_buttons_layout.addWidget(self.download_now_button)
        self.action_buttons_layout.addWidget(self.add_to_queue_button)
        self.action_buttons_layout.addWidget(self.clear_info_button)
        self.action_widget = QWidget()
        self.action_widget.setLayout(self.action_buttons_layout)
        self.action_widget.setEnabled(False)
        layout.addWidget(self.action_widget)

        layout.addStretch(1)

    def init_queue_tab(self):
        layout = QVBoxLayout(self.queue_tab)
        self.queue_table = QTableWidget()
        self.queue_table.setColumnCount(3)
        self.queue_table.setHorizontalHeaderLabels(["Title", "Quality", "Format"])
        self.queue_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.queue_table.verticalHeader().setVisible(False)
        layout.addWidget(self.queue_table)
        queue_controls = QHBoxLayout()
        self.start_queue_button = QPushButton("Start Queue Download")
        self.start_queue_button.clicked.connect(self.start_queue_download)
        self.clear_queue_button = QPushButton("Clear Queue")
        self.clear_queue_button.clicked.connect(self.clear_queue)
        queue_controls.addWidget(self.start_queue_button)
        queue_controls.addWidget(self.clear_queue_button)
        layout.addLayout(queue_controls)

    def init_history_tab(self):
        layout = QVBoxLayout(self.history_tab)
        self.history_table = QTableWidget()
        self.history_table.setColumnCount(3)
        self.history_table.setHorizontalHeaderLabels(["Title", "URL", "Date"])
        self.history_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.history_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.history_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.history_table.verticalHeader().setVisible(False)
        layout.addWidget(self.history_table)
        history_controls = QHBoxLayout()
        self.clear_history_button = QPushButton("Clear History")
        self.clear_history_button.clicked.connect(self.clear_history)
        history_controls.addStretch(1)
        history_controls.addWidget(self.clear_history_button)
        layout.addLayout(history_controls)

    def init_settings_tab(self):
        layout = QVBoxLayout(self.settings_tab)
        layout.setAlignment(Qt.AlignTop)

        path_group = QGroupBox("Default Download Folder")
        path_layout = QHBoxLayout()
        self.path_edit = QLineEdit(self.settings.value("outputPath", "", str))
        path_button = QPushButton("Browse...")
        path_button.clicked.connect(self.browse_settings_path)
        path_layout.addWidget(self.path_edit)
        path_layout.addWidget(path_button)
        path_group.setLayout(path_layout)
        layout.addWidget(path_group)

        filename_group = QGroupBox("Filename Template (yt-dlp format)")
        filename_layout = QVBoxLayout()
        self.filename_template_edit = QLineEdit(self.settings.value("filenameTemplate", "%(title)s [%(id)s].%(ext)s", str))
        filename_layout.addWidget(self.filename_template_edit)
        filename_group.setLayout(filename_layout)
        layout.addWidget(filename_group)

        rate_limit_group = QGroupBox("Download Speed Limit (e.g., 500K, 2M)")
        rate_limit_layout = QVBoxLayout()
        self.rate_limit_edit = QLineEdit(self.settings.value("rateLimit", "", str))
        rate_limit_layout.addWidget(self.rate_limit_edit)
        rate_limit_group.setLayout(rate_limit_layout)
        layout.addWidget(rate_limit_group)

        save_button = QPushButton("Save Settings")
        save_button.clicked.connect(self.save_settings)
        layout.addWidget(save_button, 0, Qt.AlignRight)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        urls = event.mimeData().urls()
        if urls:
            self.url_input.setText(urls[0].toLocalFile() if urls[0].isLocalFile() else urls[0].toString())
            self.fetch_video_info()

    def fetch_video_info(self):
        url = self.url_input.text()
        if not url: return
        self.reset_info_fields()
        self.status_label.setText("Fetching information...")
        self.fetch_button.setEnabled(False)
        
        self.info_thread = InfoFetcherThread(url)
        self.info_thread.finished.connect(self.on_info_fetched)
        self.info_thread.error.connect(self.on_info_fetch_error)
        self.info_thread.start()

    def on_info_fetch_error(self, error_message):
        self.status_label.setText(f"Error fetching info: {error_message}")
        self.fetch_button.setEnabled(True)

    def on_info_fetched(self, info):
        if not info:
            self.on_info_fetch_error("No information returned.")
            return

        self.fetched_info = info
        is_playlist = 'entries' in info and info.get('entries')

        if is_playlist:
            self.playlist_items = [entry for entry in info['entries'] if entry and not entry.get('is_live')]
            if not self.playlist_items:
                self.status_label.setText("Playlist contains no valid videos.")
                self.fetch_button.setEnabled(True)
                return
            
            first_video_url = self.playlist_items[0].get('url') or self.playlist_items[0].get('webpage_url')
            with yt_dlp.YoutubeDL({'quiet': True}) as ydl_single:
                first_video_info = ydl_single.extract_info(first_video_url, download=False)
            self.update_ui_with_video_info(first_video_info)
            self.fetched_info = first_video_info
            self.populate_playlist_view()
            self.status_label.setText(f"Playlist fetched: {len(self.playlist_items)} videos.")
        else: # Single Video
            if info.get('is_live'):
                self.status_label.setText("Live streams cannot be downloaded.")
                self.fetch_button.setEnabled(True)
                return
            self.playlist_items = [info]
            self.update_ui_with_video_info(info)
            self.status_label.setText("Video info fetched successfully!")

        self.action_widget.setEnabled(True)
        self.fetch_button.setEnabled(True)

    def update_ui_with_video_info(self, video_info):
        self.video_title.setText(f"Title: {video_info.get('title', 'N/A')}")
        duration = video_info.get('duration')
        if duration:
            minutes, seconds = divmod(int(duration), 60)
            self.video_duration.setText(f"Duration: {minutes}m {seconds}s")
        else:
            self.video_duration.setText("Duration: N/A")

        thumbnail_url = video_info.get('thumbnail')
        if thumbnail_url:
            self.thumbnail_downloader = ThumbnailDownloaderThread(thumbnail_url)
            self.thumbnail_downloader.finished.connect(self.set_thumbnail)
            self.thumbnail_downloader.start()
        
        self.resolution_combo.clear()
        resolutions = sorted(
            list(set(f['height'] for f in video_info.get('formats', []) if f.get('height') and f.get('vcodec') != 'none')),
            reverse=True
        )
        if resolutions:
            self.resolution_combo.addItems([f"{h}p" for h in resolutions])
            self.resolution_combo.setEnabled(True)
        else:
            self.resolution_combo.setEnabled(False)
        
        self.toggle_resolution_box()
        self.update_file_size()

    def set_thumbnail(self, pixmap):
        if not pixmap.isNull():
            self.thumbnail_label.setPixmap(pixmap.scaled(self.thumbnail_label.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))
    
    def clear_playlist_view(self):
        while self.video_list_layout.count():
            child = self.video_list_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

    def populate_playlist_view(self):
        self.clear_playlist_view()
        for entry in self.playlist_items:
            checkbox = QCheckBox(f"{entry.get('title', 'Untitled')}")
            checkbox.setChecked(True)
            self.video_list_layout.addWidget(checkbox)
        self.playlist_scroll_area.setVisible(True)

    def format_file_size(self, size_bytes):
        if size_bytes is None or size_bytes == 0:
            return "N/A"
        size_name = ("B", "KB", "MB", "GB", "TB")
        i = int(math.floor(math.log(size_bytes, 1024)))
        p = math.pow(1024, i)
        s = round(size_bytes / p, 2)
        return f"{s} {size_name[i]}"

    def update_file_size(self, *args):
        if not self.fetched_info or 'formats' not in self.fetched_info:
            self.file_info.setText("Estimated File Size: N/A")
            return

        total_size = 0
        formats = self.fetched_info['formats']
        
        if "Audio" in self.format_combo.currentText():
            best_audio = max([f for f in formats if f.get('acodec') != 'none' and f.get('vcodec') == 'none'], 
                             key=lambda x: x.get('abr', 0), default=None)
            if best_audio:
                total_size = best_audio.get('filesize') or best_audio.get('filesize_approx')
        else:
            selected_height_str = self.resolution_combo.currentText()
            if not selected_height_str: return
            selected_height = int(selected_height_str.replace('p', ''))
            
            matching_videos = [f for f in formats if f.get('height') == selected_height and f.get('vcodec') != 'none']
            best_video = max(matching_videos, key=lambda x: x.get('tbr', 0), default=None)
            if best_video:
                total_size += best_video.get('filesize') or best_video.get('filesize_approx') or 0

            audio_formats = [f for f in formats if f.get('acodec') != 'none' and f.get('vcodec') == 'none']
            if audio_formats:
                best_audio = max(audio_formats, key=lambda x: x.get('abr', 0))
                total_size += best_audio.get('filesize') or best_audio.get('filesize_approx') or 0

        self.file_info.setText(f"Estimated File Size: {self.format_file_size(total_size)}")

    def toggle_resolution_box(self):
        is_audio = "Audio" in self.format_combo.currentText()
        self.resolution_combo.setVisible(not is_audio)
        self.resolution_label.setVisible(not is_audio)
        self.update_file_size()
        
    def get_selected_items_from_downloader_tab(self):
        selected_items = []
        if self.playlist_scroll_area.isVisible():
            for i in range(self.video_list_layout.count()):
                checkbox = self.video_list_layout.itemAt(i).widget()
                if checkbox and checkbox.isChecked():
                    selected_items.append(self.playlist_items[i])
        elif self.playlist_items:
            selected_items.append(self.playlist_items[0])
        return selected_items

    def add_selected_to_queue(self):
        selected_items = self.get_selected_items_from_downloader_tab()
        if not selected_items:
            self.status_label.setText("No items selected to add.")
            return

        for item in selected_items:
            row_position = self.queue_table.rowCount()
            self.queue_table.insertRow(row_position)
            
            item['selected_quality'] = self.resolution_combo.currentText() if "Video" in self.format_combo.currentText() else "Audio"
            item['selected_format_text'] = self.format_combo.currentText()
            
            self.queue_table.setItem(row_position, 0, QTableWidgetItem(item.get('title', 'N/A')))
            self.queue_table.setItem(row_position, 1, QTableWidgetItem(item['selected_quality']))
            self.queue_table.setItem(row_position, 2, QTableWidgetItem(item['selected_format_text']))
            
            self.download_queue.append(item.copy())
        
        self.tab_bar.setCurrentIndex(1)
        self.status_label.setText(f"Added {len(selected_items)} item(s) to the queue.")

    def start_direct_download(self):
        if self.is_downloading: return
        
        self.download_queue = self.get_selected_items_from_downloader_tab()
        
        if not self.download_queue:
            self.status_label.setText("No items selected to download.")
            return
        
        for item in self.download_queue:
            item['selected_quality'] = self.resolution_combo.currentText() if "Video" in self.format_combo.currentText() else "Audio"
            item['selected_format_text'] = self.format_combo.currentText()

        self.start_queue_download(is_direct=True)

    def start_queue_download(self, is_direct=False):
        if self.is_downloading: return
        if not self.download_queue:
            self.status_label.setText("Download queue is empty.")
            return

        if not self.output_path:
            self.status_label.setText("Please set a default download folder in Settings.")
            self.tab_bar.setCurrentIndex(3)
            return
            
        self.is_downloading = True
        self.set_controls_enabled(False)
        self.process_download_queue(is_direct)

    def process_download_queue(self, is_direct=False):
        if not self.download_queue or not self.is_downloading:
            self.on_all_downloads_finished()
            return
        
        video_to_download = self.download_queue.pop(0)
        if not is_direct:
            self.queue_table.removeRow(0)
        
        title = video_to_download.get('title', 'Unknown Video')
        format_text = video_to_download.get('selected_format_text', '')
        
        format_selector = ""
        if "Audio" in format_text:
            format_selector = 'bestaudio/best'
            video_to_download['selected_format_type'] = 'audio'
            video_to_download['selected_format_ext'] = 'mp3' if 'MP3' in format_text else 'm4a'
        else:
            height = video_to_download.get('selected_quality', '720p')[:-1]
            format_selector = f'bestvideo[height<={height}]+bestaudio/best'
            video_to_download['selected_format_type'] = 'video'
            video_to_download['selected_format_ext'] = 'mkv' if 'MKV' in format_text else 'mp4'

        self.status_label.setText(f"Downloading: {title}")
        self.reset_progress_bar(determinate=True)
        
        with yt_dlp.YoutubeDL({'quiet': True}) as ydl:
            full_video_info = ydl.extract_info(video_to_download.get('webpage_url') or video_to_download.get('url'), download=False)
        
        full_video_info.update(video_to_download)

        self.downloader_thread = DownloaderThread(full_video_info, format_selector, self.output_path, self.filename_template, self.rate_limit)
        self.downloader_thread.progress.connect(self.progress_bar.setValue)
        self.downloader_thread.stats.connect(self.update_stats)
        self.downloader_thread.postprocessing.connect(self.on_postprocessing)
        self.downloader_thread.finished.connect(lambda s, m, v, direct=is_direct: self.on_one_download_finished(s, m, v, direct))
        self.downloader_thread.start()

    def on_postprocessing(self, message):
        self.status_label.setText(message)
        self.progress_bar.setRange(0, 0)

    def on_one_download_finished(self, success, message, video_info, is_direct):
        if success:
            self.add_to_history(video_info)
        else:
            print(f"Failed to download {video_info.get('title', 'N/A')}: {message}")
        
        if self.is_downloading:
            self.process_download_queue(is_direct)

    def on_all_downloads_finished(self):
        self.status_label.setText("All downloads completed!")
        self.is_downloading = False
        self.set_controls_enabled(True)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(100)
        self.open_folder_button.setVisible(True)

    def stop_download(self):
        self.is_downloading = False
        self.download_queue.clear()
        self.queue_table.setRowCount(0)
        if hasattr(self, 'downloader_thread') and self.downloader_thread.isRunning():
            self.downloader_thread.terminate()
            self.downloader_thread.wait()
        
        self.status_label.setText("Download process stopped.")
        self.reset_progress_bar()
        self.set_controls_enabled(True)

    def set_controls_enabled(self, enabled):
        self.fetch_button.setEnabled(enabled)
        self.action_widget.setEnabled(self.fetched_info is not None and enabled)
        self.start_queue_button.setEnabled(enabled)
        self.clear_queue_button.setEnabled(enabled)
        self.stop_button.setEnabled(not enabled)

    def update_stats(self, speed, eta):
        self.speed_label.setText(f"Speed: {speed}")
        self.eta_label.setText(f"ETA: {eta}")

    def reset_progress_bar(self, determinate=False):
        if determinate:
            self.progress_bar.setRange(0, 100)
            self.progress_bar.setValue(0)
        else:
            self.progress_bar.setRange(0, 0)
        self.speed_label.setText("Speed: N/A")
        self.eta_label.setText("ETA: N/A")

    def reset_info_fields(self):
        self.video_title.setText("Title:")
        self.video_duration.setText("Duration:")
        self.file_info.setText("Estimated File Size:")
        self.thumbnail_label.clear()
        self.thumbnail_label.setStyleSheet("border: 1px solid #43b581; background-color: #2C2F33;")
        self.resolution_combo.clear()
        self.clear_playlist_view()
        self.playlist_scroll_area.setVisible(False)
        self.action_widget.setEnabled(False)
        self.fetched_info = None
        self.playlist_items = []
        self.open_folder_button.setVisible(False)
        self.reset_progress_bar()
        self.progress_bar.setValue(0)

    def select_output_folder(self):
        path = QFileDialog.getExistingDirectory(self, "Select Output Folder")
        if path:
            self.output_path = path
            self.status_label.setText(f"Output folder set to: {path}")
            self.path_edit.setText(path)
            self.settings.setValue("outputPath", path)
            
    def open_download_folder(self):
        path_to_open = self.output_path or self.settings.value("outputPath", "", str)
        if path_to_open and os.path.exists(path_to_open):
            QDesktopServices.openUrl(QUrl.fromLocalFile(path_to_open))
        else:
            self.status_label.setText("Output folder not found.")
            
    def clear_queue(self):
        if self.is_downloading: return
        self.download_queue.clear()
        self.queue_table.setRowCount(0)
        self.status_label.setText("Queue cleared.")

    def load_settings(self):
        self.output_path = self.settings.value("outputPath", "", str)
        self.filename_template = self.settings.value("filenameTemplate", "%(title)s [%(id)s].%(ext)s", str)
        self.rate_limit = self.settings.value("rateLimit", "", str)

    def load_history(self):
        if os.path.exists(self.history_file):
            try:
                with open(self.history_file, 'r', encoding='utf-8') as f:
                    history = json.load(f)
                    for item in history:
                        self.add_history_row(item)
            except (json.JSONDecodeError, IOError) as e:
                print(f"Could not parse history file: {e}")

    def add_to_history(self, video_info):
        history = []
        if os.path.exists(self.history_file):
            try:
                with open(self.history_file, 'r', encoding='utf-8') as f:
                    history = json.load(f)
            except (json.JSONDecodeError, IOError):
                history = []
        
        history_item = {
            'title': video_info.get('title', 'N/A'),
            'url': video_info.get('webpage_url', 'N/A'),
            'date': datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        history.insert(0, history_item)
        self.add_history_row(history_item, at_top=True)
        
        try:
            with open(self.history_file, 'w', encoding='utf-8') as f:
                json.dump(history, f, indent=4, ensure_ascii=False)
        except IOError as e:
            print(f"Could not write to history file: {e}")
            
    def add_history_row(self, item, at_top=False):
        row_position = 0 if at_top else self.history_table.rowCount()
        self.history_table.insertRow(row_position)
        self.history_table.setItem(row_position, 0, QTableWidgetItem(item.get('title')))
        self.history_table.setItem(row_position, 1, QTableWidgetItem(item.get('url')))
        self.history_table.setItem(row_position, 2, QTableWidgetItem(item.get('date')))

    def clear_history(self):
        self.history_table.setRowCount(0)
        if os.path.exists(self.history_file):
            try:
                os.remove(self.history_file)
                self.status_label.setText("History cleared.")
            except OSError as e:
                print(f"Error removing history file: {e}")

    def browse_settings_path(self):
        path = QFileDialog.getExistingDirectory(self, "Select Default Folder")
        if path:
            self.path_edit.setText(path)

    def save_settings(self):
        self.settings.setValue("outputPath", self.path_edit.text())
        self.settings.setValue("filenameTemplate", self.filename_template_edit.text())
        self.settings.setValue("rateLimit", self.rate_limit_edit.text())
        self.load_settings()
        self.status_label.setText("Settings saved successfully.")

    def closeEvent(self, event):
        if self.is_downloading:
            self.stop_download()
        event.accept()

if __name__ == '__main__':
    app = QApplication(sys.argv)
    ex = VideoDownloader()
    ex.show()
    sys.exit(app.exec_())