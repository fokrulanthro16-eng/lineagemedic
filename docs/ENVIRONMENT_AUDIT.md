# Environment Audit — Phase 0

**Audit date:** 2026-07-28
**Machine:** Windows developer workstation (local, single-node)
**Auditor:** LineageMedic Phase 0 automated audit
**Secrets:** none recorded in this file. No tokens, passwords, or personal identifiers are captured by the audit commands below.

---

## 1. Detected values

| Item | Detected value | Required baseline | Status |
| --- | --- | --- | --- |
| Windows version | Microsoft Windows 11 Home 10.0.26200 (Build 26200) | Windows 10/11 x64 | **PASS** |
| Total RAM | 15.79 GB | 8 GB min, 16 GB recommended for DataHub Quickstart | **PASS** |
| Free RAM (at audit time) | 6.96 GB | ≥ 6 GB free before starting Quickstart | **PASS (tight)** |
| Free disk (C:) | 189.86 GB | ≥ 20 GB for DataHub images + volumes | **PASS** |
| Git | git version 2.54.0.windows.1 | any 2.x | **PASS** |
| GitHub CLI (`gh`) | not installed | optional | **WARN (optional)** |
| Python (default) | Python 3.14.2 | 3.10+ | **PASS**, but see note below |
| Python 3.11 | Python 3.11.9 (`py -3.11`) | 3.10–3.11 for DataHub CLI | **PASS** |
| Node.js | v22.22.0 | 18+ | **PASS** |
| npm | 10.9.4 | 9+ | **PASS** |
| Docker Desktop / Engine | **not installed** | required | **FAIL — BLOCKER** |
| Docker Compose v2 | **not available** (ships with Docker Desktop) | required | **FAIL — BLOCKER** |
| `uv` / `uvx` | not installed | optional (nice-to-have) | **WARN (optional)** |
| WSL 2 | installed, default version 2, **no distribution installed** | required by Docker Desktop WSL2 backend | **WARN** |
| Hardware virtualization | `HypervisorPresent = True` | required | **PASS** |
| `winget` | available at `%LOCALAPPDATA%\Microsoft\WindowsApps\winget.exe` | used for remediation | **PASS** |
| Port 8000 (LineageMedic API) | FREE | free | **PASS** |
| Port 5173 (Vite dev server) | FREE | free | **PASS** |
| Port 8080 (DataHub GMS) | FREE | free | **PASS** |
| Port 9002 (DataHub Frontend UI) | FREE | free | **PASS** |

### Note on Python version

The system default interpreter is **Python 3.14.2**. The DataHub CLI (`acryl-datahub`) and much of its
dependency tree do not yet publish wheels for 3.14. LineageMedic therefore pins its virtual
environments to **Python 3.11.9**, which is already installed and selectable via the launcher:

```powershell
py -3.11 --version   # -> Python 3.11.9
```

All project setup scripts use `py -3.11` explicitly rather than bare `python`.

---

## 2. Blocking issue

### Docker Desktop is not installed — DataHub Quickstart cannot run

Verification performed (all negative):

```powershell
Get-Command docker                                    # no result
Test-Path "C:\Program Files\Docker\Docker\Docker Desktop.exe"   # False
Get-Service com.docker.service                        # no such service
# registry uninstall keys searched for "*Docker*"     # no entries
```

DataHub OSS Quickstart is distributed exclusively as a Docker Compose stack. Without a container
runtime there is no way to bring up GMS (port 8080), the frontend (port 9002), Elasticsearch, MySQL,
or Kafka. This is a **GUI-only installation requirement**: Docker Desktop's installer and its first
run require interactive acceptance of the license agreement and, on Windows Home, a WSL2 backend
setup step that cannot be completed headlessly.

---

## 3. Remediation commands

### 3.1 Required — install Docker Desktop (one-time, interactive)

```powershell
winget install --id Docker.DockerDesktop --accept-package-agreements --accept-source-agreements
```

Then **launch Docker Desktop from the Start Menu once** and accept the license agreement. Docker
Desktop will install its own WSL2 backend distribution automatically (no separate distro needed).

Verification command to run afterwards:

```powershell
docker compose version; docker run --rm hello-world
```

Expected: a Compose `v2.x.x` version string, followed by the `hello-world` container printing
"Hello from Docker!".

### 3.2 Optional — GitHub CLI

```powershell
winget install --id GitHub.cli --accept-package-agreements --accept-source-agreements
gh --version
```

Only needed for creating the public GitHub repository from the terminal. The repository can also be
created through the GitHub web UI and added with `git remote add origin <url>`.

### 3.3 Optional — `uv`

```powershell
winget install --id astral-sh.uv --accept-package-agreements --accept-source-agreements
uv --version
```

LineageMedic's setup scripts use the standard library `venv` + `pip` and do **not** require `uv`.

### 3.4 Free-RAM guidance before running DataHub

DataHub Quickstart runs Elasticsearch, MySQL, Kafka, GMS, and the frontend. With 15.79 GB total and
~6.96 GB free at audit time, close memory-heavy applications (browsers, IDE extra windows) before
`datahub docker quickstart`. Check available memory with:

```powershell
[math]::Round((Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory/1MB,2)
```

Aim for ≥ 8 GB free.

---

## 4. Baseline verdict

| Baseline requirement | Verdict |
| --- | --- |
| Python 3.10+ | **PASS** (3.11.9 pinned; 3.14.2 also present) |
| Docker with Compose v2 | **FAIL** — Docker Desktop not installed |
| Sufficient resources for DataHub Quickstart | **PASS** (16 GB RAM, 190 GB free disk, VT-x enabled) |
| Required ports free (8000 / 5173 / 8080 / 9002) | **PASS** |

**Overall: BLOCKED on Docker Desktop installation.** Every other baseline requirement is satisfied.

---

## 5. Reproducing this audit

```powershell
pwsh -File scripts\audit-environment.ps1
```

The script re-runs every check in this document and prints a pass/fail table. It reads no
credentials and writes no secrets.
