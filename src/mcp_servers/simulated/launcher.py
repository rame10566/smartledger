"""
Simulated Systems Launcher

Starts all 10 simulated MCP servers in separate processes.
Used by the Docker container (mcp-simulated service) and local dev.

Servers:
  8010 — Oracle LOS
  8011 — Salesforce LOS
  8012 — LLAS
  8013 — CRM
  8014 — Payment
  8015 — Insurance
  8016 — Dealer
  8017 — Customer Portal
  8018 — Mobile App
  8019 — IVR
"""

import multiprocessing
import signal
import sys
import time


def _run_server(module_path: str, port: int) -> None:
    """
    Run a single FastMCP server on the given port.

    Imports the server module, overrides host/port on mcp.settings,
    then starts the server. Each call runs in its own subprocess so
    there is no cross-contamination between servers.
    """
    pkg = __import__(module_path, fromlist=["mcp"])
    mcp = pkg.mcp
    mcp.settings.host = "0.0.0.0"
    mcp.settings.port = port
    mcp.run(transport="streamable-http")


def _run_oracle_los()      -> None: _run_server("mcp_servers.simulated.oracle_los.server",      8010)
def _run_salesforce_los()  -> None: _run_server("mcp_servers.simulated.salesforce_los.server",  8011)
def _run_llas()            -> None: _run_server("mcp_servers.simulated.llas.server",            8012)
def _run_crm()             -> None: _run_server("mcp_servers.simulated.crm.server",             8013)
def _run_payment()         -> None: _run_server("mcp_servers.simulated.payment.server",         8014)
def _run_insurance()       -> None: _run_server("mcp_servers.simulated.insurance.server",       8015)
def _run_dealer()          -> None: _run_server("mcp_servers.simulated.dealer.server",          8016)
def _run_customer_portal() -> None: _run_server("mcp_servers.simulated.customer_portal.server", 8017)
def _run_mobile_app()      -> None: _run_server("mcp_servers.simulated.mobile_app.server",      8018)
def _run_ivr()             -> None: _run_server("mcp_servers.simulated.ivr.server",             8019)


_SERVERS = [
    ("oracle_los",      8010, _run_oracle_los),
    ("salesforce_los",  8011, _run_salesforce_los),
    ("llas",            8012, _run_llas),
    ("crm",             8013, _run_crm),
    ("payment",         8014, _run_payment),
    ("insurance",       8015, _run_insurance),
    ("dealer",          8016, _run_dealer),
    ("customer_portal", 8017, _run_customer_portal),
    ("mobile_app",      8018, _run_mobile_app),
    ("ivr",             8019, _run_ivr),
]


def main() -> None:
    print("── SmartLedger Simulated Systems Launcher ──────────────────────")
    processes: list[multiprocessing.Process] = []

    for name, port, target in _SERVERS:
        p = multiprocessing.Process(target=target, name=name, daemon=True)
        p.start()
        processes.append(p)
        print(f"  ↑ {name:<20} pid={p.pid}  port={port}")

    print("────────────────────────────────────────────────────────────────")
    print("  All simulated servers started. CTRL+C to stop.")

    def _shutdown(sig, frame):
        print("\n── Shutting down simulated servers...")
        for p in processes:
            p.terminate()
        for p in processes:
            p.join(timeout=5)
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    # Monitor: restart any process that dies unexpectedly
    while True:
        for i, (name, port, target) in enumerate(_SERVERS):
            p = processes[i]
            if not p.is_alive():
                print(f"  ⚠ {name} (port {port}) died — restarting...")
                new_p = multiprocessing.Process(target=target, name=name, daemon=True)
                new_p.start()
                processes[i] = new_p
                print(f"  ↑ {name:<20} pid={new_p.pid}  port={port} (restarted)")
        time.sleep(5)


if __name__ == "__main__":
    multiprocessing.set_start_method("spawn")
    main()
