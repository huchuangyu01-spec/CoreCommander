# -*- coding: utf-8 -*-
import os
import threading
import urllib.request
from PySide6.QtCore import QObject, Signal
from core_commander.utils.logger import logger

# 预设的一些声音模型（托管在公共网盘、HuggingFace 或 Gitee/Github 上）
# 这里存放免费免登陆直链，如果不可用可以提示用户添加自己的本地模型。
PRESET_MODELS = [
    {
        "id": "donald_trump",
        "name_zh": "特朗普 (Trump)",
        "name_en": "Donald Trump",
        "desc_zh": "真实可用的美国前总统特朗普 AI 变声模型。",
        "desc_en": "Donald Trump AI voice model.",
        "pth_url": "https://huggingface.co/binant/Donald_Trump__RVC_v2_/resolve/main/model.pth",
        "index_url": "https://huggingface.co/binant/Donald_Trump__RVC_v2_/resolve/main/model.index"
    },
    {
        "id": "deckard_cain",
        "name_zh": "迪卡·凯恩 (Deckard Cain)",
        "name_en": "Deckard Cain",
        "desc_zh": "暗黑破坏神 迪卡·凯恩 真实声音模型。",
        "desc_en": "Diablo Deckard Cain voice model.",
        "pth_url": "https://huggingface.co/lainlives/Deckard_Cain-RVC-32k-wavlm-plus-RefineGAN/resolve/main/Deckard%20Cain2_best.pth",
        "index_url": "https://huggingface.co/lainlives/Deckard_Cain-RVC-32k-wavlm-plus-RefineGAN/resolve/main/Deckard%20Cain2.index"
    }
]

MODEL_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "CoreCommander", "models")

class ModelDownloadWorker(QObject):
    progress = Signal(str, int)  # 任务名, 进度百分比
    finished = Signal(str, bool, str)  # model_id, 是否成功, 报错信息或本地路径

    def __init__(self, model_info):
        super().__init__()
        self.model_info = model_info
        self._is_cancelled = False

    def cancel(self):
        self._is_cancelled = True

    def run(self):
        model_id = self.model_info["id"]
        pth_url = self.model_info["pth_url"]
        index_url = self.model_info.get("index_url", "")
        
        dest_dir = os.path.join(MODEL_DIR, model_id)
        os.makedirs(dest_dir, exist_ok=True)
        
        pth_path = os.path.join(dest_dir, f"{model_id}.pth")
        index_path = os.path.join(dest_dir, f"{model_id}.index")
        
        try:
            # 1. 下载 pth 文件
            self.progress.emit(f"正在下载 {self.model_info['name_zh']} 模型权重...", 5)
            self._download_file(pth_url, pth_path, start_pct=5, end_pct=80)
            
            if self._is_cancelled:
                self.finished.emit(model_id, False, "Cancelled")
                return
            
            # 2. 下载 index 文件 (如果有的话)
            if index_url:
                self.progress.emit(f"正在下载 {self.model_info['name_zh']} 特征检索文件...", 80)
                self._download_file(index_url, index_path, start_pct=80, end_pct=95)
            
            if self._is_cancelled:
                self.finished.emit(model_id, False, "Cancelled")
                return
                
            self.progress.emit("部署成功！", 100)
            self.finished.emit(model_id, True, dest_dir)
            
        except Exception as e:
            logger.error(f"Failed to download model {model_id}: {e}")
            self.finished.emit(model_id, False, str(e))

    def _download_file(self, url, dest_path, start_pct, end_pct):
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req) as response:
            total_size = int(response.info().get('Content-Length', 0))
            downloaded = 0
            block_size = 8192
            
            with open(dest_path, 'wb') as f:
                while True:
                    if self._is_cancelled:
                        break
                    buffer = response.read(block_size)
                    if not buffer:
                        break
                    downloaded += len(buffer)
                    f.write(buffer)
                    
                    if total_size > 0:
                        pct = start_pct + (downloaded / total_size) * (end_pct - start_pct)
                        self.progress.emit(f"正在下载... ({int(downloaded/(1024*1024))}MB / {int(total_size/(1024*1024))}MB)", int(pct))
