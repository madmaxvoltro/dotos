# ● Dot

**Turn a Raspberry Pi into a quiet, appliance-like thin client for Windows Remote Desktop.**

Dot boots straight into a black screen with one white dot, asks for your Windows password and drops you into your Remote Desktop session. There is no desktop to manage, no menus to get lost in, and nothing to install on the Windows side.

> **Status: early.** Dot is new and hasn't been tested widely on real hardware yet. Bug reports from a real Pi (with `journalctl -u dotd -u dot-kiosk`) are very welcome.

## Features

- **Sign in with your Windows password.** Dot checks it against the server first, so a wrong password, an expired password, a locked account or a missing Remote Desktop permission each get a clear message. Passwords are never written to disk.
- **Game mode.** Open **Game** inside Windows (or press Game mode on the login screen) and the game streams to your main monitor with Moonlight, while Windows stays on the second monitor. When you're done, you're back at work, no password needed.
- **Several people, several servers.** Each person has a default server and a switcher under their name. If the default doesn't answer, Dot tries the next one.
- **Set up on the screen itself.** After installing, the Pi walks you through keyboard, network, Tailscale (optional), servers and people.
- **Two monitors, arranged like your desk.** Drag the monitors into place at their real size, resize them from the corners, and Windows gets exactly that layout. Extend, main-only or mirror.
- **Monitor-aware standby.** When the main monitor turns off, Dot locks the session (Windows keeps your apps open), mutes the sound and disconnects USB devices. It brings everything back when the monitor turns on.
- **One volume.** Windows' volume slider and keys turn the real speakers up and down, whether that's the monitor's speakers or the Pi's output.
- **Bluetooth.** Pair keyboards, mice, headphones and game controllers in Settings, with the PIN prompts some devices need. Headphones show up as a sound output; controllers work in game mode.
- **Monitor controls over HDMI.** Brightness, contrast and speaker volume over DDC/CI, and power state over HDMI-CEC.
- **The small things.** Wi-Fi and power from the login screen, an idle clock, settings behind an optional PIN, a boot splash, and updates from Settings.

## What you need

| | |
| --- | --- |
| **Raspberry Pi** | Pi 5 (best, especially for two 4K monitors) or Pi 4 |
| **Storage** | microSD card of 16 GB or more, or a USB SSD |
| **OS** | Raspberry Pi OS **Lite (64-bit)**, Trixie (recommended) or Bookworm |
| **Server** | Windows Pro, Enterprise or Server with Remote Desktop turned on (Windows Home can't host Remote Desktop) |
| **Also** | A monitor, a keyboard and a mouse |

## Install

### 1. Flash Raspberry Pi OS Lite

1. Install [Raspberry Pi Imager](https://www.raspberrypi.com/software/).
2. **Device:** your Pi model. **OS:** *Raspberry Pi OS (other)* → **Raspberry Pi OS Lite (64-bit)**. **Storage:** your SD card.
3. When it asks about OS customisation, choose **Edit settings**:
   - set a hostname (for example `dot`)
   - set a username and password (this is the admin account for SSH, not a Windows account)
   - set up Wi-Fi if you won't use a cable
   - set your locale and keyboard layout
   - under **Services**, enable SSH
4. Write the card, put it in the Pi and power it on. The first boot takes a minute or two.

### 2. Run the installer

Connect from your computer:

```bash
ssh pi@dot.local
```

Then run this on the Pi:

```bash
curl -fsSL https://raw.githubusercontent.com/madmaxvoltro/dotos/main/install.sh | sudo bash
```

No SSH? Log in on the Pi's own screen and run the same command.

Options go after `sudo bash -s --`, for example `... | sudo bash -s -- --tailscale`:

| Option | What it does |
| --- | --- |
| `--tailscale` | Also installs Tailscale, so the Pi can reach servers on other networks |
| `--no-splash` | Keeps the normal text boot instead of the Dot splash |
| `--no-reboot` | Doesn't restart at the end |
| `--no-games` | Skips Moonlight (game mode) |
| `--gui` | Shows the install on the Pi's own screen: the options as a menu, then progress, time left and the log. Works with `--update` too |
| `--update` | Updates the code (and adds any packages a newer version needs). Keeps everything, no restart |
| `--setup` | After a reinstall, walks through the on-screen setup again with everything filled in |
| `--branch dev` | Installs from another branch |

The installer adds:
- **Kiosk:** sway (locked down: no bar, no shortcuts) and Chromium for the UI
- **Remote Desktop:** FreeRDP 3
- **System:** NetworkManager, PipeWire, and `ddcutil` plus the CEC tools for monitor control
- **Game mode:** Moonlight

It then creates a restricted `dot` user, installs the services and the boot splash, and restarts.

Prefer to watch it on the Pi's monitor? Add `--gui`:

```bash
curl -fsSL https://raw.githubusercontent.com/madmaxvoltro/dotos/main/install.sh | sudo bash -s -- --gui
```

The Pi's screen first shows the options (game mode, Tailscale, boot splash): **↑ ↓** to move, **Space** to change, **Enter** to install. It starts by itself after 90 seconds. Then it shows the Dot mark with a progress bar, the percentage, the time left and the install log on the left. The screen is a text console at this point, so it's keyboard only.

**Running it again on a Dot reinstalls it.** Everything is installed again, but your servers, people, Wi-Fi, Tailscale and settings are kept, and the options you chose last time are already selected. With `--gui`, the menu also offers **Setup again**, which walks you through the on-screen setup with everything already filled in. While Dot is running, `--gui` draws on a second console and switches back when it's done. **Settings → About → Update Dot** shows the same progress inside Dot.

### 3. Set it up on the screen

After the restart the Pi shows **Set up**:

1. **Keyboard and time.** The layout matters for typing passwords correctly.
2. **Network.** Use a cable, or pick a Wi-Fi network.
3. **Away from home?** Optionally sign in to Tailscale by scanning a QR code.
4. **Servers.** Enter a name and address, press **Test**, and add more if you like.
5. **People.** Enter a display name and Windows user name, and optionally check the password.
6. **Preferences.** Picture quality, sound, microphone, USB drives, idle clock and settings PIN.

## Using Dot

- **Sign in:** type the password and press Enter. The pill under your name shows the server; click it to switch.
- **Other user:** shows everyone set up on this Dot.
- **Dock (bottom right):** the arrow opens Sound, Wi-Fi, Settings and power.
- **Sign out** of Windows, or disconnect, to return to the Dot login.
- **Settings:** people, servers, network, remote access, connection, screen, sound, this Dot and about.

## Windows side

Nothing to install for everyday use. Dot uses Windows' own Remote Desktop. (Game mode is the one exception, see below.)

- Turn on Remote Desktop: *Settings → System → Remote Desktop*.
- The user must be allowed to sign in remotely (an administrator, or a member of *Remote Desktop Users*).
- Dot trusts a server's certificate the first time it connects. If the certificate changes later, Dot refuses to connect and says so.
- **Optional: 60 fps instead of 30.** Windows limits Remote Desktop to 30 frames per second by default. To raise it, run this in an administrator terminal on the server, then restart. Not on a Pi 4: it can't decode 60 frames a second of video, and falls behind.

  ```
  reg add "HKLM\SYSTEM\CurrentControlSet\Control\Terminal Server\WinStations" /v DWMFRAMEINTERVAL /t REG_DWORD /d 15 /f
  ```

## Hardware video decoding (experimental)

FreeRDP decodes Remote Desktop's video on the Pi's processor. On a Pi 4 that's the heaviest job during video. Dot can build its own FreeRDP with a patch ([`system/freerdp/h264-v4l2m2m.patch`](system/freerdp/h264-v4l2m2m.patch)) that hands H.264 to the Pi's hardware decoder instead:

```bash
sudo dot hwdecode
```

It builds FreeRDP 3.15 into `/opt/dot/freerdp` in the background (about 45 minutes on a Pi 4; closing SSH doesn't stop it), next to the system's FreeRDP. Then turn on *Settings → Connection → Hardware video decoding* and use *Graphics → Smooth*. If the hardware decoder doesn't keep up frame by frame, FreeRDP switches back to software decoding by itself and says why in `dot logs`. `sudo dot hwdecode --remove` takes it away.

## Monitors

A Pi 5 drives two monitors, for example two 4K at 60 Hz, or a 4K plus a 1080p at 144 Hz. In **Settings → Screen** you can:

- drag the monitors to match your desk and pick the main one
- set the resolution, refresh rate and rotation of each
- drag a monitor's corners to make it bigger (more room, smaller text) or smaller (larger text)

Monitors are drawn at their **real size**, read from each monitor's EDID. A 27″ 4K and a 24″ 1080p look about the same, because they are. Windows receives the same arrangement, so the mouse crosses between screens where you'd expect.

| Mode | In a session |
| --- | --- |
| Extend (default) | Windows gets each monitor as its own display |
| Main only | Windows on the main monitor, the clock on the others |
| Mirror | The main monitor copied to the others |

On the login screen, the main monitor shows the login and the others show the idle clock.

**Standby** follows the main monitor. When it turns off, Dot waits a few seconds, then:
- locks the session (Windows keeps your apps open)
- mutes the Pi and the monitors' speakers
- disconnects USB devices, including keyboard and mouse if you choose

Everything comes back when the monitor turns on. The system disk, network adapters and USB hubs are never disconnected. **Settings → Screen → Test standby** tries the whole cycle for 10 seconds.

Good to know: Remote Desktop runs at up to 60 fps, so a 144 Hz monitor works but the session won't use the extra refreshes. The Pi 5 decodes the picture on its CPU. That's plenty for office work on two 4K screens, but full-screen 4K video on both at once will drop frames.

## Game mode

Games stream from a Windows PC to the Pi with [Moonlight](https://moonlight-stream.org), from [Sunshine](https://github.com/LizardByte/Sunshine) on the PC. On a Pi 5 the stream is decoded in hardware (HEVC), so 4K60 or 1080p144 is realistic on a wired network.

**Set it up:**
1. **On the gaming PC**, in PowerShell as administrator:

   ```powershell
   irm https://raw.githubusercontent.com/madmaxvoltro/dotos/main/windows/install.ps1 | iex
   ```

   This installs **Game** on the desktop and in the Start menu, and Dot Helper in the tray. Game and Dot Helper are compiled on the PC from [`windows/DotHelper.cs`](windows/DotHelper.cs) with the C# compiler that ships with Windows, so no prebuilt program is downloaded. It doesn't install a streaming host: install [Sunshine](https://github.com/LizardByte/Sunshine) or [Apollo](https://github.com/ClassicOldSong/Apollo) yourself on the gaming PC first.
2. **On the Dot:** open Settings → Games, choose how each server plays games, then press **Pair**. If Dot Helper is running and you're signed in to that PC, the PIN is entered for you; otherwise type it into Sunshine's web page.

**Three setups, chosen per server:**

| Setup | While you play |
| --- | --- |
| **This PC**: the PC you work on is the gaming PC | Windows moves to the game stream. The second monitor shows the game panel, a browser or the clock. |
| **Other PC**: a separate gaming PC | Windows stays on your second monitor and keeps running |
| **Side by side**: Windows Server | Games in the console session, work in Remote Desktop, both at once |

**Playing:**
- Open **Game** inside your Dot session (`Game.exe "Steam Big Picture"` opens a specific app), or press **Game mode** on the login screen and sign in.
- Dot runs the shutter animation, wakes the PC if it's asleep (Wake-on-LAN), switches the main monitor to its fastest refresh, and starts the stream.
- **Ctrl+Alt+Shift+Z** frees the mouse to reach the second monitor. **Ctrl+Alt+Shift+Q** leaves the game.
- **Back to work** (on the panel, in the tray, or by leaving the game) returns you to Windows without retyping your password.

**How Windows and the Dot talk:** Dot shares a folder into the Remote Desktop session (`\\tsclient\dot`). Game.exe drops a small command file there and reads Dot's status back. There's no network port and no plugin, and it only works from inside your own signed-in session.

**About your password:** to reconnect after a game without asking, Dot keeps the Windows password **in memory only** while you're signed in. It's wiped when you sign out, when standby locks the session, at shutdown, and whenever Dot restarts. It's never written to disk.

## Sound

There is one volume. Dot's slider (the speaker button in the dock), Windows' slider and the keyboard's volume keys all move it.

Windows sends its volume to Dot over Remote Desktop's audio channel. Dot turns the real speakers up or down: the monitor's own speakers over HDMI when the monitor allows it, otherwise the Pi's output. It keeps the Remote Desktop stream at full level, so the sound is never turned down twice. Choose the output and where the volume lives in **Settings → Sound**.

## How it fits together

```
tty1 ─ sway (kiosk config, user "dot"), one window per monitor
        ├─ Chromium --app  →  Dot UI (ui/, plain HTML/JS, no build step)
        ├─ Chromium --app  →  idle clock on other monitors
        └─ sdl-freerdp3 /f /multimon  (on top while signed in)
                ▲
dotd (root, 127.0.0.1:7777, Python standard library only)
  ├─ /etc/dot/config.json   servers, people, preferences (no passwords)
  ├─ displays.py            arrangement, modes, scale (swaymsg, EDID)
  ├─ monitor.py             power state and controls (DDC/CI, CEC, hot-plug), standby
  ├─ audio.py               outputs and one volume (PipeWire, follows Windows)
  ├─ games.py               game mode: Moonlight, pairing, Wake-on-LAN, \\tsclient\dot commands
  ├─ rdp.py                 FreeRDP: password check, then the session
  └─ system.py              network (nmcli), Tailscale, power
```

The password goes from the UI to `dotd` over localhost, then to FreeRDP on stdin (`/args-from:stdin`). It is never written to disk and never appears in the process list; `dotd` keeps it in memory while you're signed in so game mode can reconnect. On FreeRDP 2 (Bookworm) it has to go on the command line, so Trixie is recommended.

## Repository layout

| Path | What's there |
| --- | --- |
| `install.sh`, `uninstall.sh` | The installer and its undo |
| `dotd/` | The device daemon (Python, no dependencies) |
| `ui/` | The on-screen interface (HTML, CSS, JavaScript modules; no build step) |
| `system/` | systemd units, sway config, kiosk session scripts, boot splash, udev rule |
| `windows/` | Game mode's Windows side: `install.ps1` and `DotHelper.cs` (Game.exe and the tray helper) |

## Developing

Run the whole UI on a laptop with fake hardware (Python 3.9 or newer):

```bash
python -m dotd --dev --config .dev/config.json --ui ui
```

Then open http://localhost:7777. In dev mode:

| Password | Result |
| --- | --- |
| `dot` | Signs in; the fake session ends after 45 s |
| `expired` | Password expired |
| `locked` | Account locked |
| anything else | Wrong password |

Dev-mode helpers:
- A server address containing `off` counts as unreachable.
- POST `{"on": false}` or `{"on": true}` to `/api/dev/monitor` to turn the main monitor off or on.
- POST `{"volume": 35}` to `/api/dev/stream` to act as Windows changing its volume.
- POST `{"cmd": "game", "app": "Steam Big Picture"}` to `/api/dev/game` to act as Game.exe. In dev mode `\\tsclient\dot` is `.dev/inbox`, so the real Game.exe works too: build it (below) and run it with `DOT_SHARE` set to that folder.
- Add `?screen=login`, `?screen=idle` and so on to the URL to jump to a screen.

The UI fonts (Geist) are downloaded by the installer. For local development, fetch them once:

```bash
curl -fsSL -o ui/fonts/Geist-Variable.woff2 https://cdn.jsdelivr.net/npm/geist@1.3.1/dist/fonts/geist-sans/Geist-Variable.woff2
```

Build Game.exe and Dot Helper on Windows (no SDK needed):

```powershell
& "$env:WINDIR\Microsoft.NET\Framework64\v4.0.30319\csc.exe" /target:winexe /out:Game.exe /r:System.Windows.Forms.dll /r:System.Drawing.dll /r:System.Web.Extensions.dll /r:System.Security.dll windows\DotHelper.cs
```

To install from a local checkout instead of GitHub, copy the folder to the Pi and run `sudo ./install.sh`.

Logs: `journalctl -u dotd -u dot-kiosk -f`. Uninstall: `sudo /usr/lib/dot/uninstall.sh` (add `--purge` to remove settings too).

## Roadmap

- **Dot Helper for Windows:** an optional tray app that sends brightness and night light over Remote Desktop to the monitor.
- **A ready-made image** that shows up in Raspberry Pi Imager, so there's no `curl` step.

## License

[MIT](LICENSE)
