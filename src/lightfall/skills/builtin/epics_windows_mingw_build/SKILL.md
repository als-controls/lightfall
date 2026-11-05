---
name: epics-windows-mingw-build
description: "Build EPICS on a Windows machine with Strawberry Perl (MinGW gcc + gmake), no Visual Studio: EPICS base, then the support modules seq/asyn/ipac/busy/motor from public sources, then the BCS LabVIEW motor bridge (motorPV / pvIOC.exe). Use when a Windows host needs EPICS base (caget/caput/pvget/softIoc), modules to build IOCs against, or a working BCS motor bridge, and no prebuilt download fits. Covers toolchain install, builds, known source patches, smoke tests, and environment setup."
---
# Build EPICS on Windows with Strawberry Perl (MinGW): base, modules, BCS motor bridge

## When this applies
- Windows 10/11 x64 host that needs EPICS base, and there is no prebuilt binary for the wanted version.
- You want the light toolchain: about 300 MB, one installer, no Visual Studio.
- The host also needs support modules (asyn, motor, ...), for example for the BCS LabVIEW motor bridge. Part 2 and Part 3 cover that. Some older installs pulled these modules from a facility SVN. That isn't needed: every module source in Part 2 is public, and motorPV is on the BCS controls share.
- Validated:
  - 2026-10-29: base 7.0.10 on Windows 11 Pro 26200, Strawberry Perl 5.42.3.1 (gcc 13.2.0 MinGW-W64 ucrt, gmake). On a 24-core host the build finished cleanly in a few minutes with `-j16`.
  - 2026-11-04: on Windows 10 Pro 19045 with the same toolchain, the full stack: base 7.0.10, seq 2.2.9, asyn R4-46, ipac 2.16, busy R1-7-4, motor R7-4 (+ motorMotorSim), and motorPV/pvIOC.

### Decide the toolchain first. It is the user's call.
- **MinGW (this skill):** EPICS target `windows-x64-mingw`. Everything built later against this base (IOCs, asyn, motor, areaDetector, ...) must also be built with MinGW. MSVC-built libraries will not link against it.
- **MSVC:** EPICS target `windows-x64`. Needs VS Build Tools (C++ workload, about 5 GB) plus Perl and gmake. It is more common at facilities and better for compatibility with prebuilt Windows modules. Ask the user if this choice matters.
- Note: pyepics/caproto do not need a local EPICS base. pyepics bundles its own libca.

Reference: https://docs.epics-controls.org/en/latest/getting-started/installation-windows.html (an overview only: it lists the needed tools and warns that paths must not contain spaces or parentheses).

## Part 1: EPICS base (PowerShell)

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
   $bin = "C:\epics\base-<VER>\bin\windows-x64-mingw"; $env:Path = "$bin;C:\Strawberry\c\bin;" + $env:Path
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

## Part 2: support modules seq / asyn / ipac / busy / motor

Session PATH for all of Part 2 and Part 3:
```powershell
$env:Path = "C:\epics\tools\re2c\bin;C:\Strawberry\c\bin;C:\Strawberry\perl\site\bin;C:\Strawberry\perl\bin;C:\epics\base-<VER>\bin\windows-x64-mingw;" + $env:Path
$env:EPICS_HOST_ARCH = "windows-x64-mingw"
```

1. **Sources.** These are all public. Clone each with `git clone --depth 1 --branch <tag> <repo> C:\epics\modules\<dir>`, and find newer tags with `git ls-remote --tags --refs --sort=-v:refname <repo>`.

   | dir | repo | tag |
   |---|---|---|
   | seq-2-2-9 | https://github.com/mdavidsaver/sequencer-mirror | R2-2-9 (`epics-modules/sequencer` stops at 2.2.1; the BESSY release site may be unreachable) |
   | asyn-R4-46 | https://github.com/epics-modules/asyn | R4-46 |
   | ipac-2-16 | https://github.com/epics-modules/ipac | 2.16 |
   | busy-R1-7-4 | https://github.com/epics-modules/busy | R1-7-4 |
   | motor-R7-4 | https://github.com/epics-modules/motor | R7-4. Do NOT recurse submodules; add drivers one at a time (step 7). |

2. **re2c (seq needs it; Strawberry doesn't ship it, and re2c has no Windows binary release).** Build it from source with the same toolchain:
   - Download `https://github.com/skvadrik/re2c/releases/download/4.6/re2c-4.6.tar.xz`. Extract it with **Git for Windows' tar** (`tar -xJf` from Git Bash), because Windows' built-in tar.exe fails with `unable to run program "xz -d -qq"`.
   - Configure: `cmake -S re2c-4.6 -B build -G "MinGW Makefiles" -DCMAKE_BUILD_TYPE=Release -DRE2C_BUILD_LIBS=OFF -DRE2C_BUILD_TESTS=OFF`. Also add `-DRE2C_BUILD_RE2GO=OFF` and likewise for RE2RUST/RE2D/RE2HS/RE2JAVA/RE2JS/RE2OCAML/RE2PY/RE2V/RE2ZIG.
   - Build: `cmake --build build -j<N> --target re2c`, then copy `re2c.exe` to `C:\epics\tools\re2c\bin`.

3. **One shared `C:\epics\modules\RELEASE.local`.** Every module includes `$(TOP)/../RELEASE.local`, so a single file configures them all. Use forward slashes:
   ```
   SUPPORT=C:/epics/modules
   SNCSEQ=$(SUPPORT)/seq-2-2-9
   ASYN=$(SUPPORT)/asyn-R4-46
   IPAC=$(SUPPORT)/ipac-2-16
   BUSY=$(SUPPORT)/busy-R1-7-4
   MOTOR=$(SUPPORT)/motor-R7-4
   EPICS_BASE=C:/epics/base-<VER>
   ```

4. **busy fix.** busy's own `configure/RELEASE` defines `AUTOSAVE=$(SUPPORT)/autosave-5-9`. That makes the build try its testBusyAsyn IOC, which fails with `dbdExpand.pl: Can't find file 'asSupport.dbd'`. Either build autosave too, or create `busy-R1-7-4\configure\RELEASE.local` containing `AUTOSAVE=`. The busy library itself doesn't need autosave.

5. **motor fix.** motor R7-4 (and upstream master as of 2026-11) declares variables of type `CALLBACK`. Under MinGW, windows.h defines `CALLBACK` as a calling-convention macro (`__stdcall`), so the build fails with `'callback' does not name a type` and `expected primary-expression before '__attribute__'`. Replace `\bCALLBACK\b` with `epicsCallback` (base's real type, of which `CALLBACK` is only an alias) in:
   - `motorApp/MotorSrc/`: `motordrvCom.h`, `motordrvCom.cc`, `motorRecord.cc`
   - `motorApp/SoftMotorSrc/`: `devSoft.h`, `devSoft.cc`, `devSoftAux.cc`

   That's 10 lines in total:
   ```powershell
   perl -pi -e 's/\bCALLBACK\b/epicsCallback/g' <file>
   ```
   Check any driver submodule you add for the same pattern. `git -C motor-R7-4 diff` shows the local patch.

6. **Build in dependency order:** seq, asyn, ipac, busy, motor. Run `gmake -C C:\epics\modules\<dir> -j<N> *> <log>` for each, and expect exit 0 every time.

7. **Driver submodules.** motor builds only the submodules that are checked out (`modules/Makefile` filters on `$(wildcard <sub>/Makefile)`). Add one with `git -C motor-R7-4 submodule update --init --depth 1 modules/<motorX>`, then rebuild motor. Driver binaries, dbd and db files install into motor's top-level `bin`, `dbd` and `db`.

8. **Smoke test with motorSim.** This exercises the asyn motor path that real drivers use. Init `modules/motorMotorSim`, rebuild, then run `motor-R7-4\bin\windows-x64-mingw\motorSim.exe sim.cmd`, with stdin redirected from an empty file. `sim.cmd`:
   ```
   dbLoadDatabase("<MOTOR>/dbd/motorSim.dbd")
   motorSim_registerRecordDeviceDriver(pdbbase)
   motorSimCreateController("sim1", 1)
   motorSimConfigAxis("sim1", 0, 20000, -20000, 0, 0)
   dbLoadRecords("<MOTOR>/db/basic_asyn_motor.db","P=mt:,M=sim1,DTYP=asynMotor,PORT=sim1,ADDR=0,DESC=sim,EGU=mm,DIR=Pos,VELO=2,VBAS=0,ACCL=0.2,BDST=0,BVEL=1,BACC=0.2,MRES=0.001,PREC=3,DHLM=15,DLLM=-15,INIT=")
   iocInit()
   epicsThreadSleep(30)
   ```
   Pass condition: after `caput -c -w 15 mt:sim1 3.5`, `mt:sim1.RBV` is 3.5 and `DMOV` is 1. pvget times out here; that's expected, because motorSim.exe isn't linked with QSRV.

## Part 3: BCS LabVIEW motor bridge (motorPV / pvIOC.exe)

BCS launches the bridge with a command like this (all one line):
```
cmd /c color 71& title pvIOC.exe &C:\epics\modules\motorPV\iocs\pvIOC\iocBoot\iocPV\dllPathsEpics7.bat& C:\epics\modules\motorPV\iocs\pvIOC\bin\windows-x64\pvIOC.exe C:\epics\modules\motorPV\iocs\pvIOC\iocBoot\iocPV\st_bcs.cmd
```

**What motorPV is:** an asyn motor driver for motor R7 (it uses asynMotorController, not the CALLBACK-based legacy API). Each axis is a Channel Access client of the BCS LabVIEW motor PVs `<prefix>ao-MO:VAL`, `ai-MO:DRBV`, `bi-MO:DMOV`, `bo-MO:STOP`, `ao-MO:VELO`/`ACCL`, `bo-MO:HOMF`/`HOMR`, `bi-MO:RHLS`/`RLLS`, and `stringin-MO:EGU`, all served by LabVIEW's CA Lab softIoc. `iocBoot/iocPV/pvlist.txt` in the source lists the full PV set. It needs only base, asyn and motor.

**Source:** this is a custom module, on the BCS controls share (mapped as `Z:` on beamline PCs) at `Z:\C Source Code\EPICS Motor Driver\motorPV`. `motorPV_old` beside it is older. The share holds only source from 2020: no binaries, no `dllPathsEpics7.bat`, no `st_bcs.cmd`.

1. **Copy, don't build in place.** The share path has spaces, so copy the source to `C:\epics\modules\motorPV`, which is where the BCS launch command expects it.
2. **Retarget.** The shipped `configure\RELEASE.local` points at base 3.14.12.8, motor-R6-11 and asyn4-38. Keep it as `RELEASE.local.orig-2020` and replace it with:
   ```
   SUPPORT=C:/epics/modules
   ASYN=$(SUPPORT)/asyn-R4-46
   MOTOR=$(SUPPORT)/motor-R7-4
   EPICS_BASE=C:/epics/base-<VER>
   ```
   `iocs\pvIOC` picks this up through `../../configure/RELEASE.local`.
3. **Build:** `gmake -C C:\epics\modules\motorPV -j<N>`. This builds `bin\windows-x64-mingw\motorPV.dll` and `iocs\pvIOC\bin\windows-x64-mingw\pvIOC.exe`, and generates `iocBoot\iocPV\envPaths`. No source patches were needed.
4. **Match the BCS launch command.** It expects MSVC-style paths, so add two shims:
   - `iocs\pvIOC\iocBoot\iocPV\dllPathsEpics7.bat`. Do not use `setlocal`; the caller's cmd must keep the PATH:
     ```bat
     @echo off
     set "PATH=C:\epics\modules\motorPV\bin\windows-x64-mingw;C:\epics\modules\motor-R7-4\bin\windows-x64-mingw;C:\epics\modules\asyn-R4-46\bin\windows-x64-mingw;C:\epics\base-<VER>\bin\windows-x64-mingw;C:\Strawberry\c\bin;%PATH%"
     ```
   - Copy `iocs\pvIOC\bin\windows-x64-mingw\pvIOC.exe` to `iocs\pvIOC\bin\windows-x64\pvIOC.exe`. Re-copy it after every rebuild. The alternative is to change the BCS launch command to the `windows-x64-mingw` path.
5. **`st_bcs.cmd`** must exist in `iocBoot\iocPV`, with one controller line and one motor record per BCS motor. The prefix must be the beamline's real BCS prefix. The shipped `st.cmd` / `motor.substitutions` (with `BCS1305:FakeN:` test PVs) show the pattern:
   ```
   < envPaths
   dbLoadDatabase "${TOP}/dbd/pvIOC.dbd"
   pvIOC_registerRecordDeviceDriver pdbbase
   motorPVCreateController("STP1","<BCS prefix>:<motor>:",100,1000)   # port, PV prefix, moving/idle poll ms
   dbLoadRecords("${MOTOR}/db/basic_asyn_motor.db","P=...,M=...,DTYP=asynMotor,PORT=STP1,ADDR=0,...")
   dbLoadRecords("${TOP}/db/motor_extras.db","P=...,M=...,PORT=STP1,ADDR=0")
   iocInit
   ```
   Find out from the user whether BCS writes this file or it is copied from another install.
6. **Test without LabVIEW.** Serve a fake BCS axis with base's `softIoc`:
   - For each name in `pvlist.txt`, create a record of the type in its name (`ai-MO:X` becomes `record(ai, ...)`) under a test prefix.
   - Set `ai-MO:DRBV` with `INP "<prefix>ao-MO:VAL CP"` so the readback follows the setpoint.
   - Set `bi-MO:DMOV` to `VAL 1`, and `stringin-MO:EGU` to `VAL mm`, both with `PINI YES`.

   Then run the BCS command line with a test st.cmd ending in `epicsThreadSleep(N)`. Pass condition (seen 2026-11-04): `caput -c` to the motor record sets the fake `ao-MO:VAL`, `DRBV` follows, and the motor record shows RBV = the target, DMOV = 1, NO_ALARM.

## Pitfalls seen in practice
- **`Start-Process -RedirectStandardInput NUL` fails** in Windows PowerShell 5.1, because it resolves `NUL` as a relative file. The IOC never starts, so caget times out with "not found". Use an empty temp file instead, as in step 5.
- **Harmless crash-looking message:** with `-S` and stdin at EOF, softIoc prints `epicsThreadExitMain() must no longer be used. CRITICAL ERROR ... can't proceed, suspending` plus a stack trace. Only the main thread parks; the IOC keeps serving CA and PVA. This is not a build failure.
- The empty stdin file stays locked until the IOC process is fully gone. Wait about 1 s after `Stop-Process` before deleting it.
- Do not build under a path containing spaces or parentheses. The make-based build breaks.
- The PATH change from the Strawberry installer does not reach the current shell. Prepend it manually (step 2).
- **Every MinGW-built EPICS program needs Strawberry's runtime DLLs.** That includes caget, softIoc and pvIOC: `Com.dll` imports `libgcc_s_seh-1.dll`, `libstdc++-6.dll` and `libreadline8__.dll` from `C:\Strawberry\c\bin`. A program started from a process that was launched before Strawberry was installed (LabVIEW/BCS, Lightfall, an old terminal) doesn't have that on PATH. It then exits immediately with code `-1073741515` (0xC0000135, DLL not found) and prints nothing. Put `C:\Strawberry\c\bin` on PATH explicitly in test scripts and in `dllPathsEpics7.bat`.
- **makeBaseApp-style IOCs (pvIOC, motorSim, your own) run iocsh after st.cmd.** With stdin at EOF they **exit right after iocInit**. For headless tests, end the script with `epicsThreadSleep(N)`. BCS runs the bridge in a console window, so it isn't affected.
- **motorPV kills the IOC when a BCS PV is missing.** Each axis connects to all of its PVs inside `motorPVCreateController`, with a 1 s `ca_pend_io`. A missing PV prints `sevr=minor response ERROR`, and the uncaught exception ends the process. Usual causes: a wrong prefix in st_bcs.cmd, LabVIEW/CA Lab not publishing yet when the bridge starts, or the fake/real PV server not running (see the DLL pitfall above).
- **Soft Channel motor records** (`DTYP "Soft Channel"`) with URIP=Yes need RRES set (the default is 0, which gives RBV=0) and a DINP done-link. Without DINP, readback lags one move and put-callbacks time out.
- `cas WARNING: Configured TCP port was unavailable` means another CA server already holds port 5064 (on BCS PCs, usually LabVIEW's CA Lab `softioc.exe`). `Identical process variable names on multiple servers` with the same host:port under its short and full names is the same server seen twice. Both are harmless locally.
- pvIOC.exe and motorSim.exe are CA-only, so pvget against them times out.

## Verification evidence
- 2026-10-29: EPICS base 7.0.10 built with exit code 0 at `C:\epics\base-7.0.10`. The step 5 smoke test returned `test:ai 42.5`, then `Old 42.5 / New 7.25` from caput, then pvget `7.25`.
- 2026-11-04 (Windows 10 Pro 19045): seq, asyn, ipac, busy and motor (+ motorMotorSim) all built with exit 0 after the busy and motor fixes above. The motorSim axis moved 0 → 3.5 → -1.25 with RBV tracking and DMOV=1. motorPV and pvIOC built with exit 0. The BCS launch command line with the shims drove a motor against a fake BCS axis: `caput -c LFTEST:m1 2.5` gave `ao-MO:VAL` 2.5, `ai-MO:DRBV` 2.5, and `RBV` 2.5 with DMOV 1 and NO_ALARM.
