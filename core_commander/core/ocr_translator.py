import os
import logging
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

class OCRTranslatorEngine:
    _instance = None
    _initialized = False

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(OCRTranslatorEngine, cls).__new__(cls)
        return cls._instance

    def __init__(self):
        if not self._initialized:
            self.ocr = None
            self.translator = None
            self._initialized = True

    def initialize(self):
        """Lazy load heavy models to avoid blocking startup"""
        if self.ocr is None:
            try:
                # Ensure ONNXRuntime patches are applied before engine initialization to support GPU execution
                try:
                    from core_commander.utils.rvc_patch import apply_rvc_patches
                    apply_rvc_patches()
                except Exception as pe:
                    logger.warning(f"Failed to apply RVC/ONNX patches for OCR: {pe}")

                logger.info("Initializing RapidOCR...")
                from rapidocr_onnxruntime import RapidOCR
                self.ocr = RapidOCR(use_cls=False)
                
                # Query and log the actual execution provider used
                det_providers = []
                rec_providers = []
                try:
                    if hasattr(self.ocr, "text_det") and hasattr(self.ocr.text_det, "infer") and hasattr(self.ocr.text_det.infer, "session"):
                        det_providers = self.ocr.text_det.infer.session.get_providers()
                    if hasattr(self.ocr, "text_rec") and hasattr(self.ocr.text_rec, "session") and hasattr(self.ocr.text_rec.session, "session"):
                        rec_providers = self.ocr.text_rec.session.session.get_providers()
                except Exception as ex:
                    logger.debug(f"Failed to query OCR session providers: {ex}")
                
                logger.info(f"OCR Engine initialized successfully. Detector providers: {det_providers}, Recognizer providers: {rec_providers}")
            except Exception as e:
                logger.error(f"Failed to initialize OCR Engine: {e}")
                self.ocr = None

    def recognize_text(self, image_data) -> str:
        if self.ocr is None:
            self.initialize()
            
        if self.ocr is None:
            return "Error: OCR engine not initialized"

        try:
            result, _ = self.ocr(image_data)
            if not result:
                return "未检测到文本"

            extracted_lines = [line[1] for line in result if len(line) >= 2]
            original_text = "\n".join(extracted_lines)

            if not original_text.strip():
                return "未检测到文本"

            return original_text
        except Exception as e:
            logger.error(f"OCR recognition failed: {e}")
            return f"识别失败: {str(e)}"

    def translate_text(self, original_text: str) -> str:
        if original_text in ["未检测到文本", "Error: OCR engine not initialized"] or original_text.startswith("识别失败"):
            return ""

        try:
            import translators as ts
            translated_text = ""
            engines = ['bing', 'google', 'sogou', 'alibaba']
            for engine in engines:
                try:
                    translated_text = ts.translate_text(original_text, translator=engine, to_language='zh')
                    if translated_text:
                        break
                except Exception as ex:
                    logger.warning(f"Translation with {engine} failed: {ex}")
            
            if not translated_text:
                translated_text = "翻译全部节点失败，请检查网络。"

            return translated_text
        except Exception as e:
            logger.error(f"Translation failed: {e}")
            return f"翻译异常: {str(e)}"

ocr_engine = OCRTranslatorEngine()
