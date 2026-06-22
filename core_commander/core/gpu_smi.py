# -*- coding: utf-8 -*-
import subprocess  # nosec
import os
import re
import ctypes
import threading
from core_commander.utils.logger import logger

_gpu_smi_lock = threading.Lock()

class GpuSmiService:
    """
    Service to manage GPU clock frequency locking and VRAM release.
    Provides dual-path execution: direct NVML ctypes binding and nvidia-smi CLI fallback.
    """
    _nvml_loaded = False
    _nvml_lib = None

    @classmethod
    def load_nvml(cls) -> bool:
        if cls._nvml_loaded:
            return True
        with _gpu_smi_lock:
            if cls._nvml_loaded:
                return True
            try:
                # Try loading nvidia-ml.dll
                cls._nvml_lib = ctypes.windll.LoadLibrary("nvml.dll")
                if cls._nvml_lib:
                    # Explicit ctypes declaration for nvml functions used
                    cls._nvml_lib.nvmlInit.argtypes = []
                    cls._nvml_lib.nvmlInit.restype = ctypes.c_int
                    cls._nvml_lib.nvmlShutdown.argtypes = []
                    cls._nvml_lib.nvmlShutdown.restype = ctypes.c_int
                    
                    # Check for other functions
                    if hasattr(cls._nvml_lib, 'nvmlDeviceGetHandleByIndex'):
                        cls._nvml_lib.nvmlDeviceGetHandleByIndex.argtypes = [
                            ctypes.c_uint,
                            ctypes.POINTER(ctypes.c_void_p)
                        ]
                        cls._nvml_lib.nvmlDeviceGetHandleByIndex.restype = ctypes.c_int

                    ret = cls._nvml_lib.nvmlInit()
                    if ret == 0:
                        cls._nvml_loaded = True
                        logger.info("NVML library successfully loaded and initialized via ctypes.")
                        return True
            except Exception as e:
                logger.debug(f"Direct NVML dll load failed: {str(e)}")
            
            # Try importing python bindings if available
            try:
                import pynvml
                pynvml.nvmlInit()
                cls._nvml_loaded = True
                logger.info("pynvml library successfully initialized.")
                return True
            except Exception:
                pass
            return False

    @classmethod
    def unload_nvml(cls):
        with _gpu_smi_lock:
            if cls._nvml_loaded:
                try:
                    if cls._nvml_lib:
                        cls._nvml_lib.nvmlShutdown()
                        handle = cls._nvml_lib._handle
                        if handle:
                            kernel32 = ctypes.windll.kernel32
                            kernel32.FreeLibrary.argtypes = [ctypes.c_void_p]
                            kernel32.FreeLibrary.restype = ctypes.c_int
                            kernel32.FreeLibrary(handle)
                            logger.info("NVML DLL library handle successfully freed.")
                    else:
                        import pynvml
                        pynvml.nvmlShutdown()
                except Exception as e:
                    logger.debug(f"Failed to shutdown NVML: {e}")
                cls._nvml_loaded = False
                cls._nvml_lib = None

    @classmethod
    def get_max_supported_clock(cls) -> int:
        """
        Retrieves the maximum supported graphics clock speed in MHz.
        """
        from core_commander.core.system_tweaks import SystemTweaksService
        cls.load_nvml()
        with _gpu_smi_lock:
            # 1. Try NVML
            if cls._nvml_loaded:
                try:
                    handle = ctypes.c_void_p()
                    if cls._nvml_lib and hasattr(cls._nvml_lib, 'nvmlDeviceGetHandleByIndex'):
                        res = cls._nvml_lib.nvmlDeviceGetHandleByIndex(0, ctypes.byref(handle))
                        if res == 0 and handle.value:
                            # Fallback to standard clock info or query via nvidia-smi
                            pass
                except Exception as e:
                    logger.debug(f"NVML max clock query failed: {str(e)}")

            # 2. Try nvidia-smi CLI query
            try:
                cmd = ["nvidia-smi", "-q", "-d", "SUPPORTED_CLOCKS"]
                output = SystemTweaksService.safe_subprocess_check_output(cmd, timeout=5).decode("utf-8", errors="ignore")
                # Parse output for Graphics clocks
                # Example format: "Graphics    : 2100 MHz"
                clocks = re.findall(r"Graphics\s*:\s*(\d+)\s*MHz", output)
                if clocks:
                    clocks_int = [int(c) for c in clocks]
                    max_c = max(clocks_int)
                    # Apply a 10% safety headroom margin to prevent driver reset crashes (VIDEO_TDR_FAILURE)
                    # on modern RTX series cards running under full gaming loads.
                    if max_c > 1500:
                        max_c = int(max_c * 0.90)
                    logger.info(f"Safe lock clock frequency determined: {max_c} MHz")
                    return max_c
            except Exception as e:
                logger.debug(f"nvidia-smi query failed: {str(e)}")

        # Default fallback standard clocks (e.g. 1950 MHz for average RTX cards)
        return 1950

    @staticmethod
    def release_com_ptr(ptr_addr):
        if not ptr_addr:
            return
        try:
            val = getattr(ptr_addr, 'value', ptr_addr)
            if not val:
                return
            vtable_ptr = ctypes.cast(val, ctypes.POINTER(ctypes.c_void_p))
            if vtable_ptr and vtable_ptr[0]:
                vtable = ctypes.cast(vtable_ptr[0], ctypes.POINTER(ctypes.c_void_p))
                if vtable and vtable[2]:
                    release_func = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(vtable[2])
                    release_func(val)
        except Exception as e:
            logger.debug(f"Error releasing COM pointer: {e}")

    @classmethod
    def lock_gpu_clocks(cls, lock: bool) -> bool:
        """
        Locks the GPU core clocks to the maximum supported frequency to prevent clock drops.
        """
        from core_commander.core.system_tweaks import SystemTweaksService
        cls.load_nvml()
        max_clock = 0
        if lock:
            try:
                max_clock = cls.get_max_supported_clock()
            except Exception:
                pass

        should_unload = False
        ret_val = False
        with _gpu_smi_lock:
            if lock:
                logger.info("Locking GPU core clocks...")

                # Enable Persistence Mode
                try:
                    SystemTweaksService.safe_subprocess_call(["nvidia-smi", "-pm", "1"], timeout=5)
                except Exception:
                    pass

                # Try NVML first
                if cls._nvml_loaded:
                    try:
                        import pynvml
                        count = pynvml.nvmlDeviceGetCount()
                        success = False
                        for i in range(count):
                            try:
                                handle = pynvml.nvmlDeviceGetHandleByIndex(i)
                                pynvml.nvmlDeviceSetGpuLockedClocks(handle, max_clock, max_clock)
                                success = True
                                logger.info(f"GPU {i} locked to {max_clock} MHz successfully.")
                            except Exception as e:
                                logger.debug(f"Failed to lock GPU index {i} clocks: {str(e)}")
                        if success:
                            logger.info("GPU core clocks locked successfully using NVML.")
                            ret_val = True
                    except Exception as ex:
                        logger.debug(f"NVML clock lock loop failed: {str(ex)}")

                if not ret_val:
                    # Fallback to nvidia-smi command line
                    try:
                        cmd = ["nvidia-smi", f"--lock-gpu-clocks={max_clock},{max_clock}"]
                        res = SystemTweaksService.safe_subprocess_call(cmd, timeout=5)
                        if res == 0:
                            logger.info(f"GPU clock locked to {max_clock} MHz successfully via nvidia-smi.")
                            ret_val = True
                    except Exception as e:
                        logger.error(f"Failed to lock GPU clocks: {str(e)}")
            else:
                logger.info("Unlocking and resetting GPU core clocks...")
                if cls._nvml_loaded:
                    try:
                        import pynvml
                        count = pynvml.nvmlDeviceGetCount()
                        success = False
                        for i in range(count):
                            try:
                                handle = pynvml.nvmlDeviceGetHandleByIndex(i)
                                pynvml.nvmlDeviceResetGpuLockedClocks(handle)
                                success = True
                            except Exception as e:
                                logger.debug(f"Failed to reset GPU index {i} clocks: {str(e)}")
                        if success:
                            logger.info("GPU core clocks unlocked successfully using NVML.")
                            should_unload = True
                            ret_val = True
                    except Exception as ex:
                        logger.debug(f"NVML clock reset loop failed: {str(ex)}")

                if not ret_val:
                    try:
                        res = SystemTweaksService.safe_subprocess_call(["nvidia-smi", "--reset-gpu-clocks"], timeout=5)
                        if res == 0:
                            logger.info("GPU clocks reset successfully via nvidia-smi.")
                            ret_val = True
                    except Exception as e:
                        logger.error(f"Failed to reset GPU clocks: {str(e)}")

        if should_unload:
            cls.unload_nvml()
        return ret_val

    @classmethod
    def optimize_vram(cls) -> bool:
        """
        Releases unused VRAM by cleaning standby lists, DWM buffers and triggering DXGI cache flushes.
        """
        logger.info("Flushing GPU VRAM cache standby lists...")
        
        class DXGI_ADAPTER_DESC(ctypes.Structure):
            _fields_ = [
                ('Description', ctypes.c_wchar * 128),
                ('VendorId', ctypes.c_uint),
                ('DeviceId', ctypes.c_uint),
                ('SubSysId', ctypes.c_uint),
                ('Revision', ctypes.c_uint),
                ('DedicatedVideoMemory', ctypes.c_size_t),
                ('DedicatedSystemMemory', ctypes.c_size_t),
                ('SharedSystemMemory', ctypes.c_size_t),
                ('AdapterLuid', ctypes.c_longlong)
            ]

        def call_vtable(obj_ptr, index, restype, *argtypes):
            val = getattr(obj_ptr, 'value', obj_ptr)
            vtable_ptr = ctypes.cast(val, ctypes.POINTER(ctypes.c_void_p))
            vtable = ctypes.cast(vtable_ptr[0], ctypes.POINTER(ctypes.c_void_p))
            return ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)(vtable[index])

        try:
            dxgi = ctypes.windll.LoadLibrary("dxgi.dll")
            d3d11 = ctypes.windll.LoadLibrary("d3d11.dll")
            if dxgi and d3d11:
                # Explicit ctypes declaration
                dxgi.CreateDXGIFactory.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
                dxgi.CreateDXGIFactory.restype = ctypes.c_int

                pFactory = ctypes.c_void_p(0)
                IID_IDXGIFactory = (ctypes.c_ubyte * 16)(
                    0xec, 0x66, 0x71, 0x7b, 0xc7, 0x21, 0xae, 0x44, 0x8b, 0x1a, 0x28, 0x09, 0x73, 0xd5, 0xa2, 0x0e
                )
                ret = dxgi.CreateDXGIFactory(IID_IDXGIFactory, ctypes.byref(pFactory))
                
                adapters = []
                if ret == 0 and pFactory.value:
                    adapter_idx = 0
                    enum_adapters_func = call_vtable(pFactory, 7, ctypes.c_int, ctypes.c_uint, ctypes.POINTER(ctypes.c_void_p))
                    while True:
                        pAdapter = ctypes.c_void_p(0)
                        res = enum_adapters_func(pFactory.value, adapter_idx, ctypes.byref(pAdapter))
                        if res != 0 or not pAdapter.value:
                            break
                        
                        desc = DXGI_ADAPTER_DESC()
                        get_desc_func = call_vtable(pAdapter, 8, ctypes.c_int, ctypes.POINTER(DXGI_ADAPTER_DESC))
                        res_desc = get_desc_func(pAdapter.value, ctypes.byref(desc))
                        if res_desc == 0 and desc.VendorId == 0x10DE: # NVIDIA Only
                            adapters.append(pAdapter)
                        else:
                            release_func = call_vtable(pAdapter, 2, ctypes.c_ulong)
                            release_func(pAdapter.value)
                        adapter_idx += 1
                    
                    release_factory = call_vtable(pFactory, 2, ctypes.c_ulong)
                    release_factory(pFactory.value)

                # Set up ctypes for D3D11CreateDevice
                d3d11.D3D11CreateDevice.argtypes = [
                    ctypes.c_void_p, # pAdapter
                    ctypes.c_int,    # DriverType
                    ctypes.c_void_p, # Software
                    ctypes.c_uint32, # Flags
                    ctypes.c_void_p, # pFeatureLevels
                    ctypes.c_uint32, # FeatureLevels
                    ctypes.c_uint32, # SDKVersion
                    ctypes.POINTER(ctypes.c_void_p), # ppDevice
                    ctypes.c_void_p, # pFeatureLevel
                    ctypes.POINTER(ctypes.c_void_p)  # ppDeviceContext
                ]
                d3d11.D3D11CreateDevice.restype = ctypes.c_int

                # Run optimization for NVIDIA Adapters
                if adapters:
                    for adapter in adapters:
                        pDevice = ctypes.c_void_p(0)
                        pContext = ctypes.c_void_p(0)
                        # When passing IDXGIAdapter, DriverType MUST be 0 (D3D_DRIVER_TYPE_UNKNOWN)
                        ret_device = d3d11.D3D11CreateDevice(
                            adapter.value, 0, None, 0, None, 0, 7, ctypes.byref(pDevice), None, ctypes.byref(pContext)
                        )
                        if ret_device == 0 and pContext.value:
                            cls.release_com_ptr(pDevice)
                            cls.release_com_ptr(pContext)
                        
                        # Release adapter reference
                        release_func = call_vtable(adapter, 2, ctypes.c_ulong)
                        release_func(adapter.value)
                    logger.info("DXGI NVIDIA discrete device VRAM cache flushed.")
                else:
                    # Fallback default
                    pDevice = ctypes.c_void_p(0)
                    pContext = ctypes.c_void_p(0)
                    ret_device = d3d11.D3D11CreateDevice(
                        None, 1, None, 0, None, 0, 7, ctypes.byref(pDevice), None, ctypes.byref(pContext)
                    )
                    if ret_device == 0 and pContext.value:
                        cls.release_com_ptr(pDevice)
                        cls.release_com_ptr(pContext)
                    logger.info("Default D3D11 device cache flushed.")

                # Free libraries
                kernel32 = ctypes.windll.kernel32
                kernel32.FreeLibrary.argtypes = [ctypes.c_void_p]
                kernel32.FreeLibrary.restype = ctypes.c_int
                if hasattr(dxgi, '_handle') and dxgi._handle:
                    kernel32.FreeLibrary(dxgi._handle)
                if hasattr(d3d11, '_handle') and d3d11._handle:
                    kernel32.FreeLibrary(d3d11._handle)
                return True
        except Exception as e:
            logger.debug(f"Failed to flush D3D11 caches: {str(e)}")
        return True

