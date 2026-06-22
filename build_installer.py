# -*- coding: utf-8 -*-
"""
Core Commander Installer Builder
Compiles installer.py into a standalone CoreCommander_Setup.exe.
"""

import os
import sys
import shutil
import subprocess  # nosec
from pathlib import Path

def get_qfluentwidgets_path():
    try:
        import qfluentwidgets
        return str(Path(qfluentwidgets.__file__).parent)
    except Exception:
        return None

def main():
    print("=" * 60)
    print("  Core Commander Setup Installer Compiler")
    print("=" * 60)
    
    zip_path = Path("dist/CoreCommander.zip")
    if not zip_path.exists():
        print(f"[ERROR] Required zip package '{zip_path}' does not exist!")
        print("Please compile the main app first via: python build.py")
        sys.exit(1)
        
    qfw_path = get_qfluentwidgets_path()
    if not qfw_path:
        print("[ERROR] qfluentwidgets package not found in current environment.")
        sys.exit(1)
        
    print(f"[INFO] Found QFluentWidgets path: {qfw_path}")
    
    # Define files to include
    datas_list = [
        ("dist/CoreCommander.zip", "."),
    ]
    
    resource_dirs = ['qss', 'i18n', '_rc', 'resources']
    for res_dir in resource_dirs:
        res_path = Path(qfw_path) / res_dir
        if res_path.exists():
            datas_list.append((str(res_path), f"qfluentwidgets/{res_dir}"))
            print(f"  [OK] Bundling QFluentWidgets resource: {res_dir}")
            
    # Convert paths to forward slashes for cross-platform spec format compatibility
    datas_str = ",\n        ".join([f"(r'{str(Path(src))}', '{dest}')" for src, dest in datas_list])
    
    spec_content = f'''# -*- mode: python ; coding: utf-8 -*-
# PyInstaller Spec Configuration for Core Commander Setup Installer

block_cipher = None

a = Analysis(
    ['installer.py'],
    pathex=[],
    binaries=[],
    datas=[
        {datas_str}
    ],
    hiddenimports=[
        'PySide6.QtCore',
        'PySide6.QtGui',
        'PySide6.QtWidgets',
        'qfluentwidgets',
        'win32com',
        'win32com.client',
        'win32api',
        'win32security',
        'pywintypes',
        'pythoncom',
        'lzma',
        'psutil',
        'win32service',
        'win32con',
        'win32timezone',
        'shiboken6',
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
        'matplotlib',
        'numpy',
        'scipy',
        'pandas',
        'polars',
        '_polars_runtime',
        '_polars_runtime_32',
        'pyarrow',
        'llvmlite',
        'numba',
        'librosa',
        'soundfile',
        'sounddevice',
        'resampy',
        'joblib',
        'faiss',
        'pyworld',
        'parselmouth',
        'torchcrepe',
        'torch',
        'torchvision',
        'torchaudio',
        'onnxruntime',
        'onnx',
        'fairseq',
        'rvc_python',
        'PySide6.QtWebEngineCore',
        'PySide6.QtWebEngineWidgets',
        'PySide6.QtPdf',
        'PySide6.QtPdfWidgets',
        'PySide6.QtWebEngine',
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
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='CoreCommander_Setup',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=r'图标.ico',
    uac_admin=True,
)
'''
    
    spec_file = Path("CoreCommander_Setup.spec")
    with open(spec_file, "w", encoding="utf-8") as f:
        f.write(spec_content)
    print("[INFO] Generated CoreCommander_Setup.spec")
    
    # Run PyInstaller
    cmd = [
        sys.executable,
        '-m',
        'PyInstaller',
        '--clean',
        '--noconfirm',
        '--distpath', 'dist3',
        'CoreCommander_Setup.spec'
    ]
    
    print(f"[INFO] Executing PyInstaller to bundle installer...")
    print(f"Executing: {' '.join(cmd)}\n")
    
    try:
        subprocess.run(cmd, check=True)  # nosec
        setup_exe = Path("dist3/CoreCommander_Setup.exe")
        if setup_exe.exists():
            size_mb = setup_exe.stat().st_size / (1024 * 1024)
            print("=" * 60)
            print("[OK] Packaging complete!")
            print(f"  [OK] Installer: {setup_exe.absolute()}")
            print(f"  [OK] File Size: {size_mb:.2f} MB")
            print("=" * 60)
            
            # Clean up build artifacts and any residual setup folder
            print("[INFO] Cleaning up installer build directories...")
            build_dir = Path("build/CoreCommander_Setup")
            if build_dir.exists():
                try:
                    shutil.rmtree(build_dir)
                except Exception as ex:
                    print(f"  [WARN] Failed to delete build folder: {ex}")
            setup_dir_residual = Path("dist3/CoreCommander_Setup")
            if setup_dir_residual.exists():
                try:
                    shutil.rmtree(setup_dir_residual)
                except Exception as ex:
                    print(f"  [WARN] Failed to delete residual setup folder: {ex}")
            if spec_file.exists():
                try:
                    spec_file.unlink()
                except Exception as ex:
                    print(f"  [WARN] Failed to delete spec file: {ex}")
            print("[OK] Clean up complete.")
        else:
            print("[ERROR] Output installer EXE not found.")
            sys.exit(1)
    except subprocess.CalledProcessError as e:
        print(f"[ERROR] PyInstaller compilation failed: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
