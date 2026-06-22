# -*- coding: utf-8 -*-
# Core Commander - Decoupled Layered Architecture package
__version__ = "2.0"

import sys
import dataclasses

# -------------------------------------------------------------------------
# GLOBAL MONKEY-PATCH for Python 3.11 compatibility with legacy fairseq/hydra
# This dynamically catches and bypasses the `ValueError: mutable default` 
# thrown by Python 3.11's stricter dataclasses implementation.
# -------------------------------------------------------------------------
if sys.version_info >= (3, 11):
    _orig_get_field = dataclasses._get_field

    def safe_get_field(cls, a_name, a_type, default_kw_only=False):
        try:
            return _orig_get_field(cls, a_name, a_type, default_kw_only)
        except ValueError as e:
            if "mutable default" in str(e):
                # Temporarily remove the default value from the class to trick _get_field
                val = getattr(cls, a_name, dataclasses.MISSING)
                setattr(cls, a_name, None)
                try:
                    f = _orig_get_field(cls, a_name, a_type, default_kw_only)
                    f.default = val
                    return f
                finally:
                    setattr(cls, a_name, val)
            raise e

    dataclasses._get_field = safe_get_field
