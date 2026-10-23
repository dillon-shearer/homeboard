# Home Board Pi setup

This guide takes a blank SD card to a working photo slideshow. You do not need to know anything about the Raspberry Pi. Do the steps in order. Each step says what you should see.

A few words used below:

- **Pi**: the Raspberry Pi 4B, the small computer behind the monitor.
- **SD card**: the memory card the Pi starts from. It holds the whole system.
- **Terminal**: a window where you type commands. On Windows this is PowerShell.
- **SSH**: a way to type commands on the Pi from your Windows PC over Wi-Fi.
- **sudo**: put in front of a command on the Pi to run it as the administrator.

## What you need

- Raspberry Pi 4B with its official USB-C power supply.
- A microSD card, 16 GB or larger, and a way to plug it into your PC (a USB card reader works).
- A micro-HDMI to HDMI cable and the monitor.
- Your Wi-Fi name and password. The Pi must join the same Wi-Fi as your phones.
- A Windows PC on the same Wi-Fi.

## Part 1: Prepare your PC

1. Make an SSH key. This lets you log in to the Pi without typing a password.
   Press the Windows key, type `PowerShell`, and press Enter. In the window that opens, type:

   ```
   ssh-keygen -t ed25519
   ```

   Press Enter at every question to accept the defaults. You should see a box of random characters at the end. If it says the file already exists, type `n` and press Enter: you already have a key, which is fine.

2. Install Raspberry Pi Imager. This is the official program that writes the system onto the SD card.
   Open https://www.raspberrypi.com/software/ in your browser. Click **Download for Windows**. Open the downloaded file and click through the installer with the default choices. When it finishes, you should have **Raspberry Pi Imager** in the Start menu.

## Part 2: Write the SD card

3. Put the SD card in your PC. If Windows asks to format it, click **Cancel**.

4. Open **Raspberry Pi Imager** from the Start menu. If Windows asks whether to allow changes, click **Yes**.

5. Click **Choose Device** and pick **Raspberry Pi 4**.
   Imager 2 and newer looks different: a list of **Setup steps** on the left (Device, OS, Storage, Customisation, Writing) and a **NEXT** button at the bottom right. Pick the same things on each page and click **NEXT**. In Imager 2, the Lite and Legacy entries in step 6 are under **Raspberry Pi OS (other)**.

6. Click **Choose OS**. Pick the **Lite 64-bit** system based on **Bookworm**:
   - If **Raspberry Pi OS (other)** lists **Raspberry Pi OS Lite (64-bit)** and its description says Bookworm, pick that.
   - If the Lite (64-bit) entry says Trixie instead, pick **Raspberry Pi OS (Legacy, 64-bit) Lite** in the same list. Its description says Bookworm.

   "Lite" means no desktop. That is what we want: the Pi only shows the slideshow.

7. Click **Choose Storage** and pick your SD card. Check the size so you do not pick another drive. Everything on the chosen card is erased.

8. Click **Next**. Imager asks "Would you like to apply OS customisation settings?". Click **Edit Settings**. A window with tabs opens.
   Imager 2 and newer has no question and no tabs. After **Storage** it goes straight to the **Customisation** pages: **Hostname**, **Localisation**, **User**, **Wi-Fi**, **Remote access** and **Raspberry Pi Connect**. Fill them in with the values from steps 9 and 10, click **NEXT** after each, and leave Raspberry Pi Connect off.

9. On the **General** tab:
   - Tick **Set hostname** and type `homeboard`. This is the Pi's name on your network.
   - Tick **Set username and password**. Username: `dillon`. Pick a password and write it down. You need it for `sudo`.
   - Tick **Configure wireless LAN**. Type your Wi-Fi name in **SSID** and the Wi-Fi password in **Password**. Set **Wireless LAN country** to `US`.
   - Tick **Set locale settings**. Set **Time zone** to `America/New_York` and **Keyboard layout** to `us`.

10. On the **Services** tab:
    - Tick **Enable SSH**.
    - Choose **Allow public-key authentication only**.
    - The box below should already hold a long line starting with `ssh-ed25519`. That is the key from step 1. If the box is empty, click **Run SSH-KEYGEN**, or open `C:\Users\<your name>\.ssh\id_ed25519.pub` in Notepad and paste its one line into the box.
    - The line ends with something like `yourname@DESKTOP-ABC123`. Paste that part too. It is only a label, not a secret.

11. Click **Save**. Back at the question, click **Yes** to apply the settings. Click **Yes** again to confirm erasing the card.
    In Imager 2 and newer, click **WRITE** on the last page and confirm erasing the card.
    Imager writes and then checks the card. This takes about 5 to 15 minutes. When it says **Write Successful**, click **Continue** and take the card out.

## Part 3: First boot

12. Put the SD card in the Pi. The slot is on the bottom, under the board. Connect the monitor to the HDMI port next to the USB-C port (HDMI 0). Turn the monitor on. Plug in the Pi's power last.

13. Wait about 2 minutes. The monitor shows lines of text scrolling by. The first boot may restart once by itself. It ends at a text line like `homeboard login:`. That is correct. You do not type anything on the Pi itself. The Pi needs no keyboard or mouse at any point; everything is done from your PC.

14. On your PC, open PowerShell and type:

    ```
    ssh dillon@homeboard.local
    ```

    The first time, it asks "Are you sure you want to continue connecting?". Type `yes` and press Enter. You should now see a prompt like `dillon@homeboard:~ $`. Commands you type here run on the Pi.

    If it says it could not resolve `homeboard.local`, wait one more minute and try again. If it still fails, see "Cannot connect with SSH" under Troubleshooting.

## Part 4: Install Home Board

15. In the same SSH window, download the install script:

    ```
    curl -fsSL -o install.sh https://raw.githubusercontent.com/dillon-shearer/homeboard/main/deploy/install.sh
    ```

    No output means it worked.

16. Run it:

    ```
    sudo bash install.sh
    ```

    Type your Pi password if asked. Nothing shows while you type the password; that is normal. The script installs software, gets the Home Board code, and sets up the services (programs that start by themselves when the Pi boots). It prints many lines and takes about 10 to 20 minutes. It is done when the last line says `Install finished. reboot now: sudo reboot`.

    If it stops with an error, run the same command again. Running it twice is safe. If it fails the same way twice, copy the last 20 lines and ask for help.

17. Restart the Pi:

    ```
    sudo reboot
    ```

    The SSH window closes. That is expected.

## Part 5: What you should see after the reboot

18. Watch the monitor. Text scrolls for about a minute. Then the screen goes black for a few seconds while the browser starts. Then the Home Board page fills the whole screen, with no window borders and no mouse pointer. Once photos are added from a phone, the slideshow runs here.

19. Check the admin site from your phone. On the same Wi-Fi, open `http://homeboard.local/` in the browser. You should see the Home Board page.

The Pi now starts the slideshow by itself every time it gets power. You can unplug it and plug it back in; it comes back on its own.

Things that also run by themselves:

- The browser restarts every day at 04:00 to stay fresh.
- The photos and settings are backed up every night to `/var/backups`. The newest 7 backups are kept.

## Part 6: Secure address for phones (https)

Chrome on Android can refuse plain `http` addresses. To fix that, the board gets a real address on your domain, `homeboard.datawithdillon.com`, with a free certificate from Let's Encrypt. The phone then opens `https://homeboard.datawithdillon.com/admin` with the lock icon and no warning. The monitor does not need any of this: it keeps showing the slideshow even with no internet.

Words used here:

- **Domain**: the name you bought, `datawithdillon.com`. It stays registered at GoDaddy.
- **DNS**: the internet's phone book. It turns `homeboard.datawithdillon.com` into the Pi's number.
- **DNS provider**: the company that runs the phone book for your domain. Today that is GoDaddy. In step 21 you move only this part to Cloudflare, which is free. GoDaddy does not let small accounts use its DNS API, and the Pi needs that API to renew the certificate by itself.
- **Nameservers**: two addresses at GoDaddy that say which company runs your DNS.
- **API token**: a password that lets the Pi add one temporary entry to your DNS, to prove you own the domain. Let's Encrypt checks it every 2 to 3 months.

20. Give the Pi a fixed number on your Wi-Fi. Log in to your router's admin page (often printed on a sticker on the router) and find **DHCP reservation**, **Address reservation** or **Static lease**. Pick `homeboard` from the list and reserve the number it has now. On the Pi, `hostname -I` shows that number; the first one, like `192.168.1.235`, is the one you want. If you skip this, the router may give the Pi a new number later and the phone address stops working.

21. Move the domain's DNS to Cloudflare. Your website and email keep working; Cloudflare copies their records first.
    1. Make a free account at https://dash.cloudflare.com/sign-up.
    2. Click **Add a domain** (or **Add site**), type `datawithdillon.com`, and choose to scan for existing DNS records. Pick the **Free** plan.
    3. Cloudflare shows the records it found. Open GoDaddy in another tab: **My Products** > `datawithdillon.com` > **DNS**. Check every record in GoDaddy's list is also in Cloudflare's list (same type, name and value). Add any that are missing in Cloudflare. Records for email (type `MX`, and `TXT` records that start with `v=spf1`) matter most.
    4. Cloudflare then shows two nameservers, like `ada.ns.cloudflare.com` and `bob.ns.cloudflare.com`. In GoDaddy, go to `datawithdillon.com` > **DNS** > **Nameservers** > **Change Nameservers** > **I'll use my own nameservers**, and enter the two Cloudflare names. Save. If GoDaddy shows **DNSSEC** turned on, turn it off first.
    5. Back in Cloudflare, click **Check nameservers**. It can take from a few minutes to a few hours. Cloudflare emails you when the domain is **Active**. Wait for that before step 24.

22. Add the board's address. In Cloudflare, open `datawithdillon.com` > **DNS** > **Records** > **Add record**:
    - **Type**: `A`
    - **Name**: `homeboard`
    - **IPv4 address**: the Pi's number from step 20
    - **Proxy status**: click the orange cloud so it turns grey and says **DNS only**. The orange cloud would send phones to Cloudflare instead of the Pi.

    Save it. Anyone can see this record, but the number only works inside your home, so that is fine.

23. Make the API token. In Cloudflare, click the person icon at the top right > **My Profile** > **API Tokens** > **Create Token**. Next to **Edit zone DNS**, click **Use template**. Under **Zone Resources**, pick `datawithdillon.com`. Click **Continue to summary** > **Create Token**. Copy the token it shows. It is shown only once.

    Keep the token secret. Do not put it in an email, a chat, or a file on your PC. It goes only into the Pi in the next step.

24. Connect with `ssh dillon@homeboard.local` and run the installer with the address and your email (Let's Encrypt emails you only if something is wrong with the certificate):

    ```
    sudo bash /opt/homeboard/deploy/install.sh --host homeboard.datawithdillon.com --dns cloudflare --email you@example.com
    ```

    Put your own email in place of `you@example.com`. When it asks `Paste the DNS API token`, paste the token and press Enter. Nothing shows while you paste; that is normal. Getting the certificate takes about a minute. The installer saves the token in `/etc/homeboard/dns-credentials.ini`, which only the administrator (root) can read. It is never sent anywhere except to Cloudflare and it is not in the Home Board code.

    It is done when the last line says `Install finished. reboot now: sudo reboot`. Run `sudo reboot`.

25. On your phone, on the home Wi-Fi, open `https://homeboard.datawithdillon.com/admin`. It should open with the lock icon and no warning. Add it to your home screen if you like. The old `http://` addresses now send the phone to the `https` one.

26. Check that renewal works. Certificates last 90 days. The Pi renews by itself twice a day when the certificate is close to running out, then reloads the web server. To test it without changing anything:

    ```
    sudo certbot renew --dry-run
    ```

    It should end with `Congratulations, all simulated renewals succeeded`. To see when it runs next:

    ```
    systemctl list-timers certbot.timer
    ```

Later runs of the installer, for example after an update, remember the address and the token. You do not need to type them again.

If you ever need a new token (you deleted the old one, or think someone saw it), make a new one as in step 23, delete the old one in Cloudflare, and run:

```
sudo bash /opt/homeboard/deploy/install.sh --new-token
```

## Check the logs

Logs are messages the programs write about what they are doing. They are kept in memory and cleared at each restart, to save the SD card.

1. Connect with `ssh dillon@homeboard.local`.
2. For the server that serves the pages:

   ```
   journalctl -u homeboard-api -n 50
   ```

3. For the browser on the monitor:

   ```
   journalctl -u homeboard-kiosk -n 50
   ```

`-n 50` means the last 50 lines. Lines with `Error` or `Traceback` point to the problem.

To see whether each part is running:

```
systemctl status homeboard-api homeboard-kiosk
```

Each one should say `active (running)` in green. Press `q` to leave this screen.

## Update to the newest version

1. Connect with `ssh dillon@homeboard.local`.
2. Get the new code:

   ```
   sudo git -C /opt/homeboard pull
   ```

3. Restart the server and the browser:

   ```
   sudo systemctl restart homeboard-api homeboard-kiosk
   ```

If the update added new Python packages or changed the services, run the installer again instead, then reboot:

```
sudo bash /opt/homeboard/deploy/install.sh
sudo reboot
```

## Troubleshooting

### The screen is black

1. Check the monitor is on and set to the right input (HDMI).
2. Check the cable is in the HDMI port next to the USB-C power port (HDMI 0), not the other one.
3. Wait 2 full minutes after power on. The browser waits up to a minute for the server.
4. Connect with `ssh dillon@homeboard.local` and run:

   ```
   systemctl status homeboard-api homeboard-kiosk
   ```

   - If `homeboard-api` is not `active (running)`, read its log with `journalctl -u homeboard-api -n 50`.
   - If `homeboard-kiosk` is not `active (running)`, read its log with `journalctl -u homeboard-kiosk -n 50`.

5. Try restarting both:

   ```
   sudo systemctl restart homeboard-api homeboard-kiosk
   ```

6. If it is still black, restart the whole Pi with `sudo reboot`.
7. If the monitor was off or unplugged when the Pi started, turn it on and run `sudo reboot`.

### Cannot connect with SSH

1. Wait 3 minutes after power on and try again.
2. Check your PC is on the same Wi-Fi as the Pi.
3. Look at the monitor. If it shows `homeboard login:` the Pi is running. Check your router's device list for `homeboard` and use the number it shows (for example `ssh dillon@192.168.1.50`).
4. If the Pi never shows up on the router, the Wi-Fi name or password may be wrong. Write the SD card again (Part 2) and check the wireless LAN settings.

### The phone cannot open homeboard.local

Some phones do not understand `.local` names. Use the Pi's number instead. On the Pi, run `hostname -I` and open `http://<that number>/` on the phone.

### Android Chrome shows ERR_TOO_MANY_RETRIES or cannot reach the site

Chrome on the phone may refuse plain `http` addresses (for example with **Always use secure connections** turned on in Settings > Privacy and security). Then it does not open `http://homeboard.local/` or the Pi's number, and the Pi never sees the request. Set up the secure address in Part 6 and use `https://homeboard.datawithdillon.com/admin`.

### The https address does not open on the phone

1. Check the phone is on the home Wi-Fi, not mobile data. The address points to a number that only works at home.
2. On your PC, run `nslookup homeboard.datawithdillon.com`. It should show the Pi's number. If it shows nothing, check the DNS record from step 22. If it shows other numbers on Cloudflare, the cloud is still orange; make it grey (**DNS only**).
3. If `nslookup homeboard.datawithdillon.com 1.1.1.1` shows the Pi's number but plain `nslookup homeboard.datawithdillon.com` does not, your router blocks names that point inside your home (often called **DNS rebinding protection**). Allow your domain in the router's settings, or set the phone's **Private DNS** to `one.one.one.one`.
4. Check the Pi's number has not changed: run `hostname -I` on the Pi and compare with the DNS record. If it changed, fix the record and set up the reservation in step 20.
5. Check the web server: `systemctl status nginx` should say `active (running)`. If not, `journalctl -u nginx -n 50` shows why.

### The certificate expired or the phone shows a certificate warning

Run `sudo certbot renew` and read the last lines. If it says the token is not valid, make a new token as in step 23 and run the installer with `--new-token` (end of Part 6). Then run `sudo systemctl reload nginx`.

### A mouse pointer shows in the middle of the screen

The Pi's HDMI ports report a TV-remote input that the system mistakes for a mouse. The installer adds a rule that fixes this. Check it is there, then reboot:

```
cat /etc/udev/rules.d/90-homeboard-ignore-cec-input.rules
sudo reboot
```

If the file is missing, run the installer again (`sudo bash /opt/homeboard/deploy/install.sh`).

Do not tell the system to ignore these inputs completely. With no input devices at all, the browser never starts and the screen stays on boot text.

### Photos stop changing or the page looks stuck

Run `sudo systemctl restart homeboard-kiosk`. If it keeps happening, check both logs as shown above.
