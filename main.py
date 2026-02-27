#!/usr/bin/env python3
import argparse
import random
import subprocess
import tempfile
from pathlib import Path
import shlex
import time

VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".m4v", ".avi", ".webm"}
OVERLAY_EXTS = {".mp4", ".mov", ".mkv", ".m4v", ".avi", ".webm"}

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

def pick_overlay(root: Path) -> Path | None:
    overlay_dir = root / "overlays"
    if not overlay_dir.exists():
        return None
    files = sorted([p for p in overlay_dir.iterdir() if p.is_file() and p.suffix.lower() in OVERLAY_EXTS])
    return files[0] if files else None

def apply_gate_weave(src: Path, dst: Path, max_x: float, max_y: float, seed: int | None) -> None:
    try:
        import cv2  # type: ignore
        import numpy as np  # type: ignore
    except Exception:
        raise SystemExit(
            "OpenCV (cv2) не найден. Установи: \n"
            "  pip install opencv-python\n"
        )

    cap = cv2.VideoCapture(str(src))
    if not cap.isOpened():
        raise SystemExit(f"Не удалось открыть видео: {src}")

    fps = cap.get(cv2.CAP_PROP_FPS) or 24.0
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(str(dst), fourcc, fps, (width, height))

    rng = random.Random(seed)
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        dx = rng.uniform(-max_x, max_x)
        dy = rng.uniform(-max_y, max_y)
        M = np.array([[1, 0, dx], [0, 1, dy]], dtype=np.float32)
        shifted = cv2.warpAffine(frame, M, (width, height), borderMode=cv2.BORDER_REPLICATE)
        writer.write(shifted)

    cap.release()
    writer.release()


def apply_shutter_simulation(src: Path, dst: Path, dark_factor: float) -> None:
    try:
        import cv2  # type: ignore
        import numpy as np  # type: ignore
    except Exception:
        raise SystemExit(
            "OpenCV (cv2) не найден. Установи: \n"
            "  pip install opencv-python\n"
        )

    cap = cv2.VideoCapture(str(src))
    if not cap.isOpened():
        raise SystemExit(f"Не удалось открыть видео: {src}")

    in_fps = cap.get(cv2.CAP_PROP_FPS) or 24.0
    total_in_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    out_fps = in_fps * 2.0
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(str(dst), fourcc, out_fps, (width, height))

    print(f"▶ Обтюратор: {src.name} -> {dst.name}")
    if total_in_frames > 0:
        print(f"   Вход: {total_in_frames} кадров @ {in_fps:.3f} fps, выход: {total_in_frames * 2} кадров @ {out_fps:.3f} fps")
    else:
        print(f"   Вход: неизвестное число кадров @ {in_fps:.3f} fps, выход: 2x кадров @ {out_fps:.3f} fps")

    start_ts = time.time()
    in_frames_done = 0
    next_report = 120
    while True:
        ok, frame = cap.read()
        if not ok:
            break

        in_frames_done += 1
        writer.write(frame)
        dark = np.clip(frame.astype(np.float32) * dark_factor, 0, 255).astype(np.uint8)
        writer.write(dark)

        if in_frames_done >= next_report:
            elapsed = time.time() - start_ts
            if total_in_frames > 0:
                pct = (in_frames_done / total_in_frames) * 100.0
                print(
                    f"   ... {in_frames_done}/{total_in_frames} кадров "
                    f"({pct:.1f}%), ~{in_frames_done * 2} выходных, {elapsed:.1f} c"
                )
            else:
                print(f"   ... {in_frames_done} входных, ~{in_frames_done * 2} выходных, {elapsed:.1f} c")
            next_report += 120

    cap.release()
    writer.release()
    total_elapsed = time.time() - start_ts
    print(f"✔ Обтюратор готов: {in_frames_done} -> {in_frames_done * 2} кадров за {total_elapsed:.1f} c")


def apply_sepia(
    src: Path,
    dst: Path,
    intensity: float,
    warmth: float,
    fast_m1: bool,
    fast_m1_bitrate: str,
) -> None:
    sat = 1.0 + (0.25 * intensity)
    rs = 0.03 * warmth
    gs = 0.01 * warmth
    bs = -0.05 * warmth
    sepia_filter = (
        "[0:v]split=2[orig][tmp];"
        "[tmp]colorchannelmixer="
        "rr=0.393:rg=0.769:rb=0.189:"
        "gr=0.349:gg=0.686:gb=0.168:"
        "br=0.272:bg=0.534:bb=0.131[sep];"
        f"[orig][sep]blend=all_expr='A*(1-{intensity:.6f})+B*{intensity:.6f}'[mix];"
        f"[mix]eq=saturation={sat:.6f},"
        f"colorbalance=rs={rs:.6f}:gs={gs:.6f}:bs={bs:.6f}[out]"
    )
    cmd = [
        "ffmpeg",
        "-y",
        "-i", str(src),
        "-filter_complex", sepia_filter,
        "-map", "[out]",
        "-map", "0:a?",
    ]
    if fast_m1:
        cmd += [
            "-c:v", "h264_videotoolbox",
            "-b:v", fast_m1_bitrate,
        ]
    else:
        cmd += [
            "-c:v", "libx264",
            "-crf", "18",
            "-preset", "medium",
        ]
    cmd += [
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "192k",
        str(dst),
    ]
    run(cmd)


def main():
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
    parser.add_argument("--fast-m1", action="store_true", help="Use Apple VideoToolbox (h264_videotoolbox) for faster encoding on Apple Silicon")
    parser.add_argument("--fast-m1-bitrate", default="12M", help="Target bitrate for --fast-m1 mode, e.g. 8M, 12M, 20M")
    parser.add_argument("--final-only", action="store_true", help="Delete intermediate step files and keep only final output")
    args = parser.parse_args()

    # Скрипт лежит в корне проекта.
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
    if args.shutter_percent is not None and not (0.0 <= args.shutter_percent <= 100.0):
        raise SystemExit("--shutter-percent должен быть в диапазоне 0..100")
    if not (0.0 <= args.sepia_intensity <= 1.0):
        raise SystemExit("--sepia-intensity должен быть в диапазоне 0..1")
    if not (0.0 <= args.sepia_warmth <= 1.0):
        raise SystemExit("--sepia-warmth должен быть в диапазоне 0..1")

    for src in files:
        current = src
        step01 = output_dir / f"step01_base24_{src.stem}.mp4"
        sepia_i_tag = int(round(args.sepia_intensity * 100))
        sepia_w_tag = int(round(args.sepia_warmth * 100))
        step01_sepia = output_dir / f"step01_sepia_i{sepia_i_tag:03d}_w{sepia_w_tag:03d}_{src.stem}.mp4"
        step02 = output_dir / f"step02_dust_{src.stem}.mp4"
        step03 = output_dir / f"step03_gateweave_{src.stem}.mp4"
        step04 = output_dir / f"step04_shutter_{src.stem}.mp4"

        if args.skip_24fps:
            current = src
        else:
            if step01.exists():
                print(f"⏭ Пропускаю (уже есть): {step01.name}")
                current = step01
            else:
                # Шаг 1: привести к 24 fps (интерполяция до 24)
                fps_filter = "fps=24" if args.fast_24fps else "minterpolate=fps=24"
                cmd = [
                    "ffmpeg",
                    "-y",
                    "-i", str(src),
                    "-vf", fps_filter,
                    "-r", "24",
                ]
                if args.fast_m1:
                    cmd += [
                        "-c:v", "h264_videotoolbox",
                        "-b:v", args.fast_m1_bitrate,
                    ]
                else:
                    cmd += [
                        "-c:v", "libx264",
                        "-crf", "18",
                        "-preset", "medium",
                    ]
                cmd += [
                    "-pix_fmt", "yuv420p",
                    "-c:a", "aac",
                    "-b:a", "192k",
                    str(step01),
                ]
                run(cmd)
                current = step01

        # Шаг 1b: сепия (цветокор)
        if args.sepia:
            if step01_sepia.exists():
                print(f"⏭ Пропускаю (уже есть): {step01_sepia.name}")
                current = step01_sepia
            else:
                apply_sepia(
                    current,
                    step01_sepia,
                    args.sepia_intensity,
                    args.sepia_warmth,
                    args.fast_m1,
                    args.fast_m1_bitrate,
                )
                current = step01_sepia

        # Шаг 2: пыль и царапины (overlay)
        if args.dust:
            overlay = pick_overlay(root)
            if overlay is None:
                raise SystemExit("Нет overlay-файлов в папке overlays/")

            if step02.exists():
                print(f"⏭ Пропускаю (уже есть): {step02.name}")
                current = step02
            else:
                # Зацикливаем overlay до длины основного видео
                # Масштабируем overlay под базовое видео и применяем blend
                # Накладываем эффект только на яркость (Y), чтобы не трогать
                # цветовые компоненты U/V и не получать цветовой сдвиг.
                # shortest=1 останавливает blend, когда заканчивается
                # более короткий вход.
                blend = (
                    f"blend=c0_mode={args.dust_mode}:c0_opacity={args.dust_opacity}:"
                    "c1_mode=normal:c1_opacity=1:"
                    "c2_mode=normal:c2_opacity=1:"
                    "shortest=1"
                )
                filter_complex = (
                    "[1:v][0:v]scale2ref=iw:ih[ov][base];"
                    "[base][ov]" + blend + "[out]"
                )
                cmd = [
                    "ffmpeg",
                    "-y",
                    "-i", str(current),
                    "-stream_loop", "-1",
                    "-i", str(overlay),
                    "-filter_complex", filter_complex,
                    "-map", "[out]",
                    "-map", "0:a?",
                ]
                if args.fast_m1:
                    cmd += [
                        "-c:v", "h264_videotoolbox",
                        "-b:v", args.fast_m1_bitrate,
                    ]
                else:
                    cmd += [
                        "-c:v", "libx264",
                        "-crf", str(args.dust_crf),
                        "-preset", args.dust_preset,
                    ]
                cmd += [
                    "-pix_fmt", "yuv420p",
                    "-c:a", "aac",
                    "-b:a", "192k",
                    "-shortest",
                    str(step02),
                ]
                run(cmd)
                current = step02

        # Шаг 3: gate weave (случайное дрожание кадров)
        if args.gate_weave:
            gate_weave_max_x = args.gate_weave_shift if args.gate_weave_shift is not None else args.gate_weave_max_x
            gate_weave_max_y = args.gate_weave_shift if args.gate_weave_shift is not None else args.gate_weave_max_y
            if step03.exists():
                print(f"⏭ Пропускаю (уже есть): {step03.name}")
                current = step03
            else:
                apply_gate_weave(
                    current,
                    step03,
                    gate_weave_max_x,
                    gate_weave_max_y,
                    args.gate_weave_seed,
                )
                current = step03

        # Шаг 4: имитация обтюратора (A100, A85, B100, B85 ...)
        if args.simulate_shutter:
            shutter_dark_factor = (
                args.shutter_percent / 100.0 if args.shutter_percent is not None else args.shutter_dark_factor
            )
            if not (0.0 <= shutter_dark_factor <= 1.0):
                raise SystemExit("--shutter-dark-factor должен быть в диапазоне 0..1")
            if step04.exists():
                print(f"⏭ Пропускаю (уже есть): {step04.name}")
                current = step04
            else:
                apply_shutter_simulation(
                    current,
                    step04,
                    shutter_dark_factor,
                )
                current = step04

        if args.final_only and current != src:
            for step in (step01, step01_sepia, step02, step03, step04):
                if step == current or not step.exists():
                    continue
                step.unlink()
                print(f"🧹 Удален промежуточный файл: {step.name}")

    print("\n✅ Готово. Результаты в папке output/")

if __name__ == "__main__":
    main()
