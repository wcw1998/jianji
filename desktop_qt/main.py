#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
鉴己 · 笔记洞察 · 桌面版（PyQt6 壳）

职责：
  1) 起本地代理（复用同目录 server.py 的逻辑，跑在后台线程，绕过 CORS）
  2) 用 PyQt6 QWebEngineView 开独立窗口，加载 http://127.0.0.1:<port>/index.html
     （QWebEngine 自带 Chromium，无需系统 WebView2/浏览器）
  3) 关窗即停（自动清理代理线程）

资源定位：
  - 开发模式：本文件所在目录（含 index.html / server.py / knowledge/）
  - 打包模式：exe 所在目录（资源随文件夹分发，knowledge/ 可热改免重打）

用法：
  python main.py                 # 开发模式
  python main.py --port 8000     # 指定代理端口起点
  python main.py --no-proxy      # 不起代理（调试）
"""

import argparse
import os
import sys
import threading
import time
import urllib.request

# ---- 资源根目录 ----
def resource_root():
    """开发模式：项目根（index.html/server.py/knowledge 所在）。
    打包模式：exe 所在目录（onedir，资源随文件夹分发）。"""
    if getattr(sys, "frozen", False):
        # PyInstaller onefolder：exe 和 resources 在同一目录
        return os.path.dirname(sys.executable)
    # 开发模式：main.py 在 desktop_qt/，资源在上一级（项目根）
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

ROOT = resource_root()
INDEX = os.path.join(ROOT, "index.html")
# 让 import server 能找到项目根的 server.py
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


# ---- 本地代理（直接 import 同目录 server.py 的 Handler，跑在后台线程）----
class Proxy:
    def __init__(self, port_start=8000):
        self.port = 0
        self.srv = None
        self.port_start = port_start

    def start(self):
        import socket
        # 找空闲端口
        for p in range(self.port_start, self.port_start + 20):
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                s.bind(("127.0.0.1", p))
            except OSError:
                s.close()
                continue
            s.close()
            self.port = p
            break
        else:
            raise RuntimeError("8000-8019 端口全被占用")

        # 诊断日志
        log = os.path.join(ROOT, "qt_debug.log")
        def _dlog(m):
            try:
                with open(log, "a", encoding="utf-8") as f:
                    f.write("%s [proxy] %s\n" % (time.strftime("%H:%M:%S"), m))
            except Exception:
                pass

        # 方案 A：直接 import server（PyInstaller 把它打进 _internal）
        os.environ["JIANI_ROOT"] = ROOT
        try:
            import server as srv_mod
            self.srv = srv_mod.create_server(self.port)
            self._thread = threading.Thread(target=self.srv.serve_forever, daemon=True)
            self._thread.start()
            _dlog("import server OK, port=%d" % self.port)
        except Exception as e:
            # 方案 B：import 失败（server.py 没被当模块打进），spawn 系统 python 跑 server.py
            _dlog("import server failed (%s); fallback to spawn python" % e)
            self.srv = None
            self._spawned = self._spawn_python_server()
            if not self._spawned:
                _dlog("fallback spawn FAILED")
                raise RuntimeError("无法启动代理（import 和 spawn 都失败）")

        # 等端口就绪
        for _ in range(80):
            try:
                urllib.request.urlopen(
                    "http://127.0.0.1:%d/__proxy_ping" % self.port, timeout=1)
                _dlog("proxy ready on %d" % self.port)
                print("本地代理：%d" % self.port)
                return self.port
            except Exception:
                time.sleep(0.15)
        _dlog("proxy NOT ready after wait")
        print("[!] 代理未就绪，界面可能直连失败")
        return self.port

    def _spawn_python_server(self):
        """import server 失败时，用系统/同目录 Python 起 server.py（子进程）。"""
        import subprocess
        server_py = os.path.join(ROOT, "server.py")
        if not os.path.exists(server_py):
            return None
        # 候选 python：同目录 runtime/（内置嵌入式 Python）> 同目录 python.exe > 系统 PATH 里的
        cands = []
        for pyname in ("runtime/python.exe", "python.exe"):
            local_py = os.path.join(ROOT, pyname)
            if os.path.exists(local_py):
                cands.append(local_py)
        cands += ["python", "py", "python3"]
        for pyexe in cands:
            try:
                p = subprocess.Popen(
                    [pyexe, server_py, str(self.port)],
                    cwd=ROOT,
                    env={**os.environ, "JIANI_ROOT": ROOT},
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
                )
                time.sleep(0.5)
                if p.poll() is None:
                    self._proc = p
                    return p
            except Exception:
                continue
        return None

    def stop(self):
        if self.srv:
            try:
                self.srv.shutdown()
                self.srv.server_close()
            except Exception:
                pass
            self.srv = None
        # 关掉 spawn 的子进程（方案 B）
        proc = getattr(self, "_proc", None)
        if proc and proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=3)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass


def main():
    ap = argparse.ArgumentParser(description="鉴己 · 笔记洞察 · 桌面版（PyQt6）")
    ap.add_argument("--port", type=int, default=8000, help="代理端口起点（默认 8000）")
    ap.add_argument("--no-proxy", action="store_true", help="不起本地代理")
    ap.add_argument("--width", type=int, default=1180)
    ap.add_argument("--height", type=int, default=800)
    args = ap.parse_args()

    if not os.path.exists(INDEX):
        print("[!] 找不到 index.html：%s" % INDEX)
        sys.exit(1)

    proxy = Proxy(args.port)
    if not args.no_proxy:
        try:
            proxy.start()
        except Exception as e:
            print("[!] 代理启动失败：%s" % e)
            print("    请确认目标机器装了 Python（python 在 PATH），")
            print("    或把 python.exe 放进本文件夹（与 exe 同目录）。")
            # 不直接退：仍开窗口（file:// 直连模式可用），但提示用户
            from PyQt6.QtWidgets import QApplication, QLabel
            app = QApplication([sys.argv[0]])
            lbl = QLabel("本地代理启动失败：\n%s\n\n请确认装有 Python，或把 python.exe 放到本文件夹后重启。" % e)
            lbl.setAlignment(4)
            lbl.setStyleSheet("font-size:14px; padding:40px; color:#a13c2b; background:#fff;")
            lbl.setFixedSize(520, 200)
            lbl.show()
            import sys as _s
            _s.exit(app.exec())

    # ---- PyQt6 GUI ----
    # 打包模式（onedir）下，Qt 运行时和 QtWebEngineProcess.exe 在 _internal/PyQt6/ 下，
    # 启动前必须告诉 Qt 插件目录 + WebEngine 渲染进程位置，否则黑屏。
    if getattr(sys, "frozen", False):
        _internal = os.path.join(os.path.dirname(sys.executable), "_internal")
        _qt_dir = os.path.join(_internal, "PyQt6", "Qt6")
        # Qt 插件/资源目录
        os.environ["QT_PLUGIN_PATH"] = os.path.join(_qt_dir, "plugins")
        os.environ["QT_QPA_PLATFORM_PLUGIN_PATH"] = os.path.join(_qt_dir, "plugins", "platforms")
        os.environ["QML2_IMPORT_PATH"] = os.path.join(_qt_dir, "qml")
        # 关键：WebEngine 渲染进程（找不到就黑屏）
        _wep = os.path.join(_qt_dir, "bin", "QtWebEngineProcess.exe")
        if os.path.exists(_wep):
            os.environ["QTWEBENGINEPROCESS_PATH"] = _wep
        # Chromium 资源（icudtl 等）
        os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS",
                              "--no-sandbox --allow-file-access-from-files")

    from PyQt6.QtWidgets import QApplication, QMainWindow, QWidget, QVBoxLayout
    from PyQt6.QtWebEngineWidgets import QWebEngineView
    from PyQt6.QtCore import QUrl, QTimer
    from PyQt6.QtGui import QIcon

    # Qt 需要 argv[0]（程序名），不能传空列表
    argv = [sys.argv[0]] if sys.argv else ["jianyi-qt"]
    app = QApplication(argv)
    app.setApplicationName("鉴己 · 笔记洞察")
    _ico = os.path.join(ROOT, "jianyi.ico")
    if os.path.exists(_ico):
        app.setWindowIcon(QIcon(_ico))

    # 窗口（用子类挂 closeEvent 清理代理）
    from PyQt6.QtCore import QEvent

    class _Win(QMainWindow):
        def closeEvent(self, e):
            proxy.stop()
            super().closeEvent(e)

    win = _Win()
    win.setWindowTitle("鉴己 · 笔记洞察")
    if os.path.exists(_ico):
        win.setWindowIcon(QIcon(_ico))
    win.resize(args.width, args.height)

    view = QWebEngineView()
    win.setCentralWidget(view)

    # 用 http:// 同源加载（代理已在 proxy.start() 里等 ping 通，确保就绪）。
    # 之前 file:// 在部分环境 load ok=False（Chromium file 访问限制），http 最稳。
    if not args.no_proxy:
        url = "http://127.0.0.1:%d/index.html" % proxy.port
    else:
        url = "file://" + INDEX.replace(os.sep, "/")

    # 诊断：监听页面加载，写到日志文件（黑屏时排查看）
    page = view.page()
    log_path = os.path.join(ROOT, "qt_debug.log")
    def _plog(msg):
        try:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write("%s %s\n" % (time.strftime("%H:%M:%S"), msg))
        except Exception:
            pass
    _plog("load url=%s" % url)

    _retries = {"n": 0}
    def on_load_done(ok):
        _plog("load finished, ok=%s" % ok)
        try:
            _plog("page title: %r" % page.title())
        except Exception:
            pass
        # 加载失败且是 http 代理页：等 2 秒重试（渲染进程可能慢半拍）
        if not ok and not args.no_proxy and _retries["n"] < 5:
            _retries["n"] += 1
            _plog("retry load #%d" % _retries["n"])
            QTimer.singleShot(2000, lambda: view.load(QUrl(url)))
    page.loadFinished.connect(on_load_done)

    view.load(QUrl(url))
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
