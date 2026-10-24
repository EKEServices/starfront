# Home-network test lab

This folder sets up live stacking across the machines on a home network: the Starfront server, a Rapid Stacker Collab hub and workers, with N.I.N.A. on the Windows PC. `terraform apply` brings it up from the Mac, and `terraform destroy` takes it down. It is for local testing only. The server runs with the stand-in Discord (`server/run_local.py --lan`), so anyone on the network can sign in as anyone, the owner included.

| Machine | What runs there |
|---|---|
| **Linux server host** (`omarchy.lan`) | the Starfront server on port 8800 and `collab-hub` on 8790, both as `systemd --user` units; a worker |
| **This Mac** | Terraform; a worker (a LaunchAgent); `collab-uploader` for the smoke test and real uploads |
| **Windows PC** (`Eds9950X.lan`) | N.I.N.A. with the Starfront plugin, the Starfront desktop, and a worker that runs as a logon task and pauses while `NINA.exe` runs |
| **NAS** (`DXP8800PLUS-76B2.lan`) | the read-only `XPSD` share (the 16 `gdr3-*.xpsd` files, about 45 GB) |

Terraform here is only the inventory, the order of the steps, change detection and teardown. Every step is a script in `scripts/` that runs over SSH from this Mac, and each one can also be run by hand. There is no cloud provider and nothing leaves the LAN. State is local and git-ignored, because the scripts pass tokens around.

## 0. Once, by hand

These steps need root or administrator rights, so Terraform does not do them.

1. On the main router, reserve addresses for the server host, the Windows PC and the NAS, by each one's built-in network card. On the author's network that is the GFiber router (https://192.168.1.1), not the eero: the eero is a second router behind it with its own 192.168.4.x Wi-Fi network, so it refuses 192.168.1.x reservations. The scripts use the `.lan` names, but the names follow the leases.
2. **On the Mac:** run `ssh-keygen -t ed25519` if `~/.ssh/id_ed25519` does not exist yet.
3. **On the Linux server host**, at its own keyboard:
   ```bash
   sudo bash bootstrap-linux.sh --nas DXP8800PLUS-76B2.lan --share XPSD --nas-user <nas user>
   ```
   - It installs sshd, rsync, python, curl and cifs-utils.
   - It opens TCP 22, 8800 and 8790 to `192.168.1.0/24` and the network's IPv6 prefix only, in ufw. The IPv6 rules matter: the router's DNS also gives the server's IPv6 addresses, clients try those first, and a blocked one costs every request a timeout.
   - It enables linger, so the units run while you are logged out.
   - It mounts the share read-only at `/mnt/xpsd`.

   Then, from the Mac, `ssh-copy-id <you>@omarchy.lan`.
4. **On the Windows PC**, in an elevated PowerShell, signed in as the account N.I.N.A. uses:
   ```powershell
   powershell -ExecutionPolicy Bypass -File bootstrap-windows.ps1 -MacPublicKey "<contents of ~/.ssh/id_ed25519.pub>" -NasUser <nas user>
   ```
   - It installs OpenSSH Server, with PowerShell as its shell.
   - It sets the network profile to Private, and allows SSH from the subnet on that profile only.
   - It authorises the Mac's key.
   - It stores the NAS credential for the worker's logon task.
5. **On the NAS:** create the `XPSD` share with a read-only user, and copy the `gdr3-*.xpsd` files from `~/Astronomy/XPSD`. The `gdr3sp-*` and `apass_*` files are not needed.
6. **On the Mac,** for its worker in real mode: Finder > Go > Connect to Server, `smb://DXP8800PLUS-76B2.lan/XPSD`. It mounts at `/Volumes/XPSD`.

To check the network the way Terraform will see it:

```bash
echo '{"server":"omarchy.lan","windows":"Eds9950X.lan","nas":"DXP8800PLUS-76B2.lan","ssh":"server,windows"}' \
  | python3 scripts/discover.py
```

## 1. Binaries

Rust is not cross-compiled. Each OS's `collab-hub` and `collab-worker` are built on that OS:

```bash
scripts/fetch-binaries.sh --mac                         # this Mac: also collab-uploader
scripts/fetch-binaries.sh --from-run <worker-release run id>   # Linux and Windows, from CI
scripts/fetch-binaries.sh --build-on <you>@omarchy.lan  # or Linux natively (needs rustup, cmake, rapid-stacker access)
```

`worker-release` is the Rapid Stacker Collab workflow that you run by hand (Actions > worker-release > Run workflow). It builds the archives as workflow artifacts and signs, uploads and tags nothing. Its unsigned archives are fine here, because nothing installs them through the desktop's signed-manifest path. That path is blocked until the release key exists.

## 2. Up

```bash
cp terraform.tfvars.example terraform.tfvars   # set server_user and windows_user
terraform init
terraform apply                                # catalogue_mode = "synthetic" by default
scripts/lab.sh status                          # three workers, the Starfront link
scripts/lab.sh smoke relay                     # synthetic frames through the server's front door
scripts/lab.sh smoke hub                       # ...and straight to the hub over the LAN
```

Once both smoke runs publish a live preview, switch to the real catalogue and re-apply. The hub then reads `/mnt/xpsd`, and the workers are given each OS's path to the share:

```bash
terraform apply -var catalogue_mode=real -var lab_region="<RA>,<DEC>,1,1"
scripts/lab.sh night                           # the "Lab night" collaboration and its project page
```

Point N.I.N.A. and the desktop at `terraform output -raw server_url`:
- N.I.N.A.: set `ServerUrl` in the plugin's `settings.json`.
- The desktop: set `collab.serverUrl`.

`scripts/lab.sh upload <folder>` sends real frames from the Mac, as the Lab scope telescope.

A change to the Starfront sources on this Mac, committed or not, changes the source hash, so the next `apply` copies them across and restarts the server. A new binary in `bin/` re-installs what runs it. Workers keep their tokens across re-installs.

## 3. Down

`terraform destroy` stops and removes every unit, LaunchAgent and logon task. The data stays on the server host in `~/starfront-lab`. With `-var wipe_data=true`, the hub is first backed up to `~/starfront-lab/backups`, and then the database, frames and stacks are deleted.

## The test campaign

The manual tasks in `TASKS.DISTRIBUTED_STACKING.md` (T208, T310, T417, T507, T606 and T705) assume a Mac hub. Here the hub is the Linux host, so read "the Mac" in them as `omarchy.lan`. `STARFRONT_DISTRIBUTED_STACKING_CHECKLIST.md` gives the order to run them in. T507's install from the Volunteer page waits for the release signing key. Its other half, the worker pausing while N.I.N.A. is open, can be tested now with the worker installed here.
