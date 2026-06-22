import os
import torch
import numpy as np
import logging
import time

logger = logging.getLogger(__name__)

class ONNXNetGWrapper:
    """
    A wrapper around an ONNX session that mimics the PyTorch `net_g` interface
    used in rvc_python's pipeline.py.
    """
    def __init__(self, onnx_path, device_id=0):
        import onnxruntime as ort
        providers = [
            (
                "DmlExecutionProvider",
                {"device_id": device_id},
            )
        ]
        logger.info(f"Loading ONNX model for Generator from {onnx_path} with DML...")
        self.sess = ort.InferenceSession(onnx_path, providers=providers)
        
    def infer(self, feats, p_len, pitch, pitchf, sid):
        # inputs are PyTorch tensors because rvc_python pipeline operates on them.
        # Shape reference:
        # feats: [1, seq_len, dim]
        # p_len: [1]
        # pitch: [1, seq_len]
        # pitchf: [1, seq_len]
        # sid: [1]
        
        seq_len = feats.shape[1]
        rnd = torch.randn(1, 192, seq_len).numpy().astype(np.float32)
        
        onnx_input = {
            self.sess.get_inputs()[0].name: feats.cpu().numpy().astype(np.float32),
            self.sess.get_inputs()[1].name: p_len.cpu().numpy().astype(np.int64),
            self.sess.get_inputs()[2].name: pitch.cpu().numpy().astype(np.int64),
            self.sess.get_inputs()[3].name: pitchf.cpu().numpy().astype(np.float32),
            self.sess.get_inputs()[4].name: sid.cpu().numpy().astype(np.int64),
            self.sess.get_inputs()[5].name: rnd,
        }
        
        # Output shape: [1, 1, audio_len]
        out_audio = self.sess.run(None, onnx_input)[0]
        
        # Convert back to torch tensor so the rest of the pipeline handles it correctly.
        # rvc_python does: (net_g.infer(*arg)[0][0, 0]).data.cpu().float().numpy()
        # We need to return a tuple where [0] is the audio tensor.
        audio_tensor = torch.from_numpy(out_audio)
        return (audio_tensor, None, None)

def export_model_to_onnx_if_needed(pth_path):
    """
    Checks if an .onnx file exists for the given .pth. If not, exports it.
    Returns the path to the .onnx file.
    """
    onnx_path = pth_path.replace('.pth', '.onnx')
    if os.path.exists(onnx_path):
        return onnx_path
        
    logger.info(f"ONNX model not found. Starting automatic conversion from {pth_path} to {onnx_path}...")
    try:
        from rvc_python.modules.onnx.export import export_onnx
        export_onnx(pth_path, onnx_path)
        logger.info(f"ONNX model successfully exported to {onnx_path}")
        return onnx_path
    except Exception as e:
        logger.error(f"Failed to export ONNX model: {e}")
        return None
