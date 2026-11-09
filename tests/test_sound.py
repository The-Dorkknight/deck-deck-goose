# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
"""Sound tab: quiet ROG Ally fix, volume boost, output choice, login service."""
import json
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_steam import SteamBase  # noqa: E402
from ddg import core, osk, sound  # noqa: E402

ALLY = "effect_input.rog-ally"
FAMILY = "alsa_output.pci-0000_04_00.6.analog-stereo"
HDMI = "alsa_output.pci-0000_04_00.1.hdmi-stereo"
USB = "alsa_output.usb-Generic_Headset-00.analog-stereo"
BT = "bluez_output.AA_BB_CC_DD_EE_FF.1"


def ally_layout(default=ALLY):
    alsa = {"device.api": "alsa", "media.class": "Audio/Sink"}
    return {"default": default, "sinks": [
        {"name": ALLY, "description": "ROG Ally", "volume": 80,
         "properties": {"media.class": "Audio/Sink", "node.virtual": "true"}},
        {"name": FAMILY, "description": "Family 17h/19h/1ah HD Audio Controller Analog Stereo", "volume": 40,
         "mute": True, "properties": dict(alsa, **{"device.bus": "pci"})},
        {"name": HDMI, "description": "Rembrandt Radeon High Definition Audio Controller Digital Stereo (HDMI)",
         "volume": 30, "properties": dict(alsa, **{"device.bus": "pci"})},
        {"name": USB, "description": "USB Headset", "volume": 20, "properties": dict(alsa, **{"device.bus": "usb"})},
        {"name": BT, "description": "Buds", "volume": 50, "properties": {"device.api": "bluez5"}}]}


class Sound(SteamBase):
    def setUp(self):
        super().setUp()
        self.audio = Path(self.tmp.name) / "audio.json"
        self.audio.write_text(json.dumps(ally_layout()))
        os.environ["THISPC_FAKE_AUDIO"] = str(self.audio)

    def tearDown(self):
        os.environ.pop("THISPC_FAKE_AUDIO", None)
        super().tearDown()

    def vol(self, name):
        return {s["name"]: s for s in json.loads(self.audio.read_text())["sinks"]}[name]

    def test_ally_layout_is_recognised(self):
        st = sound.status()
        self.assertEqual(st["error"], "")
        self.assertEqual(st["default_desc"], "ROG Ally")
        self.assertTrue(st["processing"])
        self.assertTrue(st["can_fix"])
        self.assertFalse(st["fixed"])
        kinds = {s["name"]: s for s in st["sinks"]}
        self.assertTrue(kinds[FAMILY]["internal"] and not kinds[FAMILY]["hdmi"])
        self.assertTrue(kinds[HDMI]["hdmi"])
        self.assertFalse(kinds[USB]["internal"])
        self.assertTrue(kinds[BT]["bluetooth"] and not kinds[BT]["processing"])

    def test_fix_raises_only_the_built_in_speakers(self):
        steps = sound.fix_quiet()
        self.assertTrue(all(s.ok for s in steps), steps)
        self.assertEqual(len(steps), 1)
        self.assertIn("was 40%, unmuted", steps[0].detail)
        self.assertEqual(self.vol(FAMILY)["volume"], 100)
        self.assertFalse(self.vol(FAMILY)["mute"])
        self.assertEqual(self.vol(HDMI)["volume"], 30)            # screen untouched
        self.assertEqual(self.vol(USB)["volume"], 20)             # never blast a headset
        self.assertEqual(self.vol(BT)["volume"], 50)
        self.assertEqual(self.vol(ALLY)["volume"], 80)            # your slider stays where it was
        self.assertTrue(sound.status()["fixed"])
        self.assertTrue(core.load_config()["sound"]["fix"])

    def test_boost_limits_and_kde_slider(self):
        (self.home / ".config").mkdir(parents=True, exist_ok=True)
        sound.plasmaparc().write_text("[General]\nVolumeStep=5\n")
        steps = sound.boost(130)
        self.assertTrue(all(s.ok for s in steps), steps)
        self.assertEqual(self.vol(ALLY)["volume"], 130)
        self.assertIn("distort", steps[0].detail)
        self.assertEqual(osk.read_key(sound.plasmaparc(), "General", "RaiseMaximumVolume"), "true")
        self.assertIn("VolumeStep=5", sound.plasmaparc().read_text())
        sound.boost(999)
        self.assertEqual(self.vol(ALLY)["volume"], 150)
        sound.boost(-5)
        self.assertEqual(self.vol(ALLY)["volume"], 10)
        sound.boost(100)
        self.assertIsNone(osk.read_key(sound.plasmaparc(), "General", "RaiseMaximumVolume"))
        self.assertEqual(core.load_config()["sound"]["boost"], 100)

    def test_use_family_directly(self):
        steps = sound.use_output(FAMILY)
        self.assertTrue(steps[0].ok, steps)
        self.assertEqual(json.loads(self.audio.read_text())["default"], FAMILY)
        st = sound.status()
        self.assertFalse(st["processing"])
        self.assertFalse(st["can_fix"])
        self.assertIn("Nothing to fix", sound.fix_quiet()[0].detail)
        self.assertFalse(sound.use_output("not-there")[0].ok)

    def test_keep_apply_and_reset(self):
        sound.fix_quiet()
        sound.boost(115)
        steps = sound.keep(True)
        self.assertTrue(all(s.ok for s in steps), steps)
        unit = core.systemd_dir() / sound.SERVICE
        self.assertIn("python3 -m ddg sound apply", unit.read_text())
        self.assertIn("WantedBy=default.target", unit.read_text())
        self.assertTrue(sound.status()["keep"])
        core.remove_timer()                                     # Remove everything keeps the program
        self.assertTrue((core.data_dir() / "lib/ddg/sound.py").exists())
        self.audio.write_text(json.dumps(ally_layout()))          # as after a restart
        steps = sound.apply_saved()
        self.assertTrue(all(s.ok for s in steps), steps)
        self.assertEqual(self.vol(FAMILY)["volume"], 100)
        self.assertEqual(self.vol(ALLY)["volume"], 115)
        steps = sound.reset()
        self.assertTrue(all(s.ok for s in steps), steps)
        self.assertFalse(unit.exists())
        self.assertNotIn("sound", core.load_config())
        self.assertEqual(self.vol(ALLY)["volume"], 100)
        self.assertEqual(sound.apply_saved()[0].detail, "nothing saved to re-apply")

    def test_no_sound_system(self):
        os.environ.pop("THISPC_FAKE_AUDIO")
        old = os.environ.get("PATH", "")
        os.environ["PATH"] = self.tmp.name
        try:
            self.assertIn("Neither pactl nor wpctl", sound.status()["error"])
            self.assertFalse(sound.boost(120)[0].ok)
        finally:
            os.environ["PATH"] = old

    def test_pw_dump_parsing(self):
        dump = [
            {"id": 31, "type": "PipeWire:Interface:Metadata", "props": {"metadata.name": "default"},
             "metadata": [{"subject": 0, "key": "default.audio.sink", "type": "Spa:String:JSON",
                           "value": {"name": ALLY}}]},
            {"id": 52, "type": "PipeWire:Interface:Node", "info": {"props": {
                "media.class": "Audio/Sink", "node.name": ALLY, "node.description": "ROG Ally"}}},
            {"id": 48, "type": "PipeWire:Interface:Node", "info": {"props": {
                "media.class": "Audio/Sink", "node.name": FAMILY, "device.api": "alsa",
                "node.description": "Family 17h/19h/1ah HD Audio Controller Analog Stereo"}}},
            {"id": 60, "type": "PipeWire:Interface:Node", "info": {"props": {
                "media.class": "Audio/Source", "node.name": "alsa_input.pci-0000_04_00.6.analog-stereo"}}}]
        lst, default = sound.parse_pw_dump(json.dumps(dump))
        self.assertEqual(default, ALLY)
        self.assertEqual([s["id"] for s in lst], ["52", "48"])
        self.assertTrue(lst[0]["processing"] and lst[1]["internal"])
        dump[0]["metadata"][0]["value"] = json.dumps({"name": FAMILY})     # older PipeWire: a JSON string
        self.assertEqual(sound.parse_pw_dump(json.dumps(dump))[1], FAMILY)

    def test_page_api_rejects_bad_numbers(self):
        import http.client
        import threading
        from ddg import webui
        srv = webui._Server(("127.0.0.1", 0), webui.Handler)
        srv.token = "tok"
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            c = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=10)
            c.request("POST", "/api/sound/boost", body=json.dumps({"pct": "loud"}),
                      headers={"X-Token": "tok", "Content-Type": "application/json",
                               "Host": "127.0.0.1:%d" % srv.server_address[1]})
            r = json.loads(c.getresponse().read())
            self.assertFalse(r[0]["ok"])
            self.assertEqual(self.vol(ALLY)["volume"], 80)
        finally:
            srv.shutdown()
            srv.server_close()


if __name__ == "__main__":
    unittest.main()
