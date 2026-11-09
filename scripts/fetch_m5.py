"""Download and checksum the pinned public M5 mirror without extracting it."""

import argparse
import hashlib
import shutil
from pathlib import Path
from urllib.request import urlopen

REVISION = "72b8e7fd3b565b3c538adcb1d1a05117d8562d7e"
URL = f"https://raw.githubusercontent.com/Nixtla/m5-forecasts/{REVISION}/datasets/m5.zip"
SHA256 = "cc704ba15d6802f8262e6ec7d4c6041e4ad6366a94365e8c84f721e450eed774"


def verify(path):
    with path.open("rb") as source:
        actual = hashlib.file_digest(source, "sha256").hexdigest()
    if actual != SHA256:
        raise ValueError(f"M5 checksum mismatch: {actual}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("artifacts/m5.zip"))
    output = parser.parse_args().output
    output.parent.mkdir(parents=True, exist_ok=True)
    if not output.exists():
        temporary = output.with_suffix(".download")
        try:
            with urlopen(URL, timeout=60) as response, temporary.open("wb") as target:
                shutil.copyfileobj(response, target)
            verify(temporary)
            temporary.replace(output)
        finally:
            temporary.unlink(missing_ok=True)
    verify(output)
    print(output.resolve())


if __name__ == "__main__":
    main()
