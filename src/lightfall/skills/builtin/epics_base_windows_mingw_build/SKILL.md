---
name: epics-base-windows-mingw-build
description: "Build and install EPICS base from source on a Windows machine using Strawberry Perl (MinGW gcc + gmake), with no Visual Studio. Use when a Windows host needs EPICS base (caget/caput/pvget/softIoc, or a base to build IOCs/modules against) and no prebuilt download exists for the wanted version. Covers toolchain install, build, smoke test, and environment setup."
---
# Build EPICS base on Windows with Strawberry Perl (MinGW)

## When this applies
- Windows 10/11 x64 host that needs EPICS base, and there is no prebuilt binary for the wanted version.
- You want the light toolchain: about 300 MB, one installer, no Visual Studio.
- Validated on 2026-10-29: EPICS base 7.0.10 on Windows 11 Pro 26200, Strawberry Perl 5.42.3.1 (gcc 13.2.0 MinGW-W64 ucrt, gmake). On a 24-core host the build finished cleanly in a few minutes with `-j16`.

### Decide the toolchain first. It is the user's call.
- **MinGW (this skill):** EPICS target `windows-x64-mingw`. Everything built later against this base (IOCs, asyn, motor, areaDetector, ...) must also be built with MinGW. MSVC-built libraries will not link against it.
- **MSVC:** EPICS target `windows-x64`. Needs VS Build Tools (C++ workload, about 5 GB) plus Perl and gmake. It is more common at facilities and better for compatibility with prebuilt Windows modules. Ask the user if this choice matters.
- Note: pyepics/caproto do not need a local EPICS base. pyepics bundles its own libca.

Reference: https://docs.epics-controls.org/en/latest/getting-started/installation-windows.html (an overview only: it lists the needed tools and warns that paths must not contain spaces or parentheses).

## Steps (PowerShell)

1. **Check what already exists.**
   ```powershell
   foreach ($t in 'perl','gmake','gcc','cl') { "$t -> $((Get-Command $t -EA SilentlyContinue).Source)" }
   Test-Path C:\epics
   ```

2. **Install the toolchain.** Strawberry Perl installs to `C:\Strawberry` and adds itself to the machine PATH.
   ```powershell
   winget install --id StrawberryPerl.StrawberryPerl -e --silent --accept-package-agreements --accept-source-agreements
   ```
   The current shell will not see the new PATH yet. Prepend it for this session:
   ```powershell
   $env:Path = "C:\Strawberry\c\bin;C:\Strawberry\perl\site\bin;C:\Strawberry\perl\bin;" + $env:Path
   ```
   Check that `perl`, `gcc`, and `gmake` all resolve. There is NO `make.exe`, only `gmake.exe` and `mingw32-make.exe`.

3. **Download and extract** into a path with no spaces. Replace `<VER>` (e.g. 7.0.10).
   ```powershell
   New-Item -ItemType Directory -Force C:\epics\_download | Out-Null
   curl.exe -L --fail -o C:\epics\_download\base-<VER>.tar.gz https://epics.anl.gov/download/base/base-<VER>.tar.gz
   tar -xzf C:\epics\_download\base-<VER>.tar.gz -C C:\epics   # creates C:\epics\base-<VER>
   ```

4. **Build.** No config edits are needed. Set the host arch and run gmake:
   ```powershell
   $env:EPICS_HOST_ARCH = "windows-x64-mingw"
   gmake -C C:\epics\base-<VER> -j16 *> C:\epics\_download\build.log
   $LASTEXITCODE   # expect 0
   ```
   Binaries end up in `C:\epics\base-<VER>\bin\windows-x64-mingw` (about 26 exes).

5. **Smoke test** with a headless soft IOC.
   ```powershell
   $bin = "C:\epics\base-<VER>\bin\windows-x64-mingw"; $env:Path = "$bin;" + $env:Path
   $db = "$env:TEMP\t.db"; $in = "$env:TEMP\empty.txt"
   'record(ai, "test:ai") { field(VAL, "42.5") }' | Set-Content -Encoding ascii $db
   New-Item -ItemType File -Force $in | Out-Null
   $p = Start-Process "$bin\softIocPVA.exe" -ArgumentList "-S","-d",$db -PassThru -WindowStyle Hidden -RedirectStandardInput $in -RedirectStandardOutput "$env:TEMP\ioc.out"
   Start-Sleep 3
   caget test:ai; caput test:ai 7.25; pvget test:ai
   Stop-Process $p.Id -Force; Start-Sleep 1; Remove-Item $db,$in,"$env:TEMP\ioc.out"
   ```
   Pass condition: caget returns 42.5, caput shows Old 42.5 / New 7.25, and pvget returns 7.25.

6. **Set persistent user environment variables.** New shells pick these up; already-open apps must be restarted.
   ```powershell
   [Environment]::SetEnvironmentVariable("EPICS_BASE", "C:\epics\base-<VER>", "User")
   [Environment]::SetEnvironmentVariable("EPICS_HOST_ARCH", "windows-x64-mingw", "User")
   $bin = "C:\epics\base-<VER>\bin\windows-x64-mingw"
   $up = [Environment]::GetEnvironmentVariable("Path","User")
   if (($up -split ';') -notcontains $bin) { [Environment]::SetEnvironmentVariable("Path", $up.TrimEnd(';') + ";$bin", "User") }
   ```
   Optionally delete the tarball. Keep `build.log` for reference.

## Pitfalls seen in practice
- **`Start-Process -RedirectStandardInput NUL` fails** in Windows PowerShell 5.1, because it resolves `NUL` as a relative file. The IOC never starts, so caget times out with "not found". Use an empty temp file instead, as in step 5.
- **Harmless crash-looking message:** with `-S` and stdin at EOF, softIoc prints `epicsThreadExitMain() must no longer be used. CRITICAL ERROR ... can't proceed, suspending` plus a stack trace. Only the main thread parks; the IOC keeps serving CA and PVA. This is not a build failure.
- The empty stdin file stays locked until the IOC process is fully gone. Wait about 1 s after `Stop-Process` before deleting it.
- Do not build under a path containing spaces or parentheses. The make-based build breaks.
- The PATH change from the Strawberry installer does not reach the current shell. Prepend it manually (step 2).

## Verification evidence
On 2026-10-29, EPICS base 7.0.10 built with exit code 0 at `C:\epics\base-7.0.10`. The step 5 smoke test returned `test:ai 42.5`, then `Old 42.5 / New 7.25` from caput, then pvget `7.25`.
