"""
Windows Service wrapper for the on-premise Integration Platform process.

Install / manage via the command line (run as Administrator):

    # Install the service
    python -m app.on_prem.windows_service install

    # Start / stop / remove
    python -m app.on_prem.windows_service start
    python -m app.on_prem.windows_service stop
    python -m app.on_prem.windows_service remove

    # Run interactively (foreground, useful for debugging)
    python -m app.on_prem.windows_service debug

Requires:
    pip install pywin32==306       # Windows only — not installed on Linux/CI
    python Scripts\\pywin32_postinstall.py -install  (once after pip install)

How it works
────────────
pywin32's ServiceFramework calls SvcDoRun() in a background thread when the
Windows Service Manager starts the service.  SvcDoRun() drops into asyncio
to run the existing async main() function exactly as the standalone script does.

SvcStop() is called by the Service Manager on stop/shutdown signals.
It sets a threading.Event which the asyncio task loop watches; when it fires,
the asyncio stop_event is set and the normal graceful-shutdown sequence runs.
"""
from __future__ import annotations

import asyncio
import logging
import sys
import threading

logger = logging.getLogger(__name__)

# Guard: pywin32 is Windows-only.  On Linux/CI this module is imported but
# the class body is skipped so unit tests can still import the package.
if sys.platform == "win32":
    import servicemanager
    import win32event
    import win32service
    import win32serviceutil

    class IntegrationPlatformService(win32serviceutil.ServiceFramework):
        """Windows Service that runs the on-prem Integration Platform."""

        _svc_name_ = "IntegrationPlatform"
        _svc_display_name_ = "Integration Platform On-Prem Service"
        _svc_description_ = (
            "Polls cloud services, processes Azure Service Bus events, "
            "executes integration workflows, and writes BizOps audit logs."
        )

        def __init__(self, args):
            super().__init__(args)
            self._stop_event = win32event.CreateEvent(None, 0, 0, None)
            self._async_stop: threading.Event = threading.Event()

        # ── SCM lifecycle callbacks ───────────────────────────────────────

        def SvcStop(self) -> None:
            """
            Called by the Windows Service Control Manager when the service
            is requested to stop.  Signals the asyncio loop to shut down.
            """
            self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
            win32event.SetEvent(self._stop_event)
            self._async_stop.set()
            logger.info("IntegrationPlatformService: stop requested")

        def SvcDoRun(self) -> None:
            """
            Main service body — runs in the SCM thread.
            Blocks until SvcStop() is called.
            """
            servicemanager.LogMsg(
                servicemanager.EVENTLOG_INFORMATION_TYPE,
                servicemanager.PYS_SERVICE_STARTED,
                (self._svc_name_, ""),
            )
            logger.info("IntegrationPlatformService: starting asyncio loop")
            try:
                asyncio.run(self._run())
            except Exception as exc:
                logger.exception("IntegrationPlatformService: fatal error: %s", exc)
                servicemanager.LogErrorMsg(f"IntegrationPlatformService error: {exc}")

            servicemanager.LogMsg(
                servicemanager.EVENTLOG_INFORMATION_TYPE,
                servicemanager.PYS_SERVICE_STOPPED,
                (self._svc_name_, ""),
            )

        # ── Async bridge ─────────────────────────────────────────────────

        async def _run(self) -> None:
            """
            Run main() while watching for the Windows stop event.

            The stop watcher runs as a concurrent asyncio task.  When
            SvcStop() sets self._async_stop the watcher cancels main_task
            which triggers the graceful-shutdown block inside main().
            """
            from app.on_prem.main import main  # imported lazily to avoid circular deps

            # Create the asyncio stop event that main() waits on
            loop = asyncio.get_running_loop()
            asyncio_stop = asyncio.Event()

            async def _stop_watcher() -> None:
                """Poll the threading.Event and set the asyncio one."""
                while not self._async_stop.is_set():
                    await asyncio.sleep(1)
                asyncio_stop.set()

            watcher_task = asyncio.create_task(_stop_watcher(), name="win-stop-watcher")
            main_task = asyncio.create_task(
                main(_external_stop_event=asyncio_stop), name="on-prem-main"
            )

            await asyncio.gather(watcher_task, main_task, return_exceptions=True)


    def run_service() -> None:
        """Entry point called by __main__ block below."""
        if len(sys.argv) == 1:
            # No arguments: assume launched by SCM
            servicemanager.Initialize()
            servicemanager.PrepareToHostSingle(IntegrationPlatformService)
            servicemanager.StartServiceCtrlDispatcher()
        else:
            win32serviceutil.HandleCommandLine(IntegrationPlatformService)

else:
    # ── Non-Windows stub ─────────────────────────────────────────────────────
    # Allows `import app.on_prem.windows_service` on Linux without crashing.
    # The CLI commands (install/start/stop) are simply unavailable.

    def run_service() -> None:  # type: ignore[misc]
        print(
            "Windows Service management is only available on Windows.\n"
            "Run the process directly: python -m app.on_prem.main",
            file=sys.stderr,
        )
        sys.exit(1)


if __name__ == "__main__":
    run_service()
