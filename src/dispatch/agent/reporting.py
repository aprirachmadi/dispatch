"""Private, local JSON run artifacts; atomic replacement and no accidental overwrite."""

import json
import os
import tempfile
from pathlib import Path


class RunFile:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(descriptor)

    def write(self, payload: dict) -> None:
        """An existing report belongs to this run; temp failure never truncates it."""
        descriptor, temporary = tempfile.mkstemp(dir=self.path.parent, prefix=".dispatch-run-")
        try:
            with os.fdopen(descriptor, "w") as stream:
                json.dump(payload, stream, indent=2, allow_nan=False)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            try:
                Path(temporary).unlink(missing_ok=True)
            except OSError:
                pass
