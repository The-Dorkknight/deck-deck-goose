"""Build a fake /sys tree that looks like a ROG Ally Z1 Extreme on Bazzite.

Used by the tests and handy for a manual dry run on any machine:

    python3 tests/fake_sysfs.py /tmp/ally          # healthy
    python3 tests/fake_sysfs.py /tmp/ally --zombie # as it looks after a bad wake
    ZD_ROOT=/tmp/ally ZD_STATE_DIR=/tmp/ally/run/zombie-deck sh src/zombie-deck status
"""
import os
import sys


def w(path, value):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(str(value) + "\n")


def build(root, zombie=False, mcu_present=True, cpus=16):
    sys_ = os.path.join(root, "sys")
    w(f"{sys_}/firmware/acpi/platform_profile", "balanced" if zombie else "performance")
    arm = f"{sys_}/class/firmware-attributes/asus-armoury/attributes"
    for name, good, bad in (("ppt_pl1_spl", 25, 17), ("ppt_pl2_sppt", 27, 17), ("ppt_pl3_fppt", 30, 17)):
        w(f"{arm}/{name}/current_value", bad if zombie else good)
        w(f"{arm}/{name}/default_value", 17)
        w(f"{arm}/{name}/min_value", 7)
        w(f"{arm}/{name}/max_value", 30 if name == "ppt_pl1_spl" else 43)
    w(f"{sys_}/class/drm/card1/device/power_dpm_force_performance_level", "manual" if zombie else "auto")
    cpu = f"{sys_}/devices/system/cpu"
    w(f"{cpu}/possible", f"0-{cpus - 1}")
    w(f"{cpu}/online", f"0-{cpus // 2 - 1}" if zombie else f"0-{cpus - 1}")
    w(f"{cpu}/cpufreq/boost", 1)
    for i in range(cpus):
        c = f"{cpu}/cpu{i}"
        if i:
            w(f"{c}/online", 0 if (zombie and i >= cpus // 2) else 1)
        w(f"{c}/cpufreq/scaling_governor", "powersave")
        w(f"{c}/cpufreq/energy_performance_preference", "0" if zombie else "balance_performance")
        w(f"{c}/cpufreq/cpuinfo_max_freq", 5100000)
        w(f"{c}/cpufreq/scaling_max_freq", 544000 if zombie else 5100000)
        w(f"{c}/cpufreq/scaling_min_freq", 400000)
    hub = f"{sys_}/bus/usb/devices/usb1"
    w(f"{hub}/authorized", 1)
    if mcu_present:
        dev = f"{sys_}/bus/usb/devices/1-3"
        w(f"{dev}/idVendor", "0b05")
        w(f"{dev}/idProduct", "1abe")
    net = f"{sys_}/class/net/wlan0"
    os.makedirs(f"{net}/wireless", exist_ok=True)
    w(f"{net}/operstate", "down" if zombie else "up")
    drv = f"{sys_}/bus/pci/drivers/mt7921e"
    os.makedirs(drv, exist_ok=True)
    os.makedirs(f"{net}/device", exist_ok=True)
    link = f"{net}/device/driver"
    if not os.path.lexists(link):
        os.symlink(drv, link)
    bat = f"{sys_}/class/power_supply/BAT0"
    w(f"{bat}/capacity", 63)
    w(f"{bat}/status", "Discharging")
    w(f"{root}/proc/sys/kernel/random/boot_id", "00000000-0000-4000-8000-000000000001")
    os.makedirs(f"{root}/run/zombie-deck", exist_ok=True)
    os.makedirs(f"{root}/var/lib/zombie-deck", exist_ok=True)
    os.makedirs(f"{root}/etc/systemd/system-sleep", exist_ok=True)
    hook = f"{root}/etc/systemd/system-sleep/zombie-deck"
    if not os.path.exists(hook):
        w(hook, "#!/bin/sh\nexit 0")
        os.chmod(hook, 0o755)
    os.makedirs(f"{root}/etc/zombie-deck", exist_ok=True)
    os.makedirs(f"{root}/var/log", exist_ok=True)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    build(sys.argv[1], zombie="--zombie" in sys.argv, mcu_present="--no-mcu" not in sys.argv)
    print("fake sysfs built at", sys.argv[1])
