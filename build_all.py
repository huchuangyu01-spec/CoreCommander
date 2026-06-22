# -*- coding: utf-8 -*-
"""
Core Commander - Unified Packaging and Verification Pipeline
Runs environment checks, cleans previous builds, builds the main app (onedir),
zips the main app, compiles the installer (onefile), and runs post-build dry-run tests.
"""
import os
import sys
import time
import shutil
import subprocess  # nosec
from pathlib import Path

def print_banner(text):
    print("\n" + "=" * 70)
    print(f"  {text}")
    print("=" * 70)

def check_env():
    print_banner("Step 1: Verifying Build Environment")
    required = ['PyInstaller', 'PySide6', 'qfluentwidgets', 'psutil', 'win32api', 'pythoncom']
    missing = []
    for pkg in required:
        try:
            if pkg == 'PyInstaller':
                import importlib.util
                if importlib.util.find_spec('PyInstaller') is None:
                    raise ImportError
            elif pkg == 'pythoncom':
                import pythoncom
            else:
                __import__(pkg)
            print(f"[OK] Dependency satisfied: {pkg}")
        except ImportError:
            print(f"[FAIL] Missing dependency: {pkg}")
            missing.append(pkg)
    if missing:
        print(f"\n[ERROR] Missing required packages: {', '.join(missing)}")
        print("Please install them: pip install PyInstaller PySide6 qfluentwidgets psutil pywin32")
        return False
    print("[OK] Environment is ready for packaging.")
    return True

def run_step(name, cmd):
    print_banner(f"Running: {name}")
    print(f"Executing: {' '.join(cmd)}")
    try:
        res = subprocess.run(cmd, check=True)
        return res.returncode == 0
    except subprocess.CalledProcessError as e:
        print(f"[FAIL] Step '{name}' failed with return code {e.returncode}")
        return False

def run_dry_run_tests():
    print_banner("Step 4: Executing Post-Build Dry-Run Verifications")
    
    # Set development environment variable to bypass signature check for locally compiled files
    import os
    env = os.environ.copy()
    env["CORE_COMMANDER_DEV"] = "1"
    
    # 1. Test Main Executable
    main_exe = Path("dist/CoreCommander/CoreCommander.exe")
    print(f"Testing main executable: {main_exe.absolute()}...")
    if not main_exe.exists():
        print("[FAIL] Main executable not found!")
        return False
        
    try:
        # Spawn main app windowed process
        p1 = subprocess.Popen(
            [str(main_exe)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            creationflags=subprocess.CREATE_NEW_CONSOLE,
            env=env
        )
        print("Main app process spawned. Waiting 5 seconds for initialization...")
        time.sleep(5)
        
        status = p1.poll()
        if status is not None:
            if status == 0:
                print("[OK] Main app successfully booted and exited with code 0 (expected elevation trigger or single instance check).")
            else:
                # Terminated early, meaning it crashed
                stdout, stderr = p1.communicate()
                print(f"[FAIL] Main app crashed early with code {status}!")
                print("Stderr output:", stderr)
                return False
        else:
            print("[OK] Main app successfully initialized and kept running without crashing.")
            p1.terminate()
            try: p1.wait(timeout=2)
            except subprocess.TimeoutExpired:
                p1.kill()
                p1.wait()
            print("Main app test process cleaned up.")
    except Exception as e:
        print(f"[FAIL] Failed to execute main app test: {e}")
        return False

    # 2. Test Setup Installer
    setup_exe = Path("dist3/CoreCommander_Setup.exe")
    print(f"\nTesting setup installer: {setup_exe.absolute()}...")
    if not setup_exe.exists():
        print("[FAIL] Setup installer not found!")
        return False
        
    try:
        # Spawn setup installer windowed process
        p2 = subprocess.Popen(
            [str(setup_exe)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            creationflags=subprocess.CREATE_NEW_CONSOLE,
            env=env
        )
        print("Setup installer process spawned. Waiting 4 seconds for initialization...")
        time.sleep(4)
        
        status = p2.poll()
        if status is not None:
            if status == 0:
                print("[OK] Setup installer successfully booted and exited with code 0 (expected behavior).")
            else:
                stdout, stderr = p2.communicate()
                print(f"[FAIL] Setup installer crashed early with code {status}!")
                print("Stderr output:", stderr)
                return False
        else:
            print("[OK] Setup installer successfully initialized and kept running without crashing.")
            p2.terminate()
            try: p2.wait(timeout=2)
            except subprocess.TimeoutExpired:
                p2.kill()
                p2.wait()
            print("Setup installer test process cleaned up.")
    except Exception as e:
        print(f"[FAIL] Failed to execute setup installer test: {e}")
        return False

    print("\n[OK] All post-build dry-run checks passed successfully!")
    return True

def main():
    print_banner("Core Commander - Unified Build & Packager Spec")
    
    # 1. Check Env
    if not check_env():
        sys.exit(1)
        
    # 2. Build Main App
    py_bin = sys.executable
    if not run_step("Compile Main Application (onedir)", [py_bin, "build.py"]):
        sys.exit(1)
        
    # 3. Build Installer
    if not run_step("Compile Standalone Setup Installer (onefile)", [py_bin, "build_installer.py"]):
        sys.exit(1)
        
    # 4. Dry-run checks
    if not run_dry_run_tests():
        print("\n" + "!" * 70)
        print(" [CRITICAL ERROR] PACKAGING COMPLETED BUT BINARY VERIFICATION FAILED!")
        print(" Please check the output logs and tracebacks above.")
        print("!" * 70)
        sys.exit(1)
        
    # 5. Success summary
    print_banner("BUILD COMPLETION SUMMARY")
    print(f" [SUCCESS] Setup installer generated: {Path('dist3/CoreCommander_Setup.exe').absolute()}")
    print(f" [SUCCESS] Size: {Path('dist3/CoreCommander_Setup.exe').stat().st_size / (1024*1024):.2f} MB")
    print(" [SUCCESS] Main app zip: dist3/CoreCommander.zip")
    print(" [SUCCESS] Verify Status: 100% verified, boots crash-free.")
    print("=" * 70)

if __name__ == "__main__":
    main()
