<#
.SYNOPSIS
    Alchitry Au Flasher - a WinForms UI that downloads an FPGA .bin from GitHub
    and flashes it onto an Alchitry Au / Au V2 board over USB, and can install
    the Alchitry Labs V2 loader for you.

.DESCRIPTION
    The actual USB programming is performed by Alchitry Labs V2's command-line
    tool (Alchitry.exe). This UI:
        * Installs Alchitry Labs V2 (portable ZIP, no admin; or the installer EXE).
        * Downloads the chosen .bin from GitHub.
        * Verifies SHA-256 of everything it downloads.
        * Caches downloads so repeated runs are instant.
        * Shells out to:  Alchitry.exe load --bin <file> --board <b> [--flash]
    Every control has a (?) button that opens a local HTML help page.

    PowerShell cannot speak the FTDI flash-bridge protocol itself, so the
    official loader is required.

.NOTES
    Windows PowerShell 5.1 (STA).
        powershell -ExecutionPolicy Bypass -File .\AlchitryFlasher.ps1
#>

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[System.Windows.Forms.Application]::EnableVisualStyles()
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
$RepoRaw = 'https://raw.githubusercontent.com/drhalftone/AuV2-SLI/main/Bitstream'

# Bitstreams shipped in this repo, next to the flasher (..\Bitstream). When the
# local file is present it is flashed directly - no download. Url is kept only as a
# fallback for a checkout that is missing the .bin.
$BitstreamDir = Join-Path $PSScriptRoot '..\Bitstream'

# label -> @{ Path (local, preferred) ; Url (fallback) ; Sha256 (verified before flash) }
$BinChoices = [ordered]@{
    'Au2_SLI.bin (SLI system, Bank-B stack board)' = @{
        Path   = (Join-Path $BitstreamDir 'Au2_SLI.bin')
        Url    = "$RepoRaw/Au2_SLI.bin"
        Sha256 = 'A250146B6A5199BE8E836B28ADF091323CFF851F73ABF5373EB01D4D711AAEEF'
    }
}

$AppDir      = Join-Path $env:LOCALAPPDATA 'AlchitryFlasher'
$DownloadDir = Join-Path $AppDir 'downloads'
$CacheDir    = Join-Path $AppDir 'cache'      # raw downloaded archives, keyed by name
$ToolsDir    = Join-Path $AppDir 'tools'      # extracted Alchitry Labs, keyed by tag
$HelpDir     = Join-Path $AppDir 'help'
foreach ($d in @($AppDir, $DownloadDir, $CacheDir, $ToolsDir, $HelpDir)) {
    if (-not (Test-Path $d)) { New-Item -ItemType Directory -Path $d -Force | Out-Null }
}

# Pinned fallback if the GitHub API is unreachable / rate-limited.
$PinnedTag    = '2.0.52'
$PinnedZipUrl = "https://github.com/alchitry/Alchitry-Labs-V2/releases/download/$PinnedTag/alchitry-labs-$PinnedTag-windows-amd64.zip"
$PinnedZipSha = '0fab44602dd685a1a80b418042a5d84c9908fb5d37d57f7edae843d2e4be3b06'
$PinnedExeUrl = "https://github.com/alchitry/Alchitry-Labs-V2/releases/download/$PinnedTag/alchitry-labs.exe"
$PinnedExeSha = '2abd6333a0b950d69fee1d10a30e3a41a2f35189d7e65e360c5ed85029ae4428'

# --- Step 2 diagnostics ----------------------------------------------------
# The Au V2's FT2232H. On Windows the board uses the STOCK FTDI driver (D2XX) that
# Windows installs automatically - do NOT replace it with WinUSB/libusbK (that breaks
# detection). "No devices detected" on a plugged-in board almost always means the loader
# is too old: the Au V2 needs Alchitry Labs 2.0.52+ (older builds predate Au V2 support).
$FtdiVid = '0403'   # FTDI
$FtdiPid = '6010'   # FT2232H (Au / Au V2)

function Find-AlchitryExe {
    # 1) anything we already extracted
    $hit = Get-ChildItem -Path $ToolsDir -Filter 'Alchitry.exe' -Recurse -ErrorAction SilentlyContinue |
           Select-Object -First 1
    if ($hit) { return $hit.FullName }
    # 2) standard install locations
    $candidates = @(
        "$env:ProgramFiles\Alchitry Labs V2\Alchitry.exe",
        "$env:ProgramFiles\Alchitry\Alchitry.exe",
        "${env:ProgramFiles(x86)}\Alchitry Labs V2\Alchitry.exe",
        "$env:LOCALAPPDATA\Programs\Alchitry Labs V2\Alchitry.exe"
    )
    foreach ($c in $candidates) { if (Test-Path $c) { return $c } }
    foreach ($root in @($env:ProgramFiles, ${env:ProgramFiles(x86)})) {
        if ($root -and (Test-Path $root)) {
            $h = Get-ChildItem -Path $root -Filter 'Alchitry.exe' -Recurse -Depth 2 -ErrorAction SilentlyContinue |
                 Select-Object -First 1
            if ($h) { return $h.FullName }
        }
    }
    return $null
}

# ===========================================================================
# Help pages (written locally, opened by the (?) buttons)
# ===========================================================================
function Write-HelpFiles {
    $head = @'
<!DOCTYPE html><html><head><meta charset="utf-8"><title>TITLE</title><style>
body{font-family:Segoe UI,Arial,sans-serif;max-width:780px;margin:36px auto;padding:0 22px;color:#1b1b1b;line-height:1.55}
h1{color:#0a8f5b}h2{color:#0a7048;margin-top:1.4em}
code,kbd{background:#f1f1f1;padding:2px 6px;border-radius:4px;font-family:Consolas,monospace}
.note{background:#fff8e1;border-left:4px solid #ffb300;padding:9px 13px;margin:14px 0}
.warn{background:#fdecea;border-left:4px solid #e53935;padding:9px 13px;margin:14px 0}
table{border-collapse:collapse;margin:10px 0}td,th{border:1px solid #ccc;padding:5px 9px;text-align:left}
a{color:#0a8f5b}</style></head><body>
'@
    $tail = '<hr><p style="color:#999;font-size:12px">Alchitry Au Flasher &mdash; local help page</p></body></html>'

    $topics = @{
        loader = @'
<h1>Alchitry.exe (the loader)</h1>
<p>This box holds the path to <code>Alchitry.exe</code>, the command-line program from
<b>Alchitry Labs V2</b> that actually programs the board over USB. This UI is a wrapper around it &mdash;
PowerShell cannot talk to the board&rsquo;s FTDI flash chip directly.</p>
<h2>How to fill it</h2>
<ul>
<li><b>Install Alchitry Labs</b> &mdash; fetches the tool automatically and fills this in.</li>
<li><b>Browse&hellip;</b> &mdash; point at an existing <code>Alchitry.exe</code> if you already installed Labs V2.</li>
</ul>
<div class="note">If this is empty, the <b>Download &amp; Flash</b> button will refuse to run.</div>
'@
        bitstream = @'
<h1>Bitstream</h1>
<p>The <code>.bin</code> file that gets written to the FPGA. These come straight from the
<a href="https://github.com/drhalftone/AuV2-SLI">drhalftone/AuV2-SLI</a> GitHub repo.</p>
<table><tr><th>File</th><th>Use</th></tr>
<tr><td>Au2_SLI.bin</td><td>The full structured-light illumination design (Bank-B remap for the LauCameraTrigger stack board; idle LED slider when nothing is connected; USB control + EDID read-back).</td></tr></table>
<p>Each download is checked against a known <b>SHA-256</b> hash; a mismatch aborts the flash so a
corrupted file never reaches the board. Verified files are cached and reused.</p>
'@
        board = @'
<h1>Board type</h1>
<p>Tells the loader which hardware it is talking to (<code>--board</code>). The box is editable.</p>
<table><tr><th>Value</th><th>Meaning</th></tr>
<tr><td><b>AuV2</b></td><td>Default. The Alchitry <b>Au V2</b> programs as <code>AuV2</code> on Labs V2 (verified on tag 2.0.52 &mdash; <code>load --list</code> reports &ldquo;Alchitry Au V2&rdquo;). Using <code>Au+</code> here fails with &ldquo;No board of type Alchitry Au+ found!&rdquo;.</td></tr>
<tr><td>Au+</td><td>Original Alchitry Au+ (not the V2).</td></tr>
<tr><td>Au</td><td>Original Alchitry Au.</td></tr>
<tr><td>Cu</td><td>Lattice-based Alchitry Cu.</td></tr></table>
<div class="note">Use <b>Detect Boards</b> to confirm what your loader version actually reports.</div>
'@
        mode = @'
<h1>Flash vs RAM</h1>
<table><tr><th>Mode</th><th>Flag</th><th>Behaviour</th></tr>
<tr><td><b>Flash (persistent)</b></td><td><code>--flash</code></td><td>Writes to the configuration flash. Survives power cycles &mdash; the board boots this design every time. Slower.</td></tr>
<tr><td><b>RAM (temporary)</b></td><td><code>--ram</code></td><td>Loads straight into the FPGA. Fast, great for testing, but lost on power-off.</td></tr></table>
<p>Pick RAM while iterating; pick Flash when you want the design to stick.</p>
'@
        install = @'
<h1>Install Alchitry Labs</h1>
<p>Downloads and sets up the loader, then fills the <b>Alchitry.exe</b> path automatically.</p>
<h2>Install methods</h2>
<table><tr><th>Method</th><th>Notes</th></tr>
<tr><td><b>Portable ZIP (no admin)</b></td><td>~418&nbsp;MB. Bundles its own Java runtime. Extracted under your user profile &mdash; no administrator rights, no Start-menu entry.</td></tr>
<tr><td><b>Installer EXE (admin)</b></td><td>Small (~0.7&nbsp;MB) web installer. Prompts for admin, does a normal Start-menu install, and can set up USB drivers.</td></tr></table>
<h2>Verification &amp; caching</h2>
<ul>
<li>Every download is checked against GitHub&rsquo;s published <b>SHA-256</b> digest; a mismatch aborts.</li>
<li>Results are <b>cached by version tag</b>. If the current release is already extracted, install is instant and re-downloads nothing.</li>
</ul>
<div class="warn">The portable ZIP gives you the software but not USB drivers. If the loader can&rsquo;t see the
board, use the <b>Installer EXE</b> method, or install the FTDI driver separately.</div>
'@
        detect = @'
<h1>Detect Boards</h1>
<p>Runs <code>Alchitry.exe load --list</code> and prints every board the loader can see, with its
device index. Use this to:</p>
<ul>
<li>Confirm the board is plugged in and the USB driver is working.</li>
<li>Check the exact board name your loader expects (for the <b>Board</b> box).</li>
<li>Find the device index when more than one board is connected.</li>
</ul>
<h2>&ldquo;No devices detected&rdquo; but the board is plugged in?</h2>
<p>On Windows the Au V2 uses the <b>stock FTDI driver</b> that Windows installs automatically
(it appears as &ldquo;USB Serial Converter A/B&rdquo; and a COM port). That is the <i>correct</i>
driver. <b>Do not</b> replace it with WinUSB or libusbK via Zadig &mdash; that actually stops the
loader from seeing the board.</p>
<p>If the board is plugged in but not detected, it is almost always one of:</p>
<table><tr><th>Cause</th><th>Fix</th></tr>
<tr><td><b>Outdated loader</b> (most common)</td><td>The Au V2 needs <b>Alchitry Labs 2.0.52+</b>;
older builds don&rsquo;t recognize it. In <b>Step 1</b> pick <b>Portable ZIP</b>, click
<b>Install</b>, then Detect again.</td></tr>
<tr><td>Cable / port</td><td>Use a known-good <b>data</b> cable (not charge-only) straight into a
rear USB port &mdash; no hub.</td></tr>
<tr><td>Board power</td><td>Confirm the board is powered and fully re-seated.</td></tr></table>
<div class="warn">If an earlier attempt switched the driver to WinUSB/libusbK with Zadig, put the
FTDI driver back: Device Manager &rarr; the Interface&nbsp;0 device &rarr; <i>Uninstall device</i>
(&checkmark; delete the driver software) &rarr; <b>Action &rarr; Scan for hardware changes</b>.</div>
'@
        flash = @'
<h1>Download &amp; Flash</h1>
<p>The main action. In order, it:</p>
<ol>
<li>Downloads the selected bitstream (or reuses a cached, hash-verified copy).</li>
<li>Verifies its <b>SHA-256</b>; aborts on mismatch.</li>
<li>Runs <code>Alchitry.exe load --bin &lt;file&gt; --board &lt;b&gt; [--flash]</code>.</li>
<li>Streams the loader output live into the log pane below.</li>
</ol>
<div class="note">Have the board plugged in and powered over USB first. Watch the log for
&ldquo;<b>DONE</b>&rdquo; or an error code.</div>
'@
        uninstall = @'
<h1>Uninstall / clean up</h1>
<p>Deletes everything this tool created under your user profile:</p>
<pre>%LOCALAPPDATA%\AlchitryFlasher\</pre>
<table><tr><th>Folder</th><th>Contents removed</th></tr>
<tr><td>downloads\</td><td>Downloaded bitstreams and transient loader output.</td></tr>
<tr><td>cache\</td><td>Cached tool archives (the ~418&nbsp;MB ZIP / installer EXE).</td></tr>
<tr><td>tools\</td><td>The extracted portable Alchitry Labs install.</td></tr>
<tr><td>help\</td><td>These local help pages.</td></tr></table>
<p>The button reports how much disk space will be freed and asks you to confirm first.</p>
<div class="warn">This does <b>not</b> uninstall an Alchitry Labs that you installed with the
<b>Installer EXE</b> method &mdash; that is a normal system install. Remove it from Windows
<b>Settings &rarr; Apps &rarr; Installed apps</b>.</div>
<div class="note">After cleaning up you can close the window. Re-running the tool will recreate the
folders and re-download whatever you flash next.</div>
'@
    }

    foreach ($k in $topics.Keys) {
        $html = ($head -replace 'TITLE', $k) + $topics[$k] + $tail
        Set-Content -Path (Join-Path $HelpDir "$k.html") -Value $html -Encoding UTF8
    }
}
Write-HelpFiles

# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------
$form = New-Object System.Windows.Forms.Form
$form.Text = 'Alchitry Au Flasher'
$form.ClientSize = New-Object System.Drawing.Size(684, 660)
$form.StartPosition = 'CenterScreen'
$form.Font = New-Object System.Drawing.Font('Segoe UI', 9)
# Fixed-size window: controls are absolute-positioned (no reflow), so prevent resizing.
$form.FormBorderStyle = [System.Windows.Forms.FormBorderStyle]::FixedDialog
$form.MaximizeBox = $false

$RegFont = New-Object System.Drawing.Font('Segoe UI', 9)

function New-Label([string]$text, [int]$x, [int]$y, $parent, [int]$w = 110) {
    $l = New-Object System.Windows.Forms.Label
    $l.Text = $text; $l.Location = "$x,$y"; $l.Size = "$w,22"; $l.Font = $RegFont
    $parent.Controls.Add($l); return $l
}
function New-HelpButton([int]$x, [int]$y, [string]$topic, $parent) {
    $b = New-Object System.Windows.Forms.Button
    $b.Text = '?'; $b.Location = "$x,$y"; $b.Size = '26,24'
    $b.Font = New-Object System.Drawing.Font('Segoe UI', 9, [System.Drawing.FontStyle]::Bold)
    $b.Add_Click({ Open-Help $topic }.GetNewClosure())
    $parent.Controls.Add($b); return $b
}
function New-Group([string]$title, [int]$x, [int]$y, [int]$w, [int]$h) {
    $g = New-Object System.Windows.Forms.GroupBox
    $g.Text = $title; $g.Location = "$x,$y"; $g.Size = "$w,$h"
    $g.Font = New-Object System.Drawing.Font('Segoe UI', 9, [System.Drawing.FontStyle]::Bold)
    $form.Controls.Add($g); return $g
}
function Open-Help([string]$topic) {
    $f = Join-Path $HelpDir "$topic.html"
    if (-not (Test-Path $f)) {
        # Help pages can be removed mid-session (e.g. by Uninstall / clean up,
        # which deletes the whole AlchitryFlasher tree). Regenerate on demand.
        if (-not (Test-Path $HelpDir)) { New-Item -ItemType Directory -Path $HelpDir -Force | Out-Null }
        Write-HelpFiles
    }
    if (Test-Path $f) { Start-Process $f } else { Write-Log "! help page '$topic' missing" }
}

# === STEP 1 : get the loader ==============================================
$g1 = New-Group ' Step 1  -  Get the loader  (one-time setup) ' 12 8 664 100
New-Label 'Install via:' 12 26 $g1 75 | Out-Null
$cmbMethod = New-Object System.Windows.Forms.ComboBox
$cmbMethod.Location = '92,23'; $cmbMethod.Size = '205,22'; $cmbMethod.Font = $RegFont
$cmbMethod.DropDownStyle = 'DropDownList'
$cmbMethod.Items.AddRange(@('Portable ZIP (no admin)', 'Installer EXE (admin)'))
$cmbMethod.SelectedIndex = 0
$g1.Controls.Add($cmbMethod)
$btnInstall = New-Object System.Windows.Forms.Button
$btnInstall.Text = 'Install Alchitry Labs'; $btnInstall.Location = '308,22'; $btnInstall.Size = '180,26'; $btnInstall.Font = $RegFont
$g1.Controls.Add($btnInstall)
New-HelpButton 628 22 'install' $g1 | Out-Null
New-Label 'Alchitry.exe:' 12 60 $g1 80 | Out-Null
$txtExe = New-Object System.Windows.Forms.TextBox
$txtExe.Location = '92,57'; $txtExe.Size = '335,22'; $txtExe.Font = $RegFont
$txtExe.Text = (Find-AlchitryExe)
$g1.Controls.Add($txtExe)
$btnBrowse = New-Object System.Windows.Forms.Button
$btnBrowse.Text = 'Browse...'; $btnBrowse.Location = '433,56'; $btnBrowse.Size = '80,24'; $btnBrowse.Font = $RegFont
$g1.Controls.Add($btnBrowse)
New-HelpButton 628 57 'loader' $g1 | Out-Null

# === STEP 2 : connect + detect ============================================
$g2 = New-Group ' Step 2  -  Plug the board into USB, then detect it ' 12 116 664 60
$btnDetect = New-Object System.Windows.Forms.Button
$btnDetect.Text = 'Detect Boards'; $btnDetect.Location = '12,22'; $btnDetect.Size = '130,26'; $btnDetect.Font = $RegFont
$g2.Controls.Add($btnDetect)
New-Label 'Power the Au V2 over USB first, then click to confirm it is seen.' 152 26 $g2 460 | Out-Null
New-HelpButton 628 22 'detect' $g2 | Out-Null

# === STEP 3 : choose what to flash ========================================
$g3 = New-Group ' Step 3  -  Choose what to flash ' 12 182 664 104
New-Label 'Bitstream:' 12 28 $g3 70 | Out-Null
$cmbBin = New-Object System.Windows.Forms.ComboBox
$cmbBin.Location = '92,25'; $cmbBin.Size = '410,22'; $cmbBin.Font = $RegFont; $cmbBin.DropDownStyle = 'DropDownList'
$cmbBin.Items.AddRange($BinChoices.Keys)
$cmbBin.SelectedIndex = 0
$g3.Controls.Add($cmbBin)
$btnBrowseBin = New-Object System.Windows.Forms.Button
$btnBrowseBin.Text = 'Browse .bin...'; $btnBrowseBin.Location = '508,24'; $btnBrowseBin.Size = '110,24'; $btnBrowseBin.Font = $RegFont
$g3.Controls.Add($btnBrowseBin)
New-HelpButton 628 24 'bitstream' $g3 | Out-Null
New-Label 'Board:' 12 64 $g3 70 | Out-Null
$cmbBoard = New-Object System.Windows.Forms.ComboBox
$cmbBoard.Location = '92,61'; $cmbBoard.Size = '100,22'; $cmbBoard.Font = $RegFont
$cmbBoard.Items.AddRange(@('AuV2', 'Au+', 'Au', 'Cu'))
$cmbBoard.Text = 'AuV2'
$g3.Controls.Add($cmbBoard)
New-HelpButton 198 61 'board' $g3 | Out-Null
$rbFlash = New-Object System.Windows.Forms.RadioButton
$rbFlash.Text = 'Flash (persistent)'; $rbFlash.Location = '240,63'; $rbFlash.Size = '150,22'; $rbFlash.Font = $RegFont
$rbFlash.Checked = $true
$g3.Controls.Add($rbFlash)
$rbRam = New-Object System.Windows.Forms.RadioButton
$rbRam.Text = 'RAM (temporary)'; $rbRam.Location = '400,63'; $rbRam.Size = '150,22'; $rbRam.Font = $RegFont
$g3.Controls.Add($rbRam)
New-HelpButton 628 61 'mode' $g3 | Out-Null

# === STEP 4 : flash =======================================================
$g4 = New-Group ' Step 4  -  Flash the board ' 12 292 664 58
$btnGo = New-Object System.Windows.Forms.Button
$btnGo.Text = 'Flash board'; $btnGo.Location = '12,18'; $btnGo.Size = '180,28'
$btnGo.Font = New-Object System.Drawing.Font('Segoe UI', 9, [System.Drawing.FontStyle]::Bold)
$g4.Controls.Add($btnGo)
New-Label 'Verifies the local bitstream (..\Bitstream) and writes it to the board.' 202 23 $g4 440 | Out-Null
New-HelpButton 628 19 'flash' $g4 | Out-Null

# === Progress + log =======================================================
New-Label 'Activity log:' 14 356 $form 100 | Out-Null
# Maintenance: wipe everything this tool downloaded/extracted (right-aligned, far from the action buttons).
$btnUninstall = New-Object System.Windows.Forms.Button
$btnUninstall.Text = 'Uninstall / clean up'; $btnUninstall.Location = '492,352'; $btnUninstall.Size = '152,24'; $btnUninstall.Font = $RegFont
$form.Controls.Add($btnUninstall)
New-HelpButton 650 352 'uninstall' $form | Out-Null
$prog = New-Object System.Windows.Forms.ProgressBar
$prog.Location = '14,378'; $prog.Size = '662,18'; $prog.Style = 'Continuous'
$form.Controls.Add($prog)
$log = New-Object System.Windows.Forms.TextBox
$log.Location = '14,402'; $log.Size = '662,248'
$log.Multiline = $true; $log.ScrollBars = 'Vertical'; $log.ReadOnly = $true
$log.BackColor = 'Black'; $log.ForeColor = 'LightGreen'
$log.Font = New-Object System.Drawing.Font('Consolas', 9)
$form.Controls.Add($log)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
function Write-Log([string]$msg, [string]$color = 'LightGreen') {
    $log.AppendText("$msg`r`n")
    [System.Windows.Forms.Application]::DoEvents()
}
function Set-Busy([bool]$busy) {
    foreach ($b in @($btnGo, $btnDetect, $btnInstall, $btnUninstall)) { $b.Enabled = -not $busy }
    $form.Cursor = if ($busy) { 'WaitCursor' } else { 'Default' }
    [System.Windows.Forms.Application]::DoEvents()
}
function Quote([string]$s) { '"' + $s + '"' }

# (a) SHA-256 verification. $expected may be 'sha256:hex' or bare hex; $null => skip.
function Test-Sha256([string]$path, [string]$expected) {
    $actual = (Get-FileHash -Path $path -Algorithm SHA256).Hash
    if (-not $expected) { Write-Log "  sha256 $actual (no reference to check against)"; return $true }
    $exp = ($expected -replace '^sha256:', '').Trim()
    if ($actual -ieq $exp) { Write-Log "  sha256 OK ($actual)" 'Cyan'; return $true }
    Write-Log "  sha256 MISMATCH"
    Write-Log "    expected $exp"
    Write-Log "    actual   $actual"
    return $false
}

# Extract a zip entry-by-entry so the UI keeps repainting (no "Not Responding").
function Expand-ZipWithProgress([string]$zip, [string]$dest) {
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $archive = [System.IO.Compression.ZipFile]::OpenRead($zip)
    try {
        $entries = $archive.Entries; $count = $entries.Count; $i = 0
        $prog.Style = 'Continuous'; $prog.Maximum = 100; $prog.Value = 0
        foreach ($e in $entries) {
            $i++
            $rel = $e.FullName -replace '/', '\'
            $target = Join-Path $dest $rel
            if ($e.FullName.EndsWith('/')) {
                if (-not (Test-Path $target)) { New-Item -ItemType Directory -Path $target -Force | Out-Null }
            } else {
                $dir = Split-Path $target -Parent
                if ($dir -and -not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
                [System.IO.Compression.ZipFileExtensions]::ExtractToFile($e, $target, $true)
            }
            if (($i % 25) -eq 0 -or $i -eq $count) {
                $prog.Value = [int](($i * 100) / $count)
                [System.Windows.Forms.Application]::DoEvents()
            }
        }
    } finally { $archive.Dispose(); $prog.Value = 0 }
}

# Stream a download to disk with a live byte-percentage progress bar.
function Get-FileWithProgress([string]$url, [string]$dest) {
    $req = [System.Net.HttpWebRequest]::Create($url)
    $req.UserAgent = 'AlchitryFlasher'; $req.AllowAutoRedirect = $true
    $resp = $req.GetResponse(); $total = $resp.ContentLength
    $stream = $resp.GetResponseStream(); $fs = [System.IO.File]::Create($dest)
    try {
        if ($total -gt 0) { $prog.Style = 'Continuous'; $prog.Maximum = 100; $prog.Value = 0 }
        else { $prog.Style = 'Marquee' }
        $buf = New-Object byte[] 65536; $sum = 0; $tick = 0
        while (($read = $stream.Read($buf, 0, $buf.Length)) -gt 0) {
            $fs.Write($buf, 0, $read); $sum += $read
            if ($total -gt 0) { $prog.Value = [int](($sum * 100) / $total) }
            if ((++$tick % 16) -eq 0) { [System.Windows.Forms.Application]::DoEvents() }
        }
    } finally {
        $fs.Close(); $stream.Close(); $resp.Close()
        $prog.Style = 'Continuous'; $prog.Value = 0
    }
    return $sum
}

# (b) cache-aware fetch: reuse the file if it already verifies, else (re)download.
function Get-CachedFile([string]$url, [string]$dest, [string]$expectedSha) {
    if ((Test-Path $dest) -and $expectedSha) {
        Write-Log "Checking cached $(Split-Path $dest -Leaf) ..."
        if (Test-Sha256 $dest $expectedSha) { Write-Log '  using cached copy (no download).' 'Cyan'; return $true }
        Write-Log '  cached copy invalid - re-downloading.'
    }
    Write-Log "Downloading $url" 'Cyan'
    $n = Get-FileWithProgress $url $dest
    Write-Log ("  got {0:N0} bytes" -f $n) 'Cyan'
    if ($expectedSha) { return (Test-Sha256 $dest $expectedSha) }
    Test-Sha256 $dest $null | Out-Null
    return $true
}

# Run a process and stream stdout+stderr into the log live.
function Invoke-Loader([string]$exe, [string]$argString) {
    Write-Log ">> $exe $argString" 'White'
    $outFile = Join-Path $DownloadDir 'loader_out.txt'
    $errFile = Join-Path $DownloadDir 'loader_err.txt'
    Remove-Item $outFile, $errFile -ErrorAction SilentlyContinue
    $p = Start-Process -FilePath $exe -ArgumentList $argString -NoNewWindow -PassThru `
            -RedirectStandardOutput $outFile -RedirectStandardError $errFile
    $sw = @{}
    foreach ($f in @($outFile, $errFile)) {
        if (-not (Test-Path $f)) { New-Item -ItemType File -Path $f | Out-Null }
        $sw[$f] = New-Object System.IO.StreamReader([System.IO.File]::Open($f, 'Open', 'Read', 'ReadWrite'))
    }
    try {
        while (-not $p.HasExited) {
            foreach ($f in @($outFile, $errFile)) { $c = $sw[$f].ReadToEnd(); if ($c) { $log.AppendText($c) } }
            [System.Windows.Forms.Application]::DoEvents(); Start-Sleep -Milliseconds 100
        }
        foreach ($f in @($outFile, $errFile)) { $c = $sw[$f].ReadToEnd(); if ($c) { $log.AppendText($c) } }
    } finally { foreach ($k in $sw.Keys) { $sw[$k].Dispose() } }
    # Start-Process -PassThru with redirected output can leave ExitCode $null until the
    # handle is explicitly waited on - force it so callers get a real code.
    try { $p.WaitForExit() } catch {}
    return $p.ExitCode
}

# Resolve the chosen asset (zip or exe) from the latest release, with fallback.
function Resolve-Release([string]$kind) {   # kind = 'zip' | 'exe'
    $out = @{ Tag = $PinnedTag; Url = $null; Sha = $null }
    try {
        $headers = @{ 'User-Agent' = 'AlchitryFlasher'; 'Accept' = 'application/vnd.github+json' }
        $rel = Invoke-RestMethod -Uri 'https://api.github.com/repos/alchitry/Alchitry-Labs-V2/releases/latest' -Headers $headers
        $out.Tag = $rel.tag_name
        $asset = if ($kind -eq 'zip') {
            $rel.assets | Where-Object { $_.name -match 'windows-amd64\.zip$' } | Select-Object -First 1
        } else {
            $rel.assets | Where-Object { $_.name -ieq 'alchitry-labs.exe' } | Select-Object -First 1
        }
        if ($asset) { $out.Url = $asset.browser_download_url; $out.Sha = $asset.digest }
    } catch { Write-Log "  (release lookup failed: $($_.Exception.Message))" }
    if (-not $out.Url) {
        Write-Log "Falling back to pinned $PinnedTag build."
        if ($kind -eq 'zip') { $out.Url = $PinnedZipUrl; $out.Sha = "sha256:$PinnedZipSha" }
        else { $out.Url = $PinnedExeUrl; $out.Sha = "sha256:$PinnedExeSha" }
    }
    return $out
}

# (b+c) Install: portable zip (cached, no admin) or installer exe (admin).
function Install-AlchitryLabs([string]$method) {
    if ($method -like 'Installer*') {
        $rel = Resolve-Release 'exe'
        $exeDl = Join-Path $CacheDir 'alchitry-labs.exe'
        if (-not (Get-CachedFile $rel.Url $exeDl $rel.Sha)) { Write-Log '! installer hash check failed - aborting.'; return $false }
        Write-Log 'Launching the installer (accept the admin prompt)...' 'White'
        $proc = Start-Process -FilePath $exeDl -PassThru
        $proc.WaitForExit()
        Write-Log "Installer exited (code $($proc.ExitCode)). Locating Alchitry.exe..."
        $exe = Find-AlchitryExe
        if ($exe) { $txtExe.Text = $exe; Write-Log "Installed: $exe" 'White'; return $true }
        Write-Log '! Could not auto-locate Alchitry.exe - use Browse... to point at it.'
        return $false
    }

    # Portable ZIP, cached by tag.
    $rel = Resolve-Release 'zip'
    $tagDir = Join-Path $ToolsDir $rel.Tag
    $marker = Join-Path $tagDir '.installed'   # written only after a complete extract
    if (Test-Path $marker) {
        $exePath = Get-Content $marker -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($exePath -and (Test-Path $exePath)) {
            $txtExe.Text = $exePath
            Write-Log "Alchitry Labs $($rel.Tag) already installed (cached): $exePath" 'White'
            return $true
        }
    }
    $zipDl = Join-Path $CacheDir ("alchitry-labs-$($rel.Tag)-windows-amd64.zip")
    Write-Log "Installing Alchitry Labs $($rel.Tag) (portable, ~418 MB)..." 'Cyan'
    if (-not (Get-CachedFile $rel.Url $zipDl $rel.Sha)) { Write-Log '! archive hash check failed - aborting.'; return $false }
    Write-Log "Extracting to $tagDir (this takes a minute - watch the progress bar)..." 'Cyan'
    if (Test-Path $tagDir) { Remove-Item $tagDir -Recurse -Force -ErrorAction SilentlyContinue }
    New-Item -ItemType Directory -Path $tagDir -Force | Out-Null
    Expand-ZipWithProgress $zipDl $tagDir
    $exe = Get-ChildItem -Path $tagDir -Filter 'Alchitry.exe' -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($exe) {
        Set-Content -Path $marker -Value $exe.FullName -Encoding ASCII   # mark install complete
        $txtExe.Text = $exe.FullName
        Write-Log "Installed: $($exe.FullName)" 'White'
        Write-Log 'Ready. NOTE: first board use may still need the FTDI USB driver (see the install (?) help).'
        return $true
    }
    Write-Log '! Could not find Alchitry.exe in the extracted files.'
    return $false
}

# Remove every artifact this tool created under %LOCALAPPDATA%\AlchitryFlasher.
function Uninstall-Artifacts {
    $bytes = 0
    if (Test-Path $AppDir) {
        $bytes = (Get-ChildItem -Path $AppDir -Recurse -File -ErrorAction SilentlyContinue |
                  Measure-Object -Property Length -Sum).Sum
    }
    $mb = [math]::Round(([double]$bytes) / 1MB, 1)
    $msg = "This deletes everything Alchitry Flasher created under:`n$AppDir`n`n" +
           "  - downloaded bitstreams`n" +
           "  - cached tool archives (the ~418 MB ZIP / installer EXE)`n" +
           "  - the extracted portable Alchitry Labs`n" +
           "  - the local (?) help pages`n`n" +
           "Disk space to free: ~$mb MB.`n`n" +
           "It does NOT uninstall an Alchitry Labs you installed with the Installer EXE method " +
           "(remove that from Windows Settings > Apps).`n`nProceed?"
    $ans = [System.Windows.Forms.MessageBox]::Show($msg, 'Uninstall / clean up', 'YesNo', 'Warning')
    if ($ans -ne 'Yes') { Write-Log 'Uninstall cancelled.'; return }

    Set-Busy $true
    try {
        # If the loader path points inside the tree we are about to delete, clear it.
        if ($txtExe.Text -and $txtExe.Text.StartsWith($AppDir, [System.StringComparison]::OrdinalIgnoreCase)) {
            $txtExe.Text = ''
        }
        if (Test-Path $AppDir) {
            Write-Log "Removing $AppDir ..." 'White'
            Remove-Item $AppDir -Recurse -Force -ErrorAction Stop
            Write-Log ("Removed all artifacts (~{0} MB freed)." -f $mb) 'Cyan'
            Write-Log 'Clean. Close the window, or flash again to recreate the folders.' 'White'
        } else {
            Write-Log 'Nothing to remove - no artifacts found.' 'Cyan'
        }
        Update-LoaderGate
    }
    catch { Write-Log "! Uninstall failed: $($_.Exception.Message)" }
    finally { Set-Busy $false }
}

# Step 2 diagnostic: is the Au V2's FT2232H present on the USB bus at all?
# Distinguishes "board there but loader didn't recognize it (old loader / cable)"
# from "board not plugged in / no power".
function Test-FtdiPresent {
    [bool](Get-PnpDevice -PresentOnly -ErrorAction SilentlyContinue |
           Where-Object { $_.InstanceId -match "VID_$FtdiVid&PID_$FtdiPid" })
}

# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------
# Progressive gating: each step unlocks the next.
#   Step 2 (detect)        -> enabled only once a loader path is set (Step 1 done).
#   Steps 3 (choose) + 4 (flash) -> enabled only once a board has been detected (Step 2 done).
function Update-LoaderGate {
    $haveLoader = [bool]($txtExe.Text -and (Test-Path $txtExe.Text))
    $g2.Enabled = $haveLoader
    if (-not $haveLoader) { $g3.Enabled = $false; $g4.Enabled = $false }   # lose loader -> re-lock all
}
function Set-BoardDetected([bool]$found) {
    $g3.Enabled = $found
    $g4.Enabled = $found
    if ($found) { Write-Log 'Board detected - Steps 3 and 4 unlocked.' 'Cyan' }
}
$g3.Enabled = $false     # locked until a board is detected
$g4.Enabled = $false
$txtExe.Add_TextChanged({ Update-LoaderGate })
Update-LoaderGate        # set Step 2 from whatever loader was found at startup

$btnBrowse.Add_Click({
    $dlg = New-Object System.Windows.Forms.OpenFileDialog
    $dlg.Filter = 'Alchitry.exe|Alchitry.exe|Executables|*.exe'
    if ($dlg.ShowDialog() -eq 'OK') { $txtExe.Text = $dlg.FileName }
})

# Pick any local .bin to flash. Adds it as a choice (no SHA reference -> flashed as-is).
$btnBrowseBin.Add_Click({
    $dlg = New-Object System.Windows.Forms.OpenFileDialog
    $dlg.Filter = 'Bitstream (*.bin)|*.bin|All files|*.*'
    if (Test-Path $BitstreamDir) { $dlg.InitialDirectory = (Resolve-Path $BitstreamDir).Path }
    if ($dlg.ShowDialog() -eq 'OK') {
        $p = $dlg.FileName
        $label = "Local: $(Split-Path $p -Leaf)"
        $BinChoices[$label] = @{ Path = $p; Url = $null; Sha256 = $null }
        if (-not $cmbBin.Items.Contains($label)) { [void]$cmbBin.Items.Add($label) }
        $cmbBin.SelectedItem = $label
        Write-Log "Selected local bitstream: $p" 'Cyan'
    }
})

$btnInstall.Add_Click({
    Set-Busy $true
    try { Install-AlchitryLabs $cmbMethod.SelectedItem | Out-Null }
    catch { Write-Log "! Install failed: $($_.Exception.Message)" }
    finally { Set-Busy $false }
})

$btnUninstall.Add_Click({
    try { Uninstall-Artifacts }   # handles its own confirm + Set-Busy
    catch { Write-Log "! Uninstall failed: $($_.Exception.Message)" }
})

# Step 2 core: list boards. On "none", tell apart an old-loader/cable problem
# (board present on USB) from a not-plugged-in board.
function Invoke-Detect {
    if (-not (Test-Path $txtExe.Text)) { Write-Log '! Alchitry.exe not found. Install or Browse first.'; return }
    Set-Busy $true
    try {
        Invoke-Loader $txtExe.Text 'load --list' | Out-Null
        # Loader prints "Detected N Alchitry ..." -- N>=1 means a board is present.
        $out = Get-Content (Join-Path $DownloadDir 'loader_out.txt') -Raw -ErrorAction SilentlyContinue
        $found = ($out -match 'Detected\s+(\d+)') -and ([int]$Matches[1] -ge 1)
        Set-BoardDetected $found
        if ($found) { return }

        if (Test-FtdiPresent) {
            Write-Log '! The board is on USB, but the loader did not recognize it.'
            Write-Log '  Almost always an OUTDATED loader - the Au V2 needs Alchitry Labs' 'White'
            Write-Log '  2.0.52+. In Step 1 pick "Portable ZIP (no admin)" and click Install,' 'White'
            Write-Log '  then Detect again. (If it still fails, try another USB data cable or' 'White'
            Write-Log '  port - do NOT change the FTDI driver with Zadig.)' 'White'
        } else {
            Write-Log '! No board on USB - check the cable (must carry data, not charge-only),'
            Write-Log '  board power, and that it is plugged in, then Detect again.'
        }
    }
    catch { Write-Log "! $($_.Exception.Message)" }
    finally { Set-Busy $false }
}
$btnDetect.Add_Click({ Invoke-Detect })

$btnGo.Add_Click({
    if (-not (Test-Path $txtExe.Text)) {
        [System.Windows.Forms.MessageBox]::Show(
            "Alchitry.exe not found.`nClick 'Install Alchitry Labs' or Browse to its location.",
            'Loader missing', 'OK', 'Warning') | Out-Null
        return
    }
    Set-Busy $true
    try {
        $key  = $cmbBin.SelectedItem
        $info = $BinChoices[$key]
        # Prefer the local file shipped in ..\Bitstream (or a browsed .bin); only
        # fall back to a GitHub download if no local copy exists.
        if ($info.Path -and (Test-Path $info.Path)) {
            $dest = $info.Path
            Write-Log "Using local bitstream: $dest" 'Cyan'
            if ($info.Sha256) {
                if (-not (Test-Sha256 $dest $info.Sha256)) {
                    Write-Log '! Local bitstream failed SHA-256 - NOT flashing.'
                    return
                }
            } else {
                Test-Sha256 $dest $null | Out-Null   # informational only
            }
        } elseif ($info.Url) {
            $dest = Join-Path $DownloadDir (Split-Path $info.Url -Leaf)
            Write-Log "Local file not found - downloading from GitHub." 'White'
            if (-not (Get-CachedFile $info.Url $dest $info.Sha256)) {
                Write-Log '! Bitstream failed verification - NOT flashing.'
                return
            }
        } else {
            Write-Log '! No local file found and no download URL - NOT flashing.'
            return
        }
        $board = $cmbBoard.Text.Trim()
        $mode  = if ($rbFlash.Checked) { '--flash' } else { '--ram' }
        $args  = "load --bin $(Quote $dest) --board $board $mode".Trim()
        $code = Invoke-Loader $txtExe.Text $args
        # The loader prints "Done." on a successful flash; trust that too, since
        # Start-Process occasionally returns an empty ExitCode even on a clean run.
        $loaderOut = ((Get-Content (Join-Path $DownloadDir 'loader_out.txt') -Raw -ErrorAction SilentlyContinue) + "`n" +
                      (Get-Content (Join-Path $DownloadDir 'loader_err.txt') -Raw -ErrorAction SilentlyContinue))
        if (($code -eq 0) -or ($loaderOut -match '(?im)\bDone\.')) {
            Write-Log "`nDONE - board programmed successfully." 'White'
        } elseif ($null -eq $code -or "$code" -eq '') {
            Write-Log "`n! Flash ended without a clear result - check the log above."
        } else {
            Write-Log "`n! Loader exited with code $code - see messages above."
        }
    }
    catch { Write-Log "! $($_.Exception.Message)" }
    finally { Set-Busy $false }
})

# ---------------------------------------------------------------------------
Write-Log 'Work top to bottom: Step 1 -> 2 -> 3 -> 4.  Each (?) opens detailed help.' 'White'
if (-not $txtExe.Text) {
    Write-Log 'Step 1: click "Install Alchitry Labs" (or Browse... if you already have it).'
} else {
    Write-Log "Step 1 done - loader found: $($txtExe.Text)" 'Cyan'
    Write-Log 'Go to Step 2: plug in the Au V2 and click Detect Boards.'
}

[void]$form.ShowDialog()
