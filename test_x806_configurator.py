import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import x806_configurator_mvp as x


class ProtocolTests(unittest.TestCase):
    def test_launch_command_builder_wraps_pasted_command(self):
        events=x.launch_command_events(r'"C:\Program Files\Example\App.exe"',inter_key_ms=10)
        self.assertEqual(events[:4],x.chord_events(["Left Win"],"R"))
        self.assertEqual(events[4],{"type":"delay","ms":300})
        self.assertEqual(events[-2:],x.chord_events([],"Enter"))
        raw,tokens=x.compile_macro(events)
        self.assertGreater(tokens,10)
        self.assertEqual(len(raw)%8,0)

    def test_type_string_builder_does_not_add_run_shortcut(self):
        events=x.type_string_events("Hello!",inter_key_ms=5)
        usages={event.get("usage") for event in events if "usage" in event}
        self.assertNotIn(x.HID_KEYS["Left Win"],usages)
        x.compile_macro(events)

    def test_macro_builder_source_survives_project_normalization(self):
        project=x.default_project()
        events=x.launch_command_events("notepad")
        project["profiles"]["Game I"]["1"]={
            "type":"macro","events":events,
            "source":{"kind":"launch","text":"notepad","inter_key_ms":10},
        }
        normalized=x.normalize_project(project)
        self.assertEqual(normalized["profiles"]["Game I"]["1"]["source"]["text"],"notepad")

    def test_friendly_action_groups_cover_every_verified_action_once(self):
        grouped=[action for actions in x.DIRECT_ACTION_GROUPS.values() for action in actions]
        self.assertEqual(len(grouped),len(set(grouped)))
        self.assertEqual(set(grouped),set(x.DIRECT_ACTIONS))

    def test_macro_recorder_translates_common_tk_keys(self):
        for keysym,key_name in (
            ("a","A"),("A","A"),("Return","Enter"),("Control_L","Left Ctrl"),
            ("KP_7","Numpad 7"),("Prior","Page Up"),
        ):
            with self.subTest(keysym=keysym):
                event=SimpleNamespace(keysym=keysym)
                self.assertEqual(x.tkinter_event_usage(event),x.HID_KEYS[key_name])
        self.assertIsNone(x.tkinter_event_usage(SimpleNamespace(keysym="XF86AudioMute")))

    def test_delay_capture_example(self):
        self.assertEqual(x.encode_delay_tokens(1000), [(0x77, 0), (0x7F, 0), (0x7F, 0), (0x7F, 0)])

    def test_controlled_macro_example(self):
        events=[]
        for key in ("A", "B", "A"):
            usage=x.HID_KEYS[key]
            events += [{"type":"down","usage":usage},{"type":"up","usage":usage}]
        events += [{"type":"delay","ms":1000}]
        usage=x.HID_KEYS["B"]
        events += [{"type":"down","usage":usage},{"type":"up","usage":usage}]
        raw,count=x.compile_macro(events)
        self.assertEqual(count,12)
        self.assertEqual(raw[:26],bytes.fromhex("01 00 81 04 01 04 81 05 01 05 81 04 01 04 77 00 7f 00 7f 00 7f 00 81 05 01 05"))
        self.assertEqual(len(raw)%8,0)

    def test_text_macro_expands_shift(self):
        events=x.text_to_events("aA!")
        raw,count=x.compile_macro(events)
        self.assertEqual(count,10)
        self.assertEqual(raw[2:6],bytes.fromhex("81 04 01 04"))

    def test_calculator_preset_compiles(self):
        events=x.macro_preset_events("Calculator")
        raw,count=x.compile_macro(events)
        self.assertGreater(count,10)
        self.assertEqual(len(raw)%8,0)

    def test_every_macro_preset_compiles(self):
        for name in x.MACRO_PRESETS:
            with self.subTest(name=name):
                x.compile_macro(x.macro_preset_events(name))

    def test_packet_capture_roundtrip(self):
        data=bytes.fromhex("01 02 03 04")
        packet_text="\n".join("TX: "+x.hx(p) for p in x.build_write_packets(0x1234,data))
        parsed=x.parse_feature_packets(packet_text)
        blocks,warnings=x.reconstruct_memory_writes(parsed)
        self.assertEqual(blocks,[(0x1234,data)])
        self.assertEqual(warnings,[])

    def test_tshark_contiguous_packet_format(self):
        parsed=x.parse_feature_packets("591\t0718030008002e07\n")
        self.assertEqual(parsed,[(1,bytes.fromhex("07 18 03 00 08 00 2e 07"))])

    def test_original_project_migrates(self):
        project=x.default_project()
        project["format"]="x806-mvp-project"
        project["version"]=1
        migrated=x.normalize_project(json.loads(json.dumps(project)))
        self.assertEqual(migrated["format"],x.PROJECT_FORMAT)
        self.assertEqual(migrated["version"],x.PROJECT_VERSION)

    def test_edited_legacy_office_migrates_to_game_i(self):
        project={"format":"x806-mvp-project","version":2,"profiles":{
            "Office":x.default_profile(),"Game I":x.default_profile(),
            "Game II":x.default_profile(),"Game III":x.default_profile(),
        }}
        project["profiles"]["Office"]["1"]={"type":"key","usage":x.HID_KEYS["Z"]}
        migrated=x.normalize_project(project)
        self.assertEqual(migrated["profiles"]["Game I"]["1"]["usage"],x.HID_KEYS["Z"])

    def test_vendor_active_profile_detection(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/"config.ini"
            path.write_text("[Init]\nCurConfig=2\n",encoding="cp1252")
            active,source=x.detect_vendor_active_profile([path])
            self.assertEqual(active,"Game II")
            self.assertEqual(source,path)

    def test_verified_profiles_fit_default_config(self):
        app=x.App.__new__(x.App)
        app.project=x.default_project()
        compiled=x.App.compile_hardware_profiles(app)
        self.assertEqual(compiled["used_bytes"],24*8)
        self.assertLessEqual(compiled["used_bytes"],compiled["capacity"])
        self.assertTrue(all(len(data)<=8 for _,data in compiled["function_writes"]+compiled["descriptor_writes"]+compiled["object_writes"]))
        self.assertEqual(compiled["meta"][("Game II",1)][0],0x0440)
        self.assertEqual(compiled["meta"][("Game III",1)][0],0x0480)

    def test_capture_exact_profile_layout(self):
        app=x.App.__new__(x.App); app.project=x.default_project()
        compiled=x.App.compile_hardware_profiles(app)
        functions=dict(compiled["function_writes"])
        self.assertEqual(functions[0x0008],bytes.fromhex("2e 2e 2e 2e 2e 2e 2e 00"))
        self.assertEqual(functions[0x0010],b"\x2e")
        self.assertEqual(functions[0x0050],bytes.fromhex("2e 2e 2e 2e 2e 2e 2e 00"))
        self.assertEqual(functions[0x0060],bytes.fromhex("2e 2e 2e 2e 2e 2e 2e 00"))
        descriptors=dict(compiled["descriptor_writes"])
        self.assertEqual(descriptors[0x0240][:4],b"\x00"*4)
        self.assertEqual(descriptors[0x0240][4:],bytes.fromhex("01 01 21 00"))
        game_iii={}
        for address,data in compiled["descriptor_writes"]:
            for offset,value in enumerate(data): game_iii[address+offset]=value
        self.assertEqual(
            bytes(game_iii[0x0244+i] for i in range(10)),
            bytes.fromhex("01 01 21 00 00 80 04 00 00 00"),
        )
        self.assertEqual(x.FINAL_APPLY_PACKETS[0],bytes.fromhex("07 18 10 00 00 00 00 00"))

    def test_direct_action_capture_reconstruction(self):
        app=x.App.__new__(x.App); app.project=x.default_project()
        assignments={
            1:"switch_game_i",2:"switch_game_iii",3:"play_pause",4:"volume_up",
            5:"volume_down",6:"mute",7:"led_loop",8:"dpi_plus",
        }
        for physical,action in assignments.items():
            app.project["profiles"]["Game I"][str(physical)]={"type":"direct","action":action}
        compiled=x.App.compile_hardware_profiles(app)
        functions=dict(compiled["function_writes"])
        self.assertEqual(functions[0x0008],bytes.fromhex("2f 2f 3a 2b 35 2f 37 00"))
        self.assertEqual(functions[0x0010],b"\x2f")
        descriptors={}
        for address,data in compiled["descriptor_writes"]:
            for offset,value in enumerate(data): descriptors[address+offset]=value
        self.assertEqual(bytes(descriptors[0x00A0+i] for i in range(10)),x.DIRECT_ACTIONS["volume_up"]["descriptor"])
        self.assertEqual(bytes(descriptors[0x00AA+i] for i in range(10)),x.DIRECT_ACTIONS["play_pause"]["descriptor"])
        self.assertEqual(bytes(descriptors[0x00D2+i] for i in range(10)),x.DIRECT_ACTIONS["mute"]["descriptor"])
        self.assertEqual(bytes(descriptors[0x00F0+i] for i in range(10)),x.DIRECT_ACTIONS["volume_down"]["descriptor"])

    def test_binding_test2_capture_reconstruction(self):
        app=x.App.__new__(x.App); app.project=x.default_project()
        game_i={
            1:"dpi_loop",2:"double_click",3:"fire",4:"dpi_minus",
            5:"previous",6:"next",7:"led_loop",8:"left_mouse",
        }
        game_ii={1:"middle_mouse",2:"right_mouse"}
        for physical,action in game_i.items():
            app.project["profiles"]["Game I"][str(physical)]={"type":"direct","action":action}
        for physical,action in game_ii.items():
            app.project["profiles"]["Game II"][str(physical)]={"type":"direct","action":action}
        compiled=x.App.compile_hardware_profiles(app)
        functions=dict(compiled["function_writes"])
        self.assertEqual(functions[0x0008],bytes.fromhex("2c 29 28 21 35 2f 26 00"))
        self.assertEqual(functions[0x0010],b"\x2f")
        self.assertEqual(functions[0x0050],bytes.fromhex("2e 2e 23 2e 2e 2e 22 00"))
        descriptors={}
        for address,data in compiled["descriptor_writes"]:
            for offset,value in enumerate(data): descriptors[address+offset]=value
        self.assertEqual(bytes(descriptors[0x00D2+i] for i in range(10)),x.DIRECT_ACTIONS["next"]["descriptor"])
        self.assertEqual(bytes(descriptors[0x00F0+i] for i in range(10)),x.DIRECT_ACTIONS["previous"]["descriptor"])

    def test_button_test_capture_reconstruction_and_dynamic_direct_pointers(self):
        app=x.App.__new__(x.App); app.project=x.default_project()
        direct={1:"mail",4:"player",5:"calculator",7:"browser",8:"show_desktop"}
        macros={2:"Lock PC",3:"Close window",6:"My computer"}
        for physical,action in direct.items():
            app.project["profiles"]["Game I"][str(physical)]={"type":"direct","action":action}
        for physical,preset in macros.items():
            app.project["profiles"]["Game I"][str(physical)]={
                "type":"macro","events":x.macro_preset_events(preset),
            }
        compiled=x.App.compile_hardware_profiles(app)
        functions=dict(compiled["function_writes"])
        self.assertEqual(functions[0x0008],bytes.fromhex("2f 2d 2d 27 2f 2d 2f 00"))
        self.assertEqual(functions[0x0010],b"\x2f")
        descriptors={}
        for address,data in compiled["descriptor_writes"]:
            for offset,value in enumerate(data): descriptors[address+offset]=value
        expected={
            0x00A0:"01 00 00 83 01 00 04 00 00 00",
            0x00AA:"01 04 00 00 00 00 04 00 0a 00",
            0x00B4:"01 04 00 00 00 10 04 00 0a 00",
            0x00BE:"00 00 00 00 00 00 00 00 00 00",
            0x00C8:"01 00 00 23 02 20 04 00 00 00",
            0x00D2:"01 04 00 00 00 20 04 00 0a 00",
            0x00DC:"01 00 00 8a 01 30 04 00 00 00",
            0x00F0:"01 00 00 92 01 30 04 00 00 00",
        }
        for address,hex_bytes in expected.items():
            with self.subTest(address=hex(address)):
                self.assertEqual(bytes(descriptors[address+i] for i in range(10)),bytes.fromhex(hex_bytes))
        self.assertEqual(compiled["meta"][("Game I",2)],(0x0400,16))
        self.assertEqual(compiled["meta"][("Game I",3)],(0x0410,16))
        self.assertEqual(compiled["meta"][("Game I",6)],(0x0420,16))

    def test_lighting_capture_byte(self):
        app=x.App.__new__(x.App); app.project=x.default_project()
        compiled=x.App.compile_hardware_profiles(app)
        self.assertEqual(compiled["lighting_writes"],[(0x001C,b"\x93"),(0x001D,b"\x55")])
        app.project["lighting"]["mode"]="button_response"
        compiled=x.App.compile_hardware_profiles(app)
        self.assertEqual(compiled["lighting_writes"],[(0x001C,b"\x9b"),(0x001D,b"\x55")])

    def test_minimum_cycle_time_capture(self):
        app=x.App.__new__(x.App); app.project=x.default_project()
        app.project["lighting"]["cycle_time"]=1
        compiled=x.App.compile_hardware_profiles(app)
        self.assertEqual(dict(compiled["lighting_writes"])[0x001C],b"\x90")
        app.project["lighting"]["mode"]="button_response"
        self.assertEqual(dict(x.App.compile_hardware_profiles(app)["lighting_writes"])[0x001C],b"\x98")

    def test_usb_report_125_capture_and_inferred_intervals(self):
        expected={125:0x07,250:0x03,500:0x01,1000:0x00}
        for rate,code in expected.items():
            with self.subTest(rate=rate):
                app=x.App.__new__(x.App); app.project=x.default_project()
                app.project["usb_report_hz"]=rate
                compiled=x.App.compile_hardware_profiles(app)
                self.assertEqual(dict(compiled["usb_report_writes"])[0x001E],bytes([code]))
                self.assertTrue(all(data==bytes([code]) for _,data in compiled["usb_report_writes"]))
                descriptor_bytes={}
                for address,data in compiled["descriptor_writes"]:
                    for offset,value in enumerate(data): descriptor_bytes[address+offset]=value
                for profile in x.PROFILE_NAMES:
                    base=x.PROFILE_BANKS[profile]["descriptor_base"]
                    for internal in (1,2,3,4,5,6,7,9):
                        self.assertEqual(descriptor_bytes[base+(internal-1)*10],code)

    def test_lighting_color_toggle_captures(self):
        expected={"blue":0x54,"pink":0x51,"red":0x45,"purple":0x15}
        for color,value in expected.items():
            with self.subTest(color=color):
                app=x.App.__new__(x.App); app.project=x.default_project()
                app.project["lighting"]["colors"][color]=False
                compiled=x.App.compile_hardware_profiles(app)
                self.assertEqual(dict(compiled["lighting_writes"])[0x001D],bytes([value]))

    def test_old_project_gets_default_lighting(self):
        project=x.default_project()
        project.pop("lighting")
        project["version"]=3
        migrated=x.normalize_project(project)
        self.assertEqual(migrated["lighting"],{
            "mode":"cycle_breath",
            "cycle_time":4,
            "colors":{"blue":True,"pink":True,"red":True,"purple":True},
        })
        self.assertEqual(migrated["usb_report_hz"],500)

    def test_successful_upload_record_is_persistent_and_recoverable(self):
        app=x.App.__new__(x.App); app.project=x.default_project()
        app.project["profiles"]["Game III"]["1"]={"type":"key","usage":x.HID_KEYS["Z"]}
        compiled=x.App.compile_hardware_profiles(app)
        blocks=(compiled["function_writes"]+compiled["lighting_writes"]+compiled["usb_report_writes"]+
                compiled["descriptor_writes"]+compiled["object_writes"])
        moment=datetime(2026,9,30,12,34,56,789012,tzinfo=timezone.utc)
        record=x.build_upload_record(
            app.project,compiled,blocks,"Game III",Path("example.json"),moment,
        )
        with tempfile.TemporaryDirectory() as directory:
            latest,archive=x.persist_upload_record(record,directory)
            self.assertTrue(latest.is_file())
            self.assertTrue(archive.is_file())
            loaded=json.loads(latest.read_text(encoding="utf-8"))
            self.assertEqual(loaded["format"],x.UPLOAD_LOG_FORMAT)
            self.assertEqual(loaded["project"]["profiles"]["Game III"]["1"]["usage"],x.HID_KEYS["Z"])
            self.assertEqual(loaded["write_plan"]["blocks"][0]["address"],"0x0008")
            history=(Path(directory)/"upload_history.jsonl").read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(history),1)


if __name__ == "__main__":
    unittest.main()
