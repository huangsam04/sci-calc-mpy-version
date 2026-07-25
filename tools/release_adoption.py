# Trusted first-takeover adoption for devices running the 1.3.0 baseline.
# Adoption proves the device matches the pinned 1.3.0 trusted-base hashes
# (2026-07-25 COM6 probe evidence), then installs the new boot anchor in
# a power-cut-safe order: support modules, boot.py, and finally main.py.
import hashlib


BASELINE_130_HASHES = {
    "boot.py": "e918c98fdf02faf11d6af325eec8c42399a070281a9988a9672b9774665b5af4",
    "main.py": "44fe63c3a3f98c1a8f2779addc11df4c6a31dd54d422e6828bf2e211e5d61ab0",
    "sdcard.py": "d2d4b98ed0d466c49a6c121c90a313d782defb59483a931b1ce1aae7904b60ea",
    "recovery.py": "46feef5addb3039b94b765d4b01291c8e5a442c0adda6b53f48ee5992d180cd1",
    "display/mono_palette.py": "5f9804a6dc8be3451e5e9ab6d3a35ef0a29889582b079a8cd5b796ba73f219a7",
    "display/ssd1322.py": "4065905c2d3c6f77709606c4c09a15ef20964022d7756f55fa6e948cfdfb86e6",
}

_MKDIR_SYS_CODE = (
    "import os\n"
    "try: os.mkdir('/sys')\n"
    "except OSError: pass")


class AdoptionError(Exception):
    """The device is not in a state this flow is allowed to touch."""


def _device_path(relative_path):
    return "/" + relative_path


def _write_verified(device, path, payload):
    temp = path + ".new"
    device.write_file(temp, payload)
    if device.read_file(temp) != payload:
        raise AdoptionError("anchor write read-back mismatch: " + path)
    device.rename(temp, path)
    if device.read_file(path) != payload:
        raise AdoptionError("anchor rename verification failed: " + path)


def adopt_device(device, plan, baseline_hashes=None):
    """Install the plan's bootstrap anchor onto a trusted 1.3.0 device."""
    if baseline_hashes is None:
        baseline_hashes = BASELINE_130_HASHES
    bootstrap = tuple(
        asset for asset in plan.assets if asset.role == "bootstrap_fixed")
    if not bootstrap:
        raise AdoptionError("release plan carries no bootstrap anchor")

    current = {
        asset.key: device.read_file(_device_path(asset.relative_path))
        for asset in bootstrap
    }
    if all(current[asset.key] == asset.payload for asset in bootstrap):
        device.exec(_MKDIR_SYS_CODE)
        return False

    for path, expected in baseline_hashes.items():
        data = device.read_file(_device_path(path))
        if data is None:
            raise AdoptionError("baseline file is missing: " + path)
        if hashlib.sha256(data).hexdigest() != expected:
            raise AdoptionError("baseline file hash mismatch: " + path)

    for asset in bootstrap:
        existing = current[asset.key]
        if (existing is not None
                and existing != asset.payload
                and asset.relative_path not in baseline_hashes):
            raise AdoptionError(
                "foreign boot module conflict: " + asset.relative_path)

    def _sort_key(asset):
        if asset.relative_path == "main.py":
            return 2
        if asset.relative_path == "boot.py":
            return 1
        return 0

    for asset in sorted(bootstrap, key=_sort_key):
        if current[asset.key] != asset.payload:
            _write_verified(
                device, _device_path(asset.relative_path), asset.payload)

    device.exec(_MKDIR_SYS_CODE)
    for asset in bootstrap:
        if device.read_file(_device_path(asset.relative_path)) != (
                asset.payload):
            raise AdoptionError(
                "anchor verification failed: " + asset.relative_path)
    return True
