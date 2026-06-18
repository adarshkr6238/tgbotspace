import asyncio
import json
import logging
import os
import signal

from bot.config.config import Config

logger = logging.getLogger(__name__)


class QueueManager:
    def __init__(self, bot):
        self.bot = bot
        # Using PriorityQueue: tuple (priority_score, count, task)
        self.download_queue = asyncio.PriorityQueue()
        self.compression_queue = asyncio.PriorityQueue()
        self.task_counter = 0  # Tie-breaker for PriorityQueue

        self.active_compression_task = None
        self.paused_compression_tasks = []
        self.active_download_count = 0
        self.waiting_for_slot_count = 0
        self.all_tasks = {}  # Registry: msg_id -> task

        # Interactive Editing States: user_id -> {state: str, files: list, params: dict, msg_id: int}
        self.edit_sessions = {}

        self.settings_file = "user_settings.json"
        self.user_settings = self._load_settings()

    def start_worker(self):
        asyncio.create_task(self.download_worker())
        asyncio.create_task(self.compression_worker())

    def _load_settings(self):
        if os.path.exists(self.settings_file):
            try:
                with open(self.settings_file, "r") as f:
                    data = json.load(f)
                    return {int(k): v for k, v in data.items()}
            except Exception as e:
                logger.error(f"Error loading settings: {e}")
        return {}

    def _save_settings(self):
        try:
            with open(self.settings_file, "w") as f:
                json.dump(self.user_settings, f)
        except Exception as e:
            logger.error(f"Error saving settings: {e}")

    async def add_task(self, task):
        if self.get_queue_status() >= Config.MAX_QUEUE_SIZE:
            return False, "Queue is full"

        duration = task.get("duration", 0)
        priority = 1 if (0 < duration <= 300) else 2

        self.task_counter += 1
        msg_id = task["status_msg"].id
        self.all_tasks[msg_id] = task

        await self.download_queue.put((priority, self.task_counter, task))
        return True, self.get_position(task)

    def get_position(self, task):
        return (
            self.download_queue.qsize()
            + self.compression_queue.qsize()
            + self.waiting_for_slot_count
            + self.active_download_count
            + (1 if self.active_compression_task else 0)
        )

    async def download_worker(self):
        while True:
            priority, count, task = await self.download_queue.get()
            asyncio.create_task(self._run_download_task(task, priority, count))

    async def _run_download_task(self, task, priority, count):
        msg_id = task["status_msg"].id

        # Phase 1: Wait for a download slot
        self.waiting_for_slot_count += 1
        try:
            while True:
                # Optimized: Only 1 concurrent download allowed to maximize speed per file.
                max_slots = 1
                if self.active_download_count < max_slots:
                    break
                from bot.utils.progress import is_cancelled

                if is_cancelled(msg_id):
                    return
                await asyncio.sleep(2)
        finally:
            self.waiting_for_slot_count = max(0, self.waiting_for_slot_count - 1)

        # Phase 2: Perform the download
        self.active_download_count += 1
        try:
            from bot.utils.progress import is_cancelled

            if is_cancelled(msg_id):
                return
            await self.process_download(task)

            # If the task is now waiting for user input (e.g. stream selection),
            # don't put it in the compression queue yet.
            if not task.get("is_editing"):
                await self.compression_queue.put((priority, count, task))
            else:
                logger.info(
                    f"Task {msg_id} is being edited. Postponing compression queue."
                )
        except Exception as e:
            logger.error(f"Download task failed: {e}")
            self.cleanup_task(task)
        finally:
            self.active_download_count = max(0, self.active_download_count - 1)
            self.download_queue.task_done()

    async def continue_task(self, task):
        """Resume a task that was paused for editing and put it in the compression queue."""
        duration = task.get("duration", 0)
        priority = 1 if (0 < duration <= 300) else 2
        self.task_counter += 1
        await self.compression_queue.put((priority, self.task_counter, task))
        logger.info(
            f"Task {task['status_msg'].id} continued and added to compression queue."
        )

    async def compression_worker(self):
        while True:
            priority, count, next_task = await self.compression_queue.get()

            # Intelligent Preemption Check
            if self.active_compression_task and not self.active_compression_task.get(
                "is_paused"
            ):
                active = self.active_compression_task

                active_dur = active.get("duration", 0)
                next_dur = next_task.get("duration", 0)
                active_pct = active.get("percentage", 0)

                # Condition: Active is long (>20m), Next is short (<=5m), Active < 60% done
                if active_dur > 1200 and 0 < next_dur <= 300 and active_pct < 60:
                    process = active.get("process")
                    if process:
                        try:
                            logger.info(
                                f"Pausing task at {active_pct}% for priority short video"
                            )
                            os.kill(process.pid, signal.SIGSTOP)
                            active["is_paused"] = True
                            self.paused_compression_tasks.append(active)
                            from bot.utils.progress import safe_edit
                            await safe_edit(active["status_msg"], f"⏸ **Paused ({active_pct:.1f}%):** Processing a shorter priority video first...")
                            self.active_compression_task = None
                        except Exception:
                            pass

            # Wait if another task is still active (wasn't preempted)
            while self.active_compression_task and not self.active_compression_task.get(
                "is_paused", False
            ):
                await asyncio.sleep(1)

            # Note: We removed the HOL blocking while loop here.
            # Tasks should only be in this queue when ready to process.

            self.active_compression_task = next_task
            asyncio.create_task(self._run_compression_task(next_task))

    async def _run_compression_task(self, task):
        msg_id = task["status_msg"].id
        task["is_success"] = False
        try:
            await self.process_compression(task)
            task["is_success"] = True
        except Exception as e:
            logger.error(f"Compression task failed: {e}")
        finally:
            self.compression_queue.task_done()
            self.cleanup_task(task)
            self.all_tasks.pop(msg_id, None)

            if self.active_compression_task == task:
                self.active_compression_task = None
            elif task in self.paused_compression_tasks:
                try:
                    self.paused_compression_tasks.remove(task)
                except:
                    pass
            self.resume_if_paused()

    def resume_if_paused(self):
        if not self.active_compression_task and self.paused_compression_tasks:
            paused_task = self.paused_compression_tasks.pop(0)
            paused_task["is_paused"] = False
            self.active_compression_task = paused_task
            process = paused_task.get("process")
            if process:
                try:
                    logger.info("Resuming paused long task...")
                    os.kill(process.pid, signal.SIGCONT)
                except Exception:
                    pass

    async def process_download(self, task):
        pass

    async def process_compression(self, task):
        pass

    def cleanup_task(self, task, force=False):
        if not force and not task.get("is_success"):
            logger.warning(
                f"Task {task.get('status_msg', {}).id} did not succeed. Skipping file deletion for debug/retry."
            )
            return

        for p in task.get("paths", []):
            if os.path.exists(p):
                try:
                    os.remove(p)
                except:
                    pass

    def get_user_preset(self, user_id):
        return self.user_settings.get(user_id, Config.DEFAULT_PRESET)

    def set_user_preset(self, user_id, preset):
        self.user_settings[user_id] = preset
        self._save_settings()

    def get_queue_status(self):
        return len(self.all_tasks)

    # --- Edit Session Management ---
    def set_edit_state(self, user_id, state, msg_id, params=None):
        self.edit_sessions[user_id] = {
            "state": state,
            "files": [],
            "params": params or {},
            "msg_id": msg_id,
        }
        task = self.all_tasks.get(msg_id)
        if task:
            task["is_editing"] = True

    def get_edit_state(self, user_id):
        return self.edit_sessions.get(user_id)

    def add_edit_file(self, user_id, file_path, file_id=None):
        session = self.edit_sessions.get(user_id)
        if session:
            session["files"].append({"path": file_path, "file_id": file_id})
            return True
        return False

    def clear_edit_state(self, user_id):
        session = self.edit_sessions.pop(user_id, None)
        if session:
            task = self.all_tasks.get(session["msg_id"])
            if task:
                task["is_editing"] = False
            for f in session["files"]:
                if "path" in f and os.path.exists(f["path"]):
                    try:
                        os.remove(f["path"])
                    except:
                        pass
        return session

    # --------------------------------

    def get_current_task_info(self):
        if self.active_compression_task:
            media = (
                self.active_compression_task["message"].video
                or self.active_compression_task["message"].document
            )
            name = media.file_name or "Unknown Video"
            status = (
                " (Paused)" if self.active_compression_task.get("is_paused") else ""
            )
            return f"{name}{status}"
        return None

    async def clear_queues(self):
        """Administrative reset: stop everything and empty queues."""
        from bot.utils.progress import cancel_task

        for msg_id, task in list(self.all_tasks.items()):
            cancel_task(msg_id)
            if task.get("process"):
                try:
                    task["process"].kill()
                except:
                    pass
            self.cleanup_task(task, force=True)
            self.all_tasks.pop(msg_id, None)
        while not self.download_queue.empty():
            try:
                self.download_queue.get_nowait()
                self.download_queue.task_done()
            except:
                break
        while not self.compression_queue.empty():
            try:
                self.compression_queue.get_nowait()
                self.compression_queue.task_done()
            except:
                break
        self.all_tasks = {}
        self.active_compression_task = None
        self.paused_compression_tasks = []
        self.active_download_count = 0
        self.waiting_for_slot_count = 0
        self.task_counter = 0
        from bot.services.storage_service import wipe_all_storage

        wipe_all_storage()
