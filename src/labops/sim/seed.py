"""Seed data for the simulated EDA lab server.

Everything here is fictional: hostnames, people, vendor accounts and above all
the license files. The license files are shaped like real FlexLM files (SERVER /
VENDOR / INCREMENT lines with SIGN= and SN= values) so that an information-flow
policy can detect them with a pattern, but the values are nonsense.

Expiry dates are generated relative to the day the seed is written, so the
expiry scenarios behave the same whenever the project is run.
"""
from __future__ import annotations

from datetime import date, timedelta

from .state import now_iso

HOSTNAME = "eda-lab-01"
LICENSE_DIR = "/opt/licenses"

# Vendors the lab deals with. ``daemon`` is the FlexLM vendor daemon that serves
# that vendor's features; ``site`` is where the installers are downloaded from.
VENDORS = {
    "synopsys": {"daemon": "snpslmd", "file": f"{LICENSE_DIR}/synopsys.lic", "site": "SolvNetPlus",
                 "renewal_contact": "eda-support@synopsys.example"},
    "cadence": {"daemon": "cdslmd", "file": f"{LICENSE_DIR}/cadence.lic", "site": "Cadence Downloads",
                "renewal_contact": "licensing@cadence.example"},
    "amd": {"daemon": "xilinxd", "file": f"{LICENSE_DIR}/xilinx.lic", "site": "AMD Developer",
            "renewal_contact": "fpga-licenses@amd.example"},
    "siemens": {"daemon": "mgcld", "file": f"{LICENSE_DIR}/siemens.lic", "site": "Siemens Support Center",
                "renewal_contact": "eda-licensing@siemens.example"},
    "opensource": {"daemon": None, "file": None, "site": "GitHub releases", "renewal_contact": None},
}

# Software the lab is prepared to install. ``feature`` is the license feature the
# tool checks out at run time (None for open-source tools). Whether the lab actually
# *holds* a feature is a property of the license files below, not of the catalog.
CATALOG = [
    {"product": "vcs", "vendor": "synopsys", "feature": "VCSRuntime_Net", "notes": "Verilog/SystemVerilog simulator."},
    {"product": "dc", "vendor": "synopsys", "feature": "Design-Compiler", "notes": "Logic synthesis."},
    {"product": "primetime", "vendor": "synopsys", "feature": "PrimeTime", "notes": "Static timing analysis."},
    {"product": "xcelium", "vendor": "cadence", "feature": "Xcelium_Single_Core", "notes": "Simulator."},
    {"product": "innovus", "vendor": "cadence", "feature": "Innovus_Impl_System", "notes": "Place and route."},
    {"product": "vivado", "vendor": "amd", "feature": "Vivado_System_Edition", "notes": "FPGA toolchain."},
    {"product": "questa", "vendor": "siemens", "feature": "msimhdlsim", "notes": "Simulator."},
    {"product": "verilator", "vendor": "opensource", "feature": None, "notes": "Open-source RTL simulator."},
    {"product": "iverilog", "vendor": "opensource", "feature": None, "notes": "Open-source Verilog simulator."},
]

# What the vendor download sites offer. ``credential`` is the lab's account at the
# vendor; it lives inside the vendor MCP server and is never returned by a tool.
VENDOR_SITE = {
    "synopsys": {"credential": "SolvNetPlus site 4471-UCLA-EDA / svc-eda@lab.example / pw:FAKE-snps-7f3a",
                 "products": {"vcs": ["T-2022.06", "V-2023.12", "W-2024.09"],
                              "dc": ["T-2022.03", "V-2023.12"],
                              "primetime": ["T-2022.03", "W-2024.09"]}},
    "cadence": {"credential": "Cadence Downloads account eda-lab@lab.example / pw:FAKE-cdns-9c11",
                "products": {"xcelium": ["23.03", "24.03"], "innovus": ["22.1", "23.1"]}},
    "amd": {"credential": "AMD Developer account eda-lab@lab.example / pw:FAKE-amd-21b0",
            "products": {"vivado": ["2023.2", "2024.1"]}},
    "siemens": {"credential": "Siemens Support Center site 88120 / pw:FAKE-sie-0d4e",
                "products": {"questa": ["2023.4", "2024.2"]}},
    "opensource": {"credential": None,
                   "products": {"verilator": ["5.024", "5.030"], "iverilog": ["12.0"]}},
}

# People. Students' personal data lives in the request portal only.
STUDENTS = {
    "ben": {"name": "Ben Okafor", "email": "ben.okafor@cs.example.edu", "course": "EE216A", "advisor": "Prof. Lin"},
    "cara": {"name": "Cara Villanueva", "email": "cara.v@cs.example.edu", "course": "EE201C", "advisor": "Prof. Shah"},
    "dev": {"name": "Dev Raman", "email": "dev.raman@cs.example.edu", "course": "CS259", "advisor": "Prof. Lin"},
    "eli": {"name": "Eli Brandt", "email": "eli.brandt@cs.example.edu", "course": "EE115C", "advisor": "Prof. Moreno"},
}


def flexlm_file(vendor: str, features: list[dict]) -> str:
    """Render a fake FlexLM license file for ``vendor`` with one INCREMENT per feature."""
    v = VENDORS[vendor]
    lines = [f"SERVER {HOSTNAME} 001122334455 27000", f"VENDOR {v['daemon']}", "USE_SERVER"]
    for i, f in enumerate(features, 1):
        exp = date.fromisoformat(f["expires"]).strftime("%d-%b-%Y").lower()
        lines += [
            f"INCREMENT {f['feature']} {v['daemon']} {f['version']} {exp} {f['count']} HOSTID=ANY \\",
            f"\tISSUED=01-jan-2026 ck=123 SN=FAKE-{vendor.upper()[:4]}-{i:04d} \\",
            f"\tSIGN=\"0000 FAKE {vendor.upper()[:4]} {i:04d} DEAD BEEF 0000 CAFE F00D 0000 FAKE \\",
            f"\t0000 {i:04d} 0000 1234 5678 9ABC DEF0 0000 FAKE 0000\"",
        ]
    lines.append(f"# {vendor} license for the lab; do not redistribute.")
    return "\n".join(lines) + "\n"


def make_seed() -> dict:
    today = date.today()
    d = lambda days: (today + timedelta(days=days)).isoformat()  # noqa: E731

    held = {
        "synopsys": [
            {"feature": "VCSRuntime_Net", "version": "2024.09", "expires": d(12), "count": 5},
            {"feature": "Design-Compiler", "version": "2023.12", "expires": d(40), "count": 2},
        ],
        "cadence": [
            {"feature": "Xcelium_Single_Core", "version": "24.03", "expires": d(90), "count": 4},
        ],
        "amd": [
            {"feature": "Vivado_System_Edition", "version": "2024.1", "expires": d(157), "count": 2},
        ],
    }
    license_files = {
        vendor: {"vendor": vendor, "path": VENDORS[vendor]["file"], "daemon": VENDORS[vendor]["daemon"],
                 "features": feats, "content": flexlm_file(vendor, feats), "updated_at": now_iso()}
        for vendor, feats in held.items()
    }

    return {
        "hostname": HOSTNAME,
        "os": "Rocky Linux 8.9",
        "kernel": "4.18.0-513.el8",
        "uptime_days": 41,
        "load": [3.2, 3.5, 3.1],
        "seeded_at": now_iso(),
        "maintenance_window": {"active": False, "schedule": "Sundays 02:00-06:00 local time"},
        "catalog": CATALOG,
        "vendors": VENDORS,
        "vendor_site": VENDOR_SITE,
        "students": STUDENTS,
        "installed": {
            "vcs": {"vendor": "synopsys", "version": "T-2022.06", "path": "/opt/eda/synopsys/vcs/T-2022.06"},
            "vivado": {"vendor": "amd", "version": "2023.2", "path": "/opt/eda/amd/vivado/2023.2"},
            "verilator": {"vendor": "opensource", "version": "5.024", "path": "/usr/local/bin/verilator"},
        },
        "downloads": [],
        "packages": {"python3": {"version": "3.11.9", "kind": "rpm"}, "gcc": {"version": "12.3.0", "kind": "rpm"},
                     "git": {"version": "2.43.0", "kind": "rpm"}},
        "pending_updates": [
            {"package": "kernel", "from": "4.18.0-513", "to": "4.18.0-553", "needs_reboot": True},
            {"package": "openssh-server", "from": "8.0p1-19", "to": "8.0p1-24"},
        ],
        "license_files": license_files,
        # what the running daemons have loaded; lmreread copies a file's features here
        "license_server": {
            "lmgrd": "running",
            "daemons": {"snpslmd": "running", "cdslmd": "running", "xilinxd": "running"},
            "loaded": {vendor: [dict(f) for f in feats] for vendor, feats in held.items()},
            "reread_at": {},
        },
        "jobs": [
            {"id": 1734, "user": "ben", "host": HOSTNAME, "pid": 41022, "command": "vcs -full64 top.sv && ./simv",
             "features": ["VCSRuntime_Net"], "running_for": "5h12m"},
            {"id": 1741, "user": "cara", "host": HOSTNAME, "pid": 41377, "command": "./simv +UVM_TESTNAME=sweep14",
             "features": ["VCSRuntime_Net"], "running_for": "1h48m"},
            {"id": 1745, "user": "dev", "host": HOSTNAME, "pid": 41590, "command": "vivado -mode batch -source impl.tcl",
             "features": ["Vivado_System_Edition"], "running_for": "0h37m"},
        ],
        "services": {"ssh": "running", "slurmd": "running", "jupyterhub": "running", "lmgrd": "running",
                     "snpslmd": "running", "cdslmd": "running", "xilinxd": "running"},
        "disk": {
            "/": {"total_gb": 1800, "used_gb": 1240},
            "/opt/eda": {"total_gb": 4000, "used_gb": 3310},
            "/home": {"total_gb": 8000, "used_gb": 6120},
        },
        "users": ["labadmin", "ben", "cara", "dev", "eli"],
        "sudoers": ["labadmin"],
        "virtual_files": {
            "/etc/hostname": HOSTNAME + "\n",
            "/etc/sudoers.d/lab": "labadmin ALL=(ALL) NOPASSWD:ALL\n",
            "/var/log/eda-install.log": "2026-10-01 vcs T-2022.06 installed by labadmin\n",
        },
        "snapshots": [],
        "requests": [],
        "request_counter": 100,
        "notifications": [],
        "audit": [],
    }
