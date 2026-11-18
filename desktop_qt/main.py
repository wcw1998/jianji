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
import json
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
# 配置持久化文件：QtWebEngine 的 localStorage 跨重启不落盘（实测 _profile 从不
# 生成、重启读回 None），所以桌面版把模型配置存到自己的 JSON 文件，启动时注回页面。
CONFIG_PATH = os.path.join(ROOT, "jianji-config.json")
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
        os.environ["JIANJI_ROOT"] = ROOT
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
                    env={**os.environ, "JIANJI_ROOT": ROOT},
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
    from PyQt6.QtWebEngineCore import QWebEngineProfile, QWebEnginePage
    from PyQt6.QtCore import QUrl, QTimer
    from PyQt6.QtGui import QIcon

    # Qt 需要 argv[0]（程序名），不能传空列表
    argv = [sys.argv[0]] if sys.argv else ["jianji-qt"]
    app = QApplication(argv)
    app.setApplicationName("鉴己 · 笔记洞察")
    _ico = os.path.join(ROOT, "jianji.ico")
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

    # ---- 持久化 profile：不设这条，QWebEngine 默认 off-the-record（内存），
    #      localStorage 根本不落盘，重启就丢（表现为「API Key 不保存」）。
    #      指到 exe 旁的 _profile/，Key / 提示词调优都能跨重启保留。----
    profile = QWebEngineProfile.defaultProfile()
    profile.setPersistentStoragePath(os.path.join(ROOT, "_profile"))
    profile.setPersistentCookiesPolicy(
        QWebEngineProfile.PersistentCookiesPolicy.ForcePersistentCookies)

    view = QWebEngineView()
    win.setCentralWidget(view)

    # ---- 诊断：捕获页面 JS console（error/warn/info/log），落进 qt_debug.log。
    #      桌面版是窗口程序、没有 devtools，JS 报错平时全被吞掉——这是之前
    #      复制/Key bug 一直修不准的根因。现在把真实报错露出来。----
    log_path = os.path.join(ROOT, "qt_debug.log")
    def _plog(msg):
        try:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write("%s %s\n" % (time.strftime("%H:%M:%S"), msg))
        except Exception:
            pass

    class _DiagPage(QWebEnginePage):
        def javaScriptConsoleMessage(self, level, message, line, source):
            # level 是 QtWebEngineCore.JavaScriptConsoleMessageLevel 枚举，
            # 不能 int() 强转（会 TypeError 崩掉整个 Qt 回调）。按枚举名归类。
            try:
                tag = {
                    "Info": "js-info",
                    "Warning": "js-warn",
                    "Error": "js-error",
                }.get(level.name, "js")
            except Exception:
                tag = "js"
            try:
                _plog("[%s] %s (%s:%s)" % (tag, message, source, line))
            except Exception:
                pass
            try:
                super().javaScriptConsoleMessage(level, message, line, source)
            except Exception:
                pass

    _page = _DiagPage(profile, view)
    view.setPage(_page)
    page = _page

    # ---- 剪贴板轮询桥：网页 copy() 检测到 ?desktop=1 时，把文本存进
    #      window.__jianjiCopy（不跳转、不依赖文档焦点）。这里用 QTimer 每 120ms
    #      runJavaScript 读回该变量，非空就 QApplication.clipboard() 原生写、再清空。
    #      Windows 下 OpenClipboard 偶发被其它进程短暂锁住（COM 0x800401d0），
    #      所以写入失败时用 QTimer 退避重试，拿到锁为止。----
    _last_copy = {"v": None}
    def _write_clipboard_with_retry(text, attempt=0):
        try:
            QApplication.clipboard().setText(text)
            # 验证真的写进去了（Windows 锁竞争时 setText 会静默失败）
            if QApplication.clipboard().text() == text:
                _plog("[copy] 原生写剪贴板成功, 长度=%d" % len(text))
                return
            raise RuntimeError("写入后读回不一致（剪贴板被锁）")
        except Exception as e:
            if attempt < 6:
                QTimer.singleShot(60 * (attempt + 1),
                                  lambda: _write_clipboard_with_retry(text, attempt + 1))
            else:
                _plog("[copy] 原生写剪贴板失败(已重试): %s" % e)

    def _poll_clipboard():
        def _got(v):
            if v is None or v == "":
                return
            if v == _last_copy["v"]:
                return  # 同一段文本只写一次，避免反复覆盖系统剪贴板
            _last_copy["v"] = v
            _write_clipboard_with_retry(str(v))
        try:
            page.runJavaScript(
                "(window.__jianjiCopy==null?'':String(window.__jianjiCopy))", _got)
        except Exception:
            pass
    _clip_timer = QTimer()
    _clip_timer.timeout.connect(_poll_clipboard)
    _clip_timer.start(120)

    # ---- 配置持久化桥：QtWebEngine 的 localStorage 跨重启不落盘（实测 _profile
    #      从不生成、重启读回 None），所以模型配置改存桌面壳自己的 JSON 文件。
    #      网页 saveCfg() 除写 localStorage 外，把配置 JSON 推到 window.__jianjiCfg；
    #      这里轮询读回，变化就写 CONFIG_PATH。启动时 _restore_config() 再注回页面。----
    _last_cfg = {"v": None}
    def _restore_config():
        """启动后把上次保存的配置注回页面 localStorage 并刷新 UI。"""
        if not os.path.exists(CONFIG_PATH):
            _plog("[cfg] 无配置文件，跳过恢复 %s" % CONFIG_PATH)
            return
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                raw = f.read()
            # 校验是合法 JSON 再注入，避免坏文件搞坏页面
            json.loads(raw)
        except Exception as e:
            _plog("[cfg] 配置文件读取/解析失败: %s" % e)
            return
        js = (
            "(function(){try{localStorage.setItem('jianji_insight_cfg_v1', %s);"
            "if(typeof loadCfg==='function') loadCfg();"
            "return 'ok';}catch(e){return 'ERR:'+e;}})()" % json.dumps(raw)
        )
        page.runJavaScript(js, lambda v: _plog("[cfg] 启动恢复配置=%s" % v))
        _last_cfg["v"] = raw  # 恢复的值视为已保存，避免轮询立刻重复写

    def _poll_config():
        def _got(v):
            if v is None or v == "" or v == "{}":
                return
            if v == _last_cfg["v"]:
                return  # 没变化不重复写盘
            _last_cfg["v"] = v
            try:
                with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                    f.write(v)
                _plog("[cfg] 配置已保存到文件, 长度=%d" % len(v))
            except Exception as e:
                _plog("[cfg] 配置写文件失败: %s" % e)
        try:
            page.runJavaScript(
                "(window.__jianjiCfg==null?'':String(window.__jianjiCfg))", _got)
        except Exception:
            pass
    _cfg_timer = QTimer()
    _cfg_timer.timeout.connect(_poll_config)
    _cfg_timer.start(300)
    _plog("=== 启动，profile 持久化路径=%s ===" % profile.persistentStoragePath())

    # 用 http:// 同源加载（代理已在 proxy.start() 里等 ping 通，确保就绪）。
    # 之前 file:// 在部分环境 load ok=False（Chromium file 访问限制），http 最稳。
    # 带 ?desktop=1：让页面识别桌面环境，copy() 走原生剪贴板桥。
    if not args.no_proxy:
        url = "http://127.0.0.1:%d/index.html?desktop=1" % proxy.port
    else:
        url = "file://" + INDEX.replace(os.sep, "/")
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
        # ---- 配置持久化：QtWebEngine 的 localStorage 跨重启不落盘（实测 _profile
        #      从不生成、重启读回 None），所以改由桌面壳把配置存到自己的 JSON 文件。
        #      启动后把上次保存的配置注入回页面并刷新 UI。----
        _restore_config()
    page.loadFinished.connect(on_load_done)

    view.load(QUrl(url))
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
