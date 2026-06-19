# Telegram Bot Optimization & Debugging Session Log
**Date:** June 18, 2026

## Overview
This document serves as a record of the architecture changes, bug fixes, and feature implementations applied to the Hugging Face Spaces (`tgbotspace` and `tgbotspace2`) running the Telegram Video Compression Bot.

## 1. Parallel Cluster Setup
*   **Goal:** Utilize `tgbotspace2` as a parallel server to process videos simultaneously without duplicating work.
*   **Action:** Deployed the exact same codebase to both spaces.
*   **Load Balancing:** Implemented a highly efficient **Deterministic Modulo-Based Distribution** system.
    *   Node 1 (`tgbotspace`) strictly processes videos with **Even** Message IDs.
    *   Node 2 (`tgbotspace2`) strictly processes videos with **Odd** Message IDs.
    *   This achieved `O(1)` zero-overhead parallel processing, completely eliminating race conditions.
*   **Auto-Detection Fix:** Added automatic `SPACE_ID` mapping on startup to dynamically self-configure the correct node prefix (`node1` or `node2`), eliminating half-message dropouts caused by manual copy-pasting of environment secrets.

## 2. Bot Cloning Feature (Owner Management)
*   **Goal:** Allow the owner to spin up new bot clones dynamically.
*   **Commands Added:**
    *   `/addbot <token>`: Initializes and saves a new bot clone on the fly.
    *   `/delbot <token>`: Stops and removes an active clone.
    *   `/clones`: Lists all currently running clones on the node.
*   **Persistence:** Cloned tokens are saved securely to `cloned_bots.json` so they automatically revive upon server restarts.
*   **Cluster Propagation Sync:** Implemented internal `/addbot` and `/delbot` health check endpoints. Modifying clones on either node now instantly propagates across the entire cluster network to ensure cloned bots run on all nodes simultaneously and never miss half the stream.

## 3. Group Support & Authorization
*   **Goal:** Enable the bot to function in specific Telegram groups.
*   **Action:** Added `GROUP_ID` to configuration and updated message filters.
*   **Authorization:** 
    *   The bot now processes videos in the authorized group (`-1002335588415`) and private chats.
    *   Implemented explicit authorization checks to ensure only authorized users or group members can trigger processing, protecting server resources.

## 3. Resilience & Rate Limit Handling
*   **FloodWait Mitigation:** Addressed severe Telegram rate limits (`[420 FLOOD_WAIT_X]`) that were causing the bot to crash or ignore messages. Wrapped startup initialization and progress message edits in `try/except FloodWait` blocks. The bot now gracefully sleeps through the penalty duration instead of crashing.
*   **Cross-Node Clear:** Implemented a unified `/clear` command. Node 1 uses an internal API endpoint on Node 2's health server to wipe queues and temp files across the entire cluster simultaneously.
*   **Upload Safety:** Modified `queue_manager.py` to only delete processed videos *after* a successful upload, preventing data loss on network timeouts.

## 4. Video Processing & FFmpeg Fixes
*   **Download Concurrency:** Reduced maximum concurrent downloads per node to **1** to maximize bandwidth and prevent slow, bottlenecked transfers.
*   **Corruption Handling:** Fixed `moov atom not found` and `Invalid data found` errors. Added pre-processing file size validation and injected advanced FFmpeg recovery flags (`-ignore_unknown`, `+discardcorrupt`, `+igndts`).
*   **Speed Optimization:** Changed the default FFmpeg x264 compression preset from `superfast` to `ultrafast` to maximize throughput on CPU-bound Hugging Face environments.

## 5. UI & Progress Bar Fixes
*   **Glitch Removal:** The progress bar was previously flickering and jumping due to concurrent async tasks emitting progress data simultaneously.
*   **Global Debounce Lock:** Implemented a strict synchronous debounce lock using `_is_updating` and `_last_update_time` in `progress.py`. Progress messages are now strictly throttled to update exactly once every 6 seconds, eliminating visual glitches and reducing Telegram API spam.

## 6. Codebase Maintenance
*   Removed dead files (`fast_telethon.py`).
*   Applied professional formatting (`black`, `isort`) and stripped unused imports (`autoflake`) across the entire repository to ensure standard Python architecture.

## 7. Current Known Issue (Under Investigation)
*   **Stream Remover Freeze:** The "Stream Remover" edit feature currently halts after the download stage reaches 100%. The `queue_manager` successfully pauses the task (marking it as `is_editing=True`), but the callback to render the stream selection keyboard is either failing to fire or trapped in an async deadlock.

---
*Log generated automatically by Gemini CLI.*