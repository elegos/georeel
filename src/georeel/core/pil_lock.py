"""Global lock that serialises all PIL C-extension operations.

PIL's ``_imaging.so`` C extension is not thread-safe: its internal memory
arena and error-handler state are process-global.  Concurrent PIL calls from
multiple threads (decode, convert, paste, save …) can corrupt the heap, which
then manifests as a SIGSEGV anywhere — including in unrelated code such as
Qt's font renderer.

Usage::

    from georeel.core.pil_lock import PIL_LOCK

    with PIL_LOCK:
        image = Image.open(buf)
        image.load()
        result = image.convert("RGB")
"""

import threading

PIL_LOCK = threading.Lock()
