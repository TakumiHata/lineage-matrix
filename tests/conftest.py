import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))


@pytest.fixture
def workdir(tmp_path, monkeypatch):
    """input/output を相対パスで読み書きするモジュール群のためにカレントディレクトリを
    一時ディレクトリへ切り替える（config.py の INPUT_DIR/OUTPUT_DIR は相対Pathで、
    実際のファイルアクセス時にカレントディレクトリを基準に解決されるため）。
    """
    monkeypatch.chdir(tmp_path)
    return tmp_path
