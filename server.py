#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
jianji 本地服务：静态文件 + API 代理

为什么需要它：
  智谱 / DeepSeek 等接口不返回 Access-Control-Allow-Origin，
  浏览器直连一律报 "Failed to fetch"。把请求先发到本地同源服务，
  再由 Python 转发出去，就彻底绕开 CORS。

用法：
  python server.py            # 默认 8000 端口
  python server.py 8080       # 指定端口

  打开 http://localhost:8000
  只监听 127.0.0.1，局域网里的其他设备访问不到（顺便也是安全兜底：
  代理本身不校验身份，谁能访问谁就能用你的 Key）。
"""

import json
import os
import sys
import urllib.error
import urllib.request
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.abspath(__file__))
PROXY_PATH = "/__proxy"
PING_PATH = "/__proxy_ping"
TIMEOUT = 300


class Handler(SimpleHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "jianji/1.0"

    def __init__(self, *a, **kw):
        # ROOT 可被 app.py 在打包场景下改写（指向 exe 旁的资源目录）
        root = os.environ.get("JIANJI_ROOT") or ROOT
        super().__init__(*a, directory=root, **kw)

    # 安静日志：只打静态请求，代理请求不打（避免刷屏）。
    # 注意：PyInstaller 打包的 GUI 程序没有控制台，sys.stderr 为 None，
    # 直接 write 会把每个静态请求的工作线程炸掉（表现为连接被服务端关闭、
    # QWebEngine 页面 load ok=False 白屏）。必须判空。
    def log_message(self, fmt, *args):
        if PROXY_PATH in self.path or PING_PATH in self.path:
            return
        if sys.stderr:
            sys.stderr.write("  %s %s\n" % (self.command, self.path))

    # ---------------- 工具 ----------------
    def _send_json(self, obj, code=200):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(data)

    # ---------------- GET ----------------
    def do_GET(self):
        if self.path.startswith(PING_PATH):
            self._send_json({"ok": True, "proxy": True, "root": ROOT})
            return
        return super().do_GET()

    def end_headers(self):
        if not self.path.startswith(PROXY_PATH):
            self.send_header("Cache-Control", "no-store")
        super().end_headers()

    # ---------------- 预检（同源其实不会触发，留着兜底） ----------------
    def do_OPTIONS(self):
        origin = self.headers.get("Origin") or "*"
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", origin)
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers",
                         "Content-Type, X-Target-URL, X-Target-Auth")
        self.send_header("Content-Length", "0")
        self.end_headers()

    # ---------------- POST /__proxy ----------------
    def do_POST(self):
        if not self.path.startswith(PROXY_PATH):
            self._send_json({"error": "not found"}, 404)
            return

        target = (self.headers.get("X-Target-URL") or "").strip()
        if not target.lower().startswith(("http://", "https://")):
            self._send_json({"error": "缺少或非法的 X-Target-URL"}, 400)
            return

        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""

        req = urllib.request.Request(target, data=body, method="POST")
        req.add_header("Content-Type",
                       self.headers.get("Content-Type") or "application/json")
        auth = self.headers.get("X-Target-Auth")
        if auth:
            req.add_header("Authorization", auth)
        req.add_header("Accept", "text/event-stream, application/json")

        try:
            upstream = urllib.request.urlopen(req, timeout=TIMEOUT)
        except urllib.error.HTTPError as e:
            detail = e.read()
            try:
                msg = json.loads(detail.decode("utf-8"))
            except Exception:
                msg = detail.decode("utf-8", "ignore")[:600]
            self.send_response(e.code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(detail)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(detail)
            sys.stderr.write("  上游 HTTP %s: %s\n" % (e.code, str(msg)[:200]))
            return
        except Exception as e:  # 连不上 / DNS / 超时
            self._send_json({"error": "连不上上游接口：%s" % e}, 502)
            sys.stderr.write("  上游连接失败：%s\n" % e)
            return

        # 流式透传：HTTP/1.1 + Connection: close + 无 Content-Length
        ctype = upstream.headers.get("Content-Type", "text/event-stream")
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-cache, no-transform")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("Access-Control-Allow-Origin", self.headers.get("Origin") or "*")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True

        try:
            while True:
                chunk = upstream.read(512)
                if not chunk:
                    break
                self.wfile.write(chunk)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:
            sys.stderr.write("  转发中断：%s\n" % e)
        finally:
            try:
                upstream.close()
            except Exception:
                pass


def create_server(port=8000):
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    srv.daemon_threads = True
    return srv


def main():
    port = 8000
    if len(sys.argv) > 1:
        try:
            port = int(sys.argv[1])
        except ValueError:
            pass

    srv = create_server(port)
    print("=" * 56)
    print(" 鉴己 · 本地服务已启动")
    print("=" * 56)
    print(" 打开： http://localhost:%d" % port)
    print(" 代理： /__proxy  ->  由本机转发到模型接口（绕过 CORS）")
    print(" 关闭窗口或 Ctrl+C 停止")
    print("=" * 56)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()
