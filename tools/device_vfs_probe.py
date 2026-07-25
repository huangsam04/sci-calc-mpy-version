# Read-only boot/VFS environment probe for release planning.
# Emits VFS_* fact lines; never opens a file for writing.
import binascii
import gc
import hashlib
import os
import sys


_ANCHOR_PATHS = (
    "/boot.py",
    "/main.py",
    "/sdcard.py",
    "/recovery.py",
    "/display/mono_palette.py",
    "/display/ssd1322.py",
    "/bootsel.py",
    "/version.py",
)


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        while True:
            chunk = stream.read(512)
            if not chunk:
                break
            digest.update(chunk)
    return binascii.hexlify(digest.digest()).decode()


def _statvfs_text(path):
    try:
        stats = os.statvfs(path)
    except OSError:
        return "unavailable"
    return (
        "bsize=" + str(stats[0])
        + ",blocks=" + str(stats[2])
        + ",bfree=" + str(stats[3]))


def _listdir_text(path):
    try:
        return ",".join(sorted(os.listdir(path)))
    except OSError:
        return "<unavailable>"


def run(emit=print):
    info = os.uname()
    emit("VFS_UNAME " + str(info.sysname) + ":" + str(info.release)
         + ":" + str(info.machine))
    impl = sys.implementation
    emit("VFS_IMPLEMENTATION " + str(tuple(impl)))
    emit("VFS_SYS_VERSION " + str(sys.version))
    emit("VFS_MPY_ABI " + str(getattr(impl, "mpy", "unavailable")))
    try:
        import vfs
        emit("VFS_HAS_LFS2 " + str(hasattr(vfs, "VfsLfs2")))
        emit("VFS_HAS_FAT " + str(hasattr(vfs, "VfsFat")))
    except ImportError:
        emit("VFS_HAS_LFS2 module-vfs-missing")
        emit("VFS_HAS_FAT module-vfs-missing")
    emit("VFS_GC_FREE " + str(gc.mem_free()))
    emit("VFS_ROOT " + _statvfs_text("/"))
    emit("VFS_SD " + _statvfs_text("/sd"))
    for path in _ANCHOR_PATHS:
        try:
            os.stat(path)
        except OSError:
            emit("VFS_FILE " + path + " missing")
            continue
        emit("VFS_FILE " + path + " sha256=" + _sha256(path))
    emit("VFS_ROOT_LIST " + _listdir_text("/"))
    emit("VFS_SD_LIST " + _listdir_text("/sd"))


if __name__ == "__main__":
    run()
