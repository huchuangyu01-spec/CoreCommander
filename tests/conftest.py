# Ensure mock_bootstrap is imported first to patch platform-specific libraries before any other imports
try:
    from tests import mock_bootstrap
except ImportError:
    import mock_bootstrap

import pytest
