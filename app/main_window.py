"""主界面：照片预览（可旋转）+ 参数区 + 点评结果 + 历史记录。"""
from __future__ import annotations

from pathlib import Path

from PIL import Image
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from . import storage
from .ai_client import ApiError, critique, test_connection
from .exif_utils import read_exif
from .image_utils import HEIF_SUFFIXES, RAW_SUFFIXES, open_as_pil

IMAGE_SUFFIXES = {
    ".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff",
} | RAW_SUFFIXES | HEIF_SUFFIXES

PARAM_FIELDS = (
    ("aperture", "光圈"),
    ("shutter", "快门"),
    ("iso", "ISO"),
    ("focal_length", "焦距"),
    ("datetime", "拍摄时间"),
    ("camera", "相机"),
)


class _Worker(QThread):
    """在后台线程跑网络请求，避免界面卡死。"""

    ok = Signal(str)
    fail = Signal(str)

    def __init__(self, fn, parent=None):
        super().__init__(parent)
        self._fn = fn

    def run(self):
        try:
            self.ok.emit(self._fn())
        except ApiError as e:
            self.fail.emit(str(e))
        except Exception as e:  # noqa: BLE001 - 兜底，保证错误能显示给用户
            self.fail.emit(f"未知错误：{e}")


class SettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("设置")
        self.setMinimumWidth(460)
        cfg = storage.load_config()

        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.key_edit = QLineEdit(cfg["api_key"])
        self.key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.key_edit.setPlaceholderText("sk-...")
        self.url_edit = QLineEdit(cfg["base_url"])
        self.model_edit = QLineEdit(cfg["model"])
        form.addRow("API Key", self.key_edit)
        form.addRow("Base URL", self.url_edit)
        form.addRow("模型", self.model_edit)
        layout.addLayout(form)

        hint = QLabel(
            'API Key 获取：注册 <a href="https://platform.moonshot.cn">platform.moonshot.cn</a>'
            " → 用户中心 → API Key 管理 → 新建。"
        )
        hint.setOpenExternalLinks(True)
        hint.setWordWrap(True)
        layout.addWidget(hint)

        row = QHBoxLayout()
        self.test_btn = QPushButton("测试连接")
        self.test_btn.clicked.connect(self._test)
        self.test_label = QLabel("")
        row.addWidget(self.test_btn)
        row.addWidget(self.test_label, 1)
        layout.addLayout(row)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _cfg_from_fields(self) -> dict:
        return {
            "api_key": self.key_edit.text().strip(),
            "base_url": self.url_edit.text().strip() or storage.DEFAULT_CONFIG["base_url"],
            "model": self.model_edit.text().strip() or storage.DEFAULT_CONFIG["model"],
        }

    def _save(self):
        storage.save_config(self._cfg_from_fields())
        self.accept()

    def _test(self):
        cfg = self._cfg_from_fields()
        if not cfg["api_key"]:
            self.test_label.setText("请先填写 API Key")
            return
        self.test_btn.setEnabled(False)
        self.test_label.setText("测试中…")
        self._worker = _Worker(lambda: test_connection(cfg), self)
        self._worker.ok.connect(lambda _msg: self._on_test(True, ""))
        self._worker.fail.connect(lambda msg: self._on_test(False, msg))
        self._worker.start()

    def _on_test(self, success: bool, msg: str):
        self.test_btn.setEnabled(True)
        self.test_label.setText("连接正常 ✓" if success else f"失败：{msg}")


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("摄影学习点评助手")
        self.resize(1280, 800)
        self.setAcceptDrops(True)
        self.image_path: str | None = None
        self._base_image: Image.Image | None = None  # 已按 EXIF 转正的原图
        self._angle = 0  # 用户手动旋转角度（逆时针，0/90/180/270）
        self._rotated: Image.Image | None = None  # 旋转结果缓存
        self._worker = None

        splitter = QSplitter()
        self.setCentralWidget(splitter)

        # 左栏：照片预览 + 旋转按钮
        left = QWidget()
        lv = QVBoxLayout(left)
        self.preview = QLabel("拖入照片，或点击下方“选择照片”")
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setMinimumWidth(360)
        # Ignored：标签不向布局索要空间，窗口大小由布局决定，图片只缩放适配
        self.preview.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self.preview.setStyleSheet("background:#222; color:#aaa;")
        lv.addWidget(self.preview, 1)

        btn_row = QHBoxLayout()
        pick_btn = QPushButton("选择照片…")
        pick_btn.clicked.connect(self._pick)
        rot_left_btn = QPushButton("↺ 向左转")
        rot_left_btn.setToolTip("逆时针旋转 90°")
        rot_left_btn.clicked.connect(lambda: self._rotate(90))
        rot_right_btn = QPushButton("↻ 向右转")
        rot_right_btn.setToolTip("顺时针旋转 90°")
        rot_right_btn.clicked.connect(lambda: self._rotate(-90))
        btn_row.addWidget(pick_btn, 1)
        btn_row.addWidget(rot_left_btn)
        btn_row.addWidget(rot_right_btn)
        lv.addLayout(btn_row)
        splitter.addWidget(left)

        # 中栏：参数区
        mid = QWidget()
        mv = QVBoxLayout(mid)
        group = QGroupBox("拍摄参数（自动读取，可手动修改）")
        form = QFormLayout(group)
        self.param_edits: dict[str, QLineEdit] = {}
        for key, label in PARAM_FIELDS:
            edit = QLineEdit()
            edit.setPlaceholderText("未知")
            self.param_edits[key] = edit
            form.addRow(label, edit)
        mv.addWidget(group)

        extra_form = QFormLayout()
        self.extra_edit = QLineEdit()
        self.extra_edit.setPlaceholderText("如：傍晚 6 点，日落前逆光")
        self.intent_edit = QLineEdit()
        self.intent_edit.setPlaceholderText("如：想拍樱花树下的女朋友")
        extra_form.addRow("大概时间/光线", self.extra_edit)
        extra_form.addRow("我想拍什么", self.intent_edit)
        mv.addLayout(extra_form)

        self.go_btn = QPushButton("开始点评")
        self.go_btn.setMinimumHeight(40)
        self.go_btn.clicked.connect(self._critique)
        settings_btn = QPushButton("设置 API Key…")
        settings_btn.clicked.connect(self._settings)
        mv.addWidget(self.go_btn)
        mv.addWidget(settings_btn)
        mv.addStretch(1)
        splitter.addWidget(mid)

        # 右栏：结果 + 历史
        right = QSplitter(Qt.Orientation.Vertical)
        self.result = QTextBrowser()
        self.result.setPlaceholderText("点评结果会显示在这里")
        right.addWidget(self.result)
        self.history = QListWidget()
        self.history.itemClicked.connect(self._open_record)
        right.addWidget(self.history)
        right.setSizes([560, 160])
        splitter.addWidget(right)
        splitter.setSizes([520, 300, 460])

        self._refresh_history()
        self.statusBar().showMessage("就绪")

        if not storage.load_config()["api_key"]:
            QMessageBox.information(self, "首次使用", "请先点击“设置 API Key…”配置 Kimi API Key，然后再开始点评。")

    # ---- 照片导入与旋转 ----

    def _pick(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择照片", "",
            "图片 (*.jpg *.jpeg *.png *.webp *.bmp *.tif *.tiff *.heic *.heif *.arw *.cr2 *.nef *.dng *.rw2 *.orf *.raf)",
        )
        if path:
            self.load_image(path)

    def load_image(self, path: str):
        try:
            img = open_as_pil(path)  # 全格式统一解码（含 RAW/HEIC，按 EXIF 自动转正）
        except Exception as e:
            QMessageBox.warning(self, "无法打开", f"解析这张图片失败：{e}")
            return
        self.image_path = path
        self._base_image = img
        self._angle = 0
        self._rotated = None
        self._show_pixmap()
        for edit in self.param_edits.values():
            edit.clear()
        for key, value in read_exif(path).items():
            if value:
                self.param_edits[key].setText(value)
        self.statusBar().showMessage(f"已载入：{Path(path).name}")

    def _rotate(self, delta: int):
        """旋转预览；点评与存档都会使用旋转后的画面。"""
        if self._base_image is None:
            return
        self._angle = (self._angle + delta) % 360
        self._rotated = None
        self._show_pixmap()
        if self._angle:
            self.statusBar().showMessage(f"已旋转 {self._angle}°（点评将使用旋转后的画面）")
        else:
            self.statusBar().showMessage("已恢复原始方向")

    def _current_image(self) -> Image.Image | None:
        """当前画面（原图 + 用户旋转），带缓存。"""
        if self._base_image is None:
            return None
        if self._rotated is None:
            self._rotated = (
                self._base_image.rotate(self._angle, expand=True) if self._angle else self._base_image
            )
        return self._rotated

    @staticmethod
    def _to_pixmap(img: Image.Image) -> QPixmap:
        data = img.tobytes("raw", "RGB")
        qimg = QImage(data, img.width, img.height, img.width * 3, QImage.Format.Format_RGB888)
        return QPixmap.fromImage(qimg.copy())

    def _show_pixmap(self):
        img = self._current_image()
        if img is None:
            return
        # 缩到预览标签的实际尺寸（按屏幕 DPR 补偿清晰度），图片永远适配界面
        w = max(self.preview.width(), 1)
        h = max(self.preview.height(), 1)
        dpr = self.preview.devicePixelRatioF()
        small = img.copy()
        small.thumbnail((int(w * dpr), int(h * dpr)))
        pix = self._to_pixmap(small)
        pix.setDevicePixelRatio(dpr)
        self.preview.setPixmap(pix)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._show_pixmap()

    def dragEnterEvent(self, event):
        if any(Path(u.toLocalFile()).suffix.lower() in IMAGE_SUFFIXES for u in event.mimeData().urls()):
            event.acceptProposedAction()

    def dropEvent(self, event):
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if Path(path).suffix.lower() in IMAGE_SUFFIXES:
                self.load_image(path)
                break

    # ---- 点评 ----

    def _critique(self):
        image = self._current_image()
        if image is None:
            QMessageBox.warning(self, "还没有照片", "请先选择或拖入一张照片。")
            return
        cfg = storage.load_config()
        if not cfg["api_key"]:
            QMessageBox.warning(self, "缺少 API Key", "请先点击“设置 API Key…”完成配置。")
            return
        params = {k: e.text().strip() for k, e in self.param_edits.items()}
        extra = self.extra_edit.text().strip()
        intent = self.intent_edit.text().strip()

        self.go_btn.setEnabled(False)
        self.go_btn.setText("点评中，请稍候…")
        self.result.clear()
        self.statusBar().showMessage("正在调用 AI 点评…")
        self._worker = _Worker(lambda: critique(image, params, extra, intent, cfg), self)
        self._worker.ok.connect(lambda text: self._done(text, image, params, extra, intent))
        self._worker.fail.connect(self._failed)
        self._worker.start()

    def _done(self, text: str, image: Image.Image, params: dict, extra: str, intent: str):
        self.go_btn.setEnabled(True)
        self.go_btn.setText("开始点评")
        self.result.setMarkdown(text)
        self.statusBar().showMessage("点评完成")
        try:
            storage.save_record(Path(self.image_path).name, image, params, extra, intent, text)
        except Exception:
            pass  # 存历史失败不影响展示
        self._refresh_history()

    def _failed(self, msg: str):
        self.go_btn.setEnabled(True)
        self.go_btn.setText("开始点评")
        self.statusBar().showMessage("点评失败")
        QMessageBox.warning(self, "点评失败", msg)

    def _settings(self):
        SettingsDialog(self).exec()

    # ---- 历史记录 ----

    def _refresh_history(self):
        self.history.clear()
        for rec_dir, meta in storage.list_records():
            item = QListWidgetItem(f"{meta.get('time', '?')}  {meta.get('image_name', '')}")
            item.setData(Qt.ItemDataRole.UserRole, str(rec_dir))
            self.history.addItem(item)

    def _open_record(self, item: QListWidgetItem):
        rec = storage.load_record(Path(item.data(Qt.ItemDataRole.UserRole)))
        if not rec:
            return
        _meta, text, thumb = rec
        self.result.setMarkdown(text)
        if thumb.exists():
            try:
                self._base_image = Image.open(thumb).convert("RGB")
                self._angle = 0
                self._rotated = None
                self._show_pixmap()
            except Exception:
                pass
