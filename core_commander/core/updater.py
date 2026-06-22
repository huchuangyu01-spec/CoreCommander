# -*- coding: utf-8 -*-
import os
import sys
import json
import time
import hashlib
import tempfile
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed
from PySide6.QtCore import QThread, Signal
from core_commander.utils.logger import logger

CURRENT_VERSION = "2.0"

GITEE_TOKEN = "64f85f66692a5f55fc8f2e1c4799d232"
GITEE_VERSION_URL = "https://gitee.com/kireto/CoreCommander/raw/main/version.json"
GITHUB_VERSION_URL = "https://raw.githubusercontent.com/huchuangyu01-spec/CoreCommander/main/version.json"

class UpdateCheckWorker(QThread):
    """
    Asynchronously queries version.json metadata from Gitee/GitHub.
    """
    checked = Signal(bool, dict)

    def run(self):
        urls = [GITEE_VERSION_URL, GITHUB_VERSION_URL]
        data = None
        error_msg = "Unknown error"

        for url in urls:
            try:
                request_url = url
                if "gitee.com" in url and GITEE_TOKEN:
                    separator = "&" if "?" in url else "?"
                    request_url = f"{url}{separator}access_token={GITEE_TOKEN}"

                logger.info(f"Checking updates from: {url}")
                req = urllib.request.Request(
                    request_url, 
                    headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
                )
                with urllib.request.urlopen(req, timeout=5) as response:
                    content = response.read().decode('utf-8')
                    data = json.loads(content)
                    logger.info("Successfully fetched version metadata.")
                    break
            except Exception as e:
                error_msg = str(e)
                logger.warning(f"Failed to fetch updates from {url}: {e}")
                continue

        if data is None:
            self.checked.emit(False, {"error": error_msg})
            return

        latest_ver = data.get("latest_version", "2.0")
        has_update = self.compare_versions(CURRENT_VERSION, latest_ver)
        
        result = {
            "has_update": has_update,
            "latest_version": latest_ver,
            "release_date": data.get("release_date", ""),
            "update_notes": data.get("update_notes", {}),
            "download_urls": data.get("download_urls", {}),
            "installer_sha256": data.get("installer_sha256", "")
        }
        self.checked.emit(True, result)

    def compare_versions(self, current: str, latest: str) -> bool:
        try:
            c_parts = [int(x) for x in current.split(".")]
            l_parts = [int(x) for x in latest.split(".")]
            for c, l in zip(c_parts, l_parts):
                if l > c:
                    return True
                elif c > l:
                    return False
            return len(l_parts) > len(c_parts)
        except Exception:
            return latest != current


class UpdateDownloadWorker(QThread):
    """
    Runs concurrent speed racing tests to pick the fastest CDN mirror,
    downloads the file to %TEMP%, and validates SHA-256 integrity.
    """
    progress = Signal(int, float)  # percent, speed_mbps
    status = Signal(str)           # state description translation key
    finished = Signal(bool, str)   # success, dest_file_path or error_message

    def __init__(self, metadata: dict):
        super().__init__()
        self.metadata = metadata
        self._is_cancelled = False

    def cancel(self):
        self._is_cancelled = True

    def run(self):
        self.status.emit("update_status_checking")
        
        # 1. Collect all candidates
        download_urls = self.metadata.get("download_urls", {})
        global_url = download_urls.get("global")
        mirrors = download_urls.get("china_mirrors", [])
        
        candidates = []
        if global_url:
            candidates.append(global_url)
        for m in mirrors:
            if "gitee.com" in m and GITEE_TOKEN:
                separator = "&" if "?" in m else "?"
                m = f"{m}{separator}access_token={GITEE_TOKEN}"
            candidates.append(m)

        if not candidates:
            self.finished.emit(False, "No download URLs configured in metadata.")
            return

        # 2. Concurrently test latencies to find the fastest mirror
        logger.info(f"Initiating mirror speed test across {len(candidates)} candidates...")
        fastest_url = self.race_mirrors(candidates)
        logger.info(f"Selected mirror: {fastest_url}")

        # 3. Start download
        self.status.emit("update_status_downloading")
        dest_path = os.path.join(tempfile.gettempdir(), "CoreCommander_Setup.exe")
        
        try:
            if os.path.exists(dest_path):
                os.remove(dest_path)
        except Exception:
            pass

        try:
            req = urllib.request.Request(
                fastest_url,
                headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
            )
            
            with urllib.request.urlopen(req, timeout=15) as response:
                total_size = int(response.info().get('Content-Length', 0))
                downloaded = 0
                chunk_size = 1024 * 64
                
                start_time = time.time()
                last_report_time = start_time
                last_downloaded = 0

                with open(dest_path, 'wb') as f:
                    while not self._is_cancelled:
                        chunk = response.read(chunk_size)
                        if not chunk:
                            break
                        f.write(chunk)
                        downloaded += len(chunk)
                        
                        now = time.time()
                        # Report progress every 0.1s
                        if now - last_report_time >= 0.1:
                            duration = now - last_report_time
                            speed_mb = (downloaded - last_downloaded) / (1024 * 1024)
                            speed_mbps = speed_mb / duration if duration > 0 else 0
                            
                            percent = int((downloaded / total_size) * 100) if total_size > 0 else 0
                            self.progress.emit(percent, speed_mbps)
                            
                            last_report_time = now
                            last_downloaded = downloaded

            if self._is_cancelled:
                self.finished.emit(False, "Cancelled by user")
                return

        except Exception as e:
            logger.error(f"Download failed: {e}")
            self.finished.emit(False, str(e))
            return

        # 4. Hash verification
        self.status.emit("update_status_verifying")
        expected_sha = self.metadata.get("installer_sha256", "").strip().lower()
        if expected_sha:
            try:
                sha = hashlib.sha256()
                with open(dest_path, 'rb') as f:
                    for chunk in iter(lambda: f.read(1024 * 64), b''):
                        sha.update(chunk)
                computed_sha = sha.hexdigest().lower()
                
                if computed_sha != expected_sha:
                    logger.error(f"Integrity check failed. Expected: {expected_sha}, got: {computed_sha}")
                    self.finished.emit(False, "Hash integrity mismatch (corrupted download).")
                    return
                logger.info("SHA-256 integrity verification passed.")
            except Exception as e:
                self.finished.emit(False, f"Verification failed: {e}")
                return
        
        self.status.emit("update_status_success")
        self.finished.emit(True, dest_path)

    def race_mirrors(self, urls: list) -> str:
        """
        Sends concurrent HEAD requests to measure latency and select the best endpoint.
        """
        if len(urls) == 1:
            return urls[0]

        def test_url(url):
            try:
                req = urllib.request.Request(
                    url,
                    method='HEAD',
                    headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
                )
                start = time.time()
                with urllib.request.urlopen(req, timeout=1.5) as conn:
                    status = conn.status
                    if status < 400:
                        return url, time.time() - start
            except Exception:
                pass
            return url, 99.0

        best_url = urls[0]
        min_latency = 99.0

        with ThreadPoolExecutor(max_workers=len(urls)) as executor:
            futures = [executor.submit(test_url, url) for url in urls]
            for future in as_completed(futures):
                url, latency = future.result()
                logger.debug(f"Mirror latency test: {url} -> {latency:.3f}s")
                if latency < min_latency:
                    min_latency = latency
                    best_url = url

        return best_url
