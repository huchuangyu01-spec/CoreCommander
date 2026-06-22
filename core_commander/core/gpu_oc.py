# -*- coding: utf-8 -*-
import ctypes
from ctypes import c_uint32, c_int, c_void_p, Structure, Union, CFUNCTYPE, byref
import threading
from core_commander.utils.logger import logger

_gpu_oc_lock = threading.Lock()

# --- NVAPI Ctypes Definitions ---
class NV_GPU_PERF_PSTATES20_PARAM_DELTA(Structure):
    _fields_ = [
        ("value", c_int),
        ("valueMin", c_int),
        ("valueMax", c_int)
    ]

class NV_GPU_PSTATE20_BASE_VOLTAGE_ENTRY(Structure):
    _fields_ = [
        ("domainId", c_uint32),
        ("editable_and_reserved", c_uint32),
        ("voltageUV", c_uint32),
        ("voltageDeltaUV", NV_GPU_PERF_PSTATES20_PARAM_DELTA)
    ]

class CLOCK_ENTRY_DATA_RANGE(Structure):
    _fields_ = [
        ("minFrequencyKHz", c_uint32),
        ("maxFrequencyKHz", c_uint32),
        ("domainId", c_uint32),
        ("minVoltageUV", c_uint32),
        ("maxVoltageUV", c_uint32)
    ]

class CLOCK_ENTRY_DATA_SINGLE(Structure):
    _fields_ = [
        ("frequencyKHz", c_uint32)
    ]

class CLOCK_ENTRY_DATA(Union):
    _fields_ = [
        ("single", CLOCK_ENTRY_DATA_SINGLE),
        ("range", CLOCK_ENTRY_DATA_RANGE),
        ("reserved", c_uint32 * 5)
    ]

class NV_GPU_PSTATE20_CLOCK_ENTRY(Structure):
    _fields_ = [
        ("domainId", c_uint32),
        ("typeId", c_uint32),
        ("editable_and_reserved", c_uint32),
        ("frequencyDeltaKHz", NV_GPU_PERF_PSTATES20_PARAM_DELTA),
        ("data", CLOCK_ENTRY_DATA)
    ]

class NV_GPU_PERF_PSTATE20(Structure):
    _fields_ = [
        ("pstateId", c_uint32),
        ("editable_and_reserved", c_uint32),
        ("clocks", NV_GPU_PSTATE20_CLOCK_ENTRY * 8),
        ("baseVoltages", NV_GPU_PSTATE20_BASE_VOLTAGE_ENTRY * 4)
    ]

class NV_GPU_PERF_PSTATES20_INFO(Structure):
    _fields_ = [
        ("version", c_uint32),
        ("editable_and_reserved", c_uint32),
        ("numPstates", c_uint32),
        ("numClocks", c_uint32),
        ("numBaseVoltages", c_uint32),
        ("pstates", NV_GPU_PERF_PSTATE20 * 16),
        ("ovNumVoltages", c_uint32),
        ("ovVoltages", NV_GPU_PSTATE20_BASE_VOLTAGE_ENTRY * 4)
    ]

class nvmlUtilization_t(Structure):
    _fields_ = [
        ("gpu", c_uint32),
        ("memory", c_uint32)
    ]

class nvmlMemory_t(Structure):
    _fields_ = [
        ("total", ctypes.c_ulonglong),
        ("free", ctypes.c_ulonglong),
        ("used", ctypes.c_ulonglong)
    ]

class GpuOverclockService:
    """
    Direct interface with NVIDIA NVAPI/NVML and AMD ADL using low-level ctypes.
    Handles GPU core/memory overclocking, voltage offset, power limit and temperature target controls.
    """
    _nvapi = None
    _nvapi_initialized = False
    _nvml = None
    _nvml_initialized = False
    _adl = None
    _adl_initialized = False
    _selected_gpu_index = 0
    overclock_applied = False

    # Function pointers cached
    _NvAPI_EnumPhysicalGPUs = None
    _NvAPI_GPU_GetPstates20 = None
    _NvAPI_GPU_SetPstates20 = None
    _gpu_handles = []

    # NVML Function pointers
    _nvmlDeviceGetHandleByIndex = None
    _nvmlDeviceGetPowerManagementLimit = None
    _nvmlDeviceGetPowerManagementLimitConstraints = None
    _nvmlDeviceGetPowerManagementDefaultLimit = None
    _nvmlDeviceSetPowerManagementLimit = None
    _nvmlDeviceGetTemperatureLimit = None
    _nvmlDeviceSetTemperatureLimit = None
    _nvmlDeviceGetClockInfo = None
    _nvmlDeviceGetTemperature = None
    _nvmlDeviceGetPowerUsage = None
    _nvmlDeviceGetFanSpeed = None
    _nvmlDeviceGetName = None
    _nvmlDeviceGetUtilizationRates = None
    _nvmlDeviceGetMemoryInfo = None
    _nvmlDeviceGetCurrPcieLinkWidth = None
    _nvmlDeviceGetCurrPcieLinkGeneration = None
    _nvml_gpu_handle = None

    @classmethod
    def _find_discrete_gpu_index(cls) -> int:
        if not cls._nvml:
            return 0
        try:
            # Query count
            count_func = getattr(cls._nvml, 'nvmlDeviceGetCount', None)
            if not count_func:
                return 0
            count = c_uint32(0)
            if count_func(byref(count)) != 0 or count.value == 0:
                return 0
            
            best_idx = 0
            max_vram = 0
            
            get_handle_func = cls._nvmlDeviceGetHandleByIndex
            get_mem_func = cls._nvmlDeviceGetMemoryInfo
            
            for i in range(count.value):
                handle = c_void_p()
                if get_handle_func(i, byref(handle)) == 0 and handle.value:
                    if get_mem_func:
                        mem_info = nvmlMemory_t()
                        if get_mem_func(handle, byref(mem_info)) == 0:
                            if mem_info.total > max_vram:
                                max_vram = mem_info.total
                                best_idx = i
            return best_idx
        except Exception as e:
            logger.debug(f"Error finding discrete GPU index: {e}")
            return 0

    @classmethod
    def initialize(cls):
        """
        Initializes NVIDIA and AMD GPU libraries.
        """
        cls.initialize_nvidia()
        cls.initialize_amd()

    @classmethod
    def initialize_nvidia(cls):
        if cls._nvapi_initialized and cls._nvml_initialized:
            return True
        with _gpu_oc_lock:
            return cls._initialize_nvidia_unlocked()

    @classmethod
    def _initialize_nvidia_unlocked(cls):

        # 1. Initialize NVAPI
        try:
            cls._nvapi = ctypes.WinDLL("nvapi64.dll")
            cls._nvapi.nvapi_QueryInterface.restype = c_void_p
            cls._nvapi.nvapi_QueryInterface.argtypes = [c_uint32]

            init_ptr = cls._nvapi.nvapi_QueryInterface(0x0150E828)
            enum_ptr = cls._nvapi.nvapi_QueryInterface(0xE5AC921F)
            get_pstates_ptr = cls._nvapi.nvapi_QueryInterface(0x6FF81213)
            set_pstates_ptr = cls._nvapi.nvapi_QueryInterface(0x0F4DAE6B)

            if init_ptr and enum_ptr and get_pstates_ptr and set_pstates_ptr:
                NvAPI_Initialize = CFUNCTYPE(c_int)(init_ptr)
                if NvAPI_Initialize() == 0:
                    cls._NvAPI_EnumPhysicalGPUs = CFUNCTYPE(c_int, c_void_p * 64, ctypes.POINTER(c_uint32))(enum_ptr)
                    cls._NvAPI_GPU_GetPstates20 = CFUNCTYPE(c_int, c_void_p, ctypes.POINTER(NV_GPU_PERF_PSTATES20_INFO))(get_pstates_ptr)
                    cls._NvAPI_GPU_SetPstates20 = CFUNCTYPE(c_int, c_void_p, ctypes.POINTER(NV_GPU_PERF_PSTATES20_INFO))(set_pstates_ptr)
                    
                    gpus = (c_void_p * 64)()
                    gpu_count = c_uint32(0)
                    if cls._NvAPI_EnumPhysicalGPUs(gpus, byref(gpu_count)) == 0 and gpu_count.value > 0:
                        cls._gpu_handles = [gpus[i] for i in range(gpu_count.value)]
                        cls._nvapi_initialized = True
                        logger.info(f"NVAPI Overclocking engine initialized with {gpu_count.value} GPU(s).")
        except Exception as e:
            logger.debug(f"Failed to initialize NVAPI Overclocking engine: {e}")

        # 2. Initialize NVML
        try:
            cls._nvml = ctypes.WinDLL("nvml.dll")
            if cls._nvml.nvmlInit() == 0:
                cls._nvmlDeviceGetHandleByIndex = getattr(cls._nvml, 'nvmlDeviceGetHandleByIndex_v2', getattr(cls._nvml, 'nvmlDeviceGetHandleByIndex', None))
                cls._nvmlDeviceGetPowerManagementLimit = getattr(cls._nvml, 'nvmlDeviceGetPowerManagementLimit', None)
                cls._nvmlDeviceGetPowerManagementLimitConstraints = getattr(cls._nvml, 'nvmlDeviceGetPowerManagementLimitConstraints', None)
                cls._nvmlDeviceGetPowerManagementDefaultLimit = getattr(cls._nvml, 'nvmlDeviceGetPowerManagementDefaultLimit', None)
                cls._nvmlDeviceSetPowerManagementLimit = getattr(cls._nvml, 'nvmlDeviceSetPowerManagementLimit', None)
                cls._nvmlDeviceGetTemperatureLimit = getattr(cls._nvml, 'nvmlDeviceGetTemperatureLimit', None)
                cls._nvmlDeviceSetTemperatureLimit = getattr(cls._nvml, 'nvmlDeviceSetTemperatureLimit', None)
                cls._nvmlDeviceGetClockInfo = getattr(cls._nvml, 'nvmlDeviceGetClockInfo', None)
                cls._nvmlDeviceGetTemperature = getattr(cls._nvml, 'nvmlDeviceGetTemperature', None)
                cls._nvmlDeviceGetPowerUsage = getattr(cls._nvml, 'nvmlDeviceGetPowerUsage', None)
                cls._nvmlDeviceGetFanSpeed = getattr(cls._nvml, 'nvmlDeviceGetFanSpeed', None)
                cls._nvmlDeviceGetName = getattr(cls._nvml, 'nvmlDeviceGetName', None)
                cls._nvmlDeviceGetUtilizationRates = getattr(cls._nvml, 'nvmlDeviceGetUtilizationRates', None)
                cls._nvmlDeviceGetMemoryInfo = getattr(cls._nvml, 'nvmlDeviceGetMemoryInfo', None)
                cls._nvmlDeviceGetCurrPcieLinkWidth = getattr(cls._nvml, 'nvmlDeviceGetCurrPcieLinkWidth', None)
                cls._nvmlDeviceGetCurrPcieLinkGeneration = getattr(cls._nvml, 'nvmlDeviceGetCurrPcieLinkGeneration', None)

                # Set ctypes signatures for security and 64-bit alignment
                if cls._nvmlDeviceGetClockInfo:
                    cls._nvmlDeviceGetClockInfo.argtypes = [c_void_p, c_int, ctypes.POINTER(c_uint32)]
                    cls._nvmlDeviceGetClockInfo.restype = c_int
                if cls._nvmlDeviceGetTemperature:
                    cls._nvmlDeviceGetTemperature.argtypes = [c_void_p, c_int, ctypes.POINTER(c_uint32)]
                    cls._nvmlDeviceGetTemperature.restype = c_int
                if cls._nvmlDeviceGetPowerUsage:
                    cls._nvmlDeviceGetPowerUsage.argtypes = [c_void_p, ctypes.POINTER(c_uint32)]
                    cls._nvmlDeviceGetPowerUsage.restype = c_int
                if cls._nvmlDeviceGetFanSpeed:
                    cls._nvmlDeviceGetFanSpeed.argtypes = [c_void_p, ctypes.POINTER(c_uint32)]
                    cls._nvmlDeviceGetFanSpeed.restype = c_int
                if cls._nvmlDeviceGetName:
                    cls._nvmlDeviceGetName.argtypes = [c_void_p, ctypes.c_char_p, c_uint32]
                    cls._nvmlDeviceGetName.restype = c_int
                if cls._nvmlDeviceGetUtilizationRates:
                    cls._nvmlDeviceGetUtilizationRates.argtypes = [c_void_p, ctypes.POINTER(nvmlUtilization_t)]
                    cls._nvmlDeviceGetUtilizationRates.restype = c_int
                if cls._nvmlDeviceGetMemoryInfo:
                    cls._nvmlDeviceGetMemoryInfo.argtypes = [c_void_p, ctypes.POINTER(nvmlMemory_t)]
                    cls._nvmlDeviceGetMemoryInfo.restype = c_int
                if cls._nvmlDeviceGetCurrPcieLinkWidth:
                    cls._nvmlDeviceGetCurrPcieLinkWidth.argtypes = [c_void_p, ctypes.POINTER(c_uint32)]
                    cls._nvmlDeviceGetCurrPcieLinkWidth.restype = c_int
                if cls._nvmlDeviceGetCurrPcieLinkGeneration:
                    cls._nvmlDeviceGetCurrPcieLinkGeneration.argtypes = [c_void_p, ctypes.POINTER(c_uint32)]
                    cls._nvmlDeviceGetCurrPcieLinkGeneration.restype = c_int

                if cls._nvmlDeviceGetHandleByIndex:
                    cls._nvml_initialized = True
                    cls._selected_gpu_index = cls._find_discrete_gpu_index()
                    handle = c_void_p()
                    if cls._nvmlDeviceGetHandleByIndex(cls._selected_gpu_index, byref(handle)) == 0:
                        cls._nvml_gpu_handle = handle
                        logger.info(f"NVML Overclocking engine initialized successfully with GPU index {cls._selected_gpu_index}.")
        except Exception as e:
            logger.debug(f"Failed to initialize NVML Overclocking engine: {e}")

        return cls._nvapi_initialized

    @classmethod
    def initialize_amd(cls):
        with _gpu_oc_lock:
            # Placeholder for AMD ADL
            cls._adl_initialized = False

    @classmethod
    def get_gpu_oc_info(cls) -> dict:
        cls.initialize()
        with _gpu_oc_lock:
            return cls._get_gpu_oc_info_unlocked()

    @classmethod
    def _get_gpu_oc_info_unlocked(cls) -> dict:
        """
        Retrieves current GPU overclock offsets and power/temp limits.
        """
        info = {
            'supported': False,
            'gpu_name': "NVIDIA GPU",
            'core_offset': 0,
            'mem_offset': 0,
            'voltage_offset': 0,
            'power_limit_w': 0.0,
            'power_default_w': 0.0,
            'power_min_w': 0.0,
            'power_max_w': 0.0,
            'temp_limit': 83, # Default standard temp limit
            'temp_max': 90,
            'temp_min': 65,
            
            # Live telemetry default placeholders
            'live_core_clock': 0,
            'live_mem_clock': 0,
            'live_temp': 0,
            'live_vram_temp': 0,
            'live_power_w': 0.0,
            'live_fan_speed': -1,
            'live_gpu_util': -1,
            'live_mem_util': -1,
            'vram_total_mb': 0.0,
            'vram_used_mb': 0.0,
            'pcie_width': 0,
            'pcie_gen': 0
        }

        # 1. NVIDIA Path
        if cls._nvapi_initialized and cls._gpu_handles:
            gpu_idx = min(cls._selected_gpu_index, len(cls._gpu_handles) - 1)
            gpu = cls._gpu_handles[gpu_idx]
            pstates_info = NV_GPU_PERF_PSTATES20_INFO()
            pstates_info.version = ctypes.sizeof(NV_GPU_PERF_PSTATES20_INFO) | (2 << 16) # Version 2
            
            if cls._NvAPI_GPU_GetPstates20(gpu, byref(pstates_info)) == 0:
                info['supported'] = True
                for i in range(pstates_info.numPstates):
                    pst = pstates_info.pstates[i]
                    if pst.pstateId == 0: # P0 high performance state
                        # Clock Domain 0 = Core, Domain 4 = Memory
                        for c_idx in range(pstates_info.numClocks):
                            clk = pst.clocks[c_idx]
                            if clk.domainId == 0:
                                info['core_offset'] = clk.frequencyDeltaKHz.value // 1000 # Convert kHz to MHz
                            elif clk.domainId == 4:
                                info['mem_offset'] = clk.frequencyDeltaKHz.value // 1000

                # Read Power Limit via NVML
                if cls._nvml_initialized and cls._nvml_gpu_handle:
                    power_val = c_uint32(0)
                    if cls._nvmlDeviceGetPowerManagementLimit and cls._nvmlDeviceGetPowerManagementLimit(cls._nvml_gpu_handle, byref(power_val)) == 0:
                        info['power_limit_w'] = power_val.value / 1000.0
                    
                    default_power_val = c_uint32(0)
                    if cls._nvmlDeviceGetPowerManagementDefaultLimit and cls._nvmlDeviceGetPowerManagementDefaultLimit(cls._nvml_gpu_handle, byref(default_power_val)) == 0:
                        info['power_default_w'] = default_power_val.value / 1000.0
                    
                    min_val = c_uint32(0)
                    max_val = c_uint32(0)
                    if cls._nvmlDeviceGetPowerManagementLimitConstraints and cls._nvmlDeviceGetPowerManagementLimitConstraints(cls._nvml_gpu_handle, byref(min_val), byref(max_val)) == 0:
                        info['power_min_w'] = min_val.value / 1000.0
                        info['power_max_w'] = max_val.value / 1000.0

                    # Read Temperature Limit (Type 0 = NVML_TEMP_THRESHOLD_GPU_MAX)
                    temp_val = c_uint32(0)
                    if cls._nvmlDeviceGetTemperatureLimit and cls._nvmlDeviceGetTemperatureLimit(cls._nvml_gpu_handle, 0, byref(temp_val)) == 0:
                        info['temp_limit'] = temp_val.value

                    # --- Live Telemetry Reading ---
                    # 1. GPU Name
                    if cls._nvmlDeviceGetName:
                        name_buf = ctypes.create_string_buffer(64)
                        if cls._nvmlDeviceGetName(cls._nvml_gpu_handle, name_buf, 64) == 0:
                            info['gpu_name'] = name_buf.value.decode('utf-8', errors='ignore')

                    # 2. Live Clocks (Graphics = 0, Memory = 2)
                    if cls._nvmlDeviceGetClockInfo:
                        val = c_uint32(0)
                        if cls._nvmlDeviceGetClockInfo(cls._nvml_gpu_handle, 0, byref(val)) == 0:
                            info['live_core_clock'] = val.value
                        val = c_uint32(0)
                        if cls._nvmlDeviceGetClockInfo(cls._nvml_gpu_handle, 2, byref(val)) == 0:
                            info['live_mem_clock'] = val.value

                    # 3. Live Temperature (GPU = 0, VRAM = 1)
                    if cls._nvmlDeviceGetTemperature:
                        val = c_uint32(0)
                        if cls._nvmlDeviceGetTemperature(cls._nvml_gpu_handle, 0, byref(val)) == 0:
                            info['live_temp'] = val.value
                        val_vram = c_uint32(0)
                        if cls._nvmlDeviceGetTemperature(cls._nvml_gpu_handle, 1, byref(val_vram)) == 0:
                            info['live_vram_temp'] = val_vram.value
                        else:
                            info['live_vram_temp'] = 0

                    # 4. Live Power Draw
                    if cls._nvmlDeviceGetPowerUsage:
                        val = c_uint32(0)
                        if cls._nvmlDeviceGetPowerUsage(cls._nvml_gpu_handle, byref(val)) == 0:
                            info['live_power_w'] = val.value / 1000.0

                    # 5. Live Fan Speed
                    if cls._nvmlDeviceGetFanSpeed:
                        val = c_uint32(0)
                        if cls._nvmlDeviceGetFanSpeed(cls._nvml_gpu_handle, byref(val)) == 0:
                            info['live_fan_speed'] = val.value

                    # 6. Utilization Rates
                    if cls._nvmlDeviceGetUtilizationRates:
                        util = nvmlUtilization_t()
                        if cls._nvmlDeviceGetUtilizationRates(cls._nvml_gpu_handle, byref(util)) == 0:
                            info['live_gpu_util'] = util.gpu
                            info['live_mem_util'] = util.memory

                    # 7. VRAM Memory Info
                    if cls._nvmlDeviceGetMemoryInfo:
                        mem_info = nvmlMemory_t()
                        if cls._nvmlDeviceGetMemoryInfo(cls._nvml_gpu_handle, byref(mem_info)) == 0:
                            info['vram_total_mb'] = mem_info.total / (1024.0 * 1024.0)
                            info['vram_used_mb'] = mem_info.used / (1024.0 * 1024.0)

                    # 8. PCIe Info
                    if cls._nvmlDeviceGetCurrPcieLinkWidth:
                        val = c_uint32(0)
                        if cls._nvmlDeviceGetCurrPcieLinkWidth(cls._nvml_gpu_handle, byref(val)) == 0:
                            info['pcie_width'] = val.value
                    if cls._nvmlDeviceGetCurrPcieLinkGeneration:
                        val = c_uint32(0)
                        if cls._nvmlDeviceGetCurrPcieLinkGeneration(cls._nvml_gpu_handle, byref(val)) == 0:
                            info['pcie_gen'] = val.value

            return info

    @classmethod
    def apply_overclock(cls, core_offset: int, mem_offset: int, power_limit_pct: float = None, temp_limit: int = None, voltage_pct: int = 0) -> bool:
        cls.initialize()
        with _gpu_oc_lock:
            return cls._apply_overclock_unlocked(core_offset, mem_offset, power_limit_pct, temp_limit, voltage_pct)

    @classmethod
    def _apply_overclock_unlocked(cls, core_offset: int, mem_offset: int, power_limit_pct: float = None, temp_limit: int = None, voltage_pct: int = 0) -> bool:
        """
        Applies core offset, memory offset, power limit and temp limit to the primary GPU.
        """
        success = False

        # 1. NVIDIA Path
        if cls._nvapi_initialized and cls._gpu_handles:
            gpu_idx = min(cls._selected_gpu_index, len(cls._gpu_handles) - 1)
            gpu = cls._gpu_handles[gpu_idx]
            
            # Setup NVAPI offsets
            mod_info = NV_GPU_PERF_PSTATES20_INFO()
            mod_info.version = ctypes.sizeof(NV_GPU_PERF_PSTATES20_INFO) | (2 << 16) # Version 2
            mod_info.numPstates = 1
            mod_info.numClocks = 2
            
            pst = mod_info.pstates[0]
            pst.pstateId = 0
            
            # Core clock offset (Domain 0)
            pst.clocks[0].domainId = 0
            pst.clocks[0].typeId = 1 # Offset
            pst.clocks[0].frequencyDeltaKHz.value = core_offset * 1000 # MHz to kHz
            
            # Memory clock offset (Domain 4)
            pst.clocks[1].domainId = 4
            pst.clocks[1].typeId = 1 # Offset
            pst.clocks[1].frequencyDeltaKHz.value = mem_offset * 1000 # MHz to kHz
            
            res = cls._NvAPI_GPU_SetPstates20(gpu, byref(mod_info))
            if res == 0:
                logger.info(f"NVIDIA Overclock offsets applied: Core +{core_offset}MHz, Mem +{mem_offset}MHz")
                success = True
                if core_offset != 0 or mem_offset != 0:
                    cls.overclock_applied = True
                else:
                    cls.overclock_applied = False
            else:
                logger.error(f"NvAPI_GPU_SetPstates20 failed: {res}")

            # Apply Power and Temperature Limits via NVML
            if cls._nvml_initialized and cls._nvml_gpu_handle:
                # Calculate power in milliwatts from percentage
                if power_limit_pct is not None:
                    min_val = c_uint32(0)
                    max_val = c_uint32(0)
                    default_power = c_uint32(0)
                    
                    if cls._nvmlDeviceGetPowerManagementDefaultLimit:
                        cls._nvmlDeviceGetPowerManagementDefaultLimit(cls._nvml_gpu_handle, byref(default_power))
                    if cls._nvmlDeviceGetPowerManagementLimitConstraints:
                        cls._nvmlDeviceGetPowerManagementLimitConstraints(cls._nvml_gpu_handle, byref(min_val), byref(max_val))
                    
                    if default_power.value > 0 and cls._nvmlDeviceSetPowerManagementLimit:
                        target_mw = int(default_power.value * (power_limit_pct / 100.0))
                        # Bound to physical constraints
                        target_mw = max(min_val.value, min(max_val.value, target_mw))
                        
                        set_pl_res = cls._nvmlDeviceSetPowerManagementLimit(cls._nvml_gpu_handle, target_mw)
                        if set_pl_res == 0:
                            logger.info(f"NVIDIA Power Limit set to {target_mw / 1000.0} W ({power_limit_pct}%)")
                        else:
                            logger.error(f"nvmlDeviceSetPowerManagementLimit failed: {set_pl_res}")

                # Temperature limit
                if temp_limit is not None and cls._nvmlDeviceSetTemperatureLimit:
                    # LimitType = 0 (NVML_TEMP_THRESHOLD_GPU_MAX)
                    set_temp_res = cls._nvmlDeviceSetTemperatureLimit(cls._nvml_gpu_handle, 0, temp_limit)
                    if set_temp_res == 0:
                        logger.info(f"NVIDIA Temp Limit set to {temp_limit} C")
                    else:
                        logger.error(f"nvmlDeviceSetTemperatureLimit failed: {set_temp_res}")

            return success

    @classmethod
    def restore_defaults(cls):
        """
        Safely restores all GPU clocks, power limit, and temp limit to defaults.
        """
        logger.info("Restoring GPU clocks and limits to defaults...")
        cls.overclock_applied = False
        cls.apply_overclock(core_offset=0, mem_offset=0, power_limit_pct=100.0, temp_limit=83)
        cls.unload()

    @classmethod
    def unload(cls):
        _gpu_oc_lock.acquire()
        try:
            if cls._nvapi_initialized and cls._nvapi:
                try:
                    unload_ptr = cls._nvapi.nvapi_QueryInterface(0xD22BDD7E)
                    if unload_ptr:
                        NvAPI_Unload = CFUNCTYPE(c_int)(unload_ptr)
                        NvAPI_Unload()
                except Exception:
                    pass
                cls._nvapi = None
                cls._nvapi_initialized = False

            if cls._nvml_initialized and cls._nvml:
                try:
                    cls._nvml.nvmlShutdown()
                except Exception:
                    pass
                cls._nvml = None
                cls._nvml_initialized = False
        finally:
            _gpu_oc_lock.release()
