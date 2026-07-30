import asyncio
import json
import logging
import os
import re

logger = logging.getLogger(__name__)

# Compile Regex globally for performance
TIME_REGEX = re.compile(r"time=(\d+:\d+:\d+\.\d+)")


async def get_video_info(file_path):
    # Try with strict flags first
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-print_format",
        "json=c=1",
        "-show_format",
        "-show_streams",
        file_path,
    ]
    process = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await process.communicate()

    if process.returncode != 0:
        logger.warning(
            f"Strict ffprobe failed for {file_path}, trying lenient fallback..."
        )
        # Fallback: remove -v error to see what's happening, or try simpler probe
        cmd_fallback = [
            "ffprobe",
            "-v",
            "quiet",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            file_path,
        ]
        process = await asyncio.create_subprocess_exec(
            *cmd_fallback,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()

    if process.returncode != 0:
        logger.error(
            f"Lenient ffprobe also failed for {file_path}: {stderr.decode('utf-8', errors='ignore')}"
        )
        return None

    try:
        return json.loads(stdout)
    except Exception as e:
        logger.error(f"Failed to parse ffprobe JSON for {file_path}: {e}")
        return None


async def compress_video(input_path, output_path, preset_name, progress_callback, task):
    info = await get_video_info(input_path)
    if not info:
        # Robust Fallback: Attempt compression even if ffprobe metadata extraction fails
        logger.warning(
            f"Metadata extraction failed for {input_path}. Using safe defaults."
        )
        info = {
            "format": {"duration": task.get("duration", 0) or 0},
            "streams": [
                {
                    "codec_type": "video",
                    "width": 1280,
                    "height": 720,
                    "avg_frame_rate": "24/1",
                }
            ],
        }

    duration = float(info.get("format", {} or {}).get("duration", 0) or 0)
    streams = info.get("streams", []) if isinstance(info, dict) else []
    video_stream = next((s for s in streams if isinstance(s, dict) and s.get("codec_type") == "video"), None)

    if not video_stream:
        logger.warning(
            f"No video stream found in metadata for {input_path}. Using defaults."
        )
        video_stream = {"width": 1280, "height": 720, "avg_frame_rate": "24/1"}
    width = int(video_stream.get("width", 0))
    height = int(video_stream.get("height", 0))

    fps_str = video_stream.get("avg_frame_rate", "0/1")
    try:
        num, den = map(int, fps_str.split("/"))
        fps = num / den if den != 0 else 0
    except:
        fps = 0

    target_height = -2
    v_bitrate = "500k"
    a_bitrate = "64k"

    if duration > 900:  # > 15 minutes
        if preset_name == "low":
            target_height = 360
            v_bitrate = "300k"
        elif preset_name == "medium":
            target_height = 240
            v_bitrate = "200k"
        else:
            target_height = 144
            v_bitrate = "100k"
    else:  # <= 15 minutes
        if preset_name == "low":
            if height > 720:
                target_height = 400
            elif height >= 480:
                target_height = 360
            v_bitrate = "500k"
        elif preset_name == "medium":
            target_height = 360
            v_bitrate = "350k"
        else:
            target_height = 240
            v_bitrate = "200k"

    if preset_name == "diff":
        cmd = [
            "ffmpeg",
            "-y",
            "-ignore_unknown",  # Ignore unknown streams
            "-fflags",
            "+genpts+discardcorrupt+igndts",  # Advanced recovery flags
            "-i",
            input_path,
            "-avoid_negative_ts",
            "make_zero",
            "-threads",
            "0",
            "-fps_mode",
            "cfr",  # Fix pts/dts issues by forcing constant frame rate
            "-c:v",
            "libx264",
            "-preset",
            "fast",
            "-crf",
            "24",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-ac",
            "2",
            "-b:a",
            "96k",
            "-max_muxing_queue_size",
            "1024",
            "-movflags",
            "+faststart",
        ]
        if fps > 24:
            cmd.extend(["-r", "24"])
    else:
        cmd = [
            "ffmpeg",
            "-y",
            "-ignore_unknown",
            "-fflags",
            "+genpts+discardcorrupt+igndts",  # Regenerate missing timestamps, discard corrupt data
            "-i",
            input_path,
            "-avoid_negative_ts",
            "make_zero",  # Fix negative start times
            "-threads",
            "0",
            "-fps_mode",
            "cfr",  # Fix pts/dts issues
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast", # Switched to ultrafast for maximum speed
        ]

        if fps > 24:
            cmd.extend(["-r", "24"])

        cmd.extend(
            [
                "-b:v",
                v_bitrate,
                "-c:a",
                "aac",
                "-b:a",
                a_bitrate,
                "-max_muxing_queue_size",
                "1024",
                "-movflags",
                "+faststart",
            ]
        )

        if target_height != -2:
            cmd.extend(["-vf", f"scale=-2:{target_height}"])

    cmd.append(output_path)

    process = await asyncio.create_subprocess_exec(*cmd, stderr=asyncio.subprocess.PIPE)

    # Store process in task for pause/resume/kill support
    task["process"] = process

    last_error_lines = []

    while True:
        try:
            chunk = await process.stderr.read(1024)
            if not chunk:
                break

            line = chunk.decode("utf-8", errors="ignore")

            # Keep the last chunk for error reporting
            last_error_lines.append(line)
            if len(last_error_lines) > 20:
                last_error_lines.pop(0)

            if "time=" in line:
                match = TIME_REGEX.search(line)
                if match:
                    h, m, s = match.group(1).split(":")
                    current_time = int(h) * 3600 + int(m) * 60 + float(s)
                    if duration > 0:
                        await progress_callback(current_time, duration)
        except Exception as e:
            if str(e) == "CANCELLED":
                try:
                    process.kill()
                except:
                    pass
                raise e
            logger.error(f"Error reading ffmpeg output: {e}")
            break

    await process.wait()
    task["process"] = None

    if process.returncode != 0:
        error_msg = "".join(last_error_lines).strip()
        return False, error_msg or f"FFmpeg exited with code {process.returncode}"

    return True, None


async def merge_videos(input_paths, output_path, progress_callback, task):
    # Create concat file for demuxer (Stage 1: Fast Copy)
    concat_file = os.path.join(
        os.path.dirname(output_path), f"concat_{os.path.basename(output_path)}.txt"
    )
    total_duration = 0
    for p in input_paths:
        info = await get_video_info(p)
        if info:
            total_duration += float(info.get("format", {}).get("duration", 0))

    with open(concat_file, "w") as f:
        for p in input_paths:
            # Escape single quotes for ffmpeg concat file
            p_esc = os.path.abspath(p).replace("'", "'\\''")
            f.write(f"file '{p_esc}'\n")

    # Stage 1: Fast Copy
    cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        concat_file,
        "-c",
        "copy",
        output_path,
    ]

    process = await asyncio.create_subprocess_exec(*cmd, stderr=asyncio.subprocess.PIPE)
    task["process"] = process
    full_stderr = ""
    while True:
        try:
            chunk = await process.stderr.read(1024)
            if not chunk:
                break
            line = chunk.decode("utf-8", errors="ignore")
            full_stderr += line
            if "time=" in line:
                match = TIME_REGEX.search(line)
                if match:
                    h, m, s = match.group(1).split(":")
                    current_time = int(h) * 3600 + int(m) * 60 + float(s)
                    if total_duration > 0:
                        await progress_callback(current_time, total_duration)
        except:
            break
    await process.wait()

    if process.returncode == 0:
        task["process"] = None
        try:
            os.remove(concat_file)
        except:
            pass
        return True, ""

    task["process"] = None
    try:
        os.remove(concat_file)
    except:
        pass

    error_detail = "Fast Merge Failed. To merge videos quickly, they must be the EXACT same format (codec, resolution, etc.). Your videos are different. Please ensure files are identical before merging."
    return False, f"{error_detail}\n\nFFmpeg Error:\n{full_stderr}"


async def split_video(input_path, output_dir, parts, progress_callback, task):
    info = await get_video_info(input_path)
    if not info:
        return [], "Could not read video"
    duration = float(info.get("format", {}).get("duration", 0))
    if duration == 0:
        return [], "Invalid duration"

    part_duration = duration / parts
    output_files = []

    base_name = os.path.splitext(os.path.basename(input_path))[0]
    ext = os.path.splitext(input_path)[1]

    last_stderr = ""
    for i in range(parts):
        start_time = i * part_duration
        out_file = os.path.join(output_dir, f"{base_name}_part{i+1}{ext}")
        output_files.append(out_file)

        cmd = [
            "ffmpeg",
            "-y",
            "-ss",
            str(start_time),
            "-i",
            input_path,
            "-t",
            str(part_duration),
            "-c",
            "copy",
            out_file,
        ]
        process = await asyncio.create_subprocess_exec(
            *cmd, stderr=asyncio.subprocess.PIPE
        )
        task["process"] = process
        full_stderr = ""
        while True:
            try:
                chunk = await process.stderr.read(1024)
                if not chunk:
                    break
                line = chunk.decode("utf-8", errors="ignore")
                full_stderr += line
                if "time=" in line:
                    match = TIME_REGEX.search(line)
                    if match:
                        h, m, s = match.group(1).split(":")
                        current_time = int(h) * 3600 + int(m) * 60 + float(s)
                        await progress_callback(current_time + start_time, duration)
            except:
                break
        await process.wait()
        if process.returncode != 0:
            last_stderr = full_stderr
            break

    task["process"] = None
    if any(not os.path.exists(f) for f in output_files if f):
        return [], last_stderr
    return output_files, ""


async def remove_stream(
    input_path, output_path, stream_indices, progress_callback, task
):
    info = await get_video_info(input_path)
    if not info:
        return False, "Could not read video"
    duration = float(info.get("format", {}).get("duration", 0))

    cmd = ["ffmpeg", "-y", "-i", input_path, "-map", "0"]
    for idx in stream_indices:
        cmd.extend(["-map", f"-0:{idx}"])

    cmd.extend(["-c", "copy", output_path])

    process = await asyncio.create_subprocess_exec(*cmd, stderr=asyncio.subprocess.PIPE)
    task["process"] = process
    full_stderr = ""
    while True:
        try:
            chunk = await process.stderr.read(1024)
            if not chunk:
                break
            line = chunk.decode("utf-8", errors="ignore")
            full_stderr += line
            if "time=" in line:
                match = TIME_REGEX.search(line)
                if match:
                    h, m, s = match.group(1).split(":")
                    current_time = int(h) * 3600 + int(m) * 60 + float(s)
                    if duration > 0:
                        await progress_callback(current_time, duration)
        except:
            break
    await process.wait()
    task["process"] = None
    return process.returncode == 0, full_stderr if process.returncode != 0 else ""


async def extract_stream(
    input_path, output_path, stream_index, progress_callback, task
):
    info = await get_video_info(input_path)
    if not info:
        return False, "Could not read video"
    duration = float(info.get("format", {}).get("duration", 0))

    cmd = ["ffmpeg", "-y", "-i", input_path, "-map", f"0:{stream_index}", "-c", "copy", output_path]

    process = await asyncio.create_subprocess_exec(*cmd, stderr=asyncio.subprocess.PIPE)
    task["process"] = process
    full_stderr = ""
    while True:
        try:
            chunk = await process.stderr.read(1024)
            if not chunk:
                break
            line = chunk.decode("utf-8", errors="ignore")
            full_stderr += line
            if "time=" in line:
                match = TIME_REGEX.search(line)
                if match:
                    h, m, s = match.group(1).split(":")
                    current_time = int(h) * 3600 + int(m) * 60 + float(s)
                    if duration > 0:
                        await progress_callback(current_time, duration)
        except:
            break
    await process.wait()
    task["process"] = None
    return process.returncode == 0, full_stderr if process.returncode != 0 else ""


async def mux_audio_video(video_path, audio_path, output_path, progress_callback, task):
    info = await get_video_info(video_path)
    if not info:
        return False, "Could not read video"
    duration = float(info.get("format", {}).get("duration", 0))

    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        video_path,
        "-i",
        audio_path,
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-shortest",
        output_path,
    ]
    process = await asyncio.create_subprocess_exec(*cmd, stderr=asyncio.subprocess.PIPE)
    task["process"] = process
    full_stderr = ""
    while True:
        try:
            chunk = await process.stderr.read(1024)
            if not chunk:
                break
            line = chunk.decode("utf-8", errors="ignore")
            full_stderr += line
            if "time=" in line:
                match = TIME_REGEX.search(line)
                if match:
                    h, m, s = match.group(1).split(":")
                    current_time = int(h) * 3600 + int(m) * 60 + float(s)
                    if duration > 0:
                        await progress_callback(current_time, duration)
        except:
            break
    await process.wait()
    task["process"] = None
    return process.returncode == 0, full_stderr if process.returncode != 0 else ""


async def extract_sample(input_path, output_path, progress_callback, task):
    info = await get_video_info(input_path)
    if not info:
        return False, "Could not read video info"
    duration = float(info.get("format", {}).get("duration", 0))

    if duration <= 0:
        return False, "Invalid video duration"

    # Choose start time: 10% into the video, but not more than 10 minutes (600s).
    # If video is shorter than 2 minutes, start at 10 seconds.
    if duration < 120:
        start_time = min(10, duration / 4)
    else:
        start_time = min(duration * 0.1, 600)

    sample_duration = min(60, duration - start_time)  # 1 minute max
    if sample_duration <= 0:
        sample_duration = duration  # Fallback
        start_time = 0

    cmd = [
        "ffmpeg",
        "-y",
        "-ss",
        str(start_time),
        "-i",
        input_path,
        "-t",
        str(sample_duration),
        "-c",
        "copy",
        output_path,
    ]

    process = await asyncio.create_subprocess_exec(*cmd, stderr=asyncio.subprocess.PIPE)
    task["process"] = process
    full_stderr = ""
    while True:
        try:
            chunk = await process.stderr.read(1024)
            if not chunk:
                break
            line = chunk.decode("utf-8", errors="ignore")
            full_stderr += line
            if "time=" in line:
                match = TIME_REGEX.search(line)
                if match:
                    h, m, s = match.group(1).split(":")
                    current_time = int(h) * 3600 + int(m) * 60 + float(s)
                    if sample_duration > 0:
                        await progress_callback(current_time, sample_duration)
        except:
            break
    await process.wait()
    task["process"] = None
    return process.returncode == 0, full_stderr if process.returncode != 0 else ""
