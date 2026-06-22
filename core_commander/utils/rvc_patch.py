import sys
import types
from importlib.abc import MetaPathFinder, Loader
from core_commander.utils.logger import logger

class DummyModule(types.ModuleType):
    __path__ = []
    def __getattr__(self, name):
        if name.startswith('__') and name.endswith('__'):
            raise AttributeError(name)
        
        class DummyClass:
            def __init__(self, *args, **kwargs): pass
            def __new__(cls, *args, **kwargs): return super().__new__(cls)
            def __call__(self, *args, **kwargs): return self
            def __getattr__(self, attr):
                if attr.startswith('__') and attr.endswith('__'):
                    raise AttributeError(attr)
                return DummyClass()
                
        DummyClass.__name__ = name
        return DummyClass

class UltimateRVCFinder(MetaPathFinder, Loader):
    def find_spec(self, fullname, path, target=None):
        if fullname.startswith('ultimate_rvc'):
            from importlib.machinery import ModuleSpec
            return ModuleSpec(fullname, self)
        return None
        
    def create_module(self, spec):
        return DummyModule(spec.name)
        
    def exec_module(self, module):
        pass

_patches_applied = False

def apply_rvc_patches():
    global _patches_applied
    if _patches_applied:
        return
    _patches_applied = True
    # PyTorch 2.6+ and RVC compatibility patches
    sys.meta_path.insert(0, UltimateRVCFinder())
    
    import torch
    _orig_torch_load = torch.load
    def patched_torch_load(*args, **kwargs):
        if 'weights_only' not in kwargs:
            kwargs['weights_only'] = False
        return _orig_torch_load(*args, **kwargs)
    torch.load = patched_torch_load


    # Apply inspect monkey patch for PyInstaller JIT compilation compatibility with torch.jit
    try:
        import inspect
        _orig_findsource = inspect.findsource
        def patched_findsource(obj):
            try:
                name = getattr(obj, "__name__", None)
                if not name and hasattr(obj, "co_name"):
                    name = obj.co_name
                if name == "fused_add_tanh_sigmoid_multiply":
                    lines = [
                        "def fused_add_tanh_sigmoid_multiply(input_a, input_b, n_channels):\n",
                        "    n_channels_int = n_channels[0]\n",
                        "    in_act = input_a + input_b\n",
                        "    t_act = torch.tanh(in_act[:, :n_channels_int, :])\n",
                        "    s_act = torch.sigmoid(in_act[:, n_channels_int:, :])\n",
                        "    acts = t_act * s_act\n",
                        "    return acts\n"
                    ]
                    return lines, 0
            except Exception:
                pass
            return _orig_findsource(obj)
        inspect.findsource = patched_findsource

        _orig_getsourcefile = inspect.getsourcefile
        def patched_getsourcefile(obj):
            try:
                name = getattr(obj, "__name__", None)
                if not name and hasattr(obj, "co_name"):
                    name = obj.co_name
                if name == "fused_add_tanh_sigmoid_multiply":
                    return "fused_add_tanh_sigmoid_multiply_virtual_file.py"
            except Exception:
                pass
            return _orig_getsourcefile(obj)
        inspect.getsourcefile = patched_getsourcefile
    except Exception:
        pass

    # Apply dataclasses monkey patch for Python 3.11 compatibility with fairseq/rvc-python
    try:
        import dataclasses
        _orig_get_field = dataclasses._get_field
        def patched_get_field(cls, a_name, a_type, default_kw_only):
            default_val = getattr(cls, a_name, dataclasses.MISSING)
            if isinstance(default_val, dataclasses.Field):
                default_val = default_val.default
            has_temp_hash = False
            original_hash = None
            val_class = None
            if default_val is not dataclasses.MISSING and default_val is not None:
                val_class = default_val.__class__
                if hasattr(val_class, '__hash__') and val_class.__hash__ is None:
                    try:
                        original_hash = val_class.__hash__
                        val_class.__hash__ = lambda self: id(self)
                        has_temp_hash = True
                    except (TypeError, AttributeError):
                        pass
            try:
                return _orig_get_field(cls, a_name, a_type, default_kw_only)
            finally:
                if has_temp_hash and val_class is not None:
                    val_class.__hash__ = original_hash
        dataclasses._get_field = patched_get_field
    except Exception:
        pass

    # Pre-cache hydra to prevent circular imports during fairseq load
    try:
        import hydra
        import hydra.utils
    except ImportError:
        pass

    # Apply Pipeline.vc GPU index search patch (Clean, robust, no inspect.getsource)
    try:
        import rvc_python.modules.vc.pipeline as rvc_pipeline
        
        def patched_vc(self, model, net_g, sid, audio0, pitch, pitchf, times, index, big_npy, index_rate, version, protect):
            import torch
            import torch.nn.functional as F
            import numpy as np
            from time import time as ttime
            
            feats = torch.from_numpy(audio0)
            if self.is_half:
                feats = feats.half()
            else:
                feats = feats.float()
            if feats.dim() == 2:  # double channels
                feats = feats.mean(-1)
            assert feats.dim() == 1, feats.dim()
            feats = feats.view(1, -1)
            padding_mask = torch.BoolTensor(feats.shape).to(self.device).fill_(False)

            inputs = {
                "source": feats.to(self.device),
                "padding_mask": padding_mask,
                "output_layer": 9 if version == "v1" else 12,
            }
            t0 = ttime()
            with torch.no_grad():
                logits = model.extract_features(**inputs)
                feats = model.final_proj(logits[0]) if version == "v1" else logits[0]
            if protect < 0.5 and pitch is not None and pitchf is not None:
                feats0 = feats.clone()
            if (
                not isinstance(index, type(None))
                and not isinstance(big_npy, type(None))
                and index_rate != 0
            ):
                gpu_searched = False
                if hasattr(index, "search_gpu") and "cpu" not in str(self.device):
                    try:
                        gpu_feats = index.search_gpu(feats, index_rate, self.is_half)
                        if gpu_feats is not None:
                            feats = gpu_feats * index_rate + (1 - index_rate) * feats
                            gpu_searched = True
                    except Exception as e:
                        logger.warning(f"GPU Index search failed, falling back to CPU: {e}")
                
                if not gpu_searched:
                    npy = feats[0].cpu().numpy()
                    if self.is_half:
                        npy = npy.astype("float32")

                    # _, I = index.search(npy, 1)
                    # npy = big_npy[I.squeeze()]
                    
                    try:
                        res = index.search(npy, k=8)
                        if isinstance(res, tuple) and len(res) == 2:
                            score, ix = res
                        else:
                            score, ix = None, None
                    except Exception as e:
                        logger.error(f"index.search failed: {e}")
                        score, ix = None, None

                    if score is not None and ix is not None:
                        try:
                            weight = np.square(1 / score)
                            weight /= weight.sum(axis=1, keepdims=True)
                            npy = np.sum(big_npy[ix] * np.expand_dims(weight, axis=2), axis=1)

                            if self.is_half:
                                npy = npy.astype("float16")
                            feats = (
                                torch.from_numpy(npy).unsqueeze(0).to(self.device) * index_rate
                                + (1 - index_rate) * feats
                            )
                        except Exception as e:
                            logger.error(f"index search interpolation failed: {e}")

            feats = F.interpolate(feats.permute(0, 2, 1), scale_factor=2).permute(0, 2, 1)
            if protect < 0.5 and pitch is not None and pitchf is not None:
                feats0 = F.interpolate(feats0.permute(0, 2, 1), scale_factor=2).permute(
                    0, 2, 1
                )
            t1 = ttime()
            p_len = audio0.shape[0] // self.window
            if feats.shape[1] < p_len:
                p_len = feats.shape[1]
                if pitch is not None and pitchf is not None:
                    pitch = pitch[:, :p_len]
                    pitchf = pitchf[:, :p_len]

            if protect < 0.5 and pitch is not None and pitchf is not None:
                pitchff = pitchf.clone()
                pitchff[pitchf > 0] = 1
                pitchff[pitchf < 1] = protect
                pitchff = pitchff.unsqueeze(-1)
                feats = feats * pitchff + feats0 * (1 - pitchff)
                feats = feats.to(feats0.dtype)
            p_len = torch.tensor([p_len], device=self.device).long()
            with torch.no_grad():
                hasp = pitch is not None and pitchf is not None
                arg = (feats, p_len, pitch, pitchf, sid) if hasp else (feats, p_len, sid)
                audio1 = (net_g.infer(*arg)[0][0, 0]).data.cpu().float().numpy()
                del hasp, arg
            del feats, p_len, padding_mask
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
            t2 = ttime()
            times[0] += t1 - t0
            times[2] += t2 - t1
            return audio1

        rvc_pipeline.Pipeline.vc = patched_vc
        logger.info("Successfully applied GPU Index Search patch via direct redirection.")
    except Exception as e:
        logger.exception("Failed to apply GPU Index Search patch")
        raise RuntimeError(f"Failed to apply GPU Index Search patch: {e}") from e

    # Patch load_audio in rvc_python to accept numpy array/tuple directly
    try:
        import rvc_python.modules.vc.modules as rvc_modules
        _orig_load_audio = rvc_modules.load_audio
        def patched_load_audio(file, sr):
            import numpy as np
            if isinstance(file, tuple):
                audio = file[1] / 32768.0
                if len(audio.shape) == 2:
                    audio = np.mean(audio, -1)
                if file[0] != 16000:
                    import librosa
                    audio = librosa.resample(audio, orig_sr=file[0], target_sr=16000)
                return audio.flatten().astype(np.float32)
            if isinstance(file, np.ndarray):
                return file
            return _orig_load_audio(file, sr)
        rvc_modules.load_audio = patched_load_audio
        logger.info("Successfully patched load_audio to handle tuple/numpy arrays.")
    except Exception as e:
        logger.exception("Failed to patch load_audio")
        raise RuntimeError(f"Failed to patch load_audio: {e}") from e

    # Patch onnxruntime.InferenceSession to resolve mixing slashes and relative paths on Windows
    try:
        import onnxruntime as ort
        _orig_InferenceSession = ort.InferenceSession
        
        def patched_InferenceSession(path_or_bytes, *args, **kwargs):
            if isinstance(path_or_bytes, str):
                import os
                # Convert path to absolute normpath to satisfy ONNXRuntime on Windows
                path_or_bytes = os.path.abspath(os.path.normpath(path_or_bytes))
                # Auto-recovery: If file doesn't exist, try fallback directories
                if not os.path.exists(path_or_bytes):
                    fn = os.path.basename(path_or_bytes)
                    # Check parent directories
                    parent_dir = os.path.dirname(os.path.dirname(path_or_bytes))
                    fallback_path = os.path.join(parent_dir, fn)
                    if os.path.exists(fallback_path):
                        path_or_bytes = fallback_path
                    else:
                        # Check base_model directory inside site-packages
                        import rvc_python
                        fallback_path2 = os.path.join(os.path.dirname(rvc_python.__file__), "base_model", fn)
                        if os.path.exists(fallback_path2):
                            path_or_bytes = fallback_path2
                        else:
                            # Check rvc_python root inside site-packages
                            fallback_path3 = os.path.join(os.path.dirname(rvc_python.__file__), fn)
                            if os.path.exists(fallback_path3):
                                path_or_bytes = fallback_path3
            available = ort.get_available_providers()
            preferred = ["CUDAExecutionProvider", "DmlExecutionProvider"]
            injected_providers = [p for p in preferred if p in available]
            
            if injected_providers:
                existing_providers = kwargs.get("providers", [])
                should_upgrade = False
                if "providers" not in kwargs:
                    should_upgrade = True
                else:
                    if isinstance(existing_providers, list):
                        # check if list contains any GPU provider
                        has_gpu = False
                        for p in existing_providers:
                            p_name = p[0] if isinstance(p, tuple) else p
                            if p_name in available and p_name != "CPUExecutionProvider":
                                has_gpu = True
                                break
                        if not has_gpu:
                            should_upgrade = True
                
                if should_upgrade:
                    new_providers = []
                    for p in injected_providers:
                        if p not in new_providers:
                            new_providers.append(p)
                    if "providers" in kwargs:
                        for ep in existing_providers:
                            ep_name = ep[0] if isinstance(ep, tuple) else ep
                            if ep_name not in new_providers:
                                new_providers.append(ep)
                    else:
                        new_providers.append("CPUExecutionProvider")
                    kwargs["providers"] = new_providers
            return _orig_InferenceSession(path_or_bytes, *args, **kwargs)
            
        ort.InferenceSession = patched_InferenceSession
    except Exception as e:
        import logging
        logging.getLogger("RVC_PATCH").error(f"Failed to patch onnxruntime.InferenceSession: {e}")
