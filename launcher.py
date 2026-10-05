"""Frozen application entry point."""

import sys
from pathlib import Path

# Nuitka does not set the frozen attributes that runtime payload lookups rely on
if '__compiled__' in globals():
    meipass = str(Path(__file__).parent)
    sys._MEIPASS = meipass  # pyright: ignore[reportAttributeAccessIssue]
    sys.frozen = True  # pyright: ignore[reportAttributeAccessIssue]

if '--linux-proxy-helper' in sys.argv[1:]:
    sys.argv.remove('--linux-proxy-helper')
    from fleasion import linux_proxy_helper_daemon

    linux_proxy_helper_daemon.main()
else:
    # Load NumPy before PyQt6 on Windows. Some frozen Windows builds otherwise
    # fail while initializing NumPy's native DLLs after Qt has already loaded.
    if sys.platform == 'win32':
        import numpy  # noqa: F401

    from fleasion import main

    main()
