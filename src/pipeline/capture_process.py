"""Register the single camera pipeline for the local service controls."""
import atexit
import os
from pathlib import Path


def register(root):
    file = Path(root) / 'data/research/capture.pid'
    file.parent.mkdir(parents=True, exist_ok=True)
    try:
        pid = int(file.read_text())
        if pid != os.getpid() and pid > 0:
            if os.name == 'nt':
                import ctypes
                kernel = ctypes.WinDLL('kernel32', use_last_error=True)
                kernel.OpenProcess.restype = ctypes.c_void_p
                kernel.CloseHandle.argtypes = [ctypes.c_void_p]
                kernel.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
                handle = kernel.OpenProcess(0x1000, False, pid)
                if handle:
                    code = ctypes.c_ulong()
                    try:
                        if kernel.GetExitCodeProcess(handle, ctypes.byref(code)) and code.value == 259:
                            return False
                    finally:
                        kernel.CloseHandle(handle)
            else:
                try:
                    os.kill(pid, 0)
                    return False
                except ProcessLookupError:
                    pass
    except (OSError, ValueError):
        pass
    file.write_text(str(os.getpid()))

    def release():
        try:
            if file.read_text() == str(os.getpid()):
                file.unlink()
        except OSError:
            pass
    atexit.register(release)
    return True
