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
        'ffprobe', '-v', 'quiet', '-print_format', 'json=c=1', 
        '-show_format', '-show_streams', file_path
    ]
    process = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await process.communicate()
    if process.returncode != 0:
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
            '-c:v', 'libx264', '-preset', 'fast', '-crf', '24',
            '-pix_fmt', 'yuv420p',
            '-c:a', 'aac', '-ac', '2', '-b:a', '96k',
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
            '-c:v', 'libx264', '-preset', 'superfast'
        ]

        if fps > 24:
            cmd.extend(['-r', '24'])

        cmd.extend([
            '-b:v', v_bitrate,
            '-c:a', 'aac', '-b:a', a_bitrate, '-movflags', '+faststart'
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
