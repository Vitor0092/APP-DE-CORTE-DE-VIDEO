"""Numbered thumbnails of footage, tiled into contact sheets, so Claude can "see" the scenes it can use."""
import subprocess
from pathlib import Path

FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
TW, TH = 320, 180
COLS, ROWS = 6, 4


def _thumb(path: str, t: float, out: Path, label: str = "") -> bool:
    vf = f"scale={TW}:{TH}:force_original_aspect_ratio=decrease,pad={TW}:{TH}:(ow-iw)/2:(oh-ih)/2"
    if label:
        vf += (f",drawtext=fontfile={FONT}:text='{label}':x=8:y=8:fontsize=34:fontcolor=white"
               ":box=1:boxcolor=black@0.75:boxborderw=6")
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-ss", f"{t:.2f}", "-i", path, "-frames:v", "1",
                    "-vf", vf, "-pix_fmt", "yuvj420p", "-q:v", "4", str(out)], capture_output=True)
    return out.exists() and out.stat().st_size > 0


def _tile(folder: Path, count: int, cols: int, rows: int):
    """Tiles t_0001.jpg.. into sheet_01.jpg..; blank tiles fill the last sheet (ffmpeg drops a partial one)."""
    blank = folder / "blank.jpg"
    for i in range(count + 1, -(-count // (cols * rows)) * cols * rows + 1):
        if not blank.exists():
            subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c=0x111111:s={TW}x{TH}",
                            "-frames:v", "1", "-pix_fmt", "yuvj420p", str(blank)], capture_output=True)
        (folder / f"t_{i:04d}.jpg").write_bytes(blank.read_bytes())
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-framerate", "1", "-start_number", "1", "-i", str(folder / "t_%04d.jpg"),
                    "-vf", f"tile={cols}x{rows}:padding=4:color=0x111111", "-q:v", "3", str(folder / "sheet_%02d.jpg")],
                   capture_output=True)


def candidates(footage: list[dict], out_dir: str, max_shots: int = 96) -> tuple[list[dict], list[str]]:
    """footage: [{name, path, duration}]. Picks evenly spaced moments of every video (more for longer ones)
    and returns ([{id, name, path, t, duration}], [contact sheet paths]); ids are the numbers drawn on them."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    usable = [f for f in footage if (f.get("duration") or 0) >= 2]
    total = sum(f["duration"] for f in usable) or 1
    shots = []
    for f in usable:
        d = f["duration"]
        n = max(2, min(int(d // 3), round(max_shots * d / total)))
        for k in range(n):
            t = 0.5 + (k + 0.5) * (d - 2.5) / n
            sid = len(shots) + 1  # only advances on success, so the files stay numbered without gaps
            if _thumb(f["path"], t, out / f"t_{sid:04d}.jpg", str(sid)):
                shots.append({"id": sid, "name": f["name"], "path": f["path"], "t": round(t, 2), "duration": d})
    if not shots:
        raise RuntimeError("Não consegui tirar imagens dos vídeos para escolher as cenas.")
    _tile(out, len(shots), COLS, ROWS)
    return shots, [str(p) for p in sorted(out.glob("sheet_*.jpg"))]


def style_sheet(path: str, duration: float, out_path: str, n: int = 12) -> str:
    """12 frames spread over a video, to show its look (used when its own scenes are not reused)."""
    out = Path(out_path)
    tmp = out.parent / (out.stem + "_f")
    tmp.mkdir(parents=True, exist_ok=True)
    i = 0
    for k in range(n):
        if _thumb(path, 0.5 + (k + 0.5) * max(duration - 1, 1) / n, tmp / f"t_{i + 1:04d}.jpg"):
            i += 1
    if not i:
        return ""
    _tile(tmp, i, 4, 3)
    first = tmp / "sheet_01.jpg"
    if not first.exists():
        return ""
    first.replace(out)
    return str(out)
