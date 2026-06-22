import os
import sys
import tempfile
import queue
import time
import threading
import numpy as np
import dataclasses

# Apply global monkey-patch for Python 3.11+ dataclasses mutable default values
# to resolve compatibility issues with fairseq configuration objects.
try:
    _orig_get_field = dataclasses._get_field
    def patched_get_field(cls, a_name, a_type, default_kw_only):
        try:
            return _orig_get_field(cls, a_name, a_type, default_kw_only)
        except ValueError as e:
            if 'mutable default' in str(e):
                val = getattr(cls, a_name, dataclasses.MISSING)
                if isinstance(val, dataclasses.Field):
                    val = val.default
                if val is not dataclasses.MISSING and hasattr(val, '__class__'):
                    cls_type = val.__class__
                    if cls_type not in (list, dict, set):
                        try:
                            orig_hash = cls_type.__hash__
                            cls_type.__hash__ = lambda self: 0
                            res = _orig_get_field(cls, a_name, a_type, default_kw_only)
                            cls_type.__hash__ = orig_hash
                            return res
                        except Exception:
                            pass
            raise
    dataclasses._get_field = patched_get_field
except Exception:
    pass

def simple_deesser(audio, sr, threshold=0.15, ratio=4.0):
    try:
        import scipy.signal
        b, a = scipy.signal.butter(2, 5000 / (sr / 2), btype='highpass')
        high_freq = scipy.signal.filtfilt(b, a, audio)
        env = np.abs(high_freq)
        gain = np.ones_like(audio)
        mask = env > threshold
        gain[mask] = threshold + (env[mask] - threshold) / ratio
        gain[mask] /= env[mask]
        return audio - high_freq * (1 - gain)
    except:
        return audio
import ctypes
try:
    from ctypes import wintypes
except ImportError:
    pass

import logging
logger = logging.getLogger(__name__)

def _enable_mmcss_pro_audio():
    try:
        if sys.platform.startswith('win'):
            avrt = ctypes.WinDLL('avrt.dll')
            avrt.AvSetMmThreadCharacteristicsW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD)]
            avrt.AvSetMmThreadCharacteristicsW.restype = wintypes.HANDLE
            avrt.AvRevertMmThreadCharacteristics.argtypes = [wintypes.HANDLE]
            avrt.AvRevertMmThreadCharacteristics.restype = wintypes.BOOL
            
            task_index = wintypes.DWORD(0)
            h_task = avrt.AvSetMmThreadCharacteristicsW("Pro Audio", ctypes.byref(task_index))
            if h_task:
                logger.info("VoiceChangerEngine thread MMCSS registration successful: Pro Audio")
                return h_task
    except Exception as e:
        logger.warning(f"VoiceChangerEngine thread MMCSS registration failed: {e}")
    return None

def _disable_mmcss_pro_audio(h_task):
    if h_task:
        try:
            avrt = ctypes.WinDLL('avrt.dll')
            avrt.AvRevertMmThreadCharacteristics(h_task)
            logger.info("VoiceChangerEngine thread MMCSS priority reverted.")
        except Exception as e:
            logger.warning(f"Failed to revert MMCSS priority: {e}")

class AudioRingBuffer:
    def __init__(self, size=192000):
        self.size = size
        self.buffer = np.zeros(size, dtype=np.float32)
        self.write_ptr = 0
        self.read_ptr = 0
        self.length = 0

    def write(self, data):
        n = len(data)
        if n == 0:
            return
        if self.length + n > self.size:
            drop_len = self.length + n - self.size
            self.read_ptr = (self.read_ptr + drop_len) % self.size
            self.length = self.size - n
        
        end = self.write_ptr + n
        if end <= self.size:
            self.buffer[self.write_ptr:end] = data
        else:
            first_part = self.size - self.write_ptr
            self.buffer[self.write_ptr:] = data[:first_part]
            self.buffer[:n - first_part] = data[first_part:]
        self.write_ptr = (self.write_ptr + n) % self.size
        self.length += n

    def read(self, n):
        if self.length < n:
            return np.zeros(n, dtype=np.float32)
        
        out = np.zeros(n, dtype=np.float32)
        end = self.read_ptr + n
        if end <= self.size:
            out[:] = self.buffer[self.read_ptr:end]
        else:
            first_part = self.size - self.read_ptr
            out[:first_part] = self.buffer[self.read_ptr:]
            out[first_part:] = self.buffer[:n - first_part]
        
        self.read_ptr = (self.read_ptr + n) % self.size
        self.length -= n
        return out

    def peek(self, n):
        if self.length < n:
            return np.zeros(n, dtype=np.float32)
        out = np.zeros(n, dtype=np.float32)
        end = self.read_ptr + n
        if end <= self.size:
            out[:] = self.buffer[self.read_ptr:end]
        else:
            first_part = self.size - self.read_ptr
            out[:first_part] = self.buffer[self.read_ptr:]
            out[first_part:] = self.buffer[:n - first_part]
        return out

    def advance(self, n):
        self.read_ptr = (self.read_ptr + n) % self.size
        self.length = max(0, self.length - n)

_current_hop_size = 128

def dummy_change_rms(data1, sr1, data2, sr2, rate):
    rms1 = np.sqrt(np.mean(data1**2))
    rms2 = np.sqrt(np.mean(data2**2))
    rms1 = max(rms1, 1e-4)
    rms2 = max(rms2, 1e-4)
    data2 = data2 * (rms1 / rms2) * rate + data2 * (1 - rate)
    return data2

_IS_PATCHED = False
_faiss_logger = None
_global_index_cache = {}

def _ensure_patched_and_loaded():
    global _IS_PATCHED, _faiss_logger, _global_index_cache
    if _IS_PATCHED: return
    
    from core_commander.utils.rvc_patch import apply_rvc_patches
    apply_rvc_patches()
    
    import torch
    import faiss
    import logging
    try:
        import rvc_python.lib.rmvpe
    except Exception:
        pass
    
    try:
        import rvc_python.modules.vc.pipeline as rvc_pipeline
        rvc_pipeline.change_rms = dummy_change_rms
    except:
        pass
        
    _faiss_logger = logging.getLogger("FAISS_CACHE")
    _orig_read_index = faiss.read_index
    
    def patched_read_index(file_index):
        if file_index not in _global_index_cache:
            _faiss_logger.info(f"Loading FAISS index into RAM: {file_index}")
            try:
                idx = _orig_read_index(file_index)
                big_npy = idx.reconstruct_n(0, idx.ntotal)
                _global_index_cache[file_index] = (idx, big_npy)
            except Exception as e:
                _faiss_logger.warning(f"Index lacks reconstruct_n support, this index cannot be used: {e}")
                raise e
            
        idx, big_npy = _global_index_cache[file_index]
        class CachedIndex:
            def __init__(self, i, b):
                self.ntotal = i.ntotal
                self.i = i
                self.b = b
                self._gpu_big_npy_cache = {}
                
            def search(self, *args, **kwargs):
                try:
                    res = self.i.search(*args, **kwargs)
                    if res is None:
                        import numpy as np
                        k = kwargs.get('k', 8) or 8
                        num_queries = args[0].shape[0] if len(args) > 0 and hasattr(args[0], 'shape') else 1
                        return np.zeros((num_queries, k), dtype=np.float32), np.zeros((num_queries, k), dtype=np.int64)
                    return res
                except Exception as e:
                    import logging
                    logging.getLogger("FAISS_CACHE").error(f"faiss search failed: {e}")
                    import numpy as np
                    k = kwargs.get('k', 8) or 8
                    num_queries = args[0].shape[0] if len(args) > 0 and hasattr(args[0], 'shape') else 1
                    return np.zeros((num_queries, k), dtype=np.float32), np.zeros((num_queries, k), dtype=np.int64)
                
            def reconstruct_n(self, *args, **kwargs):
                return self.b

            def search_gpu(self, feats_tensor, index_rate, is_half=False):
                """
                feats_tensor: (1, T, D) 的 torch.Tensor，通常位于 GPU 上。
                """
                try:
                    device = feats_tensor.device
                    dev_type = getattr(device, "type", str(device)).lower()
                    if "cpu" in dev_type:
                        return None
                    
                    if device not in self._gpu_big_npy_cache:
                        import torch
                        self._gpu_big_npy_cache[device] = torch.from_numpy(self.b).to(device=device, dtype=torch.float32)
                    
                    import torch
                    db = self._gpu_big_npy_cache[device]  # (N, D)
                    q = feats_tensor[0].float()  # (T, D)
                    
                    # 用 L2 距离平方计算相似度: ||q - db||^2 = ||q||^2 + ||db||^2 - 2 * q @ db.T
                    q_norm = (q ** 2).sum(dim=-1, keepdim=True)  # (T, 1)
                    db_norm = (db ** 2).sum(dim=-1, keepdim=True).T  # (1, N)
                    
                    # 计算距离矩阵
                    dist = q_norm + db_norm - 2.0 * torch.matmul(q, db.T)  # (T, N)
                    
                    # 寻找前 8 个最近邻 (largest=False 表示寻找最小距离)
                    k = min(8, db.size(0))
                    score, ix = torch.topk(dist, k=k, dim=-1, largest=False)  # (T, k)
                    
                    # 权重计算
                    weight = 1.0 / torch.clamp(score, min=1e-9)
                    weight = weight ** 2
                    weight = weight / weight.sum(dim=-1, keepdim=True)  # (T, k)
                    
                    # 加权求和
                    # db[ix] 形状为 (T, k, D)
                    # weight.unsqueeze(-1) 形状为 (T, k, 1)
                    g_npy = (db[ix] * weight.unsqueeze(-1)).sum(dim=1)
                    
                    if is_half:
                        g_npy = g_npy.half()
                    else:
                        g_npy = g_npy.float()
                    
                    return g_npy.unsqueeze(0)  # (1, T, D)
                except Exception as e:
                    import logging
                    logging.getLogger("FAISS_CACHE").error(f"search_gpu failed: {e}")
                    return None

        return CachedIndex(idx, big_npy)
        
    faiss.read_index = patched_read_index
    
    _orig_torch_load = torch.load
    def patched_torch_load(*args, **kwargs):
        if "weights_only" not in kwargs:
            kwargs["weights_only"] = False
        return _orig_torch_load(*args, **kwargs)
    torch.load = patched_torch_load
    
    _IS_PATCHED = True

import sounddevice as sd
try:
    from pedalboard import load_plugin, Pedalboard, Compressor, Reverb, HighShelfFilter
    HAS_PEDALBOARD = True
except Exception:
    HAS_PEDALBOARD = False
from PySide6.QtCore import QThread, Signal

import logging
logger = logging.getLogger(__name__)

def get_optimal_device():
    import torch
    if torch.cuda.is_available():
        return "cuda:0"
    try:
        import torch_directml
        if torch_directml.is_available():
            # torch-directml device for AMD/Intel GPUs
            return "privateuseone:0"
    except ImportError:
        pass
    if hasattr(torch.backends, 'mps') and torch.backends.mps.is_available():
        return "mps"
    return "cpu"

import importlib.util
HAS_DEPENDENCIES = importlib.util.find_spec('rvc_python') is not None and importlib.util.find_spec('torch') is not None

class VoiceChangerEngine(QThread):
    status_signal = Signal(str)
    audio_error = Signal(str)

    def __init__(self, pth_path, index_path=None, input_device=None, output_device=None, tune_params=None):
        super().__init__()
        self.pth_path = pth_path
        self.index_path = index_path
        self.input_device = input_device
        self.output_device = output_device
        self._params_lock = threading.Lock()
        self._vst_lock = threading.Lock()
        self.tune_params = tune_params or {}
        self.is_running = False
        self.vst_plugin = None
        self.vst_path = ''
        self.sample_rate = 48000
        # Dynamic chunk size based on GUI latency slider (e.g., 150ms -> 7200 samples)
        latency_ms = self.tune_params.get("latency_ms", 150) if self.tune_params else 150
        self.chunk_size = int((latency_ms / 1000.0) * self.sample_rate)
        if tune_params and tune_params.get('vst_path'):
            self.load_vst_plugin(tune_params['vst_path'])
        
        # Reduce max queue sizes to prevent buffering delay accumulation
        self.input_queue = queue.Queue(maxsize=3)
        self.output_queue = queue.Queue(maxsize=3)

    def update_params(self, params_dict):
        with self._params_lock:
            self.tune_params.update(params_dict)

    def get_param(self, name, default=None):
        with self._params_lock:
            return self.tune_params.get(name, default)

    def load_vst_plugin(self, path):
        if not path:
            with self._vst_lock:
                self.vst_plugin = None
                self.vst_path = ""
            return True
        try:
            plugin = load_plugin(path)
            with self._vst_lock:
                self.vst_plugin = plugin
                self.vst_path = path
            return True
        except Exception as e:
            err_msg = str(e).replace(path, os.path.basename(path))
            logger.error(f"Failed to load VST: {err_msg}")
            with self._vst_lock:
                self.vst_plugin = None
                self.vst_path = ""
            return False

    def show_vst_editor(self):
        with self._vst_lock:
            vst = self.vst_plugin
        if vst:
            try:
                vst.show_editor()
            except Exception as e:
                logger.error(f"Failed to show VST editor: {e}")

    def stop(self):
        self.is_running = False

    def request_hot_swap(self, pth_path, index_path):
        self.pending_hot_swap = (pth_path, index_path)

    def run(self):
        if not hasattr(VoiceChangerEngine, '_active_instances'):
            VoiceChangerEngine._active_instances = set()
        VoiceChangerEngine._active_instances.add(self)

        h_mmcss = _enable_mmcss_pro_audio()

        if not HAS_DEPENDENCIES:
            self.status_signal.emit("未找到引擎依赖")
            return
            
        try:
            from core_commander.core.guard import _security_tainted, check_apply_optimization_hook
            check_apply_optimization_hook()
            if _security_tainted:
                logger.critical("Voice Changer engine startup aborted due to tainted environment (Security verification failed).")
                self.status_signal.emit("核心文件异常，变声引擎拒绝启动")
                return
        except Exception as e:
            logger.critical(f"Failed to load security guard in voice changer: {e}")
            self.status_signal.emit("安全校验组件缺失")
            return
        
        try:
            _ensure_patched_and_loaded()
            import torch
            import rvc_python
            from rvc_python.infer import RVCInference
            from rvc_python.configs.config import Config
            import soundfile as sf
            import resampy
            
            self.use_rvc = bool(self.pth_path and os.path.exists(self.pth_path))
            if self.use_rvc:
                try:
                    base_model_dir = os.path.join(os.path.dirname(os.path.abspath(rvc_python.__file__)), "base_model")
                    os.environ["rmvpe_root"] = base_model_dir
                    os.environ["weight_root"] = base_model_dir
                    os.environ["index_root"] = base_model_dir
                except Exception:
                    pass

                opt_device = get_optimal_device()
                logger.info(f"RVC Engine starting on device: {opt_device}")
                rvc_infer = RVCInference(device=opt_device)
                
                # FORCE RVC config to use the optimal device (bypassing its internal CPU fallback)
                rvc_infer.config.device = opt_device
                rvc_infer.config.instead = opt_device
                rvc_infer.config.is_half = (opt_device != "cpu" and not opt_device.startswith("privateuseone"))
                
                # Auto-detect RVC model version to prevent shape mismatches by inspecting actual weight shapes
                try:
                    cpt = torch.load(self.pth_path, map_location="cpu")
                    
                    # Foolproof check: V2 uses 768 for Hubert, V1 uses 256.
                    if "weight" in cpt and "enc_p.emb_phone.weight" in cpt["weight"]:
                        phone_shape = cpt["weight"]["enc_p.emb_phone.weight"].shape
                        if phone_shape[1] == 768:
                            real_version = "v2"
                        else:
                            real_version = "v1"
                    else:
                        real_version = cpt.get("version", "v1")
                        
                    sr_str = cpt.get("sr", "40k")
                    if isinstance(sr_str, str):
                        if "48k" in sr_str: self.sample_rate = 48000
                        elif "44k" in sr_str: self.sample_rate = 44100
                        else: self.sample_rate = 40000
                    else:
                        self.sample_rate = 40000
                        
                    del cpt
                except Exception as e:
                    self.audio_error.emit(f"自动检测模型版本失败: {e}")
                    real_version = "v2"
                    self.sample_rate = 40000
                    
                # Load the model using RVC's standard method
                try:
                    rvc_infer.load_model(self.pth_path, version=real_version, index_path=self.index_path or "")
                    
                    # === ONNX OFFLOAD HACK FOR DIRECTML ===
                    # To achieve ~0% CPU usage on DirectML, we bypass PyTorch entirely for the RVC generator
                    # because torch-directml lacks weight_norm support and falls back to CPU.
                    if opt_device.startswith("privateuseone"):
                        from .onnx_exporter import export_model_to_onnx_if_needed, ONNXNetGWrapper
                        onnx_path = export_model_to_onnx_if_needed(self.pth_path)
                        if onnx_path:
                            # Swap the PyTorch model out for our ONNX wrapper
                            rvc_infer.vc.net_g = ONNXNetGWrapper(onnx_path)
                            # Disable FAISS CPU overhead
                            self.index_path = ""
                            logger.info("Successfully switched Generator to ONNX! CPU overhead eliminated.")
                except Exception as e:
                    self.audio_error.emit(f"加载模型失败: {e}")
                
                pitch = self.get_param("pitch_shift", 0)
                method = self.get_param("f0_method", "rmvpe")
                index_rate = 0.0 if not self.index_path else self.get_param("index_rate", 0.75)
                rms_mix = self.get_param("rms_mix_rate", 0.25)
                protect = self.get_param("protect", 0.33)
                
                try:
                    rvc_infer.set_params(
                        f0up_key=pitch,
                        f0method=method,
                        index_rate=index_rate,
                        rms_mix_rate=rms_mix,
                        protect=protect
                    )
                except Exception as e:
                    pass
            else:
                self.sample_rate = 48000
            
            if self.use_rvc:
                latency_ms = self.get_param("latency_ms", 150) if self.tune_params else 150
                self.chunk_size = int((latency_ms / 1000.0) * self.sample_rate)
                self.fade_size = int(self.sample_rate * 0.025) # 25ms linear crossfade (Balanced to prevent popping while keeping attacks sharp)
                self.protect_size = int(self.sample_rate * 0.12) # 120ms real future context for vocoder and filtfilt edge effects
                self.future_size = self.fade_size + self.protect_size
            else:
                # Ultra-low latency for direct mic / bypass mode (approx 10ms processing latency)
                self.chunk_size = 512
                self.fade_size = 128
                self.protect_size = 0
                self.future_size = self.fade_size + self.protect_size
            
            if self.fade_size > self.chunk_size // 2:
                self.fade_size = self.chunk_size // 2
            
            self.is_running = True
            self.status_signal.emit("运行中")
            
            input_device_index = self._find_device_index(self.input_device, is_input=True)
            output_device_index = self._find_device_index(self.output_device, is_input=False)
            
            self.in_ring = AudioRingBuffer(192000)
            self.out_ring = AudioRingBuffer(192000)
            
            def callback(indata, outdata, frames, time_info, status):
                mono_audio = indata[:, 0].astype(np.float32)
                
                # Heuristic: If driver returned int16 scaled values (e.g. 32767.0 instead of 1.0), normalize them.
                # Massively clipped float32 audio sounds exactly like TV static/white noise.
                if np.max(np.abs(mono_audio)) > 2.0:
                    mono_audio = mono_audio / 32768.0
                    
                self.in_ring.write(mono_audio)
                
                # Check for input ring buffer backlog to prevent accumulated latency
                max_allowed_len = (self.chunk_size + self.future_size) + 2 * self.chunk_size
                if self.in_ring.length > max_allowed_len:
                    discard_samples = self.in_ring.length - (self.chunk_size + self.future_size)
                    self.in_ring.advance(discard_samples)
                
                # Wait for chunk_size + future_size
                while self.in_ring.length >= self.chunk_size + self.future_size:
                    chunk_to_process = self.in_ring.peek(self.chunk_size + self.future_size)
                    self.in_ring.advance(self.chunk_size)
                    try:
                        self.input_queue.put_nowait(chunk_to_process)
                    except queue.Full:
                        try:
                            self.input_queue.get_nowait() # Drop oldest chunk to prevent callback blocking
                        except queue.Empty:
                            pass
                        try:
                            self.input_queue.put_nowait(chunk_to_process)
                        except queue.Full:
                            pass
                
                try:
                    while True:
                        new_out = self.output_queue.get_nowait()
                        self.out_ring.write(new_out)
                except queue.Empty:
                    pass
                
                # Jitter buffer: Wait until we have safely accumulated enough audio
                if not hasattr(self, 'playback_started'):
                    self.playback_started = False
                    
                if not self.playback_started:
                    # Wait for 1 full chunk + 40ms safety margin (approx 9248 samples)
                    safe_margin = self.chunk_size + 2048
                    if self.out_ring.length >= safe_margin:
                        self.playback_started = True
                    else:
                        outdata[:, 0] = 0
                        return
                
                if self.out_ring.length >= frames:
                    outdata[:, 0] = self.out_ring.read(frames)
                else:
                    available = self.out_ring.length
                    if available > 0:
                        outdata[:available, 0] = self.out_ring.read(available)
                    outdata[available:, 0] = 0
                    self.playback_started = False # Underflow! Pause playback to rebuild jitter buffer

            with sd.Stream(device=(input_device_index, output_device_index),
                            samplerate=self.sample_rate, blocksize=512, latency='low',
                            channels=1, callback=callback):
                
                # Monkey-patch RVC's offline pipeline to avoid CPU explosion in real-time
                # Standard RVC pads 3 seconds of audio to the front and back for offline processing!
                # Processing 6+ seconds of audio every 150ms starves PyAudio and causes continuous electric crackle.
                try:
                    if hasattr(rvc_infer.vc, 'pipeline'):
                        rvc_infer.vc.pipeline.x_pad = 0.05
                        rvc_infer.vc.pipeline.t_pad = int(16000 * 0.05)
                        if hasattr(rvc_infer.vc, 'tgt_sr'):
                            rvc_infer.vc.pipeline.t_pad_tgt = int(rvc_infer.vc.tgt_sr * 0.05)
                        rvc_infer.vc.pipeline.t_pad2 = rvc_infer.vc.pipeline.t_pad * 2
                        
                        # Disable non-causal filtfilt which causes edge ringing in real-time chunks
                        import scipy.signal
                        rvc_infer.vc.pipeline.signal.filtfilt = lambda b, a, x, *args, **kwargs: x
                except Exception:
                    pass

                context_size = int(self.sample_rate * 0.5) # 500ms history is the perfect balance between Hubert context and CPU speed
                past_buffer = np.zeros(context_size, dtype=np.float32)
                
                # Native FX Board Initialization
                native_fx_board = None
                if HAS_PEDALBOARD:
                    native_fx_board = Pedalboard([
                        Compressor(threshold_db=0.0, ratio=1.0, attack_ms=5.0, release_ms=100.0),
                        HighShelfFilter(cutoff_frequency_hz=5000.0, gain_db=0.0), # De-esser
                        Reverb(room_size=0.0, wet_level=0.0, dry_level=1.0, damping=0.5)
                    ])
                
                prev_fade_buffer = np.zeros(self.fade_size, dtype=np.float32)
                fade_in = np.linspace(0, 1, self.fade_size, dtype=np.float32)
                fade_out = 1.0 - fade_in
                was_silent = True
                
                while self.is_running:
                    if hasattr(self, 'pending_hot_swap') and self.pending_hot_swap:
                        new_pth, new_index = self.pending_hot_swap
                        self.pending_hot_swap = None
                        try:
                            use_rvc_new = bool(new_pth and os.path.exists(new_pth))
                            if use_rvc_new:
                                cpt = torch.load(new_pth, map_location="cpu")
                                if "weight" in cpt and "enc_p.emb_phone.weight" in cpt["weight"]:
                                    phone_shape = cpt["weight"]["enc_p.emb_phone.weight"].shape
                                    real_version = "v2" if phone_shape[1] == 768 else "v1"
                                else:
                                    real_version = cpt.get("version", "v1")
                                del cpt
                                
                                if 'rvc_infer' not in locals() or rvc_infer is None:
                                    rvc_infer = RVCInference(device="cpu" if not torch.cuda.is_available() else "cuda:0")
                                rvc_infer.load_model(new_pth, version=real_version, index_path=new_index or "")
                            
                            self.use_rvc = use_rvc_new
                            self.pth_path = new_pth
                            self.index_path = new_index
                            if torch.cuda.is_available():
                                torch.cuda.empty_cache()
                        except Exception as e:
                            self.audio_error.emit(f"模型热切换失败: {e}")
                            
                    try:
                        current_block = self.input_queue.get(timeout=0.1)
                    except queue.Empty:
                        continue
                        
                    # No fake padding! We use the real future_size audio provided by PyAudio callback.
                    full_input = np.zeros(len(past_buffer) + len(current_block), dtype=np.float32)
                    full_input[:len(past_buffer)] = past_buffer
                    full_input[len(past_buffer):] = current_block
                    
                    try:
                        # Avoid parameter lock contention: copy all parameters at once in a single lock cycle
                        with self._params_lock:
                            params_copy = self.tune_params.copy()

                        dynamic_pitch = int(params_copy.get("pitch", 0))
                        dynamic_method = params_copy.get("method", "rmvpe")
                        dynamic_index_rate = params_copy.get("index_rate", 0.75)
                        dynamic_rms_mix = params_copy.get("rms_mix", 0.25)
                        dynamic_protect = params_copy.get("protect", 0.33)
                        dynamic_f0_smooth = params_copy.get("f0_smooth", 0.3)
                        dynamic_hop_size = int(params_copy.get("hop_size", 128))
                        dynamic_deesser = params_copy.get("deesser", True)
                        gate_threshold = float(params_copy.get("gate_threshold", 0.0))
                        
                        global _current_hop_size
                        _current_hop_size = dynamic_hop_size
                        
                        # Check Noise Gate based on input RMS amplitude
                        input_rms = np.sqrt(np.mean(current_block ** 2))
                        is_gate_active = (input_rms < gate_threshold)
                        
                        if is_gate_active:
                            out_audio = np.zeros(len(full_input), dtype=np.float32)
                            was_silent = True
                        else:
                            # Apply hop_size dynamically to hubert if possible
                            if self.use_rvc:
                                try:
                                    if hasattr(rvc_infer.vc, 'hubert_model'):
                                        rvc_infer.vc.hubert_model.kwargs['hop_size'] = _current_hop_size
                                except Exception:
                                    pass
                                
                                # filter_radius usually expects integer 0-7
                                mapped_filter_radius = int(dynamic_f0_smooth * 7)
                                
                                # Use user-selected index path explicitly!
                                file_index = self.index_path if self.index_path else ""
                                
                                # Pure memory inference under no_grad! No disk I/O!
                                try:
                                    with torch.no_grad():
                                        wav_opt = rvc_infer.vc.vc_single(
                                            sid=0,
                                            input_audio_path=(self.sample_rate, full_input * 32768.0 * 0.8), # 0.8 Headroom prevents catastrophic int16 overflow (buzz)
                                            f0_up_key=dynamic_pitch,
                                            f0_file=None,
                                            f0_method=dynamic_method,
                                            file_index=file_index,
                                            file_index2=file_index,
                                            index_rate=dynamic_index_rate, # Restored FAISS index to fix the '2 styles' timbre drift
                                            filter_radius=mapped_filter_radius,
                                            resample_sr=0,
                                            rms_mix_rate=1.0, # MUST BE 1.0! Dynamic chunk-by-chunk RMS scaling causes volume pumping and ruins articulation
                                            protect=dynamic_protect,
                                        )
                                    if isinstance(wav_opt, tuple) and len(wav_opt) == 2 and getattr(wav_opt[1], "__len__", lambda: 0)() == 2:
                                        raise RuntimeError(f"RVC 内部崩溃: {wav_opt[0]}")
                                    if not isinstance(wav_opt, np.ndarray):
                                        raise TypeError(f"RVC 返回了非法数据类型: {type(wav_opt)}")
                                except Exception as inner_e:
                                    logger.error(f"RVC inference exception: {inner_e}", exc_info=True)
                                    wav_opt = full_input * 32768.0
                                
                                # Final type safety assertion
                                if not isinstance(wav_opt, np.ndarray):
                                    wav_opt = full_input * 32768.0
                                
                                # RVC returns int16 array [-32768, 32767], we must normalize it to [-1.0, 1.0] for PyAudio float32 stream
                                out_audio = wav_opt.astype(np.float32) / 32768.0
                                
                                if len(out_audio.shape) > 1:
                                    out_audio = out_audio[:, 0]
                            else:
                                # Bypass mode: pass input directly
                                out_audio = full_input.copy()
                        
                        # Extraction with Crossfade
                        start_idx = context_size
                        end_idx = context_size + self.chunk_size + self.fade_size
                        # Note: We completely discard the last self.protect_size samples from out_audio
                        
                        if len(out_audio) >= end_idx:
                            current_out = out_audio[start_idx:end_idx].copy()
                        else:
                            padded = np.zeros(context_size + self.chunk_size + self.fade_size, dtype=np.float32)
                            padded[:len(out_audio)] = out_audio
                            current_out = padded[start_idx:end_idx]
                            
                        # Apply crossfade
                        if was_silent:
                            # Preserve attack of first word by NOT fading from silence!
                            was_silent = False
                            # No crossfade, just use the direct output
                        else:
                            current_out[:self.fade_size] = current_out[:self.fade_size] * fade_in + prev_fade_buffer * fade_out
                            
                        prev_fade_buffer = current_out[-self.fade_size:]
                        final_chunk = current_out[:-self.fade_size]
                        
                        # Apply Native FX
                        if native_fx_board is not None:
                            try:
                                # Retrieve UI parameters from cached copy (0.0 to 1.0)
                                comp_amt = float(params_copy.get("compressor", 0.0))
                                deesser_amt = float(params_copy.get("deesser", 0.0))
                                reverb_amt = float(params_copy.get("reverb", 0.0))
                                
                                # Dynamic Parameter Mapping
                                # Compressor: threshold 0 to -30dB, ratio 1:1 to 4:1
                                native_fx_board[0].threshold_db = -30.0 * comp_amt
                                native_fx_board[0].ratio = 1.0 + (3.0 * comp_amt)
                                
                                # De-esser (HighShelf proxy): gain 0 to -15dB above 5kHz
                                native_fx_board[1].gain_db = -15.0 * deesser_amt
                                
                                # Reverb: room size 0 to 0.8, wet 0 to 0.4
                                native_fx_board[2].room_size = 0.8 * reverb_amt
                                native_fx_board[2].wet_level = 0.4 * reverb_amt
                                
                                # Process with state persistence
                                chunk_2d = final_chunk.reshape(1, -1)
                                processed_2d = native_fx_board(chunk_2d, self.sample_rate, reset=False)
                                final_chunk = processed_2d.reshape(-1)
                            except Exception as e:
                                logger.error(f"Native FX processing error: {e}")
                                
                        # VST Plugin processing
                        with self._vst_lock:
                            vst_to_run = self.vst_plugin
                        if vst_to_run is not None:
                            try:
                                # Ensure input is 2D (channels, samples) for Pedalboard
                                vst_input = final_chunk.reshape(1, -1) if len(final_chunk.shape) == 1 else final_chunk
                                with self._vst_lock:
                                    final_chunk = vst_to_run(vst_input, self.sample_rate)
                                
                                # Pedalboard always returns (channels, samples)
                                if len(final_chunk.shape) > 1:
                                    # Downmix to mono by averaging channels (axis=0)
                                    final_chunk = final_chunk.mean(axis=0)
                            except Exception as e:
                                pass
                        
                        # Peak limiting / Hard clipping to prevent digital clipping distortion
                        final_chunk = np.clip(final_chunk, -1.0, 1.0)
                        self.output_queue.put(final_chunk)
                    except Exception as e:
                        import traceback
                        err_msg = f"Engine inner loop error: {e}\n{traceback.format_exc()}"
                        logger.error(err_msg)
                        with open("error_log.txt", "w", encoding="utf-8") as f:
                            f.write(err_msg)
                        self.output_queue.put(np.zeros(self.chunk_size, dtype=np.float32))
                        prev_fade_buffer = np.zeros(self.fade_size, dtype=np.float32)
                        was_silent = True
                    finally:
                        shift_len = self.chunk_size
                        if shift_len < len(past_buffer):
                            past_buffer[:-shift_len] = past_buffer[shift_len:]
                            past_buffer[-shift_len:] = current_block[:shift_len]
                        else:
                            past_buffer[:] = current_block[:len(past_buffer)]
        except Exception as e:
            logger.exception("Voice Changer engine crashed in outer run loop")
            try:
                import traceback
                err_msg = f"Engine outer run error: {e}\n{traceback.format_exc()}"
                with open("engine_error_log.txt", "w", encoding="utf-8") as f_err:
                    f_err.write(err_msg)
            except Exception:
                pass
            self.audio_error.emit(f"引擎异常: {e}")
        finally:
            _disable_mmcss_pro_audio(locals().get('h_mmcss', None))
            self.is_running = False
            try:
                if 'rvc_infer' in locals():
                    del rvc_infer
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
                import gc
                gc.collect()
            except Exception: pass
            self.status_signal.emit("已停用")

    def _find_device_index(self, device_name, is_input=True):
        if not device_name: return None
        try:
            devices = sd.query_devices()
            for i, dev in enumerate(devices):
                if device_name == dev["name"] and dev["hostapi"] == sd.default.hostapi:
                    if is_input and dev["max_input_channels"] > 0: return i
                    if not is_input and dev["max_output_channels"] > 0: return i
        except Exception: pass
        return None

def get_audio_devices(force_refresh: bool = False):
    if not HAS_DEPENDENCIES: return [], []
    input_devs = []
    output_devs = []
    try:
        if force_refresh and not getattr(VoiceChangerEngine, '_active_instances', None):
            try:
                sd._terminate()
                sd._initialize()
            except Exception: pass
        elif force_refresh:
            logger.warning("VoiceChangerEngine is running. Skipping PortAudio re-initialization in get_audio_devices.")
        devices = sd.query_devices()
        for dev in devices:
            if dev.get("hostapi") != sd.default.hostapi: continue
            name = dev.get("name", "")
            if dev.get("max_input_channels", 0) > 0 and name not in input_devs: input_devs.append(name)
            if dev.get("max_output_channels", 0) > 0 and name not in output_devs: output_devs.append(name)
    except Exception: pass
    return input_devs, output_devs
