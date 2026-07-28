"""主界面：照片预览（可旋转）+ 参数区 + 点评结果 + 历史记录。"""
from __future__ import annotations

from pathlib import Path

from PIL import Image
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from . import image_edit, storage
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

    ok = Signal(object)  # str（点评文本）或 PIL Image（优化结果）
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

        # 服务商：保存多份配置，下拉选择即填入，保存后一键切换
        prov_row = QHBoxLayout()
        self.provider_combo = QComboBox()
        self._reload_provider_combo(cfg)
        self.provider_combo.currentIndexChanged.connect(self._on_provider_selected)
        save_prov_btn = QPushButton("存为服务商…")
        save_prov_btn.clicked.connect(self._save_provider)
        del_prov_btn = QPushButton("删除")
        del_prov_btn.clicked.connect(self._remove_provider)
        prov_row.addWidget(self.provider_combo, 1)
        prov_row.addWidget(save_prov_btn)
        prov_row.addWidget(del_prov_btn)

        # 模板：快速填入常见厂商的 base_url / model
        self.preset_combo = QComboBox()
        self.preset_combo.addItem("从模板填入（Kimi / 豆包 / Agnes…）", None)
        for p in storage.PROVIDER_PRESETS:
            self.preset_combo.addItem(p["name"], p)
        self.preset_combo.currentIndexChanged.connect(self._on_preset_selected)

        form = QFormLayout()
        form.addRow("服务商", prov_row)
        form.addRow("模板", self.preset_combo)
        self.key_edit = QLineEdit(cfg["api_key"])
        self.key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.key_edit.setPlaceholderText("sk-...")
        self.url_edit = QLineEdit(cfg["base_url"])
        self.model_edit = QLineEdit(cfg["model"])
        form.addRow("API Key", self.key_edit)
        form.addRow("Base URL", self.url_edit)
        form.addRow("模型", self.model_edit)

        # 图片优化模型（「按建议优化照片」功能）
        self.image_provider_combo = QComboBox()
        self.image_provider_combo.addItem("跟随当前点评服务商", "")
        for p in cfg.get("providers") or []:
            self.image_provider_combo.addItem(p["name"], p["name"])
        idx = self.image_provider_combo.findData(cfg.get("image_provider") or "")
        self.image_provider_combo.setCurrentIndex(max(idx, 0))
        self.image_model_edit = QLineEdit(cfg.get("image_model") or "")
        self.image_preset_combo = QComboBox()
        self.image_preset_combo.addItem("模板…", None)
        for p in storage.IMAGE_MODEL_PRESETS:
            self.image_preset_combo.addItem(p["name"], p["model"])
        self.image_preset_combo.currentIndexChanged.connect(self._on_image_preset)
        img_row = QHBoxLayout()
        img_row.addWidget(self.image_model_edit, 1)
        img_row.addWidget(self.image_preset_combo)
        form.addRow("图片优化", self.image_provider_combo)
        form.addRow("图片模型", img_row)
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

    # ---- 服务商管理 ----

    def _reload_provider_combo(self, cfg: dict, select: str = ""):
        self.provider_combo.blockSignals(True)
        self.provider_combo.clear()
        self.provider_combo.addItem("手动配置", "")
        for p in cfg.get("providers") or []:
            self.provider_combo.addItem(p["name"], p["name"])
        idx = self.provider_combo.findData(select or (cfg.get("active_provider") or ""))
        self.provider_combo.setCurrentIndex(max(idx, 0))
        self.provider_combo.blockSignals(False)

    def _on_provider_selected(self):
        p = storage.find_provider(storage.load_config(), self.provider_combo.currentData())
        if p:
            self.url_edit.setText(p.get("base_url", ""))
            self.model_edit.setText(p.get("model", ""))
            self.key_edit.setText(p.get("api_key", ""))

    def _on_preset_selected(self):
        p = self.preset_combo.currentData()
        if p:
            self.url_edit.setText(p["base_url"])
            self.model_edit.setText(p["model"])
            self.key_edit.setFocus()

    def _on_image_preset(self):
        model = self.image_preset_combo.currentData()
        if model:
            self.image_model_edit.setText(model)

    def _save_provider(self):
        default_name = self.provider_combo.currentData() or (
            self.preset_combo.currentData() and self.preset_combo.currentText()
        ) or ""
        name, ok = QInputDialog.getText(self, "存为服务商", "服务商名称：", text=default_name)
        name = name.strip()
        if not ok or not name:
            return
        cfg = storage.load_config()
        fields = self._cfg_from_fields()
        storage.upsert_provider(
            cfg, name, fields["base_url"], fields["model"], fields["api_key"] or None  # key 为空则沿用已存
        )
        storage.save_config(cfg)
        self._reload_provider_combo(cfg, select=name)

    def _remove_provider(self):
        name = self.provider_combo.currentData()
        if not name:
            return
        if QMessageBox.question(self, "删除服务商", f"确定删除「{name}」？") != QMessageBox.StandardButton.Yes:
            return
        cfg = storage.load_config()
        storage.remove_provider(cfg, name)
        storage.save_config(cfg)
        self._reload_provider_combo(cfg)

    # ---- 配置保存 / 测试 ----

    def _cfg_from_fields(self) -> dict:
        return {
            "api_key": self.key_edit.text().strip(),
            "base_url": self.url_edit.text().strip() or storage.DEFAULT_CONFIG["base_url"],
            "model": self.model_edit.text().strip() or storage.DEFAULT_CONFIG["model"],
        }

    def _save(self):
        cfg = storage.load_config()  # 保留 access_token / providers 等其他键
        cfg.update(self._cfg_from_fields())
        cfg["image_provider"] = self.image_provider_combo.currentData() or ""
        cfg["image_model"] = self.image_model_edit.text().strip() or storage.DEFAULT_CONFIG["image_model"]
        # 三字段与所选服务商一致才算"启用该服务商"，否则视为手动配置
        name = self.provider_combo.currentData()
        p = storage.find_provider(cfg, name) if name else None
        cfg["active_provider"] = (
            name
            if p
            and p.get("base_url") == cfg["base_url"]
            and p.get("model") == cfg["model"]
            and p.get("api_key") == cfg["api_key"]
            else ""
        )
        storage.save_config(cfg)
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


class _SuggestDialog(QDialog):
    """优化前确认/修改修图建议。"""

    def __init__(self, text: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("确认优化建议")
        self.setMinimumWidth(540)
        layout = QVBoxLayout(self)
        hint = QLabel("已从点评中提取「后期调整建议」，将作为修图指令发给图片模型，可直接修改：")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.edit = QPlainTextEdit(text)
        self.edit.setMinimumHeight(240)
        layout.addWidget(self.edit)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("开始优化")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def text(self) -> str:
        return self.edit.toPlainText().strip()


class _OptimizeDialog(QDialog):
    """展示 AI 优化后的照片，可另存。"""

    def __init__(self, img: Image.Image, pixmap: QPixmap, parent=None):
        super().__init__(parent)
        self.setWindowTitle("优化后的照片")
        self._img = img
        layout = QVBoxLayout(self)
        view = QLabel()
        view.setPixmap(
            pixmap.scaled(760, 760, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        )
        layout.addWidget(view)
        row = QHBoxLayout()
        row.addStretch(1)
        save_btn = QPushButton("另存为…")
        save_btn.clicked.connect(self._save)
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.accept)
        row.addWidget(save_btn)
        row.addWidget(close_btn)
        layout.addLayout(row)

    def _save(self):
        path, _ = QFileDialog.getSaveFileName(self, "保存优化后的照片", "优化照片.jpg", "JPEG 图片 (*.jpg)")
        if not path:
            return
        try:
            self._img.convert("RGB").save(path, "JPEG", quality=92)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "保存失败", str(e))


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
        self._last_critique = ""  # 最近一次点评的 markdown（「按建议优化照片」用）

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
        self.optimize_btn = QPushButton("✨ 按建议优化照片")
        self.optimize_btn.setEnabled(False)
        self.optimize_btn.setToolTip("点评完成后，按「后期调整建议」调用图片模型优化这张照片")
        self.optimize_btn.clicked.connect(self._optimize)
        settings_btn = QPushButton("设置 API Key…")
        settings_btn.clicked.connect(self._settings)
        mv.addWidget(self.go_btn)
        mv.addWidget(self.optimize_btn)
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
        self._last_critique = ""
        self.optimize_btn.setEnabled(False)
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
        self._last_critique = text
        self.optimize_btn.setEnabled(True)
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

    # ---- 按建议优化照片 ----

    def _optimize(self):
        image = self._current_image()
        if image is None or not self._last_critique:
            QMessageBox.warning(self, "还没有点评", "请先完成一次点评。")
            return
        # 先让用户确认/修改修图建议，再真正调用
        dlg = _SuggestDialog(image_edit.extract_suggestions(self._last_critique), self)
        if not dlg.exec():
            return
        suggestions = dlg.text()
        if not suggestions:
            QMessageBox.warning(self, "内容为空", "优化建议不能为空。")
            return
        cfg = storage.load_config()
        self.optimize_btn.setEnabled(False)
        self.optimize_btn.setText("优化中，约 1~3 分钟…")
        self.statusBar().showMessage("正在调用图片模型优化…")
        self._opt_worker = _Worker(lambda: image_edit.optimize(image, suggestions, cfg), self)
        self._opt_worker.ok.connect(self._optimize_done)
        self._opt_worker.fail.connect(self._optimize_failed)
        self._opt_worker.start()

    def _optimize_done(self, img: Image.Image):
        self.optimize_btn.setEnabled(True)
        self.optimize_btn.setText("✨ 按建议优化照片")
        self.statusBar().showMessage("优化完成")
        _OptimizeDialog(img, self._to_pixmap(img.convert("RGB")), self).exec()

    def _optimize_failed(self, msg: str):
        self.optimize_btn.setEnabled(True)
        self.optimize_btn.setText("✨ 按建议优化照片")
        self.statusBar().showMessage("优化失败")
        QMessageBox.warning(self, "优化失败", msg)

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
