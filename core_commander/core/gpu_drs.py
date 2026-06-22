# -*- coding: utf-8 -*-
import os
import ctypes
from ctypes import c_uint32, c_int, c_void_p, c_wchar, Structure, Union, CFUNCTYPE, byref
from core_commander.utils.logger import logger

# NVAPI Constants
NVAPI_OK = 0
NVDRS_MAX_SETTING_VALUES = 100
NVAPI_UNICODE_STRING_SIZE = 2048

# Unicode String Type
NvAPI_UnicodeString = c_wchar * NVAPI_UNICODE_STRING_SIZE

# Union for value
class NVDRS_SETTING_VALUE(Union):
    _fields_ = [
        ('dword', c_uint32),
        ('binary', c_void_p),
        ('wstring', NvAPI_UnicodeString)
    ]

# NVDRS_SETTING Struct
class NVDRS_SETTING(Structure):
    _fields_ = [
        ('version', c_uint32),
        ('settingName', NvAPI_UnicodeString),
        ('settingId', c_uint32),
        ('settingLocation', c_int),
        ('isCurrentPredefined', c_uint32, 1),
        ('isPredefinedValid', c_uint32, 1),
        ('reserved', c_uint32, 30),
        ('settingType', c_uint32),
        ('settingValue', NVDRS_SETTING_VALUE)
    ]

# Function version macros
# #define NVDRS_SETTING_VER_1 NVAPI_STRUCT_VERSION(NVDRS_SETTING_v1, 1)
# Struct version is typically: sizeof(struct) | (ver << 16)
NVDRS_SETTING_VER = ctypes.sizeof(NVDRS_SETTING) | (1 << 16)


class NvidiaDrsService:
    """
    Direct interface with NVIDIA Driver Settings (DRS) API using low-level ctypes and NVAPI.
    Acts as a secure, programmatically validated alternative to external tools.
    """
    _nvapi = None
    _initialized = False

    # Function IDs for QueryInterface lookup
    ID_Initialize = 0x0150E828
    ID_Unload = 0xD22BDD7E
    ID_DRS_CreateSession = 0x0694D52E
    ID_DRS_DestroySession = 0xdad4c43c
    ID_DRS_LoadSettings = 0x375DBD6B
    ID_DRS_SaveSettings = 0x0B4B5808
    ID_DRS_GetCurrentGlobalProfile = 0x61715367
    ID_DRS_SetSetting = 0x57700D11

    # DRS Setting Hex IDs
    SETTING_LOW_LATENCY_MODE = 0x005A32AA
    SETTING_MAX_PRE_RENDERED_FRAMES = 0x80414164
    SETTING_POWER_MANAGEMENT_MODE = 0x804D7727
    SETTING_THREADED_OPTIMIZATION = 0x8062B2F4
    SETTING_SHADER_CACHE_SIZE = 0x709AD14B

    @classmethod
    def initialize_nvapi(cls) -> bool:
        if cls._initialized:
            return True
        try:
            # Load nvapi64.dll for 64-bit systems
            cls._nvapi = ctypes.WinDLL("nvapi64.dll")
            if not cls._nvapi:
                logger.warning("nvapi64.dll could not be loaded directly.")
                return False

            # Query interface function
            cls._nvapi.nvapi_QueryInterface.restype = c_void_p
            cls._nvapi.nvapi_QueryInterface.argtypes = [c_uint32]

            # Resolve NvAPI_Initialize
            init_ptr = cls._nvapi.nvapi_QueryInterface(cls.ID_Initialize)
            if not init_ptr:
                logger.error("Failed to query NvAPI_Initialize function pointer.")
                return False
            
            NvAPI_Initialize = CFUNCTYPE(c_int)(init_ptr)
            status = NvAPI_Initialize()
            if status == NVAPI_OK:
                cls._initialized = True
                logger.info("NVAPI successfully initialized.")
                return True
            else:
                logger.error(f"NvAPI_Initialize failed with status code: {status}")
                return False
        except Exception as e:
            logger.debug(f"Failed to load or initialize NVAPI: {str(e)}")
            cls._nvapi = None
            return False

    @classmethod
    def unload_nvapi(cls):
        if cls._initialized and cls._nvapi:
            try:
                unload_ptr = cls._nvapi.nvapi_QueryInterface(cls.ID_Unload)
                if unload_ptr:
                    NvAPI_Unload = CFUNCTYPE(c_int)(unload_ptr)
                    status = NvAPI_Unload()
                    logger.info(f"NVAPI unloaded with status code: {status}")
            except Exception as e:
                logger.debug(f"Failed to unload NVAPI: {e}")
            cls._initialized = False
            try:
                handle = cls._nvapi._handle
                if handle:
                    kernel32 = ctypes.windll.kernel32
                    kernel32.FreeLibrary.argtypes = [c_void_p]
                    kernel32.FreeLibrary.restype = c_int
                    kernel32.FreeLibrary(handle)
                    logger.info("NVAPI DLL library handle successfully freed.")
            except Exception as ex:
                logger.debug(f"FreeLibrary failed for nvapi64.dll: {ex}")
            cls._nvapi = None

    @classmethod
    def _get_function(cls, func_id: int, restype, argtypes):
        if not cls.initialize_nvapi():
            return None
        ptr = cls._nvapi.nvapi_QueryInterface(func_id)
        if not ptr:
            return None
        return CFUNCTYPE(restype, *argtypes)(ptr)

    @classmethod
    def apply_gaming_drs_profile(cls, enable: bool) -> bool:
        """
        Applies or restores gaming DRS settings globally using direct NVAPI sessions.
        Fallback to Profile Inspector if NVAPI is not loaded.
        """
        if not cls.initialize_nvapi():
            logger.warning("NVAPI not available. Direct DRS overrides skipped. Fallback to Profile Inspector.")
            return False

        hSession = c_void_p(0)
        hProfile = c_void_p(0)

        # Retrieve function pointers
        DRS_CreateSession = cls._get_function(cls.ID_DRS_CreateSession, c_int, [ctypes.POINTER(c_void_p)])
        DRS_DestroySession = cls._get_function(cls.ID_DRS_DestroySession, c_int, [c_void_p])
        DRS_LoadSettings = cls._get_function(cls.ID_DRS_LoadSettings, c_int, [c_void_p])
        DRS_SaveSettings = cls._get_function(cls.ID_DRS_SaveSettings, c_int, [c_void_p])
        DRS_GetCurrentGlobalProfile = cls._get_function(cls.ID_DRS_GetCurrentGlobalProfile, c_int, [c_void_p, ctypes.POINTER(c_void_p)])
        DRS_SetSetting = cls._get_function(cls.ID_DRS_SetSetting, c_int, [c_void_p, c_void_p, ctypes.POINTER(NVDRS_SETTING)])

        if not all([DRS_CreateSession, DRS_DestroySession, DRS_LoadSettings, DRS_SaveSettings, DRS_GetCurrentGlobalProfile, DRS_SetSetting]):
            logger.error("Failed to query critical DRS NVAPI function pointers.")
            return False

        try:
            # Create session
            status = DRS_CreateSession(byref(hSession))
            if status != NVAPI_OK:
                logger.error(f"DRS_CreateSession failed: {status}")
                return False

            # Load settings
            status = DRS_LoadSettings(hSession)
            if status != NVAPI_OK:
                DRS_DestroySession(hSession)
                logger.error(f"DRS_LoadSettings failed: {status}")
                return False

            # Retrieve global profile
            status = DRS_GetCurrentGlobalProfile(hSession, byref(hProfile))
            if status != NVAPI_OK:
                DRS_DestroySession(hSession)
                logger.error(f"DRS_GetCurrentGlobalProfile failed: {status}")
                return False

            # Define settings to write
            # For enable=True: Low Latency=Ultra (2), Max Pre-Render=1 (1), Power=Max Performance (1), Threaded=On (1), Shader Cache=Unlimited (0xFFFFFFFF)
            # For enable=False: Reset to defaults (or driver optimal)
            settings_to_apply = []
            if enable:
                settings_to_apply = [
                    (cls.SETTING_LOW_LATENCY_MODE, 2),
                    (cls.SETTING_MAX_PRE_RENDERED_FRAMES, 1),
                    (cls.SETTING_POWER_MANAGEMENT_MODE, 1),
                    (cls.SETTING_THREADED_OPTIMIZATION, 1),
                    (cls.SETTING_SHADER_CACHE_SIZE, 0xFFFFFFFF)
                ]
            else:
                # Default driver profile settings
                settings_to_apply = [
                    (cls.SETTING_LOW_LATENCY_MODE, 0), # Off
                    (cls.SETTING_MAX_PRE_RENDERED_FRAMES, 3), # Use application setting
                    (cls.SETTING_POWER_MANAGEMENT_MODE, 0), # Optimal Power
                    (cls.SETTING_THREADED_OPTIMIZATION, 3), # Auto
                    (cls.SETTING_SHADER_CACHE_SIZE, 4096) # default 4GB limit
                ]

            success_count = 0
            for setting_id, val in settings_to_apply:
                setting = NVDRS_SETTING()
                setting.version = NVDRS_SETTING_VER
                setting.settingId = setting_id
                setting.settingType = 0 # DWORD type
                setting.settingLocation = 0
                setting.settingValue.dword = val

                status = DRS_SetSetting(hSession, hProfile, byref(setting))
                if status == NVAPI_OK:
                    success_count += 1
                    logger.debug(f"Successfully configured DRS setting {hex(setting_id)} to {hex(val)}")
                else:
                    logger.warning(f"Failed to set DRS setting {hex(setting_id)}. Status: {status}")

            # Save settings
            if success_count > 0:
                save_status = DRS_SaveSettings(hSession)
                if save_status == NVAPI_OK:
                    logger.info(f"NVIDIA DRS Profile Settings saved successfully. Applied {success_count} overrides.")
                else:
                    logger.error(f"DRS_SaveSettings failed: {save_status}")
            
            # Clean up session
            DRS_DestroySession(hSession)
            return success_count == len(settings_to_apply)

        except Exception as e:
            logger.error(f"Error during NVAPI DRS profile operations: {str(e)}")
            if hSession:
                try:
                    DRS_DestroySession(hSession)
                except Exception:
                    pass
            return False
