# 鉴己 · 笔记洞察 · 桌面版（PyQt6）打包配置
# 用法：python -m PyInstaller jianji_qt.spec --noconfirm --clean
# 产物：dist/jianji-qt/（文件夹：exe + 资源，knowledge/ 可热改）
#
# PyQt6 + QtWebEngine 打包要点：
#   - collect_all('PyQt6') 收全 Qt6/bin 下的 dll、plugins、QWebEngineProcess.exe、
#     以及 QtWebEngineProcess 用的 icudtl/chromium 资源
#   - hiddenimports 补上 PyInstaller 静态分析漏掉的动态导入
#   - 资源（index.html / knowledge/ 等）放 exe 旁，改 knowledge 免重打

from PyInstaller.utils.hooks import collect_all
import os

# PyInstaller 执行 spec 时注入 SPEC（spec 文件完整路径）；spec 就在项目根
ROOT = os.path.dirname(os.path.abspath(SPEC))

# 全量收集 PyQt6 + QtWebEngine（QWebEngineProcess.exe 在 webengine 包里）
qt_datas, qt_bins, qt_hidden = [], [], []
for pkg in ('PyQt6', 'pyqt6_webengine', 'pyqt6_webengine_qt6'):
    try:
        d, b, h = collect_all(pkg)
        qt_datas += d
        qt_bins += b
        qt_hidden += h
    except Exception as e:
        print('  collect_all(%s) skipped: %s' % (pkg, e))

# 关键：QWebEngineProcess.exe（WebEngine 渲染进程）必须收全。
# collect_all('PyQt6') 会收 PyQt6/Qt6/bin 下的 dll，但漏掉 QWebEngineProcess.exe。
# 显式按 site-packages 绝对路径补上。
import glob
import sys as _sys
import site as _site

_qt_bin = None
_sp_list = list(_site.getsitepackages()) + list(_site.getusersitepackages())
# 当前解释器的 site-packages 也加上
_sp_list.append(os.path.join(os.path.dirname(_sys.executable), 'Lib', 'site-packages'))
# 常见 PyQt6 Qt6/bin 安装位置（按本机实际追加）：
#   Windows 系统级:  <Python 安装目录>\Lib\site-packages\PyQt6\Qt6\bin
#   pip 用户级:      %APPDATA%\Python\Python3xx\site-packages\PyQt6\Qt6\bin
_known = []
for _c in _known + [os.path.join(_sp, 'PyQt6', 'Qt6', 'bin') for _sp in _sp_list if _sp]:
    if os.path.isdir(_c):
        _qt_bin = _c
        break

if _qt_bin:
    _count = 0
    for _f in glob.glob(os.path.join(_qt_bin, '*')):
        _b = os.path.basename(_f)
        if _b.endswith(('.exe', '.dll')):
            qt_bins.append((_f, 'PyQt6/Qt6/bin'))
            _count += 1
    print('  收齐 Qt6/bin:', _qt_bin, '(%d exe/dll)' % _count)
else:
    print('  [warn] 没找到 PyQt6/Qt6/bin，QWebEngineProcess.exe 可能缺失')
CORE_DATAS = [
    (os.path.join(ROOT, 'index.html'), '.'),
    (os.path.join(ROOT, 'knowledge.js'), '.'),
    (os.path.join(ROOT, 'prompts.md'), '.'),
    (os.path.join(ROOT, 'server.py'), '.'),
    (os.path.join(ROOT, 'jianji_insight.py'), '.'),
    (os.path.join(ROOT, 'build_knowledge.py'), '.'),
    (os.path.join(ROOT, 'jianji.ico'), '.'),
    (os.path.join(ROOT, 'knowledge'), 'knowledge'),
    (os.path.join(ROOT, 'sample'), 'sample'),
    (os.path.join(ROOT, 'README.md'), '.'),
    (os.path.join(ROOT, '桌面版说明.md'), '.'),
]

a = Analysis(
    [os.path.join(ROOT, 'desktop_qt', 'main.py')],
    pathex=[ROOT, os.path.join(ROOT, 'desktop_qt')],
    binaries=qt_bins,
    datas=qt_datas + CORE_DATAS,
    hiddenimports=qt_hidden + [
        'PyQt6.QtCore', 'PyQt6.QtGui', 'PyQt6.QtWidgets',
        'PyQt6.QtWebEngineWidgets', 'PyQt6.QtWebEngineCore',
        'PyQt6.QtNetwork',
        'server',  # main.py 里 import server（项目根）
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=['tkinter'],  # 不用 tkinter，省体积
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='jianji-qt',
    debug=False,
    strip=False,
    upx=False,
    console=False,  # 无控制台窗口
    icon=os.path.join(ROOT, 'jianji.ico'),  # 禅圆（enso+金点），见 桌面\jianji-icons\03-ens
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name='jianji-qt',
)

# 把可编辑资源从 _internal/ 搬到 exe 旁（main.py 打包模式 resource_root() 返回 exe 所在目录）
# 这样改 knowledge/*.md 后跑 build_knowledge.py 直接替换，免重打
import shutil
dist_dir = os.path.join(DISTPATH, 'jianji-qt')
_internal = os.path.join(dist_dir, '_internal')
for name in ['knowledge', 'sample']:
    src = os.path.join(_internal, name)
    dst = os.path.join(dist_dir, name)
    if os.path.isdir(src):
        shutil.copytree(src, dst, dirs_exist_ok=True)
        print("  -> exe 旁：", name)
for name in ['index.html', 'knowledge.js', 'server.py', 'jianji_insight.py',
             'build_knowledge.py', 'jianji.ico', 'prompts.md', 'README.md', '桌面版说明.md']:
    src = os.path.join(_internal, name)
    dst = os.path.join(dist_dir, name)
    if os.path.exists(src):
        shutil.move(src, dst)
        print("  -> exe 旁：", name)

# 内置嵌入式 Python（目标机无 Python 时，降级 spawn / CLI 直接可用；纯标准库零依赖）
_rt = os.path.join(ROOT, 'runtime')
if os.path.isdir(_rt):
    shutil.copytree(_rt, os.path.join(dist_dir, 'runtime'), dirs_exist_ok=True)
    print("  -> exe 旁：runtime/ (嵌入式 Python, %d 文件)" % len(os.listdir(_rt)))

# 打包完成：删掉 PyInstaller 的中间产物 build/（spec 同目录下的 build/），只留 dist/ 产物
# 这样每次打包后项目根目录保持干净
import shutil as _sh
_build = os.path.join(os.path.dirname(os.path.abspath(SPEC)), 'build')
if os.path.isdir(_build):
    _sh.rmtree(_build, ignore_errors=True)
    print("  已清理 build/（中间产物）")
