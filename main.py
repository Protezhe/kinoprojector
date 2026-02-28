#!/usr/bin/env python3
import argparse
import math
import shlex
import subprocess
import time
from pathlib import Path

VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".m4v", ".avi", ".webm"}
OVERLAY_EXTS = {".mp4", ".mov", ".mkv", ".m4v", ".avi", ".webm"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
MASK_EXTS = VIDEO_EXTS | IMAGE_EXTS


def run(cmd: list[str]) -> None:
    print("\n$ " + " ".join(shlex.quote(c) for c in cmd))
    p = subprocess.run(cmd)
    if p.returncode != 0:
        raise RuntimeError(f"Command failed with exit code {p.returncode}")


def ffmpeg_exists() -> None:
    try:
        subprocess.run(["ffmpeg", "-version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    except Exception:
        raise SystemExit(
            "ffmpeg не найден. Установи: \n"
            "  brew install ffmpeg\n"
        )


def encoder_exists(name: str) -> bool:
    p = subprocess.run(
        ["ffmpeg", "-hide_banner", "-encoders"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    return p.returncode == 0 and name in p.stdout


def is_video(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in VIDEO_EXTS


def parse_timecode_to_seconds(value: str) -> float:
    raw = value.strip()
    if not raw:
        raise argparse.ArgumentTypeError("Пустое значение таймкода")

    try:
        if ":" not in raw:
            seconds = float(raw)
        else:
            parts = raw.split(":")
            if len(parts) not in (2, 3):
                raise ValueError

            if len(parts) == 2:
                hours = 0.0
                minutes = float(parts[0])
                secs = float(parts[1])
            else:
                hours = float(parts[0])
                minutes = float(parts[1])
                secs = float(parts[2])

            seconds = hours * 3600 + minutes * 60 + secs
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"Некорректный таймкод '{value}'. Используй секунды или HH:MM:SS(.ms)"
        ) from exc

    if seconds < 0:
        raise argparse.ArgumentTypeError("Таймкод должен быть >= 0")

    return seconds


def format_seconds(seconds: float) -> str:
    return f"{seconds:.6f}".rstrip("0").rstrip(".") or "0"


def pick_file(root: Path, dirname: str, exts: set[str]) -> Path | None:
    src_dir = root / dirname
    if not src_dir.exists():
        return None
    files = sorted([p for p in src_dir.iterdir() if p.is_file() and p.suffix.lower() in exts])
    return files[0] if files else None


def pick_overlay(root: Path) -> Path | None:
    return pick_file(root, "overlays", OVERLAY_EXTS)


def pick_mask(root: Path) -> Path | None:
    return pick_file(root, "mask", MASK_EXTS)


def build_filter_complex(
    args: argparse.Namespace,
    overlay_input_index: int | None,
    mask_input_index: int | None,
) -> tuple[str, str]:
    filters: list[str] = []
    current = "0:v"
    stage = 0

    def next_label() -> str:
        nonlocal stage
        stage += 1
        return f"v{stage}"

    if not args.skip_24fps:
        fps_filter = "fps=24" if args.fast_24fps else "minterpolate=fps=24"
        out = next_label()
        filters.append(f"[{current}]{fps_filter}[{out}]")
        current = out

    if args.sepia:
        sat = 1.0 + (0.25 * args.sepia_intensity)
        rs = 0.03 * args.sepia_warmth
        gs = 0.01 * args.sepia_warmth
        bs = -0.05 * args.sepia_warmth

        orig = f"sep_orig_{stage}"
        tmp = f"sep_tmp_{stage}"
        sep = f"sep_tone_{stage}"
        mix = f"sep_mix_{stage}"
        out = next_label()

        filters.append(f"[{current}]split=2[{orig}][{tmp}]")
        filters.append(
            f"[{tmp}]colorchannelmixer="
            "rr=0.393:rg=0.769:rb=0.189:"
            "gr=0.349:gg=0.686:gb=0.168:"
            f"br=0.272:bg=0.534:bb=0.131[{sep}]"
        )
        filters.append(
            f"[{orig}][{sep}]blend=all_expr='A*(1-{args.sepia_intensity:.6f})+B*{args.sepia_intensity:.6f}'[{mix}]"
        )
        filters.append(
            f"[{mix}]eq=saturation={sat:.6f},colorbalance=rs={rs:.6f}:gs={gs:.6f}:bs={bs:.6f}[{out}]"
        )
        current = out

    if overlay_input_index is not None:
        out = next_label()
        ov = f"ov_{stage}"
        base = f"base_{stage}"
        blend = (
            f"blend=c0_mode={args.dust_mode}:c0_opacity={args.dust_opacity}:"
            "c1_mode=normal:c1_opacity=1:"
            "c2_mode=normal:c2_opacity=1:"
            "shortest=1"
        )
        filters.append(f"[{overlay_input_index}:v][{current}]scale2ref=iw:ih[{ov}][{base}]")
        filters.append(f"[{base}][{ov}]{blend}[{out}]")
        current = out

    if args.gate_weave:
        gate_weave_max_x = args.gate_weave_shift if args.gate_weave_shift is not None else args.gate_weave_max_x
        gate_weave_max_y = args.gate_weave_shift if args.gate_weave_shift is not None else args.gate_weave_max_y
        border_x = int(math.ceil(gate_weave_max_x))
        border_y = int(math.ceil(gate_weave_max_y))

        seed_x = args.gate_weave_seed if args.gate_weave_seed is not None else int(time.time() * 1000)
        seed_y = seed_x + 1
        x_expr = f"'if(eq(n,0),st(0,{seed_x}),0);(random(0)*2-1)*{gate_weave_max_x:.6f}'"
        y_expr = f"'if(eq(n,0),st(1,{seed_y}),0);(random(1)*2-1)*{gate_weave_max_y:.6f}'"

        out = next_label()
        filters.append(
            f"[{current}]"
            f"pad=iw+{border_x * 2}:ih+{border_y * 2}:{border_x}:{border_y}:color=black,"
            f"crop=w=iw-{border_x * 2}:h=ih-{border_y * 2}:x={border_x}+{x_expr}:y={border_y}+{y_expr}"
            f"[{out}]"
        )
        current = out

    if args.simulate_shutter:
        shutter_dark_factor = args.shutter_percent / 100.0 if args.shutter_percent is not None else args.shutter_dark_factor
        out = next_label()
        filters.append(
            f"[{current}]fps=fps=source_fps*2,"
            f"lutyuv=y='val*{shutter_dark_factor:.6f}':enable='eq(mod(n,2),1)'"
            f"[{out}]"
        )
        current = out

    if mask_input_index is not None:
        out = next_label()
        msk = f"mask_{stage}"
        base = f"mask_base_{stage}"
        filters.append(f"[{mask_input_index}:v][{current}]scale2ref=iw:ih[{msk}][{base}]")
        filters.append(f"[{base}][{msk}]overlay=shortest=1[{out}]")
        current = out

    # Нормализуем формат на выходе фильтрграфа, чтобы избежать
    # несовместимостей с энкодерами (особенно h264_videotoolbox).
    out = next_label()
    filters.append(f"[{current}]format=yuv420p[{out}]")
    current = out

    return ";".join(filters), current


def build_step_paths(output_dir: Path, src: Path, args: argparse.Namespace) -> tuple[Path, Path, Path, Path, Path, Path]:
    step01 = output_dir / f"step01_base24_{src.stem}.mp4"
    sepia_i_tag = int(round(args.sepia_intensity * 100))
    sepia_w_tag = int(round(args.sepia_warmth * 100))
    step01_sepia = output_dir / f"step01_sepia_i{sepia_i_tag:03d}_w{sepia_w_tag:03d}_{src.stem}.mp4"
    step02 = output_dir / f"step02_dust_{src.stem}.mp4"
    step03 = output_dir / f"step03_gateweave_{src.stem}.mp4"
    step04 = output_dir / f"step04_shutter_{src.stem}.mp4"
    step05 = output_dir / f"step05_mask_{src.stem}.mp4"
    return step01, step01_sepia, step02, step03, step04, step05


def main() -> None:
    parser = argparse.ArgumentParser(description="Video processing pipeline")
    parser.add_argument("--skip-24fps", action="store_true", help="Skip 24 fps conversion step")
    parser.add_argument("--fast-24fps", action="store_true", help="Use fast fps=24 conversion instead of slow minterpolate")
    parser.add_argument("--simulate-shutter", action="store_true", help="Duplicate frames to 48 fps and darken every second duplicate")
    parser.add_argument("--shutter-dark-factor", type=float, default=0.9, help="Brightness factor for dark duplicate (0..1)")
    parser.add_argument("--shutter-percent", type=float, default=None, help="Dark duplicate brightness in percent (0..100), e.g. 85")
    parser.add_argument("--gate-weave", action="store_true", help="Apply random gate weave jitter")
    parser.add_argument("--gate-weave-shift", type=float, default=None, help="Max jitter shift in pixels for both X/Y")
    parser.add_argument("--gate-weave-max-x", type=float, default=0.6, help="Max horizontal shift in pixels")
    parser.add_argument("--gate-weave-max-y", type=float, default=1.0, help="Max vertical shift in pixels")
    parser.add_argument("--gate-weave-seed", type=int, default=None, help="Random seed for gate weave")
    parser.add_argument("--dust", action="store_true", help="Apply dust/scratches overlay")
    parser.add_argument("--dust-mode", choices=["screen", "multiply"], default="screen", help="Blend mode")
    parser.add_argument("--dust-opacity", type=float, default=0.5, help="Overlay opacity 0..1")
    parser.add_argument("--dust-crf", type=int, default=18, help="CRF for dust step (lower=better, slower)")
    parser.add_argument("--dust-preset", default="medium", help="x264 preset for dust step (faster=lower quality)")
    parser.add_argument("--sepia", action="store_true", help="Apply sepia color grading")
    parser.add_argument("--sepia-intensity", type=float, default=0.45, help="Sepia intensity 0..1 (less keeps more source color)")
    parser.add_argument("--sepia-warmth", type=float, default=0.75, help="Extra warm tint 0..1")
    parser.add_argument("--mask", action="store_true", help="Apply final mask from mask/ folder")
    parser.add_argument("--fast-m1", action="store_true", help="Use Apple VideoToolbox (h264_videotoolbox) for faster encoding on Apple Silicon")
    parser.add_argument("--fast-m1-bitrate", default="12M", help="Target bitrate for --fast-m1 mode, e.g. 8M, 12M, 20M")
    parser.add_argument("--final-only", action="store_true", help="Delete intermediate step files and keep only final output")
    parser.add_argument(
        "--in-sec",
        "--start-sec",
        dest="in_sec",
        type=parse_timecode_to_seconds,
        default=0.0,
        help="Start processing from this time (seconds or HH:MM:SS(.ms)); default: 0",
    )
    parser.add_argument(
        "--out-sec",
        "--end-sec",
        dest="out_sec",
        type=parse_timecode_to_seconds,
        default=20.0,
        help="Stop processing at this time (seconds or HH:MM:SS(.ms)); default: 20",
    )
    args = parser.parse_args()

    root = Path(__file__).resolve().parent
    input_dir = root / "input"
    output_dir = root / "output"
    output_dir.mkdir(parents=True, exist_ok=True)

    if not input_dir.exists():
        raise SystemExit(f"Нет папки: {input_dir}. Создай её и положи туда видео.")

    ffmpeg_exists()
    if args.fast_m1 and not encoder_exists("h264_videotoolbox"):
        raise SystemExit(
            "Энкодер h264_videotoolbox не найден в ffmpeg. "
            "Установи ffmpeg с поддержкой VideoToolbox или запусти без --fast-m1."
        )

    files = sorted([p for p in input_dir.iterdir() if is_video(p)])
    if not files:
        raise SystemExit(f"В {input_dir} нет видеофайлов. Поддерживаемые расширения: {sorted(VIDEO_EXTS)}")

    print(f"Найдено файлов: {len(files)}")

    if args.gate_weave_shift is not None and args.gate_weave_shift < 0.0:
        raise SystemExit("--gate-weave-shift должен быть >= 0")
    if args.gate_weave_max_x < 0.0 or args.gate_weave_max_y < 0.0:
        raise SystemExit("--gate-weave-max-x и --gate-weave-max-y должны быть >= 0")
    if args.shutter_percent is not None and not (0.0 <= args.shutter_percent <= 100.0):
        raise SystemExit("--shutter-percent должен быть в диапазоне 0..100")
    if not (0.0 <= args.dust_opacity <= 1.0):
        raise SystemExit("--dust-opacity должен быть в диапазоне 0..1")
    if not (0.0 <= args.sepia_intensity <= 1.0):
        raise SystemExit("--sepia-intensity должен быть в диапазоне 0..1")
    if not (0.0 <= args.sepia_warmth <= 1.0):
        raise SystemExit("--sepia-warmth должен быть в диапазоне 0..1")
    if args.out_sec <= args.in_sec:
        raise SystemExit("--out-sec должен быть больше --in-sec")

    shutter_dark_factor = args.shutter_percent / 100.0 if args.shutter_percent is not None else args.shutter_dark_factor
    if args.simulate_shutter and not (0.0 <= shutter_dark_factor <= 1.0):
        raise SystemExit("--shutter-dark-factor должен быть в диапазоне 0..1")

    print(f"Диапазон обработки: {format_seconds(args.in_sec)}s -> {format_seconds(args.out_sec)}s")

    for src in files:
        step01, step01_sepia, step02, step03, step04, step05 = build_step_paths(output_dir, src, args)
        target = src

        if not args.skip_24fps:
            target = step01
        if args.sepia:
            target = step01_sepia
        if args.dust:
            target = step02
        if args.gate_weave:
            target = step03
        if args.simulate_shutter:
            target = step04
        if args.mask:
            target = step05

        if target == src:
            print(f"⏭ Пропускаю (нет активных шагов): {src.name}")
            continue

        if target.exists():
            print(f"⏭ Пропускаю (уже есть): {target.name}")
        else:
            overlay = pick_overlay(root) if args.dust else None
            if args.dust and overlay is None:
                raise SystemExit("Нет overlay-файлов в папке overlays/")

            mask = pick_mask(root) if args.mask else None
            if args.mask and mask is None:
                raise SystemExit("Нет mask-файлов в папке mask/")

            overlay_input_index = None
            mask_input_index = None
            next_input_index = 1
            if overlay is not None:
                overlay_input_index = next_input_index
                next_input_index += 1
            if mask is not None:
                mask_input_index = next_input_index

            filter_complex, out_label = build_filter_complex(args, overlay_input_index, mask_input_index)

            cmd = [
                "ffmpeg",
                "-y",
                "-ss", format_seconds(args.in_sec),
                "-to", format_seconds(args.out_sec),
                "-i", str(src),
            ]
            if overlay is not None:
                cmd += [
                    "-stream_loop", "-1",
                    "-i", str(overlay),
                ]
            if mask is not None:
                if mask.suffix.lower() in IMAGE_EXTS:
                    cmd += [
                        "-loop", "1",
                        "-i", str(mask),
                    ]
                else:
                    cmd += [
                        "-stream_loop", "-1",
                        "-i", str(mask),
                    ]

            cmd += [
                "-filter_complex", filter_complex,
                "-map", f"[{out_label}]",
                "-map", "0:a?",
            ]
            crf = str(args.dust_crf) if args.dust else "18"
            preset = args.dust_preset if args.dust else "medium"
            sw_video_args = [
                "-c:v", "libx264",
                "-crf", crf,
                "-preset", preset,
            ]
            fast_video_args = [
                "-c:v", "h264_videotoolbox",
                "-b:v", args.fast_m1_bitrate,
                "-allow_sw", "1",
            ]
            cmd += [
                "-pix_fmt", "yuv420p",
                "-c:a", "aac",
                "-b:a", "192k",
            ]
            if overlay is not None or mask is not None:
                cmd += ["-shortest"]
            tail = [str(target)]

            if args.fast_m1:
                try:
                    run(cmd + fast_video_args + tail)
                except RuntimeError:
                    print("⚠️ VideoToolbox не смог стартовать, повторяю через libx264.")
                    run(cmd + sw_video_args + tail)
            else:
                run(cmd + sw_video_args + tail)

        if args.final_only:
            for step in (step01, step01_sepia, step02, step03, step04, step05):
                if step == target or not step.exists():
                    continue
                step.unlink()
                print(f"🧹 Удален промежуточный файл: {step.name}")

    print("\n✅ Готово. Результаты в папке output/")


if __name__ == "__main__":
    main()
