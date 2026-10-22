"""图片预处理：统一比例、压缩到微信素材限制内。

微信硬限制：单张素材 ≤ 2MB（超了报 45001/40006）。这里默认压到 1.8MB 留余量。
"""

from __future__ import annotations

from pathlib import Path

try:
    from PIL import Image, ImageOps
except ImportError:  # pragma: no cover
    Image = None  # type: ignore

RESAMPLE = getattr(getattr(Image, "Resampling", Image), "LANCZOS", 1) if Image else 1

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}

# 常用比例 → 目标像素（宽, 高），按小绿书竖版习惯
RATIO_SIZES = {
    "3:4": (1080, 1440),
    "1:1": (1080, 1080),
    "4:3": (1440, 1080),
    "16:9": (1440, 810),
}


class ImageError(Exception):
    pass


def _require_pillow() -> None:
    if Image is None:
        raise ImageError("缺少 Pillow，先跑：pip install Pillow")


def collect_images(folder: Path) -> list[Path]:
    """按文件名排序收集图片（01.jpg、02.jpg … 的数字顺序即发布顺序）。"""
    if not folder.exists():
        raise ImageError(f"图片目录不存在：{folder}")
    files = [p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXTS]
    if not files:
        raise ImageError(f"目录里没有图片：{folder}（支持 {'/'.join(sorted(IMAGE_EXTS))}）")
    return sorted(files, key=lambda p: p.name.lower())


def dimensions(path: Path) -> tuple[int, int]:
    _require_pillow()
    with Image.open(path) as im:
        return im.size


def normalize_ratio(path: Path, ratio: str, out_path: Path | None = None) -> Path:
    """居中裁成指定比例并缩放到标准尺寸（只裁不改形，避免拉伸变形）。"""
    _require_pillow()
    if ratio not in RATIO_SIZES:
        raise ImageError(f"不支持的比例 {ratio}，可选：{'、'.join(RATIO_SIZES)}")
    target_w, target_h = RATIO_SIZES[ratio]
    target_ratio = target_w / target_h

    with Image.open(path) as im:
        im = ImageOps.exif_transpose(im)
        im = im.convert("RGB")
        w, h = im.size
        src_ratio = w / h
        if src_ratio > target_ratio:
            new_w = int(h * target_ratio)
            left = (w - new_w) // 2
            box = (left, 0, left + new_w, h)
        elif src_ratio < target_ratio:
            new_h = int(w / target_ratio)
            top = (h - new_h) // 2
            box = (0, top, w, top + new_h)
        else:
            box = (0, 0, w, h)
        im = im.crop(box)
        im = im.resize((target_w, target_h), RESAMPLE)

        dest = out_path or path
        im.save(dest, "JPEG", quality=90, optimize=True, progressive=True)
    return dest


def compress_to_limit(path: Path, max_bytes: int = 1_800_000, out_path: Path | None = None) -> Path:
    """逐级降 quality / 缩尺寸，压到 max_bytes 以内。已达标则原样返回。"""
    _require_pillow()
    if path.stat().st_size <= max_bytes and path.suffix.lower() in (".jpg", ".jpeg"):
        return path

    dest = out_path or path.with_suffix(".jpg")
    with Image.open(path) as im:
        im = ImageOps.exif_transpose(im).convert("RGB")
        w, h = im.size
        scale = 1.0
        for _ in range(6):
            frame = im if scale == 1.0 else im.resize((int(w * scale), int(h * scale)), RESAMPLE)
            for quality in (90, 82, 74, 66, 58):
                frame.save(dest, "JPEG", quality=quality, optimize=True, progressive=True)
                if dest.stat().st_size <= max_bytes:
                    return dest
            scale *= 0.82
    raise ImageError(
        f"压缩失败：{path.name} 压到最后一档仍超过 {max_bytes / 1024:.0f} KB，建议换尺寸小一点的图"
    )


def prepare_batch(
    folder: Path,
    ratio: str | None = None,
    max_bytes: int = 1_800_000,
    out_dir: Path | None = None,
) -> list[dict]:
    """批量处理：可选统一比例 → 压缩。

    out_dir 给定时产物写到独立目录（推荐），原图不动、可反复重跑；
    不给则就地覆盖（会产生同名 .jpg 副本，不推荐）。
    """
    sources = collect_images(folder)
    if out_dir is not None:
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        for stale in out_dir.glob("*.jpg"):
            stale.unlink()

    results = []
    for idx, src in enumerate(sources, start=1):
        target = (out_dir / f"{idx:02d}.jpg") if out_dir is not None else src.with_suffix(".jpg")
        if ratio:
            normalize_ratio(src, ratio, target)
        else:
            target.write_bytes(src.read_bytes())
        if target.stat().st_size > max_bytes:
            compress_to_limit(target, max_bytes=max_bytes, out_path=target)
        w, h = dimensions(target)
        results.append(
            {
                "path": str(target),
                "name": target.name,
                "source": src.name,
                "width": w,
                "height": h,
                "bytes": target.stat().st_size,
            }
        )
    return results
