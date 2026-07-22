"""入口：启动摄影学习点评助手。"""
import sys

from PySide6.QtWidgets import QApplication

from app.main_window import MainWindow


def main():
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
