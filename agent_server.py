"""Agent server — runs inside each user's isolated Docker container.

Exposes a WebSocket API that the main_server bridges to.
Receives agent task instructions, runs the Claude CLI, streams results.

Runs the CLI subprocess in a dedicated thread with its own asyncio event loop
to avoid event-loop incompatibilities between asyncio subprocess management
and uvicorn on macOS.

The main_server (container_manager.py) mounts per-user volumes at host-matching
paths so files inside the container have the same absolute path as on the host:

  {HOST_DATA_ROOT}/shared-skills            (ro) — shared skill library
  {HOST_DATA_ROOT}/users/{uid}/skills        (rw) — user's own skills
  {HOST_DATA_ROOT}/users/{uid}/.claude       (rw) — sessions, memory, settings
  {HOST_DATA_ROOT}/users/{uid}/workspace     (rw) — file workspace
  {HOST_DATA_ROOT}/users/{uid}/logs          (rw) — container logs
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import queue as _threading_queue
import signal
import tempfile
import threading
import uuid as uuid_mod
from contextlib import suppress
from pathlib import Path
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

from src.workspace_enforcement import (
    ContainerPaths,
    _rewrite_bash_command,
    is_path_within_user_dir,
    normalize_write_path,
    rewrite_path_to_workspace,
)
from src.security_filter import OutputFilter

logger = logging.getLogger("agent_server")

_LOG_DIR = Path(os.getenv("LOG_DIR", os.path.join(os.getenv("HOME", "/home/agent"), "logs")))
_LOG_DIR.mkdir(parents=True, exist_ok=True)
_log_file = _LOG_DIR / "agent_server.log"

_formatter = logging.Formatter(
    "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

_file_handler = logging.FileHandler(_log_file)
_file_handler.setFormatter(_formatter)

_stream_handler = logging.StreamHandler()
_stream_handler.setFormatter(_formatter)

_logger = logging.getLogger()
_logger.addHandler(_file_handler)
_logger.addHandler(_stream_handler)
LOG_LEVEL = getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO)
_logger.setLevel(LOG_LEVEL)

app = FastAPI(title="agent-server")

WORKSPACE = Path(os.getenv("WORKSPACE", "/workspace"))
HOME_DIR = Path(os.getenv("HOME", "/home/agent"))

_BUNDLED_CLI = "/app/.venv/lib/python3.12/site-packages/claude_agent_sdk/_bundled/claude"


@app.get("/api/health")
async def health() -> JSONResponse:
    return JSONResponse({"status": "ok", "user_id": os.getenv("USER_ID", "unknown")})


# ── Hook helpers ─────────────────────────────────────────────────────


_INVALID_FILENAMES = {"null", "undefined", "none", ""}


def _apply_write_path_hook(
    tool_input: dict, container_paths: ContainerPaths, session_id: str
) -> dict:
    file_path = str(tool_input.get("file_path", ""))
    if not file_path or file_path.lower() in _INVALID_FILENAMES:
        logger.warning("PreToolUse[Write]: blocked invalid file_path '%s'", file_path)
        return {**tool_input, "file_path": "__blocked_invalid_path__"}
    if is_path_within_user_dir(file_path, container_paths):
        return tool_input
    rewritten = rewrite_path_to_workspace(file_path, container_paths)
    if rewritten == file_path:
        return tool_input
    # Inject session_id so writes land in outputs/{session_id}/...
    rewritten = normalize_write_path(rewritten, session_id)
    logger.info("PreToolUse[Write]: '%s' -> '%s'", file_path, rewritten)
    return {**tool_input, "file_path": rewritten}


def _apply_bash_path_hook(tool_input: dict, container_paths: ContainerPaths) -> dict:
    cmd = str(tool_input.get("command", ""))
    if not cmd:
        return tool_input
    rewritten = _rewrite_bash_command(cmd, container_paths)
    if rewritten == cmd:
        return tool_input
    logger.info("PreToolUse[Bash]: rewriting command")
    return {**tool_input, "command": rewritten}


# ── CLI command builder ───────────────────────────────────────────────

_SYSTEM_PROMPT_FILE_THRESHOLD = 4000  # chars; above this, use --system-prompt-file


def _build_cli_command(options_dict: dict, sp_file: str | None = None) -> list[str]:
    cmd = [_BUNDLED_CLI, "--output-format", "stream-json", "--verbose"]

    if sp_file:
        cmd.extend(["--system-prompt-file", sp_file])
    else:
        sp = options_dict.get("system_prompt")
        if sp is None or sp == "":
            cmd.extend(["--system-prompt", ""])
        else:
            cmd.extend(["--system-prompt", str(sp)])

    allowed = options_dict.get("allowed_tools", [])
    if allowed:
        cmd.extend(["--allowedTools", ",".join(allowed)])

    max_turns = options_dict.get("max_turns", 200)
    cmd.extend(["--max-turns", str(max_turns)])

    model = options_dict.get("model")
    if model:
        cmd.extend(["--model", str(model)])

    disallowed = options_dict.get("disallowed_tools", [])
    if disallowed:
        cmd.extend(["--disallowedTools", ",".join(disallowed)])

    perm_mode = options_dict.get("permission_mode", "acceptEdits")
    cmd.extend(["--permission-mode", str(perm_mode)])

    if options_dict.get("include_partial_messages", True):
        cmd.append("--include-partial-messages")

    cmd.extend(["--input-format", "stream-json"])

    mcp_servers = options_dict.get("mcp_servers")
    if mcp_servers and isinstance(mcp_servers, dict):
        cmd.extend(["--mcp-config", json.dumps({"mcpServers": mcp_servers})])

    resume_id = options_dict.get("resume_session_id")
    if resume_id:
        cmd.extend(["--resume", str(resume_id)])

    extra_args = options_dict.get("extra_args", {})
    for flag, value in extra_args.items():
        if value is None:
            cmd.append(f"--{flag}")
        else:
            cmd.extend([f"--{flag}", str(value)])

    return cmd


def _build_cli_env(options_dict: dict) -> dict[str, str]:
    env: dict[str, str] = {
        k: v for k, v in os.environ.items() if k != "CLAUDECODE"
    }
    env["CLAUDE_CODE_ENTRYPOINT"] = "sdk-py"

    sdk_env = options_dict.get("env") or {}
    env.update({str(k): str(v) for k, v in sdk_env.items()})

    cwd = options_dict.get("cwd")
    if cwd:
        env["PWD"] = str(cwd)

    return env


# ── CLI runner (runs in a dedicated thread) ──────────────────────────


class _CliRunner:
    """Runs the Claude CLI subprocess in a dedicated thread with its own event loop.

    Bridges events to the uvicorn event loop via a thread-safe queue.
    """

    def __init__(
        self,
        cmd: list[str],
        env: dict[str, str],
        cwd: str | None,
        prompt: str,
        session_id: str,
        container_paths: ContainerPaths,
        sp_file: str | None = None,
    ):
        self._cmd = cmd
        self._env = env
        self._cwd = cwd
        self._prompt = prompt
        self._session_id = session_id
        self._container_paths = container_paths
        self._sp_file = sp_file
        self._event_queue: _threading_queue.Queue = _threading_queue.Queue()
        self._cancel_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._assistant_sent = False

    @property
    def event_queue(self) -> _threading_queue.Queue:
        return self._event_queue

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def cancel(self) -> None:
        self._cancel_event.set()

    def _run(self) -> None:
        asyncio.run(self._async_run())

    async def _async_run(self) -> None:
        try:
            await self._run_cli()
        except Exception as exc:
            logger.exception("CLI thread exception: %s", exc)
            self._event_queue.put(("exception", exc))
        finally:
            if self._sp_file:
                with suppress(OSError):
                    os.unlink(self._sp_file)

    async def _run_cli(self) -> None:
        logger.info("CLI: %s", " ".join(self._cmd))
        process = await asyncio.create_subprocess_exec(
            *self._cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self._cwd,
            env=self._env,
        )
        logger.info("CLI pid=%d", process.pid)

        # Disable the 64KB readline limit — CLI can emit large JSON lines
        # (e.g. tool results containing base64-encoded images).
        process.stdout._limit = 10 * 1024 * 1024  # 10 MiB
        process.stderr._limit = 10 * 1024 * 1024

        # Background stderr → event queue
        async def _read_stderr() -> None:
            while True:
                line = await process.stderr.readline()
                if not line:
                    break
                err = line.decode(errors="replace").rstrip()
                if err:
                    self._event_queue.put(("stderr", err))

        stderr_task = asyncio.create_task(_read_stderr())

        try:
            # ── Phase 1: initialize ──────────────────────────────
            hooks_config = {
                "PreToolUse": [
                    {"matcher": "Write", "hookCallbackIds": ["__hook_write__"]},
                    {"matcher": "Bash", "hookCallbackIds": ["__hook_bash__"]},
                ],
            }
            init_req = {
                "type": "control_request",
                "request_id": f"init_{uuid_mod.uuid4().hex[:8]}",
                "request": {"subtype": "initialize", "hooks": hooks_config},
            }
            init_line = json.dumps(init_req, ensure_ascii=False) + "\n"
            process.stdin.write(init_line.encode())
            await process.stdin.drain()
            logger.info("CLI: sent initialize")

            # ── Phase 2: wait for control_response ───────────────
            json_buf = ""
            initialized = False
            while not self._cancel_event.is_set():
                line = await asyncio.wait_for(
                    process.stdout.readline(), timeout=60
                )
                if not line:
                    logger.error("CLI exited before initialize response (returncode=%d)", process.returncode)
                    raise RuntimeError("CLI exited before initialize response")

                line_str = line.decode(errors="replace").strip()
                if not line_str:
                    continue
                json_buf += line_str
                try:
                    data = json.loads(json_buf)
                    json_buf = ""
                except json.JSONDecodeError:
                    continue

                if data.get("type") == "control_response":
                    subtype = data.get("response", {}).get("subtype", "")
                    logger.info("CLI init response: %s", subtype)
                    if subtype == "error":
                        err = data.get("response", {}).get("error", "unknown")
                        raise RuntimeError(f"CLI init error: {err}")
                    initialized = True
                    break

                logger.debug("CLI pre-init: type=%s", data.get("type"))

            if not initialized:
                self._event_queue.put(("cancelled", None))
                return

            # ── Phase 3: send user message ───────────────────────
            user_msg = json.dumps(
                {
                    "type": "user",
                    "message": {"role": "user", "content": self._prompt},
                    "session_id": self._session_id,
                },
                ensure_ascii=False,
            ) + "\n"
            process.stdin.write(user_msg.encode())
            await process.stdin.drain()
            logger.info("CLI: sent user message session=%s", self._session_id)

            # ── Phase 4: read & bridge messages ──────────────────
            hook_callbacks = {"__hook_write__": "Write", "__hook_bash__": "Bash"}

            while not self._cancel_event.is_set():
                line = await process.stdout.readline()
                if not line:
                    break

                line_str = line.decode(errors="replace").strip()
                if not line_str:
                    continue
                json_buf += line_str
                try:
                    data = json.loads(json_buf)
                    json_buf = ""
                except json.JSONDecodeError:
                    continue

                msg_type = data.get("type", "")

                if msg_type == "stream_event":
                    self._event_queue.put(("stream_event", data.get("event", {})))
                elif msg_type == "assistant":
                    self._assistant_sent = True
                    self._event_queue.put(("assistant", data))

                elif msg_type == "result":
                    logger.info(
                        "CLI: result event received is_error=%s result=%s keys=%s",
                        data.get("is_error"),
                        str(data.get("result", ""))[:80],
                        list(data.keys()),
                    )
                    if data.get("is_error"):
                        errors = data.get("errors", [])
                        logger.error("CLI result error: %s", errors)
                    if not self._assistant_sent:
                        result_text = data.get("result", "")
                        logger.info(
                            "CLI: fallback assistant from result (already_sent=%s text_len=%d)",
                            self._assistant_sent,
                            len(result_text),
                        )
                        if result_text:
                            self._assistant_sent = True
                            self._event_queue.put(("assistant", {
                                "type": "assistant",
                                "content": result_text,
                            }))
                            logger.info("CLI: fallback assistant queued (len=%d)", len(result_text))
                    break

                elif msg_type == "control_request":
                    # Hook callback from CLI
                    req = data.get("request", {})
                    req_id = data.get("request_id", "")
                    if req.get("subtype") == "hook_callback":
                        cid = req.get("hook_callback_id", "")
                        tool_name = hook_callbacks.get(cid, "")
                        tool_input = req.get("tool_input", {})

                        if tool_name == "Write":
                            new_input = _apply_write_path_hook(
                                tool_input, self._container_paths, self._session_id
                            )
                        elif tool_name == "Bash":
                            from src.security_filter import BashCommandFilter
                            cmd = tool_input.get("command", "")
                            allowed, reason = BashCommandFilter.check(cmd)
                            if not allowed:
                                result = {
                                    "subtype": "success",
                                    "request_id": req_id,
                                    "response": {
                                        "continue_": True,
                                        "hookSpecificOutput": {
                                            "hookEventName": "PreToolUse",
                                            "permissionDecision": "deny",
                                            "permissionDecisionReason": reason,
                                        },
                                    },
                                }
                                resp = {"type": "control_response", "response": result}
                                resp_line = json.dumps(resp, ensure_ascii=False) + "\n"
                                process.stdin.write(resp_line.encode())
                                await process.stdin.drain()
                                continue
                            new_input = _apply_bash_path_hook(
                                tool_input, self._container_paths
                            )
                        elif tool_name == "Read":
                            from src.security_filter import FileAccessFilter
                            file_path = tool_input.get("file_path", "")
                            allowed, reason = FileAccessFilter.check(file_path)
                            if not allowed:
                                result = {
                                    "subtype": "success",
                                    "request_id": req_id,
                                    "response": {
                                        "continue_": True,
                                        "hookSpecificOutput": {
                                            "hookEventName": "PreToolUse",
                                            "permissionDecision": "deny",
                                            "permissionDecisionReason": reason,
                                        },
                                    },
                                }
                                resp = {"type": "control_response", "response": result}
                                resp_line = json.dumps(resp, ensure_ascii=False) + "\n"
                                process.stdin.write(resp_line.encode())
                                await process.stdin.drain()
                                continue
                            new_input = tool_input
                        else:
                            new_input = tool_input

                        if new_input != tool_input:
                            result = {
                                "subtype": "success",
                                "request_id": req_id,
                                "response": {
                                    "continue_": True,
                                    "hookSpecificOutput": {
                                        "hookEventName": "PreToolUse",
                                        "updatedInput": new_input,
                                    },
                                },
                            }
                        else:
                            result = {
                                "subtype": "success",
                                "request_id": req_id,
                                "response": {
                                    "continue_": True,
                                },
                            }
                    else:
                        result = {
                            "subtype": "success",
                            "request_id": req_id,
                        }

                    resp = {"type": "control_response", "response": result}
                    resp_line = json.dumps(resp, ensure_ascii=False) + "\n"
                    process.stdin.write(resp_line.encode())
                    await process.stdin.drain()

                elif msg_type == "system":
                    subtype = data.get("subtype", "")
                    if subtype == "init":
                        logger.info("CLI: system init")

        except Exception as exc:
            logger.exception("CLI thread error: %s", exc)
            self._event_queue.put(("exception", exc))
        finally:
            stderr_task.cancel()
            with suppress(Exception):
                process.stdin.close()
            try:
                process.send_signal(signal.SIGTERM)
                await asyncio.wait_for(process.wait(), timeout=5)
            except (TimeoutError, ProcessLookupError):
                with suppress(ProcessLookupError):
                    process.kill()
                await process.wait()
            logger.info("CLI: terminated pid=%d", process.pid)
            self._event_queue.put(("done", None))


# ── WebSocket endpoint ──────────────────────────────────────────────


@app.websocket("/ws")
async def agent_ws(websocket: WebSocket) -> None:
    await websocket.accept()
    logger.info("Agent WS connected")

    container_paths = ContainerPaths(workspace=WORKSPACE, home_dir=HOME_DIR)
    runner: _CliRunner | None = None
    loop = asyncio.get_event_loop()

    async def _ws_send(data: dict) -> None:
        with suppress(Exception):
            await websocket.send_json(data)

    try:
        while True:
            raw = await websocket.receive_text()
            msg = json.loads(raw)
            msg_type = msg.get("type", "")

            if msg_type == "cancel":
                if runner:
                    runner.cancel()
                await _ws_send({"type": "cancelled"})
                continue

            if msg_type != "run":
                continue

            prompt = msg.get("prompt", "")
            session_id = msg.get("session_id", "")
            options_dict = msg.get("options", {})

            logger.info(
                "Run: session=%s model=%s max_turns=%s cwd=%s",
                session_id,
                options_dict.get("model", "?"),
                options_dict.get("max_turns", "?"),
                options_dict.get("cwd", "?"),
            )
            logger.info(
                "Agent env: WORKSPACE=%s AUTH_SET=%s BASE_URL=%s",
                WORKSPACE,
                bool(
                    os.getenv("ANTHROPIC_AUTH_TOKEN")
                    or os.getenv("ANTHROPIC_API_KEY")
                ),
                os.getenv("ANTHROPIC_BASE_URL", "default"),
            )

            # Check if system prompt is long — write to temp file
            sp_file: str | None = None
            sp_text = options_dict.get("system_prompt")
            if sp_text and isinstance(sp_text, str) and len(sp_text) > _SYSTEM_PROMPT_FILE_THRESHOLD:
                fd, sp_file = tempfile.mkstemp(suffix=".txt", prefix="sp_")
                os.write(fd, sp_text.encode())
                os.close(fd)
                logger.info("Wrote system prompt to temp file (%d chars)", len(sp_text))

            # Start CLI in a dedicated thread
            runner = _CliRunner(
                cmd=_build_cli_command(options_dict, sp_file=sp_file),
                env=_build_cli_env(options_dict),
                cwd=options_dict.get("cwd"),
                prompt=prompt,
                session_id=session_id,
                container_paths=container_paths,
                sp_file=sp_file,
            )
            runner.start()

            # Pump events from the CLI thread to the WebSocket
            done_event = asyncio.Event()

            async def _pump_events(
                _runner: _CliRunner = runner,
                _done: asyncio.Event = done_event,
            ) -> None:
                while True:
                    evt_type, evt_data = await loop.run_in_executor(
                        None, _runner.event_queue.get
                    )
                    if evt_type == "done":
                        await _ws_send({"type": "done"})
                        _done.set()
                        return
                    elif evt_type == "stream_event":
                        await _ws_send(
                            {"type": "stream_event", "event": evt_data}
                        )
                    elif evt_type == "assistant":
                        # Forward the full assistant message including
                        # thinking, tool_use, and text blocks.
                        message = evt_data.get("message", {})
                        if message:
                            # Scan text blocks for sensitive content
                            content_blocks = message.get("content", [])
                            if isinstance(content_blocks, list):
                                for block in content_blocks:
                                    if isinstance(block, dict) and block.get("type") == "text":
                                        block["text"] = OutputFilter.scan(block.get("text", ""))
                            await _ws_send({
                                "type": "assistant",
                                "message": message,
                            })
                        else:
                            # CLI fallback: bare text (from result fallback path)
                            assistant_content = OutputFilter.scan(evt_data.get("content", ""))
                            if assistant_content:
                                await _ws_send({
                                    "type": "assistant",
                                    "content": assistant_content,
                                })
                    elif evt_type == "stderr":
                        logger.info("CLI stderr: %s", evt_data)
                    elif evt_type == "exception":
                        await _ws_send(
                            {"type": "error", "message": str(evt_data)}
                        )
                        _done.set()
                        return
                    elif evt_type == "cancelled":
                        await _ws_send({"type": "cancelled"})
                        _done.set()
                        return

            pump_task = asyncio.create_task(_pump_events())

            # Read WebSocket messages (cancel) while CLI runs
            while not done_event.is_set():
                try:
                    raw_ws = await asyncio.wait_for(
                        websocket.receive_text(), timeout=0.1
                    )
                except TimeoutError:
                    continue
                except WebSocketDisconnect:
                    runner.cancel()
                    break

                msg_ws = json.loads(raw_ws)
                if msg_ws.get("type") == "cancel":
                    runner.cancel()

            if not pump_task.done():
                runner.cancel()
                try:
                    await asyncio.wait_for(pump_task, timeout=5)
                except TimeoutError:
                    pump_task.cancel()

            runner = None
            break  # one run per WebSocket connection

    except WebSocketDisconnect:
        logger.info("Agent WS disconnected")
    except RuntimeError:
        # Starlette raises RuntimeError when receive_text is called after
        # the client has already disconnected — this is normal cleanup.
        logger.debug("Agent WS disconnected (RuntimeError)")
    except Exception:
        logger.exception("Agent WS fatal error")
        with suppress(Exception):
            await _ws_send(
                {
                    "type": "error",
                    "message": "Agent server internal error — check container logs for details",
                }
            )
