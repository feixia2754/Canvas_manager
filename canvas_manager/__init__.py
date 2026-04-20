# canvas_manager package
from importlib.metadata import version, PackageNotFoundError

try:
    __version__ = version("canvas-manager")
except PackageNotFoundError:
    __version__ = "unknown"
