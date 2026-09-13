from pathlib import Path
from urllib.request import urlopen
import tempfile
import shutil
import subprocess
import sys

BASE_DIR = Path(__file__).parent
LOCAL_FILE = BASE_DIR / "file.txt"

FILE_URL = "https://example.com/files/file.txt"
APP = BASE_DIR / "main.py"


def update_file():
    print("Downloading latest file.txt...")

    try:
        with urlopen(FILE_URL, timeout=15) as response:
            data = response.read()

        # Write to a temporary file first
        with tempfile.NamedTemporaryFile(
            mode="wb",
            delete=False,
            dir=BASE_DIR
        ) as temp:
            temp.write(data)
            temp_path = Path(temp.name)

        # Replace the old file only after download succeeds
        shutil.move(str(temp_path), str(LOCAL_FILE))
        print("file.txt updated.")

    except Exception as error:
        print(f"Update failed: {error}")
        print("Using the existing file.txt.")


update_file()

subprocess.run([sys.executable, str(APP)])
