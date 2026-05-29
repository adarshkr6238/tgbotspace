import asyncio
import os
import math
import logging
from typing import Callable, Optional
from pyrogram import Client
from pyrogram.raw import functions, types
from pyrogram.file_id import FileId

logger = logging.getLogger(__name__)

# Configurable constants for transfer
CHUNK_SIZE = 512 * 1024  # 512 KB per chunk (must be multiple of 1KB)
MAX_WORKERS = 10         # Concurrent connections

async def fast_download(client: Client, message, file_path: str, progress: Optional[Callable] = None):
    """
    Downloads a file using parallel chunking via Pyrogram's raw API.
    """
    media = getattr(message, message.media.value) if message.media else None
    if not media:
        raise ValueError("Message does not contain media")
        
    file_id_obj = FileId.decode(media.file_id)
    file_size = getattr(media, "file_size", 0)
    
    if file_size == 0 or file_size < CHUNK_SIZE:
        # Fallback to standard for small/unknown files
        return await client.download_media(message, file_name=file_path, progress=progress)

    # Resolve the file location for the raw API
    if file_id_obj.file_type in (types.InputDocumentFileLocation, types.InputPhotoFileLocation):
         location = types.InputDocumentFileLocation(
             id=file_id_obj.media_id,
             access_hash=file_id_obj.access_hash,
             file_reference=file_id_obj.file_reference,
             thumb_size=""
         )
    else:
        # A generic fallback if the location can't be easily constructed.
        # It's safer to use standard download here to avoid complex MTProto location construction errors.
        logger.warning(f"Fast download not supported for file type: {file_id_obj.file_type}. Using standard.")
        return await client.download_media(message, file_name=file_path, progress=progress)

    os.makedirs(os.path.dirname(os.path.abspath(file_path)), exist_ok=True)
    
    total_parts = math.ceil(file_size / CHUNK_SIZE)
    downloaded_size = 0
    
    with open(file_path, "wb") as f:
        # Pre-allocate file size
        f.seek(file_size - 1)
        f.write(b"\0")
        
        semaphore = asyncio.Semaphore(MAX_WORKERS)
        
        async def download_part(part_num, offset):
            nonlocal downloaded_size
            async with semaphore:
                try:
                    result = await client.invoke(
                        functions.upload.GetFile(
                            location=location,
                            offset=offset,
                            limit=CHUNK_SIZE
                        )
                    )
                    
                    if isinstance(result, types.upload.File):
                        f.seek(offset)
                        f.write(result.bytes)
                        downloaded_size += len(result.bytes)
                        
                        if progress:
                            # Run progress in background to not block downloads
                            asyncio.create_task(progress(downloaded_size, file_size))
                except Exception as e:
                    logger.error(f"Error downloading chunk {part_num}: {e}")
                    raise e

        tasks = []
        for i in range(total_parts):
            offset = i * CHUNK_SIZE
            tasks.append(asyncio.create_task(download_part(i, offset)))
            
        await asyncio.gather(*tasks)
        
    return file_path

async def fast_upload(client: Client, file_path: str, progress: Optional[Callable] = None):
    """
    Uploads a file using parallel chunking via Pyrogram's raw API, returning an InputFile.
    """
    file_size = os.path.getsize(file_path)
    file_id = os.urandom(8).hex() # Random unique ID for the file session
    
    if file_size == 0:
        raise ValueError("Cannot upload empty file")
        
    if file_size < CHUNK_SIZE:
        # Fallback to standard for very small files (less overhead)
        # Note: This returns a path, client.send_document will handle it automatically
        return file_path

    total_parts = math.ceil(file_size / CHUNK_SIZE)
    is_big = file_size > 10 * 1024 * 1024 # Telegram defines "big" as > 10MB
    uploaded_size = 0
    
    with open(file_path, "rb") as f:
        semaphore = asyncio.Semaphore(MAX_WORKERS)
        
        async def upload_part(part_num):
            nonlocal uploaded_size
            async with semaphore:
                f.seek(part_num * CHUNK_SIZE)
                chunk = f.read(CHUNK_SIZE)
                
                try:
                    if is_big:
                        await client.invoke(
                            functions.upload.SaveBigFilePart(
                                file_id=int(file_id, 16),
                                file_part=part_num,
                                file_total_parts=total_parts,
                                bytes=chunk
                            )
                        )
                    else:
                        await client.invoke(
                            functions.upload.SaveFilePart(
                                file_id=int(file_id, 16),
                                file_part=part_num,
                                bytes=chunk
                            )
                        )
                    
                    uploaded_size += len(chunk)
                    if progress:
                        asyncio.create_task(progress(uploaded_size, file_size))
                        
                except Exception as e:
                    logger.error(f"Error uploading chunk {part_num}: {e}")
                    raise e

        tasks = []
        for i in range(total_parts):
            tasks.append(asyncio.create_task(upload_part(i)))
            
        await asyncio.gather(*tasks)
        
    filename = os.path.basename(file_path)
    if is_big:
        return types.InputFileBig(
            id=int(file_id, 16),
            parts=total_parts,
            name=filename
        )
    else:
        return types.InputFile(
            id=int(file_id, 16),
            parts=total_parts,
            name=filename,
            md5_checksum="" # MD5 is optional and complex to compute fast, leaving empty
        )