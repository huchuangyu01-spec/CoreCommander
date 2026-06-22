# -*- coding: utf-8 -*-
"""
Core Commander - Automated Build Script
Uses PyInstaller to compile the modular PySide6 application into a standalone EXE.
"""

import os
import sys
import shutil
import io
import subprocess  # nosec
from pathlib import Path

# Force stdout and stderr to use UTF-8 to prevent UnicodeEncodeError in Windows GBK console environments
if sys.platform.startswith('win'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

def check_environment():
    """Validates the local packing environment."""
    print("=" * 60)
    print("[INFO] Checking build environment...")
    print("=" * 60)
    
    # Check Python version
    py_version = sys.version_info
    print(f"[OK] Python version: {py_version.major}.{py_version.minor}.{py_version.micro}")
    
    # Check necessary packages
    required_packages = {
        'PyInstaller': 'PyInstaller',
        'PySide6': 'PySide6',
        'QFluentWidgets': 'qfluentwidgets',
        'psutil': 'psutil',
        'pywin32': 'win32api'
    }
    
    missing = []
    for name, import_name in required_packages.items():
        try:
            if name == 'PyInstaller':
                import importlib.util
                spec = importlib.util.find_spec('PyInstaller')
                if spec is None:
                    raise ImportError
            else:
                __import__(import_name)
            print(f"[OK] {name} is installed")
        except ImportError:
            print(f"[FAIL] {name} is not installed")
            missing.append(name)
    
    if missing:
        print(f"\n[ERROR] Missing dependencies: {', '.join(missing)}")
        print("Please install them first: pip install " + " ".join(missing))
        return False
    
    print("\n[OK] Environment verification passed!\n")
    return True

def stop_windivert_services():
    """Stops and deletes any running WinDivert kernel driver services so that
    PyInstaller can overwrite WinDivert64.sys in the dist folder without a
    PermissionError (WinError 5)."""
    import ctypes
    known_services = [
        "WinDivert",
        "WinDivert1.3",
        "WinDivert1.4",
        "WinDivert2.0",
        "WinDivert2.2",
        "WinDivert14",
    ]
    stopped_any = False
    for svc in known_services:
        # Stop
        ret = subprocess.run(
            ["sc", "stop", svc],
            capture_output=True, text=True
        )
        if "SUCCESS" in ret.stdout or "STOP_PENDING" in ret.stdout:
            print(f"  [OK] Stopped WinDivert service: {svc}")
            stopped_any = True
        # Delete so it cannot auto-restart
        ret2 = subprocess.run(
            ["sc", "delete", svc],
            capture_output=True, text=True
        )
        if "SUCCESS" in ret2.stdout:
            print(f"  [OK] Deleted WinDivert service entry: {svc}")
    if stopped_any:
        import time
        time.sleep(2)  # Give kernel time to release the file handle
    # Force-unlock the file using takeown + icacls as a last resort
    target_sys = Path("dist") / "CoreCommander" / "_internal" / "pydivert" / "windivert_dll" / "WinDivert64.sys"
    if target_sys.exists():
        subprocess.run(["takeown", "/f", str(target_sys), "/a"], capture_output=True)
        subprocess.run(["icacls", str(target_sys), "/grant", "Administrators:F"], capture_output=True)
        try:
            target_sys.unlink()
            print(f"  [OK] Force-removed locked: {target_sys}")
        except Exception as e:
            print(f"  [WARN] Could not remove {target_sys}: {e}")


def clean_build():
    """Cleans up legacy build directories and files."""
    print("[INFO] Cleaning up previous build outputs...")
    # Must stop any running WinDivert kernel service BEFORE removing dist/,
    # otherwise WinDivert64.sys will be locked and shutil.rmtree() raises WinError 5.
    stop_windivert_services()

    dirs_to_clean = ['build', 'dist', '__pycache__']
    files_to_clean = ['*.spec']
    
    for dir_name in dirs_to_clean:
        if os.path.exists(dir_name):
            try:
                shutil.rmtree(dir_name)
                print(f"  [OK] Removed {dir_name}/")
            except Exception as e:
                print(f"  [WARN] Failed to remove {dir_name}/: {e}. Attempting to clean contents...")
                # Try to clean contents inside directory
                for root, dirs, files in os.walk(dir_name, topdown=False):
                    for name in files:
                        try: os.remove(os.path.join(root, name))
                        except Exception: pass  # nosec
                    for name in dirs:
                        try: os.rmdir(os.path.join(root, name))
                        except Exception: pass  # nosec
    
    for pattern in files_to_clean:
        for file in Path('.').glob(pattern):
            file.unlink()
            print(f"  [OK] Removed {file}")
    
    # Delete backup/old compilation pyd files in core_commander/core/
    core_dir = Path("core_commander/core")
    if core_dir.exists():
        for pattern in ["*.pyd.bak", "*.pyd.old*"]:
            for file in core_dir.glob(pattern):
                try:
                    file.unlink()
                    print(f"  [OK] Removed old build file: {file}")
                except Exception as e:
                    print(f"  [WARN] Failed to remove {file}: {e}")
    
    print()

def get_qfluentwidgets_path():
    """Gets the path of the installed qfluentwidgets package."""
    try:
        import qfluentwidgets
        qfw_path = Path(qfluentwidgets.__file__).parent
        return str(qfw_path)
    except Exception:
        return None

def create_spec_file():
    """Generates the custom .spec configuration for PyInstaller."""
    print("[INFO] Generating PyInstaller spec configuration...")
    
    qfw_path = get_qfluentwidgets_path()
    
    # Check present resource files in qfluentwidgets
    datas_list = []
    if qfw_path:
        resource_dirs = ['qss', 'i18n', '_rc', 'resources']
        for res_dir in resource_dirs:
            res_path = Path(qfw_path) / res_dir
            if res_path.exists():
                datas_list.append(f"(qfw_path + '/{res_dir}', 'qfluentwidgets/{res_dir}')")
                print(f"  [OK] Found QFluentWidgets resource: {res_dir}")
                
    # Add custom core_commander resources (power plans, executables, script files, etc.)
    datas_list.append("('core_commander/resources/', 'core_commander/resources')")
    
    # Add rapidocr_onnxruntime model resources dynamically
    try:
        import rapidocr_onnxruntime
        rapidocr_path = Path(rapidocr_onnxruntime.__file__).parent
        # Add config.yaml
        datas_list.append(f"(r'{rapidocr_path}/config.yaml', 'rapidocr_onnxruntime')")
        # Add models folder containing onnx files
        datas_list.append(f"(r'{rapidocr_path}/models/*.onnx', 'rapidocr_onnxruntime/models')")
        print("  [OK] Added rapidocr_onnxruntime resource files to datas list.")
    except Exception as e:
        print(f"  [WARN] Failed to add rapidocr_onnxruntime path to datas list: {e}")
    
    datas_str = ',\n        '.join(datas_list) if datas_list else ''
    
    spec_content = f'''# -*- mode: python ; coding: utf-8 -*-
# PyInstaller Spec Configuration for Core Commander

import os

block_cipher = None
qfw_path = r'{qfw_path}'

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[
        # QFluentWidgets resources
        {datas_str}
    ],
    hiddenimports=[
        'core_commander.core.guard', 'core_commander.core.license', 'core_commander.core.hwid', 'core_commander.core.throttler',
        'rsa',
        'PySide6.QtCore',
        'PySide6.QtGui',
        'PySide6.QtWidgets',
        'PySide6.QtSvg',
        'PySide6.QtXml',
        'PySide6.QtNetwork',
        'qfluentwidgets',
        'qfluentwidgets.common',
        'qfluentwidgets.components',
        'qfluentwidgets.window',
        'qfluentwidgets.components.dialog_box',
        'qfluentwidgets.components.widgets',
        'qfluentwidgets._rc',
        'win32api',
        'win32security',
        'pywintypes',
        'pythoncom',
        'pynvml',
        'core_commander',
        'core_commander.config',
        'core_commander.config.exceptions',
        'core_commander.config.settings',
        'core_commander.core',
        'core_commander.core.topology',
        'core_commander.core.memory',
        'core_commander.core.power',
        'core_commander.core.isolation',
        'core_commander.core.worker',
        'core_commander.core.fps_collector',
        'core_commander.core.gpu_drs',
        'core_commander.core.gpu_smi',
        'core_commander.core.irq_aff',
        'core_commander.core.latency_monitor',
        'core_commander.core.rtss_collector',
        'core_commander.core.system_tweaks',
        'core_commander.core.watchdog',
        'core_commander.ui',
        'core_commander.ui.window',
        'core_commander.ui.components',
        'core_commander.ui.dialogs',
        'core_commander.ui.pages',
        'core_commander.ui.pages.home',
        'core_commander.ui.pages.settings',
        'core_commander.ui.pages.about',
        'core_commander.ui.pages.logs',
        'core_commander.ui.pages.startup',
        'core_commander.utils',
        'core_commander.utils.logger',
        'core_commander.utils.admin',
        'core_commander.utils.device',
        'core_commander.utils.i18n',
        'core_commander.utils.process',
        'core_commander.utils.stderr_hook',
        'requests',
        'cryptography',
        'cryptography.hazmat.primitives.ciphers.aead',
        'shiboken6',
        'joblib',
        'joblib.externals.loky',
        'joblib.externals.cloudpickle',
        'joblib.externals.loky.backend',
        'sympy',
        'mpmath',
        'hydra.experimental',
        'bitarray',
        'regex',
        'portalocker',
        'sacrebleu',
        'omegaconf',
        'antlr4',
        'scipy',
        'scipy.io',
        'scipy.io.wavfile',
        'scipy.signal',
        'scipy.integrate',
        'scipy.interpolate',
        'librosa',
        'librosa.filters',
        'librosa.util',
        'soundfile',
        'pyworld',
        'parselmouth',
        'torchcrepe',
        'faiss',
        'av',
        'loguru',
        'tqdm',
        'pydantic',
        'fastapi',
        'uvicorn',
        'huggingface_hub',
    ],
    hookspath=[],
    hooksconfig={{}},
    runtime_hooks=['pyi_rth_force_pyside6.py'],
    excludes=[
        'PyQt5',
        'PyQt5.QtCore',
        'PyQt5.QtGui',
        'PyQt5.QtWidgets',
        'PyQt6',
        'PyQt6.QtCore',
        'PyQt6.QtGui',
        'PyQt6.QtWidgets',
        'tkinter',
        'matplotlib',
        'torch',
        'torchvision',
        'torchaudio',
        'onnxruntime',
        'onnx',
        'pandas',
        'polars',
        '_polars_runtime',
        '_polars_runtime_32',
        'pyarrow',
        'PySide6.QtWebEngineCore',
        'PySide6.QtWebEngineWidgets',
        'PySide6.QtPdf',
        'PySide6.QtPdfWidgets',
        'PySide6.QtWebEngine',
        'Cython',
        'botocore',
        'boto3',
        's3transfer',
        'PySide6.Qt3DCore',
        'PySide6.Qt3DRender',
        'PySide6.Qt3DInput',
        'PySide6.Qt3DLogic',
        'PySide6.Qt3DExtras',
        'PySide6.Qt3DQuick',
        'PySide6.QtCharts',
        'PySide6.QtDataVisualization',
        'PySide6.QtDesigner',
        'PySide6.QtVirtualKeyboard',
        'PySide6.QtGraphs',
        'PySide6.QtPrintSupport',
        'PySide6.QtMultimedia',
        'PySide6.QtMultimediaWidgets',
        'PySide6.QtOpenGL',
        'PySide6.QtOpenGLWidgets',
        'PySide6.QtQml',
        'PySide6.QtQuick',
        'PySide6.QtQuickWidgets',
        'fairseq',
        'rvc_python',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='CoreCommander',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=r'图标.ico',
    uac_admin=True,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='CoreCommander',
)
'''
    
    with open('CoreCommander.spec', 'w', encoding='utf-8') as f:
        f.write(spec_content)
    
    print("  [OK] Generated CoreCommander.spec file.\n")

def build_exe():
    """Runs the PyInstaller packaging step."""
    print("=" * 60)
    print("[INFO] Launching PyInstaller packager...")
    print("=" * 60)
    print()
    
    cmd = [
        sys.executable,
        '-m',
        'PyInstaller',
        '--noconfirm',
        'CoreCommander.spec'
    ]
    
    print(f"Executing: {' '.join(cmd)}\n")
    
    try:
        subprocess.run(cmd, check=True)  # nosec
        return True
    except subprocess.CalledProcessError as e:
        print(f"\n[FAIL] PyInstaller failed to execute: {e}")
        return False

def verify_build():
    """Checks the output EXE size and presence, then signs it with overlay signature."""
    print("\n" + "=" * 60)
    print("[INFO] Verifying package output...")
    print("=" * 60)
    
    exe_path = Path('dist/CoreCommander/CoreCommander.exe')
    
    if exe_path.exists():
        # Sign the executable using Overlay HMAC-SHA256
        print("[INFO] Signing CoreCommander.exe overlay signature...")
        import hmac
        import hashlib
        try:
            with open(exe_path, "rb") as f:
                content = f.read()
            
            # Key must match the hardcoded one in guard.py
            secret_key = b"CoreCommanderOverlayIntegritySecretKey_2026"
            computed_sig = hmac.new(secret_key, content, hashlib.sha256).digest()
            
            with open(exe_path, "ab") as f:
                f.write(computed_sig)
                
            print("[OK] Executable overlay signed successfully.")
        except Exception as sign_err:
            print(f"[FAIL] Overlay signing failed: {sign_err}")
            return False

        total_size = sum(f.stat().st_size for f in Path('dist/CoreCommander').rglob('*') if f.is_file())
        size_mb = total_size / (1024 * 1024)
        print("[OK] Packaging complete!")
        print(f"  [OK] File Location: {exe_path.absolute()}")
        print(f"  [OK] Directory Size: {size_mb:.2f} MB")
        return True
    else:
        print("[FAIL] Output file CoreCommander.exe not found under dist/CoreCommander/ folder.")
        return False

def compress_dist():
    """Compresses the onedir output directory dist/CoreCommander/ into dist/CoreCommander.zip."""
    import zipfile
    print("\n" + "=" * 60)
    print("[INFO] Compressing CoreCommander directory into ZIP...")
    print("=" * 60)
    
    src_dir = Path('dist/CoreCommander')
    zip_path = Path('dist/CoreCommander.zip')
    
    if not src_dir.exists():
        print(f"[FAIL] Source directory {src_dir} not found for compression.")
        return False
        
    try:
        if zip_path.exists():
            zip_path.unlink()
            
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_LZMA) as zipf:
            for root, dirs, files in os.walk(src_dir):
                for file in files:
                    file_path = Path(root) / file
                    if not file_path.exists():
                        continue
                    try:
                        rel_path = file_path.relative_to(src_dir)
                        zipf.write(str(file_path), str(rel_path))
                    except Exception as fe:
                        print(f"  [WARN] Skipping file due to error: {file_path} - {fe}")
                    
        size_mb = zip_path.stat().st_size / (1024 * 1024)
        print(f"[OK] ZIP compression complete: {zip_path.absolute()}")
        print(f"  [OK] ZIP file size: {size_mb:.2f} MB")
        return True
    except Exception as e:
        print(f"[FAIL] Compression failed: {e}")
        return False

def create_readme():
    """Generates user documentation for the standalone distribution."""
    readme_content = """# Core Commander - Standalone Distribution Release Guide

## Files included:
- `CoreCommander.exe` - Unified stand-alone executable

## How to run:
1. Right click on `CoreCommander.exe`.
2. Select "Run as Administrator" (以管理员身份运行).
3. If UAC elevated prompt appears, click Yes.

## Key considerations:
- Must run with administrative privileges.
- First launch may take 10-30 seconds to unpack dependencies into temporary cache.
- Compatible with Windows 10 and Windows 11.

---
Enjoy modern optimization!
"""
    
    with open('dist/README.txt', 'w', encoding='utf-8') as f:
        f.write(readme_content)
    
    print("\n[OK] Generated user guide: dist/README.txt")

def compile_cython_modules():
    """Compiles the core security guard module into a native .pyd binary using Cython."""
    # Ensure source files are restored from backups before Cython compilation
    restore_guard_source()
    
    print("=" * 60)
    print("[INFO] Compiling security guard module with Cython...")
    print("=" * 60)
    
    setup_code = """
from setuptools import setup, Extension
from Cython.Build import cythonize
import sys
import os

ext_guard = Extension(
    "core_commander.core.guard",
    [os.path.normpath("core_commander/core/guard.py")],
    extra_compile_args=["/O2", "/Oy", "/GL", "/Gy"],
    extra_link_args=["/LTCG", "/OPT:REF", "/OPT:ICF", "/DEBUG:NONE"]
)

ext_license = Extension(
    "core_commander.core.license",
    [os.path.normpath("core_commander/core/license.py")],
    extra_compile_args=["/O2", "/Oy", "/GL", "/Gy"],
    extra_link_args=["/LTCG", "/OPT:REF", "/OPT:ICF", "/DEBUG:NONE"]
)

setup(
    ext_modules=cythonize(
        [ext_guard, ext_license],
        compiler_directives={
            'language_level': "3",
            'boundscheck': False,
            'wraparound': False,
            'initializedcheck': False,
            'nonecheck': False,
            'cdivision': True,
            'optimize.use_switch': True,
            'optimize.unpack_method_calls': True
        }
    ),
    script_args=["build_ext", "--inplace"]
)
"""
    setup_file = "temp_setup_cython.py"
    with open(setup_file, "w", encoding="utf-8") as f:
        f.write(setup_code)
        
    try:
        cmd = [sys.executable, setup_file]
        print(f"Executing: {' '.join(cmd)}")
        subprocess.run(cmd, check=True)
        print("[OK] Cython compilation complete!")
    except Exception as e:
        print(f"[FAIL] Cython compilation failed: {e}")
        if os.path.exists(setup_file):
            os.remove(setup_file)
        sys.exit(1)
        
    if os.path.exists(setup_file):
        os.remove(setup_file)
        
    # Clean up setuptools temporary files under build directory
    try:
        shutil.rmtree("build/temp.win-amd64-cpython-311", ignore_errors=True)
        shutil.rmtree("build/temp.win-amd64-3.11", ignore_errors=True)
    except Exception:
        pass

    core_dir = Path("core_commander/core")
    
    # Process compiled extensions
    for name in ["guard", "license"]:
        pyd_files = list(core_dir.glob(f"{name}.*.pyd"))
        if not pyd_files:
            pyd_files = list(core_dir.glob(f"{name}.pyd"))
            
        if pyd_files:
            compiled_pyd = pyd_files[0]
            target_pyd = core_dir / f"{name}.pyd"
            if compiled_pyd != target_pyd:
                if target_pyd.exists():
                    target_pyd.unlink()
                shutil.copy(compiled_pyd, target_pyd)
                compiled_pyd.unlink()
                print(f"[OK] Renamed compiled extension to: {target_pyd}")
                
            # Hide original .py to enforce .pyd loading
            raw_py = core_dir / f"{name}.py"
            if raw_py.exists():
                backup_py = core_dir / f"{name}.py.bak"
                if backup_py.exists():
                    backup_py.unlink()
                shutil.move(raw_py, backup_py)
                print(f"[OK] Moved raw {name}.py to {name}.py.bak to enforce .pyd loading during packaging.")
                
            c_file = core_dir / f"{name}.c"
            if c_file.exists():
                c_file.unlink()
        else:
            print(f"[FAIL] Compiled .pyd for {name} not found in core_commander/core/")
            sys.exit(1)

def restore_guard_source():
    """Restores the original guard.py and license.py source code from backups."""
    core_dir = Path("core_commander/core")
    for name in ["guard", "license"]:
        raw_py = core_dir / f"{name}.py"
        backup_py = core_dir / f"{name}.py.bak"
        if backup_py.exists():
            if raw_py.exists():
                raw_py.unlink()
            shutil.move(backup_py, raw_py)
            print(f"[OK] Restored raw {name}.py from {name}.py.bak")

def post_build_cleanup():
    """Physically removes large, unused modules and DLLs from the dist folder
    that might have been implicitly pulled in by PyInstaller hooks (e.g., torch,
    onnxruntime, polars, opencv, pyarrow)."""
    print("\n" + "=" * 60)
    print("[INFO] Performing post-build dependency cleanup...")
    print("=" * 60)
    
    internal_dir = Path("dist/CoreCommander/_internal")
    if not internal_dir.exists():
        print("[WARN] Target internal folder does not exist. Skipping post-build cleanup.")
        return

    # Folder/file paths relative to dist/CoreCommander/_internal
    unwanted_paths = [
        "torch",
        "torchvision",
        "torchaudio",
        "onnxruntime",
        "onnx",
        "pandas",
        "polars",
        "_polars_runtime.pyd",
        "_polars_runtime_32.pyd",
        "pyarrow",
        "Cython",
        "botocore",
        "boto3",
        "s3transfer",
        "PySide6/opengl32sw.dll",
        "PySide6/Qt6Pdf.dll",
        "PySide6/Qt6PdfWidgets.dll",
        "PySide6/Qt6Quick3DRuntimeRender.dll",
        "PySide6/Qt63DRender.dll",
        "PySide6/Qt63DCore.dll",
        "PySide6/Qt63DQuick.dll",
        "PySide6/Qt63DInput.dll",
        "PySide6/Qt63DLogic.dll",
        "PySide6/Qt63DExtras.dll",
        "PySide6/Qt6Graphs.dll",
        "PySide6/Qt6VirtualKeyboard.dll",
        "PySide6/Qt6WebEngineCore.dll",
        "PySide6/Qt6WebEngineWidgets.dll",
        "PySide6/Qt6WebEngine.dll",
        "PySide6/Qt6Designer.dll",
        "PySide6/Qt6Qml.dll",
        "PySide6/Qt6QmlModels.dll",
        "PySide6/Qt6Quick.dll",
        "PySide6/Qt6QuickWidgets.dll",
        "PySide6/Qt6Multimedia.dll",
        "PySide6/Qt6OpenGL.dll",
        "PySide6/Qt6PrintSupport.dll",
        "PySide6/Qt6Positioning.dll",
        "PySide6/Qt6Sensors.dll",
        "PySide6/Qt6ShaderTools.dll",
    ]

    for p in unwanted_paths:
        target = internal_dir / p
        if target.exists():
            try:
                if target.is_dir():
                    shutil.rmtree(target)
                    print(f"  [OK] Cleaned directory: {p}")
                else:
                    target.unlink()
                    print(f"  [OK] Cleaned file: {p}")
            except Exception as e:
                print(f"[WARN] Failed to delete {p}: {e}")
    print("[OK] Post-build cleanup complete.\n")

def copy_required_assets():
    """Copies required asset folders (vst3, crosshairs, config files) that are not packed by PyInstaller."""
    print("=" * 60)
    print("[INFO] Copying asset and config directories...")
    print("=" * 60)
    
    # Define mapping: (source, destination relative to dist/CoreCommander)
    mappings = [
        ("core_commander/assets", "core_commander/assets"),
        ("assets", "assets"),
        ("data", "_internal/data"),
        ("core_commander/data", "_internal/core_commander/data"),
    ]
    
    for src, dst in mappings:
        src_path = Path(src)
        dst_path = Path("dist/CoreCommander") / dst
        if src_path.exists():
            try:
                if dst_path.exists():
                    shutil.rmtree(dst_path)
                shutil.copytree(src_path, dst_path)
                print(f"  [OK] Copied {src} to {dst_path}")
            except Exception as e:
                print(f"  [WARN] Failed to copy {src} to {dst_path}: {e}")
        else:
            print(f"  [WARN] Source path {src} does not exist. Skipping.")

    # Copy fairseq recursively from Python site-packages without importing it (to avoid dataclasses mutable default errors on Python 3.11)
    try:
        import importlib.util
        spec = importlib.util.find_spec("fairseq")
        if spec is not None and spec.submodule_search_locations:
            fairseq_src = Path(spec.submodule_search_locations[0])
            fairseq_dst = Path("dist/CoreCommander/_internal/fairseq")
            if fairseq_dst.exists():
                shutil.rmtree(fairseq_dst)
            shutil.copytree(fairseq_src, fairseq_dst, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"))
            print(f"  [OK] Dynamic copy: Copied fairseq package to {fairseq_dst}")
        else:
            print("  [WARN] Dynamic copy: fairseq package spec not found.")
    except Exception as e:
        print(f"  [WARN] Dynamic copy: Failed to copy fairseq package: {e}")
    print()

    # Copy rvc_python recursively from Python site-packages (to ensure non-py asset configs like json files are physically present in the filesystem)
    try:
        import importlib.util
        spec = importlib.util.find_spec("rvc_python")
        if spec is not None and spec.submodule_search_locations:
            rvc_src = Path(spec.submodule_search_locations[0])
            rvc_dst = Path("dist/CoreCommander/_internal/rvc_python")
            if rvc_dst.exists():
                shutil.rmtree(rvc_dst)
            # Exclude large model files (pt, pth, onnx, zip) and caches to keep the installer light
            ignore_pattern = shutil.ignore_patterns("*.pt", "*.pth", "*.onnx", "*.zip", "__pycache__", "*.pyc", "*.pyo")
            shutil.copytree(rvc_src, rvc_dst, ignore=ignore_pattern)
            print(f"  [OK] Dynamic copy: Copied rvc_python package (without large models) to {rvc_dst}")
            
            # Patch download_model.py in _internal/rvc_python to use hf-mirror.com and add exception safety
            dl_model_py = rvc_dst / "download_model.py"
            if dl_model_py.exists():
                try:
                    content = dl_model_py.read_text(encoding="utf-8")
                    content = content.replace("https://huggingface.co", "https://hf-mirror.com")
                    old_get = "            response = requests.get(url)"
                    new_get = """            try:
                response = requests.get(url, timeout=30)
            except Exception as e:
                print(f"Failed to download {filename} from {url}: {e}")
                continue"""
                    content = content.replace(old_get, new_get)
                    dl_model_py.write_text(content, encoding="utf-8")
                    print("  [OK] Dynamic patch: Configured download_model.py to use hf-mirror.com and added exception safety.")
                except Exception as patch_err:
                    print(f"  [WARN] Failed to patch download_model.py: {patch_err}")
        else:
            print("  [WARN] Dynamic copy: rvc_python package spec not found.")
    except Exception as e:
        print(f"  [WARN] Dynamic copy: Failed to copy rvc_python package: {e}")
    print()

def main():
    """Main execution sequence."""
    print("\n" + "=" * 60)
    print("  Core Commander - Packaging Tool")
    print("=" * 60)
    print()
    
    # 1. Environment check
    if not check_environment():
        sys.exit(1)
    
    # 2. Cleanup
    clean_build()
    
    try:
        # 2.5 Compile Cython pyd module
        compile_cython_modules()
        
        # 3. Create specs
        create_spec_file()
        
        # 4. Packaging
        if not build_exe():
            print("\n[FAIL] Packaging process encountered errors. Please check the log output above.")
            restore_guard_source()
            sys.exit(1)
            
        # 4.5 Post-build cleanup of heavy dependencies
        post_build_cleanup()
        
        # 4.7 Copy required assets and configs
        copy_required_assets()
        
        # 5. Verify outputs
        if not verify_build():
            restore_guard_source()
            sys.exit(1)
            
        # 5.5 Compress into ZIP for installer
        if not compress_dist():
            restore_guard_source()
            sys.exit(1)
        
        # 6. Documentation
        create_readme()
    finally:
        # Always restore source code
        restore_guard_source()
    
    # 7. Complete
    print("\n" + "=" * 60)
    print("[DONE] Build operations completed successfully!")
    print("=" * 60)
    print("\nOutput Folder: dist/")
    print("Zip Package: dist/CoreCommander.zip")
    print("\nTips:")
    print("  - Right click and run as Administrator.")
    print("  - Distribution folder 'dist/' is ready for packaging/archiving.")
    print("\nEnjoy using Core Commander!\n")

if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n[WARN] User interrupted packaging process.")
        sys.exit(1)
    except Exception as e:
        print(f"\n\n[ERROR] Unhandled build exception: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
