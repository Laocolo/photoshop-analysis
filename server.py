"""手机网页版启动入口。

用法：
    .venv/Scripts/python server.py

启动后在手机浏览器（与电脑连同一 Wi-Fi）打开打印出的局域网地址即可。
iPhone 上可用 Safari「分享 → 添加到主屏幕」变成全屏 App。
"""
from __future__ import annotations

import socket

from app.webapp import create_app

HOST = "0.0.0.0"
PORT = 8000


def _lan_ip() -> str:
    """通过连接外部地址拿到本机局域网 IP（不产生真实流量）。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def main() -> None:
    app = create_app()
    ip = _lan_ip()
    print("=" * 52)
    print("  摄影点评助手 · 手机网页版")
    print("=" * 52)
    print(f"  电脑访问：  http://127.0.0.1:{PORT}")
    print(f"  手机访问：  http://{ip}:{PORT}   （手机需与电脑连同一 Wi-Fi）")
    print()
    print("  首次启动如弹出 Windows 防火墙提示，请勾选「专用网络」并允许。")
    print("  iPhone：Safari 打开 → 分享 → 添加到主屏幕，即可像 App 一样全屏使用。")
    print("  按 Ctrl+C 停止服务。")
    print("=" * 52)
    app.run(host=HOST, port=PORT, threaded=True)


if __name__ == "__main__":
    main()
