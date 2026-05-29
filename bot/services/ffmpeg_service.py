import asyncio
import os
import subprocess
import json
import logging
import re

logger = logging.getLogger(__name__)

# Compile Regex globally for performance
TIME_REGEX = re.compile(r"time=(\d+:\d+:\d+\.\d+)")

async def get_video_info(file_path):
    cmd = [
        'ffprobe', '-v', 'error', '-print_format', 'json=c=1', 
        '-show_format', '-show_streams', file_path
    ]
    process = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await process.communicate()
    if process.returncode != 0:
        logger.error(f"ffprobe failed for {file_path}: {stderr.decode('utf-8', errors='ignore')}")
        return None
    return json.loads(stdout)

async def compress_video(input_path, output_path, preset_name, progress_callback, task):
    info = await get_video_info(input_path)
    if not info:
        return False, "Could not get video info with ffprobe."
        
    duration = float(info.get('format', {}).get('duration', 0))
    video_stream = next((s for s in info['streams'] if s['codec_type'] == 'video'), None)
    if not video_stream:
        return False, "No video stream found in file."
        
    width = int(video_stream.get('width', 0))
    height = int(video_stream.get('height', 0))
    
    fps_str = video_stream.get('avg_frame_rate', '0/1')
    try:
        num, den = map(int, fps_str.split('/'))
        fps = num / den if den != 0 else 0
    except:
        fps = 0

    target_height = -2 
    v_bitrate = "500k"
    a_bitrate = "64k"
    
    if duration > 900: # > 15 minutes
        if preset_name == "low":
            target_height = 360
            v_bitrate = "300k"
        elif preset_name == "medium":
            target_height = 240
            v_bitrate = "200k"
        else:
            target_height = 144
            v_bitrate = "100k"
    else: # <= 15 minutes
        if preset_name == "low":
            if height > 720: target_height = 400
            elif height >= 480: target_height = 360
            v_bitrate = "500k"
        elif preset_name == "medium":
            target_height = 360
            v_bitrate = "350k"
        else:
            target_height = 240
            v_bitrate = "200k"

    if preset_name == "diff":
        cmd = [
            'ffmpeg', '-y', 
            '-fflags', '+genpts',
            '-i', input_path,
            '-avoid_negative_ts', 'make_zero',
            '-threads', '0', 
            '-fps_mode', 'cfr', # Fix pts/dts issues by forcing constant frame rate
            '-c:v', 'libx264', '-preset', 'fast', '-crf', '24',
            '-pix_fmt', 'yuv420p',
            '-c:a', 'aac', '-ac', '2', '-b:a', '96k',
            '-max_muxing_queue_size', '1024',
            '-movflags', '+faststart'
        ]
        if fps > 24:
            cmd.extend(['-r', '24'])
    else:
        cmd = [
            'ffmpeg', '-y', 
            '-fflags', '+genpts', # Regenerate missing timestamps
            '-i', input_path,
            '-avoid_negative_ts', 'make_zero', # Fix negative start times
            '-threads', '0', 
            '-fps_mode', 'cfr', # Fix pts/dts issues
            '-c:v', 'libx264', '-preset', 'superfast'
        ]

        if fps > 24:
            cmd.extend(['-r', '24'])

        cmd.extend([
            '-b:v', v_bitrate,
            '-c:a', 'aac', '-b:a', a_bitrate, 
            '-max_muxing_queue_size', '1024',
            '-movflags', '+faststart'
        ])
        
        if target_height != -2:
            cmd.extend(['-vf', f"scale=-2:{target_height}"])
        
    cmd.append(output_path)
    
    process = await asyncio.create_subprocess_exec(
        *cmd, stderr=asyncio.subprocess.PIPE
    )
    
    # Store process in task for pause/resume/kill support
    task['process'] = process
    
    last_error_lines = []
    
    while True:
        try:
            chunk = await process.stderr.read(1024)
            if not chunk:
                break
                
            line = chunk.decode('utf-8', errors='ignore')
            
            # Keep the last chunk for error reporting
            last_error_lines.append(line)
            if len(last_error_lines) > 20:
                last_error_lines.pop(0)

            if "time=" in line:
                match = TIME_REGEX.search(line)
                if match:
                    h, m, s = match.group(1).split(":")
                    current_time = int(h)*3600 + int(m)*60 + float(s)
                    if duration > 0:
                        await progress_callback(current_time, duration)
        except Exception as e:
            if str(e) == "CANCELLED":
                try:
                    process.kill()
                except: pass
                raise e
            logger.error(f"Error reading ffmpeg output: {e}")
            break
                
    await process.wait()
    task['process'] = None
    
    if process.returncode != 0:
        error_msg = "".join(last_error_lines).strip()
        return False, error_msg or f"FFmpeg exited with code {process.returncode}"
    
    return True, None

async def merge_videos(input_paths, output_path, progress_callback, task):
    # Create concat file for demuxer (Stage 1: Fast Copy)
    concat_file = os.path.join(os.path.dirname(output_path), f"concat_{os.path.basename(output_path)}.txt")
    total_duration = 0
    for p in input_paths:
        info = await get_video_info(p)
        if info:
            total_duration += float(info.get('format', {}).get('duration', 0))

    with open(concat_file, "w") as f:
        for p in input_paths:
            # Escape single quotes for ffmpeg concat file
            p_esc = os.path.abspath(p).replace("'", "'\\''")
            f.write(f"file '{p_esc}'\n")

    # Stage 1: Fast Copy
    cmd = [
        'ffmpeg', '-y', '-f', 'concat', '-safe', '0', '-i', concat_file,
        '-c', 'copy', output_path
    ]
    
    process = await asyncio.create_subprocess_exec(*cmd, stderr=asyncio.subprocess.PIPE)
    task['process'] = process
    full_stderr = ""
    while True:
        try:
            chunk = await process.stderr.read(1024)
            if not chunk: break
            line = chunk.decode('utf-8', errors='ignore')
            full_stderr += line
            if "time=" in line:
                match = TIME_REGEX.search(line)
                if match:
                    h, m, s = match.group(1).split(":")
                    current_time = int(h)*3600 + int(m)*60 + float(s)
                    if total_duration > 0: await progress_callback(current_time, total_duration)
        except: break
    await process.wait()
    
    if process.returncode == 0:
        task['process'] = None
        try: os.remove(concat_file)
        except: pass
        return True, ""

    # Stage 2: Fallback to Re-encoding (Robust but slower)
    # This handles mismatched codecs, resolutions, or pixel formats
    logger.info("Fast merge failed. Falling back to re-encoding merge...")
    
    # Construct complex filter: [0:v][0:a][1:v][1:a]...concat=n=N:v=1:a=1[v][a]
    filter_complex = ""
    inputs = []
    for i in range(len(input_paths)):
        inputs.extend(['-i', input_paths[i]])
        filter_complex += f"[{i}:v:0][i}:a:0]" # Assumes 1 video + 1 audio per file
    
    # Actually need to check if audio exists for each file or it will fail
    filter_v = ""
    filter_a = ""
    for i in range(len(input_paths)):
        filter_v += f"[{i}:v:0]"
        filter_a += f"[{i}:a:0]"
    
    filter_complex = f"{filter_v}{filter_a}concat=n={len(input_paths)}:v=1:a=1[v][a]"
    
    cmd = [
        'ffmpeg', '-y'
    ]
    for p in input_paths:
        cmd.extend(['-i', p])
        
    cmd.extend([
        '-filter_complex', filter_complex,
        '-map', '[v]', '-map', '[a]',
        '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '28',
        '-c:a', 'aac', '-b:a', '128k',
        output_path
    ])

    process = await asyncio.create_subprocess_exec(*cmd, stderr=asyncio.subprocess.PIPE)
    task['process'] = process
    full_stderr = ""
    while True:
        try:
            chunk = await process.stderr.read(1024)
            if not chunk: break
            line = chunk.decode('utf-8', errors='ignore')
            full_stderr += line
            if "time=" in line:
                match = TIME_REGEX.search(line)
                if match:
                    h, m, s = match.group(1).split(":")
                    current_time = int(h)*3600 + int(m)*60 + float(s)
                    if total_duration > 0: await progress_callback(current_time, total_duration)
        except: break
    await process.wait()
    task['process'] = None
    try: os.remove(concat_file)
    except: pass
    
    return process.returncode == 0, full_stderr if process.returncode != 0 else ""

async def split_video(input_path, output_dir, parts, progress_callback, task):
    info = await get_video_info(input_path)
    if not info: return [], "Could not read video"
    duration = float(info.get('format', {}).get('duration', 0))
    if duration == 0: return [], "Invalid duration"
    
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
            'ffmpeg', '-y', '-ss', str(start_time), '-i', input_path, 
            '-t', str(part_duration), 
            '-c', 'copy', out_file
        ]
        process = await asyncio.create_subprocess_exec(*cmd, stderr=asyncio.subprocess.PIPE)
        task['process'] = process
        full_stderr = ""
        while True:
            try:
                chunk = await process.stderr.read(1024)
                if not chunk: break
                line = chunk.decode('utf-8', errors='ignore')
                full_stderr += line
                if "time=" in line:
                    match = TIME_REGEX.search(line)
                    if match:
                        h, m, s = match.group(1).split(":")
                        current_time = int(h)*3600 + int(m)*60 + float(s)
                        await progress_callback(current_time + start_time, duration)
            except: break
        await process.wait()
        if process.returncode != 0:
            last_stderr = full_stderr
            break
        
    task['process'] = None
    if any(not os.path.exists(f) for f in output_files if f):
         return [], last_stderr
    return output_files, ""

async def remove_stream(input_path, output_path, stream_indices, progress_callback, task):
    info = await get_video_info(input_path)
    if not info: return False, "Could not read video"
    duration = float(info.get('format', {}).get('duration', 0))
    
    cmd = [
        'ffmpeg', '-y', '-i', input_path, 
        '-map', '0'
    ]
    for idx in stream_indices:
        cmd.extend(['-map', f'-0:{idx}'])
        
    cmd.extend(['-c', 'copy', output_path])
    
    process = await asyncio.create_subprocess_exec(*cmd, stderr=asyncio.subprocess.PIPE)
    task['process'] = process
    full_stderr = ""
    while True:
        try:
            chunk = await process.stderr.read(1024)
            if not chunk: break
            line = chunk.decode('utf-8', errors='ignore')
            full_stderr += line
            if "time=" in line:
                match = TIME_REGEX.search(line)
                if match:
                    h, m, s = match.group(1).split(":")
                    current_time = int(h)*3600 + int(m)*60 + float(s)
                    if duration > 0: await progress_callback(current_time, duration)
        except: break
    await process.wait()
    task['process'] = None
    return process.returncode == 0, full_stderr if process.returncode != 0 else ""

async def mux_audio_video(video_path, audio_path, output_path, progress_callback, task):
    info = await get_video_info(video_path)
    if not info: return False, "Could not read video"
    duration = float(info.get('format', {}).get('duration', 0))
    
    cmd = [
        'ffmpeg', '-y', '-i', video_path, '-i', audio_path,
        '-c:v', 'copy', '-c:a', 'aac', '-map', '0:v:0', '-map', '1:a:0',
        '-shortest', output_path
    ]
    process = await asyncio.create_subprocess_exec(*cmd, stderr=asyncio.subprocess.PIPE)
    task['process'] = process
    full_stderr = ""
    while True:
        try:
            chunk = await process.stderr.read(1024)
            if not chunk: break
            line = chunk.decode('utf-8', errors='ignore')
            full_stderr += line
            if "time=" in line:
                match = TIME_REGEX.search(line)
                if match:
                    h, m, s = match.group(1).split(":")
                    current_time = int(h)*3600 + int(m)*60 + float(s)
                    if duration > 0: await progress_callback(current_time, duration)
        except: break
    await process.wait()
    task['process'] = None
    return process.returncode == 0, full_stderr if process.returncode != 0 else ""

