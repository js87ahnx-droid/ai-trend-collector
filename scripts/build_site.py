"""GitHub Pages 산출물(_site/) 만들기: web/* + data/*.json."""
import shutil
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
out = Path(sys.argv[1]) if len(sys.argv) > 1 else root / "_site"
data = Path(sys.argv[2]) if len(sys.argv) > 2 else root / "data"
if out.exists():
    shutil.rmtree(out)
shutil.copytree(root / "web", out)
(out / "data").mkdir()
for name in ("items.json", "status.json"):
    shutil.copy2(data / name, out / "data" / name)
(out / ".nojekyll").touch()
print(f"built {out}")
