"""Nén output/ thành zip nộp bài.

Chỉ đưa vào đúng 50 file EC_001.json..EC_050.json. Không source, không .env,
không .gitkeep, không file audit (README mục 8 và mục 9.2).

    py -3 tools/pack_output.py            # tạo output.zip rồi liệt kê nội dung
    py -3 tools/pack_output.py --list     # chỉ liệt kê zip đã có

Từ chối nén nếu output/ không đủ 50 file hoặc có file lạ.
"""

from __future__ import annotations

import argparse
import json
import sys
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
INPUT_DIR = REPO_ROOT / "input"
OUTPUT_DIR = REPO_ROOT / "output"
ZIP_PATH = REPO_ROOT / "output.zip"

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def expected_names() -> list[str]:
    names = sorted(p.name for p in INPUT_DIR.glob("EC_*.json"))
    if not names:
        raise SystemExit("Không tìm thấy input/EC_*.json")
    return names


def list_zip(path: Path) -> None:
    with zipfile.ZipFile(path) as zf:
        infos = sorted(zf.infolist(), key=lambda i: i.filename)
        print(f"{path.name} — {len(infos)} entry, {path.stat().st_size:,} bytes\n")
        print(f"{'#':>3}  {'tên file trong zip':<20} {'bytes':>8}")
        print("-" * 36)
        for idx, info in enumerate(infos, 1):
            print(f"{idx:>3}  {info.filename:<20} {info.file_size:>8,}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Nén output/ thành zip nộp bài")
    parser.add_argument("--list", action="store_true", help="chỉ liệt kê zip đã có")
    args = parser.parse_args(argv)

    if args.list:
        if not ZIP_PATH.exists():
            print(f"Chưa có {ZIP_PATH.name}", file=sys.stderr)
            return 1
        list_zip(ZIP_PATH)
        return 0

    wanted = expected_names()
    present = {p.name for p in OUTPUT_DIR.iterdir() if p.is_file()}

    missing = sorted(set(wanted) - present)
    if missing:
        print(f"Thiếu {len(missing)} file trong output/: {', '.join(missing[:5])}", file=sys.stderr)
        return 1

    # File ngoài danh sách (kể cả .gitkeep) bị loại khỏi zip, chỉ báo để biết.
    skipped = sorted(present - set(wanted))
    for name in wanted:
        try:
            json.loads((OUTPUT_DIR / name).read_text(encoding="utf-8"))
        except json.JSONDecodeError as err:
            print(f"{name} không phải JSON hợp lệ: {err}", file=sys.stderr)
            return 1

    ZIP_PATH.unlink(missing_ok=True)
    with zipfile.ZipFile(ZIP_PATH, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name in wanted:
            zf.write(OUTPUT_DIR / name, arcname=name)

    if skipped:
        print(f"Đã loại khỏi zip: {', '.join(skipped)}\n")
    list_zip(ZIP_PATH)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
