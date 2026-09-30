#!/usr/bin/env python3
"""Read-only X806 device probe and offline command-0x18 capture analyzer."""

import argparse
import subprocess
import sys
import time
from pathlib import Path

from x806_configurator_mvp import (
    HID_IMPORT_ERROR, HidSession, parse_feature_packets,
    reconstruct_memory_writes, hx,
)

SCRIPT_DIR=Path(__file__).resolve().parent


def resolve_capture_path(value):
    supplied=Path(value).expanduser()
    candidates=[supplied]
    if not supplied.is_absolute():
        candidates += [SCRIPT_DIR/supplied,Path.home()/"Downloads"/supplied,
                       Path.home()/"Desktop"/supplied,Path.home()/"Documents"/supplied]
    unique=[]
    for candidate in candidates:
        if candidate.resolve() not in [item.resolve() for item in unique]: unique.append(candidate)
    for candidate in unique:
        if candidate.is_file(): return candidate.resolve()
    searched="\n  ".join(str(path.resolve()) for path in unique)
    raise FileNotFoundError(f"Capture file {value!r} was not found. Searched:\n  {searched}")


def read_device(count, interval):
    if HID_IMPORT_ERROR:
        raise RuntimeError(f"pywinusb unavailable: {HID_IMPORT_ERROR}")
    session = HidSession()
    try:
        path = session.connect()
        print(f"Connected: {path}")
        for index in range(count):
            print(f"{index + 1:04}: {hx(session.get_feature())}")
            if index + 1 < count:
                time.sleep(interval)
    finally:
        session.close()


def decode_capture(path):
    path=resolve_capture_path(path)
    if path.suffix.lower() in (".pcap", ".pcapng", ".cap"):
        tshark=Path(r"C:\Program Files\Wireshark\tshark.exe")
        if not tshark.is_file():
            raise RuntimeError("Wireshark/tshark is required to analyze packet captures")
        result=subprocess.run(
            [str(tshark),"-r",str(path),"-Y","usb.data_fragment","-T","fields","-e","frame.number","-e","usb.data_fragment"],
            check=False,capture_output=True,text=True,
        )
        if result.returncode:
            detail=result.stderr.strip() or "no diagnostic output"
            raise RuntimeError(f"tshark could not read {path}: {detail}")
        text=result.stdout
    else:
        text = path.read_text(encoding="utf-8", errors="replace")
    packets = parse_feature_packets(text)
    blocks, warnings = reconstruct_memory_writes(packets)
    return packets,blocks,warnings


def analyze_capture(path):
    packets,blocks,warnings=decode_capture(path)
    print(f"Parsed {len(packets)} report-7 packet(s); reconstructed {len(blocks)} block(s).")
    for address, data in blocks:
        print(f"0x{address:04X} len={len(data):3} {hx(data)}")
    for warning in warnings:
        print(f"WARNING: {warning}", file=sys.stderr)
    return 0 if blocks or packets else 2


def compare_captures(before,after):
    _,before_blocks,before_warnings=decode_capture(before)
    _,after_blocks,after_warnings=decode_capture(after)
    def image(blocks):
        result={}
        for address,data in blocks:
            for offset,value in enumerate(data): result[address+offset]=value
        return result
    old,new=image(before_blocks),image(after_blocks)
    changed=[address for address in sorted(set(old)|set(new)) if old.get(address)!=new.get(address)]
    print(f"Changed byte addresses: {len(changed)}")
    for address in changed:
        left="--" if address not in old else f"{old[address]:02X}"
        right="--" if address not in new else f"{new[address]:02X}"
        print(f"0x{address:04X}: {left} -> {right}")
    for warning in before_warnings+after_warnings:
        print(f"WARNING: {warning}",file=sys.stderr)
    return 0 if changed else 2


def capture_vendor(seconds, interface, output):
    tshark = Path(r"C:\Program Files\Wireshark\tshark.exe")
    if not tshark.is_file():
        raise RuntimeError("Wireshark/tshark was not found")
    output = Path(output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    print(f"Capturing USB traffic for {seconds} seconds to {output}")
    print("In the vendor X806 program: select one profile, make one distinctive key change, and click Apply now.")
    result = subprocess.run(
        [str(tshark), "-i", str(interface), "-a", f"duration:{seconds}", "-w", str(output)],
        check=False,
    )
    if result.returncode:
        raise RuntimeError(f"tshark exited with code {result.returncode}")
    extracted = subprocess.run(
        [str(tshark), "-r", str(output), "-Y", "usb.data_fragment", "-T", "fields", "-e", "frame.number", "-e", "usb.data_fragment"],
        check=True, capture_output=True, text=True,
    ).stdout
    text_path = output.with_suffix(output.suffix + ".packets.txt")
    text_path.write_text(extracted, encoding="utf-8")
    print(f"Extracted packet text: {text_path}")
    return analyze_capture(text_path)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--read", action="store_true", help="read Feature Report 7 without sending data")
    group.add_argument("--analyze", metavar="FILE", help="decode a text/USBPcap packet export")
    group.add_argument("--capture-vendor", type=int, metavar="SECONDS", help="capture while you click Apply in X806.exe")
    group.add_argument("--compare",nargs=2,metavar=("BEFORE","AFTER"),help="compare two vendor captures by memory address")
    parser.add_argument("--count", type=int, default=1, help="number of read-only samples (default: 1)")
    parser.add_argument("--interval", type=float, default=0.25, help="seconds between samples")
    parser.add_argument("--interface", default="10", help="tshark USBPcap interface number (this PC: 10)")
    parser.add_argument("--output", default="captures/vendor_apply.pcapng", help="capture output path")
    args = parser.parse_args(argv)
    if args.count < 1 or args.count > 10000:
        parser.error("--count must be between 1 and 10000")
    if args.interval < 0:
        parser.error("--interval cannot be negative")
    try:
        if args.analyze:
            return analyze_capture(args.analyze)
        if args.compare:
            return compare_captures(*args.compare)
        if args.capture_vendor:
            if not 5 <= args.capture_vendor <= 300:
                parser.error("--capture-vendor must be between 5 and 300 seconds")
            return capture_vendor(args.capture_vendor,args.interface,args.output)
        return read_device(args.count, args.interval) or 0
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
