# Screenshots of the dashboard for docs/ui/flow.md: headless Chrome driven over the Chrome
# DevTools Protocol (CDP), from plain PowerShell (.NET's ClientWebSocket). No extra dependency.
#
# Why CDP and not `chrome --screenshot`: that one-shot mode takes the picture without waiting
# for open streams, and the run page loads its steps over Server-Sent Events (EventSource): it
# was captured at "connecting...". CDP navigates, waits REAL time, then captures. (Playwright
# and Puppeteer use the same protocol; for still pages this is all we need.)
#
#   .\docs\ui\capture.ps1 -Name 01-overview-healthy -Path /
#   .\docs\ui\capture.ps1 -Name 05-run-diagnosis -Path /runs/<thread-id> -Height 2400 -WaitMs 12000
#
# The dashboard must be running with data. Use a production build (docker compose / the web
# image): `pnpm dev` puts its "Compiling..." badge into the picture.
param(
    [Parameter(Mandatory)] [string] $Name,
    [Parameter(Mandatory)] [string] $Path,
    [int] $Width = 1440,
    [int] $Height = 900,
    [int] $WaitMs = 6000,
    [string] $BaseUrl = "http://localhost:3000",  # localhost, not 127.0.0.1: the API's CORS origin
    [string] $Click = "",  # optional: click the button/tab with exactly this text first (e.g. "History")
    [int] $Port = 9223
)
$ErrorActionPreference = "Stop"

$chrome = @(
    "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
    "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $chrome) { throw "Chrome or Edge not found" }

$out = Join-Path $PSScriptRoot "screenshots\$Name.png"
# A throwaway profile: no extensions, no sign-in, nothing from your own browser in the picture.
$profileDir = Join-Path ([IO.Path]::GetTempPath()) "fabpilot-capture-profile"

$proc = Start-Process -PassThru -FilePath $chrome -ArgumentList @(
    "--headless=new", "--disable-gpu", "--hide-scrollbars", "--no-first-run",
    "--disable-extensions", "--user-data-dir=`"$profileDir`"",
    "--remote-debugging-port=$Port", "--window-size=$Width,$Height", "about:blank"
)
$ws = [System.Net.WebSockets.ClientWebSocket]::new()
$script:id = 0

function Send-Cdp([string] $method, [hashtable] $params = @{}) {
    # One request, then read messages until the reply with OUR id (events arrive in between).
    $script:id++
    $json = @{ id = $script:id; method = $method; params = $params } | ConvertTo-Json -Depth 6 -Compress
    $bytes = [Text.Encoding]::UTF8.GetBytes($json)
    $ws.SendAsync([ArraySegment[byte]]::new($bytes), "Text", $true, [Threading.CancellationToken]::None).Wait()
    $buffer = [byte[]]::new(1MB)
    while ($true) {
        $ms = [IO.MemoryStream]::new()
        do {
            $r = $ws.ReceiveAsync([ArraySegment[byte]]::new($buffer), [Threading.CancellationToken]::None).Result
            $ms.Write($buffer, 0, $r.Count)
        } until ($r.EndOfMessage)
        $msg = [Text.Encoding]::UTF8.GetString($ms.ToArray()) | ConvertFrom-Json
        if ($msg.id -eq $script:id) {
            if ($msg.error) { throw "CDP $method failed: $($msg.error.message)" }
            return $msg.result
        }
    }
}

try {
    # The DevTools endpoint needs a moment after start.
    $target = $null
    for ($i = 0; $i -lt 40 -and -not $target; $i++) {
        Start-Sleep -Milliseconds 250
        try {
            $target = (Invoke-RestMethod "http://127.0.0.1:$Port/json" -TimeoutSec 2) |
                Where-Object type -eq "page" | Select-Object -First 1
        } catch { }
    }
    if (-not $target) { throw "Chrome's DevTools endpoint didn't come up on port $Port" }
    $ws.ConnectAsync([Uri] $target.webSocketDebuggerUrl, [Threading.CancellationToken]::None).Wait()

    Send-Cdp "Emulation.setDeviceMetricsOverride" @{
        width = $Width; height = $Height; deviceScaleFactor = 1; mobile = $false
    } | Out-Null
    Send-Cdp "Page.enable" | Out-Null
    Send-Cdp "Page.navigate" @{ url = "$BaseUrl$Path" } | Out-Null
    Start-Sleep -Milliseconds $WaitMs  # REAL time: client fetches and SSE streams arrive
    if ($Click) {
        # State that has no URL (a client-side tab): click it like a user would.
        $label = $Click | ConvertTo-Json  # a JS string literal, quotes escaped
        $js = "(() => { const el = [...document.querySelectorAll('button,[role=tab],a')]" +
            ".find(e => e.textContent.trim() === $label); if (!el) return false;" +
            # The whole sequence a real mouse produces: Radix tabs (shadcn) activate on
            # pointer/mousedown, not on click(), so click() alone left the tab unchanged.
            " for (const t of ['pointerdown','mousedown','pointerup','mouseup','click'])" +
            " el.dispatchEvent(new (t.startsWith('pointer') ? PointerEvent : MouseEvent)(t," +
            " {bubbles: true, cancelable: true, button: 0, isPrimary: true}));" +
            " return true; })()"
        $res = Send-Cdp "Runtime.evaluate" @{ expression = $js; returnByValue = $true }
        if (-not $res.result.value) { throw "nothing to click with the text '$Click'" }
        Start-Sleep -Milliseconds 3000
    }
    $shot = Send-Cdp "Page.captureScreenshot" @{ format = "png" }
    [IO.File]::WriteAllBytes($out, [Convert]::FromBase64String($shot.data))
} finally {
    try { Send-Cdp "Browser.close" | Out-Null } catch { }
    $ws.Dispose()
    if (-not $proc.HasExited) { $proc.WaitForExit(5000) | Out-Null }
}
"{0}  {1:N0} KB  <- {2}" -f (Split-Path $out -Leaf), ((Get-Item $out).Length / 1KB), "$BaseUrl$Path"
