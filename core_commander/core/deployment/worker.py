import os
import sys
import subprocess
import shutil
import urllib.request
import winreg
import tempfile
import zipfile
from PySide6.QtCore import QThread, Signal

# Default URLs for precompiled dependency packages
CUDA_ENV_ITEMS = [
    {
        "urls": [
            "https://mirror.sjtu.edu.cn/pytorch-wheels/cu118/torch-2.0.1%2Bcu118-cp311-cp311-win_amd64.whl",
            "https://download.pytorch.org/whl/cu118/torch-2.0.1%2Bcu118-cp311-cp311-win_amd64.whl"
        ],
        "label": "PyTorch (CUDA 11.8)"
    },
    {
        "urls": [
            "https://mirror.sjtu.edu.cn/pytorch-wheels/cu118/torchaudio-2.0.2%2Bcu118-cp311-cp311-win_amd64.whl",
            "https://download.pytorch.org/whl/cu118/torchaudio-2.0.2%2Bcu118-cp311-cp311-win_amd64.whl"
        ],
        "label": "TorchAudio (CUDA 11.8)"
    },
    {
        "urls": [
            "https://mirrors.aliyun.com/pypi/packages/dc/7c/7cb90b83e6f5f4c9d6085df8b2e3e8be762f75e25afcc62d71c0df66fb3f/onnxruntime_gpu-1.16.3-cp311-cp311-win_amd64.whl"
        ],
        "label": "ONNX Runtime (GPU)"
    }
]

CPU_ENV_ITEMS = [
    {
        "url": "https://mirrors.aliyun.com/pypi/packages/d0/c8/f0dc8642e3ce0a3ae5f05e5149ab9df5375d569294f7be9a1ab1d95a1d76/torch-2.0.1-cp311-cp311-win_amd64.whl",
        "label": "PyTorch (CPU)"
    },
    {
        "url": "https://mirrors.aliyun.com/pypi/packages/18/34/3d47ad10261d643d84219ae0807df2661a647393e470550d3a8f0bcce24d/torchaudio-2.0.2-cp311-cp311-win_amd64.whl",
        "label": "TorchAudio (CPU)"
    },
    {
        "url": "https://mirrors.aliyun.com/pypi/packages/49/bd/a00f271510098ee62c097ecec663484ff12de632bea1bcaa02ea3679cd03/onnxruntime-1.16.3-cp311-cp311-win_amd64.whl",
        "label": "ONNX Runtime (CPU)"
    }
]
VC_REDIST_URL = "https://aka.ms/vs/17/release/vc_redist.x64.exe"

class DeploymentWorker(QThread):
    progress = Signal(int, str)
    log = Signal(str)
    finished = Signal(bool)

    def __init__(self, strategy="auto", env_7z_url=None):
        super().__init__()
        self.strategy = strategy
        self.env_7z_url = env_7z_url or "https://example.com/env_placeholder.7z"

    def run(self):
        try:
            self.log.emit("Starting dependency deployment...")
            self.progress.emit(2, "Detecting Hardware...")

            # 1. Hardware Detection
            is_nvidia = self._check_nvidia_gpu()
            if is_nvidia:
                self.log.emit("NVIDIA GPU detected. Target: Native CUDA")
            else:
                self.log.emit("No NVIDIA GPU detected. Target: CPU/DirectML")

            self.progress.emit(5, "Checking VC++ 2015-2022 Redistributable...")
            
            # 2. VC++ Runtime Check and Silent Installation
            vc_installed = self._check_vc_redist()
            if not vc_installed:
                self.log.emit("VC++ 2015-2022 Redistributable (x64) is missing. Preparing silent installation...")
                
                temp_dir = tempfile.gettempdir()
                vc_installer_path = os.path.join(temp_dir, "VC_redist.x64.exe")
                
                if os.path.exists(vc_installer_path):
                    try:
                        os.remove(vc_installer_path)
                    except Exception:
                        pass
                
                self.progress.emit(10, "Downloading VC++ 2015-2022 Redistributable...")
                download_success = self._download_file(
                    VC_REDIST_URL, vc_installer_path, start_pct=10, end_pct=20, label="VC++ Redistributable"
                )
                if not download_success:
                    raise RuntimeError("Failed to download VC++ Redistributable installer.")
                
                self.progress.emit(20, "Installing VC++ 2015-2022 Redistributable silently...")
                install_success = self._install_vc_redist(vc_installer_path)
                if not install_success:
                    raise RuntimeError("VC++ Redistributable installation failed.")
                
                try:
                    if os.path.exists(vc_installer_path):
                        os.remove(vc_installer_path)
                except Exception:
                    pass
            else:
                self.log.emit("Visual C++ 2015-2022 Redistributable (x64) is already installed.")
            
            self.progress.emit(25, "Preparing AI package download...")

            # 3. Environment Strategy Selection and Download
            items_to_download = []
            if self.strategy == "auto":
                if self.env_7z_url and self.env_7z_url != "https://example.com/env_placeholder.7z":
                    items_to_download = [{"url": self.env_7z_url, "label": "Custom Package"}]
                else:
                    items_to_download = CUDA_ENV_ITEMS if is_nvidia else CPU_ENV_ITEMS
            else:
                items_to_download = [{"url": self.env_7z_url, "label": "Custom Package"}]
            
            temp_dir = tempfile.gettempdir()
            internal_dir = self._get_internal_dir()
            os.makedirs(internal_dir, exist_ok=True)
            self.log.emit(f"Target extraction directory: {internal_dir}")

            total_items = len(items_to_download)
            for idx, item in enumerate(items_to_download):
                target_urls = item.get("urls", [item.get("url")])
                first_url = target_urls[0] if target_urls else ""
                label = item["label"]
                self.log.emit(f"Preparing download {idx + 1}/{total_items}: {label}")
                
                # Determine extension (.whl, .zip or .7z etc.)
                ext = ".whl" if ".whl" in first_url.lower() else ".zip"
                if ".7z" in first_url.lower():
                    ext = ".7z"
                env_archive_path = os.path.join(temp_dir, f"core_commander_dep_{idx}{ext}")
                
                if os.path.exists(env_archive_path):
                    try:
                        os.remove(env_archive_path)
                    except Exception:
                        pass
                
                # Progress calculation bounds for this download item
                start_pct = 25 + int((idx / total_items) * 73)
                mid_pct = 25 + int(((idx + 0.85) / total_items) * 73)
                end_pct = 25 + int(((idx + 1) / total_items) * 73)
                
                self.progress.emit(start_pct, f"Downloading {label}...")
                download_success = self._download_file(
                    target_urls, env_archive_path, start_pct=start_pct, end_pct=mid_pct, label=label
                )
                if not download_success:
                    raise RuntimeError(f"Failed to download dependency: {label}")
                
                self.progress.emit(mid_pct, f"Extracting {label}...")
                extract_success = self._extract_zip(env_archive_path, internal_dir, start_pct=mid_pct, end_pct=end_pct)
                if not extract_success:
                    raise RuntimeError(f"Extraction of {label} failed.")
                
                # Clean up temp file
                try:
                    if os.path.exists(env_archive_path):
                        os.remove(env_archive_path)
                except Exception as e:
                    self.log.emit(f"Warning: Failed to delete temp archive file: {e}")
            
            self.progress.emit(100, "Ready!")
            self.log.emit("Deployment finished successfully.")
            self.finished.emit(True)
            
        except Exception as e:
            self.log.emit(f"Critical error during deployment: {str(e)}")
            self.finished.emit(False)

    def _check_nvidia_gpu(self) -> bool:
        """Runs nvidia-smi to detect Nvidia GPUs."""
        try:
            result = subprocess.run(
                ["nvidia-smi"], 
                stdout=subprocess.PIPE, 
                stderr=subprocess.PIPE, 
                creationflags=subprocess.CREATE_NO_WINDOW
            )
            return result.returncode == 0
        except FileNotFoundError:
            return False

    def _check_vc_redist(self) -> bool:
        """Checks if Visual C++ 2015-2022 Redistributable (x64) is installed."""
        try:
            key_path = r"SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64"
            key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY)
            installed, _ = winreg.QueryValueEx(key, "Installed")
            winreg.CloseKey(key)
            return int(installed) == 1
        except FileNotFoundError:
            return False
        except Exception as e:
            self.log.emit(f"Warning checking registry: {e}")
            return False

    def _install_vc_redist(self, installer_path: str) -> bool:
        """Executes the VC++ redistributable installer silently."""
        try:
            self.log.emit(f"Running silent VC++ installer: {installer_path} /quiet /norestart")
            process = subprocess.Popen(
                [installer_path, "/quiet", "/norestart"],
                creationflags=subprocess.CREATE_NO_WINDOW
            )
            while process.poll() is None:
                if self.isInterruptionRequested():
                    process.terminate()
                    self.log.emit("VC++ installation aborted by user request.")
                    return False
                self.msleep(100)
            
            ret_code = process.returncode
            self.log.emit(f"VC++ redistributable installer exited with code: {ret_code}")
            if ret_code in [0, 1641, 3010]:
                return True
            else:
                self.log.emit(f"VC++ redistributable installation failed with exit code: {ret_code}")
                return False
        except Exception as e:
            self.log.emit(f"Error launching VC++ redist installer: {e}")
            return False

    def _get_internal_dir(self) -> str:
        """Gets the path of the target extraction directory (_internal)."""
        if getattr(sys, 'frozen', False):
            return os.path.join(sys._MEIPASS, "_internal")
        else:
            root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
            return os.path.join(root_dir, "_internal")

    def _download_file(self, urls, dest_path: str, start_pct: int, end_pct: int, label: str) -> bool:
        if isinstance(urls, str):
            urls = [urls]
            
        import ssl
        # Create unverified SSL context to bypass potential certificate validation issues
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        
        for url in urls:
            self.log.emit(f"Downloading {label} from: {url}")
            for attempt in range(1, 4):
                if self.isInterruptionRequested():
                    self.log.emit("Download cancelled by user.")
                    return False
                
                if attempt > 1:
                    self.log.emit(f"Retrying download of {label} (Attempt {attempt}/3)...")
                    
                req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
                try:
                    with urllib.request.urlopen(req, context=ctx, timeout=30) as response:
                        total_size = int(response.info().get('Content-Length', 0))
                        bytes_read = 0
                        block_size = 1024 * 64
                        
                        os.makedirs(os.path.dirname(dest_path), exist_ok=True)
                        
                        last_reported_pct = -1
                        with open(dest_path, 'wb') as f:
                            while True:
                                if self.isInterruptionRequested():
                                    self.log.emit("Download cancelled by user.")
                                    return False
                                
                                chunk = response.read(block_size)
                                if not chunk:
                                    break
                                f.write(chunk)
                                bytes_read += len(chunk)
                                
                                if total_size > 0:
                                    percent = int(bytes_read * 100 / total_size)
                                    if percent != last_reported_pct:
                                        last_reported_pct = percent
                                        overall_pct = start_pct + int((percent / 100.0) * (end_pct - start_pct))
                                        status_text = f"Downloading {label}: {percent}% ({bytes_read / (1024*1024):.1f}MB / {total_size / (1024*1024):.1f}MB)"
                                        self.progress.emit(overall_pct, status_text)
                                        if percent % 10 == 0:
                                            self.log.emit(status_text)
                                else:
                                    status_text = f"Downloading {label}: {bytes_read / (1024*1024):.1f}MB"
                                    self.progress.emit(start_pct + 5, status_text)
                        
                        self.log.emit(f"Successfully downloaded {label} to {dest_path}")
                        return True
                except Exception as e:
                    self.log.emit(f"Attempt {attempt}/3 failed for {label}: {e}")
                    # Remove incomplete file
                    try:
                        if os.path.exists(dest_path):
                            os.remove(dest_path)
                    except Exception:
                        pass
                    
                    if self.isInterruptionRequested():
                        return False
                        
                    self.msleep(1000)
                    
        self.log.emit(f"All download sources failed for {label}")
        return False

    def _extract_zip(self, zip_path: str, dest_dir: str, start_pct: int, end_pct: int) -> bool:
        self.log.emit(f"Extracting dependencies archive to {dest_dir}...")
        try:
            ext = os.path.splitext(zip_path)[1].lower()
            if ext in ['.zip', '.whl']:
                with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                    infolist = zip_ref.infolist()
                    total_files = len(infolist)
                    if total_files == 0:
                        self.log.emit("Zip archive is empty.")
                        return False
                    
                    last_reported_pct = -1
                    for i, file in enumerate(infolist):
                        if self.isInterruptionRequested():
                            self.log.emit("Extraction cancelled by user.")
                            return False
                        
                        zip_ref.extract(file, dest_dir)
                        
                        percent = int((i + 1) * 100 / total_files)
                        if percent != last_reported_pct:
                            last_reported_pct = percent
                            overall_pct = start_pct + int((percent / 100.0) * (end_pct - start_pct))
                            status_text = f"Extracting files: {percent}% ({i + 1}/{total_files})"
                            self.progress.emit(overall_pct, status_text)
                            if percent % 20 == 0:
                                self.log.emit(status_text)
                
                self.log.emit("Extraction completed successfully.")
                return True
            elif ext in ['.7z', '.rar']:
                try:
                    import py7zr
                    self.log.emit("Using py7zr to extract archive...")
                    with py7zr.SevenZipFile(zip_path, mode='r') as sz_ref:
                        sz_ref.extractall(path=dest_dir)
                        self.progress.emit(end_pct, "Extraction complete.")
                        self.log.emit("Extraction completed successfully.")
                        return True
                except ImportError:
                    self.log.emit("py7zr is not installed. Attempting system 7z utility...")
                
                try:
                    self.progress.emit(start_pct + (end_pct - start_pct) // 2, "Running system 7z extractor...")
                    result = subprocess.run(
                        ["7z", "x", zip_path, f"-o{dest_dir}", "-y"],
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                        creationflags=subprocess.CREATE_NO_WINDOW
                    )
                    if result.returncode == 0:
                        self.progress.emit(end_pct, "Extraction complete.")
                        self.log.emit("Extraction completed successfully.")
                        return True
                    else:
                        self.log.emit(f"System 7z extraction failed with return code {result.returncode}")
                except FileNotFoundError:
                    self.log.emit("System 7-zip command line utility (7z) not found.")
                
                raise RuntimeError("No extraction tool available for .7z files. Please ensure the package is a .zip file or install 7-Zip.")
            else:
                self.log.emit(f"Unsupported archive extension: {ext}")
                return False
        except Exception as e:
            self.log.emit(f"Error during extraction: {e}")
            import traceback
            self.log.emit(traceback.format_exc())
            return False
