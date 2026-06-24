import sys
import os
import json
import re
import time
import csv
import urllib.parse

import requests
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QComboBox, QProgressBar, QTableWidget,
    QTableWidgetItem, QFileDialog, QMessageBox, QHeaderView,
    QGroupBox, QTextEdit, QGridLayout
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QTimer
from PyQt5.QtGui import QFont, QColor


# ─── Configuration ───────────────────────────────────────────────────────────

CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

def load_config():
    if not os.path.exists(CONFIG_FILE):
        QMessageBox.critical(None, "Config Missing",
            f"config.json not found at:\n{CONFIG_FILE}\n\n"
            "Please create it with your LinkedIn cookies and headers.")
        sys.exit(1)
    with open(CONFIG_FILE, "r") as f:
        return json.load(f)


# ─── URL / Username helpers ──────────────────────────────────────────────────

def extract_public_username(url):
    """Extract public username from a LinkedIn profile URL."""
    url = url.strip()
    # Match pattern: linkedin.com/in/{username}
    m = re.search(r'linkedin\.com/in/([^/?&#]+)', url, re.IGNORECASE)
    if m:
        return m.group(1).rstrip("/")
    return None


def build_profile_urn_url(public_id):
    return (
        f"https://www.linkedin.com/voyager/api/identity/dash/profiles"
        f"?q=memberIdentity&memberIdentity={public_id}"
        f"&decorationId=com.linkedin.voyager.dash.deco.identity.profile.FullProfileWithEntities-93"
    )


def build_compose_options_url(urn_id):
    encoded_urn = urllib.parse.quote(
        f"urn:li:fsd_composeOption:({urn_id},NON_SELF_PROFILE_VIEW,EMPTY_CONTEXT_ENTITY_URN)",
        safe=""
    )
    return f"https://www.linkedin.com/voyager/api/voyagerMessagingDashComposeOptions/{encoded_urn}"


# ─── Worker Thread ───────────────────────────────────────────────────────────

class DetectionWorker(QThread):
    progress = pyqtSignal(int, int, str)       # current, total, status_message
    result_ready = pyqtSignal(dict)             # one result dict per profile
    finished = pyqtSignal()

    def __init__(self, profiles, cookies, headers, delay=5):
        super().__init__()
        self.profiles = profiles          # list of (original_url, public_username)
        self.cookies = cookies
        self.headers = headers
        self.delay = delay
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        total = len(self.profiles)
        for idx, (url, username) in enumerate(self.profiles, 1):
            if self._stop:
                break

            self.progress.emit(idx, total, f"Processing {username} ({idx}/{total})...")

            result = {
                "url": url,
                "username": username,
                "urn": "",
                "open": None,
                "error": None,
            }

            try:
                # Step 1: Get profile URN
                csrf = self.cookies.get("JSESSIONID", "").strip('"')
                self.headers["csrf-token"] = csrf

                resp = requests.get(
                    build_profile_urn_url(username),
                    headers=self.headers,
                    cookies=self.cookies,
                    timeout=30,
                )
                resp.raise_for_status()
                data = resp.json()

                profile_obj = None
                for item in data.get("included", []):
                    if item.get("firstName") and "fsd_profile:" in item.get("entityUrn", ""):
                        profile_obj = item
                        break

                if not profile_obj:
                    result["error"] = "Could not find profile in response"
                    self.result_ready.emit(result)
                    if idx < total and not self._stop:
                        time.sleep(self.delay)
                    continue

                urn = profile_obj.get("entityUrn", "")
                urn_id = urn.split("fsd_profile:")[-1] if "fsd_profile:" in urn else urn
                result["urn"] = urn_id

                # Step 2: Check if profile is open
                compose_resp = requests.get(
                    build_compose_options_url(urn_id),
                    headers=self.headers,
                    cookies=self.cookies,
                    timeout=30,
                )
                compose_resp.raise_for_status()
                compose_data = compose_resp.json()

                compose_type = compose_data.get("data", {}).get("composeOptionType", "")
                result["open"] = compose_type == "PREMIUM_INMAIL"

            except requests.RequestException as e:
                result["error"] = f"Request failed: {str(e)}"
            except (KeyError, ValueError, json.JSONDecodeError) as e:
                result["error"] = f"Parse error: {str(e)}"

            self.result_ready.emit(result)

            # Delay before next request (except last)
            if idx < total and not self._stop:
                time.sleep(self.delay)

        self.finished.emit()


# ─── Main Window ─────────────────────────────────────────────────────────────

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.config = load_config()
        self.cookies = self.config["cookies"]
        self.headers = self.config["headers"]
        self.delay = 5

        self.csv_data = []           # list of dicts (original CSV rows)
        self.profile_column = None   # column name selected by user
        self.results = []            # list of result dicts from worker

        self.worker = None

        self._build_ui()
        self._show_start_view()

    # ─── UI Builders ─────────────────────────────────────────────────────

    def _build_ui(self):
        self.setWindowTitle("LinkedIn Open Profile Detector")
        self.setMinimumSize(900, 650)

        central = QWidget()
        self.setCentralWidget(central)
        self.main_layout = QVBoxLayout(central)

        # ── Stack container ──
        self.stack = QVBoxLayout()

        # --- View 1: Start / Upload ---
        self.view_start = QWidget()
        self._build_view_start()
        self.stack.addWidget(self.view_start)

        # --- View 2: Detection progress ---
        self.view_progress = QWidget()
        self._build_view_progress()
        self.stack.addWidget(self.view_progress)

        # --- View 3: Results ---
        self.view_results = QWidget()
        self._build_view_results()
        self.stack.addWidget(self.view_results)

        self.main_layout.addLayout(self.stack)

    def _build_view_start(self):
        layout = QVBoxLayout(self.view_start)

        title = QLabel("LinkedIn Open Profile Detector")
        title.setStyleSheet("font-size: 20px; font-weight: bold; padding: 15px 0;")
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)

        # Upload area
        upload_group = QGroupBox("Step 1: Upload CSV")
        upload_layout = QVBoxLayout(upload_group)

        self.btn_upload = QPushButton("📂  Upload CSV File")
        self.btn_upload.setMinimumHeight(40)
        self.btn_upload.clicked.connect(self._on_upload_csv)
        upload_layout.addWidget(self.btn_upload)

        self.lbl_uploaded = QLabel("No file selected")
        self.lbl_uploaded.setStyleSheet("color: #666;")
        upload_layout.addWidget(self.lbl_uploaded)
        layout.addWidget(upload_group)

        # Column selection
        col_group = QGroupBox("Step 2: Select Profile Column")
        col_layout = QVBoxLayout(col_group)

        self.cmb_column = QComboBox()
        col_layout.addWidget(QLabel("Choose the column containing LinkedIn profile URLs:"))
        col_layout.addWidget(self.cmb_column)
        layout.addWidget(col_group)

        self.btn_start = QPushButton("🚀  Start Detection")
        self.btn_start.setMinimumHeight(45)
        self.btn_start.setStyleSheet("background-color: #0a66c2; color: white; font-weight: bold; font-size: 14px;")
        self.btn_start.clicked.connect(self._on_start_detection)
        self.btn_start.setEnabled(False)
        layout.addWidget(self.btn_start)

        layout.addStretch()

    def _build_view_progress(self):
        layout = QVBoxLayout(self.view_progress)

        self.lbl_progress_status = QLabel("Starting...")
        self.lbl_progress_status.setAlignment(Qt.AlignCenter)
        self.lbl_progress_status.setStyleSheet("font-size: 16px; padding: 20px 0;")
        layout.addWidget(self.lbl_progress_status)

        self.progress_bar = QProgressBar()
        self.progress_bar.setMinimum(0)
        self.progress_bar.setMaximum(100)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setMinimumHeight(30)
        layout.addWidget(self.progress_bar)

        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.clicked.connect(self._on_cancel)
        layout.addWidget(self.btn_cancel)

        # Live log area
        self.txt_log = QTextEdit()
        self.txt_log.setReadOnly(True)
        self.txt_log.setMaximumHeight(300)
        layout.addWidget(self.txt_log)

    def _build_view_results(self):
        layout = QVBoxLayout(self.view_results)

        # Summary
        self.lbl_summary = QLabel("")
        self.lbl_summary.setStyleSheet("font-size: 16px; font-weight: bold; padding: 10px 0;")
        layout.addWidget(self.lbl_summary)

        # Results table
        self.table = QTableWidget()
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels(["Profile URL", "Username", "URN", "Status", "Error"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        layout.addWidget(self.table)

        # Save buttons
        btn_layout = QHBoxLayout()
        self.btn_save_full = QPushButton("💾  Save Full Results")
        self.btn_save_full.setMinimumHeight(40)
        self.btn_save_full.clicked.connect(lambda: self._on_save_results(all_results=True))
        btn_layout.addWidget(self.btn_save_full)

        self.btn_save_open = QPushButton("💾  Save Only Open Profiles")
        self.btn_save_open.setMinimumHeight(40)
        self.btn_save_open.clicked.connect(lambda: self._on_save_results(all_results=False))
        btn_layout.addWidget(self.btn_save_open)

        self.btn_back = QPushButton("🔄  Start New")
        self.btn_back.setMinimumHeight(40)
        self.btn_back.clicked.connect(self._reset_and_go_back)
        btn_layout.addWidget(self.btn_back)

        layout.addLayout(btn_layout)

    # ─── View Switching ──────────────────────────────────────────────────

    def _show_view(self, widget):
        for i in range(self.stack.count()):
            self.stack.itemAt(i).widget().setVisible(False)
        widget.setVisible(True)

    def _show_start_view(self):
        self._show_view(self.view_start)

    def _show_progress_view(self):
        self._show_view(self.view_progress)

    def _show_results_view(self):
        self._show_view(self.view_results)

    # ─── CSV Handling ────────────────────────────────────────────────────

    def _on_upload_csv(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select CSV File", "", "CSV Files (*.csv);;All Files (*)"
        )
        if not path:
            return

        try:
            with open(path, "r", encoding="utf-8-sig") as f:
                reader = csv.DictReader(f)
                self.csv_data = list(reader)

            if not self.csv_data:
                QMessageBox.warning(self, "Empty CSV", "The CSV file is empty.")
                return

            headers = list(self.csv_data[0].keys())
            if not headers:
                QMessageBox.warning(self, "No Headers", "CSV has no header row.")
                return

            self.cmb_column.clear()
            self.cmb_column.addItems(headers)

            # Auto-select a likely column
            for h in headers:
                if "profile" in h.lower() or "url" in h.lower() or "link" in h.lower():
                    self.cmb_column.setCurrentText(h)
                    break

            self.lbl_uploaded.setText(f"Loaded: {os.path.basename(path)} ({len(self.csv_data)} rows)")
            self.lbl_uploaded.setStyleSheet("color: green; font-weight: bold;")
            self.btn_start.setEnabled(True)

        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to read CSV:\n{str(e)}")

    # ─── Detection ───────────────────────────────────────────────────────

    def _on_start_detection(self):
        col = self.cmb_column.currentText()
        if not col:
            QMessageBox.warning(self, "No Column", "Please select a profile column first.")
            return

        # Build profile list: (url, username)
        profiles = []
        skipped = 0
        for row in self.csv_data:
            url = row.get(col, "").strip()
            if not url:
                skipped += 1
                continue
            username = extract_public_username(url)
            if not username:
                skipped += 1
                continue
            profiles.append((url, username))

        if not profiles:
            QMessageBox.warning(self, "No Valid Profiles",
                "No valid LinkedIn profile URLs found in the selected column.")
            return

        if skipped:
            reply = QMessageBox.question(self, "Skipped Rows",
                f"{skipped} row(s) had invalid/missing URLs and will be skipped.\n\n"
                f"Proceed with {len(profiles)} valid profile(s)?",
                QMessageBox.Yes | QMessageBox.No)
            if reply != QMessageBox.Yes:
                return

        # Reset state
        self.results = []
        self.txt_log.clear()
        self._show_progress_view()
        self.progress_bar.setValue(0)
        self.progress_bar.setMaximum(len(profiles))

        # Start worker
        self.worker = DetectionWorker(profiles, self.cookies, self.headers, self.delay)
        self.worker.progress.connect(self._on_progress)
        self.worker.result_ready.connect(self._on_result)
        self.worker.finished.connect(self._on_detection_finished)
        self.worker.start()

    def _on_progress(self, current, total, message):
        self.progress_bar.setValue(current)
        self.lbl_progress_status.setText(message)
        self.txt_log.append(f"[{current}/{total}] {message}")

    def _on_result(self, result):
        self.results.append(result)
        status = "OPEN" if result["open"] is True else "NOT OPEN" if result["open"] is False else "ERROR"
        self.txt_log.append(f"  → {result['username']}: {status}" +
                            (f" ({result['error']})" if result['error'] else ""))

    def _on_detection_finished(self):
        self._show_results_view()
        self._populate_results_table()

    def _on_cancel(self):
        if self.worker and self.worker.isRunning():
            self.worker.stop()
            self.worker.wait()
            QMessageBox.information(self, "Cancelled", "Detection was cancelled.")
            self._reset_and_go_back()

    # ─── Results Display ─────────────────────────────────────────────────

    def _populate_results_table(self):
        total = len(self.results)
        open_count = sum(1 for r in self.results if r["open"] is True)
        not_open_count = sum(1 for r in self.results if r["open"] is False)
        error_count = sum(1 for r in self.results if r["open"] is None)

        self.lbl_summary.setText(
            f"Summary:  {total} processed  •  "
            f"🟢 {open_count} Open  •  "
            f"🔴 {not_open_count} Not Open  •  "
            f"⚠️ {error_count} Errors"
        )

        self.table.setRowCount(len(self.results))
        for row, r in enumerate(self.results):
            self.table.setItem(row, 0, QTableWidgetItem(r["url"]))
            self.table.setItem(row, 1, QTableWidgetItem(r["username"]))
            self.table.setItem(row, 2, QTableWidgetItem(r["urn"]))
            self.table.setItem(row, 3, QTableWidgetItem(
                "OPEN" if r["open"] is True else "NOT OPEN" if r["open"] is False else "ERROR"
            ))

            # Color the status cell
            status_item = self.table.item(row, 3)
            if r["open"] is True:
                status_item.setBackground(QColor("#27ae60"))
                status_item.setForeground(QColor("white"))
            elif r["open"] is False:
                status_item.setBackground(QColor("#e74c3c"))
                status_item.setForeground(QColor("white"))
            else:
                status_item.setBackground(QColor("#f39c12"))
                status_item.setForeground(QColor("white"))

            self.table.setItem(row, 4, QTableWidgetItem(r.get("error", "")))

    # ─── Saving ──────────────────────────────────────────────────────────

    def _on_save_results(self, all_results=True):
        # Determine save path
        default_name = "linkedin_open_profiles.csv" if not all_results else "linkedin_full_results.csv"
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Results", default_name, "CSV Files (*.csv);;All Files (*)"
        )
        if not path:
            return

        # Filter results
        if all_results:
            to_save = self.results
        else:
            to_save = [r for r in self.results if r["open"] is True]

        if not to_save:
            QMessageBox.information(self, "No Data", "No results to save.")
            return

        # Write CSV
        try:
            with open(path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(["Profile URL", "Username", "URN", "Status", "Error"])
                for r in to_save:
                    status = "OPEN" if r["open"] is True else "NOT OPEN" if r["open"] is False else "ERROR"
                    writer.writerow([r["url"], r["username"], r["urn"], status, r.get("error", "")])

            QMessageBox.information(self, "Saved",
                f"Successfully saved {len(to_save)} result(s) to:\n{path}")
        except Exception as e:
            QMessageBox.critical(self, "Save Error", f"Failed to save:\n{str(e)}")

    def _reset_and_go_back(self):
        self.results = []
        self._show_start_view()


# ─── Entry Point ─────────────────────────────────────────────────────────────

def main():
    # Check dependencies
    try:
        import PyQt5
    except ImportError:
        print("PyQt5 is not installed. Install it with: pip install PyQt5")
        sys.exit(1)

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    window = MainWindow()
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
