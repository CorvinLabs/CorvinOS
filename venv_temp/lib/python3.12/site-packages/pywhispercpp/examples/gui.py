import sys
import threading
from datetime import datetime

from PyQt5.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QFileDialog, QProgressBar, QLabel, QFrame,
    QSizePolicy, QTableWidget, QTableWidgetItem, QHeaderView,
    QGroupBox, QFormLayout, QComboBox, QLineEdit, QCheckBox,
    QSpinBox, QDoubleSpinBox, QToolButton, QDialog, QMenu  # Import QMenu
)
from PyQt5.QtCore import Qt, pyqtSignal, QObject
import os
import importlib.metadata

__version__ = importlib.metadata.version('pywhispercpp')

from pywhispercpp.model import Model, Segment
from pywhispercpp.utils import output_txt, output_srt, output_vtt, output_csv  # Import utilities


# --- Available Models ---
# Define a custom order for model sizes
MODEL_SIZE_ORDER = {"tiny": 0, "base": 1, "small": 2, "medium": 3, "large": 4}

UNSORTED_MODELS = [
    "base", "base-q5_1", "base-q8_0", "base.en", "base.en-q5_1",
    "base.en-q8_0", "large-v1", "large-v2", "large-v2-q5_0",
    "large-v2-q8_0", "large-v3", "large-v3-q5_0", "large-v3-turbo",
    "large-v3-turbo-q5_0", "large-v3-turbo-q8_0", "medium",
    "medium-q5_0", "medium-q8_0", "medium.en", "medium.en-q5_0",
    "medium.en-q8_0", "small", "small-q5_1", "small-q8_0", "small.en",
    "small.en-q5_1", "small.en-q8_0", "tiny", "tiny-q5_1", "tiny-q8_0",
    "tiny.en", "tiny.en-q5_1", "tiny.en-q8_0",
]


# Custom sort key function
def get_model_sort_key(model_name):
    # Extract the base model name (e.g., "tiny.en" -> "tiny", "large-v3-turbo" -> "large")
    base_name = model_name.split('.')[0].split('-')[0]
    # Return a tuple for multi-level sorting: (size_order, full_model_name_for_secondary_sort)
    return (MODEL_SIZE_ORDER.get(base_name, 99), model_name)  # 99 for any unexpected names


# Sort the models
AVAILABLE_MODELS = sorted(UNSORTED_MODELS, key=get_model_sort_key)

# --- Retouched Minimal Stylesheet ---
STYLESHEET = """
/* General Application Styles */
QWidget {
    background-color: #f5f5f5; /* Light gray background */
    color: #333333; /* Dark text */
    font-family: Arial, sans-serif;
    font-size: 14px;
}

/* Buttons */
QPushButton {
    background-color: #e0e0e0; /* Slightly darker gray for buttons */
    color: #333333;
    border: 1px solid #c0c0c0; /* Light gray border */
    padding: 6px 12px;
    border-radius: 3px; /* Slightly rounded corners */
    outline: none; /* Remove focus outline for a cleaner look */
}

QPushButton:hover {
    background-color: #d0d0d0; /* Darker on hover */
}

QPushButton:pressed {
    background-color: #c0c0c0; /* Even darker when pressed */
}

QPushButton:disabled {
    background-color: #f0f0f0;
    color: #aaaaaa;
    border-color: #e0e0e0;
}

/* Specific styling for the Transcribe button (Call to Action) */
QPushButton#TranscribeButton {
    background-color: #007bff; /* Vibrant blue */
    color: #ffffff; /* White text */
    font-weight: bold;
    font-size: 15px; /* Slightly larger font */
    padding: 8px 18px; /* More padding */
    border: 1px solid #007bff; /* Matching border */
    border-radius: 4px;
}

QPushButton#TranscribeButton:hover {
    background-color: #0056b3; /* Darker blue on hover */
    border-color: #0056b3;
}

QPushButton#TranscribeButton:pressed {
    background-color: #004085; /* Even darker blue when pressed */
    border-color: #004085;
}

QPushButton#TranscribeButton:disabled {
    background-color: #cccccc; /* Light gray for disabled state */
    color: #888888;
    border-color: #cccccc;
}

/* Stop button */
QPushButton#StopButton {
    background-color: #ff0000; /* Red for stop */
    color: #ffffff; /* White text */
    font-weight: bold;
    font-size: 15px; /* Slightly larger font */
    padding: 8px 18px; /* More padding */
    border: 1px solid #ff0000; /* Matching border */
    border-radius: 4px;
}

QPushButton#StopButton:hover {
    background-color: #cc0000; /* Darker red on hover */
    border-color: #cc0000;
}

QPushButton#StopButton:pressed {
    background-color: #990000; /* Even darker red when pressed */
    border-color: #990000;
}

QPushButton#StopButton:disabled {
    background-color: #cccccc; /* Light gray for disabled state */
    color: #888888;
    border-color: #cccccc;
}

/* ToolButton for accordion header */
QToolButton {
    background-color: transparent; /* Keep it transparent */
    border: none;
    padding: 5px 0; /* Some padding for clickable area */
    font-weight: bold;
    color: #444444; /* Darker text for emphasis */
    text-align: left;
    outline: none; /* Remove focus outline */
}

QToolButton::menu-indicator {
    image: none; /* Hide default menu indicator */
}

/* Table Widget */
QTableWidget {
    background-color: #ffffff;
    border: 1px solid #d0d0d0; /* Slightly softer border */
    gridline-color: #e8e8e8; /* Very light grid lines */
    border-radius: 3px;
}

QTableWidget::item {
    padding: 4px;
    border-bottom: 1px solid #f5f5f5; /* Match general background for subtle row separation */
}

/* Table Header */
QHeaderView::section {
    background-color: #e8e8e8; /* Light header background */
    color: #333333;
    padding: 5px;
    border: 1px solid #d0d0d0;
    font-weight: bold;
}

/* Labels */
QLabel {
    color: #333333;
}

/* Specific styling for the main title label */
QLabel#TitleLabel {
    font-size: 18px; /* Slightly smaller than previous '20px' to integrate better */
    font-weight: bold;
    color: #0056b3; /* A slightly darker blue for main title */
    padding-bottom: 3px;
    margin-bottom: 5px;
    border-bottom: 1px solid #e0e0e0;
}

/* Progress Bar */
QProgressBar {
    border: 1px solid #bbbbbb;
    border-radius: 3px;
    text-align: center;
    background-color: #e6e6e6;
    color: #333333;
}

QProgressBar::chunk {
    background-color: #4CAF50; /* A pleasant green */
    border-radius: 2px;
}

/* Input Widgets (ComboBox, LineEdit, SpinBox, DoubleSpinBox) */
QComboBox, QLineEdit, QSpinBox, QDoubleSpinBox {
    border: 1px solid #cccccc;
    padding: 3px;
    background-color: #ffffff;
    color: #333333;
    border-radius: 3px; /* Apply rounded corners */
}

/* File label should visually match text inputs */
QLabel#file_label {
    border: 1px solid #cccccc;
    padding: 3px;
    background-color: #ffffff;
    border-radius: 3px;
}

QFrame {
    border: none; /* Keep frames invisible unless needed for structure */
}

/* Status Bar Label */
QLabel#status_bar_label {
    background-color: #e0e0e0; /* Light grey background */
    border-top: 1px solid #cccccc; /* Separator */
    color: #444444; /* Darker text */
    padding: 3px 5px; /* Add internal padding */
    font-size: 13px; /* Slightly smaller font */
}
"""


# --- Communication Object for Threading ---
class WorkerSignals(QObject):
    """
    Defines signals available from a running worker thread.
    Supported signals are:
    - finished: No data
    - error: tuple (exctype, value, traceback.format_exc())
    - result: list (the transcribed segments)
    - progress: int (0-100)
    - status_update: str
    """
    finished = pyqtSignal()
    error = pyqtSignal(tuple)
    segment = pyqtSignal(Segment)
    result = pyqtSignal(list)
    progress = pyqtSignal(int)
    status_update = pyqtSignal(str)


# --- Worker Thread for Transcription ---
class PyWhisperCppWorker(threading.Thread):

    def __init__(self, audio_file_path, model_name, **transcribe_params):
        super().__init__()
        self.audio_file_path = audio_file_path
        self.model_name = model_name
        self.transcribe_params = transcribe_params
        self.signals = WorkerSignals()
        self._is_running = False

    def run(self):
        """
        Executes the transcription process.
        """
        try:
            self._is_running = True
            self.signals.status_update.emit(f"Loading model: {self.model_name}...")

            # pywhispercpp will download the specified model if not found
            model_init_params = {}
            if 'n_threads' in self.transcribe_params and self.transcribe_params['n_threads'] is not None:
                model_init_params['n_threads'] = self.transcribe_params['n_threads']
                # Remove from transcribe_params as it's a model init param
                del self.transcribe_params['n_threads']

            model = Model(self.model_name, **model_init_params)

            self.signals.status_update.emit("Model loaded. Starting transcription...")

            def new_segment_callback(segment):
                if not self._is_running:
                    raise RuntimeError("Transcription manually stopped")
                self.signals.segment.emit(segment)

            segments = model.transcribe(self.audio_file_path,
                                        new_segment_callback=new_segment_callback,
                                        progress_callback=lambda progress: self.signals.progress.emit(progress),
                                        **self.transcribe_params)

            self.signals.status_update.emit("Transcription complete!")
            self.signals.result.emit(segments)

        except Exception as e:
            print(e)
            self.signals.status_update.emit(f"Error: {str(e)}")
            self.signals.error.emit((type(e), e, str(e)))
        finally:
            self._is_running = False
            self.signals.finished.emit()

    def stop(self):
        self._is_running = False


# --- Main Application Window ---
class TranscriptionApp(QWidget):
    def __init__(self):
        super().__init__()
        self.selected_file_path = None
        self.whisper_thread = None
        # Settings widgets
        self.model_combo = None
        self.language_input = None
        self.translate_checkbox = None
        self.n_threads_spinbox = None
        self.no_context_checkbox = None
        self.temperature_spinbox = None
        self.settings_content_frame = None  # Frame to hold collapsible settings
        self.toggle_settings_button = None  # Button to toggle settings
        self.status_bar_label = None  # New label for the status bar
        self.about_button = None  # About button
        self.segments = []  # Store segments for export
        self.copy_text_button = None  # New button for copy text

        self.initUI()

    def initUI(self):
        """
        Initializes the user interface of the application.
        """
        self.setWindowTitle('PyWhisperCpp Simple GUI')
        self.setGeometry(100, 100, 450, 500)
        # Apply the updated stylesheet
        self.setStyleSheet(STYLESHEET)

        # Main vertical layout
        main_layout = QVBoxLayout()
        # Set bottom margin to 0 for the main layout to ensure status bar is flush
        main_layout.setContentsMargins(4, 4, 4, 0)
        main_layout.setSpacing(10)

        # --- Header (Title + About Button) ---
        header_layout = QHBoxLayout()
        title_label = QLabel("PyWhisperCpp Simple GUI")  # Updated main title label
        title_label.setObjectName("TitleLabel")  # Add objectName for styling
        title_label.setAlignment(Qt.AlignLeft)  # Keep title centered within its allocated space

        # Adding stretch before and after title to center it
        # header_layout.addStretch()
        header_layout.addWidget(title_label)
        header_layout.addStretch()

        # About button
        self.about_button = QPushButton("About")
        self.about_button.clicked.connect(self.show_about_dialog)
        # Removed setFixedSize to allow text to fit, or adjust as needed
        # self.about_button.setFixedSize(50, 25)
        header_layout.addWidget(self.about_button)  # Add it to the header layout

        main_layout.addLayout(header_layout)  # Add the combined header to main layout

        # --- File Selection Area ---
        file_frame = QFrame()
        file_layout = QHBoxLayout(file_frame)
        file_layout.setContentsMargins(0, 0, 0, 0)
        file_layout.setSpacing(10)

        self.select_button = QPushButton("Select Audio File")
        self.select_button.clicked.connect(self.select_file)

        self.file_label = QLabel("No file selected.")
        self.file_label.setObjectName("file_label")  # Added objectName for styling
        self.file_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

        file_layout.addWidget(self.select_button)
        file_layout.addWidget(self.file_label)
        main_layout.addWidget(file_frame)

        # --- Collapsible Settings Section ---
        settings_group = QGroupBox()  # No title here, using QToolButton for title
        settings_group_layout = QVBoxLayout(settings_group)
        settings_group_layout.setContentsMargins(5, 5, 5, 5)

        # Custom title bar for the collapsible group box
        header_layout_settings = QHBoxLayout()  # Renamed to avoid clash
        self.toggle_settings_button = QToolButton(settings_group)
        self.toggle_settings_button.setText("Transcription Settings")
        self.toggle_settings_button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.toggle_settings_button.setArrowType(Qt.RightArrow)
        self.toggle_settings_button.setCheckable(True)
        self.toggle_settings_button.setChecked(False)  # Start collapsed
        self.toggle_settings_button.clicked.connect(self.toggle_settings_visibility)

        header_layout_settings.addWidget(self.toggle_settings_button)
        header_layout_settings.addStretch()  # Push button to left

        settings_group_layout.addLayout(header_layout_settings)

        # Frame to hold the actual settings form (this will be hidden/shown)
        self.settings_content_frame = QFrame()
        settings_form_layout = QFormLayout(self.settings_content_frame)
        settings_form_layout.setContentsMargins(15, 5, 10, 10)
        settings_form_layout.setSpacing(8)

        # Model Selection
        self.model_combo = QComboBox()
        self.model_combo.addItems(AVAILABLE_MODELS)
        self.model_combo.setCurrentText("tiny")  # Default to 'tiny' as requested
        settings_form_layout.addRow("Model:", self.model_combo)

        # Language Input
        self.language_input = QLineEdit()
        self.language_input.setPlaceholderText('e.g., "en", "es", or leave empty for auto-detect')
        self.language_input.setText("")  # Default to auto-detect
        settings_form_layout.addRow("Language:", self.language_input)

        # Translate Checkbox
        self.translate_checkbox = QCheckBox("Translate to English")
        self.translate_checkbox.setChecked(False)  # Default
        settings_form_layout.addRow("Translate:", self.translate_checkbox)

        # N Threads Spinbox
        self.n_threads_spinbox = QSpinBox()
        self.n_threads_spinbox.setRange(1, os.cpu_count() if os.cpu_count() else 8)  # Max threads based on CPU cores
        self.n_threads_spinbox.setValue(4)  # Sensible default
        settings_form_layout.addRow("Number of Threads:", self.n_threads_spinbox)

        # No Context Checkbox
        self.no_context_checkbox = QCheckBox("No Context (do not use past transcription)")
        self.no_context_checkbox.setChecked(False)  # Default
        settings_form_layout.addRow("No Context:", self.no_context_checkbox)

        # Temperature Spinbox
        self.temperature_spinbox = QDoubleSpinBox()
        self.temperature_spinbox.setRange(0.0, 1.0)
        self.temperature_spinbox.setSingleStep(0.1)
        self.temperature_spinbox.setValue(0.0)  # Default
        settings_form_layout.addRow("Temperature:", self.temperature_spinbox)

        settings_group_layout.addWidget(self.settings_content_frame)
        self.settings_content_frame.setVisible(False)  # Initially hidden

        main_layout.addWidget(settings_group)

        # --- Transcription Button ---
        self.transcribe_button = QPushButton("Transcribe")
        self.transcribe_button.setObjectName("TranscribeButton")  # Add objectName for styling
        self.transcribe_button.setEnabled(False)
        self.transcribe_button.clicked.connect(self.start_transcription)
        main_layout.addWidget(self.transcribe_button)

        # --- Stop Button ---
        self.stop_button = QPushButton("Stop")
        self.stop_button.setObjectName("StopButton")  # Add objectName for styling
        self.stop_button.setEnabled(True)
        self.stop_button.setVisible(False)
        self.stop_button.clicked.connect(self.stop_transcription)
        main_layout.addWidget(self.stop_button)

        # --- Progress Bar ---
        progress_frame = QFrame()
        progress_layout = QVBoxLayout(progress_frame)
        progress_layout.setContentsMargins(0, 5, 0, 5)
        progress_layout.setSpacing(5)

        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)

        progress_layout.addWidget(self.progress_bar)
        main_layout.addWidget(progress_frame)

        # --- Transcription Output Table ---
        output_label = QLabel("Transcription Output:")
        main_layout.addWidget(output_label)

        self.results_table = QTableWidget()
        self.results_table.setColumnCount(3)
        self.results_table.setHorizontalHeaderLabels(["Start Time", "End Time", "Text"])
        header = self.results_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.Stretch)
        self.results_table.verticalHeader().setVisible(False)
        main_layout.addWidget(self.results_table)

        # --- Output Buttons (Export and Copy) ---
        output_buttons_layout = QHBoxLayout()
        output_buttons_layout.addStretch()  # Pushes buttons to the right

        # Export Button with Menu
        self.export_button = QPushButton("Export as...")
        self.export_button.setEnabled(False)
        self.export_menu = QMenu(self)

        self.export_action_txt = self.export_menu.addAction("Plain Text (.txt)")
        self.export_action_srt = self.export_menu.addAction("SRT Subtitle (.srt)")
        self.export_action_vtt = self.export_menu.addAction("VTT Subtitle (.vtt)")
        self.export_action_csv = self.export_menu.addAction("CSV (.csv)")

        self.export_action_txt.triggered.connect(lambda: self.export_transcription("txt"))
        self.export_action_srt.triggered.connect(lambda: self.export_transcription("srt"))
        self.export_action_vtt.triggered.connect(lambda: self.export_transcription("vtt"))
        self.export_action_csv.triggered.connect(lambda: self.export_transcription("csv"))

        self.export_button.setMenu(self.export_menu)
        output_buttons_layout.addWidget(self.export_button)

        # Copy Text Button
        self.copy_text_button = QPushButton("Copy Text")
        self.copy_text_button.setEnabled(False)  # Initially disabled
        self.copy_text_button.clicked.connect(self.copy_all_text_to_clipboard)  # Connect to new method
        output_buttons_layout.addWidget(self.copy_text_button)

        main_layout.addLayout(output_buttons_layout)

        # --- Status Bar at the very bottom ---
        self.status_bar_label = QLabel("Ready.")
        self.status_bar_label.setObjectName("status_bar_label")  # Add objectName for styling
        self.status_bar_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.status_bar_label.setContentsMargins(5, 2, 5, 2)
        main_layout.addWidget(self.status_bar_label)

        self.setLayout(main_layout)

    def toggle_settings_visibility(self):
        """Toggles the visibility of the settings content frame and updates the arrow."""
        is_visible = self.settings_content_frame.isVisible()
        self.settings_content_frame.setVisible(not is_visible)
        if not is_visible:
            self.toggle_settings_button.setArrowType(Qt.DownArrow)
        else:
            self.toggle_settings_button.setArrowType(Qt.RightArrow)

    def select_file(self):
        """
        Opens a file dialog to select an audio file.
        """
        options = QFileDialog.Options()
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Select a Media File", "",
            "All Files (*)",
            options=options
        )
        if file_path:
            self.selected_file_path = file_path
            self.file_label.setText(f"Selected: {os.path.basename(file_path)}")
            self.transcribe_button.setEnabled(True)
            self.results_table.setRowCount(0)
            self.export_button.setEnabled(False)  # Disable export until transcription
            self.copy_text_button.setEnabled(False)  # Disable copy until transcription
            self.update_status("File selected: " + os.path.basename(file_path))  # Update new status bar

    def start_transcription(self):
        """
        Starts the transcription process in a separate thread, passing selected settings.
        """
        if self.selected_file_path:
            self.transcribe_button.setVisible(False)
            self.stop_button.setVisible(True)
            self.select_button.setEnabled(False)
            self.progress_bar.setVisible(True)
            self.progress_bar.setValue(0)
            self.results_table.setRowCount(0)
            self.export_button.setEnabled(False)  # Disable export during transcription
            self.copy_text_button.setEnabled(False)  # Disable copy during transcription
            self.update_status("Starting transcription...")
            self.segments = []  # Clear segments for new transcription

            # Gather settings from GUI widgets
            selected_model = self.model_combo.currentText()
            transcribe_params = {
                "language": self.language_input.text() if self.language_input.text() else None,
                "translate": self.translate_checkbox.isChecked(),
                "n_threads": self.n_threads_spinbox.value(),
                "no_context": self.no_context_checkbox.isChecked(),
                "temperature": self.temperature_spinbox.value(),
            }
            # Remove None values to use pywhispercpp defaults where applicable
            transcribe_params = {k: v for k, v in transcribe_params.items() if v is not None}

            # Create and start the worker thread
            self.whisper_thread = PyWhisperCppWorker(
                self.selected_file_path,
                selected_model,
                **transcribe_params
            )
            self.whisper_thread.signals.result.connect(self.on_transcription_result)
            self.whisper_thread.signals.segment.connect(self.on_new_segment)
            self.whisper_thread.signals.finished.connect(self.on_transcription_finished)
            self.whisper_thread.signals.error.connect(self.on_transcription_error)
            self.whisper_thread.signals.progress.connect(self.update_progress)
            self.whisper_thread.signals.status_update.connect(self.update_status)
            self.whisper_thread.start()

    def stop_transcription(self):
        if self.whisper_thread:
            self.whisper_thread.stop()
            # self.transcribe_button.setVisible(True)
            # self.stop_button.setVisible(False)
            # self.select_button.setEnabled(True)
            # self.progress_bar.setVisible(False)
            # self.on_transcription_finished()

    def update_progress(self, value):
        self.progress_bar.setValue(value)
        # Update status bar with progress if not already showing a specific message
        if not self.status_bar_label.text().startswith("Error:") and \
                not self.status_bar_label.text().startswith("Finished.") and \
                not self.status_bar_label.text().startswith("Text exported") and \
                not self.status_bar_label.text().startswith("Text copied"):  # Updated check
            self.update_status(f"Progress: {value}%")

    def update_status(self, status_text):
        # Update the new status bar label directly
        if self.status_bar_label:
            self.status_bar_label.setText(status_text)
            # Polish stylesheet for status_bar_label to ensure updates are reflected
            self.status_bar_label.style().unpolish(self.status_bar_label)
            self.status_bar_label.style().polish(self.status_bar_label)

    def format_time(self, milliseconds):
        """Converts milliseconds to HH:MM:SS.ms format."""
        seconds_total = milliseconds / 1000
        minutes, seconds = divmod(seconds_total, 60)
        hours, minutes = divmod(minutes, 60)
        return f"{int(hours):02d}:{int(minutes):02d}:{seconds:06.3f}"

    def on_new_segment(self, segment):
        row_position = self.results_table.rowCount()
        self.results_table.insertRow(row_position)
        start_time_str = self.format_time(segment.t0)
        end_time_str = self.format_time(segment.t1)

        start_item = QTableWidgetItem(start_time_str)
        end_item = QTableWidgetItem(end_time_str)
        text_item = QTableWidgetItem(segment.text.strip())

        self.results_table.setItem(row_position, 0, start_item)
        self.results_table.setItem(row_position, 1, end_item)
        self.results_table.setItem(row_position, 2, text_item)

    def on_transcription_result(self, segments):
        """
        Populates the results table with the transcription segments.
        Stores segments for export.
        """
        self.segments = segments  # Store segments
        self.export_button.setEnabled(True if segments else False)  # Enable export if segments exist
        self.copy_text_button.setEnabled(True if segments else False)  # Enable copy if segments exist

    def on_transcription_finished(self):
        """
        Cleans up after the transcription thread is finished.
        """
        self.transcribe_button.setVisible(True)
        self.transcribe_button.setEnabled(True)
        self.stop_button.setVisible(False)
        self.select_button.setEnabled(True)
        self.progress_bar.setVisible(False)
        if self.results_table.rowCount() == 0:
            self.update_status("Finished. No transcription data.")
        else:
            self.update_status("Transcription finished successfully!")
        self.whisper_thread = None

    def on_transcription_error(self, err):
        """
        Displays an error message if transcription fails.
        """
        exctype, value, tb = err
        error_message = f"Error: {value}"
        self.update_status(error_message)  # Update new status bar
        self.on_transcription_finished()

    def export_transcription(self, format_type):
        """
        Handles exporting the transcription to a chosen file format.
        """
        if not self.segments:
            self.update_status("No transcription data to export.")
            return

        file_dialog_filter = {
            "txt": "Plain Text Files (*.txt)",
            "srt": "SRT Subtitle Files (*.srt)",
            "vtt": "VTT Subtitle Files (*.vtt)",
            "csv": "CSV (Comma Separated Values) Files (*.csv)",
        }

        default_file_name = os.path.basename(self.selected_file_path).rsplit('.', 1)[
                                0] + f".{format_type}" if self.selected_file_path else f"transcription.{format_type}"

        options = QFileDialog.Options()
        file_path, _ = QFileDialog.getSaveFileName(
            self, f"Save Transcription as {format_type.upper()}",
            default_file_name,
            file_dialog_filter.get(format_type, "All Files (*)"),
            options=options
        )

        if file_path:
            try:
                # Use pywhispercpp.utils functions based on format_type
                if format_type == "txt":
                    # For TXT, we'll re-use the text from the table or segments
                    all_text = []
                    for segment in self.segments:
                        all_text.append(segment.text.strip())
                    output_txt_content = "\n".join(all_text)
                    with open(file_path, 'w', encoding='utf-8') as f:
                        f.write(output_txt_content)

                elif format_type == "srt":
                    if output_srt:
                        output_srt(self.segments, file_path)
                    else:
                        raise ImportError("pywhispercpp.utils.output_srt not available.")
                elif format_type == "vtt":
                    if output_vtt:
                        output_vtt(self.segments, file_path)
                    else:
                        raise ImportError("pywhispercpp.utils.output_vtt not available.")
                elif format_type == "csv":
                    if output_csv:
                        # For CSV, we need to pass a list of lists/tuples representing rows
                        # pywhispercpp.utils.output_csv expects a list of segments and a file path
                        output_csv(self.segments, file_path)
                    else:
                        raise ImportError("pywhispercpp.utils.output_csv not available.")

                self.update_status(f"Transcription successfully exported to {os.path.basename(file_path)}")
            except Exception as e:
                self.update_status(f"Error exporting to {format_type.upper()}: {e}")
        else:
            self.update_status("Export cancelled.")

    def copy_all_text_to_clipboard(self):
        """
        Concatenates all text from segments and copies it to the clipboard.
        """
        if not self.segments:
            self.update_status("No transcription data to copy.")
            return

        all_text = []
        for segment in self.segments:
            all_text.append(segment.text.strip())

        QApplication.clipboard().setText("\n".join(all_text))
        self.update_status("Text copied to clipboard!")

    def show_about_dialog(self):
        """Opens a small dialog with About information."""
        about_dialog = QDialog(self)
        about_dialog.setWindowTitle("About PyWhisperCPP Simple GUI")
        about_dialog.setFixedSize(400, 220)

        dialog_layout = QVBoxLayout(about_dialog)
        dialog_layout.setContentsMargins(20, 20, 20, 20)

        info_text = QLabel()
        info_text.setTextFormat(Qt.RichText)
        info_text.setText(
            "<b>PyWhisperCPP Simple GUI</b><br>"
            f"Version {__version__}<br>"
            "<br>"
            "A simple graphical user interface for PyWhisperCpp Using PyQt.<br><br>"
            "<a href='https://github.com/absadiki/pywhispercpp'>PyWhisperCpp GitHub repository</a><br>"
            "<br>"
            f"Copyright © {datetime.now().year}"
        )
        info_text.setOpenExternalLinks(True)

        dialog_layout.addWidget(info_text)

        close_button = QPushButton("Close")
        close_button.clicked.connect(about_dialog.accept)
        dialog_layout.addWidget(close_button, alignment=Qt.AlignCenter)

        about_dialog.exec_()


def _main():
    """Main function to run the application."""
    if Model is None:
        print("pywhispercpp is not installed.")
        print("Please install it by running: pip install pywhispercpp")
        print("You also need ffmpeg installed on your system.")
        return

    app = QApplication(sys.argv)
    ex = TranscriptionApp()
    ex.show()
    sys.exit(app.exec_())


if __name__ == '__main__':
    _main()
